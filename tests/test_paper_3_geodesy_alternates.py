"""Recovering paywalled `best_oa_location`s through their green copies.

The probe that motivated this: a Springer `pdf_url` of the form
`/content/pdf/{doi}.pdf` — already the canonical PDF path — returned HTTP 200
with `text/html`. The rewrite was a no-op; the paper is paywalled at the
publisher and OpenAlex's `is_oa` came from a repository copy that
`to_record` had discarded.
"""
from __future__ import annotations

import pandas as pd
import pytest

from src.paper_3.briefs import geodesy as g


# ── host ranking ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("url,rank", [
    ("https://arxiv.org/pdf/2101.00001", 0),
    ("https://hal.science/hal-01234/document", 0),
    ("https://www.mdpi.com/x/pdf", 0),
    ("https://hess.copernicus.org/x.pdf", 0),
    ("https://link.springer.com/content/pdf/x.pdf", 1),
    ("https://onlinelibrary.wiley.com/doi/pdf/x", 2),
    ("https://www.sciencedirect.com/x", 2),
])
def test_repositories_first_publishers_middle_blockers_last(url, rank):
    assert g._host_rank(url) == rank


# ── alternate discovery ───────────────────────────────────────────────────────

def _work(best=None, locations=()):
    return {
        "doi": "https://doi.org/10.1/x",
        "best_oa_location": {"pdf_url": best} if best else None,
        "locations": [{"is_oa": oa, "pdf_url": u} for oa, u in locations],
    }


def test_alternates_collect_every_oa_location_friendliest_first(monkeypatch):
    from src.paper_3 import harvest_openalex as ho
    monkeypatch.setattr(ho, "fetch_by_doi", lambda doi, session: _work(
        best="https://link.springer.com/content/pdf/x.pdf",
        locations=[
            (True, "https://link.springer.com/content/pdf/x.pdf"),
            (True, "https://hal.science/hal-1/document"),
            (False, "https://onlinelibrary.wiley.com/x"),   # not OA: skipped
            (True, "https://arxiv.org/pdf/1"),
        ]))
    monkeypatch.setattr(ho, "make_session", lambda: object())

    urls = g.alternate_pdf_urls("10.1/x")
    assert urls[0].startswith(("https://hal.science", "https://arxiv.org"))
    assert urls[-1].startswith("https://link.springer.com")
    assert "https://onlinelibrary.wiley.com/x" not in urls
    assert len(urls) == len(set(urls)), "no duplicates"


def test_alternates_include_best_even_if_not_in_locations(monkeypatch):
    from src.paper_3 import harvest_openalex as ho
    monkeypatch.setattr(ho, "fetch_by_doi", lambda doi, session: _work(
        best="https://arxiv.org/pdf/9", locations=[]))
    monkeypatch.setattr(ho, "make_session", lambda: object())
    assert g.alternate_pdf_urls("10.1/x") == ["https://arxiv.org/pdf/9"]


def test_alternates_are_empty_when_openalex_has_nothing(monkeypatch):
    from src.paper_3 import harvest_openalex as ho
    monkeypatch.setattr(ho, "fetch_by_doi", lambda doi, session: None)
    monkeypatch.setattr(ho, "make_session", lambda: object())
    assert g.alternate_pdf_urls("10.1/x") == []


# ── the retry step ────────────────────────────────────────────────────────────

def _manifest(tmp_path, status):
    gd = g.geodesy_dir(tmp_path)
    (gd / "pdf").mkdir(parents=True)
    frame = pd.DataFrame([{
        "doi": "10.1/x", "slug": "10.1_x", "title": "paywalled at publisher",
        "source_type": "paper", "pdf_path": str(gd / "pdf" / "10.1_x.pdf"),
        "download_status": status, "xml_path": "", "grobid_status": "",
        "text_path": "", "n_chars": 0,
    }])
    frame.to_parquet(gd / g.FULLTEXT_MANIFEST, index=False)
    return gd


def test_retry_recovers_through_a_repository_copy(tmp_path, monkeypatch):
    gd = _manifest(tmp_path, "not_pdf_http_200")

    monkeypatch.setattr(g, "alternate_pdf_urls",
                        lambda doi, session=None: ["https://arxiv.org/pdf/1"])

    def fake_fetch(session, url, target):
        # write_bytes returns the byte count, which is truthy — a bare
        # `write_bytes(...) or "downloaded"` would yield 5, not the status.
        target.write_bytes(b"%PDF-")
        return "downloaded"

    monkeypatch.setattr(g, "_fetch_pdf", fake_fetch)
    monkeypatch.setattr(g.time, "sleep", lambda s: None)
    from src.paper_3 import harvest_ingest, harvest_openalex
    monkeypatch.setattr(harvest_ingest, "_pdf_session", lambda: object())
    monkeypatch.setattr(harvest_openalex, "make_session", lambda: object())

    out = g.retry_alternates(tmp_path)
    assert out.iloc[0]["download_status"] == "downloaded"
    assert (gd / "pdf" / "10.1_x.pdf").exists()


def test_retry_records_how_many_alternates_were_tried_on_failure(tmp_path, monkeypatch):
    _manifest(tmp_path, "not_pdf_http_200")

    monkeypatch.setattr(g, "alternate_pdf_urls",
                        lambda doi, session=None: ["https://a/1", "https://b/2"])
    monkeypatch.setattr(g, "_fetch_pdf", lambda session, url, target: "not_pdf_http_403")
    monkeypatch.setattr(g.time, "sleep", lambda s: None)
    from src.paper_3 import harvest_ingest, harvest_openalex
    monkeypatch.setattr(harvest_ingest, "_pdf_session", lambda: object())
    monkeypatch.setattr(harvest_openalex, "make_session", lambda: object())

    out = g.retry_alternates(tmp_path)
    assert out.iloc[0]["download_status"] == "not_pdf_http_403|alternates_tried=2"


def test_retry_leaves_successful_rows_alone(tmp_path, monkeypatch):
    _manifest(tmp_path, "downloaded")
    called = []
    monkeypatch.setattr(g, "alternate_pdf_urls",
                        lambda doi, session=None: called.append(doi) or [])
    from src.paper_3 import harvest_ingest, harvest_openalex
    monkeypatch.setattr(harvest_ingest, "_pdf_session", lambda: object())
    monkeypatch.setattr(harvest_openalex, "make_session", lambda: object())

    g.retry_alternates(tmp_path)
    assert called == [], "a downloaded row must not cost an OpenAlex lookup"


def test_alternates_is_a_step_between_download_and_grobid():
    steps = list(g.STEPS)
    assert steps.index("download") < steps.index("alternates") < steps.index("grobid")
