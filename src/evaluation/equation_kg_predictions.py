"""
equation_kg_predictions.py — freeze the system's predictions for every item of an equation gold set
(docs_v2/EQUATION_KG_PLAN.md, phase 6).

Labels (people, verify.human_check) and predictions (versioned components) stay separate: a better
component means a new predictions file against the same labels. Writes

    data/gold/equation_kg_<version>/predictions/<UTC stamp>.jsonl    one row per (target_kind, target_id)
    data/gold/equation_kg_<version>/predictions/<UTC stamp>.json     component versions, counts

Never overwrites. Usage:
    python -m src.evaluation.equation_kg_predictions --gold v1
"""
from __future__ import annotations

import argparse
import json
import sys
import warnings
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
GOLD = ROOT / "data" / "gold"


def _jsonl(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.open()] if path.exists() else []


def versions() -> dict:
    from src.document import formula_algebra, formula_code, formula_structure
    from src.ontology.laws import registry
    from src.ontology.quantities import ontology
    return {"quantities": ontology()["version"], "structure": formula_structure.PARSER_VERSION,
            "algebra": formula_algebra.ALGEBRA_VERSION, "codegen": formula_code.CODEGEN_VERSION,
            "laws": registry()["version"]}


class Context:
    """Everything the predictions are read from, loaded once."""

    def __init__(self, cache: dict | None = None, algebra: pd.DataFrame | None = None,
                 law_links: pd.DataFrame | None = None, code: pd.DataFrame | None = None):
        from src.document import formula_algebra, formula_code
        from src.document.formula_structure import load_cache
        from src.ontology.laws import out_path
        self.cache = load_cache() if cache is None else cache
        self.algebra = (pd.read_parquet(formula_algebra.OUT) if formula_algebra.OUT.exists() else pd.DataFrame()) \
            if algebra is None else algebra
        self.links = (pd.read_parquet(out_path()) if out_path().exists() else pd.DataFrame()) \
            if law_links is None else law_links
        self.code = (pd.read_parquet(formula_code.OUT) if formula_code.OUT.exists() else pd.DataFrame()) \
            if code is None else code
        self._alg = {}
        for r in self.algebra.to_dict("records"):
            key = tuple(sorted((r["structural_hash_a"], r["structural_hash_b"])))
            self._alg[key] = r["verdict"]
        self._links = {}
        for r in self.links.to_dict("records"):
            self._links[(r["eq_id"], r["law_id"])] = r
        self._code = {r["structural_hash"]: r for r in self.code.to_dict("records")}

    def structure(self, formula_hash: str | None) -> dict:
        return self.cache.get(formula_hash or "") or {}

    def accepted_laws(self, eq_id: str) -> set[str]:
        return {l for (e, l), r in self._links.items() if e == eq_id and r["status"] == "accepted"}


def predict_quantity_name(name: str) -> dict:
    from src.ontology.quantity_map import resolve
    m = resolve(name)
    return {"quantity_id": m.quantity_id, "method": m.method, "score": m.score, "qualifiers": m.qualifiers}


def predict_parameter(item: dict) -> dict:
    from src.document.formula_parameters import quantity_name
    from src.ontology.quantities import check_dimension
    sysv = item.get("system") or {}
    q = quantity_name(sysv.get("description") or "")
    m = predict_quantity_name(q) if q else {"quantity_id": None, "method": "empty"}
    return {**{k: sysv.get(k) for k in ("description", "unit", "value", "source")}, "quantity": q,
            "quantity_id": m["quantity_id"], "quantity_method": m["method"],
            "dimension_check": check_dimension(m["quantity_id"], sysv.get("unit")) if sysv.get("unit") else None}


def predict_equation(item: dict, ctx: Context) -> dict:
    eq = item["equation"]
    st = ctx.structure(eq.get("formula_hash"))
    code = ctx._code.get(st.get("structural_hash") or "") or {}
    sysv = item.get("system") or {}
    return {"lhs_symbol": sysv.get("lhs_symbol"), "purpose": sysv.get("purpose"),
            "structure_status": st.get("status") or ("no_latex" if not eq.get("latex") else "not_cached"),
            "structural_hash": st.get("structural_hash"), "canonical_expression": st.get("canonical_expression"),
            "code_status": code.get("status"), "code_check": code.get("check"),
            "accepted_laws": sorted(ctx.accepted_laws(item["target_id"]))}


