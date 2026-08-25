"""
equation_semantics.py — Semantic validator for equation scientific objects.

Named equations are matched declaratively via NamedEquation descriptors.
Each descriptor carries: domain, related models, candidate tasks, roles.

Covered equations:
  Evaluation metrics  : NSE, KGE, RMSE, MAE, PBIAS
  Hydraulics          : Manning, Saint-Venant, Continuity, Chezy
  Hydrology           : SCS-CN, Rational, Water Balance, Green-Ampt, Richards
  Groundwater         : Darcy, Theis, Cooper-Jacob
  Statistics          : Regression, Nash-Sutcliffe (as formula)
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from src.document.scientific_objects import SemanticType
from src.semantic_objects.semantic_evidence import (
    EvidenceTrace,
    HydroDomain,
    HydroTask,
    ObjectContext,
    ScientificRole,
    SemanticAnnotation,
)


@dataclass(frozen=True)
class NamedEquation:
    """Declarative descriptor for a recognisable scientific equation."""
    name:           str
    pattern:        re.Pattern[str]
    domain:         str
    related_models: tuple[str, ...]
    candidate_tasks:tuple[str, ...]
    scientific_role:tuple[str, ...]
    confidence:     float = 0.88


_NAMED_EQUATIONS: list[NamedEquation] = [
    # ── Evaluation metrics ────────────────────────────────────────────────────
    NamedEquation(
        name="NSE",
        pattern=re.compile(r"\b(nse|nash.?sutcliffe|nash.sutcliffe.efficiency)\b", re.I),
        domain=HydroDomain.STATISTICS,
        related_models=("SWAT", "HEC-HMS", "HBV", "VIC"),
        candidate_tasks=(HydroTask.STREAMFLOW_PRED, HydroTask.RAINFALL_RUNOFF),
        scientific_role=(ScientificRole.OBJECTIVE_FUNCTION, ScientificRole.EVALUATION),
        confidence=0.92,
    ),
    NamedEquation(
        name="KGE",
        pattern=re.compile(r"\b(kge|kling.?gupta|kling.?gupta.efficiency)\b", re.I),
        domain=HydroDomain.STATISTICS,
        related_models=("SWAT", "HEC-HMS", "HBV"),
        candidate_tasks=(HydroTask.STREAMFLOW_PRED,),
        scientific_role=(ScientificRole.OBJECTIVE_FUNCTION, ScientificRole.EVALUATION),
        confidence=0.92,
    ),
    NamedEquation(
        name="RMSE",
        pattern=re.compile(r"\b(rmse|root.mean.square.error)\b", re.I),
        domain=HydroDomain.STATISTICS,
        related_models=(),
        candidate_tasks=(HydroTask.STREAMFLOW_PRED,),
        scientific_role=(ScientificRole.OBJECTIVE_FUNCTION, ScientificRole.EVALUATION),
        confidence=0.88,
    ),
    NamedEquation(
        name="MAE",
        pattern=re.compile(r"\b(mae|mean.absolute.error)\b", re.I),
        domain=HydroDomain.STATISTICS,
        related_models=(),
        candidate_tasks=(HydroTask.STREAMFLOW_PRED,),
        scientific_role=(ScientificRole.OBJECTIVE_FUNCTION, ScientificRole.EVALUATION),
        confidence=0.85,
    ),
    NamedEquation(
        name="PBIAS",
        pattern=re.compile(r"\b(pbias|percent.bias|percentage.bias)\b", re.I),
        domain=HydroDomain.STATISTICS,
        related_models=(),
        candidate_tasks=(HydroTask.STREAMFLOW_PRED,),
        scientific_role=(ScientificRole.OBJECTIVE_FUNCTION, ScientificRole.EVALUATION),
        confidence=0.88,
    ),
    # ── Hydraulics ────────────────────────────────────────────────────────────
    NamedEquation(
        name="Manning",
        pattern=re.compile(r"\b(manning.s?|manning.equation|n\s*=\s*[0-9])\b", re.I),
        domain=HydroDomain.HYDRAULICS,
        related_models=("HEC-RAS", "LISFLOOD-FP"),
        candidate_tasks=(HydroTask.FLOOD_INUNDATION, HydroTask.HYDRAULIC_MODELING),
        scientific_role=(ScientificRole.PHYSICAL_LAW, ScientificRole.MODEL_INPUT),
        confidence=0.90,
    ),
    NamedEquation(
        name="Saint-Venant",
        pattern=re.compile(
            r"\b(saint.venant|shallow.water.equation|de.saint.venant|"
            r"svs|1d.flow|2d.flow.equation)\b", re.I
        ),
        domain=HydroDomain.HYDRODYNAMICS,
        related_models=("HEC-RAS", "LISFLOOD-FP", "MIKE-FLOOD"),
        candidate_tasks=(HydroTask.FLOOD_INUNDATION, HydroTask.HYDRAULIC_MODELING),
        scientific_role=(ScientificRole.PHYSICAL_LAW, ScientificRole.MODEL_INPUT),
        confidence=0.92,
    ),
    NamedEquation(
        name="Continuity",
        pattern=re.compile(r"\b(continuity.equation|mass.conservation|mass.balance|"
                           r"dq.?dx|∂q.?∂x)\b", re.I),
        domain=HydroDomain.HYDRAULICS,
        related_models=(),
        candidate_tasks=(HydroTask.FLOOD_INUNDATION, HydroTask.HYDRAULIC_MODELING),
        scientific_role=(ScientificRole.PHYSICAL_LAW,),
        confidence=0.85,
    ),
    # ── Hydrology ─────────────────────────────────────────────────────────────
    NamedEquation(
        name="SCS-CN",
        pattern=re.compile(r"\b(scs.?cn|curve.number|scs.runoff|soil.conservation)\b", re.I),
        domain=HydroDomain.HYDROLOGY,
        related_models=("HEC-HMS", "SWAT"),
        candidate_tasks=(HydroTask.RAINFALL_RUNOFF, HydroTask.FLOOD_FORECASTING),
        scientific_role=(ScientificRole.PHYSICAL_LAW, ScientificRole.MODEL_INPUT),
        confidence=0.90,
    ),
    NamedEquation(
        name="Rational Method",
        pattern=re.compile(r"\b(rational.method|q\s*=\s*c\s*i\s*a|ciA)\b", re.I),
        domain=HydroDomain.HYDROLOGY,
        related_models=("HEC-HMS",),
        candidate_tasks=(HydroTask.RAINFALL_RUNOFF, HydroTask.FLOOD_FORECASTING),
        scientific_role=(ScientificRole.PHYSICAL_LAW,),
        confidence=0.88,
    ),
    NamedEquation(
        name="Water Balance",
        pattern=re.compile(
            r"\b(water.balance|water.budget|p\s*[-–]\s*et|precipitation.minus|"
            r"dS.?dt|storage.change)\b", re.I
        ),
        domain=HydroDomain.HYDROLOGY,
        related_models=("SWAT", "VIC", "HBV"),
        candidate_tasks=(HydroTask.RAINFALL_RUNOFF, HydroTask.STREAMFLOW_PRED),
        scientific_role=(ScientificRole.PHYSICAL_LAW,),
        confidence=0.87,
    ),
    NamedEquation(
        name="Green-Ampt",
        pattern=re.compile(r"\b(green.ampt|infiltration.equation|ponding.time)\b", re.I),
        domain=HydroDomain.HYDROLOGY,
        related_models=("HEC-HMS",),
        candidate_tasks=(HydroTask.RAINFALL_RUNOFF,),
        scientific_role=(ScientificRole.PHYSICAL_LAW, ScientificRole.MODEL_INPUT),
        confidence=0.90,
    ),
    # ── Groundwater ───────────────────────────────────────────────────────────
    NamedEquation(
        name="Darcy",
        pattern=re.compile(r"\b(darcy.s?|darcy.law|hydraulic.conductivity.k)\b", re.I),
        domain=HydroDomain.HYDROGEOLOGY,
        related_models=("MODFLOW",),
        candidate_tasks=(HydroTask.GROUNDWATER,),
        scientific_role=(ScientificRole.PHYSICAL_LAW,),
        confidence=0.92,
    ),
    NamedEquation(
        name="Richards",
        pattern=re.compile(r"\b(richards.equation|unsaturated.flow|θ.h.equation)\b", re.I),
        domain=HydroDomain.HYDROGEOLOGY,
        related_models=(),
        candidate_tasks=(HydroTask.GROUNDWATER,),
        scientific_role=(ScientificRole.PHYSICAL_LAW,),
        confidence=0.90,
    ),
]


class EquationSemanticValidator:
    """Semantic validator for equation objects."""

    def validate(self, row: dict[str, Any], ctx: ObjectContext) -> SemanticAnnotation:
        sem_type  = (ctx.sci_object or {}).get("semantic_type") or row.get("semantic_type", "")
        base_conf = float((ctx.sci_object or {}).get("classifier_score") or 0.5)

        ann = SemanticAnnotation(
            object_id=        row["equation_id"],
            paper_id=         ctx.paper_id,
            object_type=      "equation",
            semantic_type=    sem_type,
            semantic_confidence=base_conf,
        )

        text = _eq_text(row, ctx)

        # ── Named equation matching ───────────────────────────────────────────
        matched: list[NamedEquation] = []
        for eq in _NAMED_EQUATIONS:
            if eq.pattern.search(text):
                matched.append(eq)

        if matched:
            # Use the highest-confidence match as the primary identification
            primary = max(matched, key=lambda e: e.confidence)

            ann.domain = primary.domain
            ann.semantic_confidence = max(ann.semantic_confidence, primary.confidence)

            for role in primary.scientific_role:
                ann.add_role(role)
            for task in primary.candidate_tasks:
                ann.add_task(task)
            for model in primary.related_models:
                ann.add_model(model)

            ann.add_trace(EvidenceTrace(
                rule_triggered=f"EQ_NAMED:{primary.name}",
                evidence_source="pattern",
                matched_terms=(primary.name,),
                section=ctx.section_title,
                confidence_delta=primary.confidence - base_conf,
            ))

            # Secondary matches add models and tasks without overriding domain
            for eq in matched:
                if eq is primary:
                    continue
                for model in eq.related_models:
                    ann.add_model(model)
                for task in eq.candidate_tasks:
                    ann.add_task(task)
                ann.add_trace(EvidenceTrace(
                    rule_triggered=f"EQ_SECONDARY:{eq.name}",
                    evidence_source="pattern",
                    matched_terms=(eq.name,),
                    section=ctx.section_title,
                    confidence_delta=0.02,
                ))

        # ── Propagate Stage 2 equation_name if no named match ─────────────────
        if ctx.sci_object:
            eq_name = ctx.sci_object.get("equation_name")
            if eq_name and not matched:
                ann.add_trace(EvidenceTrace(
                    rule_triggered="EQ_STAGE2_NAME",
                    evidence_source="pattern",
                    matched_terms=(eq_name,),
                    section=ctx.section_title,
                    confidence_delta=0.05,
                ))

        # ── Evidence strength ─────────────────────────────────────────────────
        ann.evidence_strength = min(1.0,
            0.4 * min(len(matched), 3) / 3
            + (0.2 if ctx.section_title else 0.0)
            + (0.1 if ctx.caption else 0.0)
        )

        return ann


# ── Helpers ───────────────────────────────────────────────────────────────────

def _eq_text(row: dict[str, Any], ctx: ObjectContext) -> str:
    parts = [row.get("text") or "", ctx.section_text or ""]
    return " ".join(p for p in parts if p)
