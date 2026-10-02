"""Moved to src/services/evidence_text.py (P6). This shim keeps src/paper_3 and
tools/paper3_audit importing until they are retired."""
from src.services.evidence_text import (  # noqa: F401
    FUZZY_THRESHOLD,
    MIN_QUOTE_CHARS,
    SENT_SPLIT,
    EvidencePassage,
    QuoteVerdict,
    build_passages,
    normalize_text,
    split_sentences,
    verify_quote,
)
