"""
build_author_universe.py  —  GeoHydroAI Stage 2b: author scientometrics
========================================================================

Reads all enriched paper JSONs, collects unique OpenAlex author IDs,
fetches author-level scientometrics from the /authors/{id} endpoint,
and writes data/cache/author_universe.parquet.

Author fields stored:
    author_id, display_name, orcid, works_count, cited_by_count,
    h_index, i10_index, last_known_institution, country_code

Design principles:
    - SQLite author_cache table prevents duplicate API calls across runs
    - Ray sliding-window keeps concurrency bounded (RAY_MAX_CONCURRENT)
    - Idempotent: re-running skips already-cached authors and rewrites
      the parquet snapshot from the full cache each time
    - Resume-after-crash: partial runs leave the SQLite cache populated;
      the next run skips every author that was already fetched

Usage:
    python -m src.enrichment.build_author_universe
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import ray

from src.actors.openalex_actor import OpenAlexActor
from src.config.settings import (
    CACHE_DIR,
    ENRICHED_DIR,
    PARQUET_DIR,
    RAY_MAX_CONCURRENT,
)

log = logging.getLogger(__name__)

_MAX_RETRIES   = 3
_RETRY_BACKOFF = 2.0
_AUTHOR_DB     = "author_cache.db"


# ─────────────────────────────────────────────────────────────────────────────
# SQLite author cache actor
# ─────────────────────────────────────────────────────────────────────────────

@ray.remote(max_concurrency=1)
class AuthorCacheActor:
    """Persistent SQLite cache for OpenAlex /authors/{id} responses."""

    def __init__(self, db_path: str) -> None:
        db = Path(db_path)
        db.parent.mkdir(parents=True, exist_ok=True)

        self._conn = sqlite3.connect(str(db), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS author_cache (
                author_id     TEXT PRIMARY KEY,
                response_json TEXT NOT NULL,
                updated_at    TEXT NOT NULL
            )
        """)
        self._conn.commit()

    def get(self, author_id: str) -> Optional[dict]:
        cur = self._conn.execute(
            "SELECT response_json FROM author_cache WHERE author_id = ?",
            (author_id,),
        )
        row = cur.fetchone()
        return json.loads(row[0]) if row else None

    def put(self, author_id: str, data: dict) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO author_cache (author_id, response_json, updated_at)"
            " VALUES (?, ?, ?)",
            (author_id, json.dumps(data, ensure_ascii=False),
             datetime.now(timezone.utc).isoformat()),
        )
        self._conn.commit()

    def get_all(self) -> list[dict]:
        """Return every cached author response for parquet export."""
        cur = self._conn.execute("SELECT response_json FROM author_cache")
        return [json.loads(row[0]) for row in cur.fetchall()]

    def size(self) -> int:
        cur = self._conn.execute("SELECT COUNT(*) FROM author_cache")
        return cur.fetchone()[0]


# ─────────────────────────────────────────────────────────────────────────────
# Per-author enrichment task
# ─────────────────────────────────────────────────────────────────────────────

@ray.remote
def enrich_author(
    author_id: str,
    cache_actor,
    openalex_actor,
) -> dict:
    """
    Ray task: fetch one author from OpenAlex /authors/{id}.

    Returns a summary dict: {author_id, status [, error]}.
    Never raises — one bad author must not abort the batch.
    """
    cached = ray.get(cache_actor.get.remote(author_id))
    if cached is not None:
        return {"author_id": author_id, "status": "cached"}

    data: Optional[dict] = None
    last_exc: Optional[Exception] = None

    for attempt in range(_MAX_RETRIES):
        try:
            data = ray.get(openalex_actor.get_author_by_id.remote(author_id))
            break
        except Exception as exc:
            last_exc = exc
            log.warning(
                "Author fetch attempt %d/%d failed  id=%s: %s",
                attempt + 1, _MAX_RETRIES, author_id, exc,
            )
            if attempt < _MAX_RETRIES - 1:
                time.sleep(_RETRY_BACKOFF ** attempt)

    if data is None:
        return {"author_id": author_id, "status": "failed", "error": str(last_exc)}

    cache_actor.put.remote(author_id, data)
    return {"author_id": author_id, "status": "enriched"}


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _collect_author_ids(enriched_dir: Path) -> set[str]:
    """Extract unique OpenAlex author IDs from all enriched paper JSONs."""
    ids: set[str] = set()

    for path in enriched_dir.glob("*.json"):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue

        openalex = doc.get("openalex") or {}
        for auth in openalex.get("authors", []):
            aid = auth.get("id")
            if aid:
                ids.add(aid)

    return ids


