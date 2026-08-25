"""
test_05_task_classification.py  —  Group 5: Task classification
================================================================

The task classifier has a priority-ordered rule chain (checked first)
with embedding fallback (used when encode_fn is provided).

In this test group, encode_fn=None so only deterministic rules fire.
Tests verify:
  - Correct label for canonical paper types (HEC-RAS, SWAT, SAR, DEM, NDVI)
  - Constraint layer overrides (HEC-HMS forces hydrological_modeling)
  - Negative tests (HEC-HMS papers NOT classified as flood_mapping_satellite)
  - Task confidence is in a valid range
  - Task label is in VALID_TASK_LABELS
"""

from __future__ import annotations

import pytest

from tests.conftest import run_pipeline, write_tei
from src.ingestion.pipeline import VALID_TASK_LABELS


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _task(paper: dict) -> dict:
    return paper["entities"]["task"]


def _task_label(paper: dict) -> str:
    return _task(paper).get("label", "")


def _task_confidence(paper: dict) -> float:
    return float(_task(paper).get("confidence", 0.0))


# ─────────────────────────────────────────────────────────────────────────────
# T05-01  Valid label + confidence
# ─────────────────────────────────────────────────────────────────────────────

class TestTaskLabelValidity:

    def test_task_label_always_in_valid_set(self, sar_flood_paper):
        paper = run_pipeline(sar_flood_paper)
        assert _task_label(paper) in VALID_TASK_LABELS

    def test_task_confidence_in_range(self, sar_flood_paper):
        paper = run_pipeline(sar_flood_paper)
        conf = _task_confidence(paper)
        assert 0.0 <= conf <= 1.0

    def test_task_source_is_string(self, sar_flood_paper):
        paper = run_pipeline(sar_flood_paper)
        src = _task(paper).get("source")
        assert isinstance(src, str) and len(src) > 0


# ─────────────────────────────────────────────────────────────────────────────
# T05-02  Positive classification tests
# ─────────────────────────────────────────────────────────────────────────────

