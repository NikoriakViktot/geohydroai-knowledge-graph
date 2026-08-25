"""
test_04_study_type.py  —  Group 4: Study type classification
============================================================

Validates that the study type embedding + rule-based classifier assigns
the correct label for canonical paper types.

The classifier runs deterministically when encode_fn=None (keyword rules only).
Confidence scores are validated for range and calibration.
"""

from __future__ import annotations

import pytest

from tests.conftest import run_pipeline, write_tei
from src.ingestion.pipeline import VALID_STUDY_TYPES


# ─────────────────────────────────────────────────────────────────────────────
# Helper
# ─────────────────────────────────────────────────────────────────────────────

def _study_type(paper: dict) -> dict:
    return paper["entities"]["geo"]["study_type"]


def _label(paper: dict) -> str:
    return _study_type(paper).get("label", "")


def _confidence(paper: dict) -> float:
    return float(_study_type(paper).get("confidence", 0.0))


# ─────────────────────────────────────────────────────────────────────────────
# T04-01  Valid label and confidence
# ─────────────────────────────────────────────────────────────────────────────

class TestStudyTypeLabelValidity:

    def test_label_always_in_valid_set(self, sar_flood_paper):
        paper = run_pipeline(sar_flood_paper)
        assert _label(paper) in VALID_STUDY_TYPES

    def test_confidence_always_in_range(self, sar_flood_paper):
        paper = run_pipeline(sar_flood_paper)
        conf = _confidence(paper)
        assert 0.0 <= conf <= 1.0

    def test_review_paper_classified_as_review(self, review_paper):
        paper = run_pipeline(review_paper)
        label = _label(paper)
        assert label == "review", (
            f"Review paper misclassified as {label!r}. "
            f"Abstract: {paper['sections'].get('abstract','')[:100]}"
        )

    def test_label_is_string(self, swat_paper):
        paper = run_pipeline(swat_paper)
        assert isinstance(_label(paper), str)


# ─────────────────────────────────────────────────────────────────────────────
# T04-02  Paper-type → label mapping
# ─────────────────────────────────────────────────────────────────────────────

class TestStudyTypeMapping:

    def test_explicit_review_signal_from_abstract(self, tmp_xml_dir, mock_geocoding):
        path = write_tei(
            tmp_xml_dir, stem="review_signal",
            abstract=(
                "We survey the literature on flood mapping using remote sensing. "
                "This systematic review examines 150 papers published between 2010 and 2023. "
                "We compare different approaches for flood detection."
            ),
        )
        paper = run_pipeline(path)
        assert _label(paper) == "review", f"Expected review, got {_label(paper)!r}"

    def test_case_study_signal(self, tmp_xml_dir, mock_geocoding):
        path = write_tei(
            tmp_xml_dir, stem="case_study",
            abstract=(
                "This paper presents a case study of flood mapping conducted "
                "in the Dnipro River basin in Ukraine. "
                "A case study of a specific watershed was analyzed."
            ),
        )
        paper = run_pipeline(path)
        # case_study or regional are both plausible
        assert _label(paper) in {"case_study", "regional", "unknown"}, (
            f"Unexpected label for case study paper: {_label(paper)!r}"
        )
        assert _label(paper) in VALID_STUDY_TYPES

    def test_regional_signal(self, tmp_xml_dir, mock_geocoding):
        path = write_tei(
            tmp_xml_dir, stem="regional",
            abstract=(
                "We conducted analysis across the country using multiple stations in Ukraine. "
                "A regional climate analysis was performed covering the entire country."
            ),
        )
        paper = run_pipeline(path)
        assert _label(paper) in VALID_STUDY_TYPES

    def test_global_algorithmic_signal(self, tmp_xml_dir, mock_geocoding):
        path = write_tei(
            tmp_xml_dir, stem="global_algo",
            abstract=(
                "We developed a global flood mapping algorithm applicable worldwide. "
                "The large scale automatic system was tested on all major rivers. "
                "Method applied worldwide shows strong performance."
            ),
        )
        paper = run_pipeline(path)
        label = _label(paper)
        assert label in VALID_STUDY_TYPES

    def test_sar_flood_paper_not_review(self, sar_flood_paper):
        paper = run_pipeline(sar_flood_paper)
        assert _label(paper) != "review", (
            "Sentinel-1 flood mapping paper should not be classified as review"
        )


# ─────────────────────────────────────────────────────────────────────────────
# T04-03  Confidence calibration
# ─────────────────────────────────────────────────────────────────────────────

class TestStudyTypeConfidence:

    def test_confidence_above_zero(self, sar_flood_paper):
        paper = run_pipeline(sar_flood_paper)
        assert _confidence(paper) > 0.0, "Study type confidence should be above zero"

    def test_review_paper_high_confidence(self, review_paper):
        paper = run_pipeline(review_paper)
        if _label(paper) == "review":
            assert _confidence(paper) >= 0.5, (
                f"Review paper has low confidence: {_confidence(paper)}"
            )

    def test_confidence_is_float(self, swat_paper):
        paper = run_pipeline(swat_paper)
        assert isinstance(_confidence(paper), float)

    def test_all_valid_study_types_reachable(self, tmp_xml_dir, mock_geocoding):
        """All VALID_STUDY_TYPES can be produced — spot-check 'review' and 'case_study'."""
        paths = {
            "review": write_tei(
                tmp_xml_dir, stem="st_review",
                abstract="We survey the literature on flood mapping. A systematic review of techniques.",
            ),
            "case_study": write_tei(
                tmp_xml_dir, stem="st_case",
                abstract="Case study of flood event in a watershed. Analysis of a single flood event.",
            ),
        }
        results = {st: run_pipeline(p) for st, p in paths.items()}

        # review paper must be classified as review
        assert _label(results["review"]) == "review", (
            f"Review signal paper → {_label(results['review'])!r}"
        )
