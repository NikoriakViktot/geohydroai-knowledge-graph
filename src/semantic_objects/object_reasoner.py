"""
object_reasoner.py — Cross-object semantic reasoning engine for Stage 2.5.

Each rule is a module-level function with signature:
    (ann: SemanticAnnotation, ctx: ObjectContext) -> EvidenceTrace | None

  - Returns None if the condition is not met (rule does not fire).
  - Returns an EvidenceTrace if it fires; the function also mutates ann in-place
    (adding roles, tasks, models) before returning the trace.

All rules are collected in _ALL_RULES and executed by ObjectReasoner.apply_rules().

Confidence propagation:
  Each EvidenceTrace carries a confidence_delta that is accumulated in
  SemanticAnnotation.semantic_confidence (capped at 1.0).
  Negative deltas (from constraints) are applied separately in
  semantic_constraints.py.
"""
from __future__ import annotations

import re
from typing import Any, Callable

from src.document.scientific_objects import SemanticType
from src.semantic_objects.semantic_evidence import (
    EvidenceTrace,
    HydroDomain,
    HydroTask,
    ObjectContext,
    ScientificRole,
    SemanticAnnotation,
)

RuleFn = Callable[[SemanticAnnotation, ObjectContext], EvidenceTrace | None]

_ALL_RULES: list[RuleFn] = []


def _rule(fn: RuleFn) -> RuleFn:
    _ALL_RULES.append(fn)
    return fn


# ─────────────────────────────────────────────────────────────────────────────
# Figure rules
# ─────────────────────────────────────────────────────────────────────────────

@_rule
def r01_hydrograph_in_results(ann: SemanticAnnotation, ctx: ObjectContext) -> EvidenceTrace | None:
    """Hydrograph placed in a Results section → model_output role."""
    if ann.object_type != "figure" or ann.semantic_type != SemanticType.HYDROGRAPH:
        return None
    section = ctx.section_title or ""
    if not re.search(r"\bresult", section, re.I):
        return None
    ann.add_role(ScientificRole.MODEL_OUTPUT)
    return EvidenceTrace(
        rule_triggered="R01_hydrograph_in_results",
        evidence_source="section_context",
        matched_terms=(ann.semantic_type, section),
        section=section,
        confidence_delta=0.10,
    )


@_rule
def r02_simulated_in_caption(ann: SemanticAnnotation, ctx: ObjectContext) -> EvidenceTrace | None:
    """'Simulated' in caption → model_output role for figures."""
    if ann.object_type != "figure":
        return None
    caption = ctx.caption or ctx.parsed_row.get("caption") or ""
    if not re.search(r"\bsimulated\b", caption, re.I):
        return None
    ann.add_role(ScientificRole.MODEL_OUTPUT)
    return EvidenceTrace(
        rule_triggered="R02_simulated_in_caption",
        evidence_source="caption",
        matched_terms=("simulated",),
        section=ctx.section_title,
        confidence_delta=0.12,
    )


@_rule
def r03_observed_vs_simulated(ann: SemanticAnnotation, ctx: ObjectContext) -> EvidenceTrace | None:
    """Caption with 'observed' and 'simulated' → observed_vs_simulated role."""
    if ann.object_type not in ("figure", "table"):
        return None
    text = (ctx.caption or "") + " " + (ctx.parsed_row.get("caption") or "")
    if not (re.search(r"\bobserved\b", text, re.I) and re.search(r"\bsimulated\b", text, re.I)):
        return None
    ann.add_role(ScientificRole.OBSERVED_VS_SIM)
    ann.add_role(ScientificRole.MODEL_VALIDATION)
    return EvidenceTrace(
        rule_triggered="R03_observed_vs_simulated",
        evidence_source="caption",
        matched_terms=("observed", "simulated"),
        section=ctx.section_title,
        confidence_delta=0.12,
    )


@_rule
def r04_uncertainty_figure(ann: SemanticAnnotation, ctx: ObjectContext) -> EvidenceTrace | None:
    """Caption or label mentions uncertainty → uncertainty_visualization role."""
    if ann.object_type != "figure":
        return None
    text = (ctx.caption or "") + " " + (ctx.parsed_row.get("label") or "")
    m = re.search(r"\b(uncertainty|confidence.interval|prediction.band|ensemble.spread)\b", text, re.I)
    if not m:
        return None
    ann.add_role(ScientificRole.UNCERTAINTY_VIZ)
    ann.contains_uncertainty_band = True
    return EvidenceTrace(
        rule_triggered="R04_uncertainty_figure",
        evidence_source="caption",
        matched_terms=(m.group(0),),
        section=ctx.section_title,
        confidence_delta=0.08,
    )


