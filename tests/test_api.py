"""Knowledge API: documentation, agent rules, auth, errors and identity endpoints.

No live stores: the key store, the manifest and the identity service are stubbed
(see the 'no live stores in unit tests' rule).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.api import docs_loader
from src.api.app import create_app
from src.api.deps import Principal, hash_key
from src.contracts.identity import PaperIdentity
from src.services import fulltext, graph_read, identity_store, metrics, thesis_validation

READ_KEY, NOSCOPE_KEY = "test-read-key", "test-noscope-key"


class StubKeys:
    def __init__(self):
        self.keys = {hash_key(READ_KEY): Principal("tests", frozenset({"read"}), "k1"),
                     hash_key(NOSCOPE_KEY): Principal("tests", frozenset({"llm"}), "k2")}

    def lookup(self, key_hash):
        return self.keys.get(key_hash)


class StubManifest:
    def get(self):
        return {"corpus_manifest_id": "f" * 64, "frozen": False, "papers": 3, "identity_run_id": "run-1",
                "retrieval_rules_version": "1.2.0", "created_at": None}


PAPER = PaperIdentity(paper_id="10.1029_2025gl120832", doi="10.1029/2025gl120832", title="SWOT observations",
                      year=2026, identity_status="ok")
COPY = PaperIdentity(paper_id="copy", doi="10.1029/2025gl120832", title="SWOT observations", year=2026,
                     identity_status="duplicate", duplicate_of="10.1029_2025gl120832")


def fake_resolve(*, doi=None, paper_id=None, file=None, openalex_id=None, title=None, year=None, include=frozenset()):
    if doi == "not a doi":
        raise identity_store.InvalidSelector("not a DOI: 'not a doi'")
    if doi and "2025gl120832" in doi.lower():
        return identity_store.Resolved("alias", 1.0, PAPER, None)
    if paper_id == "copy":
        return identity_store.Resolved("exact", 1.0, COPY, PAPER)
    return None


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(identity_store, "resolve", fake_resolve)
    monkeypatch.setattr(fulltext, "tei_path", lambda paper_id: None)
    monkeypatch.setattr(thesis_validation, "project_theses", lambda project_id: (None, "not checked in tests"))
    monkeypatch.setattr(graph_read, "run_read", lambda query, params=None, limit=1000: ([], [], False))
    monkeypatch.setattr(graph_read, "explain_type", lambda query, params: "r")
    monkeypatch.setattr(metrics, "query_facts", lambda **kw: {"items": [], "total": 0, "summary": {}, "coverage": {}})
    monkeypatch.setattr(identity_store, "papers_by_id", lambda ids: {})
    return TestClient(create_app(key_store=StubKeys(), manifest=StubManifest()))


H = {"X-API-Key": READ_KEY}


# ── documentation and rules are part of the API ────────────────────────────────

def test_every_documented_endpoint_is_in_openapi_with_its_rules(client):
    spec = client.get("/v1/openapi.json").json()
    ops = {(m.upper(), p): op for p, v in spec["paths"].items() for m, op in v.items()}
    baseline = set(docs_loader.load_group_rules()["all"])
    for (method, path), variants in docs_loader.load_endpoint_docs().items():
        route = "/v1" + path
        op = ops.get((method, route)) or ops.get((method, route.replace("{name}", "{name}")))
        assert op is not None, f"{method} {path} missing from OpenAPI"
        assert "Agent rules for this endpoint" in op["description"], f"{method} {path} has no rules"
        assert baseline <= set(op["x-agent-rules"]), f"{method} {path} lacks baseline rules"
        assert op["x-status"] in ("implemented", "planned")


def test_docs_status_matches_the_implementation(client):
    """A route that answers for real is documented as implemented, and vice versa."""
    for (method, path), variants in docs_loader.load_endpoint_docs().items():
        main = next((d for d in variants if d.variant is None), variants[0])
        probe = path.replace("{name}", "PaperIdentity.v1").replace("{paper_id}", "copy")
        probe = __import__("re").sub(r"\{[^}]+\}", "x", probe)
        r = client.request(method, "/v1" + probe if path != "/llms.txt" else "/llms.txt", headers=H, json={})
        is_stub = r.status_code == 501 and r.json().get("code") == "NOT_IMPLEMENTED"
        assert is_stub == (main.status == "planned"), f"{method} {path}: docs say {main.status}, got {r.status_code}"


def test_agent_rules_endpoint(client):
    body = client.get("/v1/agent-rules").json()
    ids = {r["id"] for r in body["rules"]}
    assert {"R-SCI-1", "R-SCI-3", "R-ACC-1", "R-DATA-3", "R-SEC-1"} <= ids
    assert "Quote, don't recall" in body["top_rules"]
    md = client.get("/v1/agent-rules", params={"format": "markdown"})
    assert md.headers["content-type"].startswith("text/markdown") and "R-SCI-3" in md.text


def test_every_response_points_at_the_rules(client):
    for r in (client.get("/v1/capabilities"), client.get("/v1/papers/resolve"), client.post("/v1/search/chunks", json={})):
        assert 'rel="agent-rules"' in r.headers["link"]
        assert r.headers["x-request-id"]


def test_endpoint_doc_carries_full_rule_text(client):
    body = client.get("/v1/docs/endpoint", params={"method": "POST", "path": "/quotes/verify"}).json()
    rules = {r["id"]: r for r in body["rules"]}
    assert "R-SCI-4" in rules and "original" in rules["R-SCI-4"]["text"].lower()
    assert client.get("/v1/docs/endpoint", params={"method": "GET", "path": "/nope"}).status_code == 404


def test_docs_index_and_pages(client):
    idx = client.get("/v1/docs/index").json()
    assert idx["counts"]["implemented"] >= 10 and "AGENT_RULES" in idx["pages"]
    assert "Rules for AI agents" in client.get("/v1/docs/pages/AGENT_RULES").text
    assert client.get("/v1/docs/pages/endpoints/search").status_code == 200
    assert client.get("/v1/docs/pages/../../etc").status_code == 404
    assert "GeoHydroAI Knowledge API" in client.get("/llms.txt").text


# ── errors and auth ────────────────────────────────────────────────────────────

def test_planned_endpoint_answers_501_with_docs_pointer(client):
    r = client.post("/v1/claims/check", json={}, headers=H)
    assert r.status_code == 501 and r.headers["content-type"].startswith("application/problem+json")
    assert r.json()["docs"] == "/v1/docs/endpoint?method=POST&path=/claims/check"


def test_auth_and_scopes(client):
    assert client.get("/v1/papers/resolve", params={"doi": "10.1029/2025gl120832"}).json()["code"] == "UNAUTHENTICATED"
    r = client.get("/v1/papers/resolve", params={"doi": "10.1029/2025gl120832"}, headers={"X-API-Key": NOSCOPE_KEY})
    assert r.status_code == 403 and r.json()["code"] == "FORBIDDEN_SCOPE"
    assert client.get("/v1/papers/resolve", headers={"X-API-Key": "wrong"}).status_code == 401


# ── identity endpoints ─────────────────────────────────────────────────────────

def test_resolve_found_with_provenance(client):
    r = client.get("/v1/papers/resolve", params={"doi": "https://doi.org/10.1029/2025GL120832"}, headers=H)
    body = r.json()
    assert r.status_code == 200 and body["paper"]["paper_id"] == "10.1029_2025gl120832"
    assert body["provenance"]["corpus_manifest_id"] == "f" * 64


def test_resolve_errors(client):
    assert client.get("/v1/papers/resolve", params={"doi": "10.9999/none"}, headers=H).json()["code"] == "NOT_IN_CORPUS"
    assert client.get("/v1/papers/resolve", params={"doi": "not a doi"}, headers=H).json()["code"] == "INVALID_DOI"
    assert client.get("/v1/papers/resolve", params={"include": "bogus", "doi": "10.1/x"}, headers=H).status_code == 400


def test_duplicate_points_at_canonical(client):
    body = client.get("/v1/papers/copy", headers=H).json()
    assert body["identity_status"] == "duplicate" and body["canonical"]["paper_id"] == "10.1029_2025gl120832"


def test_resolve_batch(client):
    r = client.post("/v1/papers/resolve-batch", headers=H, json={"items": [
        {"key": "Lehnigk_2026", "doi": "10.1029/2025gl120832"}, {"key": "x", "doi": "10.9999/none"},
        {"key": "bad"}, {"key": "nd", "doi": "not a doi"}]})
    assert r.json()["summary"] == {"found": 1, "not_in_corpus": 1, "invalid": 2}


def test_batch_contract_is_enforced(client):
    r = client.post("/v1/papers/resolve-batch", headers=H, json={"items": [], "extra": 1})
    assert r.status_code == 422 and r.json()["code"] == "VALIDATION_FAILED" and r.json()["errors"]
