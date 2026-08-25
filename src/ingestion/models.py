"""
models.py — Typed dataclasses for every stage of the ingestion pipeline.

These are the canonical data contracts between pipeline stages.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


# ── Stage 1: PDF triage ───────────────────────────────────────────────────────

@dataclass(frozen=True)
class TriageResult:
    """Output of the PyMuPDF triage layer."""
    is_scanned:        bool
    is_encrypted:      bool
    has_text:          bool
    page_count:        int
    estimated_tokens:  int
    file_size_mb:      float
    triage_status:     str           # "OK" | "SKIP" | "ERROR"
    skip_reason:       Optional[str] = None   # FailureType value when SKIP/ERROR

    @property
    def should_process(self) -> bool:
        return self.triage_status == "OK"


# ── Stage 2: content hash ─────────────────────────────────────────────────────

@dataclass(frozen=True)
class ContentHash:
    """SHA-256 hash of raw PDF bytes — the canonical document identity."""
    sha256:   str          # hex digest
    pdf_path: str          # absolute path at time of hashing


# ── Stage 3: GROBID response ──────────────────────────────────────────────────

@dataclass
class GROBIDResponse:
    """Raw outcome of one GROBID HTTP call (before TEI parsing)."""
    success:      bool
    xml_text:     str
    status_code:  int
    elapsed_sec:  float
    attempts:     int
    failure_type: Optional[str] = None   # FailureType value on failure


# ── Stage 4: TEI semantic quality ─────────────────────────────────────────────

@dataclass(frozen=True)
class TEIQuality:
    """Structural presence flags extracted from the TEI XML."""
    has_title:       bool
    has_abstract:    bool
    has_body:        bool
    has_references:  bool
    has_figures:     bool
    has_tables:      bool
    has_formulas:    bool
    has_coordinates: bool
    ref_count:       int = 0
    figure_count:    int = 0
    table_count:     int = 0
    sentence_count:  int = 0

    def missing_flags(self) -> list[str]:
        flags = []
        if not self.has_title:      flags.append("NO_TITLE")
        if not self.has_abstract:   flags.append("NO_ABSTRACT")
        if not self.has_body:       flags.append("NO_BODY")
        if not self.has_references: flags.append("NO_REFS")
        return flags

    def is_partial(self) -> bool:
        """True when TEI is valid but missing critical sections."""
        return bool(self.missing_flags())

    def is_minimal(self) -> bool:
        """Absolute minimum: title + body must exist for downstream KG use."""
        return self.has_title and self.has_body


# ── Final ingestion record (written to registry + JSONL) ─────────────────────

@dataclass
class IngestionRecord:
    """
    Complete record of one PDF's ingestion lifecycle.
    Serialised to JSONL for observability and fed into PipelineRegistry.
    """
    sha256:           str
    pdf_path:         str
    file_size_mb:     float
    page_count:       int
    estimated_tokens: int
    is_scanned:       bool
    triage_status:    str
    ingestion_status: str            # FailureType value
    tei_path:         Optional[str]
    tei_quality:      Optional[TEIQuality]
    elapsed_sec:      float
    attempts:         int
    processed_at:     str            # ISO-8601 UTC
    failure_detail:   str = ""

    def to_dict(self) -> dict:
        quality = None
        if self.tei_quality:
            q = self.tei_quality
            quality = {
                "has_title":       q.has_title,
                "has_abstract":    q.has_abstract,
                "has_body":        q.has_body,
                "has_references":  q.has_references,
                "has_figures":     q.has_figures,
                "has_tables":      q.has_tables,
                "has_formulas":    q.has_formulas,
                "has_coordinates": q.has_coordinates,
                "ref_count":       q.ref_count,
                "figure_count":    q.figure_count,
                "table_count":     q.table_count,
                "sentence_count":  q.sentence_count,
            }
        return {
            "sha256":           self.sha256,
            "pdf_path":         self.pdf_path,
            "file_size_mb":     round(self.file_size_mb, 2),
            "page_count":       self.page_count,
            "estimated_tokens": self.estimated_tokens,
            "is_scanned":       self.is_scanned,
            "triage_status":    self.triage_status,
            "ingestion_status": self.ingestion_status,
            "tei_path":         self.tei_path,
            "tei_quality":      quality,
            "elapsed_sec":      round(self.elapsed_sec, 2),
            "attempts":         self.attempts,
            "processed_at":     self.processed_at,
            "failure_detail":   self.failure_detail,
        }
