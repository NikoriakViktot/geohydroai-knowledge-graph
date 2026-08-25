"""
provenance.py — Parser kind, capability, and provenance data models.

These types extend TEIDocument with structured metadata about which parser
produced the document and what quality/capabilities it carries.  They are
consumed by HybridParser, ParserRouter, and observability tooling.

No lxml, no XML, no parser-specific types — pure Python dataclasses.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


# ── Parser identity ────────────────────────────────────────────────────────────

class ParserKind(str, Enum):
    """Canonical parser identifier stored on every TEIDocument."""
    GROBID  = "grobid"
    NOUGAT  = "nougat"
    HYBRID  = "hybrid"
    DOCLING = "docling"
    UNKNOWN = "unknown"


# ── Parser capabilities ────────────────────────────────────────────────────────

class ParserCapability(str, Enum):
    """
    Capabilities a parser instance actually exercised for a given document.

    Used by downstream code to decide whether to trust certain fields:
      - COORDINATES absent → skip proximity-based figure grounding
      - REFERENCES absent  → skip citation graph building
      - FORMULAS present   → include formula text in embeddings
    """
    STRUCTURE      = "structure"       # sections, paragraphs, headings
    COORDINATES    = "coordinates"     # PDF bounding boxes per element
    REFERENCES     = "references"      # bibliography list
    CITATIONS      = "citations"       # inline citation markers
    FORMULAS       = "formulas"        # LaTeX/MathML equation extraction
    TABLES         = "tables"          # tabular data
    FIGURES        = "figures"         # figure captions and labels
    OCR            = "ocr"             # optical character recognition
    MARKDOWN       = "markdown"        # markdown text output available
    VISUAL_CONTENT = "visual_content"  # any image-based content


# ── Per-parser provenance record ───────────────────────────────────────────────

@dataclass(frozen=True)
class ParserProvenance:
    """
    Immutable record of one parser's contribution to a TEIDocument.

    When HybridParser merges GROBID + Nougat, both parsers' provenance
    records are included so any downstream audit can trace every field.
    """
    parser_name:    str                          # e.g. "grobid", "nougat"
    parser_version: str                          # model/server version string
    source_format:  str                          # "tei_xml" | "pdf_image" | "markdown"
    confidence:     float = 1.0                  # 0.0–1.0; lower for OCR
    capabilities:   frozenset[ParserCapability] = field(default_factory=frozenset)
    elapsed_sec:    float = 0.0                  # inference time for this parser
    notes:          str   = ""                   # free-form diagnostic notes

    def to_dict(self) -> dict:
        return {
            "parser_name":    self.parser_name,
            "parser_version": self.parser_version,
            "source_format":  self.source_format,
            "confidence":     round(self.confidence, 3),
            "capabilities":   sorted(c.value for c in self.capabilities),
            "elapsed_sec":    round(self.elapsed_sec, 3),
            "notes":          self.notes,
        }


# ── Document-level quality snapshot ───────────────────────────────────────────

@dataclass(frozen=True)
class DocumentQuality:
    """
    Presence flags for every major document feature.

    Computed once after parsing and attached to TEIDocument.document_quality.
    Downstream components use these flags without re-inspecting the document.
    """
    has_structure:   bool   # ≥1 section with text
    has_coordinates: bool   # ≥1 element with PDF bounding box
    has_references:  bool   # ≥1 bibliographic entry
    has_citations:   bool   # ≥1 inline citation marker
    has_formulas:    bool   # ≥1 formula
    has_tables:      bool   # ≥1 table
    has_figures:     bool   # ≥1 figure
    has_ocr_text:    bool   # any text came from OCR / Nougat
    parser_strategy: str    # strategy key used (e.g. "hybrid", "grobid_only")
    quality_score:   float  # 0.0–1.0 composite score

    def missing_features(self) -> list[str]:
        flags = []
        if not self.has_structure:   flags.append("NO_STRUCTURE")
        if not self.has_references:  flags.append("NO_REFERENCES")
        if not self.has_coordinates: flags.append("NO_COORDINATES")
        return flags

    def to_dict(self) -> dict:
        return {
            "has_structure":   self.has_structure,
            "has_coordinates": self.has_coordinates,
            "has_references":  self.has_references,
            "has_citations":   self.has_citations,
            "has_formulas":    self.has_formulas,
            "has_tables":      self.has_tables,
            "has_figures":     self.has_figures,
            "has_ocr_text":    self.has_ocr_text,
            "parser_strategy": self.parser_strategy,
            "quality_score":   round(self.quality_score, 3),
        }


# ── GROBID canonical capability set ───────────────────────────────────────────

GROBID_CAPABILITIES: frozenset[ParserCapability] = frozenset({
    ParserCapability.STRUCTURE,
    ParserCapability.COORDINATES,
    ParserCapability.REFERENCES,
    ParserCapability.CITATIONS,
    ParserCapability.FIGURES,
    ParserCapability.TABLES,
    ParserCapability.FORMULAS,
})

NOUGAT_CAPABILITIES: frozenset[ParserCapability] = frozenset({
    ParserCapability.STRUCTURE,
    ParserCapability.OCR,
    ParserCapability.MARKDOWN,
    ParserCapability.VISUAL_CONTENT,
    ParserCapability.FORMULAS,
    ParserCapability.TABLES,
})
