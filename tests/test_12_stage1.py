"""
test_12_stage1.py — Unit tests for Stage 1 structural parsing layer.

Coverage:
  - ParsedStore: paths, is_complete, write/read round-trips for all artifacts,
                 pipeline_hash injection, atomic write, idempotency
  - Stage1Parser: error propagation from Stage0, already-complete skip,
                  missing PDF, parse failure, successful mock parse
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pyarrow.parquet as pq
import pytest

from src.ingestion.stage0.ingestor import Stage0Result
from src.ingestion.stage1.parsed_store import ParsedStore
from src.ingestion.stage1.parser_runner import Stage1Parser, Stage1Result
from src.ingestion.models import TriageResult


# ── Helpers ───────────────────────────────────────────────────────────────────

PIPELINE_HASH = "test-hash-abc123"
PAPER_ID      = "deadbeef" * 8   # 64-char hex


def _store(tmp_path: Path) -> ParsedStore:
    return ParsedStore(PAPER_ID, PIPELINE_HASH, tmp_path / "parsed")


def _ok_stage0(raw_root: Path) -> Stage0Result:
    paper_dir = raw_root / PAPER_ID
    paper_dir.mkdir(parents=True, exist_ok=True)
    (paper_dir / "paper.pdf").write_bytes(b"fake-pdf")
    return Stage0Result(
        paper_id=PAPER_ID, status="ok", checksum=PAPER_ID,
        raw_root=raw_root, triage=None,
    )


def _error_stage0(raw_root: Path) -> Stage0Result:
    return Stage0Result(
        paper_id=PAPER_ID, status="error", checksum="",
        raw_root=raw_root, triage=None,
    )


def _minimal_sections():
    return [
        {
            "section_id":    f"{PAPER_ID}_sec_000",
            "paper_id":      PAPER_ID,
            "title":         "Introduction",
            "level":         1,
            "n":             "1",
            "text":          "This paper investigates floods.",
            "word_count":    4,
            "page":          1,
            "bbox_x0":       None,
            "bbox_y0":       None,
            "bbox_x1":       None,
            "bbox_y1":       None,
            "source_parser": "GROBID",
        },
    ]


def _minimal_figures():
    return [
        {
            "figure_id":     f"{PAPER_ID}_fig_000",
            "paper_id":      PAPER_ID,
            "xml_id":        "fig_0",
            "label":         "Figure 1",
            "caption":       "Observed hydrograph for 2020.",
            "page":          2,
            "bbox_x0":       10.0, "bbox_y0": 20.0, "bbox_x1": 100.0, "bbox_y1": 200.0,
            "graphic_page":  None,
            "graphic_x0":    None, "graphic_y0": None, "graphic_x1": None, "graphic_y1": None,
            "source_parser": "GROBID",
        },
    ]


def _minimal_tables():
    return [
        {
            "table_id":        f"{PAPER_ID}_tbl_000",
            "paper_id":        PAPER_ID,
            "region_id":       None,
            "xml_id":          "tab_0",
            "page":            3,
            "label":           "Table 1",
            "caption":         "Model NSE performance.",
            "header_row":      ["Model", "NSE", "KGE"],
            "row_count":       3,
            "col_count":       3,
            "has_numeric_data": True,
            "source_parser":   "GROBID",
        },
    ]


def _minimal_equations():
    return [
        {
            "equation_id":   f"{PAPER_ID}_eq_000",
            "paper_id":      PAPER_ID,
            "xml_id":        "eq_0",
            "text":          "NSE = 1 - sum(Qo-Qs)^2 / sum(Qo-Qm)^2",
            "page":          2,
            "bbox_x0":       None, "bbox_y0": None, "bbox_x1": None, "bbox_y1": None,
            "source_parser": "GROBID",
        },
    ]


def _minimal_refs():
    return [
        {
            "ref_id":        f"{PAPER_ID}_ref_b0",
            "paper_id":      PAPER_ID,
            "xml_id":        "b0",
            "title":         "Flood mapping with SAR",
            "authors":       "Smith J.; Doe A.",
            "journal":       "Remote Sensing",
            "year":          2020,
            "doi":           "10.1234/rs.2020",
            "raw":           "Smith J. et al., Remote Sensing, 2020.",
            "source_parser": "GROBID",
        },
    ]


def _minimal_captions():
    return [
        {
            "caption_id":    f"{PAPER_ID}_cap_000",
            "paper_id":      PAPER_ID,
            "parent_id":     f"{PAPER_ID}_fig_000",
            "parent_type":   "figure",
            "label":         "Figure 1",
            "text":          "Observed hydrograph for 2020.",
            "page":          2,
            "source_parser": "GROBID",
        },
    ]


# ─────────────────────────────────────────────────────────────────────────────
# ParsedStore — paths
# ─────────────────────────────────────────────────────────────────────────────

class TestParsedStorePaths:
    def test_root(self, tmp_path):
        s = _store(tmp_path)
        assert s.root == tmp_path / "parsed" / PAPER_ID

    def test_tei_path(self, tmp_path):
        assert _store(tmp_path).tei_path.name == "tei.xml"

    def test_sections_path(self, tmp_path):
        assert _store(tmp_path).sections_path.name == "sections.parquet"

    def test_figures_path(self, tmp_path):
        assert _store(tmp_path).figures_path.name == "figures.parquet"

    def test_tables_path(self, tmp_path):
        assert _store(tmp_path).tables_path.name == "tables.parquet"

    def test_equations_path(self, tmp_path):
        assert _store(tmp_path).equations_path.name == "equations.parquet"

    def test_refs_path(self, tmp_path):
        assert _store(tmp_path).refs_path.name == "references.parquet"

    def test_captions_path(self, tmp_path):
        assert _store(tmp_path).captions_path.name == "captions.parquet"

    def test_manifest_path(self, tmp_path):
        assert _store(tmp_path).manifest_path.name == "parsing_manifest.json"

    def test_root_directory_created(self, tmp_path):
        s = _store(tmp_path)
        assert s.root.is_dir()


# ─────────────────────────────────────────────────────────────────────────────
# ParsedStore — is_complete
# ─────────────────────────────────────────────────────────────────────────────

class TestParsedStoreIsComplete:
    def test_false_when_empty(self, tmp_path):
        assert not _store(tmp_path).is_complete

    def test_false_when_some_files_missing(self, tmp_path):
        s = _store(tmp_path)
        s.write_tei("<TEI/>")
        assert not s.is_complete

    def test_true_when_all_mandatory_present(self, tmp_path):
        s = _store(tmp_path)
        s.write_tei("<TEI/>")
        s.write_sections(_minimal_sections())
        s.write_figures(_minimal_figures())
        s.write_tables(_minimal_tables())
        s.write_equations(_minimal_equations())
        s.write_references(_minimal_refs())
        s.write_captions(_minimal_captions())
        s.write_manifest({})
        assert s.is_complete


# ─────────────────────────────────────────────────────────────────────────────
# ParsedStore — write / read round-trips
# ─────────────────────────────────────────────────────────────────────────────

class TestParsedStoreTei:
    def test_write_read_roundtrip(self, tmp_path):
        s = _store(tmp_path)
        s.write_tei("<TEI>hello</TEI>")
        assert s.read_tei() == "<TEI>hello</TEI>"

    def test_read_returns_none_when_missing(self, tmp_path):
        assert _store(tmp_path).read_tei() is None

    def test_atomic_no_tmp_remains(self, tmp_path):
        s = _store(tmp_path)
        s.write_tei("<TEI/>")
        assert not s.tei_path.with_suffix(".xml.tmp").exists()


class TestParsedStoreManifest:
    def test_write_read_roundtrip(self, tmp_path):
        s = _store(tmp_path)
        s.write_manifest({"strategy": "grobid_only", "section_count": 5})
        m = s.read_manifest()
        assert m["strategy"] == "grobid_only"
        assert m["section_count"] == 5

    def test_injects_paper_id(self, tmp_path):
        s = _store(tmp_path)
        s.write_manifest({})
        assert s.read_manifest()["paper_id"] == PAPER_ID

    def test_injects_pipeline_hash(self, tmp_path):
        s = _store(tmp_path)
        s.write_manifest({})
        assert s.read_manifest()["pipeline_hash"] == PIPELINE_HASH

    def test_injects_completed_at(self, tmp_path):
        s = _store(tmp_path)
        s.write_manifest({})
        assert "completed_at" in s.read_manifest()


class TestParsedStoreParquetWrites:
    def test_sections_written_and_readable(self, tmp_path):
        s = _store(tmp_path)
        s.write_sections(_minimal_sections())
        rows = s.read_parquet("sections.parquet")
        assert len(rows) == 1
        assert rows[0]["title"] == "Introduction"

    def test_figures_written_and_readable(self, tmp_path):
        s = _store(tmp_path)
        s.write_figures(_minimal_figures())
        rows = s.read_parquet("figures.parquet")
        assert rows[0]["label"] == "Figure 1"

    def test_tables_written_and_readable(self, tmp_path):
        s = _store(tmp_path)
        s.write_tables(_minimal_tables())
        rows = s.read_parquet("tables.parquet")
        assert rows[0]["caption"] == "Model NSE performance."

    def test_equations_written_and_readable(self, tmp_path):
        s = _store(tmp_path)
        s.write_equations(_minimal_equations())
        rows = s.read_parquet("equations.parquet")
        assert "NSE" in rows[0]["text"]

    def test_references_written_and_readable(self, tmp_path):
        s = _store(tmp_path)
        s.write_references(_minimal_refs())
        rows = s.read_parquet("references.parquet")
        assert rows[0]["doi"] == "10.1234/rs.2020"

    def test_captions_written_and_readable(self, tmp_path):
        s = _store(tmp_path)
        s.write_captions(_minimal_captions())
        rows = s.read_parquet("captions.parquet")
        assert rows[0]["parent_type"] == "figure"

    def test_pipeline_hash_injected_into_parquet_rows(self, tmp_path):
        s = _store(tmp_path)
        s.write_sections(_minimal_sections())
        rows = s.read_parquet("sections.parquet")
        assert rows[0]["pipeline_hash"] == PIPELINE_HASH

    def test_created_at_injected_into_parquet_rows(self, tmp_path):
        s = _store(tmp_path)
        s.write_sections(_minimal_sections())
        rows = s.read_parquet("sections.parquet")
        assert rows[0]["created_at"] is not None

    def test_read_parquet_returns_empty_when_file_missing(self, tmp_path):
        assert _store(tmp_path).read_parquet("sections.parquet") == []

    def test_no_tmp_file_remains_after_parquet_write(self, tmp_path):
        s = _store(tmp_path)
        s.write_sections(_minimal_sections())
        assert not s.sections_path.with_suffix(".parquet.tmp").exists()

    def test_write_empty_list_produces_valid_parquet(self, tmp_path):
        s = _store(tmp_path)
        path = s.write_sections([])
        assert path.exists()
        assert pq.read_table(path).num_rows == 0


# ─────────────────────────────────────────────────────────────────────────────
# Stage1Parser
# ─────────────────────────────────────────────────────────────────────────────

def _build_mock_doc():
    """Return a minimal TEIDocument-like mock for Stage1Parser tests."""
    from src.document.models import TEIDocument
    from src.document.provenance import ParserKind

    doc = MagicMock(spec=TEIDocument)
    doc.paper_id      = PAPER_ID
    doc.parser_kind   = ParserKind.GROBID   # real enum; .value == "GROBID"
    doc.title         = "Test Paper"
    doc.sections      = []
    doc.figures       = []
    doc.tables        = []
    doc.formulas      = []
    doc.references    = []
    doc.source_paths  = []
    return doc


class TestStage1ParserErrors:
    def test_run_error_stage0_returns_error(self, tmp_path):
        s0 = Stage0Result(
            paper_id=PAPER_ID, status="error", checksum="",
            raw_root=tmp_path, triage=None,
        )
        parser = Stage1Parser(parsed_root=tmp_path / "parsed", pipeline_hash=PIPELINE_HASH)
        result = parser.run(s0)
        assert result.status == "error"

    def test_run_missing_pdf_returns_error(self, tmp_path):
        raw_root = tmp_path / "raw"
        raw_root.mkdir()
        # Stage0 says ok but paper.pdf doesn't exist on disk
        s0 = Stage0Result(
            paper_id=PAPER_ID, status="ok", checksum=PAPER_ID,
            raw_root=raw_root, triage=None,
        )
        parser = Stage1Parser(parsed_root=tmp_path / "parsed", pipeline_hash=PIPELINE_HASH)
        result = parser.run(s0)
        assert result.status == "error"
        assert any("pdf_not_found" in e for e in result.events)

    def test_run_parse_exception_returns_error(self, tmp_path):
        raw_root = tmp_path / "raw"
        s0 = _ok_stage0(raw_root)
        parser = Stage1Parser(parsed_root=tmp_path / "parsed", pipeline_hash=PIPELINE_HASH)

        # ParserRouter is a lazy import inside run(); patch it in the source module
        with patch("src.document.parser_router.ParserRouter") as MockRouter:
            MockRouter.return_value.parse_pdf.side_effect = RuntimeError("GROBID down")
            result = parser.run(s0)

        assert result.status == "error"
        assert any("parse_error" in e for e in result.events)

    def test_run_skipped_when_already_complete(self, tmp_path):
        raw_root = tmp_path / "raw"
        s0 = _ok_stage0(raw_root)
        parsed_root = tmp_path / "parsed"
        parser = Stage1Parser(parsed_root=parsed_root, pipeline_hash=PIPELINE_HASH)

        # Pre-populate the parsed store so is_complete = True
        store = ParsedStore(PAPER_ID, PIPELINE_HASH, parsed_root)
        store.write_tei("<TEI/>")
        store.write_sections(_minimal_sections())
        store.write_figures(_minimal_figures())
        store.write_tables(_minimal_tables())
        store.write_equations(_minimal_equations())
        store.write_references(_minimal_refs())
        store.write_captions(_minimal_captions())
        store.write_manifest({})

        result = parser.run(s0)
        assert result.status == "skipped"


class TestStage1ParserSuccess:
    def test_run_ok_returns_ok_status(self, tmp_path):
        raw_root = tmp_path / "raw"
        s0 = _ok_stage0(raw_root)
        parser = Stage1Parser(
            parsed_root=tmp_path / "parsed",
            pipeline_hash=PIPELINE_HASH,
        )

        mock_doc = _build_mock_doc()
        with patch("src.document.parser_router.ParserRouter") as MockRouter:
            MockRouter.return_value.parse_pdf.return_value = mock_doc
            result = parser.run(s0)

        assert result.status == "ok"
        assert result.should_continue is True

    def test_run_ok_writes_manifest(self, tmp_path):
        raw_root = tmp_path / "raw"
        s0 = _ok_stage0(raw_root)
        parsed_root = tmp_path / "parsed"
        parser = Stage1Parser(parsed_root=parsed_root, pipeline_hash=PIPELINE_HASH)

        mock_doc = _build_mock_doc()
        with patch("src.document.parser_router.ParserRouter") as MockRouter:
            MockRouter.return_value.parse_pdf.return_value = mock_doc
            parser.run(s0)

        store = ParsedStore(PAPER_ID, PIPELINE_HASH, parsed_root)
        manifest = store.read_manifest()
        assert manifest["paper_id"] == PAPER_ID
        assert manifest["pipeline_hash"] == PIPELINE_HASH

    def test_run_ok_counts_match_doc(self, tmp_path):
        raw_root = tmp_path / "raw"
        s0 = _ok_stage0(raw_root)
        parser = Stage1Parser(parsed_root=tmp_path / "parsed", pipeline_hash=PIPELINE_HASH)

        mock_doc = _build_mock_doc()
        with patch("src.document.parser_router.ParserRouter") as MockRouter:
            MockRouter.return_value.parse_pdf.return_value = mock_doc
            result = parser.run(s0)

        assert result.section_count == 0
        assert result.figure_count  == 0
        assert result.table_count   == 0
        assert result.equation_count == 0
        assert result.ref_count     == 0

    def test_force_reruns_even_when_complete(self, tmp_path):
        raw_root = tmp_path / "raw"
        s0 = _ok_stage0(raw_root)
        parsed_root = tmp_path / "parsed"

        store = ParsedStore(PAPER_ID, PIPELINE_HASH, parsed_root)
        store.write_tei("<TEI/>")
        store.write_sections(_minimal_sections())
        store.write_figures(_minimal_figures())
        store.write_tables(_minimal_tables())
        store.write_equations(_minimal_equations())
        store.write_references(_minimal_refs())
        store.write_captions(_minimal_captions())
        store.write_manifest({})

        parser = Stage1Parser(parsed_root=parsed_root, pipeline_hash=PIPELINE_HASH, force=True)
        mock_doc = _build_mock_doc()
        with patch("src.document.parser_router.ParserRouter") as MockRouter:
            MockRouter.return_value.parse_pdf.return_value = mock_doc
            result = parser.run(s0)

        assert result.status == "ok"


class TestStage1Result:
    def test_should_continue_true_only_for_ok(self):
        for status in ("skipped", "error"):
            r = Stage1Result(paper_id="x", status=status, parsed_root=Path("/tmp"))
            assert r.should_continue is False

    def test_should_continue_true_for_ok(self):
        r = Stage1Result(paper_id="x", status="ok", parsed_root=Path("/tmp"))
        assert r.should_continue is True
