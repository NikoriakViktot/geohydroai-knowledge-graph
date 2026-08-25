"""
normalization_runner.py  —  GeoHydroAI Stage 2 normalization backfill pipeline
===============================================================================

Reads existing *.paper.json files produced by the Stage 1 Ray ingestion
pipeline and runs ontology normalisation over them — without touching any
ingestion-layer infrastructure.

Architecture
------------
Stage 1 (completed, do not re-run):
    TEI XML → SpaCy / embeddings / Ollama → *.paper.json

Stage 2 (this module):
    *.paper.json → ontology normalisation → data/normalized/*.json

Stage 3 (downstream consumers):
    data/normalized/*.json → QA / analytics / Neo4j / parquet / OpenAlex

Design principles
-----------------
- NEVER imports lxml, Ray, SpaCy, embedding models, or Ollama clients.
- NEVER overwrites *.paper.json source files.
- Idempotent: skips already-normalised files unless --overwrite is given.
- Safe: per-file failures are logged and counted; the run never crashes.
- Reuses normalisation logic from src.normalization.normalization_utils
  (single source of truth, shared with process_paper.py).

Usage
-----
    python -m src.orchestration.normalization_runner
    python -m src.orchestration.normalization_runner --limit 100
    python -m src.orchestration.normalization_runner --overwrite
    python -m src.orchestration.normalization_runner \\
        --input-dir data/becap_json/paper_json \\
        --output-dir data/normalized

Flags
-----
    --input-dir PATH    Source *.paper.json directory  (default: settings.OUT_DIR)
    --output-dir PATH   Normalized output directory    (default: data/normalized/)
    --overwrite         Overwrite existing normalized files
    --limit N           Stop after N files (useful for smoke tests)
    --fail-fast         Abort on first per-file error
    --log-level LEVEL   Logging verbosity (default: INFO)
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

_PROJECT_ROOT   = Path(__file__).resolve().parents[2]
_DEFAULT_OUT    = _PROJECT_ROOT / "data" / "normalized"
_QA_DIR         = _PROJECT_ROOT / "data" / "ontology_qa"
_SCHEMA_VERSION = "1.0"


# ─────────────────────────────────────────────────────────────────────────────
# Lazy settings import (avoids pulling Ray/lxml at module level)
# ─────────────────────────────────────────────────────────────────────────────

def _default_input_dir() -> Path:
    try:
        from src.config.settings import OUT_DIR
        return Path(OUT_DIR)
    except Exception:
        return _PROJECT_ROOT / "data" / "literature" / "paper_json"


# ─────────────────────────────────────────────────────────────────────────────
# Per-file processing
# ─────────────────────────────────────────────────────────────────────────────

def _load_paper(path: Path) -> Optional[dict]:
    """Load a *.paper.json file safely. Returns None on error."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        log.error("[normalize] failed — %s: %s", path.name, exc)
        return None


