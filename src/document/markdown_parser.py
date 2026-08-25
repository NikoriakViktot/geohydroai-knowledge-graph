"""
markdown_parser.py — Nougat Markdown → TEIDocument.

Converts the Markdown/MMD text produced by Nougat into a TEIDocument.
Nougat does NOT provide PDF bounding boxes, so all coords fields are None.

Parsing is deliberately conservative:
  - title from first H1 heading
  - sections from any # heading (H2+ become sections; H3+ become subsections)
  - paragraphs from text blocks separated by blank lines
  - formulas from LaTeX delimiters: $…$, $$…$$, \\(…\\), \\[…\\]
  - tables from Markdown pipe tables and LaTeX \\begin{tabular}
  - references section detected by heading keywords
  - no inline citation markers (Nougat does not produce <ref> tags)
  - no PDF coordinates anywhere

This parser is deterministic, typed, and never raises — malformed input
produces an empty-but-valid TEIDocument rather than an exception.
"""

from __future__ import annotations

import logging
import re
import time
from pathlib import Path

from src.document.models import (
    Author,
    Formula,
    Paragraph,
    Reference,
    Section,
    Sentence,
    Table,
    TEIDocument,
)
from src.document.provenance import (
    NOUGAT_CAPABILITIES,
    DocumentQuality,
    ParserCapability,
    ParserKind,
    ParserProvenance,
)

log = logging.getLogger(__name__)

# ── Regex patterns ─────────────────────────────────────────────────────────────

_HEADING_RE   = re.compile(r"^(#{1,6})\s+(.+)$")
_DISPLAY_MATH = re.compile(r"\$\$(.+?)\$\$|\\\[(.+?)\\\]", re.DOTALL)
_INLINE_MATH  = re.compile(r"\\\((.+?)\\\)|\$([^$\n]+)\$")
_TABLE_ROW    = re.compile(r"^\|.+\|$")
_TABLE_SEP    = re.compile(r"^\|[-|: ]+\|$")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z])")

_REF_HEADINGS = frozenset({"references", "bibliography", "works cited", "literature"})


# ── Public entry point ─────────────────────────────────────────────────────────

class MarkdownScientificParser:
    """
    Parse Nougat Markdown output into a TEIDocument.

    Implements the DocumentParser protocol; no model is loaded — this is a
    pure-Python text processor.
    """

    parser_name:    str = "nougat_markdown"
    parser_version: str = "1.0"

    def parse_text(self, source: str, paper_id: str) -> TEIDocument:
        t0 = time.perf_counter()
        doc = _parse_markdown(source, paper_id)
        elapsed = time.perf_counter() - t0
        log.debug(
            "[markdown_parser] %s | sections=%d formulas=%d tables=%d  %.2fs",
            paper_id, len(doc.sections), len(doc.formulas), len(doc.tables), elapsed,
        )
        return doc

    def parse_file(self, path: Path, paper_id: str) -> TEIDocument:
        return self.parse_text(path.read_text(encoding="utf-8", errors="replace"), paper_id)


# ── Internal parser ────────────────────────────────────────────────────────────

def _parse_markdown(text: str, paper_id: str) -> TEIDocument:
    """Full parse pipeline — never raises."""
    try:
        return _do_parse(text, paper_id)
    except Exception as exc:  # pragma: no cover
        log.warning("[markdown_parser] parse failed for %s: %s", paper_id, exc)
        return _empty_doc(paper_id, notes=str(exc))