@_rule
def r05_satellite_in_study_area(ann: SemanticAnnotation, ctx: ObjectContext) -> EvidenceTrace | None:
    """Satellite image in Study Area section → remote_sensing_input + study_area_def."""
    if ann.object_type != "figure" or ann.semantic_type != SemanticType.SATELLITE_IMAGE:
        return None
    section = ctx.section_title or ""
    if not re.search(r"\b(study.area|case.study|site|watershed)\b", section, re.I):
        return None
    ann.add_role(ScientificRole.REMOTE_SENSING_INPUT)
    ann.add_role(ScientificRole.STUDY_AREA_DEF)
    return EvidenceTrace(
        rule_triggered="R05_satellite_in_study_area",
        evidence_source="section_context",
        matched_terms=(ann.semantic_type, section),
        section=section,
        confidence_delta=0.10,
    )


@_rule
def r06_scatter_with_r2(ann: SemanticAnnotation, ctx: ObjectContext) -> EvidenceTrace | None:
    """Scatter plot with R² or correlation in caption → evaluation_visualization."""
    if ann.object_type != "figure" or ann.semantic_type != SemanticType.SCATTER_PLOT:
        return None
    text = (ctx.caption or "") + " " + (ctx.parsed_row.get("caption") or "")
    m = re.search(r"\b(r[²2]|r\s*=|correlation|pearson)\b", text, re.I)
    if not m:
        return None
    ann.add_role(ScientificRole.EVALUATION_VIZ)
    ann.add_metric("R²")
    return EvidenceTrace(
        rule_triggered="R06_scatter_with_r2",
        evidence_source="caption",
        matched_terms=(m.group(0),),
        section=ctx.section_title,
        confidence_delta=0.08,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Table rules
# ─────────────────────────────────────────────────────────────────────────────

@_rule
def r07_metrics_table_multi_model(ann: SemanticAnnotation, ctx: ObjectContext) -> EvidenceTrace | None:
    """Metrics table with ≥2 models → model_comparison role."""
    if ann.object_type != "table":
        return None
    if ann.semantic_type not in {SemanticType.METRICS_TABLE, SemanticType.COMPARISON_TABLE}:
        return None
    if len(ann.likely_models) < 2:
        return None
    ann.add_role(ScientificRole.MODEL_COMPARISON)
    return EvidenceTrace(
        rule_triggered="R07_metrics_table_multi_model",
        evidence_source="cross_object",
        matched_terms=tuple(ann.likely_models[:4]),
        section=ctx.section_title,
        confidence_delta=0.12,
    )


@_rule
def r08_metrics_table_in_results(ann: SemanticAnnotation, ctx: ObjectContext) -> EvidenceTrace | None:
    """Metrics table in Results section → validation role."""
    if ann.object_type != "table" or ann.semantic_type != SemanticType.METRICS_TABLE:
        return None
    section = ctx.section_title or ""
    if not re.search(r"\bresult", section, re.I):
        return None
    ann.add_role(ScientificRole.MODEL_VALIDATION)
    return EvidenceTrace(
        rule_triggered="R08_metrics_table_in_results",
        evidence_source="section_context",
        matched_terms=(section,),
        section=section,
        confidence_delta=0.08,
    )


@_rule
def r09_param_table_in_methods(ann: SemanticAnnotation, ctx: ObjectContext) -> EvidenceTrace | None:
    """Parameter table in Methods section → model_calibration role."""
    if ann.object_type != "table" or ann.semantic_type != SemanticType.PARAMETER_TABLE:
        return None
    section = ctx.section_title or ""
    if not re.search(r"\b(method|calibrat|model.setup)\b", section, re.I):
        return None
    ann.add_role(ScientificRole.MODEL_CALIBRATION)
    return EvidenceTrace(
        rule_triggered="R09_param_table_in_methods",
        evidence_source="section_context",
        matched_terms=(section,),
        section=section,
        confidence_delta=0.08,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Equation rules
# ─────────────────────────────────────────────────────────────────────────────

@_rule
def r10_saint_venant_hec_ras(ann: SemanticAnnotation, ctx: ObjectContext) -> EvidenceTrace | None:
    """Saint-Venant equation + HEC-RAS mentioned in section → hydrodynamics."""
    if ann.object_type != "equation":
        return None
    sec_text = ctx.section_text or ""
    if not re.search(r"\bsaint.venant\b", (ctx.parsed_row.get("text") or ""), re.I):
        return None
    if not re.search(r"\bhec.?ras\b", sec_text, re.I):
        return None
    ann.add_model("HEC-RAS")
    ann.domain = HydroDomain.HYDRODYNAMICS
    return EvidenceTrace(
        rule_triggered="R10_saint_venant_hec_ras",
        evidence_source="cross_object",
        matched_terms=("Saint-Venant", "HEC-RAS"),
        section=ctx.section_title,
        confidence_delta=0.15,
    )


@_rule
def r11_scs_cn_rainfall_runoff(ann: SemanticAnnotation, ctx: ObjectContext) -> EvidenceTrace | None:
    """SCS-CN equation → rainfall_runoff_modeling task."""
    if ann.object_type != "equation":
        return None
    text = ctx.parsed_row.get("text") or ""
    if not re.search(r"\b(scs.?cn|curve.number)\b", text, re.I):
        return None
    ann.add_task(HydroTask.RAINFALL_RUNOFF)
    ann.add_task(HydroTask.FLOOD_FORECASTING)
    return EvidenceTrace(
        rule_triggered="R11_scs_cn_rainfall_runoff",
        evidence_source="pattern",
        matched_terms=("SCS-CN",),
        section=ctx.section_title,
        confidence_delta=0.10,
    )


@_rule
def r12_manning_hydraulic_modeling(ann: SemanticAnnotation, ctx: ObjectContext) -> EvidenceTrace | None:
    """Manning equation in section mentioning river/channel → hydraulic_modeling."""
    if ann.object_type != "equation":
        return None
    text = ctx.parsed_row.get("text") or ""
    if not re.search(r"\bmanning", text, re.I):
        return None
    sec = ctx.section_text or ""
    if not re.search(r"\b(channel|river|flood.plain|floodplain)\b", sec, re.I):
        return None
    ann.add_task(HydroTask.HYDRAULIC_MODELING)
    ann.add_role(ScientificRole.PHYSICAL_LAW)
    return EvidenceTrace(
        rule_triggered="R12_manning_hydraulic",
        evidence_source="cross_object",
        matched_terms=("Manning", "channel"),
        section=ctx.section_title,
        confidence_delta=0.10,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Section-context propagation rules
# ─────────────────────────────────────────────────────────────────────────────

@_rule
def r13_conclusion_figure_supports_claim(ann: SemanticAnnotation, ctx: ObjectContext) -> EvidenceTrace | None:
    """Any figure in Conclusions section → SUPPORTS role."""
    if ann.object_type != "figure":
        return None
    section = ctx.section_title or ""
    if not re.search(r"\b(conclus|summary|recommendation)\b", section, re.I):
        return None
    ann.add_role(ScientificRole.VALIDATION)
    return EvidenceTrace(
        rule_triggered="R13_conclusion_figure",
        evidence_source="section_context",
        matched_terms=(section,),
        section=section,
        confidence_delta=0.05,
    )


@_rule
def r14_flood_figure_adds_flood_task(ann: SemanticAnnotation, ctx: ObjectContext) -> EvidenceTrace | None:
    """Flood-related figure → flood_inundation task if not already present."""
    if ann.object_type != "figure":
        return None
    if ann.semantic_type not in {SemanticType.FLOOD_EXTENT_MAP, SemanticType.HYDROGRAPH}:
        return None
    if HydroTask.FLOOD_INUNDATION in ann.candidate_tasks:
        return None
    if not (ann.contains_flood_extent or ann.contains_discharge):
        return None
    ann.add_task(HydroTask.FLOOD_INUNDATION)
    return EvidenceTrace(
        rule_triggered="R14_flood_figure_task",
        evidence_source="cross_object",
        matched_terms=(ann.semantic_type,),
        section=ctx.section_title,
        confidence_delta=0.06,
    )


@_rule
def r15_remote_sensing_figure_adds_rs_task(
    ann: SemanticAnnotation, ctx: ObjectContext
) -> EvidenceTrace | None:
    """Remote sensing figure → add lulc or drought task based on satellite signal."""
    if ann.object_type != "figure":
        return None
    if not ann.contains_remote_sensing:
        return None
    text = (ctx.caption or "") + " " + (ctx.parsed_row.get("caption") or "")
    if re.search(r"\b(ndvi|ndwi|vegetation|drought|land.use|lulc|land.cover)\b", text, re.I):
        ann.add_task(HydroTask.LULC_CLASSIFICATION)
        ann.add_task(HydroTask.DROUGHT_MONITORING)
        return EvidenceTrace(
            rule_triggered="R15_rs_vegetation_figure",
            evidence_source="caption",
            matched_terms=("ndvi/ndwi",),
            section=ctx.section_title,
            confidence_delta=0.06,
        )
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Reasoner
# ─────────────────────────────────────────────────────────────────────────────

class ObjectReasoner:
    """
    Applies the declarative rule list to a SemanticAnnotation in-place.

    Rules run independently; earlier rules do not suppress later ones.
    Every triggered rule's EvidenceTrace is appended to ann.evidence_trace.
    """

    def apply_rules(self, ann: SemanticAnnotation, ctx: ObjectContext) -> None:
        for rule_fn in _ALL_RULES:
            trace = rule_fn(ann, ctx)
            if trace is not None:
                ann.add_trace(trace)
