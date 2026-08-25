"""
Targeted re-extraction of numeric_facts.parquet for all papers with TEI XML.

Reads each data/literature/grobid_xml/*.tei.xml, runs extract_numeric_facts()
with the current (expanded) metric ontology, and overwrites
data/sodb/{paper_id}/numeric_facts.parquet in-place.

Safe to re-run: only touches numeric_facts.parquet, never re-runs GROBID/Nougat.

Usage:
    python -m src.analytics.reextract_numeric_facts
    python -m src.analytics.reextract_numeric_facts --dry-run
    python -m src.analytics.reextract_numeric_facts --limit 100
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

GROBID_DIR = Path("data/literature/grobid_xml")
SODB_DIR   = Path("data/sodb")


def _write_numeric_facts(paper_dir: Path, rows: list[dict]) -> None:
    if not rows:
        # Write typed empty parquet so sodb_aggregator sees EMPTY not MISSING
        df = pd.DataFrame(columns=[
            "fact_id", "paper_id", "table_id", "table_label", "page",
            "column_context", "col_header", "row_context", "raw_cell",
            "metric", "canonical_id", "node_label", "value", "unit",
            "confidence", "source",
        ])
    else:
        df = pd.DataFrame(rows)
    pq.write_table(pa.Table.from_pandas(df, preserve_index=False),
                   paper_dir / "numeric_facts.parquet")


def main(grobid_dir: Path = GROBID_DIR, sodb_dir: Path = SODB_DIR,
         dry_run: bool = False, limit: int | None = None) -> None:
    from src.extraction.table_extractor import extract_numeric_facts

    tei_files = sorted(grobid_dir.glob("*.tei.xml"))
    if limit:
        tei_files = tei_files[:limit]

    print(f"Re-extracting numeric facts from {len(tei_files):,} TEI XML files …")
    print(f"  dry_run={dry_run}  sodb_dir={sodb_dir}")

    stats: Counter = Counter()
    for tei_path in tei_files:
        paper_id   = tei_path.stem.removesuffix(".tei")
        paper_dir  = sodb_dir / paper_id

        facts = extract_numeric_facts(tei_path, paper_id)
        stats["files"] += 1
        stats["facts"] += len(facts)

        if facts:
            stats["papers_with_facts"] += 1
            for f in facts:
                stats[f"cid:{f.canonical_id}"] += 1

        if not dry_run:
            import json as _json
            from datetime import datetime, timezone
            _NOW = pd.Timestamp(datetime.now(timezone.utc))
            paper_dir.mkdir(parents=True, exist_ok=True)
            rows = []
            for f in facts:
                d = f.to_dict()
                # aggregator expects plain strings, not list columns
                d["column_context"] = _json.dumps(d.get("column_context") or [])
                d["row_context"]    = _json.dumps(d.get("row_context") or [])
                # fill non-nullable schema fields
                d.setdefault("pipeline_hash", "reextract_v1_ontology30")
                d.setdefault("created_at",    _NOW)
                d.setdefault("period_type",   None)
                d.setdefault("basin_id",      None)
                d.setdefault("source_region_id", None)
                rows.append(d)
            _write_numeric_facts(paper_dir, rows)

        if stats["files"] % 500 == 0:
            print(f"  {stats['files']:,} / {len(tei_files):,}  facts so far: {stats['facts']:,}")

    print(f"\n=== DONE ===")
    print(f"  Files processed      : {stats['files']:,}")
    print(f"  Papers with ≥1 fact  : {stats['papers_with_facts']:,}")
    print(f"  Total facts extracted: {stats['facts']:,}")
    print(f"\n  Top canonical IDs:")
    top = sorted(((v, k) for k, v in stats.items() if k.startswith("cid:")), reverse=True)[:15]
    for n, k in top:
        print(f"    {k.replace('cid:',''):30s}: {n:,}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Re-extract numeric_facts.parquet from TEI XML")
    ap.add_argument("--dry-run",  action="store_true")
    ap.add_argument("--limit",    type=int, default=None)
    ap.add_argument("--grobid-dir", type=Path, default=GROBID_DIR)
    ap.add_argument("--sodb-dir",   type=Path, default=SODB_DIR)
    args = ap.parse_args()
    main(args.grobid_dir, args.sodb_dir, args.dry_run, args.limit)
