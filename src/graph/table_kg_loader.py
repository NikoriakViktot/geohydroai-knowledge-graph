"""
table_kg_loader.py — NumericFact pipeline: GROBID TEI XML → Neo4j.

Reads all data/literature/grobid_xml/*.tei.xml files, extracts numeric
measurements from table elements using table_extractor.py, and writes:

  NumericFact nodes           — one per extracted (metric, value) pair
  Paper -[:HAS_NUMERIC_FACT]-> NumericFact
  NumericFact -[:MEASURES]->  Metric | Method

Idempotent: all writes use MERGE, safe to re-run.

Usage::

    python -m src.graph.table_kg_loader              # all papers
    python -m src.graph.table_kg_loader 0030         # one paper by ID prefix
    python -m src.graph.table_kg_loader --dry-run    # parse only, no writes
    python -m src.graph.table_kg_loader --stats      # show fact statistics
"""
from __future__ import annotations

import argparse
import logging
import sys
from collections import Counter
from pathlib import Path

from src.extraction.table_extractor import (
    NumericFact,
    detect_period_type,
    extract_numeric_facts,
)
from src.graph.neo4j_writer import GraphWriter

log = logging.getLogger("geohydro.graph.table_kg_loader")

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_GROBID_DIR   = _PROJECT_ROOT / "data" / "literature" / "grobid_xml"


# ── Core loader ───────────────────────────────────────────────────────────────

def load_numeric_facts(
    grobid_dir:    Path = _GROBID_DIR,
    paper_filter:  str | None = None,
    dry_run:       bool = False,
    writer:        GraphWriter | None = None,
) -> dict[str, int]:
    """
    Extract NumericFacts from all GROBID TEI XML files and write to Neo4j.

    Parameters
    ----------
    grobid_dir   : directory containing *.tei.xml files
    paper_filter : if given, only process files whose stem starts with this string
    dry_run      : parse and log but do not write to Neo4j
    writer       : existing GraphWriter; created and closed here if None

    Returns
    -------
    dict with counters: files_processed, facts_extracted, facts_written
    """
    tei_files = sorted(grobid_dir.glob("*.tei.xml"))
    if paper_filter:
        tei_files = [f for f in tei_files if f.stem.startswith(paper_filter)]

    log.info(
        "table_kg_loader: %d TEI files to process%s",
        len(tei_files),
        f" (filter={paper_filter!r})" if paper_filter else "",
    )
    if not tei_files:
        log.warning("No TEI XML files found in %s", grobid_dir)
        return {"files_processed": 0, "facts_extracted": 0, "facts_written": 0}

    # Facts belong to papers of the graph. grobid_xml also holds TEI of duplicates,
    # non-papers and truncated papers that have no Paper node; loading them produced
    # 1,659 orphan facts (65 papers) before 2026-10-03.
    own_writer = writer is None and not dry_run
    if own_writer:
        writer = GraphWriter()
    if writer is not None:
        known = writer.paper_ids()
        before = len(tei_files)
        tei_files = [f for f in tei_files if f.stem.removesuffix(".tei") in known]
        log.info("  %d of %d TEI files belong to a Paper node; the rest are skipped",
                 len(tei_files), before)

    all_facts: list[NumericFact] = []
    for tei_path in tei_files:
        paper_id = tei_path.stem.removesuffix(".tei")
        facts = extract_numeric_facts(tei_path, paper_id)
        all_facts.extend(facts)

    total_extracted = len(all_facts)
    log.info(
        "Extracted %d NumericFacts from %d files",
        total_extracted, len(tei_files),
    )

    if dry_run or not all_facts:
        _log_stats(all_facts)
        if own_writer:
            writer.close()
        return {
            "files_processed": len(tei_files),
            "facts_extracted": total_extracted,
            "facts_written":   0,
        }

    # Write to Neo4j

    try:
        fact_rows  = [
            {**f.to_dict(),
             "period_type": detect_period_type(f.row_context, f.col_header)}
            for f in all_facts
        ]
        paper_rows = [{"paper_id": f.paper_id, "fact_id": f.fact_id} for f in all_facts]
        meas_rows  = [{"fact_id": f.fact_id, "canonical_id": f.canonical_id,
                       "node_label": f.node_label, "confidence": f.confidence,
                       "display_name": f.metric}
                      for f in all_facts]

        writer.write_numeric_facts(fact_rows)
        writer.write_paper_numeric_fact_edges(paper_rows)
        writer.write_numeric_fact_measures_edges(meas_rows)
    finally:
        if own_writer:
            writer.close()

    _log_stats(all_facts)
    return {
        "files_processed": len(tei_files),
        "facts_extracted": total_extracted,
        "facts_written":   total_extracted,
    }


def _log_stats(facts: list[NumericFact]) -> None:
    if not facts:
        log.info("  (no facts)")
        return
    by_metric = Counter(f.canonical_id for f in facts)
    log.info("NumericFact distribution:")
    for cid, count in by_metric.most_common():
        sample = next(f for f in facts if f.canonical_id == cid)
        vals = [f.value for f in facts if f.canonical_id == cid]
        log.info(
            "  %-30s  %4d facts  min=%.3f  max=%.3f  example=%s",
            cid, count, min(vals), max(vals), sample.table_label,
        )


# ── CLI ───────────────────────────────────────────────────────────────────────

def _cli() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s — %(message)s",
    )

    parser = argparse.ArgumentParser(
        description="Load NumericFacts from GROBID TEI XML into Neo4j",
    )
    parser.add_argument("paper_id", nargs="?", help="Paper ID prefix filter (optional)")
    parser.add_argument("--dry-run", action="store_true", help="Parse but do not write")
    parser.add_argument("--stats",   action="store_true", help="Show statistics after run")
    parser.add_argument("--grobid-dir", default=str(_GROBID_DIR), help="TEI XML directory")
    args = parser.parse_args()

    counters = load_numeric_facts(
        grobid_dir   = Path(args.grobid_dir),
        paper_filter = args.paper_id,
        dry_run      = args.dry_run,
    )

    print(
        f"\nDone — files={counters['files_processed']}  "
        f"extracted={counters['facts_extracted']}  "
        f"written={counters['facts_written']}"
        + ("  (dry-run)" if args.dry_run else "")
    )


if __name__ == "__main__":
    _cli()
