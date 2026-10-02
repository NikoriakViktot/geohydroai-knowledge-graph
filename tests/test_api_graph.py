"""Graph endpoints with a fake Neo4j: queries are dispatched on their '// name' marker.

No live Neo4j (rule: no live stores in unit tests). The fake also records the parameters,
so the tests check that user input reaches Cypher only as parameters.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.api.app import create_app
from src.api.deps import Principal, hash_key
from src.services import graph_read

from tests.test_api import READ_KEY, StubManifest

H, ADMIN = {"X-API-Key": READ_KEY}, {"X-API-Key": "test-admin-key"}

PAPER = {"paper_id": "10.5194_nhess-19-2405-2019", "doi": "10.5194/nhess-19-2405-2019", "title": "NWM-HAND",
         "year": 2019, "venue": float("nan"), "identity_status": "ok", "is_reference_stub": False,
         "cited_by_count": 170, "openalex_id": "W1", "study_type": "case_study", "primary_country": "USA"}
EDGE = {"canonical_id": "method.hand", "display_name": "HAND", "family": "Terrain Analysis", "confidence": 1.0,
        "role": "used", "surface_form": "HAND", "evidence": ["The HAND method maps flooding."], "page": 1,
        "section": "Abstract"}
SPURIOUS = dict(EDGE, canonical_id="method.iric", surface_form="iRIC", evidence=["an empirical relation"])


class Keys:
    def lookup(self, h):
        return {hash_key(READ_KEY): Principal("t", frozenset({"read"}), "k1"),
                hash_key("test-admin-key"): Principal("t", frozenset({"read", "admin"}), "k2")}.get(h)


class FakeNeo4j:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self.explained = "r"

    def answer(self, name: str, params: dict) -> list[dict]:
        found = params.get("doi") == PAPER["doi"] or params.get("paper_id") == PAPER["paper_id"]
        if name in ("paper",):
            return [PAPER] if found else []
        if name == "methods":
            return [EDGE, SPURIOUS]
        if name in ("sensors", "metrics", "topics", "authors", "facts"):
            return []
        if name == "countries":
            return [{"countries": ["USA"], "events": [None]}]
        if name == "counts":
            return [{"cites_out": 34, "cites_corpus": 3, "cited_in_corpus": 10}]
        if name == "cites_counts":
            return [{"out": 3, "out_corpus": 1, "in": 0, "in_corpus": 2}]
        if name == "cites_out":
            rows = [dict(PAPER, paper_id=f"r{i}", title=f"ref {i}", direction="out") for i in range(3)]
            for r in rows:
                r.pop("study_type"), r.pop("primary_country")
            return rows[params["skip"]:params["skip"] + params["limit"]]
        if name == "cites_in":
            rows = [dict(PAPER, paper_id=f"c{i}", title=f"citing {i}", direction="in") for i in range(2)]
            for r in rows:
                r.pop("study_type"), r.pop("primary_country")
            return rows[params["skip"]:params["skip"] + params["limit"]]
        if name.startswith("entity_papers"):
            item = {k: PAPER[k] for k in ("paper_id", "doi", "title", "year", "venue", "identity_status",
                                          "cited_by_count", "openalex_id")}
            items = [dict(item, is_reference_stub=False, confidence=1.0, role="used", surface_form="iRIC",
                          evidence=["an empirical relation"], page=3, score=None)]
            return [{"total": 1, "items": items}]
        if name == "corpus_size":
            return [{"n": 4806}]
        if name == "top_methods":
            return [{"canonical_id": "method.hand", "display_name": "HAND", "family": "T", "papers": 504}]
        return []

    def run_read(self, query: str, params=None, limit=1000):
        name = query.split("\n", 1)[0].removeprefix("// ").strip()
        self.calls.append((query, dict(params or {})))
        rows = self.answer(name, params or {})
        columns = list(rows[0].keys()) if rows else []
        values = [[graph_read._plain(v) for v in r.values()] for r in rows]
        return columns, values[:limit], len(values) > limit


@pytest.fixture
def neo(monkeypatch):
    fake = FakeNeo4j()
    monkeypatch.setattr(graph_read, "run_read", fake.run_read)
    monkeypatch.setattr(graph_read, "explain_type", lambda query, params: fake.explained)
    return fake


@pytest.fixture
def client(neo):
    return TestClient(create_app(key_store=Keys(), manifest=StubManifest()))


def test_paper_neighbourhood_flags_unsupported_edges(client, neo):
    r = client.get("/v1/graph/papers/https://doi.org/10.5194/NHESS-19-2405-2019", headers=H)
    body = r.json()
    assert r.status_code == 200 and body["paper"]["venue"] is None          # NaN from parquet → null
    assert [(m["canonical_id"], m["mention_in_evidence"]) for m in body["methods"]] == [
        ("method.hand", True), ("method.iric", False)]
    assert body["countries"] == ["USA"] and body["flood_events"] == [] and body["facts"] is None
    assert body["counts"] == {"cites_out": 34, "cites_corpus": 3, "cited_in_corpus": 10}
    # the DOI reached Cypher as a parameter, normalised
    assert all(p.get("doi") == "10.5194/nhess-19-2405-2019" for _, p in neo.calls)
    assert all("nhess" not in q for q, _ in neo.calls)


def test_paper_errors(client):
    assert client.get("/v1/graph/papers/unknown", headers=H).status_code == 404
    assert client.get("/v1/graph/papers/10.5194/nhess-19-2405-2019", headers=H,
                      params={"include": "authors,bogus"}).status_code == 400


def test_citations_page_through_both_directions(client):
    first = client.get("/v1/graph/papers/10.5194/nhess-19-2405-2019/citations", headers=H,
                       params={"direction": "both", "limit": 4}).json()
    assert [(i["direction"], i["paper_id"]) for i in first["items"]] == [("out", "r0"), ("out", "r1"), ("out", "r2"),
                                                                       ("in", "c0")]
    assert first["counts"] == {"out": 3, "out_in_corpus": 1, "in": 2}
    second = client.get("/v1/graph/papers/10.5194/nhess-19-2405-2019/citations", headers=H,
                        params={"direction": "both", "limit": 4, "cursor": first["next_cursor"]}).json()
    assert [i["paper_id"] for i in second["items"]] == ["c1"] and second["next_cursor"] is None
    assert client.get("/v1/graph/papers/x/citations", headers=H, params={"cursor": "garbage!"}).status_code == 422


def test_entity_papers(client, neo):
    body = client.get("/v1/graph/entities/Method/method.iric/papers", headers=H, params={"role": "used"}).json()
    assert body["count"] == 1 and body["coverage"] == {"corpus_papers_in_graph": 4806}
    assert body["items"][0]["mention_in_evidence"] is False
    query, params = next(c for c in neo.calls if c[0].startswith("// entity_papers:Method"))
    assert params["id"] == "method.iric" and params["role"] == "used" and "iric" not in query
    assert client.get("/v1/graph/entities/Weather/x/papers", headers=H).status_code == 422


def test_named_queries_validate_their_parameters(client, neo):
    names = [q["name"] for q in client.get("/v1/graph/queries", headers=H).json()["queries"]]
    assert "citation_lineage" in names and "top_methods" in names
    ok = client.post("/v1/graph/queries/top_methods", headers=H, json={"params": {"role": "used"}, "limit": 1})
    assert ok.status_code == 200 and ok.json()["columns"] == ["canonical_id", "display_name", "family", "papers"]
    for params, loc in (({"hops": 5, "canonical_id": "method.hand"}, "hops"), ({"canonical_id": 7}, "canonical_id"),
                        ({"canonical_id": "m", "evil": 1}, "evil"), ({}, "canonical_id")):
        r = client.post("/v1/graph/queries/citation_lineage", headers=H, json={"params": params})
        assert r.status_code == 422 and r.json()["errors"][0]["loc"][-1] == loc
    assert client.post("/v1/graph/queries/coauthor_network", headers=H, json={"params": {}}).status_code == 422
    assert client.post("/v1/graph/queries/nope", headers=H, json={}).status_code == 404


def test_citation_lineage_has_one_static_text_per_hop_count(client, neo):
    client.post("/v1/graph/queries/citation_lineage", headers=H,
                json={"params": {"canonical_id": "method.hand", "hops": 3, "direction": "in"}})
    query, params = neo.calls[-1]
    assert "[:CITES*1..3]" in query and "(other:Paper)-[:CITES*1..3]->(seed)" in query
    assert params["canonical_id"] == "method.hand"


def test_cypher_is_admin_only_and_read_only(client, neo):
    q = {"query": "MATCH (p:Paper) RETURN count(p) AS n"}
    assert client.post("/v1/graph/cypher", headers=H, json=q).status_code == 403
    assert client.post("/v1/graph/cypher", headers=ADMIN, json=q).status_code == 200
    neo.explained = "rw"
    r = client.post("/v1/graph/cypher", headers=ADMIN, json={"query": "MATCH (p) SET p.x = 1"})
    assert r.status_code == 422 and "'rw'" in r.json()["detail"]


@pytest.mark.parametrize("surface, evidence, expected", [
    ("HAND", ["The HAND method"], True),
    ("HAND", ["on the other hand"], False),
    ("HAND", ["a HAND-based map"], True),
    ("iRIC", ["an empirical relation"], False),
    ("iRIC", ["we used iRIC (Nays2D)"], True),
    ("HEC-HMS", ["HEC-HMS was run"], True),
    ("SAR", [], None),
])
def test_mention_in_evidence(surface, evidence, expected):
    assert graph_read.mention_in_evidence(surface, evidence) is expected