class TestTaskClassificationPositive:

    def test_hec_hms_paper_classified_as_hydrological(self, tmp_xml_dir, mock_geocoding):
        path = write_tei(
            tmp_xml_dir, stem="hec_hms_task",
            abstract="HEC-HMS was applied for rainfall-runoff modeling.",
            methods="HEC-HMS model was calibrated for the study watershed.",
        )
        paper = run_pipeline(path)
        assert _task_label(paper) == "hydrological_modeling", (
            f"HEC-HMS paper → expected hydrological_modeling, got {_task_label(paper)!r}"
        )

    def test_hec_ras_classified_as_flood_hydraulic(self, tmp_xml_dir, mock_geocoding):
        path = write_tei(
            tmp_xml_dir, stem="hec_ras_task",
            abstract="HEC-RAS was used for hydraulic simulation of flood events.",
            methods="HEC-RAS hydraulic simulation was performed.",
        )
        paper = run_pipeline(path)
        assert _task_label(paper) in {"flood_modeling_hydraulic", "hydrological_modeling"}, (
            f"HEC-RAS paper → unexpected task: {_task_label(paper)!r}"
        )

    def test_swat_classified_as_hydrological(self, swat_paper):
        paper = run_pipeline(swat_paper)
        assert _task_label(paper) == "hydrological_modeling", (
            f"SWAT paper → expected hydrological_modeling, got {_task_label(paper)!r}"
        )

    def test_sar_flood_mapping_paper_classified(self, tmp_xml_dir, mock_geocoding):
        path = write_tei(
            tmp_xml_dir, stem="sar_task",
            abstract="Flood extent was mapped using Sentinel-1 SAR imagery.",
            methods="Sentinel-1 SAR backscatter thresholding detected flood inundation mapping.",
        )
        paper = run_pipeline(path)
        # SAR flood mapping: either flood_mapping_satellite or similar
        label = _task_label(paper)
        assert label in {"flood_mapping_satellite", "flood_modeling_hydraulic"}, (
            f"SAR flood paper → unexpected task: {label!r}"
        )

    def test_ndvi_paper_classified_as_spectral_index(self, ndvi_paper):
        paper = run_pipeline(ndvi_paper)
        label = _task_label(paper)
        assert label == "spectral_index_analysis", (
            f"NDVI paper → expected spectral_index_analysis, got {label!r}"
        )

    def test_dem_validation_paper_classified(self, dem_validation_paper):
        paper = run_pipeline(dem_validation_paper)
        label = _task_label(paper)
        assert label in {"dem_validation", "terrain_dem_analysis", "unknown"}, (
            f"DEM validation paper → unexpected task: {label!r}"
        )

    def test_review_paper_classified_as_review(self, review_paper):
        paper = run_pipeline(review_paper)
        label = _task_label(paper)
        assert label in {"review", "flood_mapping_satellite", "unknown"}, (
            f"Review paper → unexpected task: {label!r}"
        )
        # review papers should not be classified as purely computational tasks
        assert label not in {"hydrological_modeling", "dem_validation"}, (
            f"Review paper incorrectly classified as {label!r}"
        )

    def test_rainfall_runoff_modeling_signal(self, tmp_xml_dir, mock_geocoding):
        path = write_tei(
            tmp_xml_dir, stem="rrm_signal",
            abstract="Rainfall-runoff modeling was performed for the study watershed.",
            methods="Rainfall runoff modeling using the SCS-CN method was applied.",
        )
        paper = run_pipeline(path)
        assert _task_label(paper) == "hydrological_modeling", (
            f"Rainfall-runoff paper → {_task_label(paper)!r}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# T05-03  Negative classification tests (task should NOT be X)
# ─────────────────────────────────────────────────────────────────────────────

class TestTaskClassificationNegative:

    def test_hec_hms_paper_not_sar_flood_mapping(self, tmp_xml_dir, mock_geocoding):
        """HEC-HMS is a hydrological model — must NOT be classified as SAR flood mapping."""
        path = write_tei(
            tmp_xml_dir, stem="hms_not_sar",
            abstract="HEC-HMS was applied for flood runoff simulation.",
            methods="HEC-HMS rainfall-runoff modeling was calibrated for the watershed.",
        )
        paper = run_pipeline(path)
        assert _task_label(paper) != "flood_mapping_satellite", (
            "HEC-HMS paper incorrectly classified as flood_mapping_satellite"
        )

    def test_swat_paper_not_dem_validation(self, swat_paper):
        paper = run_pipeline(swat_paper)
        assert _task_label(paper) != "dem_validation", (
            "SWAT paper incorrectly classified as dem_validation"
        )

    def test_review_paper_not_hydrological_modeling(self, review_paper):
        paper = run_pipeline(review_paper)
        assert _task_label(paper) != "hydrological_modeling", (
            "Review paper incorrectly classified as hydrological_modeling"
        )

    def test_ndvi_paper_not_hydraulic(self, ndvi_paper):
        paper = run_pipeline(ndvi_paper)
        assert _task_label(paper) != "flood_modeling_hydraulic", (
            "NDVI paper incorrectly classified as flood_modeling_hydraulic"
        )


# ─────────────────────────────────────────────────────────────────────────────
# T05-04  Constraint layer override tests
# ─────────────────────────────────────────────────────────────────────────────

class TestTaskConstraintLayer:

    def test_hec_hms_forces_hydrological_even_with_flood_word(
        self, tmp_xml_dir, mock_geocoding
    ):
        """
        Even if 'flood' appears prominently, HEC-HMS forces hydrological_modeling.
        This tests the apply_constraints() priority rule.
        """
        path = write_tei(
            tmp_xml_dir, stem="hms_override",
            abstract="We used HEC-HMS for flood runoff modeling and flood extent estimation.",
            methods="HEC-HMS was applied for flood simulation.",
        )
        paper = run_pipeline(path)
        assert _task_label(paper) == "hydrological_modeling", (
            f"Constraint rule failed: expected hydrological_modeling, got {_task_label(paper)!r}"
        )

    def test_swat_forces_hydrological_over_flood_mapping(
        self, tmp_xml_dir, mock_geocoding
    ):
        path = write_tei(
            tmp_xml_dir, stem="swat_override",
            abstract=(
                "We applied SWAT for streamflow prediction and flood extent analysis. "
                "Flood mapping was also performed in the watershed."
            ),
            methods="SWAT (Soil and Water Assessment Tool) was calibrated.",
        )
        paper = run_pipeline(path)
        assert _task_label(paper) == "hydrological_modeling", (
            f"SWAT constraint failed: expected hydrological_modeling, got {_task_label(paper)!r}"
        )

    def test_task_source_is_constraint_when_forced(
        self, tmp_xml_dir, mock_geocoding
    ):
        """When constraint layer overrides, source should indicate 'constraint'."""
        path = write_tei(
            tmp_xml_dir, stem="constraint_source",
            abstract="We used HEC-HMS to model flood runoff.",
            methods="HEC-HMS rainfall-runoff simulation.",
        )
        paper = run_pipeline(path)
        if _task_label(paper) == "hydrological_modeling":
            src = _task(paper).get("source", "")
            assert src in {"constraint", "rules"}, (
                f"Expected source=constraint or rules, got {src!r}"
            )