def process_one(
    source_path: Path,
    output_dir: Path,
    overwrite: bool = False,
) -> dict:
    """
    Normalise a single *.paper.json and write to output_dir.

    Args:
        source_path: Path to the *.paper.json source file.
        output_dir:  Target directory for normalised output.
        overwrite:   If False, skip when output already exists.

    Returns:
        Status dict:
            status:  "ok" | "skipped" | "load_error" | "normalize_error" | "save_error"
            paper_id: str
            output_path: str | None
            schema_valid: bool | None
            elapsed_s: float
    """
    from src.normalization.normalization_utils import (
        normalize_paper_entities,
        validate_normalized_schema,
        save_normalized,
        derive_output_path,
    )

    t0 = time.monotonic()

    # ── Determine output path before loading ─────────────────────────────────
    # We peek at the filename to guess paper_id so we can check skip condition
    # without parsing the full JSON.
    stem = source_path.name
    for suffix in (".tei.paper.json", ".paper.json", ".tei.json", ".json"):
        if stem.endswith(suffix):
            stem = stem[: -len(suffix)]
            break
    tentative_out = output_dir / f"{stem}.json"

    if tentative_out.exists() and not overwrite:
        log.info("[normalize] skipped existing — %s", source_path.name)
        return {
            "status":       "skipped",
            "paper_id":     stem,
            "output_path":  str(tentative_out),
            "schema_valid": None,
            "elapsed_s":    round(time.monotonic() - t0, 4),
        }

    # ── Load ─────────────────────────────────────────────────────────────────
    paper = _load_paper(source_path)
    if paper is None:
        return {
            "status":       "load_error",
            "paper_id":     stem,
            "output_path":  None,
            "schema_valid": None,
            "elapsed_s":    round(time.monotonic() - t0, 4),
        }

    paper_id = paper.get("metadata", {}).get("paper_id") or stem
    log.info("[normalize] loaded — %s", paper_id)

    # ── Stamp schema version ──────────────────────────────────────────────────
    paper["schema_version"] = _SCHEMA_VERSION

    # ── Normalise ─────────────────────────────────────────────────────────────
    try:
        paper = normalize_paper_entities(paper)
    except Exception as exc:
        log.error("[normalize] normalize_paper_entities failed — %s: %s", paper_id, exc)
        return {
            "status":       "normalize_error",
            "paper_id":     paper_id,
            "output_path":  None,
            "schema_valid": None,
            "elapsed_s":    round(time.monotonic() - t0, 4),
        }

    log.info("[normalize] normalized — %s", paper_id)

    # ── Validate ──────────────────────────────────────────────────────────────
    schema_valid = validate_normalized_schema(paper)
    if schema_valid:
        log.info("[normalize] schema valid — %s", paper_id)
    else:
        log.warning("[normalize] schema invalid — %s", paper_id)

    # ── Save ──────────────────────────────────────────────────────────────────
    try:
        out = save_normalized(paper, output_dir, source_path)
    except Exception as exc:
        log.error("[normalize] save failed — %s: %s", paper_id, exc)
        return {
            "status":       "save_error",
            "paper_id":     paper_id,
            "output_path":  None,
            "schema_valid": schema_valid,
            "elapsed_s":    round(time.monotonic() - t0, 4),
        }

    elapsed = round(time.monotonic() - t0, 4)
    log.info("[normalize] saved (%ss) — %s → %s", elapsed, paper_id, out.name)

    return {
        "status":       "ok",
        "paper_id":     paper_id,
        "output_path":  str(out),
        "schema_valid": schema_valid,
        "elapsed_s":    elapsed,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Batch runner
# ─────────────────────────────────────────────────────────────────────────────

def run_backfill(
    input_dir: Path,
    output_dir: Path,
    overwrite: bool = False,
    limit: Optional[int] = None,
    fail_fast: bool = False,
) -> dict:
    """
    Normalise all *.paper.json files in input_dir.

    Args:
        input_dir:  Source directory containing *.paper.json files.
        output_dir: Destination directory for normalised output.
        overwrite:  Overwrite existing normalised files.
        limit:      Stop after this many files (None = unlimited).
        fail_fast:  Raise immediately on first error.

    Returns:
        Summary dict suitable for JSON serialisation.
    """
    source_files = sorted(input_dir.glob("*.paper.json"))

    if not source_files:
        log.warning("No *.paper.json files found in %s", input_dir)
        return _empty_summary(input_dir, output_dir)

    if limit is not None:
        source_files = source_files[:limit]

    log.info(
        "Starting normalisation backfill: %d files, input=%s, output=%s, overwrite=%s",
        len(source_files), input_dir, output_dir, overwrite,
    )

    results: list[dict] = []
    counts = {
        "total":          len(source_files),
        "success":        0,
        "skipped":        0,
        "schema_invalid": 0,
        "load_error":     0,
        "normalize_error": 0,
        "save_error":     0,
    }

    t_start = time.monotonic()

    for i, path in enumerate(source_files, 1):
        result = process_one(path, output_dir, overwrite=overwrite)
        results.append(result)

        status = result["status"]
        if status == "ok":
            counts["success"] += 1
            if result.get("schema_valid") is False:
                counts["schema_invalid"] += 1
        elif status == "skipped":
            counts["skipped"] += 1
        else:
            counts[status] = counts.get(status, 0) + 1

        if i % 100 == 0 or i == len(source_files):
            elapsed = time.monotonic() - t_start
            rate    = i / elapsed if elapsed > 0 else 0
            log.info(
                "Progress: %d/%d  success=%d  skipped=%d  errors=%d  (%.1f/s)",
                i, len(source_files),
                counts["success"], counts["skipped"],
                counts["load_error"] + counts["normalize_error"] + counts["save_error"],
                rate,
            )

        is_error = status in ("load_error", "normalize_error", "save_error")
        if fail_fast and is_error:
            log.error("--fail-fast: aborting after first error on %s", path.name)
            break

    total_elapsed = round(time.monotonic() - t_start, 2)
    failed = counts["load_error"] + counts["normalize_error"] + counts["save_error"]

    summary = {
        "generated_at":   datetime.now(timezone.utc).isoformat(),
        "input_dir":      str(input_dir),
        "output_dir":     str(output_dir),
        "overwrite":      overwrite,
        "limit":          limit,
        "elapsed_s":      total_elapsed,
        "rate_per_s":     round(len(results) / total_elapsed, 2) if total_elapsed > 0 else 0,
        "total":          counts["total"],
        "processed":      len(results),
        "success":        counts["success"],
        "skipped":        counts["skipped"],
        "schema_invalid": counts["schema_invalid"],
        "failed":         failed,
        "failed_detail": {
            "load_error":      counts["load_error"],
            "normalize_error": counts["normalize_error"],
            "save_error":      counts["save_error"],
        },
    }

    _write_summary(summary)
    return summary


def _empty_summary(input_dir: Path, output_dir: Path) -> dict:
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "input_dir":    str(input_dir),
        "output_dir":   str(output_dir),
        "total": 0, "processed": 0, "success": 0,
        "skipped": 0, "schema_invalid": 0, "failed": 0,
    }


