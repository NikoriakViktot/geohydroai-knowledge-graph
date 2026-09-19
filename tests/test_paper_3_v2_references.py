"""Mandatory references: what the author named must reach the overlay, and the
registering of those sources must surface, not bury, the discrepancy they expose."""
from __future__ import annotations

import pytest

from src.paper_3.v2 import markers, references


def test_the_mandatory_sources_are_present_with_formal_citations():
    refs = {r["id"]: r for r in references.mandatory_references()}
    for must in ("STOPKHAI_2026_BS77_EVRF2019", "CRS_EU_UA_KRON_EVRF2019ZERO", "EPSG_9902"):
        assert must in refs
        assert refs[must]["formal_citation"]
    assert refs["STOPKHAI_2026_BS77_EVRF2019"]["formal_citation_en"].startswith("Stopkhai")
    assert refs["STOPKHAI_2026_BS77_EVRF2019"]["source_type"] == "curated_paper"


def test_a_non_mandatory_source_is_not_in_the_list():
    ids = {r["id"] for r in references.mandatory_references()}
    assert "TREVOHO_2021_UA_HEIGHT_SYSTEM" not in ids
    assert "SWOT_PIXC_PDD" not in ids


def test_a_mandatory_source_without_a_citation_is_refused(tmp_path):
    bad = tmp_path / "t.yaml"
    bad.write_text("- id: X\n  mandatory: true\n  formal_citation: ''\n", encoding="utf-8")
    with pytest.raises(ValueError, match="formal_citation"):
        references.mandatory_references(bad)


def test_rendered_block_carries_both_citations_and_a_parseable_marker():
    block = references.render_references_block()
    assert "Стопхай, Ю." in block
    assert "Stopkhai, Yu." in block
    assert "idw_ua_2019_z.asc" in block
    assert "ua_2019z.asc" in block
    ids = markers.find_marker_ids(block)
    assert ids == [references.REALISATIONS_MARKER_ID]
    parsed = markers.parse_markers(block)[0]
    assert parsed.kind == "reference"
    assert markers.validate(parsed) == []
    assert not parsed.blocks_submission
    assert "V1.2" in parsed.backs


def test_rendering_is_deterministic():
    assert references.render_references_block() == references.render_references_block()
