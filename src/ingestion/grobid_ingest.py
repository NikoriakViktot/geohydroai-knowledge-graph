"""
grobid_ingest.py — Production GROBID ingestion pipeline orchestrator.

Pipeline stages (sequential, one PDF at a time):

  PDF
  ↓ pdf_triage.triage_pdf()       — screen before GROBID (PyMuPDF)
  ↓ pdf_triage.sha256_pdf()       — content hash → paper_id
  ↓ PipelineRegistry              — idempotency / deduplication check
  ↓ GROBIDClient.process_pdf()    — HTTP POST with full coord params
  ↓ tei_validator.validate_tei()  — structural + semantic QA
  ↓ disk write (TEI XML)
  ↓ PipelineRegistry              — mark SUCCESS / FAIL / SKIPPED
  ↓ IngestionObserver             — JSONL + CSV observability

Why sequential?
  GROBID is CPU/GPU bound and already parallelises internally (engine pool).
  Sending more than one request at a time on a single-GPU machine saturates
  the pool and causes 503 storms.  Sequential submission keeps one engine
  busy at a time, eliminates 503s, and makes retry logic trivial.
"""

from __future__ import annotations

import argparse
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

from tqdm import tqdm

from src.config.settings import (
    PIPELINE_VERSION,
    REGISTRY_DIR,
    XML_DIR,
)
from src.ingestion.failure_types import FailureType
from src.ingestion.grobid_client import GROBIDClient
from src.ingestion.models import IngestionRecord
from src.ingestion.observability import IngestionObserver
from src.ingestion.pdf_triage import sha256_pdf, triage_pdf
from src.ingestion.tei_validator import validate_tei
from src.registry.constants import PaperStatus
from src.registry.registry_db import PipelineRegistry

log = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
PDF_DIR       = _PROJECT_ROOT / "data" / "literature" / "pdf"
RUN_LOG_DIR   = _PROJECT_ROOT / "data" / "ingestion_logs"

_EMOJI = {
    "SUCCESS": "✅",
    "PARTIAL": "⚠️ ",
    "FAIL":    "❌",
    "SKIPPED": "⏩",
}


# ─── Pipeline ─────────────────────────────────────────────────────────────────

