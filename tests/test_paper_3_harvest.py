"""Harvest: query generation, OpenAlex parsing, dedup and selection.

No network. `requests` is replaced by a fake session that serves canned payloads,
so these tests assert our handling of OpenAlex, not OpenAlex itself.
"""
from __future__ import annotations

import json

import pandas as pd
import pytest

from src.paper_3 import harvest_openalex as ho
from src.paper_3.harvest_queries import (
    CROSS_BLOCK,
    DEFAULT_FILTERS,
    build_query_set,
    run as run_queries,
    seed_dois,
)
from src.paper_3.theses import load_theses


@pytest.fixture(scope="module")
def theses():
    return load_theses()


# ── query generation ──────────────────────────────────────────────────────────

def test_every_thesis_contributes_queries(theses):
    qs = build_query_set(theses)
    covered = {tid for q in qs for tid in q["thesis_ids"]}
    assert covered == {t.id for t in theses}


def test_queries_are_deduplicated_and_merge_thesis_ids(theses):
    qs = build_query_set(theses)
    texts = [q["query"].lower() for q in qs]
    assert len(texts) == len(set(texts)), "a query must appear at most once"


def test_cross_block_queries_are_present(theses):
    qs = build_query_set(theses)
    cross = [q for q in qs if "cross_block" in q["source"]]
    assert len(cross) == len(CROSS_BLOCK)
    for q in cross:
        assert len(q["thesis_ids"]) >= 2


def test_open_access_is_not_filtered_at_search_time(theses):
    """The coverage diagnostic needs worldwide counts, paywalled works included."""
    for q in build_query_set(theses):
        assert "is_oa" not in q["filters"]
        assert q["filters"] == DEFAULT_FILTERS


def test_seed_dois_map_to_theses(theses):
    seeds = seed_dois(theses)
    assert "10.1029/2025gl119771" in seeds
    assert set(seeds["10.1029/2025gl119771"]) >= {"T04", "T07"}


def test_run_writes_queries_json(tmp_path, theses):
    path = run_queries(out_dir=tmp_path, theses=theses)
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["n_queries"] == len(payload["queries"])
    assert payload["n_seed_dois"] == len(payload["seed_dois"])
    assert payload["queries"], "query set must not be empty"


# ── OpenAlex parsing ──────────────────────────────────────────────────────────

def _work(doi="10.1029/2025GL119771", oa=True, pdf=True, year=2026, cited=7):
    return {
        "id": "https://openalex.org/W123",
        "doi": f"https://doi.org/{doi}",
        "display_name": "The Potential for Leveraging SWOT-Mapped Uneven Water Surface Elevations",
        "publication_year": year,
        "cited_by_count": cited,
        "type": "article",
        "open_access": {"is_oa": oa},
        "best_oa_location": {"pdf_url": "https://example.org/a.pdf" if (oa and pdf) else None},
        "locations": [],
        "primary_location": {"source": {"display_name": "Geophysical Research Letters"}},
        "referenced_works_count": 41,
        "abstract_inverted_index": {"Lake": [0], "levels": [1], "vary": [2]},
    }


def test_to_record_flattens_and_normalises_doi():
    rec = ho.to_record(_work())
    assert rec["doi"] == "10.1029/2025gl119771"
    assert rec["slug"] == "10.1029_2025gl119771"
    assert rec["journal"] == "Geophysical Research Letters"
    assert rec["is_oa"] and rec["pdf_url"]


def test_to_record_rejects_a_work_without_a_doi():
    work = _work()
    work["doi"] = None
    assert ho.to_record(work) is None


def test_reconstruct_abstract_restores_word_order():
    assert ho.reconstruct_abstract({"Lake": [0], "levels": [1], "vary": [2]}) == "Lake levels vary"
    assert ho.reconstruct_abstract(None) == ""


def test_oa_pdf_url_falls_back_to_locations():
    work = _work(oa=True, pdf=False)
    work["locations"] = [{"is_oa": True, "pdf_url": "https://example.org/b.pdf"}]
    assert ho._oa_pdf_url(work) == "https://example.org/b.pdf"


def test_oa_pdf_url_is_none_for_paywalled():
    work = _work(oa=False, pdf=False)
    work["locations"] = [{"is_oa": False, "pdf_url": "https://paywall/c.pdf"}]
    assert ho._oa_pdf_url(work) is None


# ── search over a fake session ────────────────────────────────────────────────

