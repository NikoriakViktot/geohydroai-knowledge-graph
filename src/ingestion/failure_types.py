"""
failure_types.py — Structured ingestion error taxonomy.

Maps every possible failure mode to a canonical enum value so the rest of
the pipeline can branch on type, not on string comparison.  Each value
declares its own retry and skip semantics so callers never hardcode policy.
"""

from __future__ import annotations

import re
from enum import Enum


class FailureType(str, Enum):
    # ── Terminal success ───────────────────────────────────────────────────────
    SUCCESS = "SUCCESS"
    PARTIAL = "PARTIAL"          # valid TEI but missing some sections

    # ── Pre-GROBID: PDF triage ────────────────────────────────────────────────
    PDF_ENCRYPTED          = "PDF_ENCRYPTED"
    PDF_SCANNED            = "PDF_SCANNED"
    PDF_NO_TEXT            = "PDF_NO_TEXT"
    PDF_CORRUPTED          = "PDF_CORRUPTED"
    PDF_TOO_MANY_PAGES     = "PDF_TOO_MANY_PAGES"

    # ── GROBID HTTP transport ─────────────────────────────────────────────────
    HTTP_503               = "HTTP_503"       # all engines busy → retry
    HTTP_500               = "HTTP_500"       # unclassified server error
    HTTP_204               = "HTTP_204"       # parsed but empty → skip
    HTTP_OTHER             = "HTTP_OTHER"
    TIMEOUT                = "TIMEOUT"
    CONNECTION_ERROR       = "CONNECTION_ERROR"

    # ── GROBID content errors (from 500 body) ─────────────────────────────────
    GROBID_NO_BLOCKS            = "NO_BLOCKS"
    GROBID_TOO_MANY_BLOCKS      = "TOO_MANY_BLOCKS"
    GROBID_TOO_MANY_TOKENS      = "TOO_MANY_TOKENS"
    GROBID_TIMEOUT              = "GROBID_TIMEOUT"
    GROBID_TAGGING_ERROR        = "TAGGING_ERROR"
    GROBID_PARSING_ERROR        = "PARSING_ERROR"
    GROBID_BAD_INPUT            = "BAD_INPUT_DATA"
    GROBID_PDFALTO_FAILURE      = "PDFALTO_CONVERSION_FAILURE"

    # ── TEI output quality ────────────────────────────────────────────────────
    INVALID_XML            = "INVALID_XML"
    EMPTY_TEI              = "EMPTY_TEI"

    # ── Nougat visual parser ──────────────────────────────────────────────────
    NOUGAT_MODEL_LOAD_ERROR = "NOUGAT_MODEL_LOAD_ERROR"   # model download/init failed
    NOUGAT_INFERENCE_ERROR  = "NOUGAT_INFERENCE_ERROR"    # per-page generation error
    NOUGAT_OOM              = "NOUGAT_OOM"                # CUDA/CPU out-of-memory
    NOUGAT_EMPTY_OUTPUT     = "NOUGAT_EMPTY_OUTPUT"       # model produced no text

    # ── Hybrid / routing ──────────────────────────────────────────────────────
    HYBRID_MERGE_ERROR      = "HYBRID_MERGE_ERROR"        # merge step raised
    PARSER_ROUTING_ERROR    = "PARSER_ROUTING_ERROR"      # router misconfiguration
    MARKDOWN_PARSE_ERROR    = "MARKDOWN_PARSE_ERROR"      # MarkdownScientificParser fail

    UNKNOWN                = "UNKNOWN"

    # ── Policy ────────────────────────────────────────────────────────────────

    def is_retriable(self) -> bool:
        """True → schedule a retry with exponential backoff."""
        return self in {
            FailureType.HTTP_503,
            FailureType.HTTP_500,
            FailureType.TIMEOUT,
            FailureType.CONNECTION_ERROR,
            FailureType.GROBID_TIMEOUT,
            FailureType.NOUGAT_INFERENCE_ERROR,   # transient GPU errors are retriable
        }

    def is_skip(self) -> bool:
        """True → PDF is structurally unprocessable; skip permanently."""
        return self in {
            FailureType.PDF_ENCRYPTED,
            FailureType.PDF_SCANNED,
            FailureType.PDF_NO_TEXT,
            FailureType.HTTP_204,
            FailureType.GROBID_NO_BLOCKS,
            FailureType.GROBID_TOO_MANY_BLOCKS,
            FailureType.GROBID_TOO_MANY_TOKENS,
            FailureType.NOUGAT_MODEL_LOAD_ERROR,  # can't run Nougat without model
            FailureType.NOUGAT_EMPTY_OUTPUT,      # document not parseable by Nougat
        }

    def is_nougat_error(self) -> bool:
        """True for any Nougat-specific failure."""
        return self in {
            FailureType.NOUGAT_MODEL_LOAD_ERROR,
            FailureType.NOUGAT_INFERENCE_ERROR,
            FailureType.NOUGAT_OOM,
            FailureType.NOUGAT_EMPTY_OUTPUT,
        }

    def to_registry_status(self) -> str:
        """Map to the PaperStatus values used by PipelineRegistry."""
        from src.registry.constants import PaperStatus
        if self in {FailureType.SUCCESS, FailureType.PARTIAL}:
            return PaperStatus.SUCCESS
        if self.is_skip():
            return PaperStatus.SKIPPED
        if self == FailureType.INVALID_XML:
            return PaperStatus.INVALID_XML
        if self == FailureType.EMPTY_TEI:
            return PaperStatus.EMPTY_OUTPUT
        if self == FailureType.NOUGAT_OOM:
            return PaperStatus.RETRY    # OOM is retriable after memory pressure drops
        if self.is_retriable():
            return PaperStatus.RETRY
        return PaperStatus.FAIL


# ── GROBID 500-body error code parser ─────────────────────────────────────────

_GROBID_CODE_RE = re.compile(
    r"\[(NO_BLOCKS|TOO_MANY_BLOCKS|TOO_MANY_TOKENS|TIMEOUT|TAGGING_ERROR"
    r"|PARSING_ERROR|BAD_INPUT_DATA|PDFALTO_CONVERSION_FAILURE|GENERAL)\]"
)

_CODE_MAP: dict[str, FailureType] = {
    "NO_BLOCKS":               FailureType.GROBID_NO_BLOCKS,
    "TOO_MANY_BLOCKS":         FailureType.GROBID_TOO_MANY_BLOCKS,
    "TOO_MANY_TOKENS":         FailureType.GROBID_TOO_MANY_TOKENS,
    "TIMEOUT":                 FailureType.GROBID_TIMEOUT,
    "TAGGING_ERROR":           FailureType.GROBID_TAGGING_ERROR,
    "PARSING_ERROR":           FailureType.GROBID_PARSING_ERROR,
    "BAD_INPUT_DATA":          FailureType.GROBID_BAD_INPUT,
    "PDFALTO_CONVERSION_FAILURE": FailureType.GROBID_PDFALTO_FAILURE,
    "GENERAL":                 FailureType.HTTP_500,
}


def classify_500_body(body: str) -> FailureType:
    """
    Parse GROBID's 500 response body for a structured error code.
    Falls back to HTTP_500 if no known code is found.
    """
    m = _GROBID_CODE_RE.search(body)
    if m:
        return _CODE_MAP.get(m.group(1), FailureType.HTTP_500)
    return FailureType.HTTP_500