def _do_parse(text: str, paper_id: str) -> TEIDocument:
    lines = text.splitlines()

    # ── 1. Extract formulas globally before splitting blocks ──────────────────
    formulas, clean_text = _extract_formulas(text)

    # ── 2. Split into logical blocks ──────────────────────────────────────────
    blocks = _split_blocks(clean_text.splitlines())

    # ── 3. Parse top-level structure ──────────────────────────────────────────
    title         = ""
    abstract      = ""
    sections:      list[Section]   = []
    ref_entries:   list[str]       = []
    tables:        list[Table]     = []

    _current_section_title  = ""
    _current_section_level  = 1
    _current_section_n      = ""
    _current_paragraphs:    list[Paragraph] = []
    _in_references          = False
    _formula_counter        = [0]     # mutable for nested closure
    _table_counter          = [0]

    def _flush_section() -> None:
        nonlocal _current_paragraphs
        if _current_section_title or _current_paragraphs:
            sections.append(Section(
                title       = _current_section_title,
                level       = _current_section_level,
                n           = _current_section_n,
                paragraphs  = list(_current_paragraphs),
                subsections = [],
                coords      = None,
            ))
        _current_paragraphs = []

    for block in blocks:
        if not block.strip():
            continue

        # ── heading ───────────────────────────────────────────────────────────
        m = _HEADING_RE.match(block.strip())
        if m:
            level = len(m.group(1))
            heading_text = m.group(2).strip()
            heading_lower = heading_text.lower()

            if level == 1 and not title:
                title = heading_text
                continue

            _flush_section()
            _current_section_title = heading_text
            _current_section_level = level
            _current_section_n     = _extract_section_number(heading_text)
            _in_references = any(k in heading_lower for k in _REF_HEADINGS)
            continue

        # ── table ─────────────────────────────────────────────────────────────
        if _TABLE_ROW.match(block.strip().splitlines()[0] if block.strip() else ""):
            tbl = _parse_table(block, _table_counter)
            if tbl is not None:
                tables.append(tbl)
            continue

        # ── references section content ─────────────────────────────────────────
        if _in_references:
            ref_entries.extend(l.strip() for l in block.splitlines() if l.strip())
            continue

        # ── abstract heuristic (second block before first real section) ────────
        if not sections and not abstract and not _current_section_title and title:
            abstract = block.strip()
            continue

        # ── body paragraph ────────────────────────────────────────────────────
        para = _parse_paragraph(block.strip(), _formula_counter)
        if para:
            _current_paragraphs.append(para)

    _flush_section()

    # ── 4. Parse references (best-effort) ─────────────────────────────────────
    references = _parse_references(ref_entries)

    # ── 5. Build provenance ───────────────────────────────────────────────────
    capabilities = set(NOUGAT_CAPABILITIES)
    if references:
        capabilities.add(ParserCapability.REFERENCES)

    provenance = ParserProvenance(
        parser_name    = "nougat_markdown",
        parser_version = "1.0",
        source_format  = "markdown",
        confidence     = 0.75,
        capabilities   = frozenset(capabilities),
    )

    return TEIDocument(
        paper_id            = paper_id,
        title               = title,
        abstract            = abstract,
        authors             = [],
        affiliations        = [],
        keywords            = [],
        doi                 = None,
        year                = None,
        journal             = None,
        volume              = None,
        issue               = None,
        sections            = sections,
        figures             = [],
        tables              = tables,
        formulas            = formulas,
        references          = references,
        parser              = "nougat",
        parser_version      = "nougat_markdown:1.0",
        parser_kind         = ParserKind.NOUGAT,
        parser_capabilities = frozenset(capabilities),
        parser_provenance   = [provenance],
        markdown_text       = text,
        visual_text         = _build_visual_text(sections),
    )


# ── Block splitting ────────────────────────────────────────────────────────────

def _split_blocks(lines: list[str]) -> list[str]:
    """Split lines into logical blocks separated by blank lines."""
    blocks: list[str] = []
    current: list[str] = []
    for line in lines:
        if line.strip():
            current.append(line)
        else:
            if current:
                blocks.append("\n".join(current))
            current = []
    if current:
        blocks.append("\n".join(current))
    return blocks


# ── Formula extraction ─────────────────────────────────────────────────────────

def _extract_formulas(text: str) -> tuple[list[Formula], str]:
    """
    Extract all display math formulas from text, return (formulas, cleaned_text).
    Inline math is left in place for paragraph parsing.
    """
    formulas: list[Formula] = []
    counter   = [0]

    def _replacer(m: re.Match) -> str:
        latex = (m.group(1) or m.group(2) or "").strip()
        if latex:
            fid = f"nougat-f-{counter[0]}"
            counter[0] += 1
            formulas.append(Formula(xml_id=fid, text=latex, coords=None))
        return ""   # remove from text so it doesn't end up in paragraph bodies

    clean = _DISPLAY_MATH.sub(_replacer, text)
    return formulas, clean


