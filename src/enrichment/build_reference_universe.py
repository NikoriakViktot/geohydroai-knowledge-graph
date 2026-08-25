"""
build_reference_universe.py  —  GeoHydroAI Stage 3b: reference DOI universe
=============================================================================

Reads all enriched paper JSONs, collects every referenced DOI and every
OpenAlex referenced-work ID, deduplicates them, and writes:

    data/cache/reference_doi_universe.parquet

Schema:
    doi            TEXT   — normalized DOI (may be null)
    openalex_id    TEXT   — OpenAlex work ID (may be null)
    source_count   INT    — number of papers that reference this work

This stage makes ZERO OpenAlex API calls.  It is purely a data-collection
and deduplication pass.  Reference enrichment is handled separately by
reference_enrichment.py.

Sources read:
    paper.references[].doi          — DOIs from GROBID-parsed reference lists
    openalex.referenced_works[]     — OpenAlex IDs from the enrichment stage

Usage:
    python -m src.enrichment.build_reference_universe
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from pathlib import Path
from typing import Optional

from src.config.settings import CACHE_DIR, ENRICHED_DIR, PARQUET_DIR
from src.enrichment.openalex_enrichment import normalize_doi

log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Collection
# ─────────────────────────────────────────────────────────────────────────────

def _collect_references(enriched_dir: Path) -> dict[str, dict]:
    """
    Return a mapping: canonical_key → {doi, openalex_id, source_count}.

    canonical_key is:
        "doi:{normalized_doi}"   when a DOI is available
        "oa:{openalex_id_short}" for OpenAlex-only references
    """
    universe: dict[str, dict] = {}

    # doi → set of source paper IDs (for source_count)
    source_counts: dict[str, int] = defaultdict(int)

    for path in sorted(enriched_dir.glob("*.json")):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            log.warning("Skipping %s: %s", path.name, exc)
            continue

        paper    = doc.get("paper", {})
        openalex = doc.get("openalex") or {}

        # ── Source 1: GROBID-parsed reference DOIs ────────────────────────
        for ref in paper.get("references", []):
            raw_doi = ref.get("doi")
            if not raw_doi:
                continue

            norm = normalize_doi(raw_doi)
            key  = f"doi:{norm}"

            if key not in universe:
                universe[key] = {"doi": norm, "openalex_id": None}

            source_counts[key] += 1

        # ── Source 2: OpenAlex referenced_works (work IDs) ───────────────
        for oa_id in openalex.get("referenced_works", []):
            if not oa_id:
                continue

            short = oa_id.split("/")[-1] if "/" in oa_id else oa_id
            key   = f"oa:{short}"

            if key not in universe:
                universe[key] = {"doi": None, "openalex_id": oa_id}

            source_counts[key] += 1

    # attach source counts
    for key, record in universe.items():
        record["source_count"] = source_counts.get(key, 0)

    return universe


# ─────────────────────────────────────────────────────────────────────────────
# Parquet output
# ─────────────────────────────────────────────────────────────────────────────

def _write_parquet(records: list[dict], out_path: Path) -> None:
    import duckdb
    import pandas as pd

    out_path.parent.mkdir(parents=True, exist_ok=True)

    if not records:
        log.warning("No reference records — skipping parquet write")
        return

    df  = pd.DataFrame(records)
    con = duckdb.connect()
    con.register("refs_df", df)
    con.execute(
        f"COPY refs_df TO '{out_path}' (FORMAT PARQUET, COMPRESSION ZSTD)"
    )
    log.info("Wrote %d reference records → %s", len(records), out_path)


# ─────────────────────────────────────────────────────────────────────────────
# Public entry point
# ─────────────────────────────────────────────────────────────────────────────

def build_reference_universe(
    enriched_dir: Path = ENRICHED_DIR,
    parquet_dir: Path = PARQUET_DIR,
) -> Path:
    """
    Build data/cache/reference_doi_universe.parquet.

    Makes no API calls.  Safe to run repeatedly (idempotent output).

    Returns:
        Path to the written parquet file.
    """
    out_path = parquet_dir / "reference_doi_universe.parquet"

    log.info("Scanning enriched papers in %s", enriched_dir)
    universe = _collect_references(enriched_dir)
    log.info("Unique references found: %d", len(universe))

    doi_count = sum(1 for r in universe.values() if r["doi"])
    oa_count  = sum(1 for r in universe.values() if r["openalex_id"] and not r["doi"])
    log.info("  With DOI: %d  |  OpenAlex-only: %d", doi_count, oa_count)

    records = list(universe.values())
    _write_parquet(records, out_path)

    return out_path


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    build_reference_universe()
