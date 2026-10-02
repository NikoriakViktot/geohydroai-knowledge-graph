"""Semantic search over the Chroma projection (docs/api/endpoints/search.md).

* the query is embedded with the same encoder as the documents (SPECTER2 base, pinned
  revision, mean pooling), loaded lazily on the first search;
* filters on year, DOI, cohort and identity resolve to a set of paper ids in Postgres;
  Chroma stores only chunk metadata, so the set goes in as `paper_id $in` (or the shorter
  `$nin` of the papers left out);
* coverage reports the papers and chunks actually searched, from Chroma's own catalogue;
* retrieval validity is NOT_MEASURED until a recall measurement exists for
  (collection, corpus manifest): an empty result means only "this search found nothing".
"""

from __future__ import annotations

import os
import sqlite3
import threading
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

DEFAULT_IDENTITY = ("ok", "no_doi", "title_doi_mismatch")
CHUNK_TYPES = ("abstract", "sentence", "paragraph", "section", "figure", "table", "formula")
SECTION_OVERFETCH = 5
_LOCK = threading.Lock()
_STATE: dict = {}


def chroma_dir() -> Path:
    from src.config import CHROMA_DIR
    return Path(os.getenv("CHROMA_DIR", str(CHROMA_DIR)))


def collection_name() -> str:
    from src.api.deps import COLLECTION
    return COLLECTION


def encoder():
    """The query encoder (sentence-transformers, CPU), same model and revision as the index."""
    with _LOCK:
        if "encoder" not in _STATE:
            from sentence_transformers import SentenceTransformer

            from src.api.deps import EMBEDDING_MODEL
            name, _, revision = EMBEDDING_MODEL.partition("@")
            _STATE["encoder"] = SentenceTransformer(name, revision=full_revision(name, revision), device="cpu")
        return _STATE["encoder"]


def full_revision(name: str, revision: str | None) -> str | None:
    """A short commit hash (as in EMBEDDING_MODEL_ID) → the full one from the local HF cache;
    the hub accepts only full hashes. Unknown prefixes are returned unchanged."""
    if not revision or len(revision) == 40:
        return revision or None
    from huggingface_hub.constants import HF_HUB_CACHE
    snapshots = Path(HF_HUB_CACHE) / f"models--{name.replace('/', '--')}" / "snapshots"
    matches = [p.name for p in snapshots.glob(f"{revision}*")] if snapshots.is_dir() else []
    return matches[0] if len(matches) == 1 else revision


def collection():
    """The Chroma collection, opened read-only in use (never created here)."""
    with _LOCK:
        key = ("collection", str(chroma_dir()), collection_name())
        if key not in _STATE:
            import chromadb
            from chromadb.config import Settings
            client = chromadb.PersistentClient(path=str(chroma_dir()), settings=Settings(anonymized_telemetry=False))
            _STATE[key] = client.get_collection(collection_name())
        return _STATE[key]


def embed(text: str) -> list[float]:
    return encoder().encode([text], convert_to_numpy=True, show_progress_bar=False)[0].tolist()


def chunk_counts() -> dict[str, dict[str, int]]:
    """paper_id → chunk_type → number of chunks, from the collection's SQLite catalogue (cached)."""
    key = ("counts", str(chroma_dir()), collection_name())
    with _LOCK:
        if key in _STATE:
            return _STATE[key]
    db = chroma_dir() / "chroma.sqlite3"
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        cid = con.execute("SELECT id FROM collections WHERE name = ?", (collection_name(),)).fetchone()
        if cid is None:
            raise LookupError(f"collection {collection_name()!r} not found in {chroma_dir()}")
        rows = con.execute("""
            SELECT p.string_value, t.string_value, count(*)
            FROM embeddings e JOIN segments s ON e.segment_id = s.id AND s.scope = 'METADATA'
            JOIN embedding_metadata p ON p.id = e.id AND p.key = 'paper_id'
            JOIN embedding_metadata t ON t.id = e.id AND t.key = 'chunk_type'
            WHERE s.collection = ? GROUP BY 1, 2""", (cid[0],)).fetchall()
    finally:
        con.close()
    counts: dict[str, dict[str, int]] = defaultdict(dict)
    for pid, ctype, n in rows:
        counts[pid][ctype] = n
    with _LOCK:
        _STATE[key] = dict(counts)
    return _STATE[key]


