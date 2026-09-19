"""Per-thesis package of what is sent to Gemini — the audit trail of the adjudication.

``classify_relation`` and ``extract_fields`` already build their own prompts;
this module writes, per thesis, one JSON file that records *what evidence was in
play* when they ran: the thesis card, our own claims that the thesis underwrites
(from ``OWN_EVIDENCE.csv``), and the candidate papers with the passages offered.
A reviewer can open ``gemini_packages/T25.json`` and see exactly which numbers
and which passages stood behind every relation verdict. Nothing here calls a
model.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import pandas as pd

from src.paper_3._utils import OUT_DIR
from src.paper_3.theses import Thesis, load_theses

logger = logging.getLogger(__name__)

PACKAGE_DIR = "gemini_packages"
OWN_COLUMNS = ["claim_id", "claim_text", "value_resolved", "uncertainty_resolved",
               "n_resolved", "audit_status", "source_table", "snapshot_sha256"]


def own_claims_for(thesis_id: str, evidence: pd.DataFrame) -> list[dict]:
    """Own-data claims whose ``literature_thesis_ids`` name this thesis."""
    ids = evidence["literature_thesis_ids"].fillna("").astype(str)
    mask = ids.str.split(";").apply(lambda xs: thesis_id in [x.strip() for x in xs])
    return evidence.loc[mask, OWN_COLUMNS].to_dict("records")


def candidate_papers_for(thesis_id: str, candidates: pd.DataFrame,
                         max_papers: int = 40) -> list[dict]:
    sub = candidates[candidates["thesis_id"] == thesis_id]
    if "prefilter_pass" in sub.columns:
        sub = sub[sub["prefilter_pass"].astype(str).str.lower().isin(["true", "1"])]
    if "prefilter_score" in sub.columns:
        sub = sub.sort_values("prefilter_score", ascending=False)
    keep = [c for c in ("paper_id", "doi", "title", "year", "retrieval_origin",
                        "n_keyterm_families_hit", "best_distance", "evidence_level") if c in sub.columns]
    return sub.head(max_papers)[keep].to_dict("records")


def build_thesis_package(thesis: Thesis, evidence: pd.DataFrame,
                         candidates: pd.DataFrame, passages: dict[str, list[dict]] | None = None) -> dict:
    """Pure: the JSON-serialisable record for one thesis."""
    papers = candidate_papers_for(thesis.id, candidates)
    if passages:
        for p in papers:
            p["passages"] = passages.get(str(p.get("paper_id", "")), [])
    return {
        "thesis": {"id": thesis.id, "block": thesis.block, "statement": thesis.statement,
                   "manuscript_anchor": thesis.manuscript_anchor,
                   "numeric_anchor": thesis.numeric_anchor,
                   "primary_family": list(thesis.primary_family),
                   "controls": [c.doi for c in thesis.positive_controls]},
        "own_claims": own_claims_for(thesis.id, evidence),
        "candidate_papers": papers,
        "n_candidates": len(papers),
    }


def run(out_dir: Path = OUT_DIR, theses: list[Thesis] | None = None) -> Path:
    evidence = pd.read_csv(out_dir / "OWN_EVIDENCE.csv", dtype=str, keep_default_na=False)
    cand_path = out_dir / "thesis_candidates.parquet"
    meta_path = out_dir / "thesis_candidates_metadata.parquet"
    if cand_path.exists():
        candidates = pd.read_parquet(cand_path)
    elif meta_path.exists():
        candidates = pd.read_parquet(meta_path)
        logger.warning("thesis_candidates.parquet absent — packaging abstract-level candidates")
    else:
        candidates = pd.DataFrame(columns=["thesis_id"])
    target = out_dir / PACKAGE_DIR
    target.mkdir(parents=True, exist_ok=True)
    for t in theses or load_theses():
        pkg = build_thesis_package(t, evidence, candidates)
        (target / f"{t.id}.json").write_text(json.dumps(pkg, indent=1, ensure_ascii=False, default=str),
                                            encoding="utf-8")
    logger.info("gemini packages written to %s", target)
    return target