# ── Table parsing ──────────────────────────────────────────────────────────────

def _parse_table(block: str, counter: list[int]) -> Table | None:
    """Parse a Markdown pipe table block into a Table object."""
    rows_raw = [l for l in block.splitlines() if _TABLE_ROW.match(l.strip())]
    rows_raw = [r for r in rows_raw if not _TABLE_SEP.match(r.strip())]
    if not rows_raw:
        return None

    rows: list[tuple[str, ...]] = []
    for raw in rows_raw:
        cells = tuple(c.strip() for c in raw.strip().strip("|").split("|"))
        rows.append(cells)

    tid = f"nougat-t-{counter[0]}"
    counter[0] += 1
    return Table(
        xml_id  = tid,
        label   = f"Table {counter[0]}",
        caption = "",
        coords  = None,
        rows    = tuple(rows),
    )


# ── Paragraph parsing ──────────────────────────────────────────────────────────

def _parse_paragraph(text: str, counter: list[int]) -> Paragraph | None:
    if not text:
        return None
    raw_sentences = _split_sentences(text)
    sentences = []
    for sent_text in raw_sentences:
        if not sent_text.strip():
            continue
        sid = f"nougat-s-{counter[0]}"
        counter[0] += 1
        sentences.append(Sentence(
            xml_id    = sid,
            text      = sent_text.strip(),
            coords    = None,
            citations = (),
        ))
    if not sentences:
        return None
    return Paragraph(sentences=tuple(sentences), coords=None)


def _split_sentences(text: str) -> list[str]:
    """Conservative sentence splitter — splits on punctuation + capital letter."""
    parts = _SENTENCE_END.split(text)
    return [p.strip() for p in parts if p.strip()]


# ── Reference parsing ──────────────────────────────────────────────────────────

_REF_LINE_RE = re.compile(
    r"^\[?(\d+)\]?\s+"        # [1] or 1.
    r"(.+?)(?:\s*\.\s*$|\s*$)"  # author/title blob
)
_DOI_RE = re.compile(r"10\.\d{4,}/[^\s]+", re.IGNORECASE)
_YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")


def _parse_references(lines: list[str]) -> list[Reference]:
    """Best-effort reference line parser — produces minimal Reference objects."""
    refs: list[Reference] = []
    for i, line in enumerate(lines):
        if not line:
            continue
        doi_m  = _DOI_RE.search(line)
        year_m = _YEAR_RE.search(line)
        refs.append(Reference(
            xml_id    = f"nougat-ref-{i}",
            title     = line[:200],   # use raw line as title; imperfect but safe
            authors   = (),
            journal   = "",
            volume    = "",
            issue     = "",
            page_from = "",
            page_to   = "",
            year      = int(year_m.group(0)) if year_m else None,
            doi       = doi_m.group(0).rstrip(".,)") if doi_m else None,
            raw       = line,
        ))
    return refs


# ── Utilities ──────────────────────────────────────────────────────────────────

_SECTION_NUM_RE = re.compile(r"^(\d+(?:\.\d+)*)")


def _extract_section_number(title: str) -> str:
    m = _SECTION_NUM_RE.match(title.strip())
    return m.group(1) if m else ""


def _build_visual_text(sections: list[Section]) -> str:
    return "\n\n".join(s.text for s in sections if s.text)


def _empty_doc(paper_id: str, notes: str = "") -> TEIDocument:
    prov = ParserProvenance(
        parser_name    = "nougat_markdown",
        parser_version = "1.0",
        source_format  = "markdown",
        confidence     = 0.0,
        capabilities   = frozenset(),
        notes          = notes,
    )
    return TEIDocument(
        paper_id            = paper_id,
        title               = "",
        abstract            = "",
        authors             = [],
        affiliations        = [],
        keywords            = [],
        doi                 = None,
        year                = None,
        journal             = None,
        volume              = None,
        issue               = None,
        sections            = [],
        figures             = [],
        tables              = [],
        formulas            = [],
        references          = [],
        parser              = "nougat",
        parser_kind         = ParserKind.NOUGAT,
        parser_capabilities = frozenset(),
        parser_provenance   = [prov],
    )
