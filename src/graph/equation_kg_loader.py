"""
equation_kg_loader.py — equation_records.parquet → Neo4j.

    (Paper)-[:HAS_EQUATION]->(Equation {formula_hash, latex, image_path, image_sha256})
    (Equation)-[:COMPUTES {derivative}]->(Quantity)      ← what the equation calculates
    (Equation)-[:HAS_PARAMETER]->(Parameter {symbol, description, unit, value, param_hash})
    (Parameter)-[:QUANTIFIES]->(Quantity {name})          ← search formulas by quantity
    (Equation)-[:HAS_STRUCTURE]->(FormulaStructure {structural_hash, canonical_expression})
                                                          ← EXACT equivalence (src/document/formula_structure.py)
    (Quantity)-[:NORMALIZED_TO {method, score}]->(QuantityConcept {canonical_id, dimension})
                                                          ← quantity ontology (src/ontology/quantities.json)
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
from src.document.formula_structure import PARSER_VERSION, load_cache
from src.ontology.quantities import check_dimension, ontology
from src.ontology.quantity_map import resolve

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
    from src.normalization.ontology_matcher import normalize_entity
    out = []
    for m in extractor.extract_methods(text, strict=False):
        n = normalize_entity(m.get("name", ""), "method", allow_semantic=False)
        cid = n.get("canonical_id")
        if cid and n.get("match_type") in ("alias", "exact") and n.get("confidence", 0) >= 0.9:
            out.append((cid, (m.get("evidence") or "")[:300]))
    return out


def quantity_rows(names: set[str]) -> tuple[list[dict], list[dict]]:
    """QuantityConcept rows for the whole ontology and NORMALIZED_TO rows for the names that match."""
    o = ontology()
    concepts = [{"canonical_id": q["id"], "label": q["label"], "kind": q.get("kind"),
                 "dimension_text": q.get("dimension_text"), "typical_unit": q.get("typical_unit"),
                 "alt_dimensions": [a["dimension_text"] for a in q.get("alt_dimensions", [])],
                 "ontology_version": o["version"]} for q in o["by_id"].values()]
    links = []
    for n in sorted(names):
        m = resolve(n)
        if m.quantity_id:
            links.append({"name": n, "canonical_id": m.quantity_id, "method": m.method, "score": m.score,
                          "qualifiers": m.qualifiers, "ontology_version": o["version"]})
    return concepts, links


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
    structures = load_cache()
    for f in files:
        d = pd.read_parquet(f)
        for r in d.to_dict("records"):
            r = {k: (None if isinstance(v, float) and v != v else v) for k, v in r.items()}
            eq_id = r["equation_id"]
            pur = r.get("purpose") or ""
            deriv = pur.startswith("rate of change of ")
            core = pur[len("rate of change of "):] if deriv else pur
            computes = quantity_name(core) if r.get("purpose_source") in ("lhs_definition",) else None
            st = structures.get(r["formula_hash"]) or {}
            eq_rows.append({**r, "eq_id": eq_id, "computes": computes, "derivative": deriv,
                            "structure_status": st.get("status") or ("no_latex" if not r.get("latex") else None),
                            "structural_hash": st.get("structural_hash"),
                            "canonical_expression": st.get("canonical_expression"),
                            "structure_symbols": st.get("symbols"),
                            "parser_version": PARSER_VERSION if st else None})
            for p in json.loads(r.get("parameters") or "[]"):
                qname = quantity_name(p.get("description") or "")
                qid = resolve(qname).quantity_id if qname else None
                par_rows.append({
                    "eq_id": eq_id, "param_id": f"{eq_id}:{p['symbol']}", "paper_id": r["paper_id"],
                    "formula_hash": r["formula_hash"], **{k: p.get(k) for k in
                    ("symbol", "symbol_tex", "description", "unit", "value", "source", "param_hash")},
                    # recomputed here so that a better quantity_name needs no record rebuild
                    "quantity": qname,
                    "quantity_id": qid,
                    "dimension_check": check_dimension(qid, p.get("unit")) if p.get("unit") else None,
                })
            for cid in _metric_of(r):
                concept_rows.append({"eq_id": eq_id, "canonical_id": cid, "node_label": "Metric",
                                     "formula_hash": r["formula_hash"], "evidence": r.get("lead_in")})
            for cid, ev in _methods_of(r, extractor):
                if cid.startswith("method."):
                    concept_rows.append({"eq_id": eq_id, "canonical_id": cid, "node_label": "Method",
                                         "formula_hash": r["formula_hash"], "evidence": ev})
    concepts, links = quantity_rows({r["quantity"] for r in par_rows if r["quantity"]}
                                    | {r["computes"] for r in eq_rows if r["computes"]})
    stats = {"papers": len(files), "equations": len(eq_rows), "parameters": len(par_rows),
             "concept_edges": len(concept_rows), "quantity_links": len(links),
             "dimension_mismatch": sum(r["dimension_check"] == "mismatch" for r in par_rows),
             "with_structure": sum(bool(r["structural_hash"]) for r in eq_rows)}
    log.info("equation records: %s", stats)
    if dry_run:
        return stats
    from src.graph.neo4j_writer import GraphWriter
    with GraphWriter() as gw:
        gw.create_constraints()                    # IF NOT EXISTS: QuantityConcept, parameter index
        gw.mark_equation_parameters_stale([r["eq_id"] for r in eq_rows])
        gw.write_equation_records(eq_rows)
        gw.write_equation_parameters(par_rows)
        gw.write_equation_concept_edges(concept_rows)
        gw.write_quantity_concepts(concepts, links)
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
