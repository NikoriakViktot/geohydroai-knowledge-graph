"""
src/document — Scientific Document Object Model (SDOM).

Public API — import from here, not from sub-modules directly.

    from src.document import TEIDocument, TEIParser, LayoutAwareChunker, DocumentChunk
    from src.document import ParserRouter, NougatParser, HybridParser

Architecture:

    PDF
     ↓  ParserRouter.parse_pdf()
    TEIDocument          ← the canonical domain model
     ↓  LayoutAwareChunker.chunk()
    list[DocumentChunk]  ← atomic units for retrieval / embedding
     ↓
    Extraction / Normalisation / KG (unchanged)

Parser swapping: any class satisfying the DocumentParser Protocol can replace
any parser implementation without changing any downstream code.

Parser hierarchy:
  TEIParser            GROBID TEI XML → TEIDocument (primary for text PDFs)
  NougatParser         PDF images → Nougat markdown → TEIDocument (scanned/formula)
  HybridParser         GROBID + Nougat merged → TEIDocument
  MarkdownScientificParser  raw markdown → TEIDocument (no model, fast)
  ParserRouter         routes any PDF to the right parser
"""

from src.document.chunker import DocumentChunk, LayoutAwareChunker
from src.document.coordinates import BoundingBox, Coordinates, parse_coords
from src.document.hybrid_parser import HybridParser
from src.document.markdown_parser import MarkdownScientificParser
from src.document.models import (
    Affiliation,
    Author,
    CitationMarker,
    Figure,
    Formula,
    Paragraph,
    Reference,
    Section,
    Sentence,
    Table,
    TEIDocument,
)
from src.document.nougat_parser import NougatParser
from src.document.parser import TEIParser
from src.document.parser_quality import assess_quality
from src.document.parser_router import ParserRouter, RouterConfig
from src.document.protocols import DocumentParser
from src.document.provenance import (
    DocumentQuality,
    ParserCapability,
    ParserKind,
    ParserProvenance,
)

__all__ = [
    # Models
    "TEIDocument",
    "Section",
    "Paragraph",
    "Sentence",
    "Figure",
    "Table",
    "Formula",
    "Author",
    "Affiliation",
    "CitationMarker",
    "Reference",
    # Coordinates
    "BoundingBox",
    "Coordinates",
    "parse_coords",
    # Parsers
    "TEIParser",
    "NougatParser",
    "HybridParser",
    "MarkdownScientificParser",
    "DocumentParser",
    # Routing
    "ParserRouter",
    "RouterConfig",
    # Quality & provenance
    "assess_quality",
    "DocumentQuality",
    "ParserCapability",
    "ParserKind",
    "ParserProvenance",
    # Chunker
    "LayoutAwareChunker",
    "DocumentChunk",
]
