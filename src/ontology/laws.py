"""
laws.py — link equations to the law registry by evidence (docs_v2/EQUATION_KG_PLAN.md, phase 4b).

    score = 0.3·S_quantity + 0.4·S_math + 0.2·S_text + 0.1·S_concept

S_quantity  share of the law's quantity concepts that the equation's parameters cover; symbol
            names that agree with the law's notation count half (Q, A, V for Q = AV)
S_math      1.0  the same structure as a reference form, or ALGEBRAIC to it (variables paired by
                 name or by quantity concept)                       — src/document/formula_algebra.py
            0.8  equal after renaming symbols (every bijection of ≤ 6 symbols tried numerically on
                 points of the reference form); renaming alone says nothing about meaning, so
                 P = IV reaches 0.8 here and stays a candidate for Q = AV
S_text      the law's name in the lead-in sentence or purpose (1.0), citing sentences (0.8),
            section head (0.6) or definition clause (0.5)
S_concept   the equation is already linked to the law's Method/Metric (DEFINES_METRIC, methods
            named around it)
status      accepted ≥ 0.5, candidate ≥ 0.3; an accepted link is capped to candidate when the
            equation has a parsed formula that matches no form (text cannot outvote the formula)
            or its left side is an acronym that is not the law's (MAE for RMSE, NDWI for NDVI); (lower scores are not kept, nor a renaming match
            with no text, quantity or concept evidence). The weights and cut-offs
            are provisional until the gold set calibrates them (phase 6). No LLM is involved.

Output: data/equations/law_links_<LAWS_VERSION>.parquet (never overwritten); the registry with
verified reference code per form is written to the graph by equation_kg_loader.

Usage:
    python -m src.ontology.laws
"""
from __future__ import annotations

import argparse
import itertools
import json
import logging
import os
import random
import re
import sys
import warnings
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import pandas as pd
import yaml

from src.document.formula_structure import CACHE, _time_limit, load_cache, structure

log = logging.getLogger("geohydro.ontology.laws")
ROOT = Path(__file__).resolve().parents[2]
REGISTRY = Path(__file__).with_name("laws.yaml")
SODB = Path(os.getenv("SODB_DIR", str(ROOT / "data" / "sodb")))
WEIGHTS = {"quantity": 0.3, "math": 0.4, "text": 0.2, "concept": 0.1}
ACCEPT, CANDIDATE = 0.5, 0.3
MAX_RENAME = 6


@lru_cache(maxsize=1)
def registry() -> dict:
    return yaml.safe_load(REGISTRY.read_text())




@dataclass
class Form:
    law_id: str
    variant: str | None
    latex: str
    structural_hash: str
    expr: object
    names: frozenset
    quantities: dict            # symbol → quantity_id
    code: dict = field(default_factory=dict)
    fingerprint: tuple = ()


def _fingerprint(expr) -> tuple:
    """What a renaming cannot change: the number of symbols, the function heads, and the
    numbers other than 0, ±1, ±2 (2/3 and 1/2 of Manning, 25400 of SCS-CN)."""
    import sympy as sp
    heads = sorted({type(n).__name__ for n in sp.preorder_traversal(expr)
                    if isinstance(n, sp.Function) or isinstance(n, (sp.Sum, sp.Derivative))})
    nums = sorted({str(abs(n)) for n in expr.atoms(sp.Number) if abs(n) not in (0, 1, 2)})
    return (len(expr.free_symbols), tuple(heads), tuple(nums))


@lru_cache(maxsize=1)
def forms() -> tuple[Form, ...]:
    from src.document.formula_algebra import to_expr
    from src.document.formula_code import generate
    out = []
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        for law in registry()["laws"]:
            q = {k: v for k, v in (law.get("variables") or {}).items() if v}
            for f in law["forms"]:
                st = structure(f["latex"])
                if st.status != "ok":
                    raise ValueError(f"{law['id']}: reference form does not parse: {st.status} {st.error}")
                e = to_expr(st.srepr)
                c = generate(st.srepr, st.lhs)
                names = frozenset(x.name for x in e.free_symbols)
                qmap = {}
                for n in names:                       # law variables name stems (O for O_i)
                    stem = n.split("_", 1)[0] if n not in q else n
                    if n in q or stem in q:
                        qmap[n] = q.get(n) or q[stem]
                out.append(Form(law["id"], f.get("variant"), f["latex"], st.structural_hash, e, names, qmap,
                                {"python": c.python, "julia": c.julia, "check": c.check, "target": c.target,
                                 "args": c.args}, _fingerprint(e)))
    return tuple(out)


