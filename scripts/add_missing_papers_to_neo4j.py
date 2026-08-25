"""
Add missing referenced papers to Neo4j using OpenAlex cache metadata.

For every DOI that:
  - appears in references.parquet (referenced by corpus papers)
  - is NOT already a Paper node in Neo4j
  - has metadata in openalex_extended cache

Creates:
  - Paper node (doi, title, year, cited_by_count, paper_id, source="openalex")
  - Author nodes + AUTHORED_BY edges
  - CITES edges from corpus papers → missing papers (via references.parquet)

Usage:
    .venv/bin/python3 scripts/add_missing_papers_to_neo4j.py [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sqlite3
from pathlib import Path

import pandas as pd
from neo4j import GraphDatabase

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CACHE_DB     = PROJECT_ROOT / "data" / "cache" / "openalex_doi.db"
REFS_PARQUET = PROJECT_ROOT / "data" / "parquet" / "references.parquet"
PAPERS_PQ    = PROJECT_ROOT / "data" / "parquet" / "papers.parquet"

NEO4J_URI  = "bolt://localhost:7687"
NEO4J_USER = "neo4j"
NEO4J_PASS = "python2024"

BATCH = 200   # Cypher UNWIND batch size


# ─── OpenAlex cache ───────────────────────────────────────────────────────────

def load_openalex_cache() -> dict[str, dict]:
    """Return {doi_lower: work_dict} for all entries in openalex_extended."""
    conn = sqlite3.connect(CACHE_DB)
    rows = conn.execute("SELECT doi, response FROM openalex_extended").fetchall()
    conn.close()
    out: dict[str, dict] = {}
    for doi, resp in rows:
        data = json.loads(resp)
        if data:
            out[doi.lower().strip()] = data
    log.info("OpenAlex cache loaded: %d entries", len(out))
    return out


# ─── helpers ──────────────────────────────────────────────────────────────────

def _journal(work: dict) -> str:
    loc = work.get("primary_location") or {}
    src = loc.get("source") or {}
    return src.get("display_name") or ""


def _authors(work: dict) -> list[dict]:
    """Return list of {name, openalex_id} dicts."""
    out = []
    for a in (work.get("authorships") or []):
        author = a.get("author") or {}
        name = author.get("display_name", "")
        oa_id = author.get("id", "")
        if name:
            out.append({"name": name, "openalex_id": oa_id})
    return out


def _paper_row(doi: str, work: dict, ref_count: int) -> dict:
    return {
        "doi":          doi,
        "paper_id":     doi.replace("/", "_"),
        "title":        work.get("title") or "",
        "year":         work.get("publication_year"),
        "cited_by_count": work.get("cited_by_count") or 0,
        "journal":      _journal(work),
        "source":       "openalex",
        "ref_count":    ref_count,  # how often corpus cites this
    }


# ─── Neo4j writes ─────────────────────────────────────────────────────────────

MERGE_PAPER = """
UNWIND $rows AS r
MERGE (p:Paper {doi: r.doi})
  ON CREATE SET
    p.paper_id     = r.paper_id,
    p.title        = r.title,
    p.year         = r.year,
    p.cited_by_count = r.cited_by_count,
    p.journal      = r.journal,
    p.source       = r.source,
    p.study_type   = 'reference_only'
  ON MATCH SET
    p.cited_by_count = CASE WHEN r.cited_by_count > coalesce(p.cited_by_count,0)
                            THEN r.cited_by_count ELSE p.cited_by_count END,
    p.title = CASE WHEN p.title IS NULL OR p.title = '' THEN r.title ELSE p.title END
RETURN count(p) as merged
"""

MERGE_AUTHOR = """
UNWIND $rows AS r
MERGE (a:Author {name: r.name})
  ON CREATE SET a.openalex_id = r.openalex_id
