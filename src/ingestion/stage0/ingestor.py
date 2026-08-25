"""
ingestor.py — Stage 0: Raw Document Ingestion.

Responsibility: acquire a PDF, validate it, establish its immutable identity,
and persist all raw artifacts. No NLP, no parsing, no embeddings.

Output contract (data/raw/{paper_id}/):
    paper.pdf          — immutable source PDF
    checksum.sha256    — SHA-256 of paper.pdf
    metadata.json      — bibliographic metadata (may be partial on first pass)
    source.json        — acquisition provenance
    ingestion_log.json — lifecycle events

The caller (orchestrator or CLI) is responsible for supplying the metadata dict.
This stage never calls OpenAlex or Crossref — that is the enrichment layer's job.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.ingestion.models import TriageResult
from src.ingestion.pdf_triage import sha256_pdf, triage_pdf
from src.ingestion.stage0.raw_store import RawStore

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Stage0Result:
    """Output contract of Stage0Ingestor.run()."""
    paper_id:  str
    status:    str                    # "ok" | "skipped" | "skip_triage" | "error"
    checksum:  str
    raw_root:  Path
    triage:    TriageResult | None
    events:    list[str] = field(default_factory=list)

    @property
    def should_continue(self) -> bool:
        return self.status == "ok"


class Stage0Ingestor:
    """
    Ingests one PDF into the raw store.

    Parameters
    ----------
    raw_root      : Root of data/raw/ tree.
    force         : Re-ingest even if raw store is already complete.
    """

    def __init__(self, raw_root: Path | None = None, force: bool = False) -> None:
        from src.config.settings import RAW_DIR
        self._raw_root = raw_root or RAW_DIR
        self._force    = force

    def run(
        self,
        pdf_path: Path,
        metadata: dict[str, Any] | None = None,
        source:   dict[str, Any] | None = None,
    ) -> Stage0Result:
        """
        Ingest one PDF.

        Parameters
        ----------
        pdf_path : Path to the source PDF.
        metadata : Optional bibliographic metadata dict (doi, title, year, …).
        source   : Optional acquisition provenance dict.

        Returns
        -------
        Stage0Result — never raises.
        """
        events: list[str] = []

        # ── 1. Compute content identity (SHA-256 of raw bytes) ─────────────
        try:
            content_hash = sha256_pdf(pdf_path)
        except Exception as exc:
            log.error("[stage0] sha256 failed for %s: %s", pdf_path.name, exc)
            return Stage0Result(
                paper_id="", status="error", checksum="",
                raw_root=self._raw_root, triage=None,
                events=[f"sha256_failed: {exc}"],
            )

        paper_id = content_hash.sha256
        store    = RawStore(paper_id, self._raw_root)

        # ── 2. Idempotency: skip if already complete ───────────────────────
        if store.is_complete and not self._force:
            log.debug("[stage0] already complete — %s", paper_id[:16])
            return Stage0Result(
                paper_id=paper_id, status="skipped", checksum=paper_id,
                raw_root=store.root, triage=None, events=["already_complete"],
            )

        events.append(f"start:{_now_iso()}")

        # ── 3. PDF triage (validity, page count, scanned detection) ────────
        try:
            triage = triage_pdf(pdf_path)
        except Exception as exc:
            log.error("[stage0] triage failed for %s: %s", pdf_path.name, exc)
            store.append_log({"event": "triage_error", "error": str(exc)})
            return Stage0Result(
                paper_id=paper_id, status="error", checksum=paper_id,
                raw_root=store.root, triage=None,
                events=events + [f"triage_error:{exc}"],
            )

        if not triage.should_process:
            reason = triage.skip_reason or "triage_rejected"
            log.info("[stage0] triage skip — %s (%s)", pdf_path.name, reason)
            store.append_log({"event": "triage_skip", "reason": reason})
            return Stage0Result(
                paper_id=paper_id, status="skip_triage", checksum=paper_id,
                raw_root=store.root, triage=triage,
                events=events + [f"triage_skip:{reason}"],
            )

        events.append(f"triage_ok:pages={triage.page_count}")

        # ── 4. Store immutable PDF copy + checksum ─────────────────────────
        try:
            digest = store.store_pdf(pdf_path)
        except Exception as exc:
            log.error("[stage0] store_pdf failed for %s: %s", pdf_path.name, exc)
            return Stage0Result(
                paper_id=paper_id, status="error", checksum=paper_id,
                raw_root=store.root, triage=triage,
                events=events + [f"store_pdf_error:{exc}"],
            )

        events.append(f"pdf_stored:sha256={digest[:16]}")

        # ── 5. Write metadata.json ─────────────────────────────────────────
        meta_payload: dict[str, Any] = {
            "paper_id":       paper_id,
            "source_pdf":     str(pdf_path),
            "page_count":     triage.page_count,
            "file_size_mb":   round(triage.file_size_mb, 3),
            "estimated_tokens": triage.estimated_tokens,
        }
        if metadata:
            meta_payload.update(metadata)
        store.write_metadata(meta_payload)
        events.append("metadata_written")

        # ── 6. Write source.json ───────────────────────────────────────────
        source_payload: dict[str, Any] = {
            "acquisition_method": "local_copy",
            "original_path":      str(pdf_path),
        }
        if source:
            source_payload.update(source)
        store.write_source(source_payload)
        events.append("source_written")

        # ── 7. Append ingestion log event ──────────────────────────────────
        store.append_log({
            "event":            "stage0_complete",
            "paper_id":         paper_id,
            "source_pdf":       str(pdf_path),
            "page_count":       triage.page_count,
            "estimated_tokens": triage.estimated_tokens,
            "file_size_mb":     round(triage.file_size_mb, 3),
        })
        events.append(f"done:{_now_iso()}")

        log.info("[stage0] complete — %s pages=%d size=%.1fMB",
                 paper_id[:16], triage.page_count, triage.file_size_mb)

        return Stage0Result(
            paper_id=paper_id, status="ok", checksum=digest,
            raw_root=store.root, triage=triage, events=events,
        )


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
