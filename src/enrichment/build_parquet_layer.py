"""
build_parquet_layer.py  —  GeoHydroAI Stage 5: parquet analytics layer
=======================================================================

Reads enriched paper JSONs, author_universe.parquet, and reference_enriched/
and writes normalized parquet tables for DuckDB / dashboard consumption.

Output tables:
    data/parquet/papers.parquet              — core paper metadata
    data/parquet/authors.parquet             — author scientometrics
    data/parquet/paper_author_edges.parquet  — paper ↔ author membership
    data/parquet/references.parquet          — directed citation edges
    data/parquet/topics.parquet              — paper ↔ topic scores

Neo4j is NOT the target here — these tables back analytical queries.
Neo4j handles graph traversal; parquet + DuckDB handle aggregation.

Usage:
    python -m src.enrichment.build_parquet_layer
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from typing import Optional

from src.config.settings import ENRICHED_DIR, PARQUET_DIR, REFERENCE_ENRICHED_DIR

log = logging.getLogger(__name__)

_MANIFEST_FILE = ".build_manifest.json"


def _load_manifest(parquet_dir: Path) -> dict[str, float]:
    """Return {filename: mtime} from the last build."""
    p = parquet_dir / _MANIFEST_FILE
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save_manifest(parquet_dir: Path, manifest: dict[str, float]) -> None:
    p = parquet_dir / _MANIFEST_FILE
    p.write_text(json.dumps(manifest, indent=2), encoding="utf-8")


# ─────────────────────────────────────────────────────────────────────────────
# Table builders
# ─────────────────────────────────────────────────────────────────────────────

def _load_enriched_docs(
    enriched_dir: Path,
    manifest: dict[str, float],
) -> tuple[list[dict], dict[str, float]]:
    """
    Load enriched JSONs.

    Returns (docs, updated_manifest) where docs contains only files that are
    new or have a changed mtime vs manifest.  Pass an empty manifest to force
    a full reload.
    """
    import os

    docs: list[dict] = []
    new_manifest = dict(manifest)

    for path in sorted(enriched_dir.glob("*.json")):
        mtime = os.stat(path).st_mtime
        if manifest.get(path.name) == mtime:
            continue  # unchanged — skip
        try:
            docs.append(json.loads(path.read_text(encoding="utf-8")))
            new_manifest[path.name] = mtime
        except Exception as exc:
            log.warning("Skipping %s: %s", path.name, exc)

    return docs, new_manifest


def _build_papers(docs: list[dict]) -> list[dict]:
    rows = []
    for doc in docs:
        meta     = (doc.get("paper") or {}).get("metadata", {})
        openalex = doc.get("openalex") or {}
        llm      = (doc.get("paper") or {}).get("llm_judge") or {}

        rows.append({
            "paper_id":      meta.get("paper_id"),
            "doi":           meta.get("doi"),
            "title":         meta.get("title"),
            "year":          openalex.get("publication_year"),
            "study_country": llm.get("country") if isinstance(llm, dict) else None,
            "task":          llm.get("task")    if isinstance(llm, dict) else None,
            "cited_by_count": openalex.get("cited_by_count"),
        })
    return rows


def _build_authors(author_universe_path: Optional[Path]) -> list[dict]:
    if not author_universe_path or not author_universe_path.exists():
        log.warning("author_universe.parquet not found — authors table will be empty")
        return []

    import duckdb
    con  = duckdb.connect()
    rows = con.execute(f"""
        SELECT
            author_id,
            display_name,
            orcid,
            works_count,
            cited_by_count,
            h_index,
            i10_index
        FROM '{author_universe_path}'
    """).fetchall()
    cols = ["author_id", "display_name", "orcid", "works_count",
            "cited_by_count", "h_index", "i10_index"]
    return [dict(zip(cols, row)) for row in rows]


def _build_paper_author_edges(docs: list[dict]) -> list[dict]:
    rows = []
    for doc in docs:
        meta     = (doc.get("paper") or {}).get("metadata", {})
        openalex = doc.get("openalex") or {}
        paper_id = meta.get("paper_id")

        for auth in openalex.get("authors", []):
            rows.append({
                "paper_id":        paper_id,
                "author_id":       auth.get("id"),
                "author_position": auth.get("author_position"),
                "is_corresponding": auth.get("is_corresponding", False),
            })
    return rows


def _build_references(docs: list[dict]) -> list[dict]:
    rows = []
    for doc in docs:
        meta     = (doc.get("paper") or {}).get("metadata", {})
        openalex = doc.get("openalex") or {}
        paper_id = meta.get("paper_id")

        # GROBID-parsed references with DOI
        for ref in (doc.get("paper") or {}).get("references", []):
            doi = ref.get("doi")
            if doi:
                rows.append({
                    "source_paper_id":      paper_id,
                    "referenced_doi":       doi,
                    "referenced_openalex_id": None,
                    "cited_by_count":       None,
                })

        # OpenAlex referenced_works (work IDs — no DOI at this stage)
        for oa_id in openalex.get("referenced_works", []):
            rows.append({
                "source_paper_id":      paper_id,
                "referenced_doi":       None,
                "referenced_openalex_id": oa_id,
                "cited_by_count":       None,
            })

    return rows


def _build_topics(docs: list[dict]) -> list[dict]:
    rows = []
    for doc in docs:
        meta     = (doc.get("paper") or {}).get("metadata", {})
        openalex = doc.get("openalex") or {}
        paper_id = meta.get("paper_id")

        for topic in openalex.get("topics", []):
            rows.append({
                "paper_id":   paper_id,
                "topic_id":   topic.get("id"),
                "topic_name": topic.get("name"),
                "score":      topic.get("score"),
            })
    return rows


# ─────────────────────────────────────────────────────────────────────────────
# Parquet writer
# ─────────────────────────────────────────────────────────────────────────────

def _write(
    rows: list[dict],
    out_path: Path,
    label: str,
    dedup_key: str = "paper_id",
    incremental: bool = False,
) -> None:
    import pandas as pd

    out_path.parent.mkdir(parents=True, exist_ok=True)

    if not rows:
        if not incremental:
            log.warning("No rows for %s — writing empty parquet", label)
        return

    new_df = pd.DataFrame(rows)

    if incremental and out_path.exists() and dedup_key in new_df.columns:
        try:
            existing = pd.read_parquet(out_path)
            # Remove stale rows for paper_ids being updated, then append
            new_ids = set(new_df[dedup_key].dropna())
            existing = existing[~existing[dedup_key].isin(new_ids)]
            combined = pd.concat([existing, new_df], ignore_index=True)
            combined.to_parquet(out_path, engine="pyarrow", compression="zstd", index=False)
            log.info("%-30s +%d rows (total %d)  [%s]", label, len(new_df), len(combined), out_path.name)
            return
        except Exception as exc:
            log.warning("Incremental merge failed for %s (%s) — falling back to full write", label, exc)

    new_df.to_parquet(out_path, engine="pyarrow", compression="zstd", index=False)
    log.info("%-30s → %d rows  [%s]", label, len(rows), out_path.name)


# ─────────────────────────────────────────────────────────────────────────────
# Public entry point
# ─────────────────────────────────────────────────────────────────────────────

def build_parquet_layer(
    enriched_dir: Path = ENRICHED_DIR,
    parquet_dir: Path = PARQUET_DIR,
    reference_enriched_dir: Path = REFERENCE_ENRICHED_DIR,
    rebuild: bool = False,
) -> None:
    """
    Stage 5 entry point.

    Reads enriched paper JSONs + author_universe.parquet and writes
    five normalized parquet tables to parquet_dir/.

    Incremental by default: only loads new/modified JSONs and merges them
    into existing parquet tables (deduplicating on paper_id).
    Pass rebuild=True to force a full scan of all enriched files.

    Idempotent — safe to re-run.
    """
    parquet_dir.mkdir(parents=True, exist_ok=True)

    manifest = {} if rebuild else _load_manifest(parquet_dir)
    log.info(
        "Loading enriched papers from %s (%s, manifest has %d entries)",
        enriched_dir, "full rebuild" if rebuild else "incremental", len(manifest),
    )

    docs, updated_manifest = _load_enriched_docs(enriched_dir, manifest)
    log.info("Loaded %d new/changed enriched paper docs", len(docs))

    if not docs:
        log.info("Nothing changed since last build — parquet layer is up to date")
        return

    incremental = not rebuild
    author_universe_path = parquet_dir / "author_universe.parquet"

    _write(_build_papers(docs),
           parquet_dir / "papers.parquet",
           "papers", incremental=incremental)

    _write(_build_authors(author_universe_path),
           parquet_dir / "authors.parquet",
           "authors")  # always full — single source file

    _write(_build_paper_author_edges(docs),
           parquet_dir / "paper_author_edges.parquet",
           "paper_author_edges", incremental=incremental)

    _write(_build_references(docs),
           parquet_dir / "references.parquet",
           "references", incremental=incremental)

    _write(_build_topics(docs),
           parquet_dir / "topics.parquet",
           "topics", incremental=incremental)

    _save_manifest(parquet_dir, updated_manifest)
    log.info("Parquet analytics layer complete → %s", parquet_dir)


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    parser = argparse.ArgumentParser(description="Build parquet analytics layer")
    parser.add_argument(
        "--rebuild", action="store_true",
        help="Force full scan of all enriched files (ignores manifest)",
    )
    args = parser.parse_args()
    build_parquet_layer(rebuild=args.rebuild)
