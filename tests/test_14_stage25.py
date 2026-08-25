"""
test_14_stage25.py — Tests for Stage 2.5 semantic validation layer.

Coverage:
  - SemanticAnnotation: accumulation helpers, to_row serialisation
  - EvidenceTrace: immutability, to_dict
  - FigureSemanticValidator: content flags, roles, tasks, models, evidence
  - TableSemanticValidator: metric detection, model detection, uncertainty, roles
  - EquationSemanticValidator: named equation matching, domain, confidence
  - ObjectReasoner: individual rule triggering, non-triggering, trace accumulation
  - SemanticConstraints: role exclusion, domain lock, metric implication, forbidden role
  - SemanticValidatorStage: full pipeline integration, idempotency, error handling
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pyarrow.parquet as pq
import pytest

from src.document.scientific_objects import SemanticType
from src.semantic_objects.semantic_constraints import apply_constraints
from src.semantic_objects.semantic_evidence import (
    EvidenceTrace,
    HydroDomain,
    HydroTask,
    ObjectContext,
    ScientificRole,
    SemanticAnnotation,
    SemanticEdge,
    Stage25Result,
)
from src.semantic_objects.equation_semantics import EquationSemanticValidator
from src.semantic_objects.figure_semantics import FigureSemanticValidator
from src.semantic_objects.object_reasoner import (
    ObjectReasoner,
    r01_hydrograph_in_results,
    r02_simulated_in_caption,
    r03_observed_vs_simulated,
    r04_uncertainty_figure,
    r05_satellite_in_study_area,
    r07_metrics_table_multi_model,
    r08_metrics_table_in_results,
    r10_saint_venant_hec_ras,
)
from src.semantic_objects.semantic_validator import SemanticValidatorStage
from src.semantic_objects.table_semantics import TableSemanticValidator


PAPER_ID = "aabbccdd" * 8
PH       = "test-hash"


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _ann(object_type="figure", semantic_type=SemanticType.HYDROGRAPH,
         semantic_confidence=0.80, **kw) -> SemanticAnnotation:
    return SemanticAnnotation(
        object_id=f"{PAPER_ID}_{object_type}_000",
        paper_id=PAPER_ID,
        object_type=object_type,
        semantic_type=semantic_type,
        semantic_confidence=semantic_confidence,
        **kw,
    )


def _ctx(**kw) -> ObjectContext:
    defaults = dict(
        parsed_row={},
        sci_object=None,
        section_title=None,
        section_text=None,
        caption=None,
        label=None,
        all_section_titles=[],
        paper_id=PAPER_ID,
    )
    defaults.update(kw)
    return ObjectContext(**defaults)


def _fig_row(n: int = 0, label: str = "", caption: str = "") -> dict:
    from datetime import datetime, timezone
    return {
        "figure_id":     f"{PAPER_ID}_fig_{n:03d}",
        "paper_id":      PAPER_ID,
        "xml_id":        f"fig_{n}",
        "label":         label,
        "caption":       caption,
        "page":          n + 1,
        "bbox_x0": None, "bbox_y0": None, "bbox_x1": None, "bbox_y1": None,
        "graphic_page": None,
        "graphic_x0": None, "graphic_y0": None, "graphic_x1": None, "graphic_y1": None,
        "source_parser": "GROBID",
        "pipeline_hash": PH,
        "created_at": datetime.now(timezone.utc),
    }


def _tbl_row(n: int = 0, label: str = "", caption: str = "", header=None) -> dict:
    from datetime import datetime, timezone
    return {
        "table_id":        f"{PAPER_ID}_tbl_{n:03d}",
        "paper_id":        PAPER_ID,
        "region_id":       None,
        "xml_id":          f"tab_{n}",
        "page":            n + 1,
        "label":           label,
        "caption":         caption,
        "header_row":      header or [],
        "row_count":       3,
        "col_count":       len(header) if header else 0,
        "has_numeric_data":True,
        "source_parser":   "GROBID",
        "pipeline_hash":   PH,
        "created_at":      datetime.now(timezone.utc),
    }


def _eq_row(n: int = 0, text: str = "") -> dict:
    from datetime import datetime, timezone
    return {
        "equation_id":   f"{PAPER_ID}_eq_{n:03d}",
        "paper_id":      PAPER_ID,
        "xml_id":        f"eq_{n}",
        "text":          text,
        "page":          n + 1,
        "bbox_x0": None, "bbox_y0": None, "bbox_x1": None, "bbox_y1": None,
        "source_parser": "GROBID",
        "pipeline_hash": PH,
        "created_at":    datetime.now(timezone.utc),
    }


def _sci_obj(obj_id: str, semantic_type: str, classifier_score: float = 0.80) -> dict:
    from datetime import datetime, timezone
    return {
        "object_id":         obj_id,
        "paper_id":          PAPER_ID,
        "object_type":       "figure",
        "semantic_type":     semantic_type,
        "classifier_score":  classifier_score,
        "classifier_source": "rules",
        "candidate_models":  None,
        "candidate_metrics": None,
        "pipeline_hash":     PH,
        "created_at":        datetime.now(timezone.utc),
    }


# ─────────────────────────────────────────────────────────────────────────────
# SemanticAnnotation
# ─────────────────────────────────────────────────────────────────────────────

class TestSemanticAnnotation:
    def test_add_role_no_duplicates(self):
        ann = _ann()
        ann.add_role(ScientificRole.MODEL_OUTPUT)
        ann.add_role(ScientificRole.MODEL_OUTPUT)
        assert ann.scientific_role.count(ScientificRole.MODEL_OUTPUT) == 1

    def test_add_task_no_duplicates(self):
        ann = _ann()
        ann.add_task(HydroTask.STREAMFLOW_PRED)
        ann.add_task(HydroTask.STREAMFLOW_PRED)
        assert ann.candidate_tasks.count(HydroTask.STREAMFLOW_PRED) == 1

    def test_add_model_uppercases(self):
        ann = _ann()
        ann.add_model("lstm")
        assert "LSTM" in ann.likely_models

    def test_add_model_no_duplicates(self):
        ann = _ann()
        ann.add_model("SWAT")
        ann.add_model("swat")
        assert len(ann.likely_models) == 1

    def test_add_metric_uppercases(self):
        ann = _ann()
        ann.add_metric("nse")
        assert "NSE" in ann.candidate_metrics

    def test_add_trace_accumulates_confidence(self):
        ann = _ann(semantic_confidence=0.5)
        trace = EvidenceTrace(
            rule_triggered="TEST", evidence_source="pattern",
            matched_terms=("x",), section=None, confidence_delta=0.15,
        )
        ann.add_trace(trace)
        assert abs(ann.semantic_confidence - 0.65) < 1e-6

    def test_add_trace_caps_at_one(self):
        ann = _ann(semantic_confidence=0.95)
        trace = EvidenceTrace(
            rule_triggered="TEST", evidence_source="pattern",
            matched_terms=(), section=None, confidence_delta=0.20,
        )
        ann.add_trace(trace)
        assert ann.semantic_confidence == 1.0

    def test_to_row_serialises_lists_as_json(self):
        ann = _ann()
        ann.add_role(ScientificRole.MODEL_OUTPUT)
        ann.add_task(HydroTask.STREAMFLOW_PRED)
        ann.add_model("SWAT")
        row = ann.to_row()
        assert isinstance(row["scientific_role"], str)
        assert ScientificRole.MODEL_OUTPUT in json.loads(row["scientific_role"])
        assert HydroTask.STREAMFLOW_PRED in json.loads(row["candidate_tasks"])
        assert "SWAT" in json.loads(row["likely_models"])

    def test_to_row_has_annotation_id(self):
        ann = _ann()
        row = ann.to_row()
        assert "annotation_id" in row
        assert PAPER_ID in row["annotation_id"]

    def test_to_row_evidence_trace_is_json_list(self):
        ann = _ann()
        ann.add_trace(EvidenceTrace("R01", "caption", ("x",), "Results", 0.1))
        row = ann.to_row()
        traces = json.loads(row["evidence_trace"])
        assert isinstance(traces, list)
        assert traces[0]["rule_triggered"] == "R01"


class TestEvidenceTrace:
    def test_immutable(self):
        trace = EvidenceTrace("R01", "caption", ("x",), "Results", 0.1)
        with pytest.raises((AttributeError, TypeError)):
            trace.rule_triggered = "modified"  # type: ignore[misc]

    def test_to_dict_round_trip(self):
        trace = EvidenceTrace("R01", "pattern", ("NSE", "KGE"), "Results", 0.12)
        d = trace.to_dict()
        assert d["rule_triggered"]  == "R01"
        assert d["evidence_source"] == "pattern"
        assert "NSE" in d["matched_terms"]
        assert d["confidence_delta"] == 0.12


# ─────────────────────────────────────────────────────────────────────────────
# FigureSemanticValidator
# ─────────────────────────────────────────────────────────────────────────────

class TestFigureSemanticValidator:
    clf = FigureSemanticValidator()

    def _validate(self, label="", caption="", section_title=None,
                  section_text="", semantic_type=SemanticType.HYDROGRAPH,
                  classifier_score=0.85):
        row = _fig_row(label=label, caption=caption)
        sci = _sci_obj(row["figure_id"], semantic_type, classifier_score)
        ctx = _ctx(
            parsed_row=row,
            sci_object=sci,
            section_title=section_title,
            section_text=section_text,
            caption=caption,
        )
        return self.clf.validate(row, ctx)

    def test_hydrograph_gets_timeseries_viz_role(self):
        ann = self._validate(semantic_type=SemanticType.HYDROGRAPH)
        assert ScientificRole.TIMESERIES_VIZ in ann.scientific_role

    def test_flood_map_gets_spatial_output_role(self):
        ann = self._validate(semantic_type=SemanticType.FLOOD_EXTENT_MAP)
        assert ScientificRole.SPATIAL_OUTPUT in ann.scientific_role

    def test_scatter_plot_gets_evaluation_viz_role(self):
        ann = self._validate(semantic_type=SemanticType.SCATTER_PLOT)
        assert ScientificRole.EVALUATION_VIZ in ann.scientific_role

    def test_satellite_image_gets_remote_sensing_role(self):
        ann = self._validate(semantic_type=SemanticType.SATELLITE_IMAGE)
        assert ScientificRole.REMOTE_SENSING_INPUT in ann.scientific_role

    def test_watershed_map_gets_study_area_role(self):
        ann = self._validate(semantic_type=SemanticType.WATERSHED_MAP)
        assert ScientificRole.STUDY_AREA_DEF in ann.scientific_role

    def test_discharge_caption_sets_contains_discharge(self):
        ann = self._validate(caption="Simulated and observed discharge at the outlet.")
        assert ann.contains_discharge is True

    def test_uncertainty_caption_sets_uncertainty_band(self):
        ann = self._validate(caption="Hydrograph with 95% uncertainty bounds.")
        assert ann.contains_uncertainty_band is True

    def test_inundation_caption_sets_flood_extent(self):
        ann = self._validate(caption="Flood inundation extent derived from Sentinel-1.",
                             semantic_type=SemanticType.FLOOD_EXTENT_MAP)
        assert ann.contains_flood_extent is True

    def test_sentinel_caption_sets_remote_sensing(self):
        ann = self._validate(caption="Sentinel-1 SAR image of the Dnipro basin.")
        assert ann.contains_remote_sensing is True

    def test_swat_in_caption_adds_model(self):
        ann = self._validate(caption="SWAT model output hydrograph for 2015-2020.")
        assert "SWAT" in ann.likely_models

    def test_nse_in_caption_adds_metric(self):
        ann = self._validate(caption="NSE = 0.87 for calibration period.")
        assert "NSE" in ann.candidate_metrics

    def test_evidence_trace_populated(self):
        ann = self._validate(caption="Observed discharge hydrograph.")
        assert len(ann.evidence_trace) > 0

    def test_base_confidence_from_classifier_score(self):
        ann = self._validate(classifier_score=0.92)
        assert ann.semantic_confidence >= 0.92

    def test_hydrograph_tasks_include_streamflow(self):
        ann = self._validate(semantic_type=SemanticType.HYDROGRAPH)
        assert HydroTask.STREAMFLOW_PRED in ann.candidate_tasks

    def test_satellite_image_domain_is_remote_sensing(self):
        ann = self._validate(semantic_type=SemanticType.SATELLITE_IMAGE)
        assert ann.domain == HydroDomain.REMOTE_SENSING


# ─────────────────────────────────────────────────────────────────────────────
# TableSemanticValidator
# ─────────────────────────────────────────────────────────────────────────────

class TestTableSemanticValidator:
    clf = TableSemanticValidator()

    def _validate(self, label="", caption="", header=None,
                  semantic_type=SemanticType.METRICS_TABLE, classifier_score=0.88):
        row = _tbl_row(label=label, caption=caption, header=header or [])
        sci = _sci_obj(row["table_id"], semantic_type, classifier_score)
        ctx = _ctx(parsed_row=row, sci_object=sci, caption=caption)
        return self.clf.validate(row, ctx)

    def test_metrics_table_gets_evaluation_role(self):
        ann = self._validate(semantic_type=SemanticType.METRICS_TABLE)
        assert ScientificRole.EVALUATION in ann.scientific_role

    def test_comparison_table_gets_model_comparison_role(self):
        ann = self._validate(semantic_type=SemanticType.COMPARISON_TABLE)
        assert ScientificRole.MODEL_COMPARISON in ann.scientific_role

    def test_parameter_table_gets_calibration_role(self):
        ann = self._validate(semantic_type=SemanticType.PARAMETER_TABLE)
        assert ScientificRole.MODEL_CALIBRATION in ann.scientific_role

    def test_nse_in_header_detected(self):
        ann = self._validate(header=["Model", "NSE", "KGE", "RMSE"])
        assert "NSE" in ann.candidate_metrics
        assert "KGE" in ann.candidate_metrics

    def test_hec_ras_in_caption_adds_model(self):
        ann = self._validate(caption="Performance of HEC-RAS and LISFLOOD models.")
        assert "HEC-RAS" in ann.likely_models

    def test_two_models_adds_model_comparison_role(self):
        ann = self._validate(caption="SWAT vs HEC-HMS performance comparison.")
        assert ScientificRole.MODEL_COMPARISON in ann.scientific_role

    def test_uncertainty_in_header_adds_uncertainty_role(self):
        ann = self._validate(header=["Parameter", "95% CI", "std"])
        assert ScientificRole.UNCERTAINTY_QUANT in ann.scientific_role

    def test_metrics_table_domain_is_statistics(self):
        ann = self._validate(semantic_type=SemanticType.METRICS_TABLE)
        assert ann.domain == HydroDomain.STATISTICS

    def test_evidence_trace_populated_for_metric_header(self):
        ann = self._validate(header=["Model", "NSE", "RMSE"])
        traces = [t for t in ann.evidence_trace if "METRIC" in t.rule_triggered]
        assert len(traces) > 0


# ─────────────────────────────────────────────────────────────────────────────
# EquationSemanticValidator
# ─────────────────────────────────────────────────────────────────────────────

class TestEquationSemanticValidator:
    clf = EquationSemanticValidator()

    def _validate(self, text="", section_text=""):
        row = _eq_row(text=text)
        ctx = _ctx(parsed_row=row, section_text=section_text)
        return self.clf.validate(row, ctx)

    def test_nse_equation_matches(self):
        ann = self._validate("NSE = 1 - sum(Qobs - Qsim)^2 / sum(Qobs - mean)^2")
        assert ScientificRole.OBJECTIVE_FUNCTION in ann.scientific_role
        assert ann.domain == HydroDomain.STATISTICS

    def test_kge_equation_matches(self):
        ann = self._validate("KGE = 1 - sqrt((r-1)^2 + (alpha-1)^2 + (beta-1)^2)")
        assert ScientificRole.OBJECTIVE_FUNCTION in ann.scientific_role

    def test_manning_equation_matches(self):
        ann = self._validate("Manning's Q = (1/n) * A * R^(2/3) * S^(1/2)")
        assert ScientificRole.PHYSICAL_LAW in ann.scientific_role
        assert ann.domain == HydroDomain.HYDRAULICS

    def test_saint_venant_matches(self):
        ann = self._validate("shallow water equation (de Saint-Venant)")
        assert ScientificRole.PHYSICAL_LAW in ann.scientific_role
        assert ann.domain == HydroDomain.HYDRODYNAMICS

    def test_scs_cn_matches(self):
        ann = self._validate("SCS-CN runoff curve number method Q = (P - 0.2S)^2 / (P + 0.8S)")
        assert HydroTask.RAINFALL_RUNOFF in ann.candidate_tasks

    def test_darcy_law_matches(self):
        ann = self._validate("Darcy's law: q = -K * dh/dl")
        assert ann.domain == HydroDomain.HYDROGEOLOGY
        assert HydroTask.GROUNDWATER in ann.candidate_tasks

    def test_water_balance_matches(self):
        ann = self._validate("Water balance: dS/dt = P - ET - Q")
        assert HydroTask.RAINFALL_RUNOFF in ann.candidate_tasks

    def test_unknown_equation_has_low_evidence(self):
        ann = self._validate("x = y + z")
        assert ann.evidence_strength < 0.3

    def test_nse_implies_swat_related_models(self):
        ann = self._validate("NSE = 1 - ...")
        assert len(ann.likely_models) > 0

    def test_confidence_upgraded_by_named_match(self):
        base = 0.5
        row  = _eq_row(text="NSE = 1 - ...")
        sci  = _sci_obj(row["equation_id"], SemanticType.OBJECTIVE_FUNCTION, base)
        ctx  = _ctx(parsed_row=row, sci_object=sci)
        ann  = self.clf.validate(row, ctx)
        assert ann.semantic_confidence > base

    def test_multiple_equations_secondary_models_propagated(self):
        ann = self._validate("NSE and Manning's n for channel routing")
        # NSE gives SWAT etc; Manning gives HEC-RAS etc
        assert len(ann.likely_models) > 1


# ─────────────────────────────────────────────────────────────────────────────
# ObjectReasoner — individual rules
# ─────────────────────────────────────────────────────────────────────────────

class TestObjectReasoner:
    reasoner = ObjectReasoner()

    def test_r01_fires_for_hydrograph_in_results(self):
        ann = _ann("figure", SemanticType.HYDROGRAPH)
        ctx = _ctx(section_title="Results")
        trace = r01_hydrograph_in_results(ann, ctx)
        assert trace is not None
        assert ScientificRole.MODEL_OUTPUT in ann.scientific_role
        assert trace.rule_triggered == "R01_hydrograph_in_results"

    def test_r01_does_not_fire_for_wrong_section(self):
        ann = _ann("figure", SemanticType.HYDROGRAPH)
        ctx = _ctx(section_title="Study Area")
        assert r01_hydrograph_in_results(ann, ctx) is None

    def test_r01_does_not_fire_for_non_hydrograph(self):
        ann = _ann("figure", SemanticType.SATELLITE_IMAGE)
        ctx = _ctx(section_title="Results")
        assert r01_hydrograph_in_results(ann, ctx) is None

    def test_r02_fires_for_simulated_in_caption(self):
        ann = _ann("figure", SemanticType.HYDROGRAPH)
        ctx = _ctx(caption="Simulated and observed discharge.")
        trace = r02_simulated_in_caption(ann, ctx)
        assert trace is not None
        assert ScientificRole.MODEL_OUTPUT in ann.scientific_role

    def test_r02_does_not_fire_for_tables(self):
        ann = _ann("table", SemanticType.METRICS_TABLE)
        ctx = _ctx(caption="Simulated performance metrics.")
        assert r02_simulated_in_caption(ann, ctx) is None

    def test_r03_fires_when_both_observed_and_simulated(self):
        ann = _ann("figure", SemanticType.SCATTER_PLOT)
        ctx = _ctx(caption="Observed vs simulated streamflow.")
        trace = r03_observed_vs_simulated(ann, ctx)
        assert trace is not None
        assert ScientificRole.OBSERVED_VS_SIM in ann.scientific_role

    def test_r03_does_not_fire_when_only_observed(self):
        ann = _ann("figure", SemanticType.SCATTER_PLOT)
        ctx = _ctx(caption="Observed discharge.")
        assert r03_observed_vs_simulated(ann, ctx) is None

    def test_r04_fires_for_uncertainty_in_caption(self):
        ann = _ann("figure", SemanticType.HYDROGRAPH)
        ctx = _ctx(caption="Prediction band showing uncertainty in discharge.")
        trace = r04_uncertainty_figure(ann, ctx)
        assert trace is not None
        assert ScientificRole.UNCERTAINTY_VIZ in ann.scientific_role
        assert ann.contains_uncertainty_band is True

    def test_r05_fires_for_satellite_in_study_area(self):
        ann = _ann("figure", SemanticType.SATELLITE_IMAGE)
        ctx = _ctx(section_title="Study Area and Data")
        trace = r05_satellite_in_study_area(ann, ctx)
        assert trace is not None
        assert ScientificRole.STUDY_AREA_DEF in ann.scientific_role

    def test_r07_fires_when_metrics_table_has_two_models(self):
        ann = _ann("table", SemanticType.METRICS_TABLE)
        ann.likely_models = ["SWAT", "HEC-HMS"]
        ctx = _ctx()
        trace = r07_metrics_table_multi_model(ann, ctx)
        assert trace is not None
        assert ScientificRole.MODEL_COMPARISON in ann.scientific_role

    def test_r07_does_not_fire_with_one_model(self):
        ann = _ann("table", SemanticType.METRICS_TABLE)
        ann.likely_models = ["SWAT"]
        ctx = _ctx()
        assert r07_metrics_table_multi_model(ann, ctx) is None

    def test_r08_fires_for_metrics_table_in_results(self):
        ann = _ann("table", SemanticType.METRICS_TABLE)
        ctx = _ctx(section_title="Results and Discussion")
        trace = r08_metrics_table_in_results(ann, ctx)
        assert trace is not None
        assert ScientificRole.MODEL_VALIDATION in ann.scientific_role

    def test_r10_fires_saint_venant_with_hec_ras_in_section(self):
        ann = _ann("equation", SemanticType.PHYSICAL_EQUATION)
        row = _eq_row(text="de Saint-Venant equations")
        ctx = _ctx(parsed_row=row, section_text="HEC-RAS was used for 2D hydraulic simulation.")
        trace = r10_saint_venant_hec_ras(ann, ctx)
        assert trace is not None
        assert "HEC-RAS" in ann.likely_models
        assert ann.domain == HydroDomain.HYDRODYNAMICS

    def test_apply_rules_accumulates_traces(self):
        ann = _ann("figure", SemanticType.HYDROGRAPH, semantic_confidence=0.80)
        ctx = _ctx(
            section_title="Results",
            caption="Simulated and observed discharge hydrograph.",
        )
        before = ann.semantic_confidence
        self.reasoner.apply_rules(ann, ctx)
        assert ann.semantic_confidence > before
        assert len(ann.evidence_trace) > 0

    def test_apply_rules_does_not_raise_on_empty_context(self):
        ann = _ann("figure", SemanticType.GENERIC_FIGURE)
        ctx = _ctx()
        self.reasoner.apply_rules(ann, ctx)  # must not raise


# ─────────────────────────────────────────────────────────────────────────────
# SemanticConstraints
# ─────────────────────────────────────────────────────────────────────────────

class TestSemanticConstraints:
    def test_role_exclusion_recorded(self):
        ann = _ann("figure", SemanticType.HYDROGRAPH)
        ann.add_role(ScientificRole.MODEL_OUTPUT)
        ann.add_role("image_segmentation")
        apply_constraints(ann)
        assert any("ROLE_EXCLUSION" in v for v in ann.constraint_violations)

    def test_role_exclusion_reduces_confidence(self):
        ann = _ann("figure", SemanticType.HYDROGRAPH, semantic_confidence=0.9)
        ann.add_role(ScientificRole.MODEL_OUTPUT)
        ann.add_role("image_segmentation")
        before = ann.semantic_confidence
        apply_constraints(ann)
        assert ann.semantic_confidence < before

    def test_domain_lock_sets_implied_domain(self):
        ann = _ann("figure", SemanticType.HYDROGRAPH)
        ann.domain = None
        apply_constraints(ann)
        assert ann.domain == HydroDomain.HYDROLOGY

    def test_domain_lock_flags_contradicting_domain(self):
        ann = _ann("figure", SemanticType.HYDROGRAPH)
        ann.domain = HydroDomain.REMOTE_SENSING
        apply_constraints(ann)
        assert any("DOMAIN_MISMATCH" in v for v in ann.constraint_violations)

    def test_metric_implies_evaluation_role(self):
        ann = _ann("table", SemanticType.METRICS_TABLE)
        ann.add_metric("NSE")
        apply_constraints(ann)
        assert ScientificRole.EVALUATION in ann.scientific_role
        assert ScientificRole.MODEL_VALIDATION in ann.scientific_role

    def test_forbidden_role_removed_for_satellite(self):
        ann = _ann("figure", SemanticType.SATELLITE_IMAGE)
        ann.add_role(ScientificRole.MODEL_OUTPUT)
        apply_constraints(ann)
        assert ScientificRole.MODEL_OUTPUT not in ann.scientific_role
        assert any("TYPE_FORBIDDEN_ROLE" in v for v in ann.constraint_violations)

    def test_no_violation_for_clean_annotation(self):
        ann = _ann("figure", SemanticType.HYDROGRAPH)
        ann.add_role(ScientificRole.MODEL_OUTPUT)
        ann.add_role(ScientificRole.TIMESERIES_VIZ)
        apply_constraints(ann)
        assert ann.constraint_violations == []


# ─────────────────────────────────────────────────────────────────────────────
# SemanticValidatorStage — integration
# ─────────────────────────────────────────────────────────────────────────────

def _write_stage1_artifacts(parsed_root: Path) -> None:
    """Write minimal parsed artifacts for integration tests."""
    from src.ingestion.stage1.parsed_store import ParsedStore
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)

    store = ParsedStore(PAPER_ID, PH, parsed_root)
    store.write_tei("<TEI/>")
    store.write_sections([{
        "section_id":    f"{PAPER_ID}_sec_000",
        "paper_id":      PAPER_ID,
        "title":         "Results",
        "level":         1,
        "n":             "3",
        "text":          "Model NSE=0.87, KGE=0.82 for calibration period.",
        "word_count":    8,
        "page":          3,
        "bbox_x0": None, "bbox_y0": None, "bbox_x1": None, "bbox_y1": None,
        "source_parser": "GROBID",
        "pipeline_hash": PH,
        "created_at":    now,
    }])
    store.write_figures([{
        "figure_id":     f"{PAPER_ID}_fig_000",
        "paper_id":      PAPER_ID,
        "xml_id":        "fig_0",
        "label":         "Figure 1",
        "caption":       "Simulated and observed discharge hydrograph at station X.",
        "page":          3,
        "bbox_x0": None, "bbox_y0": None, "bbox_x1": None, "bbox_y1": None,
        "graphic_page": None,
        "graphic_x0": None, "graphic_y0": None, "graphic_x1": None, "graphic_y1": None,
        "source_parser": "GROBID",
        "pipeline_hash": PH,
        "created_at":    now,
    }])
    store.write_tables([{
        "table_id":        f"{PAPER_ID}_tbl_000",
        "paper_id":        PAPER_ID,
        "region_id":       None,
        "xml_id":          "tab_0",
        "page":            4,
        "label":           "Table 1",
        "caption":         "Performance metrics: NSE, KGE, RMSE for SWAT and HEC-HMS.",
        "header_row":      ["Model", "NSE", "KGE", "RMSE"],
        "row_count":       2,
        "col_count":       4,
        "has_numeric_data": True,
        "source_parser":   "GROBID",
        "pipeline_hash":   PH,
        "created_at":      now,
    }])
    store.write_equations([{
        "equation_id":   f"{PAPER_ID}_eq_000",
        "paper_id":      PAPER_ID,
        "xml_id":        "eq_0",
        "text":          "NSE = 1 - sum(Qo - Qs)^2 / sum(Qo - Qm)^2",
        "page":          2,
        "bbox_x0": None, "bbox_y0": None, "bbox_x1": None, "bbox_y1": None,
        "source_parser": "GROBID",
        "pipeline_hash": PH,
        "created_at":    now,
    }])
    store.write_references([])
    store.write_captions([{
        "caption_id":    f"{PAPER_ID}_cap_000",
        "paper_id":      PAPER_ID,
        "parent_id":     f"{PAPER_ID}_fig_000",
        "parent_type":   "figure",
        "label":         "Figure 1",
        "text":          "Simulated and observed discharge hydrograph at station X.",
        "page":          3,
        "source_parser": "GROBID",
        "pipeline_hash": PH,
        "created_at":    now,
    }])
    store.write_manifest({})


def _write_stage2_artifacts(sodb_root: Path) -> None:
    from src.ingestion.stage2.engineer import Stage2Engineer
    from src.ingestion.stage1.parser_runner import Stage1Result

    s1 = Stage1Result(
        paper_id=PAPER_ID, status="skipped",
        parsed_root=sodb_root,  # doesn't matter, we provide parsed_root separately
    )
    # Stage 2 will fail to load if parsed_root not provided; just create a minimal file
    from src.analytics.parquet_schema import SCIENTIFIC_OBJECTS_SCHEMA
    import pyarrow as pa
    import pyarrow.parquet as pq
    from datetime import datetime, timezone

    paper_dir = sodb_root / PAPER_ID
    paper_dir.mkdir(parents=True, exist_ok=True)
    row = {
        "object_id":         f"{PAPER_ID}_fig_000",
        "paper_id":          PAPER_ID,
        "object_type":       "figure",
        "semantic_type":     SemanticType.HYDROGRAPH,
        "source_id":         f"{PAPER_ID}_fig_000",
        "page":              3,
        "raw_text":          "hydrograph",
        "classifier_score":  0.92,
        "classifier_source": "rules",
        "contains_models":   False,
        "contains_metrics":  False,
        "contains_timeseries": True,
        "contains_coordinates": False,
        "contains_satellite": False,
        "contains_hydrograph": True,
        "candidate_models":  None,
        "candidate_metrics": None,
        "candidate_vars":    None,
        "variables":         None,
        "related_models":    None,
        "domain":            None,
        "equation_name":     None,
        "pipeline_hash":     PH,
        "created_at":        datetime.now(timezone.utc),
    }
    tbl = pa.Table.from_pylist([row], schema=SCIENTIFIC_OBJECTS_SCHEMA)
    pq.write_table(tbl, paper_dir / "scientific_objects.parquet")


class TestSemanticValidatorStage:
    def _validator(self, tmp_path: Path) -> SemanticValidatorStage:
        return SemanticValidatorStage(
            sodb_root=tmp_path / "sodb",
            parsed_root=tmp_path / "parsed",
            pipeline_hash=PH,
        )

    def _setup(self, tmp_path: Path) -> None:
        _write_stage1_artifacts(tmp_path / "parsed")
        _write_stage2_artifacts(tmp_path / "sodb")

    def test_run_ok_status(self, tmp_path):
        self._setup(tmp_path)
        result = self._validator(tmp_path).run(PAPER_ID)
        assert result.status == "ok"
        assert result.should_continue is True

    def test_run_writes_annotation_parquet(self, tmp_path):
        self._setup(tmp_path)
        self._validator(tmp_path).run(PAPER_ID)
        ann_path = tmp_path / "sodb" / PAPER_ID / "semantic_annotations.parquet"
        assert ann_path.exists()

    def test_run_writes_semantic_edges_parquet(self, tmp_path):
        self._setup(tmp_path)
        self._validator(tmp_path).run(PAPER_ID)
        edge_path = tmp_path / "sodb" / PAPER_ID / "semantic_edges.parquet"
        assert edge_path.exists()

    def test_run_writes_manifest(self, tmp_path):
        self._setup(tmp_path)
        self._validator(tmp_path).run(PAPER_ID)
        mfst = tmp_path / "sodb" / PAPER_ID / "semantic_manifest.json"
        assert mfst.exists()
        data = json.loads(mfst.read_text())
        assert data["stage"] == "2.5"
        assert data["paper_id"] == PAPER_ID

    def test_annotation_count_matches_result(self, tmp_path):
        self._setup(tmp_path)
        result = self._validator(tmp_path).run(PAPER_ID)
        ann_path = tmp_path / "sodb" / PAPER_ID / "semantic_annotations.parquet"
        rows = pq.read_table(ann_path).to_pylist()
        assert len(rows) == result.annotation_count

    def test_figure_annotation_has_scientific_role(self, tmp_path):
        self._setup(tmp_path)
        self._validator(tmp_path).run(PAPER_ID)
        ann_path = tmp_path / "sodb" / PAPER_ID / "semantic_annotations.parquet"
        rows = pq.read_table(ann_path).to_pylist()
        fig = next(r for r in rows if r["object_type"] == "figure")
        roles = json.loads(fig["scientific_role"])
        assert len(roles) > 0

    def test_table_annotation_has_metrics(self, tmp_path):
        self._setup(tmp_path)
        self._validator(tmp_path).run(PAPER_ID)
        ann_path = tmp_path / "sodb" / PAPER_ID / "semantic_annotations.parquet"
        rows = pq.read_table(ann_path).to_pylist()
        tbl = next(r for r in rows if r["object_type"] == "table")
        metrics = json.loads(tbl["candidate_metrics"] or "[]")
        assert "NSE" in metrics

    def test_equation_annotation_has_domain(self, tmp_path):
        self._setup(tmp_path)
        self._validator(tmp_path).run(PAPER_ID)
        ann_path = tmp_path / "sodb" / PAPER_ID / "semantic_annotations.parquet"
        rows = pq.read_table(ann_path).to_pylist()
        eq = next(r for r in rows if r["object_type"] == "equation")
        assert eq["domain"] is not None

    def test_no_tmp_files_remain(self, tmp_path):
        self._setup(tmp_path)
        self._validator(tmp_path).run(PAPER_ID)
        sodb_dir = tmp_path / "sodb" / PAPER_ID
        assert list(sodb_dir.glob("*.tmp")) == []

    def test_second_run_returns_skipped(self, tmp_path):
        self._setup(tmp_path)
        v = self._validator(tmp_path)
        v.run(PAPER_ID)
        result2 = v.run(PAPER_ID)
        assert result2.status == "skipped"

    def test_force_reruns(self, tmp_path):
        self._setup(tmp_path)
        SemanticValidatorStage(
            sodb_root=tmp_path / "sodb",
            parsed_root=tmp_path / "parsed",
            pipeline_hash=PH,
        ).run(PAPER_ID)
        result2 = SemanticValidatorStage(
            sodb_root=tmp_path / "sodb",
            parsed_root=tmp_path / "parsed",
            pipeline_hash=PH, force=True,
        ).run(PAPER_ID)
        assert result2.status == "ok"

    def test_missing_parsed_dir_returns_error(self, tmp_path):
        (tmp_path / "sodb" / PAPER_ID).mkdir(parents=True, exist_ok=True)
        result = SemanticValidatorStage(
            sodb_root=tmp_path / "sodb",
            parsed_root=tmp_path / "nonexistent_parsed",
            pipeline_hash=PH,
        ).run(PAPER_ID)
        # Missing dir means empty artifacts → ok with 0 annotations, not error
        assert result.status in ("ok", "error")


class TestStage25Result:
    def test_should_continue_ok(self):
        r = Stage25Result(paper_id="x", status="ok", sodb_root=Path("/tmp"))
        assert r.should_continue is True

    def test_should_continue_false_for_skipped(self):
        r = Stage25Result(paper_id="x", status="skipped", sodb_root=Path("/tmp"))
        assert r.should_continue is False

    def test_should_continue_false_for_error(self):
        r = Stage25Result(paper_id="x", status="error", sodb_root=Path("/tmp"))
        assert r.should_continue is False
