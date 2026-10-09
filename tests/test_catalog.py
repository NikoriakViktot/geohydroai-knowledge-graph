"""Catalog quality filter, snapshot and API, on hand-made inputs (no stores)."""

from __future__ import annotations

import copy
import hashlib
import json

import pytest
from fastapi.testclient import TestClient

from src.catalog.api import Snapshot, create_app
from src.catalog.build import write_snapshot
from src.catalog.quality import HUMAN, MODEL, PaperInput, build_card, load_rules

PID = "10.1000_p1"
IDENT = {"paper_id": PID, "doi": "10.1000/p1", "title": "Water extraction index for Landsat imagery", "year": 2014,
         "venue": "Remote Sensing of Environment", "openalex_id": "W1", "identity_status": "ok", "duplicate_of": None}


def _ent(name, role="used", score=0.9, evidence=""):
    return {"name": name, "role": role, "final_score": score, "evidence": evidence}


def _norm(raw, cid, display, match="alias", conf=1.0):
    return {"raw_name": raw, "canonical_id": cid, "display_name": display, "match_type": match, "confidence": conf}


def paper(**over) -> PaperInput:
    normalized = {
        "metadata": {"title": "TEI title of the paper", "authors": [{"full_name": "A B"}]},
        "task": {"label": "flood_mapping_satellite", "confidence": 0.9, "source": "rules"},
        "entities": {
            "methods": [_ent("ML", evidence="compared with the maximum likelihood classifier"),
                        _ent("CV", evidence="the coefficient of variation (CV) of rainfall"),
                        _ent("ACCURACY_ASSESSMENT"), _ent("MNF", role="mentioned"), _ent("SVM", score=0.3),
                        _ent("LSTM")],
            "satellites": [_ent("LANDSAT"), _ent("MODIS")],
            "dems": [_ent("DEM")],
            "geo": {"study_geo": {"countries": [
                {"name": "Denmark", "source": "regex_study_text", "evidence": "water bodies in Denmark and Ethiopia"},
                {"name": "United States", "source": "regex_study_text",
                 "evidence": "images from the United States Geological Survey portal"},
                {"name": "Congalton", "source": "ner", "evidence": ""}]},
                "author_geo": [{"name": "Denmark", "confidence": 0.95}]},
        },
        "normalized_entities": {
            "methods": [_norm("ML", "method.ml", "Maximum Likelihood"), _norm("CV", "method.cv", "Cross-Validation"),
                        _norm("ACCURACY_ASSESSMENT", "method.accuracy_assessment", "Accuracy Assessment"),
                        _norm("MNF", "method.mnf", "Minimum Noise Fraction"),
                        _norm("SVM", "method.support_vector_machine", "Support Vector Machine"),
                        _norm("LSTM", "method.lstm", "LSTM"), _norm("XYZ", None, None, "unknown", 0.0)],
            "satellites": [_norm("LANDSAT", "sensor.landsat", "Landsat"), _norm("MODIS", "sensor.modis", "MODIS")],
            "dems": [_norm("DEM", "data.dem", "Digital Elevation Model")],
        },
    }
    facts = [
        {"fact_id": "f1", "canonical_id": "metric.kappa", "value": 0.93, "unit": None, "row_context": '["AWEI"]',
         "table_label": "Table 4", "page": 8, "confidence": 0.85, "table_id": "t4"},
        {"fact_id": "f2", "canonical_id": "metric.kappa", "value": 1.4, "unit": None, "row_context": '["ML"]',
         "table_label": "Table 4", "page": 8, "confidence": 0.85, "table_id": "t4"},
        {"fact_id": "f3", "canonical_id": "metric.overall_accuracy", "value": 94.2, "unit": "%",
         "row_context": '["MNDWI"]', "table_label": "Table 5", "page": 9, "confidence": 0.85, "table_id": "t5"},
        {"fact_id": "f4", "canonical_id": "metric.rmse", "value": 1.2, "unit": "Eq. rainfall",
         "row_context": '["site A"]', "table_label": "Table 6", "page": 9, "confidence": 0.85, "table_id": "t6"},
        {"fact_id": "f5", "canonical_id": "metric.rmse", "value": 0.4, "unit": "m", "row_context": '["site B"]',
         "table_label": "Table 6", "page": 9, "confidence": 0.85, "table_id": "t6"},
        {"fact_id": "f6", "canonical_id": "metric.nse", "value": 0.7, "unit": None, "row_context": "[]",
         "table_label": "Table 7", "page": 10, "confidence": 0.85, "table_id": "t7"},
        {"fact_id": "f7", "canonical_id": "metric.mse", "value": 3.0, "unit": "m", "row_context": '["x"]',
         "table_label": "Table 7", "page": 10, "confidence": 0.85, "table_id": "t7"},
    ]
    grounding = {("USES_METHOD", "method.lstm"): {"grounded": True, "tei_mentions": 9, "terms": ["LSTM"],
                                                  "tei_evidence": ["an LSTM network"]},
                 ("USES_SENSOR", "sensor.modis"): {"grounded": False, "tei_mentions": 0, "terms": ["MODIS"],
                                                   "tei_evidence": []},
                 ("USES_SENSOR", "sensor.landsat"): {"grounded": True, "tei_mentions": 42, "terms": ["LANDSAT"],
                                                     "tei_evidence": ["Landsat 5 TM"]}}
    kw = dict(identity=dict(IDENT), bib={"authors": ["Feyisa G", "Meilby H"], "cited_by_count": 2003,
                                         "oa_url": None, "is_retracted": False},
              normalized=normalized, grounding=grounding, facts=facts,
              topics=[("Flood Risk", 0.999), ("Drought", 0.5)], checks=[])
    kw.update(over)
    return PaperInput(**kw)