class IngestionPipeline:
    """
    Orchestrates the full PDF → TEI ingestion lifecycle for one corpus.

    Parameters
    ----------
    pdf_dir:    Directory of input PDFs.
    xml_dir:    Output directory for TEI XML files.
    registry:   Shared PipelineRegistry (caller owns lifecycle).
    observer:   Structured observability sink.
    client:     GROBID HTTP client (caller owns lifecycle).
    """

    def __init__(
        self,
        pdf_dir:  Path,
        xml_dir:  Path,
        registry: PipelineRegistry,
        observer: IngestionObserver,
        client:   GROBIDClient,
    ) -> None:
        self._pdf_dir  = pdf_dir
        self._xml_dir  = xml_dir
        self._registry = registry
        self._observer = observer
        self._client   = client

        self._xml_dir.mkdir(parents=True, exist_ok=True)

    # ── Main entry ────────────────────────────────────────────────────────────

    def run(self) -> None:
        pdfs = sorted(self._pdf_dir.glob("*.pdf"))
        if not pdfs:
            log.warning("No PDFs found in %s", self._pdf_dir)
            return

        log.info("PIPELINE START | total=%d pdfs", len(pdfs))

        with tqdm(total=len(pdfs), desc="GROBID ingestion",
                  unit="pdf", dynamic_ncols=True) as pbar:
            for pdf_path in pdfs:
                rec = self._process_one(pdf_path)
                self._observer.record(rec)
                self._print_line(pbar, pdf_path.name, rec)
                pbar.update(1)
                pbar.set_postfix(**self._observer.metrics.postfix(), refresh=False)

        self._observer.print_summary()

    # ── Single PDF orchestration ──────────────────────────────────────────────

    def _process_one(self, pdf_path: Path) -> IngestionRecord:
        t_start = time.perf_counter()

        # ── Stage 1: content hash + deduplication ─────────────────────────────
        content_hash = sha256_pdf(pdf_path)
        paper_id     = content_hash.sha256

        # ── Stage 2: registry idempotency check ───────────────────────────────
        existing_status = self._registry.get_status(paper_id)
        if existing_status in {PaperStatus.SUCCESS, PaperStatus.SKIPPED}:
            return self._make_record(
                paper_id=paper_id,
                pdf_path=pdf_path,
                triage=None,
                failure=FailureType.SUCCESS if existing_status == PaperStatus.SUCCESS else None,
                elapsed=0.0,
                attempts=0,
                status_override="SKIPPED",
                detail="already_in_registry",
            )

        # Check output file directly too (resumable without registry)
        out_xml = self._xml_dir / f"{pdf_path.stem}.tei.xml"
        if out_xml.exists() and existing_status is None:
            self._registry.register_paper(
                paper_id=paper_id, source_pdf=str(pdf_path),
                input_hash=paper_id,
            )
            self._registry.mark_skipped(paper_id, reason="tei_exists")
            return self._make_record(
                paper_id=paper_id, pdf_path=pdf_path,
                triage=None, failure=None, elapsed=0.0, attempts=0,
                status_override="SKIPPED", detail="tei_exists",
            )

        # ── Stage 3: PDF triage ───────────────────────────────────────────────
        triage = triage_pdf(pdf_path)

        if not triage.should_process:
            failure = FailureType(triage.skip_reason) if triage.skip_reason else FailureType.UNKNOWN
            self._register_and_skip(paper_id, pdf_path, failure.value)
            elapsed = time.perf_counter() - t_start
            return self._make_record(
                paper_id=paper_id, pdf_path=pdf_path,
                triage=triage, failure=failure,
                elapsed=elapsed, attempts=0,
                status_override="SKIPPED",
                detail=failure.value,
            )

        # ── Register as NEW before processing ─────────────────────────────────
        self._registry.register_paper(
            paper_id=paper_id,
            source_pdf=str(pdf_path),
            input_hash=paper_id,
            lineage_meta={
                "page_count":       triage.page_count,
                "estimated_tokens": triage.estimated_tokens,
                "file_size_mb":     triage.file_size_mb,
                "pipeline_version": PIPELINE_VERSION,
            },
        )
        self._registry.start_processing(paper_id, stage="grobid")

        # ── Stage 4: GROBID call ──────────────────────────────────────────────
        grobid_resp = self._client.process_pdf(pdf_path)
        elapsed     = time.perf_counter() - t_start

        if not grobid_resp.success:
            failure = FailureType(grobid_resp.failure_type or FailureType.UNKNOWN)
            self._registry.mark_failure(
                paper_id=paper_id,
                error_type=failure.value,
                runtime_sec=elapsed,
                status=failure.to_registry_status(),
            )
            return self._make_record(
                paper_id=paper_id, pdf_path=pdf_path,
                triage=triage, failure=failure,
                elapsed=elapsed, attempts=grobid_resp.attempts,
                status_override=None, detail=failure.value,
            )

        # ── Stage 5: TEI validation ───────────────────────────────────────────
        self._registry.heartbeat(paper_id, stage="tei_validation")
        quality, val_failure = validate_tei(grobid_resp.xml_text)

        if val_failure not in {FailureType.SUCCESS}:
            self._registry.mark_failure(
                paper_id=paper_id,
                error_type=val_failure.value,
                runtime_sec=elapsed,
                status=val_failure.to_registry_status(),
            )
            return self._make_record(
                paper_id=paper_id, pdf_path=pdf_path,
                triage=triage, failure=val_failure,
                elapsed=elapsed, attempts=grobid_resp.attempts,
                status_override=None, detail=val_failure.value,
            )

        # ── Stage 6: persist TEI to disk ──────────────────────────────────────
        out_xml.write_text(grobid_resp.xml_text, encoding="utf-8")

        # ── Stage 7: registry success ─────────────────────────────────────────
        is_partial  = quality.is_partial()
        final_status = FailureType.PARTIAL if is_partial else FailureType.SUCCESS
        missing     = "|".join(quality.missing_flags())

        self._registry.mark_success(
            paper_id=paper_id,
            output_json=str(out_xml),
            runtime_sec=elapsed,
            stage_timings={"grobid": grobid_resp.elapsed_sec},
        )

        return IngestionRecord(
            sha256=paper_id,
            pdf_path=str(pdf_path),
            file_size_mb=triage.file_size_mb,
            page_count=triage.page_count,
            estimated_tokens=triage.estimated_tokens,
            is_scanned=triage.is_scanned,
            triage_status=triage.triage_status,
            ingestion_status=final_status.value,
            tei_path=str(out_xml),
            tei_quality=quality,
            elapsed_sec=elapsed,
            attempts=grobid_resp.attempts,
            processed_at=_now_iso(),
            failure_detail=missing,
        )

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _register_and_skip(self, paper_id: str, pdf_path: Path, reason: str) -> None:
        self._registry.register_paper(
            paper_id=paper_id, source_pdf=str(pdf_path), input_hash=paper_id,
        )
        self._registry.mark_skipped(paper_id, reason=reason)

    def _make_record(
        self,
        paper_id: str,
        pdf_path: Path,
        triage,
        failure: FailureType | None,
        elapsed: float,
        attempts: int,
        status_override: str | None,
        detail: str,
    ) -> IngestionRecord:
        status = status_override or (failure.value if failure else "UNKNOWN")
        return IngestionRecord(
            sha256=paper_id,
            pdf_path=str(pdf_path),
            file_size_mb=triage.file_size_mb if triage else 0.0,
            page_count=triage.page_count if triage else 0,
            estimated_tokens=triage.estimated_tokens if triage else 0,
            is_scanned=triage.is_scanned if triage else False,
            triage_status=triage.triage_status if triage else "N/A",
            ingestion_status=status,
            tei_path=None,
            tei_quality=None,
            elapsed_sec=elapsed,
            attempts=attempts,
            processed_at=_now_iso(),
            failure_detail=detail,
        )

    def _print_line(self, pbar: tqdm, name: str, rec: IngestionRecord) -> None:
        status  = rec.ingestion_status
        emoji   = _EMOJI.get(status, "❓")
        detail  = f" ({rec.failure_detail})" if rec.failure_detail else ""
        timing  = f" [{rec.elapsed_sec:.1f}s]" if rec.elapsed_sec > 0 else ""
        q_info  = ""
        if rec.tei_quality:
            q = rec.tei_quality
            q_info = f" refs={q.ref_count} sents={q.sentence_count}"
        tqdm.write(f"{emoji} [{status:7s}] {name}{detail}{timing}{q_info}")


