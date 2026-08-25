"""
reference_enrichment.py  —  GeoHydroAI Stage 4: reference enrichment
=====================================================================

Reads data/cache/reference_doi_universe.parquet produced by Stage 3b,
enriches each unique reference via OpenAlex (one request per reference,
never per-paper), and writes per-reference JSON files:

    data/reference_enriched/{safe_key}.json

Architecture guarantees:
    - Each reference is enriched at most once per cluster lifetime
    - SQLite reference cache prevents duplicate API calls across runs
    - Ray sliding window keeps concurrency bounded
    - resume-after-crash: completed files are skipped on re-run
    - references with DOI use GET /works/doi:{doi}
    - references with only an OpenAlex ID use GET /works/{id}

Required metadata stored per reference:
    openalex_id, doi, title, publication_year, cited_by_count,
    authors, topics, source (journal/venue), concepts

Usage:
    python -m src.enrichment.reference_enrichment
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import ray

from src.actors.openalex_actor import OpenAlexActor
from src.config.settings import (
    CACHE_DIR,
    PARQUET_DIR,
    REFERENCE_ENRICHED_DIR,
    RAY_MAX_CONCURRENT,
)

log = logging.getLogger(__name__)

_MAX_RETRIES   = 3
_RETRY_BACKOFF = 2.0
_REF_DB        = "reference_cache.db"


# ─────────────────────────────────────────────────────────────────────────────
# SQLite reference cache actor
# ─────────────────────────────────────────────────────────────────────────────

@ray.remote(max_concurrency=1)
class ReferenceCacheActor:
    """Persistent SQLite cache for enriched reference responses."""

    def __init__(self, db_path: str) -> None:
        import sqlite3

        db = Path(db_path)
        db.parent.mkdir(parents=True, exist_ok=True)

        self._conn = sqlite3.connect(str(db), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS reference_cache (
                ref_key       TEXT PRIMARY KEY,
                response_json TEXT NOT NULL,
                fetched_at    TEXT NOT NULL
            )
        """)
        self._conn.commit()

    def get(self, ref_key: str) -> Optional[dict]:
        cur = self._conn.execute(
            "SELECT response_json FROM reference_cache WHERE ref_key = ?",
            (ref_key,),
        )
        row = cur.fetchone()
        return json.loads(row[0]) if row else None

    def put(self, ref_key: str, data: dict) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO reference_cache (ref_key, response_json, fetched_at)"
            " VALUES (?, ?, ?)",
            (ref_key, json.dumps(data, ensure_ascii=False),
             datetime.now(timezone.utc).isoformat()),
        )
        self._conn.commit()

    def size(self) -> int:
        cur = self._conn.execute("SELECT COUNT(*) FROM reference_cache")
        return cur.fetchone()[0]


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _safe_filename(key: str) -> str:
    """Convert a ref key (doi:… or oa:…) to a filesystem-safe stem."""
    return hashlib.sha256(key.encode()).hexdigest()[:24]


