"""Tests for paper_my/app/thesis_io.parse_thesis_table."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "paper_my"))

from app.thesis_io import parse_thesis_table, load_theses


SAMPLE_TABLE = """
| No. | Thesis | Sources needed | Target section |
| --- | --- | --- | --- |
| 21 | Flood mapping should be described as a hierarchy of products. | Flood product taxonomy | Methodological Trade-offs |
| 22 | A binary water mask is not a complete flood map. | SAR/optical flood studies | Methodological Trade-offs |
| 39 | A SAR backscatter threshold is not universal. | Sentinel-1 preprocessing | SAR Mapping |
"""


def test_parse_returns_thesis_objects():
    theses = parse_thesis_table(SAMPLE_TABLE)
    assert len(theses) == 3


def test_ids_are_unique():
    theses = parse_thesis_table(SAMPLE_TABLE, id_offset=200)
    ids = [t.id for t in theses]
    assert len(ids) == len(set(ids)), "Duplicate thesis IDs"


def test_section_id_mapped():
    theses = parse_thesis_table(SAMPLE_TABLE)
    sar_thesis = next(t for t in theses if "SAR backscatter" in t.claim)
    assert sar_thesis.section_id == "sar_flood"


def test_tradeoff_section_mapped():
    theses = parse_thesis_table(SAMPLE_TABLE)
    trade = next(t for t in theses if "hierarchy" in t.claim)
    assert trade.section_id == "tradeoff"


def test_claim_text_preserved():
    theses = parse_thesis_table(SAMPLE_TABLE)
    assert any("SAR backscatter threshold" in t.claim for t in theses)


def test_keywords_extracted():
    theses = parse_thesis_table(SAMPLE_TABLE)
    sar = next(t for t in theses if "SAR backscatter" in t.claim)
    assert len(sar.keywords) > 0


def test_display_no_matches_original():
    theses = parse_thesis_table(SAMPLE_TABLE)
    assert theses[0].display_no == 21
    assert theses[2].display_no == 39


def test_load_theses_v3_roundtrip():
    path = Path(__file__).resolve().parents[1] / "paper_my" / "theses_v3.json"
    if not path.exists():
        return  # skip if file missing
    theses = load_theses(path)
    assert len(theses) == 22
    assert theses[0].id == "T1"
    assert theses[0].claim  # not empty


def test_load_theses_v4_roundtrip():
    path = Path(__file__).resolve().parents[1] / "paper_my" / "theses_v4.json"
    if not path.exists():
        return  # skip if file missing
    theses = load_theses(path)
    assert len(theses) == 102
    # V3 theses should still be intact
    first = theses[0]
    assert first.id == "T1"
    # New theses should be appended
    new_one = next((t for t in theses if t.id == "T23"), None)
    assert new_one is not None
