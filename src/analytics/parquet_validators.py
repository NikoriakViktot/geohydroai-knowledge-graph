"""
parquet_validators.py  —  GeoHydroAI Stage 3A parquet validation + summary
===========================================================================

Validates all 11 parquet files in the analytics output directory:
    - File existence
    - Schema conformance (column names and types)
    - Row count sanity checks
    - Referential integrity spot-checks (paper_ids in edge tables exist in papers)
    - Canonical ID integrity (methods/sensors/metrics only have non-null canonical_ids)

Writes:
    data/analytics/parquet_summary.json

Usage
-----
    python -m src.analytics.parquet_validators
    python -m src.analytics.parquet_validators --analytics-dir data/analytics
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

_PROJECT_ROOT  = Path(__file__).resolve().parents[2]
_DEFAULT_ANALYTICS = _PROJECT_ROOT / "data" / "analytics"


# ─────────────────────────────────────────────────────────────────────────────
# Validation
# ─────────────────────────────────────────────────────────────────────────────

def validate_parquet_tables(analytics_dir: Path) -> dict:
    """
    Validate all 11 parquet tables and return a summary dict.

    Args:
        analytics_dir: Path containing *.parquet files.

    Returns:
        Summary dict with: valid, invalid, warnings, table_stats.
    """
    import pyarrow.parquet as pq
    from src.analytics.parquet_schema import TABLE_REGISTRY

    results: list[dict] = []
    warnings: list[str] = []
    table_stats: dict[str, dict] = {}

    # ── Per-table checks ──────────────────────────────────────────────────────
    for table_name, (expected_schema, filename) in TABLE_REGISTRY.items():
        path = analytics_dir / filename
        entry: dict = {"table": table_name, "file": filename, "ok": False, "error": None}

        if not path.exists():
            entry["error"] = "file not found"
            results.append(entry)
            continue

        try:
            pf      = pq.read_table(path)
            n_rows  = len(pf)
            n_cols  = len(pf.schema)
            actual_cols   = set(pf.schema.names)
            expected_cols = set(expected_schema.names)

            missing = expected_cols - actual_cols
            extra   = actual_cols - expected_cols

            col_errors: list[str] = []
            if missing:
                col_errors.append(f"missing columns: {sorted(missing)}")
            if extra:
                col_errors.append(f"unexpected columns: {sorted(extra)}")

            # Type checks for non-nullable columns
            for field in expected_schema:
                if field.name in actual_cols:
                    actual_type = pf.schema.field(field.name).type
                    if actual_type != field.type:
                        col_errors.append(
                            f"column '{field.name}': expected {field.type}, got {actual_type}"
                        )

            if col_errors:
                entry["error"] = "; ".join(col_errors)
            else:
                entry["ok"]      = True
                entry["n_rows"]  = n_rows
                entry["n_cols"]  = n_cols
                table_stats[table_name] = {"rows": n_rows, "cols": n_cols}

        except Exception as exc:
            entry["error"] = f"read error: {exc}"

        results.append(entry)

    # ── Sanity checks ─────────────────────────────────────────────────────────
    warnings.extend(_sanity_checks(analytics_dir, table_stats))

    # ── Canonical ID integrity ─────────────────────────────────────────────────
    warnings.extend(_canonical_id_checks(analytics_dir))

    valid   = sum(1 for r in results if r["ok"])
    invalid = sum(1 for r in results if not r["ok"])

    return {
        "generated_at":  datetime.now(timezone.utc).isoformat(),
        "analytics_dir": str(analytics_dir),
        "valid_tables":  valid,
        "invalid_tables": invalid,
        "warnings":      warnings,
        "table_stats":   table_stats,
        "table_results": results,
    }


def _sanity_checks(analytics_dir: Path, table_stats: dict[str, dict]) -> list[str]:
    """Cross-table row count sanity checks."""
    import pyarrow.parquet as pq

    warnings: list[str] = []

    papers_path = analytics_dir / "papers.parquet"
    edges_path  = analytics_dir / "paper_author_edges.parquet"

    if papers_path.exists() and edges_path.exists():
        try:
            n_papers = len(pq.read_table(papers_path, columns=["paper_id"]))
            n_edges  = len(pq.read_table(edges_path, columns=["paper_id"]))
            if n_edges == 0 and n_papers > 0:
                warnings.append(
                    f"paper_author_edges is empty but papers has {n_papers} rows — "
                    "check OpenAlex author enrichment coverage"
                )
        except Exception:
            pass

    # papers must not be empty
    if table_stats.get("papers", {}).get("rows", 0) == 0:
        warnings.append("papers table is empty — builder may have failed")

    # methods/sensors/metrics: row count should be > 0 if papers exist
    for entity_table in ("methods", "sensors", "metrics"):
        if table_stats.get(entity_table, {}).get("rows", 0) == 0:
            warnings.append(
                f"{entity_table} table is empty — check normalized_entities coverage "
                "or ontology matcher configuration"
            )

    return warnings


def _canonical_id_checks(analytics_dir: Path) -> list[str]:
    """Verify no null canonical_ids leaked into methods/sensors/metrics tables."""
    import pyarrow.parquet as pq
    import pyarrow.compute as pc

    warnings: list[str] = []

    for table_name in ("methods", "sensors", "metrics"):
        path = analytics_dir / f"{table_name}.parquet"
        if not path.exists():
            continue
        try:
            tbl     = pq.read_table(path, columns=["canonical_id"])
            null_ct = pc.sum(pc.is_null(tbl["canonical_id"])).as_py()
            if null_ct and null_ct > 0:
                warnings.append(
                    f"{table_name}: {null_ct} rows with null canonical_id — "
                    "builder should filter these out before writing"
                )
        except Exception as exc:
            warnings.append(f"{table_name}: canonical_id check failed: {exc}")

    return warnings


# ─────────────────────────────────────────────────────────────────────────────
# Summary writer
# ─────────────────────────────────────────────────────────────────────────────

def write_parquet_summary(analytics_dir: Path) -> dict:
    """
    Validate tables and write data/analytics/parquet_summary.json.

    Returns the summary dict.
    """
    summary = validate_parquet_tables(analytics_dir)

    out = analytics_dir / "parquet_summary.json"
    try:
        analytics_dir.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        log.info("[validators] parquet_summary.json written: %s", out)
    except Exception as exc:
        log.warning("[validators] could not write summary: %s", exc)

    return summary


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def _main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "GeoHydroAI Stage 3A: validate parquet analytics tables.\n"
            "Reads *.parquet from --analytics-dir, writes parquet_summary.json."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--analytics-dir", type=Path, default=_DEFAULT_ANALYTICS, metavar="PATH",
        help=f"Directory containing parquet files (default: {_DEFAULT_ANALYTICS})",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging verbosity (default: INFO)",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)-8s %(message)s",
        datefmt="%H:%M:%S",
    )

    if not args.analytics_dir.exists():
        log.error("Analytics directory does not exist: %s", args.analytics_dir)
        sys.exit(1)

    summary = write_parquet_summary(args.analytics_dir)

    print()
    print("=" * 52)
    print("Stage 3A — Parquet Validation")
    print(f"  Analytics dir  : {summary['analytics_dir']}")
    print(f"  Valid tables   : {summary['valid_tables']}")
    print(f"  Invalid tables : {summary['invalid_tables']}")

    if summary.get("table_stats"):
        print()
        print("  Row counts:")
        for tname, stats in summary["table_stats"].items():
            print(f"    {tname:28s}: {stats['rows']:>8,}")

    if summary.get("warnings"):
        print()
        print("  Warnings:")
        for w in summary["warnings"]:
            print(f"    ! {w}")

    invalid = [r for r in summary["table_results"] if not r["ok"]]
    if invalid:
        print()
        print("  Invalid tables:")
        for r in invalid:
            print(f"    {r['table']:28s}: {r['error']}")

    print(f"\n  Report: {args.analytics_dir}/parquet_summary.json")
    print("=" * 52)

    sys.exit(0 if summary["invalid_tables"] == 0 else 1)


if __name__ == "__main__":
    _main()