def _project_reference(work: dict) -> dict:
    """Project a raw OpenAlex work to the required reference schema."""
    source = work.get("primary_location") or {}
    venue  = source.get("source") or {}

    authors = [
        {
            "id":   (a.get("author") or {}).get("id"),
            "name": (a.get("author") or {}).get("display_name"),
        }
        for a in work.get("authorships", [])
    ]

    topics = [
        {"id": t.get("id"), "name": t.get("display_name"), "score": t.get("score")}
        for t in work.get("topics", [])
    ]

    concepts = [
        {"id": c.get("id"), "name": c.get("display_name"), "score": c.get("score")}
        for c in work.get("concepts", [])
    ]

    return {
        "openalex_id":      work.get("id"),
        "doi":              work.get("doi"),
        "title":            work.get("title"),
        "publication_year": work.get("publication_year"),
        "cited_by_count":   work.get("cited_by_count"),
        "authors":          authors,
        "topics":           topics,
        "source":           venue.get("display_name"),
        "concepts":         concepts,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Per-reference enrichment task
# ─────────────────────────────────────────────────────────────────────────────

@ray.remote
def enrich_reference(
    ref_key: str,
    doi: Optional[str],
    openalex_id: Optional[str],
    cache_actor,
    openalex_actor,
    output_dir: str,
) -> dict:
    """
    Ray task: enrich one reference.

    ref_key identifies the record ("doi:{norm}" or "oa:{short}").
    Lookup strategy: DOI preferred, fallback to OpenAlex ID.

    Never raises — one failure must not abort the batch.
    """
    out_dir  = Path(output_dir)
    filename = _safe_filename(ref_key) + ".json"
    out_path = out_dir / filename

    # idempotency: skip if file already written
    if out_path.exists():
        return {"ref_key": ref_key, "status": "already_enriched"}

    # cache lookup
    cached = ray.get(cache_actor.get.remote(ref_key))
    if cached is not None:
        out_path.write_text(
            json.dumps(cached, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        return {"ref_key": ref_key, "status": "cached"}

    # fetch from OpenAlex
    work: Optional[dict] = None
    last_exc: Optional[Exception] = None

    for attempt in range(_MAX_RETRIES):
        try:
            if doi:
                work = ray.get(openalex_actor.get_work_by_doi.remote(doi))
            else:
                work = ray.get(openalex_actor.get_work_by_id.remote(openalex_id))
            break
        except Exception as exc:
            last_exc = exc
            log.warning(
                "Reference fetch attempt %d/%d  key=%s: %s",
                attempt + 1, _MAX_RETRIES, ref_key, exc,
            )
            if attempt < _MAX_RETRIES - 1:
                time.sleep(_RETRY_BACKOFF ** attempt)

    if work is None:
        log.error("Giving up on ref_key=%s: %s", ref_key, last_exc)
        return {"ref_key": ref_key, "status": "failed", "error": str(last_exc)}

    projected = _project_reference(work)
    cache_actor.put.remote(ref_key, projected)
    out_path.write_text(
        json.dumps(projected, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    return {"ref_key": ref_key, "status": "enriched"}


# ─────────────────────────────────────────────────────────────────────────────
# Orchestration
# ─────────────────────────────────────────────────────────────────────────────

def run_reference_enrichment(
    parquet_dir: Path = PARQUET_DIR,
    cache_dir: Path = CACHE_DIR,
    output_dir: Path = REFERENCE_ENRICHED_DIR,
    max_concurrent: int = RAY_MAX_CONCURRENT,
    ray_address: Optional[str] = None,
) -> None:
    """
    Stage 4 entry point.

    Reads reference_doi_universe.parquet, enriches every reference
    that has not yet been written to data/reference_enriched/.
    """
    import duckdb

    universe_path = parquet_dir / "reference_doi_universe.parquet"
    if not universe_path.exists():
        log.error(
            "Reference universe not found: %s — run build_reference_universe first",
            universe_path,
        )
        return

    if not ray.is_initialized():
        ray.init(address=ray_address, ignore_reinit_error=True)

    output_dir.mkdir(parents=True, exist_ok=True)

    db_path     = str(cache_dir / _REF_DB)
    cache_actor = ReferenceCacheActor.remote(db_path)
    oa_actor    = OpenAlexActor.remote()

    # load universe
    con  = duckdb.connect()
    rows = con.execute(f"SELECT doi, openalex_id FROM '{universe_path}'").fetchall()
    log.info("References to process: %d", len(rows))

    from collections import Counter
    statuses: Counter = Counter()

    # build (ref_key, doi, openalex_id) triples
    def _make_key(doi: Optional[str], oa_id: Optional[str]) -> str:
        if doi:
            return f"doi:{doi}"
        short = oa_id.split("/")[-1] if oa_id and "/" in oa_id else oa_id
        return f"oa:{short}"

    work_items = [
        (_make_key(doi, oa_id), doi or None, oa_id or None)
        for doi, oa_id in rows
    ]

    total        = len(work_items)
    items_iter   = iter(work_items)
    in_flight: dict[ray.ObjectRef, str] = {}
    done         = 0

    def _submit() -> bool:
        try:
            ref_key, doi, oa_id = next(items_iter)
        except StopIteration:
            return False
        ref = enrich_reference.remote(
            ref_key, doi, oa_id, cache_actor, oa_actor, str(output_dir)
        )
        in_flight[ref] = ref_key
        return True

    for _ in range(min(max_concurrent, total or 1)):
        _submit()

    while in_flight:
        finished, _ = ray.wait(list(in_flight.keys()), num_returns=1)
        ref     = finished[0]
        ref_key = in_flight.pop(ref)
        done   += 1

        try:
            result = ray.get(ref)
            statuses[result["status"]] += 1
            log.info("[%d/%d] %s  %s", done, total, result["status"].upper(), ref_key)
        except Exception as exc:
            statuses["task_error"] += 1
            log.error("[%d/%d] TASK ERROR  %s: %s", done, total, ref_key, exc)

        _submit()

    log.info("Stage 4 reference enrichment complete — %s", dict(statuses))


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import os

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    run_reference_enrichment(ray_address=os.getenv("RAY_ADDRESS"))
