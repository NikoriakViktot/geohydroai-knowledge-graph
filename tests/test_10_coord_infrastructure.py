"""
test_10_coord_infrastructure.py — Tests for Phase 1-6 coordinate-aware infrastructure.

Covers:
  Phase 4 — ChromaDB bbox metadata (_document_chunk_meta, _bbox_from_meta, _hit_from_meta)
  Phase 5 — LayoutAwareChunker figure/table/formula chunks
  Phase 2 — entity_grounder.ground_entities
  Phase 6 — coord_validator (BoundingBox checks, IoU, ValidationReport)
"""

from __future__ import annotations

import sys
import unittest
from dataclasses import dataclass
from typing import Optional
from unittest.mock import MagicMock, patch


# ─────────────────────────────────────────────────────────────────────────────
# Phase 4 — ChromaDB helpers (no ChromaDB runtime needed)
# ─────────────────────────────────────────────────────────────────────────────

class TestBboxFromMeta(unittest.TestCase):
    def _fn(self, meta):
        from src.vectorstore.chroma_store import _bbox_from_meta
        return _bbox_from_meta(meta)

    def test_no_bbox_fields_returns_none(self):
        self.assertIsNone(self._fn({}))

    def test_sentinel_minus1_returns_none(self):
        self.assertIsNone(self._fn({"bbox_x": -1.0, "bbox_y": 0, "bbox_w": 10, "bbox_h": 20}))

    def test_valid_bbox_reconstructed(self):
        meta = {"bbox_x": 10.0, "bbox_y": 20.0, "bbox_w": 100.0, "bbox_h": 50.0}
        self.assertEqual(self._fn(meta), [10.0, 20.0, 100.0, 50.0])

    def test_none_bbox_x_returns_none(self):
        self.assertIsNone(self._fn({"bbox_x": None}))


class TestHitFromMeta(unittest.TestCase):
    def _fn(self, text, chunk_id, dist, meta):
        from src.vectorstore.chroma_store import _hit_from_meta
        return _hit_from_meta(text, chunk_id, dist, meta)

    def test_textchunk_meta_backward_compat(self):
        meta = {"filename": "paper.pdf", "page_start": 3, "page_end": 4}
        hit = self._fn("hello", "cid1", 0.1, meta)
        self.assertEqual(hit["filename"], "paper.pdf")
        self.assertEqual(hit["page_start"], 3)
        self.assertEqual(hit["page"], 3)
        self.assertIsNone(hit["bbox"])           # no bbox_x stored
        self.assertEqual(hit["chunk_type"], "")  # absent = default

    def test_documentchunk_meta_full(self):
        meta = {
            "filename": "pid1", "page_start": 2, "page_end": 2,
            "paper_id": "pid1", "chunk_type": "sentence",
            "section_title": "Methods", "section_level": 1, "section_n": "2",
            "page": 2,
            "bbox_x": 50.0, "bbox_y": 100.0, "bbox_w": 400.0, "bbox_h": 12.0,
            "near_figure": True, "figure_id": "fig1",
            "near_table": False, "table_id": "",
            "citation_count": 2,
        }
        hit = self._fn("text", "cid2", 0.05, meta)
        self.assertEqual(hit["chunk_type"], "sentence")
        self.assertEqual(hit["section_title"], "Methods")
        self.assertEqual(hit["bbox"], [50.0, 100.0, 400.0, 12.0])
        self.assertTrue(hit["near_figure"])
        self.assertEqual(hit["figure_id"], "fig1")
        self.assertIsNone(hit["table_id"])       # empty string → None
        self.assertEqual(hit["citation_count"], 2)

    def test_distance_preserved(self):
        hit = self._fn("t", "c", 0.777, {})
        self.assertAlmostEqual(hit["distance"], 0.777)


