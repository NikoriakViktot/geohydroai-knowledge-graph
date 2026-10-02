"""Entity edges grounded on the TEI text: words, not substrings; normalised forms still match."""

from __future__ import annotations

import pytest

from src.graph.entity_grounding import ground_paper, term

from tests.test_api_evidence import TEI


@pytest.mark.parametrize("raw, text, found", [
    ("iRIC", "an empirical relation", False),            # the 793-edge false-positive class
    ("iRIC", "we ran iRIC Nays2DFlood", True),
    ("HAND", "On the other hand, urban areas", False),    # short acronym vs. a common word
    ("HAND", "a HAND-based inundation map", True),
    ("GOES", "the estimation goes to Clayton", False),
    ("SWAT", "pixels within the satellite swath", False),
    ("SENTINEL", "open-access Sentinel-1 SAR data", True),  # normalised surface forms
    ("LIDAR", "LiDAR digital surface models", True),
    ("HYDROLOGICAL_MODEL", "a lumped hydrological model", True),
    ("two_dimensional_hydrodynamic_model", "a two-dimensional hydrodynamic model", True),
    ("x", "x", None),
])
def test_term_matching(raw, text, found):
    t = term(raw)
    assert (t.found_in(text) if t else None) is found


def test_ground_paper_on_tei(tmp_path):
    path = tmp_path / "p.tei.xml"
    path.write_text(TEI, encoding="utf-8")
    rows = ground_paper(str(path), "p", [
        {"rel": "USES_METHOD", "canonical_id": "method.hand", "terms": ["HAND", "Height Above Nearest Drainage"]},
        {"rel": "USES_SENSOR", "canonical_id": "sensor.fabdem", "terms": ["FABDEM"]},
        {"rel": "USES_METHOD", "canonical_id": "method.iric", "terms": ["iRIC"]},
    ])
    hand, fabdem, iric = rows
    assert hand["grounded"] and hand["tei_mentions"] == 1 and hand["tei_evidence"][0].startswith("The HAND method")
    assert fabdem["grounded"] and fabdem["tei_page"] == 15 and fabdem["tei_section"] == "Discussion"
    assert iric["grounded"] is False and iric["tei_evidence"] == []


def test_unreadable_tei_is_not_checked(tmp_path):
    bad = tmp_path / "bad.tei.xml"
    bad.write_text("<TEI><unclosed", encoding="utf-8")
    row = ground_paper(str(bad), "bad", [{"rel": "USES_METHOD", "canonical_id": "m", "terms": ["HAND"]}])[0]
    assert row["grounded"] is None and row["note"].startswith("TEI not parsed")
