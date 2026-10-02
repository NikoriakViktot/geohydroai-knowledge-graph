"""graph_loader must read entities and references from the real enriched layout.

data/enriched/*.json nest the paper under "paper" (with "openalex" and
"enrichment_meta" beside it). Before the flat view, the loader read
normalized_entities / references from the top level and produced no
USES_METHOD / USES_SENSOR / REPORTS_METRIC / CITES rows for the whole corpus.
"""

from __future__ import annotations

import json

import pytest

import src.graph.graph_loader as gl

NESTED = {
    "paper": {
        "metadata": {"paper_id": "paper_a", "doi": "10.1000/A1"},
        "normalized_entities": {
            "methods": [{"canonical_id": "method.hand", "confidence": 0.9, "surface_form": "HAND"}],
            "satellites": [{"canonical_id": "sensor.sentinel_1", "confidence": 0.8}],
            "metrics": [{"canonical_id": "metric.nse", "confidence": 0.7}],
        },
        "references": [
            {"doi": "10.5194/nhess-19-2405-2019", "title": "An integrated evaluation", "year": 2019},
            {"doi": None, "title": "A title-only reference", "year": "2001"},
            {"doi": None, "title": None},
        ],
    },
    "openalex": {"publication_year": 2021},
    "enrichment_meta": {"status": "enriched"},
}


@pytest.fixture
def enriched_dir(tmp_path, monkeypatch):
    d = tmp_path / "enriched"
    d.mkdir()
    (d / "paper_a.json").write_text(json.dumps(NESTED), encoding="utf-8")
    monkeypatch.setattr(gl, "_ENRICHED_DIR", d)
    monkeypatch.setattr(gl, "_PAPER_JSON_DIR", tmp_path / "paper_json")
    return d


def test_flatten_keeps_paper_keys_and_siblings():
    flat = gl._flatten_enriched(NESTED)
    assert flat["normalized_entities"]["methods"][0]["canonical_id"] == "method.hand"
    assert flat["openalex"] == {"publication_year": 2021}
    assert flat["enrichment_meta"]["status"] == "enriched"
    assert flat["metadata"]["paper_id"] == "paper_a"


def test_flatten_leaves_flat_documents_alone():
    flat_doc = {"metadata": {"paper_id": "x"}, "references": []}
    assert gl._flatten_enriched(flat_doc) is flat_doc


def test_entity_edges_come_from_nested_layout(enriched_dir):
    pm, ps, pmet, _ = gl.load_entity_edges_from_enriched()
    assert [(r["paper_id"], r["canonical_id"]) for r in pm] == [("paper_a", "method.hand")]
    assert [r["canonical_id"] for r in ps] == ["sensor.sentinel_1"]
    assert [r["canonical_id"] for r in pmet] == ["metric.nse"]


def test_apply_identity_uses_canonical_doi_and_drops_copies():
    rows = [
        {"paper_id": "10.1029_2025gl120832", "doi": "10.1029/2025GL120832", "title": "T", "year": 2026},
        {"paper_id": "copy_of_it", "doi": "10.1029/2025GL120832", "title": "T", "year": 2026},
        {"paper_id": "VKNU", "doi": "10.1126/science.aan2506", "title": "Ukrainian floods", "year": None},
        {"paper_id": "EXHIBIT", "doi": None, "title": "Exhibit A", "year": None},
        {"paper_id": "not_in_postgres", "doi": None, "title": "x", "year": 2020},
    ]
    identity = {
        "10.1029_2025gl120832": {"doi": "10.1029/2025gl120832", "title": "T", "year": 2026,
                                 "identity_status": "ok", "duplicate_of": None},
        "copy_of_it": {"doi": "10.1029/2025gl120832", "title": "T", "year": 2026,
                       "identity_status": "duplicate", "duplicate_of": "10.1029_2025gl120832"},
        "VKNU": {"doi": None, "title": "Ukrainian floods", "year": 2018,
                 "identity_status": "title_doi_mismatch", "duplicate_of": None},
        "EXHIBIT": {"doi": None, "title": "Exhibit A", "year": None,
                    "identity_status": "not_a_paper", "duplicate_of": None},
    }
    out, excluded = gl.apply_identity(rows, identity)
    by_id = {r["paper_id"]: r for r in out}
    assert excluded == {"copy_of_it", "EXHIBIT"}
    assert set(by_id) == {"10.1029_2025gl120832", "VKNU", "not_in_postgres"}
    assert by_id["10.1029_2025gl120832"]["doi"] == "10.1029/2025gl120832"
    assert by_id["VKNU"]["doi"] is None            # header DOI names another work
    assert by_id["VKNU"]["year"] == 2018           # filled from the layer of truth
    assert by_id["not_in_postgres"]["identity_status"] == "unknown"

    edges = [{"paper_id": "copy_of_it"}, {"paper_id": "VKNU"}]
    assert gl.drop_excluded(edges, excluded) == [{"paper_id": "VKNU"}]


def test_cites_rows_come_from_nested_layout(enriched_dir):
    rows = gl.load_cites_edges()
    assert len(rows) == 2  # the reference with neither DOI nor title is skipped
    by_title = {r["target_title"]: r for r in rows}
    assert by_title["An integrated evaluation"]["target_doi"] == "10.5194/nhess-19-2405-2019"
    assert by_title["A title-only reference"]["target_doi"] is None
    assert all(r["source_paper_id"] == "paper_a" for r in rows)