def _names(card, kind):
    return [e["id"] for e in card["analysis"][kind]]


def test_entities_rules():
    card, why, hidden = build_card(paper())
    assert why is None
    reasons = {(h.value, h.reason) for h in hidden}
    # ML has its long form in the evidence; CV means "coefficient of variation" there
    assert "method.ml" in _names(card, "methods")
    assert ("method.cv", "ambiguous_acronym") in reasons
    assert ("method.accuracy_assessment", "generic_term") in reasons
    assert ("method.mnf", "role_mentioned") in reasons
    assert ("method.support_vector_machine", "low_score") in reasons
    assert ("XYZ", "not_in_ontology") in reasons
    # an unambiguous acronym needs no long form; grounding orders by mentions
    assert _names(card, "methods") == ["method.lstm", "method.ml"]
    assert _names(card, "sensors") == ["sensor.landsat"]
    assert ("sensor.modis", "not_in_text") in reasons
    assert _names(card, "data") == ["data.dem"]
    assert all(e["status"] == MODEL for e in card["analysis"]["methods"])


def test_countries_and_labels():
    card, _, hidden = build_card(paper())
    a = card["analysis"]
    assert [c["name"] for c in a["study_countries"]] == ["Denmark"]  # agency name and NER noise dropped
    assert ("United States", "name_not_in_evidence") in {(h.value, h.reason) for h in hidden}
    assert [c["name"] for c in a["author_countries"]] == ["Denmark"]
    assert a["task"] is None and ("task", "label_not_validated") in {(h.field, h.reason) for h in hidden}


def test_results_rules():
    card, _, hidden = build_card(paper())
    shown = {r["fact_id"]: r for r in card["analysis"]["results"]}
    assert set(shown) == {"f1", "f3", "f5"}
    assert shown["f3"]["value"] == pytest.approx(0.942) and shown["f3"]["unit"] is None  # 94.2 % → ratio
    assert shown["f1"]["context"] == "AWEI" and shown["f5"]["unit"] == "m"
    reasons = {h.value: h.reason for h in hidden if h.field == "results"}
    assert reasons == {"f2": "out_of_range", "f4": "unit_unknown", "f6": "no_row_label", "f7": "metric_without_range"}


def test_topics_threshold():
    card, _, _ = build_card(paper())
    assert [t["name"] for t in card["topics"]] == ["Flood Risk"]


def test_human_judgements_win():
    checks = [
        {"target_kind": "entity_edge", "target_id": f"{PID}|method.accuracy_assessment", "verdict": "correct"},
        {"target_kind": "entity_edge", "target_id": f"{PID}|method.ml", "verdict": "incorrect"},
        {"target_kind": "metric_fact", "target_id": "f2", "verdict": "incorrect", "corrected": {"value": 0.14}},
        {"target_kind": "metric_fact", "target_id": "f1", "verdict": "incorrect", "corrected": None},
        {"target_kind": "location", "target_id": PID, "verdict": "incorrect",
         "corrected": json.dumps({"primary_country": "Ethiopia"})},
        {"target_kind": "paper", "target_id": PID, "field": "metadata", "verdict": "correct"},
    ]
    card, _, _ = build_card(paper(checks=checks))
    a = card["analysis"]
    assert card["metadata_status"] == HUMAN
    assert "method.ml" not in _names(card, "methods")
    assert {"id": "method.accuracy_assessment", "status": HUMAN}.items() <= a["methods"][0].items()
    results = {r["fact_id"]: r for r in a["results"]}
    assert "f1" not in results and results["f2"]["value"] == 0.14 and results["f2"]["status"] == HUMAN
    assert a["study_countries"] == [{"name": "Ethiopia", "status": HUMAN}]
    assert card["quality"]["human_checks"] == 6


