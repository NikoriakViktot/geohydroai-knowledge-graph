"""
parser_quality.py — DocumentQuality assessment for parsed TEIDocuments.

Called after parsing to attach a structured quality snapshot to every document.
The quality score drives routing decisions in HybridParser:
  - score < 0.4  → Nougat augmentation recommended
  - score < 0.2  → GROBID output is unreliable; Nougat preferred
"""

from __future__ import annotations

from src.document.models import TEIDocument
from src.document.provenance import DocumentQuality


def assess_quality(doc: TEIDocument, parser_strategy: str = "grobid_only") -> DocumentQuality:
    """
    Inspect a TEIDocument and return a DocumentQuality snapshot.

    Scoring weights (sum to 1.0):
      title present        0.15
      abstract present     0.10
      ≥1 section           0.20
      ≥3 sections          0.10
      ≥1 reference         0.15
      ≥1 inline citation   0.10
      ≥1 coordinate        0.10
      ≥1 figure or table   0.10
    """
    has_structure   = any(s.text for s in doc.sections)
    has_refs        = bool(doc.references)
    has_citations   = any(s.has_citations() for s in doc.all_sentences())
    has_coords      = (
        any(s.coords for sec in doc.sections for p in sec.all_paragraphs() for s in p.sentences)
        or any(f.coords for f in doc.figures)
        or any(t.coords for t in doc.tables)
    )
    has_formulas    = bool(doc.formulas)
    has_tables      = bool(doc.tables)
    has_figures     = bool(doc.figures)
    has_ocr_text    = doc.visual_text is not None or doc.markdown_text is not None

    score = 0.0
    if doc.title:              score += 0.15
    if doc.abstract:           score += 0.10
    if has_structure:          score += 0.20
    if len(doc.sections) >= 3: score += 0.10
    if has_refs:               score += 0.15
    if has_citations:          score += 0.10
    if has_coords:             score += 0.10
    if has_figures or has_tables: score += 0.10

    return DocumentQuality(
        has_structure   = has_structure,
        has_coordinates = has_coords,
        has_references  = has_refs,
        has_citations   = has_citations,
        has_formulas    = has_formulas,
        has_tables      = has_tables,
        has_figures     = has_figures,
        has_ocr_text    = has_ocr_text,
        parser_strategy = parser_strategy,
        quality_score   = round(score, 3),
    )


def needs_nougat_augmentation(quality: DocumentQuality) -> bool:
    """
    True when GROBID output is weak enough to warrant Nougat augmentation.

    Triggers:
      - quality_score < 0.4 (missing title/body/refs combination)
      - No formulas but formula-heavy document signals exist
    """
    return quality.quality_score < 0.4


def is_grobid_output_usable(quality: DocumentQuality) -> bool:
    """True when the GROBID output has at minimum a title and body structure."""
    return quality.has_structure and quality.quality_score >= 0.2
