"""reference_registry — DOIs resolve through an injectable fetcher; unresolved never gain a title."""
from __future__ import annotations

import pandas as pd

from src.paper_3.v2 import reference_registry as rr


def _fake_fetch(doi: str):
    known = {
        "10.3133/wsp2339": {"title": "Guide for selecting Manning's roughness coefficients",
                            "authors": "Arcement, George J.; Schneider, Verne R.", "year": "1989",
                            "journal": "Water-Supply Paper", "url": "https://doi.org/10.3133/wsp2339"},
        "10.1000/a": {"title": "A", "authors": "Smith, J.", "year": "2024", "journal": "J", "url": "u"},
        "10.1000/b": {"title": "B", "authors": "Smith, K.", "year": "2024", "journal": "J", "url": "u"},
        "10.1000/kuz": {"title": "Початкові стадії", "authors": "Куземко, А.", "year": "2025", "journal": "УБЖ", "url": "u"},
    }
    return known.get(doi)


def test_build_resolves_dedups_and_disambiguates_keys():
    dois = [("10.3133/wsp2339", "control T33"), ("10.3133/wsp2339", "SRC-04"),
            ("10.1000/a", ""), ("10.1000/b", ""), ("10.9999/nope", "ghost"), ("10.1000/kuz", "")]
    calls = []
    def fetch(d):
        calls.append(d); return _fake_fetch(d)
    frame = rr.build(dois, technical=[], fetch=fetch, delay=0)
    assert len(calls) == 5                                  # duplicate DOI fetched once
    by = frame.set_index("doi")
    assert by.loc["10.3133/wsp2339", "cite_key"] == "arcement1989"
    assert "control T33" in by.loc["10.3133/wsp2339", "note"] and "SRC-04" in by.loc["10.3133/wsp2339", "note"]
    assert set(frame[frame.doi.str.startswith("10.1000/")].cite_key) == {"smith2024", "smith2024b", "kuzemko2025"}
    assert by.loc["10.9999/nope", "resolved"] == False
    assert by.loc["10.9999/nope", "title"] == ""
    assert by.loc["10.9999/nope", "cite_key"].startswith("doi_")


def test_cache_short_circuits_fetch():
    cache = {"10.1000/a": _fake_fetch("10.1000/a"), "10.9999/nope": None}
    def fetch(d):
        raise AssertionError("must not be called")
    frame = rr.build([("10.1000/a", ""), ("10.9999/nope", "")], technical=[], fetch=fetch, cache=cache, delay=0)
    assert frame.resolved.tolist() == [True, False]


def test_technical_sources_pass_through_unresolved_until_url_verified():
    tech = rr.from_technical_sources()
    assert tech and all(t["source"] == "technical" for t in tech)
    assert all(t["resolved"] == (t["resolution_method"] == "url_verified") for t in tech)
    md = rr.render_md(pd.DataFrame(tech, columns=rr.COLUMNS))
    assert "[UNRESOLVED]" in md


def test_thesis_controls_are_collected_as_dois():
    dois = rr.from_theses()
    assert any(d == "10.3133/wsp2339" for d, _ in dois)
    assert all(d.startswith("10.") for d, _ in dois)
