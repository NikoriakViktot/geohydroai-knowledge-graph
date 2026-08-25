"""
pdf_triage.py — Pre-GROBID PDF validation using PyMuPDF (fitz).

Screens PDFs before sending to GROBID to avoid wasting GPU resources on
documents that GROBID cannot parse: encrypted files, scanned/image-only
documents, corrupted files, and PDFs with no embedded text.

All checks are fast (milliseconds) because they use PyMuPDF's native C
layer without invoking any ML model.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

from src.document.pdf_io import PdfIOError, probe_pdf

from src.ingestion.failure_types import FailureType
from src.ingestion.models import ContentHash, TriageResult

log = logging.getLogger(__name__)

# ── Triage thresholds ─────────────────────────────────────────────────────────

MAX_PAGES          = 500    # GROBID raises TOO_MANY_BLOCKS on very large docs
SCANNED_THRESHOLD  = 0.75   # fraction of pages with no text → scanned
MIN_TEXT_PER_PAGE  = 50     # characters — below this a page is "empty"
SAMPLE_PAGES       = 20     # pages to inspect for scanned detection
CHARS_PER_TOKEN    = 4      # rough token estimate


def triage_pdf(pdf_path: Path) -> TriageResult:
    """
    Inspect a PDF with PyMuPDF and return a TriageResult.

    Never raises — all exceptions are caught and returned as TriageResult
    with triage_status="ERROR" so the caller can log and skip cleanly.
    """
    size_bytes = pdf_path.stat().st_size
    size_mb    = size_bytes / 1_048_576

    try:
        probe = probe_pdf(
            pdf_path,
            sample_pages=SAMPLE_PAGES,
            min_chars_per_page=MIN_TEXT_PER_PAGE,
            full_sample_pages=50,
        )
    except PdfIOError as exc:
        log.warning("triage | CORRUPTED | %s | %s", pdf_path.name, exc)
        return TriageResult(
            is_scanned=False, is_encrypted=False, has_text=False,
            page_count=0, estimated_tokens=0, file_size_mb=size_mb,
            triage_status="ERROR", skip_reason=FailureType.PDF_CORRUPTED,
        )

    try:
        # ── Encrypted ─────────────────────────────────────────────────────────
        if probe.is_locked:
            log.info("triage | ENCRYPTED | %s", pdf_path.name)
            return TriageResult(
                is_scanned=False, is_encrypted=True, has_text=False,
                page_count=probe.page_count, estimated_tokens=0,
                file_size_mb=size_mb,
                triage_status="SKIP", skip_reason=FailureType.PDF_ENCRYPTED,
            )

        page_count = probe.page_count

        # ── Page count limit ──────────────────────────────────────────────────
        if page_count > MAX_PAGES:
            log.info("triage | TOO_MANY_PAGES | %s | pages=%d", pdf_path.name, page_count)
            return TriageResult(
                is_scanned=False, is_encrypted=False, has_text=True,
                page_count=page_count, estimated_tokens=0, file_size_mb=size_mb,
                triage_status="SKIP", skip_reason=FailureType.PDF_TOO_MANY_PAGES,
            )

        # ── Text + scanned detection (пороги — політика цього модуля) ────────
        sample_n         = probe.sample_pages
        total_chars      = probe.sampled_chars
        image_only_n     = probe.image_only_pages
        estimated_tokens = probe.estimated_chars // CHARS_PER_TOKEN

        has_text   = total_chars > MIN_TEXT_PER_PAGE * max(sample_n * 0.1, 1)
        is_scanned = (image_only_n / sample_n) >= SCANNED_THRESHOLD if sample_n else False

        # ── No text at all ────────────────────────────────────────────────────
        if not has_text and not is_scanned:
            log.info("triage | NO_TEXT | %s", pdf_path.name)
            return TriageResult(
                is_scanned=False, is_encrypted=False, has_text=False,
                page_count=page_count, estimated_tokens=0, file_size_mb=size_mb,
                triage_status="SKIP", skip_reason=FailureType.PDF_NO_TEXT,
            )

        # ── Scanned ───────────────────────────────────────────────────────────
        if is_scanned:
            log.info(
                "triage | SCANNED | %s | image_only=%d/%d",
                pdf_path.name, image_only_n, sample_n,
            )
            return TriageResult(
                is_scanned=True, is_encrypted=False, has_text=False,
                page_count=page_count, estimated_tokens=0, file_size_mb=size_mb,
                triage_status="SKIP", skip_reason=FailureType.PDF_SCANNED,
            )

        log.debug(
            "triage | OK | %s | pages=%d tokens~%d size=%.1fMB",
            pdf_path.name, page_count, estimated_tokens, size_mb,
        )
        return TriageResult(
            is_scanned=False, is_encrypted=False, has_text=True,
            page_count=page_count, estimated_tokens=estimated_tokens,
            file_size_mb=size_mb, triage_status="OK",
        )

    except Exception as exc:
        log.warning("triage | ERROR | %s | %s", pdf_path.name, exc)
        return TriageResult(
            is_scanned=False, is_encrypted=False, has_text=False,
            page_count=0, estimated_tokens=0, file_size_mb=size_mb,
            triage_status="ERROR", skip_reason=FailureType.PDF_CORRUPTED,
        )


def sha256_pdf(pdf_path: Path) -> ContentHash:
    """Compute SHA-256 of raw PDF bytes for content-based identity."""
    h = hashlib.sha256()
    with open(pdf_path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return ContentHash(sha256=h.hexdigest(), pdf_path=str(pdf_path))