def _project_author(raw: dict) -> dict:
    """Project a raw /authors/{id} response to the canonical schema."""
    last_inst = raw.get("last_known_institution") or {}
    summary   = raw.get("summary_stats") or {}

    return {
        "author_id":             raw.get("id"),
        "display_name":          raw.get("display_name"),
        "orcid":                 raw.get("orcid"),
        "works_count":           raw.get("works_count"),
        "cited_by_count":        raw.get("cited_by_count"),
        "h_index":               summary.get("h_index"),
        "i10_index":             summary.get("i10_index"),
        "last_known_institution": last_inst.get("display_name"),
        "country_code":          last_inst.get("country_code"),
    }


def _write_parquet(rows: list[dict], out_path: Path) -> None:
    """Write author rows to parquet via duckdb (no pyarrow dependency)."""
    import duckdb

    out_path.parent.mkdir(parents=True, exist_ok=True)

    if not rows:
        log.warning("No author rows to write — skipping parquet export")
        return

    con = duckdb.connect()
    import pandas as pd
    df = pd.DataFrame(rows)
    con.register("authors_df", df)
    con.execute(
        f"COPY authors_df TO '{out_path}' (FORMAT PARQUET, COMPRESSION ZSTD)"
    )
    log.info("Wrote %d author rows → %s", len(rows), out_path)


# ─────────────────────────────────────────────────────────────────────────────
# Public orchestration
# ─────────────────────────────────────────────────────────────────────────────

def build_author_universe(
    enriched_dir: Path = ENRICHED_DIR,
    cache_dir: Path = CACHE_DIR,
    parquet_dir: Path = PARQUET_DIR,
    max_concurrent: int = RAY_MAX_CONCURRENT,
    ray_address: Optional[str] = None,
) -> None:
    """
    Stage 2b entry point.

    1. Collect unique author IDs from enriched paper JSONs.
    2. Skip authors already in SQLite cache.
    3. Fetch remaining from OpenAlex /authors/{id} with bounded concurrency.
    4. Rebuild author_universe.parquet from the complete cache.
    """
    if not ray.is_initialized():
        ray.init(address=ray_address, ignore_reinit_error=True)

    db_path      = str(cache_dir / _AUTHOR_DB)
    cache_actor  = AuthorCacheActor.remote(db_path)
    oa_actor     = OpenAlexActor.remote()

    log.info("Collecting author IDs from enriched papers in %s", enriched_dir)
    all_ids = _collect_author_ids(enriched_dir)
    log.info("Unique author IDs found: %d", len(all_ids))

    cached_count = ray.get(cache_actor.size.remote())
    log.info("Already cached: %d", cached_count)

    pending_ids = list(all_ids)

    # ── sliding-window enrichment ─────────────────────────────────────────
    in_flight: dict[ray.ObjectRef, str] = {}
    from collections import Counter
    statuses: Counter = Counter()
    ids_iter = iter(pending_ids)
    total    = len(pending_ids)
    done     = 0

    def _submit() -> bool:
        try:
            aid = next(ids_iter)
        except StopIteration:
            return False
        ref = enrich_author.remote(aid, cache_actor, oa_actor)
        in_flight[ref] = aid
        return True

    for _ in range(min(max_concurrent, total or 1)):
        _submit()

    while in_flight:
        finished, _ = ray.wait(list(in_flight.keys()), num_returns=1)
        ref = finished[0]
        aid = in_flight.pop(ref)
        done += 1

        try:
            result = ray.get(ref)
            statuses[result["status"]] += 1
            log.info("[%d/%d] %s  %s", done, total, result["status"].upper(), aid)
        except Exception as exc:
            statuses["task_error"] += 1
            log.error("[%d/%d] TASK ERROR  %s: %s", done, total, aid, exc)

        _submit()

    # ── rebuild parquet from full cache ──────────────────────────────────
    all_cached = ray.get(cache_actor.get_all.remote())
    rows = [_project_author(r) for r in all_cached]
    _write_parquet(rows, parquet_dir / "author_universe.parquet")

    log.info("Stage 2b complete — authors: %d total / %s", len(all_cached), dict(statuses))


# ─────────────────────────────────────────────────────────────────────────────
# CLI entry point
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import os

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    build_author_universe(ray_address=os.getenv("RAY_ADDRESS"))
