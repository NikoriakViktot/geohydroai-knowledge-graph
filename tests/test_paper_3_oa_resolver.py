"""OA resolver: asks only open-access indexes, saves only real PDFs, records routes.

No network — a fake session serves canned payloads per URL.
"""
from __future__ import annotations

import pandas as pd
import pytest

from src.paper_3 import oa_resolver as oa

PDF = b"%PDF-1.7 fake"


class _Resp:
    def __init__(self, status=200, payload=None, content=b""):
        self.status_code = status
        self._payload = payload
        self.content = content

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class _Session:
    def __init__(self, table: dict):
        self.table = table
        self.calls: list[str] = []

    def get(self, url, params=None, timeout=None, allow_redirects=True):
        self.calls.append(url)
        for key, resp in self.table.items():
            if url.startswith(key):
                return resp
        return _Resp(404)


def test_unpaywall_only_returns_pdf_urls_of_open_works():
    s = _Session({oa._UNPAYWALL.format(doi="10.1/a"): _Resp(payload={
        "is_oa": True,
        "best_oa_location": {"url_for_pdf": "https://x/best.pdf"},
        "oa_locations": [{"url_for_pdf": "https://x/best.pdf"}, {"url_for_pdf": "https://x/2.pdf"},
                         {"url_for_pdf": None}]})})
    assert oa.unpaywall_urls("10.1/a", s, "me@example.org") == ["https://x/best.pdf", "https://x/2.pdf"]


def test_unpaywall_is_skipped_without_an_email_and_for_closed_works():
    s = _Session({oa._UNPAYWALL.format(doi="10.1/a"): _Resp(payload={"is_oa": False})})
    assert oa.unpaywall_urls("10.1/a", s, "") == []
    assert s.calls == [], "no email, no request"
    assert oa.unpaywall_urls("10.1/a", s, "me@example.org") == []


def test_europepmc_uses_open_pdf_links_and_pmcid_render():
    s = _Session({oa._EUROPEPMC: _Resp(payload={"resultList": {"result": [
        {"doi": "10.1/A", "pmcid": "PMC123", "isOpenAccess": "Y",
         "fullTextUrlList": {"fullTextUrl": [
             {"documentStyle": "pdf", "availability": "Open access", "url": "https://e/1.pdf"},
             {"documentStyle": "html", "availability": "Open access", "url": "https://e/1.html"},
             {"documentStyle": "pdf", "availability": "Subscription required", "url": "https://e/paywall.pdf"},
         ]}},
        {"doi": "10.1/other", "pmcid": "PMC999", "isOpenAccess": "Y"},
    ]}})})
    urls = oa.europepmc_urls("10.1/a", s)
    assert urls == ["https://e/1.pdf", oa._EUROPEPMC_PDF.format(pmcid="PMC123")]


def test_openalex_locations_require_the_is_oa_flag():
    s = _Session({oa._OPENALEX.format(doi="10.1/a"): _Resp(payload={"locations": [
        {"is_oa": True, "pdf_url": "https://o/1.pdf"},
        {"is_oa": False, "pdf_url": "https://o/closed.pdf"},
        {"is_oa": True, "pdf_url": None},
    ]})})
    assert oa.openalex_location_urls("10.1/a", s) == ["https://o/1.pdf"]


@pytest.mark.parametrize("doi, landing, expected", [
    ("10.3390/rs16214010", "https://www.mdpi.com/2072-4292/16/21/4010",
     "https://www.mdpi.com/2072-4292/16/21/4010/pdf"),
    ("10.5194/hess-27-1-2023", "", "https://hess.copernicus.org/articles/27/1/2023/hess-27-1-2023.pdf"),
    ("10.3389/feart.2024.1234", "", "https://www.frontiersin.org/articles/10.3389/feart.2024.1234/pdf"),
    ("10.48550/arxiv.2401.01234", "", "https://arxiv.org/pdf/2401.01234"),
    ("10.1029/2025gl120832", "", "https://onlinelibrary.wiley.com/doi/pdfdirect/10.1029/2025gl120832"),
])
def test_publisher_patterns_follow_from_the_doi(doi, landing, expected):
    assert expected in oa.publisher_pattern_urls(doi, landing, is_oa=True)


def test_wiley_pattern_is_only_offered_for_open_works():
    assert oa.publisher_pattern_urls("10.1029/2025gl120832", "", is_oa=False) == []


def test_fetch_pdf_refuses_html_served_as_pdf(tmp_path):
    s = _Session({"https://x/html": _Resp(200, content=b"<html>interstitial</html>"),
                  "https://x/real": _Resp(200, content=PDF)})
    assert not oa.fetch_pdf("https://x/html", s, tmp_path / "a.pdf")
    assert not (tmp_path / "a.pdf").exists()
    assert oa.fetch_pdf("https://x/real", s, tmp_path / "b.pdf")
    assert (tmp_path / "b.pdf").read_bytes() == PDF


def test_run_attempts_only_open_access_rows_and_records_the_route(tmp_path, monkeypatch):
    monkeypatch.setattr(oa, "_DELAY", 0)
    s = _Session({
        oa._UNPAYWALL.format(doi="10.1/open"): _Resp(payload={
            "is_oa": True, "best_oa_location": {"url_for_pdf": "https://u/open.pdf"}}),
        "https://u/open.pdf": _Resp(200, content=PDF),
        oa._UNPAYWALL.format(doi="10.1/none"): _Resp(404),
        oa._EUROPEPMC: _Resp(payload={"resultList": {"result": []}}),
        oa._OPENALEX.format(doi="10.1/none"): _Resp(payload={"locations": []}),
    })
    frame = pd.DataFrame([
        {"doi": "10.1/open", "slug": "10.1_open", "is_oa": True, "drop_reason": ""},
        {"doi": "10.1/none", "slug": "10.1_none", "is_oa": True, "drop_reason": ""},
        {"doi": "10.1/closed", "slug": "10.1_closed", "is_oa": False, "drop_reason": ""},
        {"doi": "10.1/have", "slug": "10.1_have", "is_oa": True, "drop_reason": ""},
    ])
    (tmp_path / "10.1_have.pdf").write_bytes(PDF)
    out = oa.run(frame, out_dir=tmp_path, session=s, email="me@example.org", pdf_dir=tmp_path)
    by = out.set_index("doi")
    assert by.loc["10.1/open", "route"] == "unpaywall"
    assert (tmp_path / "10.1_open.pdf").exists()
    assert by.loc["10.1/none", "route"] == "unresolved"
    assert "10.1/closed" not in by.index, "paywalled works are never attempted"
    assert "10.1/have" not in by.index, "already on disk is not re-fetched"
    assert not any("10.1/closed" in c for c in s.calls)
    assert (tmp_path / oa.RESULT_FILE).exists()
