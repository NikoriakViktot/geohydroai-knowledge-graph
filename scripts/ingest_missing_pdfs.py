"""
Batch GROBID ingest for data/literature/pdf_missing/*.pdf

For every PDF in pdf_missing that does NOT already have a .tei.xml in
data/literature/grobid_xml/, submits it to GROBID and writes the XML.
After the GROBID pass, hands off to pipeline_runner so the new XMLs are
processed exactly like the original corpus papers.

Usage:
    # Step 1 — GROBID only (produces .tei.xml files)
    .venv/bin/python3 scripts/ingest_missing_pdfs.py --grobid-only

    # Step 2 — pipeline only (runs on already-produced XMLs)
    TOKENIZERS_PARALLELISM=false RAYON_NUM_THREADS=1 OMP_NUM_THREADS=2 \\
    OLLAMA_MODEL=mistral-nemo:12b OLLAMA_URL=http://localhost:11434 \\
    SPACY_MODEL=en_core_web_sm \\
    .venv/bin/python3 scripts/ingest_missing_pdfs.py --pipeline-only --workers 3

    # Both steps in sequence
    TOKENIZERS_PARALLELISM=false RAYON_NUM_THREADS=1 OMP_NUM_THREADS=2 \\
    OLLAMA_MODEL=mistral-nemo:12b OLLAMA_URL=http://localhost:11434 \\
    SPACY_MODEL=en_core_web_sm \\
    .venv/bin/python3 scripts/ingest_missing_pdfs.py --workers 3

    # Dry run — show what would be done
    .venv/bin/python3 scripts/ingest_missing_pdfs.py --dry-run
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

# ─── project root on sys.path ─────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.ingestion.grobid_client import GROBIDClient

PDF_MISSING_DIR = PROJECT_ROOT / "data" / "literature" / "pdf_missing"
GROBID_XML_DIR  = PROJECT_ROOT / "data" / "literature" / "grobid_xml"
PAPER_JSON_DIR  = PROJECT_ROOT / "data" / "literature" / "paper_json"
INGEST_LOG      = PROJECT_ROOT / "data" / "ingestion_logs" / "missing_pdf_grobid.jsonl"

GROBID_XML_DIR.mkdir(parents=True, exist_ok=True)
INGEST_LOG.parent.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


# ─── helpers ──────────────────────────────────────────────────────────────────

def _xml_name(pdf_path: Path) -> str:
    """Return the .tei.xml filename that corresponds to this PDF."""
    return pdf_path.stem + ".tei.xml"


def _paper_json_name(pdf_path: Path) -> str:
    return pdf_path.stem + ".tei.paper.json"


def _already_has_xml(pdf_path: Path) -> bool:
    return (GROBID_XML_DIR / _xml_name(pdf_path)).exists()


def _already_has_paper_json(pdf_path: Path) -> bool:
    return (PAPER_JSON_DIR / _paper_json_name(pdf_path)).exists()


def _log_result(entry: dict) -> None:
    with open(INGEST_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, default=str) + "\n")


# ─── GROBID batch ─────────────────────────────────────────────────────────────

def run_grobid_batch(pdfs: list[Path]) -> dict[str, int]:
    """Send each PDF to GROBID; write XML to grobid_xml/; return counts."""
    stats = {"ok": 0, "skip": 0, "fail": 0}

    with GROBIDClient(max_retries=3) as client:
        if not client.is_alive():
            log.error("GROBID is not running on port 8070.  Start it with: docker compose up -d")
            sys.exit(1)

        log.info("GROBID is alive. Processing %d PDFs…", len(pdfs))

        for i, pdf_path in enumerate(pdfs, 1):
            xml_path = GROBID_XML_DIR / _xml_name(pdf_path)

            if xml_path.exists():
                log.debug("[%d/%d] SKIP (xml exists): %s", i, len(pdfs), pdf_path.name)
                stats["skip"] += 1
                continue

            log.info("[%d/%d] GROBID → %s", i, len(pdfs), pdf_path.name)
            result = client.process_pdf(pdf_path)

            entry = {
                "pdf":      pdf_path.name,
                "xml":      xml_path.name,
                "success":  result.success,
                "status":   result.status_code,
                "elapsed":  round(result.elapsed_sec, 2),
                "attempts": result.attempts,
                "failure":  str(result.failure_type) if result.failure_type else None,
            }

            if result.success and result.xml_text:
                xml_path.write_text(result.xml_text, encoding="utf-8")
                log.info("  ✓ wrote %s  (%.1fs)", xml_path.name, result.elapsed_sec)
                stats["ok"] += 1
                entry["bytes"] = len(result.xml_text.encode())
            else:
                log.warning("  ✗ GROBID failed: %s", result.failure_type)
                stats["fail"] += 1

            _log_result(entry)

    return stats


# ─── pipeline runner ──────────────────────────────────────────────────────────

def run_pipeline(workers: int) -> None:
    """
    Run pipeline_runner on data/literature/grobid_xml/, which includes the
    newly added XML files.  Only papers without .paper.json are processed
    (pipeline_runner's built-in level-1 pre-filter handles this).
    """
    import subprocess

    env_prefix = (
        "TOKENIZERS_PARALLELISM=false "
        "RAYON_NUM_THREADS=1 "
        "OMP_NUM_THREADS=2 "
    )
    cmd = (
        f"{env_prefix}"
        f"{sys.executable} -m src.orchestration.pipeline_runner "
        f"--workers {workers}"
    )
    log.info("Launching pipeline_runner with %d workers…", workers)
    log.info("  Command: %s", cmd)

    # exec() replaces the current process — pipeline_runner has its own
    # signal handling and progress bar; we don't want to wrap it.
    import os
    os.execlp(
        sys.executable,
        sys.executable, "-m", "src.orchestration.pipeline_runner",
        "--workers", str(workers),
    )


# ─── main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--grobid-only",   action="store_true", help="only run GROBID, skip pipeline")
    ap.add_argument("--pipeline-only", action="store_true", help="skip GROBID, run pipeline only")
    ap.add_argument("--workers",       type=int, default=3, help="pipeline_runner --workers (default 3)")
    ap.add_argument("--dry-run",       action="store_true", help="print plan without doing anything")
    args = ap.parse_args()

    # ── discover PDFs ─────────────────────────────────────────────────────────
    all_pdfs   = sorted(PDF_MISSING_DIR.glob("*.pdf"))
    need_grobid = [p for p in all_pdfs if not _already_has_xml(p)]
    need_pipe   = [p for p in all_pdfs if not _already_has_paper_json(p)]

    log.info("pdf_missing/  total PDFs      : %d", len(all_pdfs))
    log.info("  need GROBID (no .tei.xml)   : %d", len(need_grobid))
    log.info("  need pipeline (no .paper.json): %d", len(need_pipe))

    if args.dry_run:
        log.info("[dry-run] Would send %d PDFs to GROBID:", len(need_grobid))
        for p in need_grobid[:20]:
            log.info("  %s", p.name)
        if len(need_grobid) > 20:
            log.info("  … and %d more", len(need_grobid) - 20)
        log.info("[dry-run] Would then run pipeline_runner --workers %d on all XMLs.", args.workers)
        return

    # ── GROBID pass ───────────────────────────────────────────────────────────
    if not args.pipeline_only and need_grobid:
        t0    = time.perf_counter()
        stats = run_grobid_batch(need_grobid)
        elapsed = time.perf_counter() - t0
        log.info(
            "GROBID batch done in %.0fs — ok:%d  skip:%d  fail:%d",
            elapsed, stats["ok"], stats["skip"], stats["fail"],
        )
    elif not need_grobid:
        log.info("All PDFs already have .tei.xml — skipping GROBID.")

    # ── pipeline pass ─────────────────────────────────────────────────────────
    if not args.grobid_only:
        run_pipeline(args.workers)   # replaces this process


if __name__ == "__main__":
    main()