@pytest.mark.parametrize("over, reason", [
    ({"duplicate_of": "x", "identity_status": "duplicate"}, "identity_duplicate"),
    ({"doi": None, "openalex_id": None}, "no_link"),
])
def test_identity_exclusions(over, reason):
    card, why, _ = build_card(paper(identity={**IDENT, **over}))
    assert card is None and why == reason


def test_identity_fallbacks_and_doubt():
    card, _, _ = build_card(paper(identity={**IDENT, "title": None}, bib={"title": "Title from OpenAlex works"}))
    assert card["title"] == "Title from OpenAlex works"
    card, _, hidden = build_card(paper(identity={**IDENT, "identity_status": "title_doi_mismatch"}))
    assert card["links"]["doi"] is None and card["links"]["openalex"] == "https://openalex.org/W1"
    card, _, _ = build_card(paper(normalized=None))
    assert card["analysis"] is None and card["quality"]["analysis_available"] is False
    card, why, _ = build_card(paper(checks=[{"target_kind": "paper", "target_id": PID, "field": "metadata",
                                             "verdict": "incorrect"}]))
    assert card is None and why == "human_rejected_metadata"


def _inputs():
    other = paper(identity={**IDENT, "paper_id": "10.1000_p2", "doi": "10.1000/p2", "title": "Second paper on floods",
                            "year": 2020}, checks=[{"target_kind": "paper", "target_id": "10.1000_p2",
                                                    "field": "metadata", "verdict": "correct"}])
    other.bib = {"cited_by_count": 5, "oa_url": "https://repo.example/p2.pdf"}
    gone = paper(identity={**IDENT, "paper_id": "x3", "doi": None, "openalex_id": None})
    return [paper(), other, gone]


def test_snapshot_deterministic_and_never_overwritten(tmp_path):
    m1 = write_snapshot(_inputs(), tmp_path / "a")
    m2 = write_snapshot(copy.deepcopy(_inputs()), tmp_path / "b")
    assert m1["files"] == m2["files"]
    assert m1["counts"]["cards"] == 2 and m1["excluded_reasons"] == {"no_link": 1}
    assert m1["rules_version"] == load_rules()["version"]
    with pytest.raises(FileExistsError):
        write_snapshot(_inputs(), tmp_path / "a")


@pytest.fixture
def snap_dir(tmp_path):
    write_snapshot(_inputs(), tmp_path / "s")
    return tmp_path / "s"


def test_api_cards(snap_dir):
    c = TestClient(create_app(Snapshot(snap_dir), key_hashes=set()))
    assert c.get("/health").json()["cards"] == 2
    assert c.get("/v1/release").json()["counts"]["cards"] == 2
    body = c.get("/v1/cards").json()
    assert body["total"] == 2 and body["items"][0]["paper_id"] == PID  # sorted by citations
    assert c.get("/v1/cards", params={"q": "second floods"}).json()["total"] == 1
    assert c.get("/v1/cards", params={"q": "feyisa"}).json()["total"] == 1
    assert c.get("/v1/cards", params={"method": "method.lstm"}).json()["total"] == 2
    assert c.get("/v1/cards", params={"country": "denmark", "year_from": 2015}).json()["total"] == 1
    assert c.get("/v1/cards", params={"metric": "kappa", "open_access_only": True}).json()["total"] == 1
    assert [i["paper_id"] for i in c.get("/v1/cards", params={"verified_only": True}).json()["items"]] == ["10.1000_p2"]
    card = c.get(f"/v1/cards/{PID}").json()
    assert card["links"]["doi"] == "https://doi.org/10.1000/p1" and card["analysis"]["results"]
    assert c.get("/v1/cards/nope").status_code == 404
    f = c.get("/v1/facets").json()
    assert {"value": "sensor.landsat", "cards": 2} in f["sensors"]


def test_api_key_and_integrity(snap_dir):
    key = "s3cret"
    c = TestClient(create_app(Snapshot(snap_dir), key_hashes={hashlib.sha256(key.encode()).hexdigest()}))
    assert c.get("/health").status_code == 200
    assert c.get("/agent-guide").status_code == 200 and "X-API-Key" in c.get("/agent-guide").text
    assert c.get("/llms.txt").status_code == 200
    assert c.get("/v1/cards").status_code == 401
    assert c.get("/v1/cards", headers={"X-API-Key": "wrong"}).status_code == 401
    assert c.get("/v1/cards", headers={"X-API-Key": key}).status_code == 200
    with (snap_dir / "cards.jsonl").open("a") as f:
        f.write("\n")
    with pytest.raises(ValueError, match="sha256"):
        Snapshot(snap_dir)
