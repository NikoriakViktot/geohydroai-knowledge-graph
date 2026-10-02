"""Finding a paper's PDF: identifier parsing, legal open-access copies, the endpoint. No network."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.api.app import create_app
from src.contracts.identity import PaperIdentity
from src.services import doi as doi_service
from src.services import http, identity_store, locate

from tests.test_api import READ_KEY, StubKeys, StubManifest


@pytest.mark.parametrize("query, doi, how", [
    ("10.1016/j.isprsjprs.2019.10.017", "10.1016/j.isprsjprs.2019.10.017", "doi"),
    ("https://doi.org/10.1029/2018WR023457", "10.1029/2018wr023457", "doi"),
    ("https://agupubs.onlinelibrary.wiley.com/doi/full/10.1029/2018WR023457", "10.1029/2018wr023457", "url"),
    ("https://link.springer.com/content/pdf/10.1007/s10712-015-9346-y.pdf", "10.1007/s10712-015-9346-y", "url"),
    ("https://iopscience.iop.org/article/10.1088/1748-9326/ac4d4f/meta", "10.1088/1748-9326/ac4d4f", "url"),
    ("https://arxiv.org/abs/1802.08872v2", "10.48550/arxiv.1802.08872", "arxiv"),
    ("1802.08872", "10.48550/arxiv.1802.08872", "arxiv"),
    ("liang2020", None, "paper_id"),
])
def test_parse(query, doi, how):
    got_doi, got_how, _, _ = locate.parse(query)
    assert (got_doi, got_how) == (doi, how)


def test_pii_urls_resolve_through_crossref(monkeypatch):
    seen = {}

    def fake_get_json(service, key, url, params=None, **kw):
        seen.update(service=service, key=key, params=params)
        return http.Fetched(200, {"message": {"items": [{"DOI": "10.1016/J.ISPRSJPRS.2019.10.011"}]}}, None, "network", url)

    monkeypatch.setattr(http, "get_json", fake_get_json)
    doi, how, _, _ = locate.parse("https://www.sciencedirect.com/science/article/pii/S0924271619302485")
    assert (doi, how) == ("10.1016/j.isprsjprs.2019.10.011", "pii")
    assert seen["params"] == {"filter": "alternative-id:S0924271619302485", "rows": 2}


def test_the_api_mode_never_fetches_pages():
    doi, how, _, notes = locate.parse("https://www.mdpi.com/2072-4292/11/19/2210", allow_fetch=False)
    assert doi is None and "give the DOI" in notes[0]


def test_open_access_copies_are_merged_and_ranked(monkeypatch):
    openalex = {"open_access": {"is_oa": True, "oa_status": "green"},
                "best_oa_location": {"is_oa": True, "landing_page_url": "https://repo.example/abs/1",
                                     "pdf_url": None, "version": "acceptedVersion", "source": {"type": "repository"}},
                "locations": [
                    {"is_oa": False, "pdf_url": "https://publisher.example/paywalled.pdf"},
                    {"is_oa": True, "pdf_url": "https://repo.example/1.pdf", "version": "acceptedVersion",
                     "source": {"type": "repository"}},
                    {"is_oa": True, "pdf_url": "https://arxiv.org/pdf/1802.08872", "version": "submittedVersion",
                     "source": {"type": "repository"}}]}
    monkeypatch.setattr(locate, "unpaywall", lambda doi: {
        "is_oa": True, "oa_status": "green",
        "oa_locations": [{"url_for_pdf": "https://repo.example/1.pdf", "version": "acceptedVersion",
                          "license": "cc-by", "host_type": "repository"}]})
    found, status = locate.open_access("10.1/x", openalex, None)
    assert [x["url"] for x in found] == ["https://repo.example/1.pdf", "https://arxiv.org/pdf/1802.08872",
                                         "https://repo.example/abs/1"]
    assert found[0]["source"] == "openalex+unpaywall" and found[0]["license"] == "cc-by"
    assert "https://publisher.example/paywalled.pdf" not in [x["url"] for x in found]
    assert status == {"is_oa": True, "oa_status": "green", "unpaywall": True}


def test_endpoint(monkeypatch, tmp_path):
    paper = PaperIdentity(paper_id="liang2020", doi="10.1016/j.isprsjprs.2019.10.017", title="A local thresholding "
                          "approach", year=2020, identity_status="ok")
    pdf = tmp_path / "liang2020.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    monkeypatch.setattr(identity_store, "resolve", lambda **kw: identity_store.Resolved("alias", 1.0, paper, None)
                        if paper.doi in (kw.get("doi"),) or kw.get("paper_id") == "liang2020" else None)
    monkeypatch.setattr(locate, "corpus_files", lambda ids: [{"paper_id": "liang2020", "kind": "pdf", "path": str(pdf),
                                                              "windows_path": None, "exists": True, "status": "ok"}])
    monkeypatch.setattr(doi_service, "lookup", lambda d, refresh=False: doi_service.Registries(
        openalex={"open_access": {"is_oa": True, "oa_status": "bronze"},
                  "locations": [{"is_oa": True, "landing_page_url": "https://doi.org/" + d, "version": None,
                                 "source": {"type": "journal"}}]}, fetched={"openalex": "cache"}))
    monkeypatch.setattr(locate, "unpaywall", lambda doi: None)
    client = TestClient(create_app(key_store=StubKeys(), manifest=StubManifest()))
    body = client.get("/v1/locate", headers={"X-API-Key": READ_KEY}, params={"q": "liang2020"}).json()
    assert body["doi"] == paper.doi and body["in_corpus"]["paper_id"] == "liang2020"
    assert body["files"][0]["exists"] and body["oa_status"] == "bronze" and body["best_pdf_url"] is None
    assert body["open_access"][0]["host"] == "publisher" and body["doi_url"].endswith(paper.doi)
    r = client.get("/v1/locate", headers={"X-API-Key": READ_KEY}, params={"q": "https://example.org/no-doi-here"})
    assert r.status_code == 404 and "give the DOI" in r.json()["detail"]