def _write_summary(summary: dict) -> None:
    try:
        _QA_DIR.mkdir(parents=True, exist_ok=True)
        out = _QA_DIR / "normalization_backfill_summary.json"
        out.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
        log.info("Backfill summary written: %s", out)
    except Exception as exc:
        log.warning("Could not write summary: %s", exc)


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "GeoHydroAI Stage 2: normalise existing *.paper.json files.\n"
            "Reads from --input-dir, writes to --output-dir/.\n"
            "Does NOT re-run XML parsing, NER, embeddings, or Ollama."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--input-dir", type=Path, default=None, metavar="PATH",
        help="Source directory of *.paper.json files (default: settings.OUT_DIR)",
    )
    parser.add_argument(
        "--output-dir", type=Path, default=_DEFAULT_OUT, metavar="PATH",
        help=f"Output directory for normalised JSON (default: {_DEFAULT_OUT})",
    )
    parser.add_argument(
        "--overwrite", action="store_true",
        help="Overwrite already-normalised files (default: skip)",
    )
    parser.add_argument(
        "--limit", type=int, default=None, metavar="N",
        help="Process only the first N files",
    )
    parser.add_argument(
        "--fail-fast", action="store_true",
        help="Abort on first per-file error",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging verbosity (default: INFO)",
    )
    return parser


def _main() -> None:
    parser = _build_parser()
    args   = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)-8s %(message)s",
        datefmt="%H:%M:%S",
    )

    input_dir  = args.input_dir or _default_input_dir()
    output_dir = args.output_dir

    if not input_dir.exists():
        log.error("Input directory does not exist: %s", input_dir)
        sys.exit(1)

    log.info("=" * 60)
    log.info("GeoHydroAI Normalization Backfill")
    log.info("  Input  : %s", input_dir)
    log.info("  Output : %s", output_dir)
    log.info("  Overwrite: %s  Limit: %s  Fail-fast: %s",
             args.overwrite, args.limit, args.fail_fast)
    log.info("=" * 60)

    summary = run_backfill(
        input_dir  = input_dir,
        output_dir = output_dir,
        overwrite  = args.overwrite,
        limit      = args.limit,
        fail_fast  = args.fail_fast,
    )

    # ── Terminal report ──────────────────────────────────────────────────────
    print()
    print("=" * 50)
    print("Normalization Backfill Complete")
    print(f"  Input dir      : {summary['input_dir']}")
    print(f"  Output dir     : {summary['output_dir']}")
    print(f"  Total files    : {summary['total']}")
    print(f"  Processed      : {summary['processed']}")
    print(f"  Success        : {summary['success']}")
    print(f"  Skipped        : {summary['skipped']}")
    print(f"  Schema invalid : {summary['schema_invalid']}")
    print(f"  Failed         : {summary['failed']}")
    if summary.get("failed_detail"):
        for k, v in summary["failed_detail"].items():
            if v:
                print(f"    {k:20s}: {v}")
    print(f"  Elapsed        : {summary.get('elapsed_s', 0):.1f}s")
    print(f"  Rate           : {summary.get('rate_per_s', 0):.1f} papers/s")

    # ── Semantic-tier телеметрія (Фаза 2.5) ──────────────────────────────────
    # Прогін зі зламаною embedding-моделлю мовчки змінює розподіл match_type —
    # снапшот робить деградацію видимою.
    try:
        from src.normalization.embedding_matcher import telemetry_snapshot
        tier = telemetry_snapshot()
        if tier:
            print("  Semantic-tier degradations:")
            for reason, n in sorted(tier.items(), key=lambda kv: -kv[1]):
                print(f"    {reason:28s}: {n}")
            if tier.get("model_load_failed") or tier.get("model_fallback_tier"):
                print("  ⚠  Embedding model деградувала — semantic match_type "
                      "розподіл цього прогону НЕ зіставний з попередніми!")
    except ImportError:
        pass

    print("=" * 50)
    print(f"Report: data/ontology_qa/normalization_backfill_summary.json")

    sys.exit(1 if summary.get("failed", 0) > 0 else 0)


if __name__ == "__main__":
    _main()
