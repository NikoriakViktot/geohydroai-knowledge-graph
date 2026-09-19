"""Slice screening: the denominators behind absence and prevalence claims.

No network, no stores. Full text is served from a monkeypatched loader.
"""
from __future__ import annotations

import json

import pandas as pd
import pytest

from src.paper_3.harvest_queries import BOOLEAN_SLICES, slice_query
from src.paper_3.v2 import slice_screening as ss

S1 = "S1_kakhovka_status_quo"


@pytest.fixture(scope="module")
def slices():
    return ss.load_slices()


def _slice():
    return ss.Slice(id=S1, attributes=(
        ("reports_extent", ("water extent", "NDWI")),
        ("reports_wse_slope", ("water surface slope",)),
    ), topic_terms=("Kakhovka",))


def _candidate(doi, abstract, labels=(S1,), title="A Kakhovka study"):
    return {"doi": doi, "title": title, "abstract": abstract, "year": 2024,
            "matched_labels": list(labels)}


LONG = " lorem ipsum" * 30


def test_shipped_slices_match_the_boolean_slices(slices):
    assert ss.validate_slices(slices) == []
    assert set(slices) == {sid for sid, _, _ in BOOLEAN_SLICES}


def test_validate_names_unknown_slices_and_empty_terms():
    bad = {"S9_nope": ss.Slice("S9_nope", (("Bad Name", ()),))}
    problems = ss.validate_slices(bad)
    assert any("not a Boolean slice" in p for p in problems)
    assert any("empty term list" in p for p in problems)
    assert any("snake_case" in p for p in problems)
    assert any("no topic_terms" in p for p in problems)


def test_every_shipped_slice_declares_its_distinguishing_concept(slices):
    for sid, slc in slices.items():
        assert slc.topic_terms, sid
        assert not slc.on_topic("a paper about something else entirely")
    assert slices[S1].on_topic("The Kakhovka dam breach")


def test_off_topic_works_are_excluded_from_the_denominator_but_kept_on_record():
    """OpenAlex boolean search ranks rather than enforcing AND, so a slice returns
    works it is not about. Counting them would inflate every absence claim."""
    candidates = pd.DataFrame([
        _candidate("10.1/on", "Kakhovka reservoir NDWI water extent" + LONG),
        _candidate("10.1/off", "Water surface slope in Amazon rivers" + LONG,
                   title="An unrelated altimetry study"),
    ])
    screening, den = ss.screen(candidates, None, {S1: _slice()},
                               worldwide={slice_query(S1): 10})
    rows = screening.set_index("doi")
    assert rows.loc["10.1/on", "on_topic"] and rows.loc["10.1/on", "screened"]
    assert not rows.loc["10.1/off", "on_topic"]
    assert not rows.loc["10.1/off", "screened"]
    assert "10.1/off" in rows.index, "an exclusion with no record is a deletion"
    assert rows.loc["10.1/off", "reports_wse_slope_hits"] == 0

    d = den.set_index("slice_id").loc[S1]
    assert d["n_candidates"] == 2 and d["n_off_topic"] == 1 and d["n_screened"] == 1
    assert d["reports_wse_slope_n"] == 0, "the off-topic hit must not enter the count"
    assert d["on_topic_fraction"] == 0.5
    assert d["screened_fraction"] == pytest.approx(0.1), "measured against worldwide"


def test_hits_are_case_insensitive_occurrence_counts():
    assert ss.hits("NDWI and ndwi and Water Extent", ("NDWI", "water extent")) == 3
    assert ss.hits("", ("x",)) == 0


def test_screen_counts_attributes_over_abstracts_and_full_text(monkeypatch):
    monkeypatch.setattr(ss, "load_paper_json", lambda pid: {
        "title": "In corpus", "abstract": "",
        "sections": {"Methods": "we fit the water surface slope" + LONG}} if pid == "P1" else None)
    candidates = pd.DataFrame([
        _candidate("10.1/in", "abstract without the vocabulary" + LONG),
        _candidate("10.1/out", "NDWI water extent mapping" + LONG),
        _candidate("10.1/short", "too short"),
        _candidate("10.1/other", "S1 did not match this" + LONG, labels=("S2_altimetry_vertical_datum",)),
    ])
    index = pd.DataFrame([{"paper_id": "P1", "doi": "10.1/in"}])
    screening, den = ss.screen(candidates, index, {S1: _slice()},
                               worldwide={slice_query(S1): 40})

    rows = screening.set_index("doi")
    assert rows["on_topic"].all()
    assert rows.loc["10.1/in", "evidence_level"] == ss.FULL_TEXT
    assert rows.loc["10.1/in", "reports_wse_slope_hits"] == 1
    assert rows.loc["10.1/out", "evidence_level"] == ss.ABSTRACT
    assert rows.loc["10.1/out", "reports_extent_hits"] == 2
    assert not rows.loc["10.1/short", "screened"]
    assert "10.1/other" not in rows.index
    assert not rows["human_checked"].any()

    d = den.set_index("slice_id").loc[S1]
    assert d["worldwide_count"] == 40
    assert d["n_candidates"] == 3
    assert d["n_screened"] == 2
    assert d["n_fulltext"] == 1 and d["n_abstract_only"] == 1
    assert d["reports_wse_slope_n"] == 1 and d["reports_extent_n"] == 1
    assert d["screened_fraction"] == pytest.approx(0.05)


def test_a_harvest_without_labels_screens_nothing_but_still_writes_denominators():
    candidates = pd.DataFrame([{"doi": "10.1/a", "title": "t", "abstract": LONG, "year": 2024}])
    screening, den = ss.screen(candidates, None, {S1: _slice()})
    assert screening.empty
    assert den.set_index("slice_id").loc[S1, "n_screened"] == 0


def test_run_reads_the_harvest_and_writes_both_files(tmp_path, monkeypatch):
    harvest = tmp_path / "harvest"
    harvest.mkdir()
    pd.DataFrame([_candidate("10.1/x", "water surface slope study" + LONG)]).to_parquet(
        harvest / "harvest_candidates.parquet", index=False)
    (harvest / "worldwide_counts.json").write_text(json.dumps({slice_query(S1): 3}))
    monkeypatch.setattr(ss, "load_slices", lambda path=None: {S1: _slice()})
    csv_path, den_path = ss.run(out_dir=tmp_path)
    assert csv_path.exists() and den_path.exists()
    den = pd.read_csv(den_path).set_index("slice_id").loc[S1]
    assert den["n_screened"] == 1 and den["reports_wse_slope_n"] == 1


def test_run_without_a_harvest_is_a_named_error(tmp_path):
    with pytest.raises(FileNotFoundError, match="run --step discover"):
        ss.run(out_dir=tmp_path)
