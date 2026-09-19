"""Host-aware selection and URL rewriting for the geodesy mini-corpus.

The first download round lost 46 of 60 to two failure modes: publishers that
refuse scripted requests outright, and OpenAlex `pdf_url`s that point at an
article page rather than the PDF binary. Neither is a retrieval problem, and
neither should silently shrink the corpus.
"""
from __future__ import annotations

import pandas as pd
import pytest

from src.paper_3.briefs import geodesy as g


# ── URL rewriting ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("url,expected", [
    ("https://link.springer.com/article/10.1007/s00190-020-01417-y",
     "https://link.springer.com/content/pdf/10.1007/s00190-020-01417-y.pdf"),
    ("https://link.springer.com/article/10.1007/s00190-020-01417-y?error=cookies",
     "https://link.springer.com/content/pdf/10.1007/s00190-020-01417-y.pdf"),
    ("https://www.mdpi.com/2072-4292/12/3/456/htm",
     "https://www.mdpi.com/2072-4292/12/3/456/pdf"),
    ("https://www.mdpi.com/2072-4292/12/3/456",
     "https://www.mdpi.com/2072-4292/12/3/456/pdf"),
    ("https://www.mdpi.com/2072-4292/12/3/456/pdf",
     "https://www.mdpi.com/2072-4292/12/3/456/pdf"),
    ("https://earth-planets-space.springeropen.com/articles/10.1186/s40623-020-01234-5",
     "https://earth-planets-space.springeropen.com/track/pdf/10.1186/s40623-020-01234-5"),
])
def test_known_article_pages_are_rewritten_to_pdf(url, expected):
    assert g._rewrite_pdf_url(url) == expected


@pytest.mark.parametrize("url", [
    "https://onlinelibrary.wiley.com/doi/pdf/10.1029/x",
    "https://hess.copernicus.org/articles/27/1011/2023/hess-27-1011-2023.pdf",
    "https://arxiv.org/pdf/2101.00001",
    "",
])
def test_unrecognised_urls_are_left_alone(url):
    """A rewrite that guesses would turn a working link into a broken one."""
    assert g._rewrite_pdf_url(url) == url


def test_rewrite_is_idempotent():
    once = g._rewrite_pdf_url("https://www.mdpi.com/2072-4292/12/3/456/htm")
    assert g._rewrite_pdf_url(once) == once


# ── host-aware selection ──────────────────────────────────────────────────────

def _cand(doi, host, fams=2, prio=5.0, oa=True):
    return {"doi": doi, "slug": doi.replace("/", "_"), "title": doi,
            "is_oa": oa, "pdf_url": f"https://{host}/x/{doi}" if oa else "",
            "n_families_matched": fams, "download_priority": prio}


def test_blocking_hosts_are_queued_last_not_dropped():
    frame = pd.DataFrame([
        _cand("10.1/wiley", "onlinelibrary.wiley.com", prio=50.0),
        _cand("10.1/mdpi", "www.mdpi.com", prio=1.0),
        _cand("10.1/copernicus", "hess.copernicus.org", prio=1.0),
    ])
    chosen = g.select_for_download(frame, target_max=2)
    assert set(chosen["doi"]) == {"10.1/mdpi", "10.1/copernicus"}

    everything = g.select_for_download(frame, target_max=3)
    assert "10.1/wiley" in set(everything["doi"]), "blocked hosts still get a turn"


def test_exclude_dois_skips_already_tried():
    frame = pd.DataFrame([_cand(f"10.1/{i}", "www.mdpi.com") for i in range(4)])
    chosen = g.select_for_download(frame, target_max=10,
                                   exclude_dois={"10.1/0", "10.1/1"})
    assert set(chosen["doi"]) == {"10.1/2", "10.1/3"}


def test_selection_output_has_no_helper_columns():
    frame = pd.DataFrame([_cand("10.1/a", "www.mdpi.com")])
    chosen = g.select_for_download(frame)
    assert not any(c.startswith("_") for c in chosen.columns)


def test_all_first_round_blockers_are_listed():
    """The hosts that failed every request should be the ones deprioritised."""
    for host in ("onlinelibrary.wiley.com", "sciencedirect.com", "degruyter.com",
                 "espace.curtin.edu.au", "tandfonline.com"):
        assert host in g.BLOCKING_HOSTS
    assert "mdpi.com" not in g.BLOCKING_HOSTS
    assert "hess.copernicus.org" not in g.BLOCKING_HOSTS


# ── fetching ──────────────────────────────────────────────────────────────────

class _Resp:
    def __init__(self, status, content):
        self.status_code, self.content = status, content


class _Session:
    def __init__(self, resp):
        self._resp = resp

    def get(self, url, timeout=None, allow_redirects=True):
        if isinstance(self._resp, Exception):
            raise self._resp
        return self._resp


def test_fetch_writes_only_a_real_pdf(tmp_path):
    target = tmp_path / "a.pdf"
    status = g._fetch_pdf(_Session(_Resp(200, b"%PDF-1.4 ...")), "u", target)
    assert status == "downloaded"
    assert target.exists()


def test_fetch_rejects_html_served_with_200(tmp_path):
    """The Springer failure mode: 200 OK, but it is a landing page."""
    target = tmp_path / "a.pdf"
    status = g._fetch_pdf(_Session(_Resp(200, b"<!DOCTYPE html>")), "u", target)
    assert status == "not_pdf_http_200"
    assert not target.exists()


def test_fetch_reports_the_status_code_on_refusal(tmp_path):
    status = g._fetch_pdf(_Session(_Resp(403, b"")), "u", tmp_path / "a.pdf")
    assert status == "not_pdf_http_403"


def test_fetch_reports_a_network_error_without_raising(tmp_path):
    import requests
    status = g._fetch_pdf(_Session(requests.ConnectionError()), "u", tmp_path / "a.pdf")
    assert status.startswith("error:")