def slice_papers(filters: dict) -> set[str]:
    """Paper ids allowed by the filters (identity, years, explicit ids/DOIs, excluded cohorts)."""
    from sqlalchemy import select

    from src.db.engine import session_scope
    from src.db.models import CohortMember, Paper, PaperAlias
    from src.services.identity import normalize_doi
    statuses = filters.get("identity_status") or list(DEFAULT_IDENTITY)
    q = select(Paper.paper_id).where(Paper.identity_status.in_(statuses))
    if filters.get("year_from") is not None:
        q = q.where(Paper.year >= filters["year_from"])
    if filters.get("year_to") is not None:
        q = q.where(Paper.year <= filters["year_to"])
    with session_scope() as s:
        allowed = set(s.scalars(q))
        wanted: set[str] | None = None
        if filters.get("paper_ids"):
            wanted = set(filters["paper_ids"])
        if filters.get("dois"):
            dois = {d for d in (normalize_doi(x) for x in filters["dois"]) if d}
            by_doi = set(s.scalars(select(PaperAlias.paper_id).where(PaperAlias.alias_type == "doi",
                                                                     PaperAlias.alias.in_(dois))))
            wanted = by_doi if wanted is None else wanted | by_doi
        if wanted is not None:
            allowed &= wanted
        if filters.get("exclude_cohorts"):
            allowed -= set(s.scalars(select(CohortMember.paper_id)
                                     .where(CohortMember.cohort.in_(filters["exclude_cohorts"]))))
    return allowed


#: Below this share of the index, the slice goes into Chroma as a filter; above it, the query
#: runs unfiltered with more candidates and the hits are filtered here. Chroma evaluates a
#: metadata filter row by row in SQLite: a $nin over 218 papers took 4.5 s on 1.4 M chunks,
#: the unfiltered query 0.02 s (2026-10-02).
PREFILTER_BELOW = 0.25
MAX_CANDIDATES = 2000


@dataclass(frozen=True)
class Plan:
    where: dict | None
    n_results: int
    allowed: frozenset[str]
    types: frozenset[str] | None
    postfilter: bool


def plan(allowed: set[str], types: list[str] | None, k: int, counts: dict[str, dict[str, int]]) -> Plan:
    total = sum(sum(c.values()) for c in counts.values()) or 1
    in_slice = sum(n for p in allowed if p in counts for t, n in counts[p].items() if not types or t in types)
    share = in_slice / total
    tset = frozenset(types) if types and set(types) != set(CHUNK_TYPES) else None
    if share >= PREFILTER_BELOW:
        n = min(MAX_CANDIDATES, int(k / max(share, 0.01) * 1.5) + 20)
        return Plan(None, n, frozenset(allowed), tset, True)
    clauses = [{"paper_id": {"$in": sorted(p for p in allowed if p in counts) or ["__none__"]}}]
    if tset:
        clauses.append({"chunk_type": {"$in": sorted(tset)}})
    return Plan(clauses[0] if len(clauses) == 1 else {"$and": clauses}, k, frozenset(allowed), tset, False)


def _where(allowed: set[str], indexed: set[str], types: list[str] | None) -> dict | None:
    """The Chroma filter for a slice (used when the slice is small; see `plan`)."""
    clauses = []
    in_slice = allowed & indexed
    left_out = indexed - allowed
    if len(left_out) < len(in_slice):
        if left_out:
            clauses.append({"paper_id": {"$nin": sorted(left_out)}})
    else:
        clauses.append({"paper_id": {"$in": sorted(in_slice) or ["__none__"]}})
    if types and set(types) != set(CHUNK_TYPES):
        clauses.append({"chunk_type": {"$in": list(types)}})
    if not clauses:
        return None
    return clauses[0] if len(clauses) == 1 else {"$and": clauses}


def run_query(vector: list[float], pl: Plan) -> dict:
    """Query Chroma by the plan; with post-filtering, drop hits outside the slice and retry with
    the filter if too few remain."""
    result = collection().query(query_embeddings=[vector], n_results=pl.n_results, where=pl.where,
                                include=["documents", "metadatas", "distances"])
    if not pl.postfilter:
        return result
    keep = [i for i, m in enumerate(result["metadatas"][0])
            if (m or {}).get("paper_id") in pl.allowed and (pl.types is None or (m or {}).get("chunk_type") in pl.types)]
    return {key: [[result[key][0][i] for i in keep]] for key in ("ids", "documents", "metadatas", "distances")}


def coverage(allowed: set[str], types: list[str] | None, filters: dict) -> dict:
    counts = chunk_counts()
    papers = [p for p in allowed if p in counts]
    chunks = sum(n for p in papers for t, n in counts[p].items() if not types or t in types)
    return {"papers_in_slice": len(papers), "chunks_in_slice": chunks,
            "papers_without_chunks": len(allowed) - len(papers),
            "filters_applied": {k: v for k, v in filters.items() if v not in (None, [], {})},
            "excluded_cohorts": list(filters.get("exclude_cohorts") or [])}


