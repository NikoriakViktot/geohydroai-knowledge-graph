"""gemini_package — per-thesis record of own claims + candidates; extract_fields gains the new fields."""
from __future__ import annotations

import pandas as pd

from src.paper_3 import extract_fields, gemini_package as gp
from src.paper_3.theses import get_thesis, load_theses


def test_package_binds_own_claims_and_candidates_to_the_thesis():
    theses = load_theses()
    t25 = get_thesis("T25", theses)
    evidence = pd.DataFrame([
        {"claim_id": "S1.1", "claim_text": "veg rose", "value_resolved": "84 %", "uncertainty_resolved": "",
         "n_resolved": "12", "audit_status": "SUPPORTED_WITH_LIMITATION", "source_table": "x.csv",
         "snapshot_sha256": "abc", "literature_thesis_ids": "T25;T27"},
        {"claim_id": "M1.1", "claim_text": "slope", "value_resolved": "+3.3", "uncertainty_resolved": "",
         "n_resolved": "14", "audit_status": "SUPPORTED", "source_table": "y.csv",
         "snapshot_sha256": "def", "literature_thesis_ids": "T01;T03"},
    ])
    cands = pd.DataFrame([
        {"thesis_id": "T25", "paper_id": "p1", "doi": "10.1/a", "title": "A", "year": 2025,
         "prefilter_pass": True, "prefilter_score": 3.0},
        {"thesis_id": "T25", "paper_id": "p2", "doi": "10.1/b", "title": "B", "year": 2024,
         "prefilter_pass": False, "prefilter_score": 1.0},
        {"thesis_id": "T01", "paper_id": "p3", "doi": "10.1/c", "title": "C", "year": 2020,
         "prefilter_pass": True, "prefilter_score": 2.0},
    ])
    pkg = gp.build_thesis_package(t25, evidence, cands, passages={"p1": [{"id": "P1", "text": "…"}]})
    assert pkg["thesis"]["id"] == "T25" and "10.15407/ukrbotj82.05.488" in pkg["thesis"]["controls"]
    assert [c["claim_id"] for c in pkg["own_claims"]] == ["S1.1"]
    assert [p["paper_id"] for p in pkg["candidate_papers"]] == ["p1"]     # prefilter-failed dropped
    assert pkg["candidate_papers"][0]["passages"][0]["id"] == "P1"
    assert pkg["n_candidates"] == 1


def test_extract_fields_new_fields_default_to_not_stated_and_survive_normalisation():
    new = ("vegetation_metric", "vegetation_time_series", "woody_detection_method",
           "roughness_n_value", "roughness_n_source", "channel_width_method",
           "dem_validation_type", "sar_method", "false_positive_treatment")
    assert set(new) <= set(extract_fields.TEXT_FIELDS)
    out = extract_fields.normalise_extraction({"roughness_n_value": "0.065 (reed)", "sar_method": ""})
    assert out["roughness_n_value"] == "0.065 (reed)"
    assert out["sar_method"] == extract_fields.NOT_STATED
    assert out["vegetation_metric"] == extract_fields.NOT_STATED
    prompt = extract_fields.build_prompt({"title": "t"}, [])
    for f in new:
        assert f'"{f}"' in prompt
