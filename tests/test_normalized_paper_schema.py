"""
tests/test_normalized_paper_schema.py
======================================

Schema contract and normalization summary tests for GeoHydroAI.

These tests do NOT:
    - call OpenAlex
    - write to Neo4j
    - require the ontology registry to be present
    - run the full Ray pipeline

They DO test:
    T1  NormalizedPaper Pydantic schema — valid and invalid inputs
    T2  validate_directory — scanning a temp dir of JSON files
    T3  normalization_summary.generate_summary — synthetic data
    T4  RF → method.random_forest canonical entity is schema-valid
    T5  Unknown entities are counted correctly by generate_summary
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from src.schemas.normalized_paper import (
    SCHEMA_VERSION,
    MatchType,
    NormalizedEntity,
    NormalizedPaper,
    PaperMetadata,
    NormalizationProvenance,
    ReferenceItem,
    validate_paper_dict,
    validate_directory,
)
from src.ontology.refinement.normalization_summary import generate_summary


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

def _minimal_paper(
    paper_id: str = "test_paper_001",
    normalized_entities: dict | None = None,
    schema_version: str = SCHEMA_VERSION,
) -> dict:
    """Build the minimal valid normalized paper dict."""
    return {
        "schema_version": schema_version,
        "metadata": {
            "paper_id": paper_id,
            "title":    "Test Paper on Flood Mapping",
            "doi":      "10.1234/test.001",
            "authors":  ["Alice Smith", "Bob Jones"],
        },
        "sections":  {"abstract": "Flood mapping using SAR imagery."},
        "entities":  {"methods": ["Random Forest"], "satellites": ["Sentinel-1"]},
        "normalized_entities": normalized_entities or {},
        "references": [
            {"title": "Prior work", "authors": ["C Davis"], "year": 2020, "doi": "10.1/x"}
        ],
        "provenance": {
            "parser":    "grobid_tei_kb_v2",
            "judge_used": False,
        },
    }


_PREFIX_TYPE = {
    "method": "method", "sensor": "sensor", "metric": "metric",
    "data": "data", "concept": "concept", "org": "organization",
}


def _norm_entity(
    raw_name: str = "Random Forest",
    canonical_id: str | None = "method.random_forest",
    match_type: str = "alias",
    confidence: float = 1.0,
    source_field: str = "methods",
) -> dict:
    # Derive type from canonical_id prefix so tests use correct ontology types
    if canonical_id:
        prefix = canonical_id.split(".")[0]
        inferred_type = _PREFIX_TYPE.get(prefix, "method")
    else:
        inferred_type = None

    return {
        "raw_name":     raw_name,
        "canonical_id": canonical_id,
        "display_name": raw_name if canonical_id else None,
        "type":         inferred_type,
        "match_type":   match_type,
        "confidence":   confidence,
        "source_field": source_field,
        "evidence":     None,
    }


# ─────────────────────────────────────────────────────────────────────────────
# T1 — NormalizedPaper Pydantic schema
# ─────────────────────────────────────────────────────────────────────────────

class TestNormalizedPaperSchema:

    def test_valid_minimal_paper(self):
        paper = _minimal_paper()
        model = NormalizedPaper.model_validate(paper)
        assert model.schema_version == SCHEMA_VERSION
        assert model.metadata.paper_id == "test_paper_001"

    def test_valid_paper_with_normalized_entities(self):
        paper = _minimal_paper(normalized_entities={
            "methods": [_norm_entity("Random Forest", "method.random_forest", "alias", 1.0)],
            "satellites": [_norm_entity("Sentinel-1", "sensor.sentinel_1", "alias", 1.0, "satellites")],
        })
        model = NormalizedPaper.model_validate(paper)
        assert len(model.normalized_entities["methods"]) == 1
        assert model.normalized_entities["methods"][0].canonical_id == "method.random_forest"
        assert model.normalized_entities["methods"][0].match_type == MatchType.alias

    def test_unknown_entity_canonical_id_null_is_valid(self):
        paper = _minimal_paper(normalized_entities={
            "methods": [_norm_entity("xyzzy unknown term", None, "unknown", 0.0)],
        })
        model = NormalizedPaper.model_validate(paper)
        ent = model.normalized_entities["methods"][0]
        assert ent.canonical_id is None
        assert ent.match_type == MatchType.unknown

    def test_error_entity_canonical_id_null_is_valid(self):
        entity = NormalizedEntity(
            raw_name="bad extract",
            canonical_id=None,
            match_type=MatchType.error,
            confidence=0.0,
        )
        assert entity.canonical_id is None

    def test_alias_entity_without_canonical_id_is_invalid(self):
        with pytest.raises(Exception):
            NormalizedEntity(
                raw_name="Random Forest",
                canonical_id=None,        # not allowed when match_type=alias
                match_type=MatchType.alias,
                confidence=1.0,
            )

    def test_confidence_below_zero_is_invalid(self):
        with pytest.raises(Exception):
            NormalizedEntity(
                raw_name="RF",
                canonical_id="method.random_forest",
                match_type=MatchType.alias,
                confidence=-0.1,
            )

    def test_confidence_above_one_is_invalid(self):
        with pytest.raises(Exception):
            NormalizedEntity(
                raw_name="RF",
                canonical_id="method.random_forest",
                match_type=MatchType.alias,
                confidence=1.1,
            )

    def test_empty_raw_name_is_invalid(self):
        with pytest.raises(Exception):
            NormalizedEntity(
                raw_name="   ",
                match_type=MatchType.unknown,
                confidence=0.0,
            )

    def test_unknown_schema_version_is_invalid(self):
        paper = _minimal_paper(schema_version="9.9")
        ok, msg = validate_paper_dict(paper)
        assert not ok
        assert "schema_version" in msg.lower() or "9.9" in msg

    def test_missing_paper_id_is_invalid(self):
        paper = _minimal_paper()
        del paper["metadata"]["paper_id"]
        ok, msg = validate_paper_dict(paper)
        assert not ok

    def test_match_type_enum_values(self):
        for mt in ("alias", "exact", "semantic", "unknown", "error"):
            ent = NormalizedEntity(
                raw_name="test",
                canonical_id="method.test" if mt not in ("unknown", "error") else None,
                match_type=MatchType(mt),
                confidence=1.0 if mt not in ("unknown", "error") else 0.0,
            )
            assert ent.match_type.value == mt

    def test_invalid_match_type_is_rejected(self):
        with pytest.raises(Exception):
            NormalizedEntity(
                raw_name="test",
                canonical_id="method.test",
                match_type="fuzzy",          # not in enum
                confidence=1.0,
            )

    def test_reference_item_optional_fields(self):
        ref = ReferenceItem(title="Some Paper")
        assert ref.doi is None
        assert ref.authors == []

    def test_extra_fields_allowed_in_provenance(self):
        paper = _minimal_paper()
        paper["provenance"]["custom_field"] = "extra_data"
        model = NormalizedPaper.model_validate(paper)
        assert model.provenance.model_extra.get("custom_field") == "extra_data"


# ─────────────────────────────────────────────────────────────────────────────
# T2 — validate_directory
# ─────────────────────────────────────────────────────────────────────────────

class TestValidateDirectory:

    def test_empty_directory(self, tmp_path):
        result = validate_directory(tmp_path, write_errors=False)
        assert result["total_files"] == 0
        assert result["valid"] == 0
        assert result["invalid"] == 0

    def test_all_valid_files(self, tmp_path):
        for i in range(3):
            paper = _minimal_paper(paper_id=f"paper_{i:03d}")
            (tmp_path / f"paper_{i:03d}.json").write_text(
                json.dumps(paper), encoding="utf-8"
            )
        result = validate_directory(tmp_path, write_errors=False)
        assert result["total_files"] == 3
        assert result["valid"] == 3
        assert result["invalid"] == 0

    def test_mixed_valid_and_invalid(self, tmp_path):
        # valid
        good = _minimal_paper(paper_id="good")
        (tmp_path / "good.json").write_text(json.dumps(good), encoding="utf-8")
        # invalid (missing paper_id)
        bad = _minimal_paper()
        del bad["metadata"]["paper_id"]
        (tmp_path / "bad.json").write_text(json.dumps(bad), encoding="utf-8")
        # corrupt JSON
        (tmp_path / "corrupt.json").write_text("{not valid json", encoding="utf-8")

        result = validate_directory(tmp_path, write_errors=False)
        assert result["total_files"] == 3
        assert result["valid"] == 1
        assert result["invalid"] == 2

    def test_writes_error_report(self, tmp_path):
        qa_dir = tmp_path / "qa"
        qa_dir.mkdir()
        bad = _minimal_paper()
        del bad["metadata"]["paper_id"]
        norm_dir = tmp_path / "normalized"
        norm_dir.mkdir()
        (norm_dir / "bad.json").write_text(json.dumps(bad), encoding="utf-8")

        import src.schemas.normalized_paper as mod
        original_qa = mod._QA_DIR
        mod._QA_DIR = qa_dir
        try:
            result = validate_directory(norm_dir, write_errors=True)
            assert result["invalid"] == 1
            assert (qa_dir / "normalized_schema_errors.json").exists()
        finally:
            mod._QA_DIR = original_qa


# ─────────────────────────────────────────────────────────────────────────────
# T3 — generate_summary (synthetic data)
# ─────────────────────────────────────────────────────────────────────────────

class TestNormalizationSummary:

    def _write_papers(self, directory: Path, papers: list[dict]) -> None:
        for p in papers:
            pid = p["metadata"]["paper_id"]
            (directory / f"{pid}.json").write_text(json.dumps(p), encoding="utf-8")

    def test_empty_directory(self, tmp_path):
        import src.ontology.refinement.normalization_summary as mod
        original = mod._QA_DIR
        mod._QA_DIR = tmp_path / "qa"
        try:
            result = generate_summary(tmp_path)
            assert result["total_papers"] == 0
            assert result["total_normalized_entities"] == 0
        finally:
            mod._QA_DIR = original

    def test_coverage_and_unknown_counts(self, tmp_path):
        norm_dir = tmp_path / "normalized"
        norm_dir.mkdir()

        paper = _minimal_paper(
            paper_id="p001",
            normalized_entities={
                "methods": [
                    _norm_entity("Random Forest", "method.random_forest", "alias", 1.0),
                    _norm_entity("unknown algo",  None,                   "unknown", 0.0),
                ],
                "satellites": [
                    _norm_entity("Sentinel-1", "sensor.sentinel_1", "alias", 1.0, "satellites"),
                ],
            },
        )
        self._write_papers(norm_dir, [paper])

        import src.ontology.refinement.normalization_summary as mod
        original = mod._QA_DIR
        mod._QA_DIR = tmp_path / "qa"
        try:
            result = generate_summary(norm_dir)
            assert result["total_papers"] == 1
            assert result["total_normalized_entities"] == 3
            # 2 of 3 have canonical_id → ~66.67%
            assert result["coverage_percent"] == pytest.approx(66.67, abs=0.1)
            # 1 of 3 is unknown → ~33.33%
            assert result["unknown_percent"] == pytest.approx(33.33, abs=0.1)
            # 2 alias hits → ~66.67%
            assert result["alias_hit_percent"] == pytest.approx(66.67, abs=0.1)
        finally:
            mod._QA_DIR = original

    def test_qa_warning_unknown_threshold(self, tmp_path):
        norm_dir = tmp_path / "normalized"
        norm_dir.mkdir()

        # 3 unknown out of 4 total → 75% unknown → should trigger warning
        paper = _minimal_paper(
            paper_id="p_warn",
            normalized_entities={
                "methods": [
                    _norm_entity("RF", "method.random_forest", "alias", 1.0),
                    _norm_entity("unk1", None, "unknown", 0.0),
                    _norm_entity("unk2", None, "unknown", 0.0),
                    _norm_entity("unk3", None, "unknown", 0.0),
                ],
            },
        )
        self._write_papers(norm_dir, [paper])

        import src.ontology.refinement.normalization_summary as mod
        original = mod._QA_DIR
        mod._QA_DIR = tmp_path / "qa"
        try:
            result = generate_summary(norm_dir)
            assert not result["qa_passed"]
            assert any("unknown_percent" in w for w in result["qa_warnings"])
        finally:
            mod._QA_DIR = original

    def test_output_file_written(self, tmp_path):
        norm_dir = tmp_path / "normalized"
        norm_dir.mkdir()
        qa_dir = tmp_path / "qa"
        paper = _minimal_paper(paper_id="p_out")
        self._write_papers(norm_dir, [paper])

        import src.ontology.refinement.normalization_summary as mod
        original = mod._QA_DIR
        mod._QA_DIR = qa_dir
        try:
            generate_summary(norm_dir)
            assert (qa_dir / "normalization_summary.json").exists()
        finally:
            mod._QA_DIR = original


# ─────────────────────────────────────────────────────────────────────────────
# T4 — RF → method.random_forest is schema-valid
# ─────────────────────────────────────────────────────────────────────────────

class TestRFCanonicalEntity:

    def test_rf_normalized_entity_is_valid(self):
        ent = NormalizedEntity(
            raw_name="Random Forest",
            canonical_id="method.random_forest",
            display_name="Random Forest",
            type="method",
            match_type=MatchType.alias,
            confidence=1.0,
            source_field="methods",
        )
        assert ent.canonical_id == "method.random_forest"
        assert ent.match_type == MatchType.alias
        assert ent.confidence == 1.0

    def test_rf_in_full_paper_validates(self):
        paper = _minimal_paper(normalized_entities={
            "methods": [
                _norm_entity("Random Forest", "method.random_forest", "alias", 1.0),
                _norm_entity("RF",            "method.random_forest", "alias", 1.0),
            ],
        })
        ok, msg = validate_paper_dict(paper)
        assert ok, f"Schema validation failed: {msg}"

    def test_rf_semantic_match_at_boundary(self):
        # semantic match with confidence exactly at threshold is valid
        ent = NormalizedEntity(
            raw_name="random forest regression",
            canonical_id="method.random_forest",
            display_name="Random Forest",
            type="method",
            match_type=MatchType.semantic,
            confidence=0.82,
            source_field="methods",
        )
        assert ent.match_type == MatchType.semantic
        assert ent.confidence == 0.82


# ─────────────────────────────────────────────────────────────────────────────
# T5 — Unknown entity counting in summary
# ─────────────────────────────────────────────────────────────────────────────

class TestUnknownEntityCounting:

    def _summary_from_papers(self, papers: list[dict], tmp_path: Path) -> dict:
        norm_dir = tmp_path / "normalized"
        norm_dir.mkdir(exist_ok=True)
        qa_dir   = tmp_path / "qa"
        for p in papers:
            pid = p["metadata"]["paper_id"]
            (norm_dir / f"{pid}.json").write_text(json.dumps(p), encoding="utf-8")

        import src.ontology.refinement.normalization_summary as mod
        original = mod._QA_DIR
        mod._QA_DIR = qa_dir
        try:
            return generate_summary(norm_dir)
        finally:
            mod._QA_DIR = original

    def test_all_unknown(self, tmp_path):
        paper = _minimal_paper(
            paper_id="all_unk",
            normalized_entities={
                "methods": [
                    _norm_entity("unk1", None, "unknown", 0.0),
                    _norm_entity("unk2", None, "unknown", 0.0),
                ],
            },
        )
        result = self._summary_from_papers([paper], tmp_path)
        assert result["unknown_percent"] == 100.0
        assert result["coverage_percent"] == 0.0

    def test_all_alias_hits(self, tmp_path):
        paper = _minimal_paper(
            paper_id="all_alias",
            normalized_entities={
                "methods": [
                    _norm_entity("Random Forest", "method.random_forest", "alias", 1.0),
                    _norm_entity("LSTM",          "method.lstm",          "alias", 1.0),
                ],
            },
        )
        result = self._summary_from_papers([paper], tmp_path)
        assert result["unknown_percent"] == 0.0
        assert result["coverage_percent"] == 100.0
        assert result["alias_hit_percent"] == 100.0

    def test_top_unresolved_sorted_by_frequency(self, tmp_path):
        papers = []
        for i in range(5):
            papers.append(_minimal_paper(
                paper_id=f"p_{i}",
                normalized_entities={
                    "methods": [
                        _norm_entity("rare term",    None, "unknown", 0.0),
                        _norm_entity("common term",  None, "unknown", 0.0),
                        _norm_entity("common term",  None, "unknown", 0.0),
                    ],
                },
            ))
        result = self._summary_from_papers(papers, tmp_path)
        unresolved = result["top_unresolved_entities"]
        assert len(unresolved) >= 2
        # common term appears 10x, rare term 5x → common first
        first = unresolved[0]["mention"]
        assert first == "common term"

    def test_error_entities_counted_separately(self, tmp_path):
        paper = _minimal_paper(
            paper_id="with_errors",
            normalized_entities={
                "methods": [
                    _norm_entity("crash", None, "error", 0.0),
                    _norm_entity("RF", "method.random_forest", "alias", 1.0),
                ],
            },
        )
        result = self._summary_from_papers([paper], tmp_path)
        assert result["error_percent"] == 50.0
        assert result["unknown_percent"] == 0.0  # error ≠ unknown

    def test_by_entity_type_coverage(self, tmp_path):
        paper = _minimal_paper(
            paper_id="by_type",
            normalized_entities={
                "methods": [
                    _norm_entity("RF", "method.random_forest", "alias", 1.0, "methods"),
                    _norm_entity("LSTM", "method.lstm",         "alias", 1.0, "methods"),
                    _norm_entity("unk", None,                   "unknown", 0.0, "methods"),
                ],
                "satellites": [
                    _norm_entity("S1", "sensor.sentinel_1", "alias", 1.0, "satellites"),
                ],
            },
        )
        result = self._summary_from_papers([paper], tmp_path)
        method_stats = result["by_entity_type"].get("method", {})
        sensor_stats = result["by_entity_type"].get("sensor", {})
        assert sensor_stats.get("coverage_percent", 0) == 100.0
        # method: 2/3 covered → ~66.67%
        assert method_stats.get("coverage_percent", 0) == pytest.approx(66.67, abs=0.1)