class TestDocumentChunkMeta(unittest.TestCase):
    def _make_chunk(self, bbox=None, near_fig=False, fig_id=None,
                    near_tab=False, tab_id=None, cites=(), page=5):
        from src.document.chunker import DocumentChunk
        return DocumentChunk(
            chunk_id="abc", paper_id="pid1", text="hello",
            section_title="Methods", section_level=1, section_n="2",
            page=page, bbox=bbox,
            near_figure=near_fig, figure_id=fig_id,
            near_table=near_tab, table_id=tab_id,
            citations=cites, chunk_type="sentence",
        )

    def _fn(self, chunk):
        from src.vectorstore.chroma_store import _document_chunk_meta
        return _document_chunk_meta(chunk)

    def test_backward_compat_fields_present(self):
        chunk = self._make_chunk()
        meta = self._fn(chunk)
        self.assertIn("filename", meta)
        self.assertIn("page_start", meta)
        self.assertIn("page_end", meta)
        self.assertEqual(meta["filename"], "pid1")
        self.assertEqual(meta["page_start"], 5)

    def test_bbox_none_stores_sentinel(self):
        chunk = self._make_chunk(bbox=None)
        meta = self._fn(chunk)
        self.assertEqual(meta["bbox_x"], -1.0)
        self.assertEqual(meta["bbox_y"], -1.0)

    def test_bbox_stored_correctly(self):
        chunk = self._make_chunk(bbox=(10.0, 20.0, 300.0, 15.0))
        meta = self._fn(chunk)
        self.assertEqual(meta["bbox_x"], 10.0)
        self.assertEqual(meta["bbox_y"], 20.0)
        self.assertEqual(meta["bbox_w"], 300.0)
        self.assertEqual(meta["bbox_h"], 15.0)

    def test_citation_count(self):
        chunk = self._make_chunk(cites=("b1", "b2", "b3"))
        meta = self._fn(chunk)
        self.assertEqual(meta["citation_count"], 3)

    def test_figure_id_empty_string_when_none(self):
        chunk = self._make_chunk(fig_id=None)
        meta = self._fn(chunk)
        self.assertEqual(meta["figure_id"], "")   # ChromaDB can't store None


# ─────────────────────────────────────────────────────────────────────────────
# Phase 5 — LayoutAwareChunker float chunks
# ─────────────────────────────────────────────────────────────────────────────

def _make_coords(page: int, x=10.0, y=20.0, w=100.0, h=12.0):
    from src.document.coordinates import BoundingBox, Coordinates
    return Coordinates(boxes=(BoundingBox(page=page, x=x, y=y, w=w, h=h),))


