"""Chunk ids must be unique across papers (API_PLAN_v1 О-24).

_make_id used to hash only paper_id[:16], so every "10.1016_j.jhydro…" paper got
the same abstract / figure / table / formula ids and a Chroma upsert kept only the
last paper of each group: 2,462 of 4,955 indexed papers kept an abstract chunk.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from src.document.chunker import LayoutAwareChunker
from src.document.models import Figure, Formula, Table, TEIDocument

# Same first 16 characters ("10.1016_j.jhydro"), different papers.
PAPER_A = "10.1016_j.jhydrol.2020.124001"
PAPER_B = "10.1016_j.jhydrol.2021.126500"


def _float(cls, xml_id: str, text: str):
    obj = MagicMock(spec=cls)
    obj.xml_id = xml_id
    obj.label = text
    obj.caption = text
    obj.coords = None
    obj.graphic_coords = None
    if cls is Table:
        obj.rows = []
        obj.text = text
    if cls is Formula:
        obj.text = text
        obj.latex = None
    return obj


def _doc(paper_id: str) -> MagicMock:
    doc = MagicMock(spec=TEIDocument)
    doc.paper_id = paper_id
    doc.abstract = f"Abstract of {paper_id} about flood inundation mapping."
    doc.sections = []
    doc.figures = [_float(Figure, "fig_0", f"Study area of {paper_id}.")]
    doc.tables = [_float(Table, "tab_0", f"Calibration results of {paper_id}.")]
    doc.formulas = [_float(Formula, "formula_0", f"NSE definition in {paper_id}.")]
    return doc


def test_make_id_uses_the_whole_paper_id():
    assert PAPER_A[:16] == PAPER_B[:16]
    assert LayoutAwareChunker._make_id(PAPER_A, "abstract") != LayoutAwareChunker._make_id(PAPER_B, "abstract")


def test_no_chunk_id_shared_between_papers_with_a_common_prefix():
    chunker = LayoutAwareChunker(strategy="sentence", min_text_length=1)
    ids_a = {c.chunk_id for c in chunker.chunk(_doc(PAPER_A))}
    ids_b = {c.chunk_id for c in chunker.chunk(_doc(PAPER_B))}
    assert ids_a and ids_b
    assert ids_a.isdisjoint(ids_b)


def test_ids_are_stable_for_the_same_input():
    chunker = LayoutAwareChunker(strategy="sentence", min_text_length=1)
    first = [c.chunk_id for c in chunker.chunk(_doc(PAPER_A))]
    second = [c.chunk_id for c in chunker.chunk(_doc(PAPER_A))]
    assert first == second
