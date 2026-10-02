"""Incremental parquet builds must merge, never replace (API_PLAN_v1 О-25).

references.parquet has no "paper_id" column, so the incremental merge was skipped
and the table was overwritten with only the changed papers' rows: 320,633 rows on
2026-05-24, 883 on 2026-09-23.
"""

from __future__ import annotations

import json
import os
import time

import pandas as pd
import pytest

from src.enrichment import build_parquet_layer as bpl


def _doc(paper_id: str, dois: list[str]) -> dict:
    return {
        "paper": {
            "metadata": {"paper_id": paper_id, "title": paper_id, "doi": None, "year": 2020},
            "references": [{"doi": d, "title": d} for d in dois],
        },
        "openalex": {"referenced_works": [], "topics": []},
        "enrichment_meta": {"status": "enriched"},
    }


def _write_doc(d, paper_id, dois):
    p = d / f"{paper_id}.json"
    p.write_text(json.dumps(_doc(paper_id, dois)), encoding="utf-8")
    return p


@pytest.fixture
def dirs(tmp_path):
    enriched, parquet = tmp_path / "enriched", tmp_path / "parquet"
    enriched.mkdir()
    return enriched, parquet


def _refs(parquet):
    return pd.read_parquet(parquet / "references.parquet")


def test_incremental_run_keeps_untouched_papers_references(dirs):
    enriched, parquet = dirs
    _write_doc(enriched, "paper_a", ["10.1000/A1", "10.1000/A2"])
    _write_doc(enriched, "paper_b", ["10.1000/B1"])
    bpl.build_parquet_layer(enriched, parquet, enriched, rebuild=True)
    assert len(_refs(parquet)) == 3

    time.sleep(0.01)
    changed = _write_doc(enriched, "paper_b", ["10.1000/B1", "10.1000/B2"])
    os.utime(changed, None)
    bpl.build_parquet_layer(enriched, parquet, enriched, rebuild=False)

    refs = _refs(parquet)
    assert sorted(refs["referenced_doi"]) == ["10.1000/a1", "10.1000/a2", "10.1000/b1", "10.1000/b2"]
    assert (refs["source_paper_id"] == "paper_a").sum() == 2


def test_reference_dois_are_normalised(dirs):
    enriched, parquet = dirs
    _write_doc(enriched, "paper_a", ["https://doi.org/10.1029/2024WR038314", "not a doi"])
    bpl.build_parquet_layer(enriched, parquet, enriched, rebuild=True)
    assert list(_refs(parquet)["referenced_doi"]) == ["10.1029/2024wr038314"]


def test_incremental_write_refuses_to_replace_with_partial_rows(tmp_path):
    out = tmp_path / "t.parquet"
    pd.DataFrame([{"source_paper_id": "a", "x": 1}, {"source_paper_id": "b", "x": 2}]).to_parquet(out)
    with pytest.raises(ValueError):
        bpl._write([{"source_paper_id": "b", "x": 3}], out, "t", dedup_key="paper_id", incremental=True)
    assert len(pd.read_parquet(out)) == 2  # untouched