class TestFloatChunks(unittest.TestCase):
    """LayoutAwareChunker must produce figure, table, formula chunks."""

    def _make_doc(self, figures=None, tables=None, formulas=None):
        from src.document.models import TEIDocument
        doc = MagicMock(spec=TEIDocument)
        doc.paper_id = "papX"
        doc.abstract = ""
        doc.sections = []
        doc.figures = figures or []
        doc.tables = tables or []
        doc.formulas = formulas or []
        return doc

    def _chunker(self):
        from src.document.chunker import LayoutAwareChunker
        return LayoutAwareChunker(strategy="sentence", min_text_length=1)

    def test_figure_chunk_produced(self):
        from src.document.models import Figure
        fig = MagicMock(spec=Figure)
        fig.xml_id = "fig1"
        fig.label = "Figure 1"
        fig.caption = "Rainfall–runoff relationship for the study catchment."
        fig.coords = _make_coords(3)
        fig.graphic_coords = None

        doc = self._make_doc(figures=[fig])
        chunks = self._chunker().chunk(doc)
        fig_chunks = [c for c in chunks if c.chunk_type == "figure"]
        self.assertEqual(len(fig_chunks), 1)
        c = fig_chunks[0]
        self.assertEqual(c.paper_id, "papX")
        self.assertEqual(c.figure_id, "fig1")
        self.assertTrue(c.near_figure)
        self.assertEqual(c.page, 3)
        self.assertIsNotNone(c.bbox)
        self.assertIn("Rainfall", c.text)

    def test_figure_chunk_uses_label_when_caption_empty(self):
        from src.document.models import Figure
        fig = MagicMock(spec=Figure)
        fig.xml_id = "fig2"
        fig.label = "Fig. 2"
        fig.caption = ""
        fig.coords = _make_coords(1)
        fig.graphic_coords = None

        doc = self._make_doc(figures=[fig])
        chunks = self._chunker().chunk(doc)
        fig_chunks = [c for c in chunks if c.chunk_type == "figure"]
        self.assertEqual(len(fig_chunks), 1)
        self.assertIn("Fig", fig_chunks[0].text)

    def test_figure_no_text_skipped(self):
        from src.document.models import Figure
        fig = MagicMock(spec=Figure)
        fig.xml_id = "fig3"
        fig.label = ""
        fig.caption = ""
        fig.coords = _make_coords(1)
        fig.graphic_coords = None

        doc = self._make_doc(figures=[fig])
        chunks = self._chunker().chunk(doc)
        self.assertEqual(len([c for c in chunks if c.chunk_type == "figure"]), 0)

    def test_table_chunk_produced(self):
        from src.document.models import Table
        tab = MagicMock(spec=Table)
        tab.xml_id = "tab1"
        tab.label = "Table 1"
        tab.caption = "Monthly discharge statistics for Dnieper River."
        tab.coords = _make_coords(4)

        doc = self._make_doc(tables=[tab])
        chunks = self._chunker().chunk(doc)
        tab_chunks = [c for c in chunks if c.chunk_type == "table"]
        self.assertEqual(len(tab_chunks), 1)
        c = tab_chunks[0]
        self.assertEqual(c.table_id, "tab1")
        self.assertTrue(c.near_table)
        self.assertEqual(c.page, 4)

    def test_formula_chunk_produced(self):
        from src.document.models import Formula
        form = MagicMock(spec=Formula)
        form.xml_id = "formula1"
        form.text = "Q = \\frac{A \\cdot v}{t}"
        form.coords = _make_coords(2)

        doc = self._make_doc(formulas=[form])
        chunks = self._chunker().chunk(doc)
        form_chunks = [c for c in chunks if c.chunk_type == "formula"]
        self.assertEqual(len(form_chunks), 1)
        c = form_chunks[0]
        self.assertEqual(c.section_n, "formula1")
        self.assertIn("frac", c.text)
        self.assertEqual(c.page, 2)

    def test_formula_no_coords_page_zero(self):
        from src.document.models import Formula
        form = MagicMock(spec=Formula)
        form.xml_id = "formula2"
        form.text = "y = mx + b"
        form.coords = None

        doc = self._make_doc(formulas=[form])
        chunks = self._chunker().chunk(doc)
        form_chunks = [c for c in chunks if c.chunk_type == "formula"]
        self.assertEqual(len(form_chunks), 1)
        self.assertEqual(form_chunks[0].page, 0)
        self.assertIsNone(form_chunks[0].bbox)


# ─────────────────────────────────────────────────────────────────────────────
# Phase 2 — entity_grounder
# ─────────────────────────────────────────────────────────────────────────────

def _make_sentence(text: str, page: int = 1, x=10.0, y=20.0):
    """Create a minimal Sentence-like mock with coordinates."""
    from src.document.coordinates import BoundingBox, Coordinates
    sent = MagicMock()
    sent.text = text
    sent.coords = Coordinates(boxes=(BoundingBox(page=page, x=x, y=y, w=100.0, h=12.0),))
    sent.citations = []
    return sent


def _make_doc_with_sentences(sentences_by_section: dict) -> MagicMock:
    """sentences_by_section = {"Methods": [("sentence text", page), ...]}"""
    from src.document.models import TEIDocument

    sections = []
    for title, sent_specs in sentences_by_section.items():
        sec = MagicMock()
        sec.title = title
        sec.n = ""
        sec.level = 1
        sec.coords = None
        sec.subsections = []
        para = MagicMock()
        para.text = " ".join(s for s, _ in sent_specs)
        para.coords = None
        para.sentences = [_make_sentence(s, p) for s, p in sent_specs]
        para.all_citations = []
        sec.paragraphs = [para]
        sections.append(sec)

    doc = MagicMock(spec=TEIDocument)
    doc.paper_id = "pid_test"
    doc.abstract = ""
    doc.sections = sections
    doc.figures = []
    doc.tables = []
    doc.formulas = []
    return doc


