"""
openalex_enrichment.py  —  GeoHydroAI Stage 2: OpenAlex enrichment
===================================================================

Enriches Stage 1 paper JSONs with OpenAlex citation metadata.

Architecture position:
    Stage 1 (parsing) → data/literature/paper_json/*.paper.json
    Stage 2 (this)    → data/enriched/{paper_id}.json

Public surface:
    DOICacheActor   Ray actor — SQLite-backed persistent cache for DOI→response
    normalize_doi   Canonical DOI normalization (strips URL prefix, lowercases)
    enrich_paper    Ray remote task — one paper JSON path → one enriched JSON

Cache guarantee:
    The same DOI never triggers more than one live OpenAlex request per Ray
    cluster lifetime.  A SQLite row written by put() is visible to all
    subsequent get() calls because DOICacheActor is max_concurrency=1 and
    runs in a single greenlet, so reads always see the latest committed state.

Note on OpenAlexActor internals:
    build_graph_entity() makes exactly ONE HTTP call per DOI.  All sub-fields
    (authors, topics, institutions) are projected from the same fetched work
    object via extract_* static methods — no redundant requests.

Upgrade path:
    Replace ray.get() calls inside enrich_paper with async Ray DAG chaining
    when throughput requirements exceed the current blocking-task model.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import ray

log = logging.getLogger(__name__)

_MAX_RETRIES   = 3
_RETRY_BACKOFF = 2.0   # seconds; delay = _RETRY_BACKOFF ** attempt


# ─────────────────────────────────────────────────────────────────────────────
# DOI normalisation
# ─────────────────────────────────────────────────────────────────────────────

def normalize_doi(doi: str) -> str:
    """Return a canonical DOI: lowercase, no resolver prefix."""
    doi = doi.strip().lower()
    for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
        if doi.startswith(prefix):
            doi = doi[len(prefix):]
            break
    return doi


# ─────────────────────────────────────────────────────────────────────────────
# SQLite cache actor
# ─────────────────────────────────────────────────────────────────────────────

@ray.remote(max_concurrency=1)
class DOICacheActor:
    """Persistent SQLite cache for OpenAlex DOI responses.

    max_concurrency=1 serialises all reads and writes through a single
    greenlet, preventing write-lock contention in concurrent enrichment tasks.
    WAL mode is still set so that future multi-reader upgrades work without
    schema changes.
    """

    def __init__(self, db_path: str) -> None:
        import sqlite3

        db = Path(db_path)
        db.parent.mkdir(parents=True, exist_ok=True)

        self._conn = sqlite3.connect(str(db), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS doi_cache (
                doi         TEXT    PRIMARY KEY,
                response    TEXT    NOT NULL,
                fetched_at  TEXT    NOT NULL
            )
        """)
        self._conn.commit()

    def get(self, doi: str) -> Optional[dict]:
        """Return cached OpenAlex response for doi, or None if not present."""
        cur = self._conn.execute(
            "SELECT response FROM doi_cache WHERE doi = ?", (doi,)
        )
        row = cur.fetchone()
        return json.loads(row[0]) if row else None

    def put(self, doi: str, data: dict) -> None:
        """Persist an OpenAlex response.  Overwrites existing entry silently."""
        self._conn.execute(
            "INSERT OR REPLACE INTO doi_cache (doi, response, fetched_at)"
            " VALUES (?, ?, ?)",
            (
                doi,
                json.dumps(data, ensure_ascii=False),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        self._conn.commit()

    def size(self) -> int:
        """Return number of cached DOIs."""
        cur = self._conn.execute("SELECT COUNT(*) FROM doi_cache")
        return cur.fetchone()[0]

    def close(self) -> None:
        self._conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# Output structure helpers
# ─────────────────────────────────────────────────────────────────────────────

def _openalex_block(data: dict) -> dict:
    """Project a build_graph_entity response to the canonical output block."""
    return {
        "openalex_id":      data.get("openalex_id"),
        "cited_by_count":   data.get("cited_by_count"),
        "publication_year": data.get("publication_year"),
        "authors":          data.get("authors", []),
        "topics":           data.get("topics", []),
        "institutions":     data.get("institutions", []),
        "referenced_works": data.get("referenced_works", []),
        "related_works":    data.get("related_works", []),
    }


def _build_output(
    paper: dict,
    openalex_data: Optional[dict],
    status: str,
    doi: Optional[str],
    cache_hit: bool,
    error: Optional[str] = None,
) -> dict:
    """
    Merge a Stage 1 paper dict with an OpenAlex enrichment result.

    Output schema:
        {
          "paper":           { ...Stage 1 paper dict... },
          "openalex":        { openalex_id, cited_by_count, publication_year,
                               authors, topics, referenced_works, related_works }
                             | null,
          "enrichment_meta": { status, paper_id, doi, fetched_at,
                               cache_hit [, error] }
        }
    """
    meta: dict = {
        "status":     status,
        "paper_id":   paper.get("metadata", {}).get("paper_id"),
        "doi":        doi,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "cache_hit":  cache_hit,
    }
    if error is not None:
        meta["error"] = error

    return {
        "paper":           paper,
        "openalex":        _openalex_block(openalex_data) if openalex_data else None,
        "enrichment_meta": meta,
    }


def _write_output(path: Path, doc: dict) -> None:
    path.write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")


# ─────────────────────────────────────────────────────────────────────────────
# Ray enrichment task
# ─────────────────────────────────────────────────────────────────────────────

@ray.remote
def enrich_paper(
    paper_path: str,
    cache_actor,
    openalex_actor,
    enriched_dir: str,
) -> dict:
    """
    Ray task: enrich one Stage 1 paper JSON with OpenAlex citation metadata.

    Flow:
        1. Load paper JSON, extract DOI.
        2. If no DOI → write stub with status="no_doi", return.
        3. Check DOICacheActor.  Cache hit → write enriched output, return.
        4. Cache miss → call OpenAlexActor.build_graph_entity with retry.
        5. Persist result to cache (fire-and-forget).
        6. Write enriched output JSON to enriched_dir/{paper_id}.json.

    Args:
        paper_path:    Absolute path to the *.paper.json file (str for Ray).
        cache_actor:   DOICacheActor handle.
        openalex_actor: OpenAlexActor handle.
        enriched_dir:  Absolute path to Stage 2 output directory (str for Ray).

    Returns:
        Summary dict: { paper_id, status [, cache_hit, error] }.
        Never raises — the pipeline must not crash on one failed DOI.

    Upgrade path:
        Replace ray.get() calls with async Ray DAG chaining (ObjectRef
        pipelines) when throughput requires non-blocking task execution.
    """
    paper_path   = Path(paper_path)
    enriched_dir = Path(enriched_dir)

    paper    = json.loads(paper_path.read_text(encoding="utf-8"))
    metadata = paper.get("metadata", {})
    paper_id = metadata.get("paper_id") or paper_path.stem
    doi_raw  = metadata.get("doi")

    out_path = enriched_dir / f"{paper_id}.json"

    # ── Idempotency: skip already-enriched papers ─────────────────────────
    if out_path.exists():
        return {"paper_id": paper_id, "status": "already_enriched"}

    # ── No DOI: write stub and continue ──────────────────────────────────
    if not doi_raw:
        log.warning("No DOI — skipping enrichment for paper_id=%s", paper_id)
        _write_output(out_path, _build_output(paper, None, "no_doi", None, False))
        return {"paper_id": paper_id, "status": "no_doi"}

    doi = normalize_doi(doi_raw)

    # ── Cache lookup ──────────────────────────────────────────────────────
    cached: Optional[dict] = ray.get(cache_actor.get.remote(doi))
    if cached is not None:
        log.info("Cache hit  doi=%s  paper_id=%s", doi, paper_id)
        _write_output(out_path, _build_output(paper, cached, "enriched", doi, True))
        return {"paper_id": paper_id, "status": "enriched", "cache_hit": True}

    # ── OpenAlex API call with exponential-backoff retry ─────────────────
    openalex_data: Optional[dict] = None
    last_exc: Optional[Exception] = None

    for attempt in range(_MAX_RETRIES):
        try:
            openalex_data = ray.get(
                openalex_actor.build_graph_entity.remote(doi)
            )
            break
        except Exception as exc:
            last_exc = exc
            log.warning(
                "OpenAlex attempt %d/%d failed  doi=%s: %s",
                attempt + 1, _MAX_RETRIES, doi, exc,
            )
            if attempt < _MAX_RETRIES - 1:
                time.sleep(_RETRY_BACKOFF ** attempt)

    if openalex_data is None:
        error_msg = str(last_exc)
        log.error(
            "OpenAlex gave up after %d retries  doi=%s: %s",
            _MAX_RETRIES, doi, error_msg,
        )
        _write_output(
            out_path,
            _build_output(paper, None, "failed", doi, False, error_msg),
        )
        return {"paper_id": paper_id, "status": "failed", "error": error_msg}

    # ── Persist to cache (fire-and-forget) ────────────────────────────────
    cache_actor.put.remote(doi, openalex_data)

    # ── Write enriched output ─────────────────────────────────────────────
    _write_output(out_path, _build_output(paper, openalex_data, "enriched", doi, False))

    log.info("Enriched  paper_id=%s  doi=%s", paper_id, doi)
    return {"paper_id": paper_id, "status": "enriched", "cache_hit": False}
