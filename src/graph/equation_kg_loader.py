"""
equation_kg_loader.py — equation_records.parquet → Neo4j.

    (Paper)-[:HAS_EQUATION]->(Equation {formula_hash, latex, image_path, image_sha256})
    (Equation)-[:COMPUTES {derivative}]->(Quantity)      ← what the equation calculates
    (Equation)-[:HAS_PARAMETER]->(Parameter {symbol, description, unit, value, param_hash})
    (Parameter)-[:QUANTIFIES]->(Quantity {name})          ← search formulas by quantity
    (Equation)-[:EQUATION_GROUNDS_TO {formula_hash}]->(Method)
    (Equation)-[:DEFINES_METRIC {formula_hash}]->(Metric)  ← NSE = 1 − …, KGE = …

A reload marks parameters that are no longer extracted with stale = true; queries
should use WHERE NOT p.stale.

MERGE only. Usage:
    python -m src.graph.equation_kg_loader [--paper-list FILE] [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from pathlib import Path

import pandas as pd

from src.document.formula_parameters import quantity_name

log = logging.getLogger("geohydro.graph.equation_kg_loader")
ROOT = Path(__file__).resolve().parents[2]
SODB = Path(os.getenv("SODB_DIR", str(ROOT / "data" / "sodb")))


def _metric_of(row: dict) -> list[str]:
    """A formula whose left-hand side names a metric defines it (NSE = 1 − …)."""
    from src.extraction.table_extractor import _map_all
    lhs = re.split(r"=", (row.get("latex") or row.get("text_grobid") or ""), maxsplit=1)[0]
    lhs = re.sub(r"\\\\(?:text|mathrm|rm)\\s*\\{([^}]*)\\}", r"\\1", lhs)
    lhs = re.sub(r"[\\\\{}\\[\\]$]", " ", lhs)
    return [cid for cid, label, _ in _map_all(lhs) if label == "Metric"]


def _methods_of(row: dict, extractor) -> list[tuple[str, str]]:
    """Methods named in the sentence before the equation and in its definition clause."""
    text = " ".join(filter(None, [row.get("lead_in"), row.get("clause")]))
    if not text or extractor is None:
        return []
    out = []
    for m in extractor.extract_methods(text, strict=False):
        cid = (m.get("kb_metadata") or {}).get("canonical_id") or m.get("canonical_id")
        if cid:
            out.append((cid, (m.get("evidence") or "")[:300]))
    return out


def load(paper_ids: list[str] | None = None, dry_run: bool = False) -> dict:
    files = sorted(SODB.glob("*/equation_records.parquet"))
    if paper_ids is not None:
        keep = set(paper_ids)
        files = [f for f in files if f.parent.name in keep]
    eq_rows, par_rows, concept_rows = [], [], []
    try:
        from src.ingestion.knowledge.entity_extractor import EntityExtractor
        from src.ingestion.stages.entity_pipeline import _get_kb
        from src.normalization.normalization_utils import normalize_paper_entities  # noqa: F401
        extractor = EntityExtractor(_get_kb())
    except Exception as exc:                     # the graph still gets equations and parameters
        log.warning("method extractor unavailable: %s", exc)
        extractor = None
    for f in files:
        d = pd.read_parquet(f)
        for r in d.to_dict("records"):
            r = {k: (None if isinstance(v, float) and v != v else v) for k, v in r.items()}
            eq_id = r["equation_id"]
            pur = r.get("purpose") or ""
            deriv = pur.startswith("rate of change of ")
            core = pur[len("rate of change of "):] if deriv else pur
            computes = quantity_name(core) if r.get("purpose_source") in ("lhs_definition",) else None
            eq_rows.append({**r, "eq_id": eq_id, "computes": computes, "derivative": deriv})
            for p in json.loads(r.get("parameters") or "[]"):
                par_rows.append({
                    "eq_id": eq_id, "param_id": f"{eq_id}:{p['symbol']}", "paper_id": r["paper_id"],
                    "formula_hash": r["formula_hash"], **{k: p.get(k) for k in
                    ("symbol", "symbol_tex", "description", "unit", "value", "source", "param_hash", "quantity")},
                })
            for cid in _metric_of(r):
                concept_rows.append({"eq_id": eq_id, "canonical_id": cid, "node_label": "Metric",
                                     "formula_hash": r["formula_hash"], "evidence": r.get("lead_in")})
            for cid, ev in _methods_of(r, extractor):
                if cid.startswith("method."):
                    concept_rows.append({"eq_id": eq_id, "canonical_id": cid, "node_label": "Method",
                                         "formula_hash": r["formula_hash"], "evidence": ev})
    stats = {"papers": len(files), "equations": len(eq_rows), "parameters": len(par_rows),
             "concept_edges": len(concept_rows)}
    log.info("equation records: %s", stats)
    if dry_run:
        return stats
    from src.graph.neo4j_writer import GraphWriter
    with GraphWriter() as gw:
        gw.mark_equation_parameters_stale([r["eq_id"] for r in eq_rows])
        gw.write_equation_records(eq_rows)
        gw.write_equation_parameters(par_rows)
        gw.write_equation_concept_edges(concept_rows)
    return stats


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--paper-list", type=Path)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
    ids = [x.strip() for x in args.paper_list.read_text().splitlines() if x.strip()] if args.paper_list else None
    print(load(ids, args.dry_run))
    return 0


if __name__ == "__main__":
    sys.exit(main())