class TestEntityGrounder(unittest.TestCase):
    def test_exact_match_attaches_provenance(self):
        from src.document.entity_grounder import ground_entities

        doc = _make_doc_with_sentences({
            "Methods": [
                ("We used the HEC-HMS model to simulate runoff.", 3),
                ("Results were validated using Nash-Sutcliffe efficiency.", 4),
            ]
        })

        paper = {
            "entities": {
                "methods": [{"name": "HEC-HMS"}],
            }
        }
        result = ground_entities(paper, doc)
        ent = result["entities"]["methods"][0]
        self.assertIn("provenance", ent)
        self.assertEqual(ent["provenance"]["page"], 3)
        self.assertEqual(ent["provenance"]["match"], "exact")
        self.assertEqual(ent["provenance"]["section"], "Methods")
        self.assertIsNotNone(ent["provenance"]["bbox"])

    def test_partial_match_multi_token(self):
        from src.document.entity_grounder import ground_entities

        doc = _make_doc_with_sentences({
            "Results": [
                ("MODIS satellite imagery was acquired for 2019.", 5),
            ]
        })
        paper = {
            "entities": {
                "satellites": [{"name": "MODIS satellite"}],
            }
        }
        result = ground_entities(paper, doc)
        ent = result["entities"]["satellites"][0]
        self.assertIn("provenance", ent)
        self.assertIn(ent["provenance"]["match"], ("exact", "partial"))

    def test_no_match_leaves_entity_unchanged(self):
        from src.document.entity_grounder import ground_entities

        doc = _make_doc_with_sentences({
            "Abstract": [("This paper describes hydrology.", 1)],
        })
        paper = {
            "entities": {
                "methods": [{"name": "SWMM"}],  # not in doc
            }
        }
        result = ground_entities(paper, doc)
        ent = result["entities"]["methods"][0]
        self.assertNotIn("provenance", ent)

    def test_empty_name_skipped(self):
        from src.document.entity_grounder import ground_entities

        doc = _make_doc_with_sentences({"Intro": [("some text here", 1)]})
        paper = {"entities": {"methods": [{"name": ""}]}}
        # should not crash
        result = ground_entities(paper, doc)
        self.assertNotIn("provenance", result["entities"]["methods"][0])

    def test_already_has_provenance_not_overwritten(self):
        from src.document.entity_grounder import ground_entities

        doc = _make_doc_with_sentences({
            "Methods": [("SCS-CN method was applied.", 2)],
        })
        existing_prov = {"page": 99, "match": "manual"}
        paper = {
            "entities": {
                "methods": [{"name": "SCS-CN", "provenance": existing_prov}],
            }
        }
        result = ground_entities(paper, doc)
        self.assertEqual(result["entities"]["methods"][0]["provenance"]["page"], 99)

    def test_non_entity_structure_handled_gracefully(self):
        from src.document.entity_grounder import ground_entities

        doc = _make_doc_with_sentences({"Methods": [("text", 1)]})
        # entities key is absent
        paper = {"metadata": {"title": "Test"}}
        result = ground_entities(paper, doc)
        self.assertNotIn("entities", result)   # structure unchanged

    def test_multiple_entity_types_grounded(self):
        from src.document.entity_grounder import ground_entities

        doc = _make_doc_with_sentences({
            "Data": [
                ("We used Landsat-8 imagery.", 2),
                ("Nash-Sutcliffe efficiency (NSE) was computed.", 2),
            ]
        })
        paper = {
            "entities": {
                "satellites": [{"name": "Landsat-8"}],
                "metrics":    [{"name": "NSE"}],
            }
        }
        result = ground_entities(paper, doc)
        self.assertIn("provenance", result["entities"]["satellites"][0])
        self.assertIn("provenance", result["entities"]["metrics"][0])


# ─────────────────────────────────────────────────────────────────────────────
# Phase 6 — coord_validator
# ─────────────────────────────────────────────────────────────────────────────