# ─── Top-level run function ───────────────────────────────────────────────────

def run(
    pdf_dir:     Path = PDF_DIR,
    xml_dir:     Path = XML_DIR,
    max_retries: int  = 3,
) -> None:
    _setup_logging()

    registry = PipelineRegistry(
        registry_dir=REGISTRY_DIR,
        pipeline_version=PIPELINE_VERSION,
        max_retries=max_retries,
    )
    registry.init_registry()

    observer = IngestionObserver(run_dir=RUN_LOG_DIR)

    with GROBIDClient(max_retries=max_retries) as client:

        if not client.is_alive():
            raise RuntimeError(
                "GROBID is not responding at http://localhost:8070/api/isalive. "
                "Start the Docker container before running the pipeline."
            )
        log.info("GROBID is alive")

        pipeline = IngestionPipeline(
            pdf_dir=pdf_dir,
            xml_dir=xml_dir,
            registry=registry,
            observer=observer,
            client=client,
            )
        pipeline.run()

    registry.close()


# ─── Logging setup ────────────────────────────────────────────────────────────

def _setup_logging() -> None:
    log_path = RUN_LOG_DIR / "grobid_pipeline.log"
    RUN_LOG_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=str(log_path),
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    stderr = logging.StreamHandler()
    stderr.setLevel(logging.WARNING)
    stderr.setFormatter(logging.Formatter("%(levelname)s | %(message)s"))
    logging.getLogger().addHandler(stderr)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ─── CLI ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Production GROBID PDF ingestion pipeline",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--pdf-dir",  type=Path, default=PDF_DIR)
    parser.add_argument("--xml-dir",  type=Path, default=XML_DIR)
    parser.add_argument("--retries",  type=int,  default=3)
    args = parser.parse_args()

    try:
        run(pdf_dir=args.pdf_dir, xml_dir=args.xml_dir, max_retries=args.retries)
    except RuntimeError as exc:
        log.critical(str(exc))
        raise SystemExit(1) from exc
    except KeyboardInterrupt:
        tqdm.write("\nInterrupted.")
        raise SystemExit(130)


if __name__ == "__main__":
    main()