# ── channels ───────────────────────────────────────────────────────────────────

@lru_cache(maxsize=64)
def _text_re(law_id: str):
    law = next(l for l in registry()["laws"] if l["id"] == law_id)
    return re.compile("|".join(f"(?:{p})" for p in law.get("text") or ["$^"]), re.I)


def s_text(law_id: str, row: dict) -> float:
    rx = _text_re(law_id)
    best = 0.0
    for fld, w in (("lead_in", 1.0), ("purpose", 1.0), ("mentions", 0.8), ("section", 0.6), ("clause", 0.5)):
        v = row.get(fld)
        if isinstance(v, str) and v and rx.search(v):
            best = max(best, w)
    return best


def s_quantity(law_id: str, eq_quantities: set, eq_names: set) -> float:
    law = next(l for l in registry()["laws"] if l["id"] == law_id)
    lq = {v for v in (law.get("variables") or {}).values() if v}
    by_q = len(lq & eq_quantities) / len(lq) if lq else 0.0
    ln = set((law.get("variables") or {}))
    by_name = len(ln & {n.split("_", 1)[0] if n.split("_", 1)[0] in ln else n for n in eq_names}) / len(ln) if ln else 0.0
    return round(max(by_q, 0.5 * by_name), 3)


def s_concept(law_id: str, concepts: set) -> float:
    law = next(l for l in registry()["laws"] if l["id"] == law_id)
    return 1.0 if set(law.get("concepts") or []) & concepts else 0.0


def _renamed(form: Form, expr, rng: random.Random) -> dict | None:
    """A bijection of symbols under which every point of the reference form satisfies the
    equation (checked numerically), or None."""
    import numpy as np
    import sympy as sp
    if not form.code.get("target") or expr.has(sp.Sum, sp.Product, sp.Integral) or form.expr.has(sp.Sum):
        return None
    fs = sorted(form.expr.free_symbols, key=lambda x: x.name)
    es = sorted(expr.free_symbols, key=lambda x: x.name)
    if len(fs) != len(es) or len(fs) > MAX_RENAME:
        return None
    ns: dict = {}
    exec(form.code["python"], ns)                               # noqa: S102 — registry code
    compute = ns[f"compute_{form.code['target']}"]
    argnames = [a["name"] for a in form.code["args"] if a["name"] != form.code["target"]]
    from src.document.formula_code import ident
    by_ident = {ident(s.name): s for s in fs}
    points = []
    for _ in range(4):
        vals = {a: rng.uniform(0.7, 2.3) for a in argnames}
        try:
            with np.errstate(all="ignore"):
                t = float(compute(**vals))
        except Exception:
            continue
        if t != t or abs(t) == float("inf"):
            continue
        points.append({by_ident[k]: v for k, v in {**vals, form.code["target"]: t}.items()})
    if len(points) < 3:
        return None
    f_e = sp.lambdify(es, expr.lhs - expr.rhs, "math")
    for perm in itertools.permutations(fs):
        m = dict(zip(es, perm))                                   # equation symbol → form symbol
        ok = True
        for p in points:
            try:
                r = f_e(*[p[m[s]] for s in es])
                scale = 1 + sum(abs(v) for v in p.values())
                if not abs(r) <= 1e-7 * scale:
                    ok = False
                    break
            except Exception:
                ok = False
                break
        if ok:
            return {s.name: m[s].name for s in es}
    return None


