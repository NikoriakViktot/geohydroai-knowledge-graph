"""
protocols.py — Parser-agnostic DocumentParser interface.

Any backend that converts raw source material into a TEIDocument must satisfy
this Protocol.  Downstream code (extraction, normalisation, KG) only imports
TEIDocument from src.document.models — never a concrete parser class.

Current implementations:
  TEIParser  (GROBID TEI XML)   src/document/parser.py

Planned:
  NougatParser   (Nougat Markdown output)
  DoclingParser  (Docling JSON)
  MarkerParser   (Marker Markdown)

Swapping parsers requires no changes to any downstream module.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from src.document.models import TEIDocument


@runtime_checkable
class DocumentParser(Protocol):
    """
    Contract all document parsers must satisfy.

    The two entry points cover the most common cases:
      parse_text — in-memory string (from GROBID HTTP response or cache)
      parse_file — on-disk file path (for batch reprocessing)
    """

    parser_name:    str   # e.g. "grobid", "nougat", "docling"
    parser_version: str   # parser/model version for provenance tracking

    def parse_text(self, source: str, paper_id: str) -> TEIDocument:
        """Parse from a raw string (XML, JSON, Markdown…)."""
        ...

    def parse_file(self, path: Path, paper_id: str) -> TEIDocument:
        """Parse from a file on disk."""
        ...
