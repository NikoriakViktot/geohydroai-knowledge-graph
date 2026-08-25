"""
raw_store.py — Immutable raw artifact store for Stage 0.

Layout::

    data/raw/{paper_id}/
        paper.pdf          — copy of the source PDF (immutable)
        metadata.json      — DOI, title, year, journal, openalex_id
        source.json        — acquisition provenance (URL, method, timestamp)
        checksum.sha256    — SHA-256 hex of paper.pdf
        ingestion_log.json — per-paper lifecycle events

Design rules:
  - All writes are atomic (write tmp → rename).
  - Files are never mutated after first write (idempotent re-runs skip).
  - paper.pdf is a hard copy, not a symlink (immutability guarantee).
"""
from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.config.settings import RAW_DIR


class RawStore:
    """
    Reads and writes Stage 0 artifacts for a single paper.

    Parameters
    ----------
    paper_id : Canonical paper identifier (SHA-256 hex, first 32 chars).
    raw_root : Root directory for all raw stores (default: $RAW_DIR).
    """

    def __init__(self, paper_id: str, raw_root: Path = RAW_DIR) -> None:
        self.paper_id = paper_id
        self.root     = raw_root / paper_id
        self.root.mkdir(parents=True, exist_ok=True)

    # ── Existence checks ──────────────────────────────────────────────────────

    @property
    def is_complete(self) -> bool:
        """True when all mandatory Stage 0 artifacts are present."""
        return (
            self.pdf_path.exists()
            and self.checksum_path.exists()
            and self.metadata_path.exists()
            and self.source_path.exists()
        )

    # ── Path accessors ────────────────────────────────────────────────────────

    @property
    def pdf_path(self) -> Path:
        return self.root / "paper.pdf"

    @property
    def checksum_path(self) -> Path:
        return self.root / "checksum.sha256"

    @property
    def metadata_path(self) -> Path:
        return self.root / "metadata.json"

    @property
    def source_path(self) -> Path:
        return self.root / "source.json"

    @property
    def log_path(self) -> Path:
        return self.root / "ingestion_log.json"

    # ── Write API ─────────────────────────────────────────────────────────────

    def store_pdf(self, src: Path) -> str:
        """
        Copy src PDF to paper.pdf and write checksum.sha256.

        Returns the SHA-256 hex digest.
        Skips the copy if paper.pdf already exists with a matching checksum.
        """
        if self.pdf_path.exists() and self.checksum_path.exists():
            existing = self.checksum_path.read_text().strip()
            if existing == _sha256(self.pdf_path):
                return existing

        # Atomic copy via tmp file
        tmp = self.pdf_path.with_suffix(".pdf.tmp")
        shutil.copy2(str(src), str(tmp))
        tmp.rename(self.pdf_path)

        digest = _sha256(self.pdf_path)
        _atomic_write(self.checksum_path, digest)
        return digest

    def write_metadata(self, meta: dict[str, Any]) -> None:
        """Write metadata.json (DOI, title, year, journal, openalex_id, …)."""
        _atomic_json(self.metadata_path, meta)

    def write_source(self, source: dict[str, Any]) -> None:
        """
        Write source.json — acquisition provenance.

        Standard fields:
          acquisition_method : "doi_download" | "manual" | "openalex_pdf" | "local_copy"
          source_url         : original URL (if downloaded)
          acquired_at        : ISO-8601 UTC timestamp
          acquired_by        : script name or user
        """
        source.setdefault("acquired_at", _now_iso())
        _atomic_json(self.source_path, source)

    def append_log(self, event: dict[str, Any]) -> None:
        """Append one event dict to ingestion_log.json (list of events)."""
        event.setdefault("timestamp", _now_iso())
        events: list[dict] = []
        if self.log_path.exists():
            try:
                events = json.loads(self.log_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass
        events.append(event)
        _atomic_json(self.log_path, events)

    # ── Read API ──────────────────────────────────────────────────────────────

    def read_metadata(self) -> dict[str, Any]:
        if not self.metadata_path.exists():
            return {}
        return json.loads(self.metadata_path.read_text(encoding="utf-8"))

    def read_source(self) -> dict[str, Any]:
        if not self.source_path.exists():
            return {}
        return json.loads(self.source_path.read_text(encoding="utf-8"))

    def checksum(self) -> str | None:
        if not self.checksum_path.exists():
            return None
        return self.checksum_path.read_text().strip()

    def verify_pdf_integrity(self) -> bool:
        """Return True when stored PDF matches its checksum."""
        stored = self.checksum()
        if not stored or not self.pdf_path.exists():
            return False
        return _sha256(self.pdf_path) == stored


# ── Helpers ───────────────────────────────────────────────────────────────────

def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.rename(path)


def _atomic_json(path: Path, data: Any) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str),
                   encoding="utf-8")
    tmp.rename(path)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
