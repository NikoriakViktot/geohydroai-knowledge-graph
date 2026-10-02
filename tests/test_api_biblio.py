"""DOI metadata and verification with a fake registry and an in-memory cache.

No network and no live Postgres: httpx gets a MockTransport serving the trimmed registry
records in tests/fixtures/registry (fetched 2026-10-02), and biblio.http_cache is a dict.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from src.api.app import create_app
from src.contracts.identity import PaperIdentity
from src.services import http, identity_store

from tests.test_api import READ_KEY, StubKeys, StubManifest

H = {"X-API-Key": READ_KEY}
FIX = Path(__file__).parent / "fixtures" / "registry"
SECRET = "sk-test-secret"

ROUTES = {
    "api.crossref.org/works/10.1007/s10712-015-9346-y": "crossref_biancamaria",
    "api.openalex.org/works/doi:10.1007/s10712-015-9346-y": "openalex_biancamaria",
    "api.crossref.org/works/10.24425/agg.2023.146162": "crossref_monti",
    "api.openalex.org/works/doi:10.24425/agg.2023.146162": "openalex_monti",
    "api.datacite.org/dois/10.5067/atlas/atl13.006": "datacite_atl13",
}


class Registry:
    """Fake registries; `down` makes every request fail with 503."""

    def __init__(self):
        self.down = False
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.down:
            return httpx.Response(503)
        name = ROUTES.get(f"{request.url.host}{request.url.path}")
        if name is None:
            return httpx.Response(404)
        return httpx.Response(200, json=json.loads((FIX / f"{name}.json").read_text(encoding="utf-8")))


@pytest.fixture
def registry(monkeypatch):
    reg = Registry()
    cache: dict = {}
    monkeypatch.setattr(http, "client", lambda: httpx.Client(transport=httpx.MockTransport(reg)))
    monkeypatch.setattr(http, "cache_read", lambda service, key: cache.get((service, key)))

    def write(service, key, url, status, body, fetched_at):
        cache[(service, key)] = (status, body, fetched_at, fetched_at + http.TTL[status], url)

    monkeypatch.setattr(http, "cache_write", write)
    monkeypatch.setattr(http.time, "sleep", lambda s: None)
    from src.config import settings
    monkeypatch.setattr(settings, "OPEN_ALEX_API", SECRET)
    reg.cache = cache
    return reg


@pytest.fixture
def client(monkeypatch, registry):
    monti = PaperIdentity(paper_id="10.24425_agg.2023.146162", doi="10.24425/agg.2023.146162", title="Monti",
                          year=2024, identity_status="ok")

    def fake_resolve(*, doi=None, **kw):
        return identity_store.Resolved("alias", 1.0, monti, None) if doi == monti.doi else None

    monkeypatch.setattr(identity_store, "resolve", fake_resolve)
    return TestClient(create_app(key_store=StubKeys(), manifest=StubManifest()))


BIANCAMARIA = {"key": "Biancamaria_2016", "doi": "https://doi.org/10.1007/S10712-015-9346-Y",
               "title": "The SWOT mission and its capabilities for land hydrology",
               "authors": "Biancamaria, S. and Lettenmaier, D. P. and Pavelsky, T. M.", "year": "2016a",
               "journal": "Surv. Geophys.", "volume": 37, "issue": 2, "pages": "307--337"}
MONTI = {"key": "Monti_2024", "doi": "10.24425/agg.2023.146162",
         "title": "The Nova Kakhovka dam collapse flooding as seen from Sentinel-1 SAR satellite images",
         "authors": "Monti, R. and Rossi, L. and Reguzzoni, M.", "year": 2024,
         "journal": "Advances in Geodesy and Geoinformation"}


def verify(client, *entries, **extra):
    r = client.post("/v1/doi/verify", headers=H, json={"entries": list(entries), **extra})
    assert r.status_code == 200, r.text
    return r.json()


def test_online_and_print_years_give_a_note(client):
    res = verify(client, BIANCAMARIA)["results"][0]
    assert res["verdict"] == "VERIFIED_WITH_NOTES" and res["diffs"] == []
    assert res["notes"][0].startswith("online 2015-10-27, print 2016-03 (vol. 37)")
    reg = res["registry"]
    assert (reg["year_online"], reg["year_print"], reg["year_issued"]) == (2015, 2016, 2015)
    assert reg["sources"]["title"] == "crossref" and reg["sources"]["oa_status"] == "openalex"


def test_page_artefact_is_a_note_and_the_corpus_copy_is_named(client):
    body = verify(client, MONTI)
    res = body["results"][0]
    assert res["verdict"] == "VERIFIED_WITH_NOTES" and res["diffs"] == []
    assert "'50-50' look like an article-number artefact" in res["notes"][0]
    assert res["in_corpus"]["paper_id"] == "10.24425_agg.2023.146162"
    assert body["summary"] == {"VERIFIED_WITH_NOTES": 1}


def test_wrong_metadata_is_a_mismatch(client):
    wrong = dict(MONTI, title="Flooding of the Dnipro seen from space", year=2022, authors="Rossi, L. and Monti, R.")
    res = verify(client, wrong)["results"][0]
    assert res["verdict"] == "MISMATCH"
    assert {(d["field"], d["severity"]) for d in res["diffs"]} >= {("title", "major"), ("year", "major"),
                                                                    ("authors[0]", "major")}


def test_minor_differences_do_not_fail(client):
    res = verify(client, dict(BIANCAMARIA, volume=36, pages="300-337",
                              authors="Biancamaria, J. and Lettenmaier, D. P. and Pavelsky, T. M."))["results"][0]
    assert res["verdict"] == "VERIFIED_WITH_NOTES"
    assert {d["field"]: d["severity"] for d in res["diffs"]} == {"volume": "minor", "pages": "minor",
                                                                 "authors.initials": "minor"}


def test_bibtex_entries_and_datacite_dois(client):
    bib = ("@dataset{ATL13_v6, author = {Jasinski, Michael and others}, year = {2023},\n"
           " title = {{ATLAS/ICESat-2 L3A Along Track Inland Surface Water Data, Version 6}},\n"
           " doi = {10.5067/ATLAS/ATL13.006}}")
    res = verify(client, {"bibtex": bib})["results"][0]
    assert res["input_key"] == "ATL13_v6" and res["verdict"] == "VERIFIED"
    assert res["registry"]["sources"]["title"] == "datacite"
    assert res["registry"]["fetched"]["crossref"] == "not_found"


def test_unresolved_and_not_a_doi(client):
    body = verify(client, {"key": "Pedregosa_2011", "title": "Scikit-learn: Machine Learning in Python"},
                  {"key": "Bad", "doi": "doi-less"}, {"key": "Ghost", "doi": "10.9999/does-not-exist"})
    a, b, c = body["results"]
    assert a["verdict"] == "UNRESOLVED" and "no DOI given" in a["notes"][0]
    assert b["verdict"] == "NOT_A_DOI"
    assert c["verdict"] == "UNRESOLVED" and "no registry knows" in c["notes"][0]


def test_a_registry_outage_is_not_cached_as_not_found(client, registry):
    registry.down = True
    res = verify(client, MONTI)["results"][0]
    assert res["verdict"] == "UNRESOLVED" and "did not answer" in res["notes"][0]
    assert registry.cache == {}
    registry.down = False
    assert verify(client, MONTI)["results"][0]["verdict"] == "VERIFIED_WITH_NOTES"
    assert ("crossref", "10.24425/agg.2023.146162") in registry.cache


def test_stale_answers_are_used_and_reported(client, registry):
    verify(client, MONTI)
    for k, (status, body, at, _exp, url) in list(registry.cache.items()):
        registry.cache[k] = (status, body, at, at, url)        # expired
    registry.down = True
    res = verify(client, MONTI)["results"][0]
    assert res["verdict"] == "VERIFIED_WITH_NOTES"
    assert res["registry"]["fetched"] == {"crossref": "stale_cache", "openalex": "stale_cache"}
    assert any("cached answer was used" in n for n in res["notes"])


def test_the_api_key_never_leaves_the_request(client, registry):
    verify(client, MONTI)
    sent = [r for r in registry.requests if r.url.host == "api.openalex.org"]
    assert sent and all(r.url.params.get("api_key") == SECRET for r in sent)
    stored_urls = [v[4] for v in registry.cache.values()]
    assert stored_urls and not any(SECRET in u for u in stored_urls)
    r = client.get("/v1/doi/10.24425/agg.2023.146162", headers=H)
    assert SECRET not in r.text


def test_author_lists_with_shared_family_names_and_corporate_authors():
    from src.contracts.api import BibInput, DoiAuthor, DoiMetadata
    from src.services.bibtex import parse_authors
    from src.services.doi import compare
    assert parse_authors("Jasinski, Michael and {the ICESat-2 Science Team}")[0] == [
        ("Jasinski", "Michael", False), ("the ICESat-2 Science Team", "", True)]
    meta = DoiMetadata(doi="10.1000/x", authors=[DoiAuthor(family="Höhle", given="Joachim"),
                                                 DoiAuthor(family="Höhle", given="Michael")])
    diffs, _ = compare(BibInput(authors="Hohle, J. and Höhle, M."), meta)
    assert diffs == []
    diffs, _ = compare(BibInput(authors="Höhle, J. and {Some Team}"), meta)
    assert diffs == []


@pytest.mark.parametrize("given, registry, same", [
    ("Surv. Geophys.", "Surveys in Geophysics", True),
    ("J. Hydrol.", "Journal of Hydrology", True),
    ("Nat. Hazards Earth Syst. Sci.", "Natural Hazards and Earth System Sciences", True),
    ("IEEE Trans. Geosci. Remote Sens.", "IEEE Transactions on Geoscience and Remote Sensing", True),
    ("Remote Sensing of Environment", "Remote Sensing", True),
    ("J. Hydrol.", "Hydrological Processes", False),
    ("Water Resour. Res.", "Water Research", False),
])
def test_journal_abbreviations(given, registry, same):
    from src.services.doi import same_venue
    assert same_venue(given, registry) is same


def test_get_doi(client, registry):
    r = client.get("/v1/doi/https://doi.org/10.24425/AGG.2023.146162", headers=H)
    body = r.json()
    assert r.status_code == 200 and body["doi"] == "10.24425/agg.2023.146162"
    assert body["authors"][0] == {"family": "Monti", "given": "Roberto", "orcid": "0009-0006-1608-6648"}
    assert body["oa_status"] == "diamond" and body["in_corpus"]["paper_id"] == "10.24425_agg.2023.146162"
    assert client.get("/v1/doi/10.9999/nothing", headers=H).json()["code"] == "NOT_FOUND"
    assert client.get("/v1/doi/not-a-doi", headers=H).status_code == 422
    registry.down = True
    r = client.get("/v1/doi/10.1000/new", headers=H)
    assert r.status_code == 504 and r.json()["code"] == "UPSTREAM_TIMEOUT"
