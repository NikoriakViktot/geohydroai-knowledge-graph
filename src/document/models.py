"""
models.py — Scientific Document Object Model (SDOM).

Canonical domain model for a parsed scientific paper.  This is the only
representation that downstream extraction, normalisation, and KG code should
ever see.  TEI XML, BibTeX, JSON-LD — all are transient formats that parsers
convert *into* these types.

Design rules:
  - Frozen dataclasses for all value objects (coordinates, quality flags).
  - Mutable dataclasses only where post-construction mutation is needed
    (Section nesting, TEIDocument index build).
  - No lxml / XML types anywhere in this file.
  - All fields have explicit types; Optional[x] is written as x | None.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.document.coordinates import Coordinates
from src.document.provenance import (
    DocumentQuality,
    ParserCapability,
    ParserKind,
    ParserProvenance,
)


# ─── Inline citation marker ────────────────────────────────────────────────────

@dataclass(frozen=True)
class CitationMarker:
    """An inline citation callout within a sentence, e.g. '[5]' or '(Smith, 2020)'."""
    ref_id:  str                  # matches Reference.xml_id, e.g. "b5"
    label:   str                  # displayed text, e.g. "[5]"
    coords:  Coordinates | None = None


# ─── Sentence ─────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Sentence:
    """One GROBID-segmented sentence."""
    xml_id:    str
    text:      str
    coords:    Coordinates | None
    citations: tuple[CitationMarker, ...]

    def has_citations(self) -> bool:
        return bool(self.citations)

    def cited_ref_ids(self) -> list[str]:
        return [c.ref_id for c in self.citations]


# ─── Paragraph ────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Paragraph:
    """A sequence of sentences forming one logical paragraph."""
    sentences: tuple[Sentence, ...]
    coords:    Coordinates | None = None

    @property
    def text(self) -> str:
        return " ".join(s.text for s in self.sentences)

    @property
    def all_citations(self) -> list[CitationMarker]:
        return [c for s in self.sentences for c in s.citations]

    @property
    def cited_ref_ids(self) -> list[str]:
        return [c.ref_id for c in self.all_citations]


# ─── Section ──────────────────────────────────────────────────────────────────

@dataclass
class Section:
    """
    A titled section of the document body.

    Sections are hierarchical: subsections are nested directly so the tree
    mirrors the original document outline.
    """
    title:       str
    level:       int              # 1 = top-level, 2 = sub, 3 = subsub, …
    n:           str              # section number from <head n="1.2">, may be ""
    paragraphs:  list[Paragraph]
    subsections: list[Section]
    coords:      Coordinates | None = None

    @property
    def text(self) -> str:
        parts = [p.text for p in self.paragraphs]
        for sub in self.subsections:
            parts.append(sub.text)
        return "\n\n".join(p for p in parts if p)

    def all_sentences(self) -> list[Sentence]:
        result: list[Sentence] = []
        for p in self.paragraphs:
            result.extend(p.sentences)
        for sub in self.subsections:
            result.extend(sub.all_sentences())
        return result

    def all_paragraphs(self) -> list[Paragraph]:
        result = list(self.paragraphs)
        for sub in self.subsections:
            result.extend(sub.all_paragraphs())
        return result


# ─── Author ───────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Author:
    first:             str
    middle:            str              # space-joined middle forenames
    last:              str
    affiliation_keys:  tuple[str, ...]  # e.g. ("aff0", "aff1")
    email:             str | None = None
    orcid:             str | None = None

    @property
    def full_name(self) -> str:
        parts = [self.first, self.middle, self.last]
        return " ".join(p for p in parts if p)

    @property
    def display_name(self) -> str:
        initial = f"{self.first[0]}." if self.first else ""
        mid     = f"{self.middle[0]}." if self.middle else ""
        parts   = [initial, mid, self.last]
        return " ".join(p for p in parts if p)


# ─── Affiliation ──────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Affiliation:
    key:          str      # "aff0", "aff1" — matches Author.affiliation_keys
    institution:  str
    department:   str
    laboratory:   str
    address:      str      # settlement, postcode (joined)
    country:      str
    country_code: str      # ISO 3166-1 alpha-2 from GROBID's <country key="">
    raw:          str      # original string for provenance


# ─── Float elements ───────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Figure:
    xml_id:        str
    label:         str              # "Figure 1", "Fig. 3"
    caption:       str
    coords:        Coordinates | None
    graphic_coords: Coordinates | None = None   # inner <graphic> bbox


@dataclass(frozen=True)
class Table:
    xml_id:  str
    label:   str
    caption: str
    coords:  Coordinates | None
    rows:    tuple[tuple[str, ...], ...] = ()   # row-major cell text

    def to_text(self) -> str:
        """Flat text representation of table cells for embedding."""
        lines = [self.caption] if self.caption else []
        for row in self.rows:
            lines.append(" | ".join(row))
        return "\n".join(lines)


@dataclass(frozen=True)
class Formula:
    xml_id: str
    text:   str          # LaTeX or raw text content
    coords: Coordinates | None


# ─── Reference ────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Reference:
    """A bibliographic entry from the back-matter reference list."""
    xml_id:     str              # "b0", "b1" — target of CitationMarker.ref_id
    title:      str
    authors:    tuple[Author, ...]
    journal:    str
    volume:     str
    issue:      str
    page_from:  str
    page_to:    str
    year:       int | None
    doi:        str | None
    raw:        str              # <note type="raw_reference"> if present
    coords:     Coordinates | None = None

    @property
    def citation_key(self) -> str:
        last = self.authors[0].last if self.authors else "unknown"
        yr   = str(self.year) if self.year else "?"
        return f"{last}{yr}"

    @property
    def author_string(self) -> str:
        return "; ".join(a.full_name for a in self.authors)

    def to_dict(self) -> dict[str, Any]:
        return {
            "xml_id":    self.xml_id,
            "title":     self.title,
            "authors":   [a.full_name for a in self.authors],
            "journal":   self.journal,
            "volume":    self.volume,
            "issue":     self.issue,
            "pages":     f"{self.page_from}–{self.page_to}".strip("–"),
            "year":      self.year,
            "doi":       self.doi,
            "raw":       self.raw,
        }


# ─── TEIDocument — the core abstraction ───────────────────────────────────────

@dataclass(frozen=True)
class TEIDocument:
    """
    Parser-agnostic representation of a scientific paper.

    This is the single source of truth for all downstream processing.
    No code outside src/document/ should ever touch raw XML, BibTeX, or
    any other parser-native format.

    Frozen: never mutate after construction — derive modified copies with
    dataclasses.replace() (re-runs __post_init__, so indexes stay in sync).

    Indexes (_ref_index, _figure_index, _table_index) are built at
    construction time and kept in sync automatically via __post_init__.
    """

    # ── Identity ──────────────────────────────────────────────────────────
    paper_id: str                       # SHA-256 from ingestion pipeline

    # ── Header metadata ───────────────────────────────────────────────────
    title:        str
    abstract:     str
    authors:      list[Author]
    affiliations: list[Affiliation]
    keywords:     list[str]
    doi:          str | None
    year:         int | None
    journal:      str | None
    volume:       str | None
    issue:        str | None

    # ── Structured body ───────────────────────────────────────────────────
    sections:   list[Section]
    figures:    list[Figure]
    tables:     list[Table]
    formulas:   list[Formula]
    references: list[Reference]

    # ── Parser provenance (legacy — kept for backward compat) ────────────────
    parser:         str = "grobid"
    parser_version: str = ""

    # ── Multi-parser provenance (SDOM v2) ─────────────────────────────────
    parser_kind:         ParserKind                  = field(default=ParserKind.GROBID)
    parser_capabilities: frozenset[ParserCapability] = field(default_factory=frozenset)
    parser_provenance:   list[ParserProvenance]      = field(default_factory=list)
    document_quality:    DocumentQuality | None       = None
    source_paths:        list[str]                   = field(default_factory=list)

    # ── Optional supplementary text from visual parsers ───────────────────
    markdown_text: str | None = None   # raw Nougat markdown output
    visual_text:   str | None = None   # OCR/visual body text for scanned docs

    # ── Internal indexes (populated by __post_init__) ─────────────────────
    _ref_index:    dict[str, Reference] = field(default_factory=dict, repr=False)
    _figure_index: dict[str, Figure]    = field(default_factory=dict, repr=False)
    _table_index:  dict[str, Table]     = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        self._rebuild_indexes()

    def _rebuild_indexes(self) -> None:
        # frozen dataclass: private index fields are set via object.__setattr__
        object.__setattr__(self, "_ref_index",    {r.xml_id: r for r in self.references})
        object.__setattr__(self, "_figure_index", {f.xml_id: f for f in self.figures})
        object.__setattr__(self, "_table_index",  {t.xml_id: t for t in self.tables})

    # ── Resolution ────────────────────────────────────────────────────────

    def resolve_ref(self, ref_id: str) -> Reference | None:
        return self._ref_index.get(ref_id)

    def resolve_figure(self, fig_id: str) -> Figure | None:
        return self._figure_index.get(fig_id)

    def resolve_table(self, tab_id: str) -> Table | None:
        return self._table_index.get(tab_id)

    # ── Flat text accessors (backward-compatibility surface) ───────────────
    # These maintain the same contract as the old pipeline.py helpers so
    # existing extraction code can migrate incrementally.

    def body_text(self) -> str:
        """Full body text, section-joined.  Equivalent to build_full_text()."""
        return "\n\n".join(s.text for s in self.sections if s.text)

    def abstract_and_body(self) -> str:
        parts = []
        if self.abstract:
            parts.append(self.abstract)
        parts.append(self.body_text())
        return "\n\n".join(parts)

    def all_sentences(self) -> list[Sentence]:
        result: list[Sentence] = []
        for sec in self.sections:
            result.extend(sec.all_sentences())
        return result

    def all_paragraphs(self) -> list[Paragraph]:
        result: list[Paragraph] = []
        for sec in self.sections:
            result.extend(sec.all_paragraphs())
        return result

    def section_text(self, title_prefix: str) -> str | None:
        """Return the text of the first section whose title starts with prefix."""
        prefix_lower = title_prefix.lower()
        for sec in self.sections:
            if sec.title.lower().startswith(prefix_lower):
                return sec.text
            for sub in sec.subsections:
                if sub.title.lower().startswith(prefix_lower):
                    return sub.text
        return None

    def sections_dict(self) -> dict[str, str]:
        """{'Introduction': '...', 'Methods': '...'}  — for pipeline.py compat."""
        result: dict[str, str] = {}
        for sec in self.sections:
            if sec.title:
                result[sec.title] = sec.text
        return result

    # ── Citation graph helpers ─────────────────────────────────────────────

    def citations_in_context(self) -> list[tuple[Sentence, Reference]]:
        """All (sentence, reference) pairs where the citation resolves."""
        result = []
        for sent in self.all_sentences():
            for cm in sent.citations:
                ref = self.resolve_ref(cm.ref_id)
                if ref:
                    result.append((sent, ref))
        return result

    def citation_contexts(self, ref_id: str) -> list[Sentence]:
        """All sentences that cite a specific reference."""
        return [
            s for s in self.all_sentences()
            if any(c.ref_id == ref_id for c in s.citations)
        ]

    # ── Summary ───────────────────────────────────────────────────────────

    def summary(self) -> dict[str, Any]:
        return {
            "paper_id":          self.paper_id,
            "title":             self.title,
            "year":              self.year,
            "journal":           self.journal,
            "doi":               self.doi,
            "authors":           len(self.authors),
            "sections":          len(self.sections),
            "figures":           len(self.figures),
            "tables":            len(self.tables),
            "formulas":          len(self.formulas),
            "references":        len(self.references),
            "sentences":         len(self.all_sentences()),
            "parser":            self.parser,
            "parser_kind":       self.parser_kind.value,
            "parser_capabilities": sorted(c.value for c in self.parser_capabilities),
            "has_markdown":      self.markdown_text is not None,
            "has_visual_text":   self.visual_text is not None,
        }
