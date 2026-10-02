"""MCP adapter at /mcp: key check, protocol round trips, tools = implemented REST endpoints.

No live stores: the same stubs as tests/test_api_evidence.py (identity, TEI location, registry).
The lifespan runs (``with TestClient``), because the MCP transport needs its task group.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from src.api import mcp as mcp_adapter
from src.api.app import _implemented, create_app
from src.api.docs_loader import DOCS_DIR, load_endpoint_docs
from src.contracts.research import SEED_PROJECT_IDS
from src.services import fulltext, identity_store, projects, thesis_validation

from tests.test_api import NOSCOPE_KEY, READ_KEY, StubKeys, StubManifest
from tests.test_api_evidence import NO_TEI, PAPER, TEI

ACCEPT = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
INIT = {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "pytest", "version": "0"}}


@pytest.fixture
def mcp_client(monkeypatch, tmp_path):
    tei = tmp_path / "iqbal_2023.tei.xml"
    tei.write_text(TEI, encoding="utf-8")

    def fake_resolve(*, doi=None, paper_id=None, file=None, openalex_id=None, title=None, year=None,
                     include=frozenset()):
        for p in (PAPER, NO_TEI):
            if (doi and doi.lower() == p.doi) or paper_id == p.paper_id or file == p.paper_id:
                return identity_store.Resolved("exact", 1.0, p, None)
        return None

    monkeypatch.setattr(identity_store, "resolve", fake_resolve)
    monkeypatch.setattr(identity_store, "match_references", lambda refs: [(None, None) for _ in refs])
    monkeypatch.setattr(fulltext, "tei_path", lambda paper_id: tei if paper_id == "iqbal_2023" else None)
    monkeypatch.setattr(thesis_validation, "project_theses", lambda project_id: ({"T1"}, None))
    monkeypatch.setattr(projects, "registered", lambda project_id: project_id in SEED_PROJECT_IDS)
    app = create_app(key_store=StubKeys(), manifest=StubManifest(), mcp_allowed_hosts=["testserver"])
    with TestClient(app) as client:
        yield client


def rpc(client, method, params=None, key=READ_KEY, headers=None, id_=1):
    msg = {"jsonrpc": "2.0", "id": id_, "method": method}
    if params is not None:
        msg["params"] = params
    h = {**ACCEPT, "MCP-Protocol-Version": "2025-06-18", **(headers or {})}
    if key:
        h["X-API-Key"] = key
    return client.post("/mcp", json=msg, headers=h)


def call_tool(client, name, arguments, key=READ_KEY):
    r = rpc(client, "tools/call", {"name": name, "arguments": arguments}, key=key)
    assert r.status_code == 200, r.text
    return r.json()["result"]


def test_mcp_refuses_requests_without_a_known_key(mcp_client):
    r = rpc(mcp_client, "initialize", INIT, key=None)
    assert r.status_code == 401 and r.headers["content-type"].startswith("application/problem+json")
    assert r.json()["code"] == "UNAUTHENTICATED" and "X-API-Key" in r.headers["www-authenticate"]
    assert rpc(mcp_client, "initialize", INIT, key="not-a-key").status_code == 401
    bearer = rpc(mcp_client, "initialize", INIT, key=None, headers={"Authorization": f"Bearer {READ_KEY}"})
    assert bearer.status_code == 200


def test_initialize_carries_the_agent_rules(mcp_client):
    r = rpc(mcp_client, "initialize", INIT)
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/json")
    result = r.json()["result"]
    assert result["serverInfo"]["name"] == "ghai"
    assert "ghai://docs/AGENT_RULES" in result["instructions"]
    assert {"tools", "resources", "prompts"} <= set(result["capabilities"])


def test_tools_are_exactly_the_implemented_endpoints(mcp_client):
    tools = rpc(mcp_client, "tools/list").json()["result"]["tools"]
    names = {t["name"] for t in tools}
    assert names == set(mcp_adapter.TOOL_ENDPOINTS)
    routes = _implemented(mcp_client.app)            # includes the 501 stubs of planned endpoints
    documented = load_endpoint_docs()
    for name, endpoint in mcp_adapter.TOOL_ENDPOINTS.items():
        assert endpoint in routes, f"{name} -> {endpoint} has no route"
        assert any(d.status == "implemented" for d in documented.get(endpoint, [])), \
            f"{name} -> {endpoint} is not documented as implemented"
    assert all(t["annotations"]["readOnlyHint"] for t in tools)
    page = (DOCS_DIR / "MCP_TOOLS.md").read_text(encoding="utf-8")
    missing = [n for n in names if f"`{n}`" not in page]
    assert not missing, f"tools missing from docs/api/MCP_TOOLS.md: {missing}"


def test_tool_call_is_the_rest_call_with_the_callers_key(mcp_client):
    item = {"key": "Iqbal_2023", "source": "10.1111/jfr3.12937",
            "quote": "A mean absolute vertical error of 1.12-1.61 m was found for the FABDEM in built-up areas"}
    result = call_tool(mcp_client, "verify_quotes", {"items": [item]})
    assert not result.get("isError")
    body = result["structuredContent"]
    assert body["items"][0]["status"] == "FOUND_EXACT"
    assert body["provenance"]["corpus_manifest_id"] == "f" * 64
    assert json.loads(result["content"][0]["text"])["items"][0]["key"] == "Iqbal_2023"
    # the key travels with the call: a key without `read` meets the REST scope check
    denied = call_tool(mcp_client, "verify_quotes", {"items": [item]}, key=NOSCOPE_KEY)
    assert denied["isError"] and json.loads(denied["content"][0]["text"].split(": ", 1)[1])["code"] == "FORBIDDEN_SCOPE"


def test_rest_problems_become_tool_errors_with_their_code(mcp_client):
    result = call_tool(mcp_client, "validate_theses", {"kind": "theses", "project_id": "paper9", "document": []})
    assert result["isError"]
    problem = json.loads(result["content"][0]["text"].split(": ", 1)[1])
    assert problem["code"] == "UNKNOWN_PROJECT" and problem["status"] == 422
    sections = call_tool(mcp_client, "get_paper_sections", {"paper_id": "no_tei"})
    assert sections["isError"] and "SOURCE_UNAVAILABLE" in sections["content"][0]["text"]


def test_documentation_resources_and_workflow_prompts(mcp_client):
    uris = {r["uri"] for r in rpc(mcp_client, "resources/list").json()["result"]["resources"]}
    assert {"ghai://docs/AGENT_RULES", "ghai://docs/MCP_TOOLS", "ghai://docs/endpoints/search"} <= uris
    page = rpc(mcp_client, "resources/read", {"uri": "ghai://docs/AGENT_RULES"}).json()["result"]["contents"][0]
    assert page["mimeType"] == "text/markdown" and "R-SCI-1" in page["text"]
    prompts = {p["name"] for p in rpc(mcp_client, "prompts/list").json()["result"]["prompts"]}
    assert {"w1", "w2", "w5"} <= prompts
    w1 = rpc(mcp_client, "prompts/get", {"name": "w1", "arguments": {"project_id": "floodstate-eo:paper3"}}).json()
    text = w1["result"]["messages"][0]["content"]["text"]
    assert "POST /manuscripts/citations" in text and "floodstate-eo:paper3" in text


def test_dns_rebinding_is_refused(mcp_client):
    r = rpc(mcp_client, "initialize", INIT, headers={"Host": "attacker.example"})
    assert r.status_code == 421
