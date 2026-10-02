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


def test_cites_rows_come_from_nested_layout(enriched_dir):
    rows = gl.load_cites_edges()
    assert len(rows) == 2  # the reference with neither DOI nor title is skipped
    by_title = {r["target_title"]: r for r in rows}
    assert by_title["An integrated evaluation"]["target_doi"] == "10.5194/nhess-19-2405-2019"
    assert by_title["A title-only reference"]["target_doi"] is None
    assert all(r["source_paper_id"] == "paper_a" for r in rows)
