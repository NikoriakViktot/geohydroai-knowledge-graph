"""
parser_runner.py — Stage 1: Structural Parsing.

Responsibility: take the raw PDF from Stage 0, run it through the parser
router (GROBID / Nougat / Hybrid), and persist all structured artifacts to
data/parsed/{paper_id}/.

Output contract (data/parsed/{paper_id}/):
    tei.xml              — raw GROBID TEI output (immutable)
    sections.parquet     — structured body sections with word counts
    figures.parquet      — figure metadata + layout coordinates
    tables.parquet       — table metadata + row/col structure
    equations.parquet    — formula text + layout coordinates
    references.parquet   — back-matter bibliography entries
    captions.parquet     — unified caption index (figures + tables)
    parsing_manifest.json — stage completion + quality record

Design rules:
  - NO NLP, NO embeddings, NO ontology extraction here.
  - Reads only from data/raw/{paper_id}/ (via Stage0Result).
  - Never mutates Stage 0 artifacts.
  - All writes go through ParsedStore (atomic + idempotent).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.config.settings import PARSED_DIR, PARSER_STRATEGY, PIPELINE_VERSION
from src.document.models import Figure, Formula, Reference, Section, Table, TEIDocument
from src.ingestion.stage0.ingestor import Stage0Result
from src.ingestion.stage1.parsed_store import ParsedStore

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Stage1Result:
    """Output contract of Stage1Parser.run()."""
    paper_id:       str
    status:         str           # "ok" | "skipped" | "error"
    parsed_root:    Path
    section_count:  int = 0
    figure_count:   int = 0
    table_count:    int = 0
    equation_count: int = 0
    ref_count:      int = 0
    events:         list[str] = field(default_factory=list)

    @property
    def should_continue(self) -> bool:
        return self.status == "ok"


class Stage1Parser:
    """
    Parses one PDF into structured Stage 1 artifacts.

    Parameters
    ----------
    parsed_root     : Root of data/parsed/ tree.
    strategy        : Parser strategy (see parser_router.py STRATEGIES).
    pipeline_hash   : Pipeline version identifier for artifact provenance.
    force           : Re-parse even if parsed store is already complete.
    """

    def __init__(
        self,
        parsed_root:   Path | None = None,
        strategy:      str | None  = None,
        pipeline_hash: str | None  = None,
        force:         bool        = False,
    ) -> None:
        self._parsed_root   = parsed_root   or PARSED_DIR
        self._strategy      = strategy      or PARSER_STRATEGY
        self._pipeline_hash = pipeline_hash or PIPELINE_VERSION
        self._force         = force

    def run(self, stage0: Stage0Result) -> Stage1Result:
        """
        Parse the PDF from a completed Stage 0 result.

        Parameters
        ----------
        stage0 : A successful Stage0Result (status == "ok").

        Returns
        -------
        Stage1Result — never raises.
        """
        if not stage0.should_continue:
            return Stage1Result(
                paper_id=stage0.paper_id, status="error",
                parsed_root=self._parsed_root,
                events=[f"stage0_not_ok:{stage0.status}"],
            )

        paper_id = stage0.paper_id
        store    = ParsedStore(paper_id, self._pipeline_hash, self._parsed_root)

        if store.is_complete and not self._force:
            log.debug("[stage1] already complete — %s", paper_id[:16])
            return Stage1Result(
                paper_id=paper_id, status="skipped",
                parsed_root=store.root,
                events=["already_complete"],
            )

        pdf_path = stage0.raw_root / paper_id / "paper.pdf"
        if not pdf_path.exists():
            return Stage1Result(
                paper_id=paper_id, status="error",
                parsed_root=store.root,
                events=["pdf_not_found"],
            )

        events: list[str] = [f"start:{_now_iso()}"]

        # ── Parse PDF via router ──────────────────────────────────────────────
        try:
            from src.document.parser_router import ParserRouter
            router = ParserRouter(strategy=self._strategy)
            doc    = router.parse_pdf(pdf_path, paper_id=paper_id)
        except Exception as exc:
            log.error("[stage1] parse failed for %s: %s", paper_id[:16], exc)
            return Stage1Result(
                paper_id=paper_id, status="error",
                parsed_root=store.root,
                events=events + [f"parse_error:{exc}"],
            )

        events.append(f"parsed:strategy={self._strategy}")

        # ── Persist TEI XML (if GROBID produced it) ───────────────────────────
        try:
            _write_tei_if_available(doc, store)
        except Exception as exc:
            log.warning("[stage1] tei write failed for %s: %s", paper_id[:16], exc)

        # ── Extract + write all parquet artifacts ─────────────────────────────
        try:
            counts = _write_all_artifacts(doc, store)
        except Exception as exc:
            log.error("[stage1] artifact write failed for %s: %s", paper_id[:16], exc)
            return Stage1Result(
                paper_id=paper_id, status="error",
                parsed_root=store.root,
                events=events + [f"artifact_error:{exc}"],
            )

        # ── Write manifest ────────────────────────────────────────────────────
        store.write_manifest({
            "parser_kind":   doc.parser_kind.value,
            "strategy":      self._strategy,
            "title":         doc.title,
            "section_count": counts["sections"],
            "figure_count":  counts["figures"],
            "table_count":   counts["tables"],
            "equation_count":counts["equations"],
            "ref_count":     counts["references"],
        })

        events.append(f"done:{_now_iso()}")
        log.info(
            "[stage1] complete — %s secs=%d figs=%d tabs=%d eqs=%d refs=%d",
            paper_id[:16],
            counts["sections"], counts["figures"], counts["tables"],
            counts["equations"], counts["references"],
        )

        return Stage1Result(
            paper_id=paper_id, status="ok",
            parsed_root=store.root,
            section_count=counts["sections"],
            figure_count=counts["figures"],
            table_count=counts["tables"],
            equation_count=counts["equations"],
            ref_count=counts["references"],
            events=events,
        )


# ── Artifact extraction helpers ───────────────────────────────────────────────

def _write_tei_if_available(doc: TEIDocument, store: ParsedStore) -> None:
    """Write tei.xml if the parser recorded the raw source path."""
    for src in doc.source_paths:
        if src.endswith(".xml") and Path(src).exists():
            store.write_tei(Path(src).read_text(encoding="utf-8"))
            return
    # Write empty sentinel so is_complete doesn't block forever when
    # Nougat-only parse produces no TEI.
    if not store.tei_path.exists():
        store.write_tei("<!-- no TEI: non-GROBID parse -->")


def _write_all_artifacts(doc: TEIDocument, store: ParsedStore) -> dict[str, int]:
    """Convert TEIDocument objects to parquet rows and write atomically."""
    pid    = doc.paper_id
    parser = doc.parser_kind.value

    # Sections — flatten hierarchy into a sequential numbered list
    section_rows: list[dict[str, Any]] = []
    _flatten_sections(doc.sections, pid, parser, section_rows)
    store.write_sections(section_rows)

    # Figures
    fig_rows = [_figure_row(f, i, pid, parser) for i, f in enumerate(doc.figures)]
    store.write_figures(fig_rows)

    # Tables
    tbl_rows = [_table_row(t, i, pid, parser) for i, t in enumerate(doc.tables)]
    store.write_tables(tbl_rows)

    # Equations
    eq_rows = [_equation_row(f, i, pid, parser) for i, f in enumerate(doc.formulas)]
    store.write_equations(eq_rows)

    # References
    ref_rows = [_ref_row(r, pid, parser) for r in doc.references]
    store.write_references(ref_rows)

    # Captions — unified index from figures + tables
    caption_rows = _build_captions(fig_rows, tbl_rows, pid, parser)
    store.write_captions(caption_rows)

    return {
        "sections":   len(section_rows),
        "figures":    len(fig_rows),
        "tables":     len(tbl_rows),
        "equations":  len(eq_rows),
        "references": len(ref_rows),
    }


def _flatten_sections(
    sections:     list[Section],
    paper_id:     str,
    source_parser:str,
    out:          list[dict[str, Any]],
    counter:      list[int] | None = None,
) -> None:
    if counter is None:
        counter = [0]
    for sec in sections:
        n = counter[0]
        counter[0] += 1
        c = sec.coords
        out.append({
            "section_id":    f"{paper_id}_sec_{n:03d}",
            "paper_id":      paper_id,
            "title":         sec.title or None,
            "level":         sec.level,
            "n":             sec.n or None,
            "text":          sec.text or None,
            "word_count":    len(sec.text.split()) if sec.text else 0,
            "page":          c.page if c else None,
            "bbox_x0":       float(c.x0) if c else None,
            "bbox_y0":       float(c.y0) if c else None,
            "bbox_x1":       float(c.x1) if c else None,
            "bbox_y1":       float(c.y1) if c else None,
            "source_parser": source_parser,
        })
        if sec.subsections:
            _flatten_sections(sec.subsections, paper_id, source_parser, out, counter)


def _figure_row(fig: Figure, n: int, paper_id: str, source_parser: str) -> dict[str, Any]:
    c  = fig.coords
    gc = fig.graphic_coords
    return {
        "figure_id":     f"{paper_id}_fig_{n:03d}",
        "paper_id":      paper_id,
        "xml_id":        fig.xml_id or None,
        "label":         fig.label or None,
        "caption":       fig.caption or None,
        "page":          c.page if c else None,
        "bbox_x0":       float(c.x0) if c else None,
        "bbox_y0":       float(c.y0) if c else None,
        "bbox_x1":       float(c.x1) if c else None,
        "bbox_y1":       float(c.y1) if c else None,
        "graphic_page":  gc.page if gc else None,
        "graphic_x0":    float(gc.x0) if gc else None,
        "graphic_y0":    float(gc.y0) if gc else None,
        "graphic_x1":    float(gc.x1) if gc else None,
        "graphic_y1":    float(gc.y1) if gc else None,
        "source_parser": source_parser,
    }


def _table_row(tbl: Table, n: int, paper_id: str, source_parser: str) -> dict[str, Any]:
    c = tbl.coords
    header = list(tbl.rows[0]) if tbl.rows else None
    has_num = _table_has_numeric(tbl)
    return {
        "table_id":        f"{paper_id}_tbl_{n:03d}",
        "paper_id":        paper_id,
        "region_id":       None,
        "xml_id":          tbl.xml_id or None,
        "page":            c.page if c else None,
        "label":           tbl.label or None,
        "caption":         tbl.caption or None,
        "header_row":      header,
        "row_count":       len(tbl.rows),
        "col_count":       len(tbl.rows[0]) if tbl.rows else 0,
        "has_numeric_data": has_num,
        "source_parser":   source_parser,
    }


def _equation_row(f: Formula, n: int, paper_id: str, source_parser: str) -> dict[str, Any]:
    c = f.coords
    return {
        "equation_id":   f"{paper_id}_eq_{n:03d}",
        "paper_id":      paper_id,
        "xml_id":        f.xml_id or None,
        "text":          f.text or None,
        "page":          c.page if c else None,
        "bbox_x0":       float(c.x0) if c else None,
        "bbox_y0":       float(c.y0) if c else None,
        "bbox_x1":       float(c.x1) if c else None,
        "bbox_y1":       float(c.y1) if c else None,
        "source_parser": source_parser,
    }


def _ref_row(ref: Reference, paper_id: str, source_parser: str) -> dict[str, Any]:
    return {
        "ref_id":        f"{paper_id}_ref_{ref.xml_id}",
        "paper_id":      paper_id,
        "xml_id":        ref.xml_id or None,
        "title":         ref.title or None,
        "authors":       ref.author_string or None,
        "journal":       ref.journal or None,
        "year":          ref.year,
        "doi":           ref.doi or None,
        "raw":           ref.raw or None,
        "source_parser": source_parser,
    }


def _build_captions(
    fig_rows: list[dict[str, Any]],
    tbl_rows: list[dict[str, Any]],
    paper_id: str,
    source_parser: str,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    n = 0
    for row in fig_rows:
        caption = row.get("caption")
        if caption:
            rows.append({
                "caption_id":    f"{paper_id}_cap_{n:03d}",
                "paper_id":      paper_id,
                "parent_id":     row["figure_id"],
                "parent_type":   "figure",
                "label":         row.get("label"),
                "text":          caption,
                "page":          row.get("page"),
                "source_parser": source_parser,
            })
            n += 1
    for row in tbl_rows:
        caption = row.get("caption")
        if caption:
            rows.append({
                "caption_id":    f"{paper_id}_cap_{n:03d}",
                "paper_id":      paper_id,
                "parent_id":     row["table_id"],
                "parent_type":   "table",
                "label":         row.get("label"),
                "text":          caption,
                "page":          row.get("page"),
                "source_parser": source_parser,
            })
            n += 1
    return rows


def _table_has_numeric(tbl: Table) -> bool:
    import re
    _NUM = re.compile(r"\b\d+[.,]?\d*\b")
    for row in tbl.rows:
        for cell in row:
            if _NUM.search(cell):
                return True
    return False


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