WITH a, r
MATCH (p:Paper {doi: r.doi})
MERGE (p)-[:AUTHORED_BY]->(a)
"""

MERGE_CITES = """
UNWIND $rows AS r
MATCH (src:Paper {paper_id: r.src_id})
MATCH (tgt:Paper {doi: r.tgt_doi})
MERGE (src)-[:CITES]->(tgt)
"""


def run_batch(session, query: str, rows: list[dict]) -> None:
    if not rows:
        return
    for i in range(0, len(rows), BATCH):
        session.run(query, rows=rows[i : i + BATCH])


# ─── main ─────────────────────────────────────────────────────────────────────

def main(dry_run: bool = False) -> None:
    # ── 1. Load data ──────────────────────────────────────────────────────────
    oa_cache = load_openalex_cache()

    refs   = pd.read_parquet(REFS_PARQUET)
    papers = pd.read_parquet(PAPERS_PQ)

    # DOIs already in corpus parquet
    corpus_dois: set[str] = set(papers["doi"].dropna().str.lower().str.strip())

    # Reference frequency per DOI
    ref_freq = (
        refs["referenced_doi"]
        .dropna()
        .str.lower()
        .str.strip()
        .value_counts()
        .to_dict()
    )

    # ── 2. Connect Neo4j, get existing Paper DOIs ─────────────────────────────
    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASS))
    with driver.session() as s:
        existing_neo4j: set[str] = {
            str(row["doi"]).lower()
            for row in s.run("MATCH (p:Paper) WHERE p.doi IS NOT NULL RETURN p.doi AS doi")
            if isinstance(row["doi"], str)
        }
    log.info("Paper nodes already in Neo4j: %d", len(existing_neo4j))

    # ── 3. Build candidate list ───────────────────────────────────────────────
    paper_rows:  list[dict] = []
    author_rows: list[dict] = []
    cites_rows:  list[dict] = []

    all_missing = set(ref_freq.keys()) - corpus_dois - existing_neo4j
    log.info("Missing DOIs not yet in Neo4j: %d", len(all_missing))

    in_cache = 0
    not_in_cache = 0

    for doi in sorted(all_missing):
        work = oa_cache.get(doi)
        if not work:
            not_in_cache += 1
            continue
        in_cache += 1
        rc = ref_freq.get(doi, 0)
        paper_rows.append(_paper_row(doi, work, rc))
        for a in _authors(work):
            author_rows.append({"doi": doi, **a})

    log.info("  In OpenAlex cache: %d", in_cache)
    log.info("  Not in cache (skipped): %d", not_in_cache)

    # ── 4. CITES edges: corpus_paper → missing_paper ─────────────────────────
    # Use paper_id from papers.parquet as src, doi as tgt
    new_dois = {r["doi"] for r in paper_rows}
    papers_with_id = papers[papers["doi"].notna()].copy()
    papers_with_id["doi_low"] = papers_with_id["doi"].str.lower().str.strip()
    doi_to_pid = dict(zip(papers_with_id["doi_low"], papers_with_id["paper_id"]))

    refs_filtered = refs[
        refs["referenced_doi"].str.lower().str.strip().isin(new_dois)
    ].copy()
    refs_filtered["src_doi_low"] = (
        refs_filtered["source_paper_id"]
        .map(lambda pid: doi_to_pid.get(pid, pid))  # src already is paper_id
    )
    for row in refs_filtered.itertuples():
        cites_rows.append({
            "src_id":  row.source_paper_id,
            "tgt_doi": row.referenced_doi.lower().strip(),
        })

    log.info("CITES edges to create: %d", len(cites_rows))

    if dry_run:
        log.info("[dry-run] Would MERGE %d Paper nodes", len(paper_rows))
        log.info("[dry-run] Would MERGE %d Author links", len(author_rows))
        log.info("[dry-run] Would MERGE %d CITES edges", len(cites_rows))
        log.info("Top 10 by citation count:")
        for r in sorted(paper_rows, key=lambda x: -x["cited_by_count"])[:10]:
            log.info("  %7d  %s  %s", r["cited_by_count"], r["doi"], r["title"][:55])
        driver.close()
        return

    # ── 5. Write to Neo4j ─────────────────────────────────────────────────────
    with driver.session() as s:
        log.info("Merging %d Paper nodes…", len(paper_rows))
        run_batch(s, MERGE_PAPER, paper_rows)

        log.info("Merging %d Author→Paper links…", len(author_rows))
        run_batch(s, MERGE_AUTHOR, author_rows)

        log.info("Merging %d CITES edges…", len(cites_rows))
        run_batch(s, MERGE_CITES, cites_rows)

        # Final counts
        total_papers = s.run("MATCH (p:Paper) RETURN count(p) as c").single()["c"]
        total_cites  = s.run("MATCH ()-[r:CITES]->() RETURN count(r) as c").single()["c"]
        log.info("Neo4j final state:")
        log.info("  Paper nodes : %d", total_papers)
        log.info("  CITES edges : %d", total_cites)

    driver.close()
    log.info("Done.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    main(dry_run=args.dry_run)
