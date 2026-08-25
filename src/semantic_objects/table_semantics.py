"""
table_semantics.py — Semantic validator for table scientific objects.

Enriches table objects with:
  - Scientific roles (evaluation, calibration, comparison, …)
  - Fine-grained metric and model detection from header + caption
  - Uncertainty-table identification
  - Candidate tasks
  - Evidence traces
"""
from __future__ import annotations

import json
import re
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

# ── Role map ──────────────────────────────────────────────────────────────────

_TYPE_ROLES: dict[str, list[str]] = {
    SemanticType.METRICS_TABLE:    [ScientificRole.EVALUATION, ScientificRole.MODEL_VALIDATION],
    SemanticType.COMPARISON_TABLE: [ScientificRole.MODEL_COMPARISON, ScientificRole.EVALUATION],
    SemanticType.PARAMETER_TABLE:  [ScientificRole.MODEL_CALIBRATION, ScientificRole.DATA_DESCRIPTION],
    SemanticType.STATION_TABLE:    [ScientificRole.DATA_DESCRIPTION, ScientificRole.STUDY_AREA_DEF],
    SemanticType.DATA_TABLE:       [ScientificRole.DATA_DESCRIPTION],
}

# ── Task map ──────────────────────────────────────────────────────────────────

_TYPE_TASKS: dict[str, list[str]] = {
    SemanticType.METRICS_TABLE:    [HydroTask.STREAMFLOW_PRED, HydroTask.RAINFALL_RUNOFF],
    SemanticType.COMPARISON_TABLE: [HydroTask.STREAMFLOW_PRED],
    SemanticType.PARAMETER_TABLE:  [HydroTask.RAINFALL_RUNOFF],
    SemanticType.STATION_TABLE:    [],
    SemanticType.DATA_TABLE:       [],
}

# ── Domain map ────────────────────────────────────────────────────────────────

_TYPE_DOMAIN: dict[str, str] = {
    SemanticType.METRICS_TABLE:    HydroDomain.STATISTICS,
    SemanticType.COMPARISON_TABLE: HydroDomain.STATISTICS,
    SemanticType.PARAMETER_TABLE:  HydroDomain.HYDROLOGY,
    SemanticType.STATION_TABLE:    HydroDomain.HYDROLOGY,
    SemanticType.DATA_TABLE:       HydroDomain.HYDROLOGY,
}

# ── Header / column patterns ──────────────────────────────────────────────────
# Detect specific metrics in header row cells

_METRIC_COL_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # Hydrological efficiency
    (re.compile(r"\bnse\b|nash[\s\-]?sutcliffe",    re.I), "NSE"),
    (re.compile(r"\bkge\b|kling[\s\-]?gupta",       re.I), "KGE"),
    (re.compile(r"\bpbias\b|percent[\s\-]?bias",    re.I), "PBIAS"),
    (re.compile(r"\brsr\b",                          re.I), "RSR"),
    (re.compile(r"\bioa\b|index[\s\-]?of[\s\-]?agreement", re.I), "IOA"),
    # Error metrics
    (re.compile(r"\brmse\b",                         re.I), "RMSE"),
    (re.compile(r"\bmbe\b|mean[\s\-]?bias[\s\-]?err", re.I), "MBE"),
    (re.compile(r"\bmae\b",                          re.I), "MAE"),
    (re.compile(r"\bmse\b",                          re.I), "MSE"),
    (re.compile(r"\bmape\b",                         re.I), "MAPE"),
    (re.compile(r"\bnmad\b",                         re.I), "NMAD"),
    (re.compile(r"\bbias\b",                         re.I), "BIAS"),
    # Regression
    (re.compile(r"\br[²2]\b",                        re.I), "R²"),
    (re.compile(r"\bcc\b|correlation[\s\-]?coeff",  re.I), "CC"),
    # Classification
    (re.compile(r"balanced[\s\-]?acc(?:uracy)?",    re.I), "BA"),
    (re.compile(r"overall[\s\-]?acc(?:uracy)?|accuracy|\boa\b", re.I), "OA"),
    (re.compile(r"\bauc\b|roc[\s\-]?auc",           re.I), "AUC"),
    (re.compile(r"\bf1\b|f[\s\-]?measure|dice[\s\-]?(?:score|coeff)", re.I), "F1"),
    (re.compile(r"\biou\b|jaccard",                  re.I), "IoU"),
    (re.compile(r"\bkappa\b",                        re.I), "Kappa"),
    (re.compile(r"\bmcc\b|matthews[\s\-]?corr",      re.I), "MCC"),
    (re.compile(r"\bprecision\b|\bppv\b",            re.I), "Precision"),
    (re.compile(r"\bpod\b|hit[\s\-]?rate|recall|sensitivity|\btpr\b", re.I), "POD"),
    (re.compile(r"\bcsi\b|critical[\s\-]?success",   re.I), "CSI"),
    (re.compile(r"\bfar\b|false[\s\-]?alarm",        re.I), "FAR"),
    (re.compile(r"\bspecificity\b|\btnr\b",           re.I), "Specificity"),
]

# ── Uncertainty-table signals ─────────────────────────────────────────────────

_UNCERTAINTY_SIGNALS: re.Pattern[str] = re.compile(
    r"\b(uncertainty|std|standard.deviation|confidence.interval|ci|"
    r"95.%|percentile|ensemble|monte.carlo|bootstrap|range)\b", re.I
)

# ── Model detection (header + caption) ───────────────────────────────────────

