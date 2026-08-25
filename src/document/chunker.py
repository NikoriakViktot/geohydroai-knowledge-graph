"""
chunker.py — Layout-aware DocumentChunk generation.

Converts a TEIDocument into a flat list of DocumentChunks for downstream
retrieval, embedding, and RAG.  Each chunk carries its spatial position,
section provenance, and grounding links to nearby figures/tables.

Chunking strategies
-------------------
sentence   One chunk per sentence.  Fine-grained, best for RAG.  Preserves
           GROBID coordinate precision (sentence-level bboxes).
paragraph  One chunk per paragraph.  Mid-grained; good when sentence
           segmentation is noisy or absent.
section    One chunk per top-level section.  Coarse; useful for overview
           retrieval or topic classification.

Proximity detection
-------------------
A sentence/paragraph is flagged near_figure / near_table when its primary
bounding box is within PROXIMITY_THRESHOLD PDF points of any float on the
same page.  The closest float wins.  Default threshold = 150 pt ≈ 2 inches.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from src.document.coordinates import Coordinates
from src.document.models import (
    Figure,
    Paragraph,
    Section,
    Sentence,
    Table,
    TEIDocument,
)


# ─── Chunk model ──────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class DocumentChunk:
    """
    The atomic unit for retrieval, embedding, and RAG.

    Every chunk knows where it came from (section + page + bbox), what floats
    are nearby, and which references it cites.
    """
    chunk_id:      str
    paper_id:      str
    text:          str

    # Provenance
    section_title: str
    section_level: int
    section_n:     str              # "1.2" or "" if unnumbered

    # Layout
    page:          int
    bbox:          tuple[float, float, float, float] | None   # (x, y, w, h)

    # Grounding
    near_figure:   bool
    figure_id:     str | None
    near_table:    bool
    table_id:      str | None

    # Citations in this chunk → ids that can be resolved via TEIDocument
    citations:     tuple[str, ...]

    # Chunk type for downstream routing
    chunk_type:    str   # "abstract" | "sentence" | "paragraph" | "section"

    def to_dict(self) -> dict[str, Any]:
        return {
            "chunk_id":      self.chunk_id,
            "paper_id":      self.paper_id,
            "text":          self.text,
            "section":       self.section_title,
            "section_level": self.section_level,
            "section_n":     self.section_n,
            "page":          self.page,
            "bbox":          list(self.bbox) if self.bbox else None,
            "near_figure":   self.near_figure,
            "figure_id":     self.figure_id,
            "near_table":    self.near_table,
            "table_id":      self.table_id,
            "citations":     list(self.citations),
            "chunk_type":    self.chunk_type,
        }


# ─── Chunker ──────────────────────────────────────────────────────────────────

class LayoutAwareChunker:
    """
    Convert a TEIDocument into layout-aware DocumentChunks.

    Parameters
    ----------
    strategy:           "sentence" | "paragraph" | "section"
    proximity_threshold: Maximum distance (PDF points) to mark near_figure/
                         near_table.  Default 150 pt ≈ 2 inches.
    min_text_length:    Chunks shorter than this are discarded.
                        Prevents flooding the index with footnote fragments.
    """

    def __init__(
        self,
        strategy:            str   = "sentence",
        proximity_threshold: float = 150.0,
        min_text_length:     int   = 20,
    ) -> None:
        if strategy not in {"sentence", "paragraph", "section"}:
            raise ValueError(f"Unknown strategy: {strategy!r}")
        self._strategy   = strategy
        self._threshold  = proximity_threshold
        self._min_len    = min_text_length

    # ── Public API ────────────────────────────────────────────────────────────

    def chunk(self, doc: TEIDocument) -> list[DocumentChunk]:
        chunks: list[DocumentChunk] = []

        if doc.abstract:
            chunks.append(self._abstract_chunk(doc))

        for section in doc.sections:
            chunks.extend(self._chunk_section(doc, section))

        # Float chunks — grounded to their PDF bbox
        chunks.extend(self._figure_chunks(doc))
        chunks.extend(self._table_chunks(doc))
        chunks.extend(self._formula_chunks(doc))

        return [c for c in chunks if len(c.text) >= self._min_len]

    # ── Abstract ──────────────────────────────────────────────────────────────

    def _abstract_chunk(self, doc: TEIDocument) -> DocumentChunk:
        return DocumentChunk(
            chunk_id      = self._make_id(doc.paper_id, "abstract"),
            paper_id      = doc.paper_id,
            text          = doc.abstract,
            section_title = "Abstract",
            section_level = 0,
            section_n     = "",
            page          = 1,
            bbox          = None,
            near_figure   = False,
            figure_id     = None,
            near_table    = False,
            table_id      = None,
            citations     = (),
            chunk_type    = "abstract",
        )

    # ── Sections ──────────────────────────────────────────────────────────────

    def _chunk_section(
        self,
        doc: TEIDocument,
        section: Section,
    ) -> list[DocumentChunk]:
        chunks: list[DocumentChunk] = []

        if self._strategy == "sentence":
            for para in section.paragraphs:
                for i, sent in enumerate(para.sentences):
                    c = self._sentence_chunk(doc, section, sent, i)
                    if c:
                        chunks.append(c)

        elif self._strategy == "paragraph":
            for i, para in enumerate(section.paragraphs):
                c = self._paragraph_chunk(doc, section, para, i)
                if c:
                    chunks.append(c)

        else:  # section
            c = self._section_chunk(doc, section)
            if c:
                chunks.append(c)

        for sub in section.subsections:
            chunks.extend(self._chunk_section(doc, sub))

        return chunks

    # ── Per-granularity builders ──────────────────────────────────────────────

    def _sentence_chunk(
        self,
        doc: TEIDocument,
        section: Section,
        sent: Sentence,
        idx: int,
    ) -> DocumentChunk | None:
        if not sent.text:
            return None
        fig_id, tab_id = self._nearest_floats(sent.coords, doc)
        page, bbox     = self._layout(sent.coords)
        text_sig       = hashlib.sha1((sent.text or "").encode()).hexdigest()[:8]
        key            = f"{section.n or section.title[:12]}_{idx}_{text_sig}"

        return DocumentChunk(
            chunk_id      = self._make_id(doc.paper_id, key),
            paper_id      = doc.paper_id,
            text          = sent.text,
            section_title = section.title,
            section_level = section.level,
            section_n     = section.n,
            page          = page,
            bbox          = bbox,
            near_figure   = fig_id is not None,
            figure_id     = fig_id,
            near_table    = tab_id is not None,
            table_id      = tab_id,
            citations     = tuple(c.ref_id for c in sent.citations),
            chunk_type    = "sentence",
        )

    def _paragraph_chunk(
        self,
        doc: TEIDocument,
        section: Section,
        para: Paragraph,
        idx: int,
    ) -> DocumentChunk | None:
        text = para.text
        if not text:
            return None
        fig_id, tab_id = self._nearest_floats(para.coords, doc)
        page, bbox     = self._layout(para.coords)
        key            = f"{section.n or section.title[:12]}_p{idx}"
        all_cites      = tuple(c.ref_id for c in para.all_citations)

        return DocumentChunk(
            chunk_id      = self._make_id(doc.paper_id, key),
            paper_id      = doc.paper_id,
            text          = text,
            section_title = section.title,
            section_level = section.level,
            section_n     = section.n,
            page          = page,
            bbox          = bbox,
            near_figure   = fig_id is not None,
            figure_id     = fig_id,
            near_table    = tab_id is not None,
            table_id      = tab_id,
            citations     = all_cites,
            chunk_type    = "paragraph",
        )

    def _section_chunk(
        self,
        doc: TEIDocument,
        section: Section,
    ) -> DocumentChunk | None:
        text = section.text
        if not text:
            return None
        all_cites = tuple(
            c.ref_id
            for s in section.all_sentences()
            for c in s.citations
        )
        page, bbox = self._layout(section.coords)

        return DocumentChunk(
            chunk_id      = self._make_id(doc.paper_id, section.n or section.title[:24]),
            paper_id      = doc.paper_id,
            text          = text,
            section_title = section.title,
            section_level = section.level,
            section_n     = section.n,
            page          = page,
            bbox          = bbox,
            near_figure   = False,
            figure_id     = None,
            near_table    = False,
            table_id      = None,
            citations     = all_cites,
            chunk_type    = "section",
        )

    # ── Float (figure / table / formula) chunks ──────────────────────────────

    def _figure_chunks(self, doc: TEIDocument) -> list[DocumentChunk]:
        chunks = []
        for fig in doc.figures:
            text = fig.caption or fig.label
            if not text:
                continue
            page, bbox = self._layout(fig.coords)
            chunks.append(DocumentChunk(
                chunk_id      = self._make_id(doc.paper_id, f"fig_{fig.xml_id}"),
                paper_id      = doc.paper_id,
                text          = text,
                section_title = "Figure",
                section_level = 0,
                section_n     = fig.xml_id,
                page          = page,
                bbox          = bbox,
                near_figure   = True,
                figure_id     = fig.xml_id,
                near_table    = False,
                table_id      = None,
                citations     = (),
                chunk_type    = "figure",
            ))
        return chunks

    def _table_chunks(self, doc: TEIDocument) -> list[DocumentChunk]:
        chunks = []
        for tab in doc.tables:
            text = tab.caption or tab.label
            if not text:
                continue
            page, bbox = self._layout(tab.coords)
            chunks.append(DocumentChunk(
                chunk_id      = self._make_id(doc.paper_id, f"tab_{tab.xml_id}"),
                paper_id      = doc.paper_id,
                text          = text,
                section_title = "Table",
                section_level = 0,
                section_n     = tab.xml_id,
                page          = page,
                bbox          = bbox,
                near_figure   = False,
                figure_id     = None,
                near_table    = True,
                table_id      = tab.xml_id,
                citations     = (),
                chunk_type    = "table",
            ))
        return chunks

    def _formula_chunks(self, doc: TEIDocument) -> list[DocumentChunk]:
        chunks = []
        for formula in doc.formulas:
            if not formula.text:
                continue
            page, bbox = self._layout(formula.coords)
            chunks.append(DocumentChunk(
                chunk_id      = self._make_id(doc.paper_id, f"form_{formula.xml_id}"),
                paper_id      = doc.paper_id,
                text          = formula.text,
                section_title = "Formula",
                section_level = 0,
                section_n     = formula.xml_id,
                page          = page,
                bbox          = bbox,
                near_figure   = False,
                figure_id     = None,
                near_table    = False,
                table_id      = None,
                citations     = (),
                chunk_type    = "formula",
            ))
        return chunks

    # ── Layout helpers ────────────────────────────────────────────────────────

    @staticmethod
    def _layout(
        coords: Coordinates | None,
    ) -> tuple[int, tuple[float, float, float, float] | None]:
        if coords is None or not coords.boxes:
            return 0, None
        b = coords.boxes[0]
        return b.page, (b.x, b.y, b.w, b.h)

    def _nearest_floats(
        self,
        coords: Coordinates | None,
        doc: TEIDocument,
    ) -> tuple[str | None, str | None]:
        """Return (figure_id, table_id) of the closest float within threshold."""
        if coords is None:
            return None, None

        fig_id    = None
        best_fig  = float("inf")
        for fig in doc.figures:
            if fig.coords is not None:
                d = coords.min_distance_to(fig.coords)
                if d <= self._threshold and d < best_fig:
                    best_fig = d
                    fig_id   = fig.xml_id

        tab_id    = None
        best_tab  = float("inf")
        for tab in doc.tables:
            if tab.coords is not None:
                d = coords.min_distance_to(tab.coords)
                if d <= self._threshold and d < best_tab:
                    best_tab = d
                    tab_id   = tab.xml_id

        return fig_id, tab_id

    # ── ID generation ─────────────────────────────────────────────────────────

    @staticmethod
    def _make_id(paper_id: str, key: str) -> str:
        raw = f"{paper_id[:16]}:{key}"
        return hashlib.sha1(raw.encode()).hexdigest()[:20]
