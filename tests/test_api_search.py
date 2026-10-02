"""Search endpoints with a fake Chroma collection, encoder and paper slice (no live stores)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.api.app import create_app
from src.contracts.identity import PaperIdentity
from src.services import identity_store, search

from tests.test_api import READ_KEY, StubKeys, StubManifest

H = {"X-API-Key": READ_KEY}
PAPERS = {f"p{i}": PaperIdentity(paper_id=f"p{i}", doi=f"10.1000/p{i}", title=f"Paper {i}", year=2015 + i,
                                 identity_status="ok") for i in range(1, 6)}
# p5 is in the slice but has no chunks; "dup" has chunks but is a duplicate (never in the slice)
COUNTS = {"p1": {"abstract": 1, "sentence": 40}, "p2": {"abstract": 1, "sentence": 30},
          "p3": {"sentence": 10, "table": 2}, "p4": {"abstract": 1}, "dup": {"abstract": 1}}
CHUNKS = [  # (chunk_id, paper_id, chunk_type, section, page, text, distance)
    ("c1", "p1", "sentence", "Methods", 3, "HAND methods do not preserve hydraulic connectivity", 0.10),
    ("c2", "p2", "sentence", "Introduction", 1, "Terrain-based inundation mapping with HAND", 0.20),
    ("c3", "p1", "abstract", "Abstract", None, "We evaluate HAND flood maps", 0.25),
    ("c4", "p3", "table", "Results", 5, "Table 2 CSI 0.53", 0.60),
    ("c5", "dup", "abstract", "Abstract", 1, "A duplicate copy", 0.05),
    ("c6", "p4", "abstract", "Abstract", None, "Unrelated text about snow", 0.90),
]


class FakeCollection:
    def __init__(self):
        self.queries = []

    def _allowed(self, where, row):
        if where is None:
            return True
        clauses = where.get("$and", [where])
        for c in clauses:
            (key, cond), = c.items()
            value = row[1] if key == "paper_id" else row[2]
            if "$in" in cond and value not in cond["$in"]:
                return False
            if "$nin" in cond and value in cond["$nin"]:
                return False
            if "$eq" in cond and value != cond["$eq"]:
                return False
        return True

    def query(self, query_embeddings, n_results, where, include):
        self.queries.append({"n_results": n_results, "where": where})
        rows = sorted((r for r in CHUNKS if self._allowed(where, r)), key=lambda r: r[6])[:n_results]
        return {"ids": [[r[0] for r in rows]], "documents": [[r[5] for r in rows]],
                "metadatas": [[{"paper_id": r[1], "chunk_type": r[2], "section_title": r[3], "page": r[4] or 0}
                               for r in rows]],
                "distances": [[r[6] for r in rows]]}

    def get(self, ids=None, where=None, limit=None, include=None):
        from src.document.chunker import LayoutAwareChunker
        if ids and ids[0] == LayoutAwareChunker._make_id("p1", "abstract"):
            return {"embeddings": [[0.1, 0.2, 0.3]]}
        if where and where.get("paper_id") == "p3":
            return {"embeddings": [[0.0, 1.0, 0.0], [1.0, 0.0, 0.0]]}
        return {"embeddings": []}


@pytest.fixture
def fake(monkeypatch):
    col = FakeCollection()

    def slice_papers(filters):
        ids = {p for p, ident in PAPERS.items()
               if (filters.get("year_from") is None or ident.year >= filters["year_from"])}
        if filters.get("paper_ids"):
            ids &= set(filters["paper_ids"])
        return ids

    monkeypatch.setattr(search, "slice_papers", slice_papers)
    monkeypatch.setattr(search, "chunk_counts", lambda: COUNTS)
    monkeypatch.setattr(search, "collection", lambda: col)
    monkeypatch.setattr(search, "embed", lambda text: [0.0, 0.0, 1.0])
    monkeypatch.setattr(identity_store, "papers_by_id", lambda ids: {i: PAPERS[i] for i in ids if i in PAPERS})
    monkeypatch.setattr(identity_store, "resolve", lambda **kw: identity_store.Resolved(
        "exact", 1.0, PAPERS[kw["paper_id"]], None) if kw.get("paper_id") in PAPERS else None)
    return col


@pytest.fixture
def client(fake):
    return TestClient(create_app(key_store=StubKeys(), manifest=StubManifest()))


def test_chunk_search_with_coverage_and_validity(client, fake):
    r = client.post("/v1/search/chunks", headers=H, json={"query": "HAND hydraulic connectivity", "k": 3})
    body = r.json()
    assert r.status_code == 200
    assert [(h["chunk_id"], h["score"], h["paper"]["paper_id"]) for h in body["hits"]] == [
        ("c1", 0.9, "p1"), ("c2", 0.8, "p2"), ("c3", 0.75, "p1")]
    assert body["hits"][2]["page"] is None                                 # 0 in the index means unknown
    assert body["coverage"]["papers_in_slice"] == 4 and body["coverage"]["papers_without_chunks"] == 1
    assert body["coverage"]["chunks_in_slice"] == 41 + 31 + 12 + 1
    assert body["retrieval_validity"] == "NOT_MEASURED" and "R-SCI-3" in body["validity_detail"]["reason"]
    assert body["provenance"]["collection"] and body["provenance"]["embedding_model"]
    # a large slice runs unfiltered (a Chroma $nin costs seconds) and is filtered afterwards:
    # the duplicate's chunk is the closest of all and must not come back
    assert fake.queries[-1]["where"] is None and fake.queries[-1]["n_results"] > 3
    assert "c5" not in [h["chunk_id"] for h in body["hits"]]


def test_filters_reach_the_index(client, fake):
    body = client.post("/v1/search/chunks", headers=H, json={
        "query": "HAND", "k": 5, "filters": {"year_from": 2018, "chunk_types": ["abstract", "table"]}}).json()
    where = fake.queries[-1]["where"]
    assert where == {"$and": [{"paper_id": {"$in": ["p3", "p4"]}}, {"chunk_type": {"$in": ["abstract", "table"]}}]}
    assert [h["chunk_id"] for h in body["hits"]] == ["c4", "c6"]
    assert body["coverage"]["chunks_in_slice"] == 2 + 1


def test_plan_prefilters_only_small_slices():
    counts = {"a": {"sentence": 90}, "b": {"sentence": 5}, "c": {"abstract": 5}}
    big = search.plan({"a", "b"}, None, 10, counts)
    assert big.where is None and big.postfilter and big.n_results == int(10 / 0.95 * 1.5) + 20
    small = search.plan({"b", "c"}, ["abstract"], 10, counts)
    assert small.where == {"$and": [{"paper_id": {"$in": ["b", "c"]}}, {"chunk_type": {"$in": ["abstract"]}}]}
    assert not small.postfilter and small.n_results == 10


def test_section_filter_and_min_score(client, fake):
    body = client.post("/v1/search/chunks", headers=H, json={
        "query": "HAND", "k": 2, "filters": {"sections": ["intro"]}, "min_score": 0.5}).json()
    assert [h["chunk_id"] for h in body["hits"]] == ["c2"]
    assert fake.queries[-1]["n_results"] == 2 * search.SECTION_OVERFETCH and "section filter" in body["coverage"]["note"]


def test_validation(client):
    assert client.post("/v1/search/chunks", headers=H, json={"query": "ab"}).status_code == 422
    assert client.post("/v1/search/chunks", headers=H, json={"query": "abc", "k": 500}).status_code == 422
    r = client.post("/v1/search/chunks", headers=H, json={"query": "abc", "filters": {"identity_status": ["duplicate"]}})
    assert r.status_code == 422


def test_paper_search_aggregates_queries(client):
    body = client.post("/v1/search/papers", headers=H, json={
        "queries": ["HAND connectivity", "terrain inundation mapping"], "k": 3, "max_candidates": 10}).json()
    assert [(p["paper_id"], p["score"], p["queries_matched"]) for p in body["papers"]] == [
        ("p1", 0.9, 2), ("p2", 0.8, 2), ("p3", 0.4, 2)]
    assert len(body["papers"][0]["best_chunks"]) == 3 or body["papers"][0]["hits"] >= 2
    assert client.post("/v1/search/papers", headers=H, json={"queries": ["ok query", "x"]}).status_code == 422


def test_similar_papers_exclude_the_seed(client, fake):
    body = client.post("/v1/search/similar", headers=H, json={"paper_id": "p1", "k": 3}).json()
    assert "p1" not in [p["paper_id"] for p in body["papers"]]
    assert fake.queries[-1]["where"] is None                     # large slice: filtered after the query
    assert not {"dup", "p1"} & {p["paper_id"] for p in body["papers"]}
    seedless = client.post("/v1/search/similar", headers=H, json={"paper_id": "p2"})
    assert seedless.status_code == 424 and seedless.json()["code"] == "SOURCE_UNAVAILABLE"
    assert client.post("/v1/search/similar", headers=H, json={"paper_id": "nope"}).status_code == 404
    assert client.post("/v1/search/similar", headers=H, json={}).status_code == 422