def _hits(result: dict, sections: list[str] | None, min_score: float | None) -> list[dict]:
    out = []
    ids, docs, metas, dists = (result.get(k, [[]])[0] for k in ("ids", "documents", "metadatas", "distances"))
    for cid, doc, meta, dist in zip(ids, docs, metas, dists):
        score = round(1.0 - float(dist), 4)
        if min_score is not None and score < min_score:
            continue
        title = (meta or {}).get("section_title") or None
        if sections and not (title and any(title.lower().startswith(s.lower()) for s in sections)):
            continue
        page = (meta or {}).get("page")
        out.append({"chunk_id": cid, "paper_id": (meta or {}).get("paper_id"), "chunk_type": (meta or {}).get("chunk_type"),
                    "section": title, "page": page if isinstance(page, int) and page > 0 else None,
                    "text": doc or "", "score": score})
    return out


def search_chunks(query: str, k: int, filters: dict, min_score: float | None = None) -> dict:
    allowed = slice_papers(filters)
    types = filters.get("chunk_types")
    counts = chunk_counts()
    n = k * (SECTION_OVERFETCH if filters.get("sections") else 1)
    cov = coverage(allowed, types, filters)
    if cov["papers_in_slice"] == 0:
        return {"hits": [], "coverage": cov}
    pl = plan(allowed, types, n, counts)
    result = run_query(embed(query), pl)
    hits = _hits(result, filters.get("sections"), min_score)[:k]
    if pl.postfilter and len(hits) < k and len(result["ids"][0]) < n:
        result = run_query(embed(query), Plan(_where(allowed, set(counts), types), n, pl.allowed, pl.types, False))
        hits = _hits(result, filters.get("sections"), min_score)[:k]
    if filters.get("sections"):
        cov["note"] = f"the section filter is applied after retrieving {n} candidates"
    return {"hits": hits, "coverage": cov}


def search_papers(queries: list[str], k: int, max_candidates: int, filters: dict, aggregate: str) -> dict:
    allowed = slice_papers(filters)
    types = filters.get("chunk_types")
    cov = coverage(allowed, types, filters)
    per_paper: dict[str, list[dict]] = defaultdict(list)
    if cov["papers_in_slice"]:
        pl = plan(allowed, types, max(1, max_candidates // len(queries)), chunk_counts())
        for q in queries:
            result = run_query(embed(q), pl)
            for h in _hits(result, filters.get("sections"), None):
                per_paper[h["paper_id"]].append(dict(h, query=q))
    return {"papers": _aggregate(per_paper, k, aggregate), "coverage": cov}


def _aggregate(per_paper: dict[str, list[dict]], k: int, aggregate: str) -> list[dict]:
    ranked = []
    for pid, hits in per_paper.items():
        best_per_query: dict[str, float] = {}
        for h in hits:
            best_per_query[h["query"]] = max(best_per_query.get(h["query"], 0.0), h["score"])
        scores = list(best_per_query.values())
        score = {"max": max(scores), "mean": sum(scores) / len(scores), "count": float(len(hits))}[aggregate]
        best = sorted(hits, key=lambda h: -h["score"])[:3]
        ranked.append({"paper_id": pid, "score": round(score, 4), "hits": len(hits), "queries_matched": len(scores),
                       "best_chunks": [{k2: v for k2, v in h.items() if k2 != "query"} for h in best]})
    ranked.sort(key=lambda r: (-r["score"], -r["hits"], r["paper_id"]))
    return ranked[:k]


def seed_vector(paper_id: str) -> list[float] | None:
    """The seed paper's abstract embedding, else the mean of up to 200 of its chunks."""
    from src.document.chunker import LayoutAwareChunker
    col = collection()
    got = col.get(ids=[LayoutAwareChunker._make_id(paper_id, "abstract")], include=["embeddings"])
    embs = got.get("embeddings")
    if embs is not None and len(embs):
        return list(map(float, embs[0]))
    got = col.get(where={"paper_id": paper_id}, limit=200, include=["embeddings"])
    embs = got.get("embeddings")
    if embs is None or not len(embs):
        return None
    dim = len(embs[0])
    return [sum(float(e[i]) for e in embs) / len(embs) for i in range(dim)]


def similar_papers(paper_id: str, k: int, filters: dict) -> dict | None:
    vec = seed_vector(paper_id)
    if vec is None:
        return None
    allowed = slice_papers(filters) - {paper_id}
    types = filters.get("chunk_types")
    cov = coverage(allowed, types, filters)
    per_paper: dict[str, list[dict]] = defaultdict(list)
    if cov["papers_in_slice"]:
        result = run_query(vec, plan(allowed, types, min(1000, k * 25), chunk_counts()))
        for h in _hits(result, filters.get("sections"), None):
            if h["paper_id"] != paper_id:
                per_paper[h["paper_id"]].append(dict(h, query="seed"))
    return {"papers": _aggregate(per_paper, k, "max"), "coverage": cov}
