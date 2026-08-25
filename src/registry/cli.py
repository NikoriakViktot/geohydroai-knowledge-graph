"""
cli.py  —  Registry CLI: inspect, query, export, report
=========================================================

Usage
-----
    # Show overall stats
    python -m src.registry.cli stats

    # Show all pending papers
    python -m src.registry.cli pending

    # Show retry candidates
    python -m src.registry.cli retries

    # Show failure report
    python -m src.registry.cli failures

    # Show runtime report
    python -m src.registry.cli runtime

    # Show full analytics report
    python -m src.registry.cli report [--json]

    # Export Parquet
    python -m src.registry.cli export [--statuses SUCCESS FAIL]

    # Reset stale workers
    python -m src.registry.cli reset-stale [--timeout 300]

    # Arbitrary SQL query
    python -m src.registry.cli query "SELECT paper_id, status FROM pipeline_registry LIMIT 10"

    # Bootstrap registry from XML directory (register all papers as NEW)
    python -m src.registry.cli bootstrap --xml-dir data/literature/grobid_xml
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
from pathlib import Path

from src.config.settings import REGISTRY_DIR, XML_DIR, PIPELINE_VERSION
from src.registry.registry_db import PipelineRegistry
from src.registry.analytics import RegistryAnalytics

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger(__name__)


def _get_registry(registry_dir: Path) -> PipelineRegistry:
    reg = PipelineRegistry(registry_dir, pipeline_version=PIPELINE_VERSION)
    reg.init_registry()
    return reg


def cmd_stats(args) -> None:
    with _get_registry(args.registry_dir) as reg:
        s = reg.stats_summary()
    print(f"  OK       : {s['ok']}")
    print(f"  FAIL     : {s['fail']}")
    print(f"  SKIPPED  : {s['skip']}")
    print(f"  PROCESSING: {s['proc']}")
    print(f"  PENDING  : {s['pend']}")
    total = sum(s.values())
    if total > 0:
        pct = round(100 * s["ok"] / total, 1)
        print(f"  Total    : {total}  (success rate: {pct}%)")


def cmd_pending(args) -> None:
    with _get_registry(args.registry_dir) as reg:
        papers = reg.get_unprocessed_papers(limit=args.limit)
    if not papers:
        print("No pending papers.")
        return
    print(f"{'paper_id':<40} {'status':<12} {'retries'}")
    print("-" * 60)
    for p in papers:
        print(f"{p['paper_id']:<40} {p['status']:<12} {p['retry_count']}")


def cmd_retries(args) -> None:
    with _get_registry(args.registry_dir) as reg:
        papers = reg.get_retry_candidates()
    if not papers:
        print("No retry candidates.")
        return
    for p in papers:
        print(f"  {p['paper_id']}  retries={p['retry_count']}  error={p['error_type']}  next_retry={p['next_retry_at']}")


def cmd_failures(args) -> None:
    with _get_registry(args.registry_dir) as reg:
        report = reg.build_failure_report()

    print(f"\nTotal failures : {report['total_failures']}")
    print(f"\nBy status:")
    for status, n in report["by_status"].items():
        print(f"  {status:<20} {n}")
    print(f"\nTop error types:")
    for e in report["by_error_type"][:10]:
        print(f"  {e['error_type']:<35} {e['count']:>5}  avg_retries={e['avg_retries']}")
    rstat = report["retry_statistics"]
    print(f"\nRetry stats:")
    print(f"  Papers retried     : {rstat['papers_retried']}")
    print(f"  Permanently failed : {rstat['permanently_failed']}")
    print(f"  Max retries seen   : {rstat['max_retries_seen']}")
    if args.verbose and report["permanently_failed_papers"]:
        print(f"\nPermanently failed papers:")
        for p in report["permanently_failed_papers"][:20]:
            print(f"  {p['paper_id']}  {p['error_type']}  {p['error_msg'][:60]}")


def cmd_runtime(args) -> None:
    with _get_registry(args.registry_dir) as reg:
        report = reg.build_runtime_report()

    rt = report["runtime"]
    print(f"\nTotal papers   : {report['total_papers']}")
    print(f"Success        : {report['success']}  ({report['success_rate_pct']}%)")
    print(f"Fail           : {report['fail']}")
    print(f"Skipped        : {report['skipped']}")
    print(f"Pending        : {report['pending']}")
    print(f"\nRuntime (successful papers):")
    print(f"  avg    : {rt['avg_sec']:.3f}s")
    print(f"  median : {rt['median_sec']:.3f}s")
    print(f"  p95    : {rt['p95_sec']:.3f}s")
    print(f"  min    : {rt['min_sec']:.3f}s")
    print(f"  max    : {rt['max_sec']:.3f}s")
    if report["slowest_papers"]:
        print(f"\nSlowest papers:")
        for p in report["slowest_papers"]:
            print(f"  {p['paper_id']:<40} {p['runtime_sec']:.2f}s")


def cmd_report(args) -> None:
    with _get_registry(args.registry_dir) as reg:
        ana = RegistryAnalytics(reg)
        report = ana.full_report()

    if args.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        print(json.dumps(report, indent=2, default=str))


def cmd_export(args) -> None:
    statuses = args.statuses or None
    with _get_registry(args.registry_dir) as reg:
        outputs = reg.export_parquet(statuses=statuses, overwrite=args.overwrite)
    for status, path in outputs.items():
        print(f"  {status:<15} → {path}")
    print(f"\nExported {len(outputs)} partition(s).")


def cmd_reset_stale(args) -> None:
    with _get_registry(args.registry_dir) as reg:
        n = reg.reset_stale_workers(timeout_sec=args.timeout)
    print(f"Reset {n} stale worker(s).")


def cmd_query(args) -> None:
    with _get_registry(args.registry_dir) as reg:
        rows = reg.query(args.sql)
    if not rows:
        print("(no results)")
        return
    # Print as simple table
    cols = list(rows[0].keys())
    widths = [max(len(c), max((len(str(r.get(c, ""))) for r in rows), default=0)) for c in cols]
    header = "  ".join(c.ljust(w) for c, w in zip(cols, widths))
    print(header)
    print("-" * len(header))
    for row in rows:
        print("  ".join(str(row.get(c, "")).ljust(w) for c, w in zip(cols, widths)))


def cmd_bootstrap(args) -> None:
    """Register all .tei.xml files in xml_dir as NEW papers."""
    xml_dir = Path(args.xml_dir)
    if not xml_dir.exists():
        print(f"ERROR: xml_dir not found: {xml_dir}", file=sys.stderr)
        sys.exit(1)

    xml_files = sorted(xml_dir.glob("*.tei.xml"))
    if not xml_files:
        print(f"No .tei.xml files found in {xml_dir}")
        return

    papers = []
    for f in xml_files:
        paper_id   = f.stem.replace(".tei", "")
        input_hash = _file_hash(f)
        papers.append({
            "paper_id":   paper_id,
            "source_xml": str(f),
            "input_hash": input_hash,
        })

    with _get_registry(args.registry_dir) as reg:
        inserted, skipped = reg.bulk_register(papers)

    print(f"Bootstrap complete: {inserted} registered, {skipped} already existed.")
    print(f"Run 'python -m src.registry.cli pending' to see the queue.")


def _file_hash(path: Path) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


# ─────────────────────────────────────────────────────────────────────────────
# Argument parser
# ─────────────────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m src.registry.cli",
        description="GeoHydroAI pipeline registry CLI",
    )
    p.add_argument(
        "--registry-dir",
        type=Path,
        default=REGISTRY_DIR,
        help=f"Registry directory (default: {REGISTRY_DIR})",
    )
    sub = p.add_subparsers(dest="command", required=True)

    # stats
    sub.add_parser("stats", help="Show overall pipeline statistics")

    # pending
    pend = sub.add_parser("pending", help="Show pending papers")
    pend.add_argument("--limit", type=int, default=50)

    # retries
    sub.add_parser("retries", help="Show retry candidates")

    # failures
    fail = sub.add_parser("failures", help="Show failure report")
    fail.add_argument("-v", "--verbose", action="store_true")

    # runtime
    sub.add_parser("runtime", help="Show runtime statistics")

    # report
    rep = sub.add_parser("report", help="Show full analytics report")
    rep.add_argument("--json", action="store_true", help="Output as JSON")

    # export
    exp = sub.add_parser("export", help="Export registry to Parquet")
    exp.add_argument("--statuses", nargs="+", help="Specific statuses to export")
    exp.add_argument("--overwrite", action="store_true", default=True)

    # reset-stale
    rst = sub.add_parser("reset-stale", help="Reset stale PROCESSING papers to RETRY")
    rst.add_argument("--timeout", type=int, default=300, help="Heartbeat timeout in seconds")

    # query
    qry = sub.add_parser("query", help="Run arbitrary SQL query")
    qry.add_argument("sql", help="SELECT statement to execute")

    # bootstrap
    boot = sub.add_parser("bootstrap", help="Register all XML files as NEW")
    boot.add_argument("--xml-dir", type=str, default=str(XML_DIR))

    return p


def main() -> None:
    parser = build_parser()
    args   = parser.parse_args()

    dispatch = {
        "stats":       cmd_stats,
        "pending":     cmd_pending,
        "retries":     cmd_retries,
        "failures":    cmd_failures,
        "runtime":     cmd_runtime,
        "report":      cmd_report,
        "export":      cmd_export,
        "reset-stale": cmd_reset_stale,
        "query":       cmd_query,
        "bootstrap":   cmd_bootstrap,
    }

    fn = dispatch.get(args.command)
    if fn is None:
        parser.print_help()
        sys.exit(1)

    fn(args)


if __name__ == "__main__":
    main()
