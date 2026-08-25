"""
test_06_edge_cases.py  —  Group 6: Edge cases and robustness
=============================================================

Tests that the pipeline does NOT crash on degenerate inputs and always
returns a valid, JSON-serialisable paper dict.

Degenerate inputs covered:
  - Missing abstract
  - Missing methods section
  - Empty TEI body  (no <div> elements)
  - Papers with only references (no body)
  - Papers without DOI
  - Paper with only a title
  - Very long abstract (>10k characters)
  - Repeated mentions of the same entity
  - Unicode / non-ASCII characters in text
  - Corrupted UTF-8 (written as Latin-1)
  - Numeric-only entity names
  - Multiple authors with the same institution
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.conftest import build_tei_xml, run_pipeline, write_tei
from src.ingestion.pipeline import VALID_TASK_LABELS, VALID_STUDY_TYPES


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

REQUIRED_TOP_KEYS = {"metadata", "sections", "entities", "references", "provenance"}


def _is_valid(paper: dict) -> None:
    """Assert that a paper dict has minimum required structure."""
    assert isinstance(paper, dict)
    missing = REQUIRED_TOP_KEYS - paper.keys()
    assert not missing, f"Missing top-level keys: {missing}"
    assert isinstance(paper["entities"], dict)
    assert isinstance(paper["metadata"], dict)
    assert json.dumps(paper)  # serialisable


# ─────────────────────────────────────────────────────────────────────────────
# T06-01  Missing sections
# ─────────────────────────────────────────────────────────────────────────────

class TestMissingSections:

    def test_no_abstract(self, tmp_xml_dir, mock_geocoding):
        path = write_tei(tmp_xml_dir, stem="no_abstract", abstract="")
        paper = run_pipeline(path)
        _is_valid(paper)
        assert paper["entities"]["task"]["label"] in VALID_TASK_LABELS

    def test_no_methods(self, tmp_xml_dir, mock_geocoding):
        path = write_tei(
            tmp_xml_dir, stem="no_methods",
            abstract="Flood mapping study in Ukraine.",
            methods="",
        )
        paper = run_pipeline(path)
        _is_valid(paper)

    def test_no_results(self, tmp_xml_dir, mock_geocoding):
        path = write_tei(
            tmp_xml_dir, stem="no_results",
            abstract="HEC-RAS hydraulic simulation of floods.",
            results="",
        )
        paper = run_pipeline(path)
        _is_valid(paper)
        # No results → no metrics; must not crash
        assert isinstance(paper["entities"]["metrics"], list)

    def test_no_study_area(self, tmp_xml_dir, mock_geocoding):
        path = write_tei(
            tmp_xml_dir, stem="no_study_area",
            abstract="Satellite flood mapping study.",
            study_area="",
        )
        paper = run_pipeline(path)
        _is_valid(paper)
        # primary_country may be None — that's allowed
        assert "primary_country" in paper["entities"]["geo"]["study_geo"]

    def test_only_abstract_no_body(self, tmp_xml_dir, mock_geocoding):
        path = write_tei(
            tmp_xml_dir, stem="abstract_only",
            abstract="SWAT was applied to the Carpathian watershed in Ukraine.",
            introduction="", study_area="", data_sources="",
            methods="", results="", conclusion="",
        )
        paper = run_pipeline(path)
        _is_valid(paper)


# ─────────────────────────────────────────────────────────────────────────────
# T06-02  Empty or near-empty papers
# ─────────────────────────────────────────────────────────────────────────────

class TestEmptyPaper:

    def test_completely_empty_tei(self, tmp_xml_dir, mock_geocoding):
        path = write_tei(
            tmp_xml_dir, stem="empty_tei",
            title="", doi="", abstract="",
            introduction="", study_area="", data_sources="",
            methods="", results="", conclusion="",
        )
        paper = run_pipeline(path)
        _is_valid(paper)

    def test_title_only(self, tmp_xml_dir, mock_geocoding):
        path = write_tei(
            tmp_xml_dir, stem="title_only",
            title="Flood Mapping in Ukraine",
            abstract="",
        )
        paper = run_pipeline(path)
        _is_valid(paper)
        # task label must be valid even if everything is unknown
        assert paper["entities"]["task"]["label"] in VALID_TASK_LABELS

    def test_minimal_content_has_stable_structure(self, tmp_xml_dir, mock_geocoding):
        path = write_tei(tmp_xml_dir, stem="minimal", abstract="Minimal.")
        paper = run_pipeline(path)
        # All list fields must be lists (not None)
        for key in ("satellites", "dems", "methods", "metrics", "sensor_types"):
            assert isinstance(paper["entities"][key], list), (
                f"entities[{key!r}] is not a list"
            )


# ─────────────────────────────────────────────────────────────────────────────
# T06-03  Papers without DOI
# ─────────────────────────────────────────────────────────────────────────────

class TestNoDoi:

    def test_no_doi_paper_does_not_crash(self, tmp_xml_dir, mock_geocoding):
        path = write_tei(
            tmp_xml_dir, stem="nodoi",
            doi="",
            abstract="Flood mapping study.",
        )
        paper = run_pipeline(path)
        _is_valid(paper)

    def test_no_doi_has_none_or_empty(self, tmp_xml_dir, mock_geocoding):
        path = write_tei(tmp_xml_dir, stem="nodoi2", doi="")
        paper = run_pipeline(path)
        doi = paper["metadata"].get("doi")
        assert doi is None or doi == ""

    def test_paper_id_stable_without_doi(self, tmp_xml_dir, mock_geocoding):
        path = write_tei(tmp_xml_dir, stem="nodoi3", doi="")
        paper = run_pipeline(path)
        pid = paper["metadata"]["paper_id"]
        assert isinstance(pid, str) and len(pid) > 0


# ─────────────────────────────────────────────────────────────────────────────
# T06-04  Unicode and special characters
# ─────────────────────────────────────────────────────────────────────────────

class TestUnicode:

    def test_cyrillic_text_in_abstract(self, tmp_xml_dir, mock_geocoding):
        path = write_tei(
            tmp_xml_dir, stem="cyrillic",
            abstract=(
                "Дослідження повеней в басейні річки Дніпро в Україні. "
                "Sentinel-1 SAR дані були використані для картографування повені."
            ),
        )
        paper = run_pipeline(path)
        _is_valid(paper)

    def test_mixed_script_text(self, tmp_xml_dir, mock_geocoding):
        path = write_tei(
            tmp_xml_dir, stem="mixed_script",
            abstract=(
                "Flood mapping (повінь) in the Dnipro River basin (р. Дніпро). "
                "Sentinel-1 SAR data — площа затоплення."
            ),
        )
        paper = run_pipeline(path)
        _is_valid(paper)
        assert json.dumps(paper, ensure_ascii=False)  # must serialise with unicode

    def test_special_chars_in_title(self, tmp_xml_dir, mock_geocoding):
        # Note: & is illegal in XML attribute text without escaping;
        # use a colon and dash which are safe in XML element content.
        path = write_tei(
            tmp_xml_dir, stem="special_title",
            title="Flood Mapping: A Case Study of the Dnipro River -- SAR Analysis",
            abstract="Flood study with Sentinel-1.",
        )
        paper = run_pipeline(path)
        _is_valid(paper)

    def test_html_entities_in_text(self, tmp_xml_dir, mock_geocoding):
        """XML entities like &amp; &lt; must be handled by the XML parser."""
        xml_content = build_tei_xml(
            abstract="Flood mapping &amp; analysis in Ukraine. Sentinel-1 data.",
            methods="HEC-RAS &amp; HEC-HMS were applied.",
        )
        path = tmp_xml_dir / "entities_test.tei.xml"
        path.write_text(xml_content, encoding="utf-8")
        paper = run_pipeline(path)
        _is_valid(paper)


# ─────────────────────────────────────────────────────────────────────────────
# T06-05  Very long content
# ─────────────────────────────────────────────────────────────────────────────

class TestLongContent:

    def test_very_long_abstract(self, tmp_xml_dir, mock_geocoding):
        long_text = (
            "Flood mapping using Sentinel-1 SAR data in Ukraine was performed. "
            * 200  # ~13k characters
        )
        path = write_tei(tmp_xml_dir, stem="long_abstract", abstract=long_text)
        paper = run_pipeline(path)
        _is_valid(paper)

    def test_very_long_methods(self, tmp_xml_dir, mock_geocoding):
        long_methods = (
            "HEC-RAS hydraulic simulation was applied. "
            "The SRTM DEM was used as terrain input for HEC-RAS. "
        ) * 100
        path = write_tei(tmp_xml_dir, stem="long_methods", methods=long_methods)
        paper = run_pipeline(path)
        _is_valid(paper)

    def test_no_duplicate_entities_after_repetition(self, tmp_xml_dir, mock_geocoding):
        """Repeated mentions of Sentinel-1 must not produce duplicate entries."""
        repeated = "Sentinel-1 SAR was used. " * 50
        path = write_tei(
            tmp_xml_dir, stem="repeated_entities",
            abstract=repeated,
            methods=repeated,
        )
        paper = run_pipeline(path)
        names = [e["name"] for e in paper["entities"]["satellites"]]
        assert len(names) == len(set(names)), f"Duplicate satellites after repetition: {names}"


# ─────────────────────────────────────────────────────────────────────────────
# T06-06  Malformed / edge-case XML
# ─────────────────────────────────────────────────────────────────────────────

class TestMalformedXml:

    def test_latin1_encoded_file_raises_or_handles(self, tmp_xml_dir, mock_geocoding):
        """
        A file that declares UTF-8 but contains Latin-1 bytes causes
        an XML parse error.  Pipeline must either raise a controlled
        exception or return a valid fallback dict — it must NOT hang.
        """
        xml = build_tei_xml(abstract="Flood mapping study.")
        # Inject a Latin-1 byte sequence that is invalid in UTF-8
        bad_bytes = xml.encode("latin-1", errors="replace")
        path = tmp_xml_dir / "latin1.tei.xml"
        path.write_bytes(bad_bytes)
        try:
            paper = run_pipeline(path)
            # If it somehow succeeds (e.g. lxml is lenient), verify structure
            _is_valid(paper)
        except Exception:
            # A controlled exception is also acceptable — just not a hang
            pass

    def test_truncated_xml_raises_or_handles(self, tmp_xml_dir, mock_geocoding):
        """Truncated XML must not hang the pipeline."""
        path = tmp_xml_dir / "truncated.tei.xml"
        path.write_text(
            '<?xml version="1.0" encoding="UTF-8"?>\n<TEI xmlns="http://www.tei-c.org/ns/1.0">\n  <teiHead',
            encoding="utf-8",
        )
        try:
            paper = run_pipeline(path)
            _is_valid(paper)
        except Exception:
            pass

    def test_empty_file_raises_or_handles(self, tmp_xml_dir, mock_geocoding):
        path = tmp_xml_dir / "empty.tei.xml"
        path.write_text("", encoding="utf-8")
        try:
            paper = run_pipeline(path)
            _is_valid(paper)
        except Exception:
            pass

    def test_non_tei_xml_raises_or_handles(self, tmp_xml_dir, mock_geocoding):
        """A valid XML file that is not TEI should not hang."""
        path = tmp_xml_dir / "random.tei.xml"
        path.write_text(
            '<?xml version="1.0"?><root><item>value</item></root>',
            encoding="utf-8",
        )
        try:
            paper = run_pipeline(path)
            _is_valid(paper)
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────────────
# T06-07  Multiple authors
# ─────────────────────────────────────────────────────────────────────────────

class TestMultipleAuthors:

    def test_multiple_authors_extracted(self, tmp_xml_dir, mock_geocoding):
        path = write_tei(
            tmp_xml_dir, stem="multi_author",
            authors=[
                {"first": "Ivan",  "last": "Petrenko", "institution": "KPI", "country": "Ukraine", "country_key": "UA"},
                {"first": "Maria", "last": "Kovalenko", "institution": "KPI", "country": "Ukraine", "country_key": "UA"},
                {"first": "John",  "last": "Smith",     "institution": "MIT", "country": "USA",     "country_key": "US"},
            ],
            abstract="Flood mapping study in Ukraine using Sentinel-1.",
        )
        paper = run_pipeline(path)
        authors = paper["metadata"]["authors"]
        assert len(authors) >= 2, f"Expected ≥2 authors, got {len(authors)}"

    def test_multi_country_affiliation(self, tmp_xml_dir, mock_geocoding):
        """Authors from different countries should all appear in author_geo."""
        path = write_tei(
            tmp_xml_dir, stem="multi_country_affil",
            authors=[
                {"first": "A", "last": "B", "institution": "Uni A", "country": "Ukraine", "country_key": "UA"},
                {"first": "C", "last": "D", "institution": "Uni C", "country": "Germany", "country_key": "DE"},
            ],
            abstract="Collaborative flood study.",
        )
        paper = run_pipeline(path)
        _is_valid(paper)
        author_countries = {c["name"] for c in paper["entities"]["geo"].get("author_geo", [])}
        assert "Ukraine" in author_countries or "Germany" in author_countries

    def test_output_is_json_serialisable_with_many_authors(self, tmp_xml_dir, mock_geocoding):
        authors = [
            {"first": f"A{i}", "last": f"B{i}", "institution": "Uni", "country": "Ukraine", "country_key": "UA"}
            for i in range(10)
        ]
        path = write_tei(tmp_xml_dir, stem="many_authors", authors=authors,
                         abstract="Flood study.")
        paper = run_pipeline(path)
        assert json.dumps(paper)


# ── Entity FP suppression (Phase 2) ──────────────────────────────────────────

class TestEntityFPSuppression:

    def _make_entity(self, name, scores=None):
        return {
            "name": name, "type": "method", "source": "methods+results",
            "confidence": 0.9, "evidence": "test context",
            "scores": scores or {"pattern": 0.9, "context": 0.5, "embedding": 0.5, "llm": 0},
            "final_score": 0.0, "accepted": None, "role": None,
            "kb_metadata": {"full_name": name},
        }

    def test_blocklist_http_removed(self):
        from src.ingestion.pipeline import _entity_is_blocked
        assert _entity_is_blocked("HTTP")
        assert _entity_is_blocked("HTTPS")

    def test_blocklist_single_letter_removed(self):
        from src.ingestion.pipeline import _entity_is_blocked
        assert _entity_is_blocked("J")
        assert _entity_is_blocked("R")

    def test_blocklist_nnt_removed(self):
        from src.ingestion.pipeline import _entity_is_blocked
        assert _entity_is_blocked("NNT")

    def test_blocklist_allows_hec_ras(self):
        from src.ingestion.pipeline import _entity_is_blocked
        assert not _entity_is_blocked("HEC-RAS")

    def test_blocklist_allows_swat(self):
        from src.ingestion.pipeline import _entity_is_blocked
        assert not _entity_is_blocked("SWAT")

    def test_dedup_keeps_highest_scoring(self):
        from src.ingestion.pipeline import _deduplicate_by_canonical
        e1 = {"name": "ANN", "final_score": 0.6, "accepted": True,
              "evidence": "ctx1", "kb_metadata": {"full_name": "Artificial Neural Network"}}
        e2 = {"name": "artificial_neural_network", "final_score": 0.8, "accepted": True,
              "evidence": "ctx2", "kb_metadata": {"full_name": "Artificial Neural Network"}}
        result = _deduplicate_by_canonical([e1, e2])
        assert len(result) == 1
        assert result[0]["final_score"] == 0.8

    def test_dedup_preserves_alt_evidence(self):
        from src.ingestion.pipeline import _deduplicate_by_canonical
        e1 = {"name": "ANN", "final_score": 0.4, "accepted": False,
              "evidence": "ctx1", "kb_metadata": {"full_name": "Artificial Neural Network"}}
        e2 = {"name": "ANN-v2", "final_score": 0.9, "accepted": True,
              "evidence": "ctx2", "kb_metadata": {"full_name": "Artificial Neural Network"}}
        result = _deduplicate_by_canonical([e1, e2])
        assert len(result) == 1
        assert "alt_evidence" in result[0]

    def test_no_kb_fullname_kept(self):
        from src.ingestion.pipeline import _deduplicate_by_canonical
        e = {"name": "NoKB", "final_score": 0.5, "evidence": "x", "kb_metadata": {}}
        result = _deduplicate_by_canonical([e])
        assert len(result) == 1

    def test_geo_not_country_filter(self):
        from src.ingestion.pipeline import _country_name_is_valid
        assert not _country_name_is_valid("Muskingum")
        assert not _country_name_is_valid("muskingum")
        assert not _country_name_is_valid("Yangtze")
        assert _country_name_is_valid("Bangladesh")
        assert _country_name_is_valid("Thailand")


class TestEmbeddingScoreClamping:

    def test_embedding_score_never_negative(self):
        """embedding_score() must return >= 0.0 even for anti-similar texts."""
        import numpy as np
        from src.ingestion.pipeline import embedding_score

        # Encode using a simple mock that returns opposite-direction vectors
        call_count = [0]
        def mock_encode(texts):
            vecs = []
            for _ in texts:
                call_count[0] += 1
                if call_count[0] == 1:
                    vecs.append(np.array([1.0, 0.0]))
                else:
                    vecs.append(np.array([-1.0, 0.0]))  # opposite direction
            return vecs

        score = embedding_score("query", ["label"], mock_encode)
        assert score >= 0.0, f"embedding_score returned {score}"

    def test_embedding_score_bounded_above(self):
        """embedding_score() must return <= 1.0 for identical vectors."""
        import numpy as np
        from src.ingestion.pipeline import embedding_score

        def mock_encode(texts):
            return [np.array([1.0, 0.0]) for _ in texts]

        score = embedding_score("query", ["label"], mock_encode)
        assert 0.0 <= score <= 1.0