def _renamed_tree(form: Form, expr) -> dict | None:
    """A bijection of free symbols under which the canonical tree equals the reference form's
    (works with sums, where the numeric test cannot run)."""
    import sympy as sp
    from src.document.formula_structure import canonical
    fs = sorted(form.expr.free_symbols, key=lambda x: x.name)
    es = sorted(expr.free_symbols, key=lambda x: x.name)
    if len(fs) != len(es) or len(fs) > MAX_RENAME:
        return None
    def bound_to_idx(e):                  # Σ over t or over i is the same sum
        idx = []
        for node in sp.preorder_traversal(e):
            if isinstance(node, (sp.Sum, sp.Product)):
                idx += [l[0] for l in node.limits if l[0] not in idx]
        return e.xreplace({b: sp.Symbol(f"idx{k}_") for k, b in enumerate(idx)})

    target = sp.srepr(canonical(bound_to_idx(form.expr)))
    tmp = [sp.Symbol(f"zz{k}_") for k in range(len(es))]
    base = bound_to_idx(expr).xreplace(dict(zip(es, tmp)))
    for perm in itertools.permutations(fs):
        try:
            if sp.srepr(canonical(bound_to_idx(base.xreplace(dict(zip(tmp, perm)))))) == target:
                return {e.name: f.name for e, f in zip(es, perm)}
        except Exception:
            continue
    return None


def _foreign_name(law_id: str, lhs: str) -> bool:
    """The left side is an acronym (MAE, RVI, NDWI) that is not one of the law's own names."""
    name = re.sub(r"[^A-Za-z]", "", lhs.split("_")[0] if "_" in lhs and lhs.split("_")[0].isupper() else lhs)
    if not (len(name) >= 2 and name.isupper()):
        return False
    law = next(l for l in registry()["laws"] if l["id"] == law_id)
    own = {re.sub(r"[^A-Za-z]", "", v).upper() for v in (law.get("variables") or {})}
    return name not in own


def s_math(form: Form, st: dict, expr, tokens: dict, rng: random.Random) -> tuple[float, str | None, dict]:
    """(score, method, mapping equation symbol → form symbol)."""
    import sympy as sp
    from src.document.formula_algebra import equivalent
    if st.get("structural_hash") == form.structural_hash:
        return 1.0, "exact", {}
    names = frozenset(x.name for x in expr.free_symbols)
    pairings = []
    if names == form.names:
        pairings.append(("algebraic_by_name", {}))
    # by quantity concept: each equation symbol whose concept is unique in both
    inv_form = {}
    for n, q in form.quantities.items():
        inv_form.setdefault(q, []).append(n)
    m = {}
    for n in names:
        q = tokens.get(n)
        if q and len(inv_form.get(q, [])) == 1 and list(tokens.values()).count(q) == 1:
            m[n] = inv_form[q][0]
    if m and {m.get(n, n) for n in names} == form.names:
        pairings.append(("algebraic_by_quantity", m))
    for method, mp in pairings:
        x = expr.xreplace({sp.Symbol(k): sp.Symbol(v) for k, v in mp.items()}) if mp else expr
        try:
            verdict, _, _ = equivalent((form.expr.lhs, form.expr.rhs), (x.lhs, x.rhs), timeout=3)
        except Exception:
            continue
        if verdict == "ALGEBRAIC":
            return 1.0, method, mp
    if _fingerprint(expr) == form.fingerprint:
        mp = _renamed_tree(form, expr)
        if mp is not None:
            return 0.8, "renamed", mp
        try:
            with _time_limit(10):
                mp = _renamed(form, expr, rng)
        except Exception:
            mp = None
        if mp is not None:
            return 0.8, "renamed", mp
    return 0.0, None, {}


# ── corpus ─────────────────────────────────────────────────────────────────────

def _equation_rows(sodb: Path) -> list[dict]:
    cols = ["equation_id", "paper_id", "formula_hash", "latex", "text_grobid", "parameters", "lead_in",
            "purpose", "purpose_source", "section", "mentions", "clause"]
    out = []
    for f in sorted(sodb.glob("*/equation_records.parquet")):
        d = pd.read_parquet(f, columns=cols)
        out += [{k: (None if isinstance(v, float) and v != v else v) for k, v in r.items()} for r in d.to_dict("records")]
    return out


