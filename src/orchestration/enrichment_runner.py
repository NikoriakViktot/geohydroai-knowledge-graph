"""
enrichment_runner.py  —  GeoHydroAI Stage 2 orchestration entry point
=====================================================================

Reads Stage 1 paper JSONs, enriches each with OpenAlex citation metadata via
DOI-based lookup, and writes enriched JSONs for Stage 3 graph construction.

Architecture position:
    Stage 1 → data/literature/paper_json/*.paper.json
    Stage 2 → data/enriched/{paper_id}.json  (this runner)
    Stage 3 → Neo4j

Usage (local):
    python -m src.orchestration.enrichment_runner

Usage (Ray cluster):
    RAY_ADDRESS=ray://<head-node>:10001 python -m src.orchestration.enrichment_runner

Environment overrides:
    OUT_DIR             Stage 1 paper JSON input directory
    ENRICHED_DIR        Stage 2 output directory
    CACHE_DIR           SQLite DOI cache directory
    RAY_MAX_CONCURRENT  Max in-flight enrichment tasks (default 8)
    RAY_ADDRESS         Ray cluster address (None = local runtime)

Design notes:
    - DOICacheActor is created once and shared by all enrichment tasks.
      It serialises SQLite reads/writes (max_concurrency=1).
    - OpenAlexActor is created once and shared (max_concurrency=8).
    - Enrichment tasks run in a sliding window identical to the Stage 1
      pipeline runner, keeping at most RAY_MAX_CONCURRENT tasks in-flight.
    - The runner is fully idempotent: re-running skips already-enriched papers
      (file-based check) and avoids duplicate API calls (SQLite cache).
    - Each status category is counted and printed at the end.
"""

from __future__ import annotations

import json
import logging
import traceback
from collections import Counter
from pathlib import Path
from typing import Optional

import ray

from src.actors.openalex_actor import OpenAlexActor
from src.config.settings import (
    CACHE_DIR,
    ENRICHED_DIR,
    NORMALIZED_DIR,
    RAY_MAX_CONCURRENT,
)
from src.enrichment.openalex_enrichment import DOICacheActor, enrich_paper

log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# public orchestration API
# ─────────────────────────────────────────────────────────────────────────────

def run_enrichment(
    papers_dir: Path = NORMALIZED_DIR,
    enriched_dir: Path = ENRICHED_DIR,
    cache_dir: Path = CACHE_DIR,
    max_concurrent: int = RAY_MAX_CONCURRENT,
    ray_address: Optional[str] = None,
) -> list[Path]:
    """
    Orchestrate distributed Stage 2 enrichment.

    Creates Ray actors once, then fans out enrich_paper tasks in a sliding
    window of size `max_concurrent`.

    Args:
        papers_dir:     Directory containing *.paper.json files from Stage 1.
        enriched_dir:   Destination for enriched output JSONs.
        cache_dir:      Directory for the SQLite DOI response cache.
        max_concurrent: Maximum simultaneously in-flight Ray tasks.
        ray_address:    Ray cluster address (None = local runtime).

    Returns:
        List of enriched output paths written during this run.
    """
    # ── 1. init Ray ───────────────────────────────────────────────────────
    if not ray.is_initialized():
        ray.init(address=ray_address, ignore_reinit_error=True)
        log.info("Ray initialised  (address=%s)", ray_address or "local")

    enriched_dir.mkdir(parents=True, exist_ok=True)
    cache_dir.mkdir(parents=True, exist_ok=True)

    # ── 2. create actors ONCE — shared across all tasks ───────────────────
    db_path       = str(cache_dir / "openalex_doi.db")
    cache_actor   = DOICacheActor.remote(db_path)
    openalex_actor = OpenAlexActor.remote()
    log.info("Actors ready: DOICacheActor (db=%s) | OpenAlexActor", db_path)

    # ── 3. discover Stage 1 paper JSONs ──────────────────────────────────
    paper_files = sorted(papers_dir.glob("*.json"))
    log.info("Found %d paper JSON(s) in %s", len(paper_files), papers_dir)
    if not paper_files:
        log.warning("No *.paper.json files found — nothing to enrich.")
        return []

    cache_size_before = ray.get(cache_actor.size.remote())
    log.info("DOI cache pre-existing entries: %d", cache_size_before)

    # ── 4. sliding-window task submission ────────────────────────────────
    #
    # Mirrors the pattern in pipeline_runner.py:
    #   - Keep at most `max_concurrent` futures alive at once.
    #   - As each future resolves, record its output and submit the next file.
    #   - Bounds memory and prevents overwhelming the OpenAlexActor.

    pending:  dict[ray.ObjectRef, Path] = {}
    outputs:  list[Path]                = []
    statuses: Counter                   = Counter()
    files_iter = iter(paper_files)

    def _submit_next() -> bool:
        try:
            paper_path = next(files_iter)
        except StopIteration:
            return False
        ref = enrich_paper.remote(
            str(paper_path),
            cache_actor,
            openalex_actor,
            str(enriched_dir),
        )
        pending[ref] = paper_path
        return True

    for _ in range(min(max_concurrent, len(paper_files))):
        _submit_next()

    total      = len(paper_files)
    done_count = 0

    while pending:
        finished, _ = ray.wait(list(pending.keys()), num_returns=1, timeout=None)
        ref        = finished[0]
        paper_path = pending.pop(ref)

        try:
            summary    = ray.get(ref)
            paper_id   = summary.get("paper_id", paper_path.stem)
            status     = summary.get("status", "unknown")
            cache_hit  = summary.get("cache_hit", False)
            done_count += 1
            statuses[status] += 1

            out_path = enriched_dir / f"{paper_id}.json"
            if out_path.exists() and status not in ("already_enriched",):
                outputs.append(out_path)

            hit_tag = " [cache]" if cache_hit else ""
            log.info(
                "[%d/%d] %s  %s%s",
                done_count, total, status.upper(), paper_id, hit_tag,
            )

        except Exception as exc:
            done_count += 1
            statuses["task_error"] += 1
            log.error(
                "[%d/%d] TASK ERROR  %s — %s: %s",
                done_count, total, paper_path.name,
                type(exc).__name__, exc,
            )
            traceback.print_exc()

        _submit_next()

    # ── 5. final report ───────────────────────────────────────────────────
    cache_size_after = ray.get(cache_actor.size.remote())

    log.info("=" * 60)
    log.info("Stage 2 enrichment complete")
    log.info("  Papers processed : %d", total)
    for status, count in sorted(statuses.items()):
        log.info("  %-20s : %d", status, count)
    log.info("  Cache entries    : %d → %d", cache_size_before, cache_size_after)
    log.info("  Enriched dir     : %s", enriched_dir)
    log.info("=" * 60)

    return outputs


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

    run_enrichment(ray_address=os.getenv("RAY_ADDRESS"))