def predict_pair(item: dict, ctx: Context) -> dict:
    """The relation the deployed system asserts (EXACT > ALGEBRAIC > SAME_LAW > DIFFERENT), and what the
    algebra checker says when run directly on the pair with symbols paired by name."""
    a, b = item["a"], item["b"]
    sa, sb = ctx.structure(a.get("formula_hash")), ctx.structure(b.get("formula_hash"))
    ha, hb = sa.get("structural_hash"), sb.get("structural_hash")
    if a.get("formula_hash") == b.get("formula_hash") or (ha and ha == hb):
        deployed = "EXACT"
    elif ha and hb and ctx._alg.get(tuple(sorted((ha, hb)))) == "ALGEBRAIC":
        deployed = "ALGEBRAIC"
    elif ctx.accepted_laws(a["equation_id"]) & ctx.accepted_laws(b["equation_id"]):
        deployed = "SAME_LAW"
    else:
        deployed = "DIFFERENT"
    checker = None
    if ha and hb and sa.get("kind") == "equation" and sb.get("kind") == "equation":
        from src.document.formula_algebra import equivalent, to_expr
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                ea, eb = to_expr(sa["srepr"]), to_expr(sb["srepr"])
                checker = "EXACT" if ha == hb else equivalent((ea.lhs, ea.rhs), (eb.lhs, eb.rhs), timeout=4)[0]
        except Exception:
            checker = "UNKNOWN"
    return {"deployed": deployed, "checker": checker, "parsed": [bool(ha), bool(hb)],
            "same_laws": sorted(ctx.accepted_laws(a["equation_id"]) & ctx.accepted_laws(b["equation_id"]))}


def predict_law_link(item: dict, ctx: Context) -> dict:
    eq_id, law_id = item["target_id"].split("||", 1)
    r = ctx._links.get((eq_id, law_id))
    if r is None:
        return {"status": "none", "score": 0.0}
    return {k: (None if isinstance(v, float) and v != v else v) for k, v in r.items()
            if k in ("status", "score", "variant", "s_quantity", "s_math", "s_text", "s_concept", "math_method", "capped")}


def predict(item: dict, ctx: Context) -> dict | None:
    k = item["target_kind"]
    if k == "quantity_name":
        return predict_quantity_name(item["target_id"])
    if k == "parameter":
        return predict_parameter(item)
    if k == "equation":
        return predict_equation(item, ctx)
    if k == "equation_pair":
        return predict_pair(item, ctx)
    if k == "law_link":
        return predict_law_link(item, ctx)
    if k == "metric_fact":
        return item.get("system")
    return None


FILES = ("equations.jsonl", "parameters.jsonl", "quantity_names.jsonl", "equation_pairs.jsonl", "law_links.jsonl",
         "metric_facts.jsonl")


def build(gold_dir: Path, ctx: Context | None = None) -> tuple[list[dict], dict]:
    ctx = ctx or Context()
    rows = []
    for name in FILES:
        for it in _jsonl(gold_dir / name):
            rows.append({"target_kind": it["target_kind"], "target_id": it["target_id"], "prediction": predict(it, ctx)})
    counts: dict = {}
    for r in rows:
        counts[r["target_kind"]] = counts.get(r["target_kind"], 0) + 1
    return rows, counts


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--gold", required=True, help="gold version, e.g. v1")
    args = ap.parse_args(argv)
    gold_dir = GOLD / f"equation_kg_{args.gold}"
    if not (gold_dir / "MANIFEST.json").exists():
        print(f"no frozen gold set at {gold_dir}", file=sys.stderr)
        return 2
    rows, counts = build(gold_dir)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = gold_dir / "predictions"
    out.mkdir(exist_ok=True)
    path = out / f"{stamp}.jsonl"
    if path.exists():
        print(f"{path} exists", file=sys.stderr)
        return 2
    with path.open("w") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")
    meta = {"created_at": stamp, "gold": args.gold, "versions": versions(), "counts": counts}
    (out / f"{stamp}.json").write_text(json.dumps(meta, indent=1))
    print(json.dumps(meta, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