class TestIoU(unittest.TestCase):
    def _iou(self, a, b):
        from src.document.coord_validator import _iou
        return _iou(a, b)

    def test_no_overlap(self):
        self.assertEqual(self._iou((0, 0, 10, 10), (20, 20, 10, 10)), 0.0)

    def test_perfect_overlap(self):
        self.assertAlmostEqual(self._iou((0, 0, 10, 10), (0, 0, 10, 10)), 1.0)

    def test_partial_overlap(self):
        iou = self._iou((0, 0, 10, 10), (5, 5, 10, 10))
        self.assertGreater(iou, 0.0)
        self.assertLess(iou, 1.0)

    def test_touching_edge_no_overlap(self):
        self.assertEqual(self._iou((0, 0, 10, 10), (10, 0, 10, 10)), 0.0)


class TestValidationReport(unittest.TestCase):
    def test_empty_report_ok(self):
        from src.document.coord_validator import ValidationReport
        r = ValidationReport(paper_id="p1")
        self.assertEqual(r.error_count, 0)
        self.assertEqual(r.summary(), "OK")

    def test_summary_counts_by_check(self):
        from src.document.coord_validator import ValidationReport, CoordIssue
        r = ValidationReport(paper_id="p1", issues=[
            CoordIssue("NEGATIVE_PAGE", "sent", "page=-1"),
            CoordIssue("NEGATIVE_PAGE", "sent2", "page=0"),
            CoordIssue("ZERO_DIMENSION", "sent3", "w=0"),
        ])
        s = r.summary()
        self.assertIn("NEGATIVE_PAGE×2", s)
        self.assertIn("ZERO_DIMENSION×1", s)

    def test_to_dict(self):
        from src.document.coord_validator import ValidationReport, CoordIssue
        r = ValidationReport(paper_id="p2", issues=[
            CoordIssue("BBOX_OVERLAP", "page 3", "IoU=0.75 between A and B"),
        ])
        d = r.to_dict()
        self.assertEqual(d["paper_id"], "p2")
        self.assertEqual(d["error_count"], 1)
        self.assertEqual(d["issues"][0]["check"], "BBOX_OVERLAP")


class TestCheckCoords(unittest.TestCase):
    def _check(self, page, x=10, y=10, w=100, h=50):
        from src.document.coord_validator import ValidationReport, _check_coords
        from src.document.coordinates import BoundingBox, Coordinates
        coords = Coordinates(boxes=(BoundingBox(page=page, x=x, y=y, w=w, h=h),))
        report = ValidationReport(paper_id="p")
        _check_coords(coords, "test", report)
        return report

    def test_valid_box_no_issues(self):
        r = self._check(page=1)
        self.assertEqual(r.error_count, 0)

    def test_negative_page_flagged(self):
        r = self._check(page=0)
        checks = [i.check for i in r.issues]
        self.assertIn("NEGATIVE_PAGE", checks)

    def test_zero_width_flagged(self):
        r = self._check(page=1, w=0)
        checks = [i.check for i in r.issues]
        self.assertIn("ZERO_DIMENSION", checks)

    def test_zero_height_flagged(self):
        r = self._check(page=1, h=-5)
        checks = [i.check for i in r.issues]
        self.assertIn("ZERO_DIMENSION", checks)

    def test_negative_x_flagged(self):
        r = self._check(page=1, x=-1)
        checks = [i.check for i in r.issues]
        self.assertIn("ZERO_POSITION", checks)

    def test_none_coords_no_crash(self):
        from src.document.coord_validator import ValidationReport, _check_coords
        r = ValidationReport(paper_id="p")
        _check_coords(None, "test", r)
        self.assertEqual(r.error_count, 0)

    def test_multi_page_span_flagged(self):
        from src.document.coord_validator import ValidationReport, _check_coords
        from src.document.coordinates import BoundingBox, Coordinates
        coords = Coordinates(boxes=(
            BoundingBox(page=1, x=10, y=10, w=100, h=50),
            BoundingBox(page=2, x=10, y=10, w=100, h=50),
        ))
        r = ValidationReport(paper_id="p")
        _check_coords(coords, "test", r)
        checks = [i.check for i in r.issues]
        self.assertIn("MULTI_PAGE_SPAN", checks)


if __name__ == "__main__":
    unittest.main()