class _Response:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status
        self.headers = {"Content-Type": "application/json"}

    def json(self):
        return self._payload


class _FakeSession:
    """Records every call so the polite-pool parameters can be asserted."""

    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append({"url": url, "params": dict(params or {})})
        page = (params or {}).get("page", 1)
        idx = page - 1
        if idx < len(self.pages):
            return _Response(self.pages[idx])
        return _Response({"meta": {"count": 0}, "results": []})


def test_openalex_search_returns_records_and_worldwide_count(monkeypatch):
    monkeypatch.setattr(ho, "_DELAY", 0)
    session = _FakeSession([{"meta": {"count": 2484}, "results": [_work()]}])
    records, total = ho.openalex_search("ICESat-2 river slope", session, per_page=50)
    assert total == 2484, "meta.count is the worldwide total, not the page size"
    assert len(records) == 1


def test_openalex_search_stops_on_a_short_page(monkeypatch):
    monkeypatch.setattr(ho, "_DELAY", 0)
    session = _FakeSession([{"meta": {"count": 1}, "results": [_work()]}])
    ho.openalex_search("q", session, per_page=50, max_pages=3)
    assert len(session.calls) == 1, "a page shorter than per_page ends the search"


def test_openalex_search_sends_filters_and_query(monkeypatch):
    monkeypatch.setattr(ho, "_DELAY", 0)
    session = _FakeSession([{"meta": {"count": 0}, "results": []}])
    ho.openalex_search("q", session, filters={"type": "article"})
    params = session.calls[0]["params"]
    assert params["search"] == "q"
    assert params["filter"] == "type:article"


def test_openalex_search_survives_an_http_error(monkeypatch):
    monkeypatch.setattr(ho, "_DELAY", 0)

    class _Failing:
        def get(self, *a, **k):
            return _Response({}, status=429)

    records, total = ho.openalex_search("q", _Failing())
    assert records == [] and total == 0


# ── dedup ─────────────────────────────────────────────────────────────────────

def _frame(rows):
    return pd.DataFrame(rows)


BASE = {
    "doi": "10.1000/new", "slug": "10.1000_new", "openalex_id": "W1",
    "title": "A brand new paper", "abstract": "", "year": 2024, "journal": "J",
    "cited_by_count": 3, "type": "article", "is_oa": True,
    "pdf_url": "https://example.org/a.pdf", "referenced_works_count": 10,
    "matched_thesis_ids": ["T01"], "n_query_hits": 1, "is_seed": False,
}


def test_dedup_drops_a_doi_already_in_the_corpus(monkeypatch):
    monkeypatch.setattr(ho, "_known_dois", lambda: {"10.1000/new"})
    monkeypatch.setattr(ho, "_known_titles", lambda: set())
    out = ho.dedup(_frame([BASE]))
    assert out.loc[0, "drop_reason"] == "doi_in_corpus"


def test_dedup_drops_a_slug_already_ingested(monkeypatch):
    monkeypatch.setattr(ho, "_known_dois", lambda: set())
    monkeypatch.setattr(ho, "_known_titles", lambda: set())
    monkeypatch.setattr(ho, "slug_already_ingested", lambda slug: slug == "10.1000_new")
    out = ho.dedup(_frame([BASE]))
    assert out.loc[0, "drop_reason"] == "slug_already_ingested"


def test_dedup_drops_a_title_already_in_the_corpus(monkeypatch):
    monkeypatch.setattr(ho, "_known_dois", lambda: set())
    monkeypatch.setattr(ho, "slug_already_ingested", lambda slug: False)
    monkeypatch.setattr(ho, "_known_titles", lambda: {"a brand new paper"})
    out = ho.dedup(_frame([BASE]))
    assert out.loc[0, "drop_reason"] == "title_in_corpus"


def test_dedup_keeps_a_genuinely_new_paper(monkeypatch):
    monkeypatch.setattr(ho, "_known_dois", lambda: set())
    monkeypatch.setattr(ho, "_known_titles", lambda: set())
    monkeypatch.setattr(ho, "slug_already_ingested", lambda slug: False)
    out = ho.dedup(_frame([BASE]))
    assert out.loc[0, "drop_reason"] == ""


def test_slug_convention_matches_recover_missing():
    """Both modules must derive the same stem or files are fetched twice."""
    from src.paper_audit.recover_missing import doi_to_slug as audit_slug
    from src.paper_3._utils import doi_to_slug as paper3_slug
    for doi in ("10.1029/2025GL119771", "https://doi.org/10.1016/j.rse.2016.12.029",
                "DOI:10.1038/s41597-023-02215-X"):
        assert paper3_slug(doi) == audit_slug(paper3_slug(doi))


