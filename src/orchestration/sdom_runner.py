"""
sdom_runner.py — SDOM Pipeline CLI Orchestrator (Stage 0 → 1 → 2 → 2.5)
=========================================================================

Runs the full SDOM pipeline on every PDF in data/literature/pdf/.

Stages (each stage is idempotent — skips papers already complete):
  Stage 0 : SHA-256 identity + PDF triage  → data/raw/{paper_id}/
  Stage 1 : Structural parsing             → data/parsed/{paper_id}/
  Stage 2 : Scientific object engineering  → data/sodb/{paper_id}/
  Stage 2.5: Semantic validation           → data/sodb/{paper_id}/

Design rules (CLAUDE.md invariants):
  - Never imports lxml or fitz directly
  - Never mixes XML parsing, OpenAlex HTTP, or Neo4j writes in one call
  - TOKENIZERS_PARALLELISM=false / RAYON_NUM_THREADS=1 must be set by caller
  - ThreadPoolExecutor only (no Ray) — stages have no GPU actors

Usage:
    PARSER_STRATEGY=grobid_only \\
    TOKENIZERS_PARALLELISM=false \\
    RAYON_NUM_THREADS=1 \\
    python -m src.orchestration.sdom_runner --workers 3

    # Only a specific stage range
    python -m src.orchestration.sdom_runner --workers 3 --stage 2.5

    # Test on first 10 PDFs
    python -m src.orchestration.sdom_runner --workers 1 --limit 10
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable

log = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Stage dispatch
# ─────────────────────────────────────────────────────────────────────────────

_STAGES = ("0", "1", "2", "2.5")


def _run_paper(
    pdf_path: Path,
    run_stage0: bool,
    run_stage1: bool,
    run_stage2: bool,
    run_stage25: bool,
    force: bool,
) -> dict:
    """
    Process one PDF through the requested SDOM stages.

    Returns a summary dict with status per stage.
    """
    from src.ingestion.stage0.ingestor import Stage0Ingestor
    from src.ingestion.stage1.parser_runner import Stage1Parser
    from src.ingestion.stage2.engineer import Stage2Engineer
    from src.semantic_objects.semantic_validator import SemanticValidatorStage

    result: dict = {"pdf": pdf_path.name, "paper_id": None}

    # ── Stage 0 ──────────────────────────────────────────────────────────────
    s0_result = None
    if run_stage0:
        s0 = Stage0Ingestor(force=force)
        s0_result = s0.run(pdf_path)
        result["s0"] = s0_result.status
        result["paper_id"] = s0_result.paper_id or None

        if s0_result.status == "error":
            return result
    else:
        # Infer paper_id from existing Stage 0 artifacts without re-running
        from src.config.settings import RAW_DIR
        from src.ingestion.pdf_triage import sha256_pdf
        try:
            h = sha256_pdf(pdf_path)
            result["paper_id"] = h.sha256
            # Reconstruct a minimal Stage0Result for downstream stages
            from src.ingestion.stage0.ingestor import Stage0Result
            raw_root = RAW_DIR / h.sha256
            s0_result = Stage0Result(
                paper_id=h.sha256, status="skipped",
                checksum=h.sha256, raw_root=raw_root,
                triage=None, events=["runner_skip_stage0"],
            )
        except Exception as exc:
            log.warning("[stage0-skip] sha256 failed for %s: %s", pdf_path.name, exc)
            result["s0"] = f"error:{exc}"
            return result

    paper_id = result["paper_id"]
    if not paper_id:
        return result

    # ── Stage 1 ──────────────────────────────────────────────────────────────
    s1_result = None
    if run_stage1:
        s1 = Stage1Parser(force=force)
        s1_result = s1.run(s0_result)
        result["s1"] = s1_result.status

        if s1_result.status == "error":
            return result
    else:
        from src.ingestion.stage1.parser_runner import Stage1Result
        from src.config.settings import PARSED_DIR
        s1_result = Stage1Result(
            paper_id=paper_id, status="skipped",
            parsed_root=PARSED_DIR / paper_id,
            events=["runner_skip_stage1"],
        )

    # ── Stage 2 ──────────────────────────────────────────────────────────────
    s2_result = None
    if run_stage2:
        s2 = Stage2Engineer(force=force)
        s2_result = s2.run(s1_result)
        result["s2"] = s2_result.status

        if s2_result.status == "error":
            return result
    else:
        from src.ingestion.stage2.engineer import Stage2Result
        from src.config.settings import SODB_DIR as _SODB_DIR
        s2_result = Stage2Result(
            paper_id=paper_id, status="skipped",
            sodb_root=_SODB_DIR / paper_id,
            events=["runner_skip_stage2"],
        )

    # ── Stage 2.5 ─────────────────────────────────────────────────────────────
    if run_stage25:
        sv = SemanticValidatorStage(force=force)
        sv_result = sv.run(paper_id)
        result["s25"] = sv_result.status

    return result


# ─────────────────────────────────────────────────────────────────────────────
# Orchestrator
# ─────────────────────────────────────────────────────────────────────────────

def run_sdom_pipeline(
    pdf_dir: Path,
    workers: int = 3,
    stage: str = "0-2.5",
    limit: int | None = None,
    force: bool = False,
) -> Counter:
    """
    Run SDOM pipeline on all PDFs in pdf_dir.

    Parameters
    ----------
    pdf_dir : Directory containing input PDFs.
    workers : ThreadPoolExecutor max_workers.
    stage   : Which stages to run. Examples: "0-2.5", "2.5", "1-2", "0".
    limit   : If set, process only the first N PDFs (for testing).
    force   : Pass force=True to all stage instances (re-process completed stages).

    Returns
    -------
    Counter with status keys: ok, skipped, error, partial.
    """
    # Parse stage range
    run_stage0, run_stage1, run_stage2, run_stage25 = _parse_stages(stage)

    pdfs = sorted(pdf_dir.glob("*.pdf"))
    if limit:
        pdfs = pdfs[:limit]

    log.info(
        "SDOM runner: %d PDFs | workers=%d | stage=%s | force=%s",
        len(pdfs), workers, stage, force,
    )

    stats: Counter = Counter()
    done = 0

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(
                _run_paper, pdf,
                run_stage0, run_stage1, run_stage2, run_stage25, force,
            ): pdf
            for pdf in pdfs
        }

        for fut in as_completed(futures):
            done += 1
            pdf = futures[fut]
            try:
                res = fut.result()
            except Exception as exc:
                log.error("[runner] unhandled exception for %s: %s", pdf.name, exc)
                stats["error"] += 1
                continue

            # Determine overall status
            stage_statuses = [v for k, v in res.items() if k.startswith("s")]
            if any(s == "error" for s in stage_statuses):
                stats["error"] += 1
            elif all(s == "skipped" for s in stage_statuses):
                stats["skipped"] += 1
            else:
                stats["ok"] += 1

            if done % 100 == 0 or done == len(pdfs):
                log.info(
                    "Progress %d/%d  ok=%d skipped=%d error=%d",
                    done, len(pdfs), stats["ok"], stats["skipped"], stats["error"],
                )

    log.info(
        "DONE  total=%d  ok=%d  skipped=%d  error=%d",
        len(pdfs), stats["ok"], stats["skipped"], stats["error"],
    )
    return stats


# ─────────────────────────────────────────────────────────────────────────────
# Stage range parser
# ─────────────────────────────────────────────────────────────────────────────

def _parse_stages(stage: str) -> tuple[bool, bool, bool, bool]:
    """
    Parse stage range string into (run_stage0, run_stage1, run_stage2, run_stage25).

    Examples:
        "0-2.5"  → (True, True, True, True)
        "2.5"    → (False, False, False, True)
        "1-2"    → (False, True, True, False)
        "0"      → (True, False, False, False)
    """
    s = stage.strip()

    if "-" in s:
        parts = s.split("-", 1)
        start, end = parts[0].strip(), parts[1].strip()
        order = ["0", "1", "2", "2.5"]
        try:
            si, ei = order.index(start), order.index(end)
        except ValueError:
            raise ValueError(f"Invalid stage range: {stage!r}. Valid stages: {_STAGES}")
        active = set(order[si : ei + 1])
    else:
        if s not in _STAGES:
            raise ValueError(f"Invalid stage: {stage!r}. Valid stages: {_STAGES}")
        active = {s}

    return "0" in active, "1" in active, "2" in active, "2.5" in active


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    parser = argparse.ArgumentParser(
        description="SDOM pipeline runner (Stage 0 → 1 → 2 → 2.5)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Full pipeline, 3 workers
  PARSER_STRATEGY=grobid_only TOKENIZERS_PARALLELISM=false \\
  python -m src.orchestration.sdom_runner --workers 3

  # Only Stage 2.5 (semantic validation), re-validate all
  python -m src.orchestration.sdom_runner --stage 2.5 --force

  # Test run: first 10 PDFs, stage 0-1 only
  python -m src.orchestration.sdom_runner --workers 1 --stage 0-1 --limit 10
""",
    )
    parser.add_argument(
        "--workers", type=int, default=3,
        help="Parallel workers (default: 3; stable limit on this WSL2 system)",
    )
    parser.add_argument(
        "--stage", default="0-2.5",
        help="Stage range to run: e.g. '0-2.5', '2.5', '1-2' (default: 0-2.5)",
    )
    parser.add_argument(
        "--limit", type=int, default=None,
        help="Process only first N PDFs (for testing)",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Re-process papers even if their stage is already complete",
    )
    parser.add_argument(
        "--pdf-dir", type=Path, default=None,
        help="Override PDF input directory (default: data/literature/pdf/)",
    )
    args = parser.parse_args()

    from src.config.settings import PDF_DIR as _PDF_DIR
    pdf_dir = args.pdf_dir or _PDF_DIR

    if not pdf_dir.exists():
        log.error("PDF directory not found: %s", pdf_dir)
        sys.exit(1)

    stats = run_sdom_pipeline(
        pdf_dir=pdf_dir,
        workers=args.workers,
        stage=args.stage,
        limit=args.limit,
        force=args.force,
    )

    sys.exit(0 if stats["error"] == 0 else 1)