_MODEL_SIGNALS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bhec.?ras\b",       re.I), "HEC-RAS"),
    (re.compile(r"\bhec.?hms\b",       re.I), "HEC-HMS"),
    (re.compile(r"\bswat\b",           re.I), "SWAT"),
    (re.compile(r"\blstm\b",           re.I), "LSTM"),
    (re.compile(r"\bvic\b",            re.I), "VIC"),
    (re.compile(r"\bhbv\b",            re.I), "HBV"),
    (re.compile(r"\bgr4j\b",           re.I), "GR4J"),
    (re.compile(r"\blisflood\b",       re.I), "LISFLOOD-FP"),
    (re.compile(r"\bxgboost\b",        re.I), "XGBOOST"),
    (re.compile(r"\brandom.forest\b",  re.I), "RANDOM-FOREST"),
    (re.compile(r"\bxaj\b",            re.I), "XAJ"),
    (re.compile(r"\bsimhyd\b",         re.I), "SIMHYD"),
    (re.compile(r"\bsac.?sma\b",       re.I), "SAC-SMA"),
]


class TableSemanticValidator:
    """Semantic validator for table objects."""

    def validate(self, row: dict[str, Any], ctx: ObjectContext) -> SemanticAnnotation:
        sem_type  = (ctx.sci_object or {}).get("semantic_type") or row.get("semantic_type", "")
        base_conf = float((ctx.sci_object or {}).get("classifier_score") or 0.5)

        ann = SemanticAnnotation(
            object_id=        row["table_id"],
            paper_id=         ctx.paper_id,
            object_type=      "table",
            semantic_type=    sem_type,
            semantic_confidence=base_conf,
        )

        text      = _table_text(row, ctx)
        header    = row.get("header_row") or []
        header_str= " ".join(str(h) for h in header)

        # ── 1. Base roles ─────────────────────────────────────────────────────
        for role in _TYPE_ROLES.get(sem_type, []):
            ann.add_role(role)

        # ── 2. Base tasks ─────────────────────────────────────────────────────
        for task in _TYPE_TASKS.get(sem_type, []):
            ann.add_task(task)

        # ── 3. Domain ─────────────────────────────────────────────────────────
        ann.domain = _TYPE_DOMAIN.get(sem_type)

        # ── 4. Metric detection from header ───────────────────────────────────
        metrics_found = []
        for pattern, metric_name in _METRIC_COL_PATTERNS:
            if pattern.search(header_str) or pattern.search(text):
                ann.add_metric(metric_name)
                metrics_found.append(metric_name)

        if metrics_found:
            ann.add_trace(EvidenceTrace(
                rule_triggered="TABLE_METRIC_HEADER",
                evidence_source="label",
                matched_terms=tuple(metrics_found),
                section=ctx.section_title,
                confidence_delta=0.08 * min(len(metrics_found), 3),
            ))

        # ── 5. Model detection from text ──────────────────────────────────────
        models_found = []
        for pattern, model_name in _MODEL_SIGNALS:
            if pattern.search(text):
                ann.add_model(model_name)
                models_found.append(model_name)

        if models_found:
            ann.add_trace(EvidenceTrace(
                rule_triggered="TABLE_MODEL_SIGNAL",
                evidence_source="caption",
                matched_terms=tuple(models_found),
                section=ctx.section_title,
                confidence_delta=0.06 * min(len(models_found), 4),
            ))

        # ── 6. Uncertainty detection ──────────────────────────────────────────
        if _UNCERTAINTY_SIGNALS.search(text) or _UNCERTAINTY_SIGNALS.search(header_str):
            ann.add_role(ScientificRole.UNCERTAINTY_QUANT)
            if HydroTask.UNCERTAINTY_ANALYSIS not in ann.candidate_tasks:
                ann.add_task(HydroTask.UNCERTAINTY_ANALYSIS)
            ann.add_trace(EvidenceTrace(
                rule_triggered="TABLE_UNCERTAINTY_SIGNAL",
                evidence_source="caption",
                matched_terms=("uncertainty",),
                section=ctx.section_title,
                confidence_delta=0.06,
            ))

        # ── 7. Multi-model comparison upgrade ────────────────────────────────
        if len(ann.likely_models) >= 2 and ScientificRole.MODEL_COMPARISON not in ann.scientific_role:
            ann.add_role(ScientificRole.MODEL_COMPARISON)
            ann.add_trace(EvidenceTrace(
                rule_triggered="TABLE_MULTI_MODEL_COMPARISON",
                evidence_source="pattern",
                matched_terms=tuple(ann.likely_models[:4]),
                section=ctx.section_title,
                confidence_delta=0.10,
            ))

        # ── 8. Propagate Stage 2 candidates ──────────────────────────────────
        if ctx.sci_object:
            _propagate_candidates(ann, ctx.sci_object)

        # ── 9. Evidence strength ──────────────────────────────────────────────
        ann.evidence_strength = min(1.0,
            (0.3 if metrics_found   else 0.0) +
            (0.3 if models_found    else 0.0) +
            (0.1 if ctx.caption     else 0.0) +
            (0.1 if header          else 0.0) +
            (0.1 if ctx.section_title else 0.0)
        )

        return ann


# ── Helpers ───────────────────────────────────────────────────────────────────

def _table_text(row: dict[str, Any], ctx: ObjectContext) -> str:
    parts = [
        row.get("label")   or "",
        row.get("caption") or "",
        ctx.caption        or "",
        ctx.section_text   or "",
    ]
    return " ".join(p for p in parts if p)


def _propagate_candidates(ann: SemanticAnnotation, sci_obj: dict[str, Any]) -> None:
    for field_name, add_fn in (
        ("candidate_models",  ann.add_model),
        ("candidate_metrics", ann.add_metric),
    ):
        raw = sci_obj.get(field_name)
        if raw:
            try:
                for item in json.loads(raw):
                    add_fn(item)
            except (ValueError, TypeError):
                pass