# ── selection ─────────────────────────────────────────────────────────────────

def _selectable(**over):
    row = dict(BASE, drop_reason="")
    row.update(over)
    return row


def test_select_prefers_more_query_hits():
    frame = _frame([
        _selectable(doi="10.1/a", n_query_hits=1, cited_by_count=0),
        _selectable(doi="10.1/b", n_query_hits=5, cited_by_count=0),
    ])
    out = ho.select(frame, top_n_per_thesis=1)
    assert out.loc[out["doi"] == "10.1/b", "selected"].item()
    assert not out.loc[out["doi"] == "10.1/a", "selected"].item()


def test_select_never_picks_a_paywalled_row():
    frame = _frame([_selectable(is_oa=False, pdf_url="")])
    assert not ho.select(frame).loc[0, "selected"]


def test_select_never_picks_a_row_already_in_the_corpus():
    frame = _frame([_selectable(drop_reason="doi_in_corpus", n_query_hits=99)])
    assert not ho.select(frame).loc[0, "selected"]


def test_seeds_bypass_the_per_thesis_budget():
    rows = [_selectable(doi=f"10.1/{i}", n_query_hits=9, cited_by_count=500)
            for i in range(5)]
    rows.append(_selectable(doi="10.1/seed", n_query_hits=1,
                            cited_by_count=0, is_seed=True))
    out = ho.select(_frame(rows), top_n_per_thesis=2)
    assert out.loc[out["doi"] == "10.1/seed", "selected"].item()


def test_select_keeps_every_row_even_when_unselected():
    frame = _frame([_selectable(doi=f"10.1/{i}") for i in range(4)])
    out = ho.select(frame, top_n_per_thesis=1)
    assert len(out) == 4, "unselected rows still feed the coverage diagnostic"
    assert out["selected"].sum() == 1


def test_select_on_an_empty_frame_is_not_an_error():
    empty = pd.DataFrame(columns=list(BASE) + ["drop_reason"])
    out = ho.select(empty)
    assert out.empty and "selected" in out.columns


# ── report ────────────────────────────────────────────────────────────────────

def test_report_lists_paywalled_papers_with_the_manual_drop_path(tmp_path):
    frame = ho.select(_frame([
        _selectable(doi="10.1/open"),
        _selectable(doi="10.1/closed", slug="10.1_closed", is_oa=False, pdf_url=""),
    ]))
    text = ho.write_report(frame, tmp_path).read_text(encoding="utf-8")
    assert "10.1/closed" in text
    assert "10.1_closed.pdf" in text
    assert "pdf_missing" in text


# ── Boolean slices and queryset provenance ────────────────────────────────────

def test_every_boolean_slice_names_existing_theses(theses):
    from src.paper_3.harvest_queries import BOOLEAN_SLICES
    ids = {t.id for t in theses}
    for slice_id, query, thesis_ids in BOOLEAN_SLICES:
        assert len(thesis_ids) >= 2, slice_id
        assert set(thesis_ids) <= ids, slice_id
        assert query.count("(") == query.count(")"), f"{slice_id}: unbalanced parentheses"
        assert query.count('"') % 2 == 0, f"{slice_id}: unbalanced quotes"
        for op in (" and ", " or ", " not "):
            assert op not in query, f"{slice_id}: OpenAlex boolean operators are upper-case"


def test_boolean_slices_are_in_the_query_set_once_with_their_label(theses):
    from src.paper_3.harvest_queries import BOOLEAN_SLICES, SLICE_MAX_PAGES, SLICE_SOURCE
    qs = build_query_set(theses)
    by_label = {q["label"]: q for q in qs if q["source"] == SLICE_SOURCE}
    assert set(by_label) == {sid for sid, _, _ in BOOLEAN_SLICES}
    for sid, query, thesis_ids in BOOLEAN_SLICES:
        q = by_label[sid]
        assert q["query"] == query
        assert q["thesis_ids"] == sorted(thesis_ids)
        assert q["max_pages"] == SLICE_MAX_PAGES
        assert "cross_block" not in q["source"]
    assert all(q["label"] == "" for q in qs if q["source"] != SLICE_SOURCE)


