"""Full text of corpus papers as passages, sections and spans (GROBID TEI via src/document).

The API never returns whole papers: callers get sections, pages or the passages that
match a query, each with its provenance (section, page, passage id). The TEI file of a
paper is the one recorded in core.paper_file (kind 'tei', status 'ok').
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from src.paper_3.evidence import split_sentences

ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class SentenceInfo:
    text: str
    cites: tuple[tuple[str, str], ...] = ()     # (label as printed, TEI ref id)
    page: int | None = None


@dataclass(frozen=True)
class Passage:
    passage_id: str            # "abstract", "s3.p2", "s3.s1.p0", "fig_1", "tab_2"
    kind: str                  # abstract | paragraph | figure | table
    section: str
    section_n: str
    page: int | None
    text: str
    sentences: tuple[SentenceInfo, ...]


def tei_path(paper_id: str) -> Path | None:
    """The TEI file of a paper, or None when it has none. Postgres errors propagate."""
    from sqlalchemy import select

    from src.db.engine import session_scope
    from src.db.models import PaperFile
    with session_scope() as s:
        rel = s.scalar(select(PaperFile.path).where(PaperFile.paper_id == paper_id, PaperFile.kind == "tei",
                                                    PaperFile.status == "ok").limit(1))
    if not rel:
        return None
    path = ROOT / rel
    return path if path.is_file() else None


@lru_cache(maxsize=16)   # a parsed TEIDocument can take several MB
def _parse(path: str, mtime: float, paper_id: str):
    from src.document.parser import TEIParser
    return TEIParser().parse_file(Path(path), paper_id)


def load_document(paper_id: str):
    """Parsed TEIDocument, or None when the paper has no TEI. Cached per file version."""
    path = tei_path(paper_id)
    if path is None:
        return None
    return _parse(str(path), path.stat().st_mtime, paper_id)


def _page(coords) -> int | None:
    if coords is None or not getattr(coords, "boxes", None):
        return None
    return coords.boxes[0].page


def _walk(sections, prefix=""):
    for i, sec in enumerate(sections):
        sid = f"{prefix}s{i}"
        yield sec, sid
        yield from _walk(sec.subsections, prefix=f"{sid}.")


def _plain(text: str) -> tuple[SentenceInfo, ...]:
    return tuple(SentenceInfo(s) for s in split_sentences(text))


def passages(doc) -> list[Passage]:
    out: list[Passage] = []
    if doc.abstract:
        out.append(Passage("abstract", "abstract", "Abstract", "", None, doc.abstract, _plain(doc.abstract)))
    for sec, sid in _walk(doc.sections):
        for j, para in enumerate(sec.paragraphs):
            sents = tuple(SentenceInfo(s.text, tuple((c.label, c.ref_id) for c in s.citations), _page(s.coords))
                          for s in para.sentences)
            page = _page(para.coords) or next((s.page for s in sents if s.page), None)
            out.append(Passage(f"{sid}.p{j}", "paragraph", sec.title, sec.n or "", page, para.text, sents))
    for k, fig in enumerate(doc.figures):
        text = " ".join(x for x in (fig.label, fig.caption) if x)
        if text:
            out.append(Passage(f"fig_{k}", "figure", fig.label or "Figure", "", _page(fig.coords), text, _plain(text)))
    for k, tab in enumerate(doc.tables):
        rows = " ".join(" | ".join(str(c) for c in r) for r in (tab.rows or ()))
        text = " ".join(x for x in (tab.label, tab.caption, rows) if x)
        if text:
            out.append(Passage(f"tab_{k}", "table", tab.label or "Table", "", _page(tab.coords), text, _plain(text)))
    return out


def outline(doc) -> dict:
    sections = []
    for sec, sid in _walk(doc.sections):
        pages = set()
        for para in sec.paragraphs:
            pages.update(p for p in [_page(para.coords)] + [_page(s.coords) for s in para.sentences] if p)
        sections.append({"id": sid, "n": sec.n or None, "title": sec.title, "level": sec.level,
                         "pages": sorted(pages), "paragraphs": len(sec.paragraphs),
                         "chars": sum(len(p.text) for p in sec.paragraphs)})
    return {"sections": sections, "has_abstract": bool(doc.abstract), "figures": len(doc.figures),
            "tables": len(doc.tables), "formulas": len(doc.formulas or [])}


def select(doc, *, section: str | None = None, page: int | None = None, q: str | None = None) -> list[Passage]:
    """Passages by section (number, handle or title prefix, case-insensitive), page and/or query words.

    A query keeps the paragraphs that contain every word of three or more letters, plus
    one paragraph either side for context.
    """
    items = passages(doc)
    if section:
        key = section.strip().lower()
        items = [p for p in items
                 if (key == "abstract" and p.kind == "abstract")
                 or (p.kind == "paragraph" and (p.section_n.lower() == key or p.section.lower().startswith(key)
                                                or p.passage_id.split(".p")[0] == key))]
    if page is not None:
        items = [p for p in items if p.page == page]
    if q:
        words = [w for w in re.findall(r"\w+", q.lower()) if len(w) > 2]
        if words:
            hits = {i for i, p in enumerate(items) if all(w in p.text.lower() for w in words)}
            keep = sorted({j for i in hits for j in (i - 1, i, i + 1) if 0 <= j < len(items)})
            items = [items[j] for j in keep]
    return items
