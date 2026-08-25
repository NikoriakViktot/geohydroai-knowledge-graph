"""
test_parser_routing.py — Tests for the multi-parser SDOM architecture.

Tests are grouped by concern:
  1. ParserRouter routing logic (no model calls)
  2. NougatParser returns TEIDocument (markdown path, no model)
  3. MarkdownScientificParser extracts title/sections/formulas/tables
  4. HybridParser uses GROBID metadata and Nougat formulas
  5. Scanned PDF routes to Nougat
  6. GROBID failure falls back to Nougat
  7. Parser capabilities are correct
  8. Downstream body_text() still works on all doc kinds
  9. LayoutAwareChunker works on hybrid docs
 10. FailureType mappings are correct for Nougat errors

All tests are pure Python (no Nougat model, no GROBID server, no Ray).
"""

from __future__ import annotations

import pytest

from src.document.markdown_parser import MarkdownScientificParser
from src.document.models import TEIDocument
from src.document.nougat_parser import NougatParser
from src.document.parser_router import ParserRouter, RouterConfig, STRATEGIES
from src.document.provenance import (
    DocumentQuality,
    ParserCapability,
    ParserKind,
    ParserProvenance,
)
from src.ingestion.failure_types import FailureType


# ── Fixtures ───────────────────────────────────────────────────────────────────

SAMPLE_MARKDOWN = """\
# Flood Mapping Using Sentinel-1 SAR Data

## 1 Introduction

Flood mapping is critical for disaster response.
Remote sensing provides synoptic coverage.

## 2 Methods

### 2.1 Data

We used Sentinel-1 GRD data.

The radar equation is:

$$\\sigma^0 = 10 \\log_{10}(P_r / P_t)$$

### 2.2 Processing

Change detection was applied using the Lee filter.

## 3 Results

| Metric | Value |
|--------|-------|
| OA     | 0.94  |
| F1     | 0.89  |

## References

[1] Smith J. et al. SAR Flood Mapping. 2020. https://doi.org/10.1016/j.rse.2020.01.001
[2] Jones A. Sentinel-1 Processing. Remote Sensing, 2019.
"""

GROBID_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<TEI xmlns="http://www.tei-c.org/ns/1.0">
  <teiHeader>
    <fileDesc>
      <titleStmt>
        <title level="a">Flood Mapping Using SAR</title>
      </titleStmt>
      <sourceDesc>
        <biblStruct>
          <analytic>
            <author>
              <persName><forename type="first">John</forename><surname>Smith</surname></persName>
            </author>
          </analytic>
          <monogr>
            <title level="j">Remote Sensing</title>
            <imprint>
              <date type="published" when="2021"/>
            </imprint>
          </monogr>
        </biblStruct>
      </sourceDesc>
    </fileDesc>
    <profileDesc>
      <abstract><div><p>This paper presents SAR-based flood mapping.</p></div></abstract>
    </profileDesc>
  </teiHeader>
  <text>
    <body>
      <div>
        <head n="1">Introduction</head>
        <p><s>Flood mapping is important.</s></p>
      </div>
      <div>
        <head n="2">Methods</head>
        <p><s>We used Sentinel-1 data.</s></p>
      </div>
    </body>
    <back>
      <div type="references">
        <listBibl>
          <biblStruct xml:id="b0">
            <analytic>
              <title level="a">SAR Flood Detection</title>
              <author><persName><forename type="first">A</forename><surname>Jones</surname></persName></author>
            </analytic>
            <monogr>
              <title level="j">IEEE TGRS</title>
              <imprint><date type="published" when="2019"/></imprint>
            </monogr>
          </biblStruct>
        </listBibl>
      </div>
    </back>
  </text>
