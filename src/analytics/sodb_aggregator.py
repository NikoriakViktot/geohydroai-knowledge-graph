"""
sodb_aggregator.py — Cross-paper SODB parquet aggregation.

Walks data/sodb/{paper_id}/*.parquet and concatenates each table type
into a single corpus-level parquet in data/analytics/.

Supported tables
----------------
    regions              ← regions.parquet
    formulas             ← formulas.parquet
    sodb_tables          ← tables.parquet   (renamed to avoid clash with SQL TABLE)
    numeric_facts        ← numeric_facts.parquet
    scientific_objects   ← scientific_objects.parquet
    object_graph         ← object_graph.parquet
    semantic_annotations ← semantic_annotations.parquet
    semantic_edges       ← semantic_edges.parquet

Usage
-----
    python -m src.analytics.sodb_aggregator
    python -m src.analytics.sodb_aggregator --sodb-dir data/sodb --output-dir data/analytics
    python -m src.analytics.sodb_aggregator --rebuild          # re-scan even if output exists
    python -m src.analytics.sodb_aggregator --tables regions formulas
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

import pyarrow as pa
import pyarrow.parquet as pq

from src.analytics.parquet_schema import (
    FORMULAS_SCHEMA,
    NUMERIC_FACTS_SCHEMA,
    OBJECT_GRAPH_SCHEMA,
    REGIONS_SCHEMA,
    SCIENTIFIC_OBJECTS_SCHEMA,
    SEMANTIC_ANNOTATIONS_SCHEMA,
    SEMANTIC_EDGES_SCHEMA,
    TABLES_SCHEMA,
)

log = logging.getLogger(__name__)

_PROJECT_ROOT  = Path(__file__).resolve().parents[2]
_DEFAULT_SODB  = _PROJECT_ROOT / "data" / "sodb"
_DEFAULT_OUT   = _PROJECT_ROOT / "data" / "analytics"

# Maps output analytics filename → (per-paper filename, expected schema)
_SODB_TABLES: dict[str, tuple[str, pa.Schema]] = {
    "regions.parquet":              ("regions.parquet",              REGIONS_SCHEMA),
    "formulas.parquet":             ("formulas.parquet",             FORMULAS_SCHEMA),
    "sodb_tables.parquet":          ("tables.parquet",               TABLES_SCHEMA),
    "numeric_facts.parquet":        ("numeric_facts.parquet",        NUMERIC_FACTS_SCHEMA),
    "scientific_objects.parquet":   ("scientific_objects.parquet",   SCIENTIFIC_OBJECTS_SCHEMA),
    "object_graph.parquet":         ("object_graph.parquet",         OBJECT_GRAPH_SCHEMA),
    "semantic_annotations.parquet": ("semantic_annotations.parquet", SEMANTIC_ANNOTATIONS_SCHEMA),
    "semantic_edges.parquet":       ("semantic_edges.parquet",       SEMANTIC_EDGES_SCHEMA),
}


def _iter_paper_dirs(sodb_root: Path) -> Iterator[Path]:
    """Yield each paper subdirectory that contains at least one parquet file."""
    for d in sorted(sodb_root.iterdir()):
        if d.is_dir() and any(d.glob("*.parquet")):
            yield d


def _read_cast(path: Path, schema: pa.Schema) -> pa.Table | None:
    """Read a parquet file and cast it to the target schema, returning None on error."""
    try:
        tbl = pq.read_table(path)
    except Exception as exc:
        log.warning("skip %s — read error: %s", path, exc)
        return None
    # Cast column by column; add nulls for missing columns; drop extras.
    try:
        return _coerce_to_schema(tbl, schema)
    except Exception as exc:
        log.warning("skip %s — schema coercion failed: %s", path, exc)
        return None


def _coerce_to_schema(tbl: pa.Table, schema: pa.Schema) -> pa.Table:
    columns: dict[str, pa.ChunkedArray] = {}
    for field in schema:
        if field.name in tbl.schema.names:
            col = tbl.column(field.name)
            if col.type != field.type:
                col = col.cast(field.type, safe=False)
            columns[field.name] = col
        else:
            null_arr = pa.array([None] * len(tbl), type=field.type)
            columns[field.name] = pa.chunked_array([null_arr])
    return pa.table(columns, schema=schema)


def aggregate_table(
    output_name: str,
    per_paper_name: str,
    schema: pa.Schema,
    sodb_root: Path,
    output_dir: Path,
    rebuild: bool = False,
) -> dict:
    """Concatenate one table type across all papers and write to analytics dir."""
    out_path = output_dir / output_name
    tmp_path = out_path.with_suffix(".parquet.tmp")

    if out_path.exists() and not rebuild:
        try:
            existing = pq.read_table(out_path)
            log.info("%-30s already exists (%d rows) — skip", output_name, len(existing))
            return {"table": output_name, "status": "skipped", "rows": len(existing)}
        except Exception:
            log.warning("%-30s exists but unreadable — rebuilding", output_name)

    tables: list[pa.Table] = []
    paper_count = 0
    skipped = 0

    for paper_dir in _iter_paper_dirs(sodb_root):
        src = paper_dir / per_paper_name
        if not src.exists():
            continue
        tbl = _read_cast(src, schema)
        if tbl is None or len(tbl) == 0:
            skipped += 1
            continue
        tables.append(tbl)
        paper_count += 1

    if not tables:
        log.info("%-30s no data found", output_name)
        return {"table": output_name, "status": "empty", "rows": 0, "papers": 0}

    combined = pa.concat_tables(tables)
    output_dir.mkdir(parents=True, exist_ok=True)
    pq.write_table(combined, tmp_path, compression="snappy")
    tmp_path.rename(out_path)

    row_count = len(combined)
    log.info(
        "%-30s %6d rows from %d papers (%d skipped)",
        output_name, row_count, paper_count, skipped,
    )
    return {
        "table":   output_name,
        "status":  "ok",
        "rows":    row_count,
        "papers":  paper_count,
        "skipped": skipped,
    }


def run(
    sodb_root:  Path,
    output_dir: Path,
    rebuild:    bool = False,
    tables:     list[str] | None = None,
) -> dict:
    """
    Aggregate SODB parquets for the requested table names.

    Parameters
    ----------
    tables : list of output filenames to process, e.g. ["regions.parquet"].
             None means all.
    """
    target = _SODB_TABLES
    if tables:
        target = {k: v for k, v in _SODB_TABLES.items() if k in tables or k[:-8] in tables}
        if not target:
            log.error("No matching tables for: %s", tables)
            return {"status": "error", "detail": "no matching tables"}

    results = []
    for out_name, (per_paper_name, schema) in target.items():
        result = aggregate_table(
            output_name=out_name,
            per_paper_name=per_paper_name,
            schema=schema,
            sodb_root=sodb_root,
            output_dir=output_dir,
            rebuild=rebuild,
        )
        results.append(result)

    summary = {
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "sodb_root":    str(sodb_root),
        "output_dir":   str(output_dir),
        "tables":       results,
        "total_rows":   sum(r.get("rows", 0) for r in results),
    }

    summary_path = output_dir / "sodb_aggregator_summary.json"
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    except Exception as exc:
        log.warning("Could not write summary: %s", exc)

    return summary


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    parser = argparse.ArgumentParser(
        description="Aggregate per-paper SODB parquets into corpus-level analytics tables.",
    )
    parser.add_argument(
        "--sodb-dir", type=Path, default=_DEFAULT_SODB,
        help=f"Root of data/sodb/ tree (default: {_DEFAULT_SODB})",
    )
    parser.add_argument(
        "--output-dir", type=Path, default=_DEFAULT_OUT,
        help=f"Analytics output directory (default: {_DEFAULT_OUT})",
    )
    parser.add_argument(
        "--rebuild", action="store_true",
        help="Re-aggregate even if output files already exist",
    )
    parser.add_argument(
        "--tables", nargs="+", metavar="TABLE",
        help=(
            "Aggregate only these tables. "
            "Names without .parquet suffix are accepted. "
            "Available: " + ", ".join(k[:-8] for k in _SODB_TABLES)
        ),
    )
    args = parser.parse_args(argv)

    summary = run(
        sodb_root=args.sodb_dir,
        output_dir=args.output_dir,
        rebuild=args.rebuild,
        tables=args.tables,
    )

    ok_count = sum(1 for r in summary["tables"] if r["status"] == "ok")
    total    = len(summary["tables"])
    print(
        f"\nDone: {ok_count}/{total} tables written, "
        f"{summary['total_rows']:,} total rows → {args.output_dir}"
    )
    sys.exit(0 if ok_count > 0 or all(r["status"] in ("skipped", "empty") for r in summary["tables"]) else 1)


if __name__ == "__main__":
    main()