def link(*, sodb: Path = SODB, cache: dict | None = None, rows: list[dict] | None = None,
         concepts_of=None) -> tuple[pd.DataFrame, dict]:
    from src.document.formula_algebra import param_symbols, to_expr
    from src.document.formula_parameters import quantity_name
    from src.ontology.quantity_map import resolve
    cache = load_cache(CACHE) if cache is None else cache
    rows = _equation_rows(sodb) if rows is None else rows
    fms = forms()
    law_ids = [l["id"] for l in registry()["laws"]]
    rng = random.Random(20261005)
    math_memo: dict = {}
    out = []
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        for r in rows:
            st = cache.get(r["formula_hash"]) or {}
            expr = None
            if st.get("status") == "ok" and st.get("kind") == "equation" and st.get("srepr"):
                try:
                    expr = to_expr(st["srepr"])
                except Exception:
                    expr = None
            names = set(json.loads(st.get("symbols") or "[]")) if st else set()
            params = json.loads(r.get("parameters") or "[]")
            tokens = {}
            for n, p in param_symbols(params, names).items():
                q = quantity_name(p.get("description") or "")
                qid = resolve(q).quantity_id if q else None
                if qid:
                    tokens[n] = qid
            eq_q = set(tokens.values())
            for p in params:                                 # parameters whose symbol is not in a parsed tree
                q = quantity_name(p.get("description") or "")
                qid = resolve(q).quantity_id if q else None
                if qid:
                    eq_q.add(qid)
            concepts = set(concepts_of(r)) if concepts_of else set()
            for law_id in law_ids:
                st_ = s_text(law_id, r)
                sq = s_quantity(law_id, eq_q, names)
                sc = s_concept(law_id, concepts)
                sm, method, mapping, variant = 0.0, None, {}, None
                if expr is not None:
                    for f in (f for f in fms if f.law_id == law_id):
                        key = (st["structural_hash"], f.structural_hash, tuple(sorted(tokens.items())))
                        if key not in math_memo:
                            math_memo[key] = s_math(f, st, expr, tokens, rng)
                        v, meth, mp = math_memo[key]
                        if v > sm:
                            sm, method, mapping, variant = v, meth, mp, f.variant
                score = (WEIGHTS["quantity"] * sq + WEIGHTS["math"] * sm + WEIGHTS["text"] * st_
                         + WEIGHTS["concept"] * sc)
                if score < CANDIDATE:
                    continue
                # a shape match under renaming says nothing about meaning: keep it only with
                # another channel (text, quantities, concept) or an exact/algebraic match
                if sm < 1.0 and not (st_ or sq or sc):
                    continue
                status = "accepted" if score >= ACCEPT else "candidate"
                why = None
                if status == "accepted" and st.get("status") == "degenerate":
                    status, why = "candidate", "formula_degenerate"          # Nougat lost a distinction
                if status == "accepted" and expr is not None and sm == 0:
                    status, why = "candidate", "formula_does_not_match"     # text cannot outvote the formula
                if status == "accepted" and _foreign_name(law_id, st.get("lhs") or ""):
                    status, why = "candidate", "left_side_names_another_quantity"
                out.append({"eq_id": r["equation_id"], "paper_id": r["paper_id"], "law_id": law_id,
                            "variant": variant, "score": round(score, 3), "status": status, "capped": why,
                            "s_quantity": sq, "s_math": sm, "s_text": st_, "s_concept": sc,
                            "math_method": method, "mapping": json.dumps(mapping, ensure_ascii=False),
                            "laws_version": registry()["version"]})
    df = pd.DataFrame(out)
    stats = {"equations": len(rows), "links": len(df)}
    if len(df):
        stats["by_status"] = df["status"].value_counts().to_dict()
        acc = df[df["status"] == "accepted"]
        stats["accepted_by_law"] = acc["law_id"].value_counts().to_dict()
        stats["accepted_papers"] = int(acc["paper_id"].nunique())
        stats["math_method"] = df["math_method"].value_counts().to_dict()
    return df, stats


def out_path() -> Path:
    return ROOT / "data" / "equations" / f"law_links_{registry()['version']}.parquet"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
    out = out_path()
    if out.exists():
        print(f"{out} exists; never overwritten (bump the registry version)")
        return 1
    from src.graph.equation_kg_loader import _metric_of
    df, stats = link(concepts_of=lambda r: _metric_of(r))
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out, index=False)
    print(json.dumps(stats, indent=1, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