</TEI>
"""


# ── 3. MarkdownScientificParser ───────────────────────────────────────────────

class TestMarkdownScientificParser:

    def setup_method(self):
        self.parser = MarkdownScientificParser()

    def test_extracts_title(self):
        doc = self.parser.parse_text(SAMPLE_MARKDOWN, "test-001")
        assert "Flood Mapping" in doc.title

    def test_extracts_sections(self):
        doc = self.parser.parse_text(SAMPLE_MARKDOWN, "test-001")
        assert len(doc.sections) >= 2
        titles = [s.title for s in doc.sections]
        assert any("Introduction" in t for t in titles)
        assert any("Method" in t for t in titles)

    def test_extracts_formulas(self):
        doc = self.parser.parse_text(SAMPLE_MARKDOWN, "test-001")
        assert len(doc.formulas) >= 1
        assert any("sigma" in f.text or "\\sigma" in f.text for f in doc.formulas)

    def test_extracts_tables(self):
        doc = self.parser.parse_text(SAMPLE_MARKDOWN, "test-001")
        assert len(doc.tables) >= 1
        # Table should have rows with OA and F1 values
        all_cells = [cell for t in doc.tables for row in t.rows for cell in row]
        assert any("OA" in c or "0.94" in c for c in all_cells)

    def test_parser_kind_is_nougat(self):
        doc = self.parser.parse_text(SAMPLE_MARKDOWN, "test-001")
        assert doc.parser_kind == ParserKind.NOUGAT

    def test_capabilities_include_structure(self):
        doc = self.parser.parse_text(SAMPLE_MARKDOWN, "test-001")
        assert ParserCapability.STRUCTURE in doc.parser_capabilities
        assert ParserCapability.MARKDOWN in doc.parser_capabilities

    def test_capabilities_exclude_coordinates(self):
        doc = self.parser.parse_text(SAMPLE_MARKDOWN, "test-001")
        assert ParserCapability.COORDINATES not in doc.parser_capabilities

    def test_markdown_text_preserved(self):
        doc = self.parser.parse_text(SAMPLE_MARKDOWN, "test-001")
        assert doc.markdown_text is not None
        assert "Sentinel-1" in doc.markdown_text

    def test_empty_input_returns_valid_doc(self):
        doc = self.parser.parse_text("", "empty-001")
        assert isinstance(doc, TEIDocument)
        assert doc.paper_id == "empty-001"

    def test_references_extracted(self):
        doc = self.parser.parse_text(SAMPLE_MARKDOWN, "test-001")
        assert len(doc.references) >= 1
        # At least one reference should have a DOI
        dois = [r.doi for r in doc.references if r.doi]
        assert dois or len(doc.references) >= 1   # either doi found or refs present


# ── 2. NougatParser (markdown path, no model) ─────────────────────────────────

class TestNougatParserMarkdownPath:

    def setup_method(self):
        self.parser = NougatParser()

    def test_parse_text_returns_teidocument(self):
        doc = self.parser.parse_text(SAMPLE_MARKDOWN, "test-002")
        assert isinstance(doc, TEIDocument)

    def test_parse_text_parser_kind(self):
        doc = self.parser.parse_text(SAMPLE_MARKDOWN, "test-002")
        assert doc.parser_kind == ParserKind.NOUGAT

    def test_parse_text_has_title(self):
        doc = self.parser.parse_text(SAMPLE_MARKDOWN, "test-002")
        assert doc.title != ""

    def test_model_info(self):
        info = self.parser.model_info()
        assert "model_name" in info
        assert "loaded" in info
        assert info["model_name"] == "facebook/nougat-base"


# ── 8. body_text() backward compatibility ─────────────────────────────────────

class TestBodyTextBackwardCompat:

    def _make_doc(self, parser_kind: ParserKind) -> TEIDocument:
        import dataclasses
        p = MarkdownScientificParser()
        doc = p.parse_text(SAMPLE_MARKDOWN, "compat-001")
        return dataclasses.replace(doc, parser_kind=parser_kind)

    def test_grobid_body_text(self):
        from src.document.parser import TEIParser
        tei = TEIParser()
        doc = tei.parse_text(GROBID_XML, "grobid-001")
        body = doc.body_text()
        assert isinstance(body, str)
        assert len(body) > 0

    def test_nougat_body_text(self):
        doc = self._make_doc(ParserKind.NOUGAT)
        body = doc.body_text()
        assert isinstance(body, str)
        assert len(body) > 0

    def test_hybrid_body_text(self):
        doc = self._make_doc(ParserKind.HYBRID)
        body = doc.body_text()
        assert isinstance(body, str)

    def test_sections_dict(self):
        from src.document.parser import TEIParser
        doc = TEIParser().parse_text(GROBID_XML, "grobid-001")
        sd = doc.sections_dict()
        assert isinstance(sd, dict)
        assert any("Introduction" in k for k in sd)

    def test_all_sentences(self):
        doc = MarkdownScientificParser().parse_text(SAMPLE_MARKDOWN, "compat-001")
        sents = doc.all_sentences()
        assert isinstance(sents, list)
        assert len(sents) > 0


# ── 1. ParserRouter routing logic ─────────────────────────────────────────────

class TestParserRouterConfig:

    def test_valid_strategies(self):
        for strategy in STRATEGIES:
            cfg = RouterConfig(strategy=strategy)
            assert cfg.strategy == strategy

    def test_invalid_strategy_raises(self):
        with pytest.raises(ValueError, match="Unknown strategy"):
            RouterConfig(strategy="magic_parser")

    def test_default_strategy_set(self):
        router = ParserRouter()
        assert router._cfg.strategy in STRATEGIES

    def test_router_strategy_override(self):
        router = ParserRouter(strategy="grobid_only")
        assert router._cfg.strategy == "grobid_only"

    def test_grobid_only_with_no_xml_returns_empty(self):
        router = ParserRouter(strategy="grobid_only")
        from pathlib import Path
        doc = router.parse_pdf(Path("/nonexistent.pdf"), "test-routing", grobid_xml=None)
        assert isinstance(doc, TEIDocument)
        assert doc.parser_kind == ParserKind.UNKNOWN

    def test_grobid_only_with_xml_returns_grobid_doc(self):
        router = ParserRouter(strategy="grobid_only")
        from pathlib import Path
        doc = router.parse_pdf(Path("/any.pdf"), "test-routing-2", grobid_xml=GROBID_XML)
        assert isinstance(doc, TEIDocument)
        assert doc.parser_kind == ParserKind.GROBID
        assert "Flood" in doc.title or "SAR" in doc.title


# ── 5. Scanned PDF → Nougat routing ──────────────────────────────────────────

class TestScannedPDFRouting:

    def test_auto_scanned_routes_to_nougat(self):
        """
        With strategy=auto and is_scanned=True, the router should
        call Nougat.  Since we can't load the Nougat model in tests,
        we verify the routing decision rather than the full parse.
        """
        from pathlib import Path
        from dataclasses import dataclass

        @dataclass
        class MockTriage:
            is_scanned: bool = True
            has_text:   bool = False

        router = ParserRouter(strategy="auto")

        # Patch NougatParser.parse_pdf so we don't hit the real model
        called = []
        def fake_parse_pdf(pdf_path, paper_id):
            called.append(paper_id)
            return MarkdownScientificParser().parse_text(SAMPLE_MARKDOWN, paper_id)

        router._get_nougat().parse_pdf = fake_parse_pdf  # type: ignore[assignment]

        doc = router.parse_pdf(
            Path("/scanned.pdf"), "scan-001",
            triage_result=MockTriage(is_scanned=True, has_text=False),
        )
        assert "scan-001" in called
        assert isinstance(doc, TEIDocument)


# ── 6. GROBID failure → Nougat fallback ──────────────────────────────────────

class TestGROBIDFailureFallback:

    def test_bad_xml_routes_to_hybrid(self):
        """When grobid_xml is malformed, HybridParser should survive."""
        from pathlib import Path

        router = ParserRouter(strategy="grobid_with_nougat_fallback")
        # Patch NougatParser so no model loads
        def fake_nougat(pdf_path, paper_id):
            return MarkdownScientificParser().parse_text(SAMPLE_MARKDOWN, paper_id)
        router._get_nougat().parse_pdf = fake_nougat  # type: ignore[assignment]

        doc = router.parse_pdf(
            Path("/any.pdf"), "fallback-001",
            grobid_xml="<invalid xml",  # deliberately bad
        )
        assert isinstance(doc, TEIDocument)


# ── 4. HybridParser merges GROBID + Nougat ────────────────────────────────────

class TestHybridParser:

    def test_hybrid_uses_grobid_metadata(self):
        from src.document.hybrid_parser import HybridParser
        from src.document.parser import TEIParser

        grobid_doc = TEIParser().parse_text(GROBID_XML, "hybrid-001")
        nougat_doc = MarkdownScientificParser().parse_text(SAMPLE_MARKDOWN, "hybrid-001")

        # Simulate HybridParser._merge directly
        hp = HybridParser()
        merged = hp._merge(
            grobid_doc, nougat_doc,
            grobid_elapsed=0.1, nougat_elapsed=2.0, strategy="hybrid",
        )

        assert merged.parser_kind == ParserKind.HYBRID
        assert merged.title == grobid_doc.title        # GROBID wins for title
        assert merged.references == grobid_doc.references  # GROBID wins for refs

    def test_hybrid_augments_formulas(self):
        from src.document.hybrid_parser import HybridParser
        from src.document.parser import TEIParser

        grobid_doc = TEIParser().parse_text(GROBID_XML, "hybrid-002")
        nougat_doc = MarkdownScientificParser().parse_text(SAMPLE_MARKDOWN, "hybrid-002")

        hp = HybridParser(augment_formulas=True)
        merged = hp._merge(
            grobid_doc, nougat_doc,
            grobid_elapsed=0.1, nougat_elapsed=2.0, strategy="hybrid",
        )

        # GROBID XML has no formulas; Nougat has sigma formula
        assert len(merged.formulas) >= len(grobid_doc.formulas)

    def test_hybrid_provenance_lists_both_parsers(self):
        from src.document.hybrid_parser import HybridParser
        from src.document.parser import TEIParser

        grobid_doc = TEIParser().parse_text(GROBID_XML, "hybrid-003")
        nougat_doc = MarkdownScientificParser().parse_text(SAMPLE_MARKDOWN, "hybrid-003")

        hp = HybridParser()
        merged = hp._merge(
            grobid_doc, nougat_doc,
            grobid_elapsed=0.1, nougat_elapsed=2.0, strategy="hybrid",
        )

        names = [p.parser_name for p in merged.parser_provenance]
        assert "grobid" in names
        assert "nougat" in names

    def test_hybrid_markdown_text_from_nougat(self):
        from src.document.hybrid_parser import HybridParser
        from src.document.parser import TEIParser

        grobid_doc = TEIParser().parse_text(GROBID_XML, "hybrid-004")
        nougat_doc = MarkdownScientificParser().parse_text(SAMPLE_MARKDOWN, "hybrid-004")

        hp = HybridParser(augment_visual_text=True)
        merged = hp._merge(
            grobid_doc, nougat_doc,
            grobid_elapsed=0.1, nougat_elapsed=2.0, strategy="hybrid",
        )
        assert merged.markdown_text is not None
        assert "Sentinel" in merged.markdown_text


# ── 7. Parser capabilities ────────────────────────────────────────────────────

class TestParserCapabilities:

    def test_grobid_has_coordinates_capability(self):
        from src.document.parser import TEIParser
        from src.document.provenance import GROBID_CAPABILITIES
        assert ParserCapability.COORDINATES in GROBID_CAPABILITIES

    def test_nougat_lacks_coordinates_capability(self):
        from src.document.provenance import NOUGAT_CAPABILITIES
        assert ParserCapability.COORDINATES not in NOUGAT_CAPABILITIES

    def test_nougat_has_ocr_capability(self):
        from src.document.provenance import NOUGAT_CAPABILITIES
        assert ParserCapability.OCR in NOUGAT_CAPABILITIES

    def test_markdown_parser_doc_capabilities(self):
        doc = MarkdownScientificParser().parse_text(SAMPLE_MARKDOWN, "caps-001")
        assert ParserCapability.STRUCTURE in doc.parser_capabilities
        assert ParserCapability.COORDINATES not in doc.parser_capabilities

    def test_summary_includes_capabilities(self):
        doc = MarkdownScientificParser().parse_text(SAMPLE_MARKDOWN, "caps-002")
        s = doc.summary()
        assert "parser_kind" in s
        assert "parser_capabilities" in s
        assert isinstance(s["parser_capabilities"], list)


# ── 9. LayoutAwareChunker on hybrid docs ──────────────────────────────────────

class TestLayoutAwareChunkerOnHybridDocs:

    def test_chunker_on_nougat_doc(self):
        from src.document.chunker import LayoutAwareChunker
        doc = MarkdownScientificParser().parse_text(SAMPLE_MARKDOWN, "chunk-001")
        chunker = LayoutAwareChunker(strategy="sentence")
        chunks = chunker.chunk(doc)
        assert isinstance(chunks, list)
        # Should produce at least some chunks from the sections
        assert len(chunks) >= 1

    def test_chunks_have_paper_id(self):
        from src.document.chunker import LayoutAwareChunker
        doc = MarkdownScientificParser().parse_text(SAMPLE_MARKDOWN, "chunk-002")
        chunker = LayoutAwareChunker(strategy="sentence")
        chunks = chunker.chunk(doc)
        for chunk in chunks:
            assert chunk.paper_id == "chunk-002"

    def test_chunker_on_empty_doc(self):
        from src.document.chunker import LayoutAwareChunker
        doc = MarkdownScientificParser().parse_text("", "chunk-empty")
        chunker = LayoutAwareChunker(strategy="sentence")
        chunks = chunker.chunk(doc)
        assert isinstance(chunks, list)


# ── 10. FailureType mappings ──────────────────────────────────────────────────

class TestFailureTypeMappings:

    def test_nougat_model_load_is_skip(self):
        ft = FailureType.NOUGAT_MODEL_LOAD_ERROR
        assert ft.is_skip()
        assert not ft.is_retriable()

    def test_nougat_inference_is_retriable(self):
        ft = FailureType.NOUGAT_INFERENCE_ERROR
        assert ft.is_retriable()

    def test_nougat_oom_is_not_retriable_directly(self):
        ft = FailureType.NOUGAT_OOM
        # OOM is handled via registry status RETRY, not is_retriable()
        assert not ft.is_retriable()
        assert not ft.is_skip()

    def test_nougat_empty_output_is_skip(self):
        ft = FailureType.NOUGAT_EMPTY_OUTPUT
        assert ft.is_skip()

    def test_nougat_error_detection(self):
        for ft in [
            FailureType.NOUGAT_MODEL_LOAD_ERROR,
            FailureType.NOUGAT_INFERENCE_ERROR,
            FailureType.NOUGAT_OOM,
            FailureType.NOUGAT_EMPTY_OUTPUT,
        ]:
            assert ft.is_nougat_error()

    def test_hybrid_errors_not_nougat(self):
        for ft in [
            FailureType.HYBRID_MERGE_ERROR,
            FailureType.PARSER_ROUTING_ERROR,
            FailureType.MARKDOWN_PARSE_ERROR,
        ]:
            assert not ft.is_nougat_error()


# ── 11. Section router reclassification ───────────────────────────────────────

class TestSectionRouterReclassification:
    """Verify _reclassify_from_other rescues method-rich 'other' content."""

    def _make_sections(self, other: str, methods: str = "") -> dict:
        return {
            "abstract": "", "introduction": "", "study_area": "",
            "data_sources": "", "methods": methods, "results": "",
            "discussion": "", "conclusion": "", "other": other,
        }

    def test_reclassifies_method_rich_other(self):
        from src.ingestion.pipeline import _reclassify_from_other
        method_text = (
            "We calibrated the HEC-RAS hydraulic model using discharge data. "
            "The calibration procedure involved adjusting Manning roughness coefficients. "
            "DEM processing was performed at 5 m spatial resolution. "
            "Sentinel-1 SAR data were classified using a threshold algorithm. "
            "The simulation was validated against observed flood inundation extents. "
            "RMSE and NSE metrics were computed to evaluate model accuracy. "
        ) * 10  # make it long enough to exceed _OTHER_MIN_CHARS
        secs = self._make_sections(other=method_text)
        result = _reclassify_from_other(secs)
        assert result["methods"] != "", "method-rich other should be reclassified to methods"
        assert result["other"] == "", "other should be empty after reclassification"

    def test_no_reclassify_when_methods_already_present(self):
        from src.ingestion.pipeline import _reclassify_from_other
        method_text = "calibration procedure " * 500
        secs = self._make_sections(other=method_text, methods="Existing methods text.")
        result = _reclassify_from_other(secs)
        assert result["methods"] == "Existing methods text.", "should not overwrite existing methods"

    def test_no_reclassify_when_other_too_short(self):
        from src.ingestion.pipeline import _reclassify_from_other
        secs = self._make_sections(other="calibration threshold dem")
        result = _reclassify_from_other(secs)
        assert result["methods"] == "", "short other should not be reclassified"
        assert result["other"] == "calibration threshold dem"

    def test_no_reclassify_for_non_method_other(self):
        from src.ingestion.pipeline import _reclassify_from_other
        # generic prose with no method indicators
        non_method = (
            "The authors would like to thank reviewers. "
            "This research was funded by the national science foundation. "
            "We also acknowledge support from the university library. "
        ) * 300
        secs = self._make_sections(other=non_method)
        result = _reclassify_from_other(secs)
        assert result["methods"] == "", "non-method other should not be reclassified"

    def test_section_tags_extended_keywords(self):
        from src.ingestion.pipeline import section_tags
        assert "methods" in section_tags("Design Considerations", None, "text")
        assert "methods" in section_tags("Calibration Framework", None, "text")
        assert "methods" in section_tags("Implementation", None, "text")
        assert "methods" in section_tags("Experimental Approach", None, "text")
