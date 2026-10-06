"""
formula_algebra.py — ALGEBRAIC equivalence of equations (docs_v2/EQUATION_KG_PLAN.md, phase 3).

Two equations are ALGEBRAIC equivalents when they have the same solution set after their
variables are paired: Q = AV and V = Q/A; S = 25400/CN − 254 and S = (25400 − 254 CN)/CN.
Never all pairs: only candidates.

    1. candidate blocks — equations with the same variable set, either by symbol name
       (within the usual notation) or by quantity concept (src/ontology, any notation:
       Q = AV in one paper, q = a·v with q "discharge" in another), or mixed (concept when
       known, name otherwise); P = IV never meets Q = AV
    2. variable pairing — by name, or by quantity concept; two symbols with one concept
       (inflow and outflow, both discharge) are tried in every order, at most 24 orders
    3. the check — Sum, Derivative, Integral and applied functions are opaque terms shared by
       both sides; then (a) lhs−rhs equal up to a constant factor, or (b) for a variable v:
       solve each equation for v and evaluate the solutions in the other one at random
       positive points, both directions (so the equivalence holds on positive values, as for
       physical quantities: Q n²|Q| = … and Q² n² = … agree there)
Verdicts: ALGEBRAIC | DIFFERENT (a solution of one fails the other) | UNKNOWN (nothing solvable).
EXACT pairs (same structural hash) are phase 2 and are not repeated here.

Input: data/equations/structure_<PARSER_VERSION>.parquet + equation_records parameters.
Output: data/equations/algebraic_<ALGEBRA_VERSION>.parquet, one row per pair of structures.
The LLM plays no part.

Usage:
    python -m src.document.formula_algebra [--max-block 40]
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
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from src.document.formula_structure import CACHE, _time_limit, load_cache

log = logging.getLogger("geohydro.document.formula_algebra")
ROOT = Path(__file__).resolve().parents[2]
SODB = Path(os.getenv("SODB_DIR", str(ROOT / "data" / "sodb")))
ALGEBRA_VERSION = "alg1"
OUT = ROOT / "data" / "equations" / f"algebraic_{ALGEBRA_VERSION}.parquet"
TRIALS = 6
TOL = 1e-7


@dataclass
class Eq:
    eq_id: str
    structural_hash: str
    srepr: str
    tokens: dict           # symbol name → quantity_id (only the mapped ones)

    @property
    def expr(self):
        if self.structural_hash not in _EXPR:
            _EXPR[self.structural_hash] = to_expr(self.srepr)
        return _EXPR[self.structural_hash]

    @property
    def names(self) -> frozenset:
        return frozenset(x.name for x in self.expr.free_symbols)


_EXPR: dict[str, object] = {}


# ── symbols ────────────────────────────────────────────────────────────────────

def symbol_key(sym: str | None) -> str:
    """Parameter symbol → the name formula_structure gives it: N_{h,max} → N_hmax, l(\\theta) → l."""
    s = re.sub(r"\(.*\)$", "", sym or "")
    s = re.sub(r"\\(?:mathrm|text|rm|mathit)\s*", "", s)
    # accents as formula_structure names them: \bar{O} → O_bar, \hat{y}_{i} → y_hati
    acc = re.match(r"\\(bar|overline|hat|widehat|tilde|widetilde|dot)\s*\{?\s*(\\?[A-Za-z]+)\s*\}?(?:_\s*\{?([^{}]*)\}?)?$", s.strip())
    if acc:
        kind = {"overline": "bar", "widehat": "hat", "widetilde": "tilde"}.get(acc.group(1), acc.group(1))
        return acc.group(2).lstrip("\\") + "_" + kind + re.sub(r"[{}\s\\,]", "", acc.group(3) or "")
    return re.sub(r"[{}\s\\,]", "", s)


def param_symbols(params: list[dict], names: set[str]) -> dict[str, dict]:
    """Structure symbol name → its parameter; a bare base ("a") also claims a unique indexed
    symbol ("a_i")."""
    out = {}
    for p in params:
        k = symbol_key(p.get("symbol")) or symbol_key(p.get("symbol_tex"))
        if not k:
            continue
        if k in names:
            out.setdefault(k, p)
            continue
        base = [n for n in names if n.split("_", 1)[0] == k]
        if len(base) == 1:
            out.setdefault(base[0], p)
    return out


# ── sympy helpers ──────────────────────────────────────────────────────────────

def to_expr(srepr: str):
    """The cached canonical tree (sympy.srepr output) back to an expression."""
    import sympy as sp
    ns = {k: getattr(sp, k) for k in dir(sp) if not k.startswith("_")}
    return eval(srepr, {"__builtins__": {}}, ns)          # noqa: S307 — our own srepr, no user input


def _opaque(exprs: list, table: dict) -> list:
    """Replace Sum/Derivative/Integral/applied functions by shared symbols keyed by srepr."""
    import sympy as sp
    from sympy.core.function import AppliedUndef
    kinds = (sp.Sum, sp.Product, sp.Derivative, sp.Integral, AppliedUndef)
    out = []
    for e in exprs:
        rep = {}
        for node in sp.preorder_traversal(e):
            if isinstance(node, kinds) and node not in rep:
                key = sp.srepr(node)
                if key not in table:
                    table[key] = sp.Symbol(f"op{len(table)}_")
                rep[node] = table[key]
        out.append(e.xreplace(rep) if rep else e)
    return out


def _num(x) -> complex | None:
    try:
        z = complex(x.evalf())
    except Exception:
        return None
    if z != z or abs(z) == float("inf"):
        return None
    return z


def _subset(sols, v, other: tuple, symbols: list, rng: random.Random, timeout: int) -> bool | None:
    """True when every solution for v satisfies the other equation at random points;
    False on a definite failure; None when too few points could be evaluated."""
    import sympy as sp
    checked = 0
    for _ in range(TRIALS):
        vals = {x: sp.Float(rng.uniform(0.7, 2.3)) for x in symbols if x != v}
        for sol in sols:
            try:
                with _time_limit(timeout):
                    val = _num(sol.subs(vals))
                    if val is None or abs(val.imag) > 1e-9 * (1 + abs(val.real)):
                        continue
                    at = {**vals, v: sp.Float(val.real)}
                    a, b = _num(other[0].subs(at)), _num(other[1].subs(at))
            except Exception:
                continue
            if a is None or b is None or any(abs(z.imag) > 1e-9 * (1 + abs(z.real)) for z in (a, b)):
                continue                                      # outside the real domain (log of H − z < 0)
            if abs(a - b) / (1 + abs(a) + abs(b)) > TOL:
                return False
            checked += 1
    return True if checked >= 3 else None


def equivalent(a: tuple, b: tuple, *, timeout: int = 4, seed: int = 20261005) -> tuple[str, str | None, str | None]:
    """(verdict, method, variable) for two equations given as (lhs, rhs) over shared symbols."""
    import sympy as sp
    table: dict = {}
    la, ra, lb, rb = _opaque([a[0], a[1], b[0], b[1]], table)
    fa, fb = la - ra, lb - rb
    if fa.free_symbols != fb.free_symbols:
        return "DIFFERENT", "variables", None
    symbols = sorted(fa.free_symbols, key=lambda x: x.name)
    try:
        with _time_limit(timeout):
            ratio = sp.simplify(fa / fb)
        if ratio != 0 and not ratio.free_symbols and ratio.is_finite:
            return "ALGEBRAIC", "ratio", None
    except Exception:
        pass
    rng = random.Random(seed)
    definite_fail = None
    order = sorted(symbols, key=lambda x: (fa.count(x) + fb.count(x), x.name))   # isolate the easy ones first
    for v in order[:5]:
        try:
            with _time_limit(timeout):
                sa = sp.solve(fa, v)
                sb = sp.solve(fb, v)
        except Exception:
            continue
        if not sa or not sb:
            continue
        ab = _subset(sa, v, (lb, rb), symbols, rng, timeout)
        ba = _subset(sb, v, (la, ra), symbols, rng, timeout)
        if ab and ba:
            return "ALGEBRAIC", "solve", v.name
        if ab is False or ba is False:
            definite_fail = v.name
    return ("DIFFERENT", "solve", definite_fail) if definite_fail else ("UNKNOWN", None, None)


def _sides(e):
    return (e.lhs, e.rhs)


# ── candidates ─────────────────────────────────────────────────────────────────

def collect(sodb: Path = SODB, cache: dict | None = None) -> list[Eq]:
    from src.document.formula_parameters import quantity_name
    from src.ontology.quantity_map import resolve
    cache = load_cache(CACHE) if cache is None else cache
    out = []
    for f in sorted(sodb.glob("*/equation_records.parquet")):
        d = pd.read_parquet(f, columns=["equation_id", "formula_hash", "parameters"])
        for eq_id, fh, params in zip(d["equation_id"], d["formula_hash"], d["parameters"]):
            st = cache.get(fh)
            if not st or st.get("status") != "ok" or st.get("kind") != "equation" or not st.get("srepr"):
                continue
            names = set(json.loads(st.get("symbols") or "[]"))
            tokens = {}
            for name, p in param_symbols(json.loads(params or "[]"), names).items():
                q = quantity_name(p.get("description") or "")
                qid = resolve(q).quantity_id if q else None
                if qid:
                    tokens[name] = qid
            out.append(Eq(eq_id, st["structural_hash"], st["srepr"], tokens))
    return out


def token(e: Eq, name: str, by: str) -> str:
    """What a symbol stands for in a block: its name, its quantity concept, or the concept
    when known and the name otherwise (mixed)."""
    if by == "name":
        return name
    if by == "quantity":
        return e.tokens[name]
    return e.tokens.get(name) or "sym:" + name


def blocks(eqs: list[Eq], max_block: int) -> tuple[dict, int]:
    """Candidate blocks (by, signature) → {structural_hash: Eq}; returns the blocks and how many
    were too large to check. A mixed block is kept only when it differs from both others."""
    out: dict = defaultdict(dict)
    for e in eqs:
        try:
            names = e.names
        except Exception:
            continue
        if len(names) < 2:
            continue
        out[("name", tuple(sorted(names)))].setdefault(e.structural_hash, e)
        mapped = [n for n in names if n in e.tokens]
        if len(mapped) == len(names):
            out[("quantity", tuple(sorted(token(e, n, "quantity") for n in names)))].setdefault(e.structural_hash, e)
        elif mapped:
            out[("mixed", tuple(sorted(token(e, n, "mixed") for n in names)))].setdefault(e.structural_hash, e)
    keep, skipped = {}, 0
    for k, v in out.items():
        if len(v) < 2:
            continue
        if len(v) > max_block:
            skipped += 1
            continue
        keep[k] = v
    return keep, skipped


def pairings(ea: Eq, eb: Eq, by: str) -> list[dict]:
    """Ways to pair b's symbols with a's: symbols with the same token, in every order when a
    token covers several symbols (inflow and outflow, both discharge), at most 24."""
    ga, gb = defaultdict(list), defaultdict(list)
    for n in ea.names:
        ga[token(ea, n, by)].append(n)
    for n in eb.names:
        gb[token(eb, n, by)].append(n)
    if set(ga) != set(gb) or any(len(ga[k]) != len(gb[k]) for k in ga):
        return []
    keys = sorted(gb)
    out = []
    for combo in itertools.product(*[itertools.permutations(sorted(ga[k])) for k in keys]):
        m = {}
        for k, perm in zip(keys, combo):
            m.update(dict(zip(sorted(gb[k]), perm)))
        out.append(m)
        if len(out) >= 24:
            break
    return out


def check_pair(ea: Eq, eb: Eq, by: str) -> dict:
    import sympy as sp
    best = {"verdict": "UNKNOWN", "method": None, "variable": None, "mapping": None}
    for m in pairings(ea, eb, by):
        rename = {sp.Symbol(k): sp.Symbol(v) for k, v in m.items() if k != v}
        xb = eb.expr.xreplace(rename) if rename else eb.expr
        verdict, method, var = equivalent(_sides(ea.expr), _sides(xb))
        if verdict == "ALGEBRAIC":
            return {"verdict": verdict, "method": method, "variable": var,
                    "mapping": {k: v for k, v in m.items() if k != v}}
        if verdict == "DIFFERENT" and best["verdict"] == "UNKNOWN":
            best = {"verdict": verdict, "method": method, "variable": var, "mapping": None}
    return best


def run(*, max_block: int = 40, sodb: Path = SODB, cache: dict | None = None) -> tuple[pd.DataFrame, dict]:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        eqs = collect(sodb, cache)
        bl, skipped = blocks(eqs, max_block)
        rows, seen = [], set()
        for (by, key), members in sorted(bl.items(), key=lambda kv: (kv[0][0], sorted(kv[0][1]))):
            for ha, hb in itertools.combinations(sorted(members), 2):
                if (ha, hb) in seen:
                    continue
                r = check_pair(members[ha], members[hb], by)
                seen.add((ha, hb))
                rows.append({"structural_hash_a": ha, "structural_hash_b": hb, "block": by,
                             "signature": json.dumps(list(key), ensure_ascii=False),
                             "eq_a": members[ha].eq_id, "eq_b": members[hb].eq_id,
                             "verdict": r["verdict"], "method": r["method"], "variable": r["variable"],
                             "mapping": json.dumps(r["mapping"] or {}, ensure_ascii=False),
                             "domain": "positive reals" if r["method"] == "solve" else "all",
                             "algebra_version": ALGEBRA_VERSION})
    df = pd.DataFrame(rows)
    stats = {"equations": len(eqs), "with_quantity_signature": sum(bool(e.tokens) and e.names <= set(e.tokens)
                                                                 for e in eqs if _safe_names(e)),
             "blocks": len(bl), "blocks_too_large": skipped, "pairs": len(df)}
    if len(df):
        stats["verdicts"] = df["verdict"].value_counts().to_dict()
        stats["verdicts_by_block"] = df.groupby("block")["verdict"].value_counts().unstack(fill_value=0).to_dict("index")
    return df, stats


def _safe_names(e: Eq) -> bool:
    try:
        return bool(e.names)
    except Exception:
        return False


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--max-block", type=int, default=40)
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
    if args.out.exists():
        print(f"{args.out} exists; results are never overwritten (bump ALGEBRA_VERSION)")
        return 1
    df, stats = run(max_block=args.max_block)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(args.out, index=False)
    print(json.dumps(stats, indent=1, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