def test_queries_json_carries_provenance_and_no_hardcoded_totals(tmp_path, theses):
    from src.paper_3.harvest_queries import BOOLEAN_SLICES, HARVEST_QUERYSET_VERSION
    payload = json.loads(run_queries(out_dir=tmp_path, theses=theses).read_text(encoding="utf-8"))
    assert payload["n_queries"] == len(payload["queries"])
    assert payload["n_boolean_slices"] == len(BOOLEAN_SLICES)
    prov = payload["provenance"]
    assert prov["queryset_version"] == HARVEST_QUERYSET_VERSION
    assert len(prov["queries_sha256"]) == 64 and len(prov["boolean_slices_sha256"]) == 64
    assert prov["n_queries"] == payload["n_queries"]
    assert "search=" in prov["openalex_search_mode"]


def test_queryset_hash_is_stable_across_builds(theses):
    from src.paper_3.harvest_queries import queryset_provenance
    a = queryset_provenance(build_query_set(theses))
    b = queryset_provenance(build_query_set(theses))
    assert a["queries_sha256"] == b["queries_sha256"]
    assert a["boolean_slices_sha256"] == b["boolean_slices_sha256"]


def test_discover_records_which_slice_matched_and_writes_provenance(tmp_path, monkeypatch, theses):
    from src.paper_3.harvest_queries import BOOLEAN_SLICES
    monkeypatch.setattr(ho, "_DELAY", 0)
    monkeypatch.setattr(ho, "_known_dois", lambda: set())
    monkeypatch.setattr(ho, "_known_titles", lambda: set())
    monkeypatch.setattr(ho, "slug_already_ingested", lambda slug: False)
    session = _FakeSession([{"meta": {"count": 7}, "results": [_work()]}])
    frame = ho.discover(theses=theses, out_dir=tmp_path, session=session)
    assert len(frame) == 1
    labels = set(frame.iloc[0]["matched_labels"])
    assert labels == {sid for sid, _, _ in BOOLEAN_SLICES}
    prov = json.loads((tmp_path / ho.PROVENANCE_FILE).read_text(encoding="utf-8"))
    assert prov["n_queries_run"] == len(build_query_set(theses))
    assert len(prov["queries_sha256"]) == 64


def test_extend_selection_admits_only_downloadable_new_works_and_records_why():
    frame = _frame([
        {**BASE, "doi": "10.1/a", "selected": False},
        {**BASE, "doi": "10.1/b", "selected": False, "is_oa": False},
        {**BASE, "doi": "10.1/c", "selected": False, "drop_reason": "doi_in_corpus"},
        {**BASE, "doi": "10.1/d", "selected": True},
        {**BASE, "doi": "10.1/e", "selected": False},
    ])
    frame["drop_reason"] = frame.get("drop_reason", pd.Series("", index=frame.index)).fillna("")
    out = ho.extend_selection(frame, {"10.1/a", "10.1/b", "10.1/c", "10.1/d"}, "slice:S1")
    by = out.set_index("doi")
    assert by.loc["10.1/a", "selected"] and by.loc["10.1/a", "selection_reason"] == "slice:S1"
    assert not by.loc["10.1/b", "selected"], "paywalled is never admitted"
    assert not by.loc["10.1/c", "selected"], "already in corpus is never re-fetched"
    assert by.loc["10.1/d", "selection_reason"] == "budget", "existing selection keeps its reason"
    assert not by.loc["10.1/e", "selected"], "not asked for"


def test_header_consolidation_is_configurable_and_defaults_to_on(monkeypatch):
    """An unreachable CrossRef turns a 19 s parse into a >600 s hang, so this must
    be switchable — but the default stays as the existing corpus was built."""
    import importlib

    from src.ingestion import grobid_client

    monkeypatch.delenv("GROBID_CONSOLIDATE_HEADER", raising=False)
    reloaded = importlib.reload(grobid_client)
    assert dict(reloaded.GROBID_FORM_PARAMS)["consolidateHeader"] == "1"

    monkeypatch.setenv("GROBID_CONSOLIDATE_HEADER", "0")
    reloaded = importlib.reload(grobid_client)
    assert dict(reloaded.GROBID_FORM_PARAMS)["consolidateHeader"] == "0"
    # Coordinates must survive the change: the layout-aware KG depends on them.
    assert [v for k, v in reloaded.GROBID_FORM_PARAMS if k == "teiCoordinates"]

    monkeypatch.delenv("GROBID_CONSOLIDATE_HEADER", raising=False)
    importlib.reload(grobid_client)
