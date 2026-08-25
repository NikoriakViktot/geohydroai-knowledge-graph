"""
figure_semantics.py — Semantic validator for figure scientific objects.

Validates figures produced by Stage 2 and enriches them with:
  - Fine-grained hydrological content flags (discharge, water level, …)
  - Scientific roles (what the figure DOES, not just what it IS)
  - Candidate tasks and likely models
  - Evidence traces for every inference

No LLMs, no embeddings — pattern tables and declarative role maps only.
"""
from __future__ import annotations

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

# ── Content flag patterns ─────────────────────────────────────────────────────
# Each entry: (flag_name, compiled pattern, evidence_source_label)

_CONTENT_FLAGS: list[tuple[str, re.Pattern[str], float]] = [
    ("contains_discharge",
     re.compile(r"\b(discharge|streamflow|q_obs|q_sim|flow rate|runoff volume)\b", re.I),
     0.08),
    ("contains_water_level",
     re.compile(r"\b(water level|water stage|gauge height|wl|stage|h_obs|h_sim)\b", re.I),
     0.08),
    ("contains_uncertainty_band",
     re.compile(r"\b(uncertainty|confidence interval|ci|prediction band|bounds|envelope|spread)\b", re.I),
     0.06),
    ("contains_flood_extent",
     re.compile(r"\b(inundation|flood extent|flooded area|flood map|flood boundary|water body)\b", re.I),
     0.08),
    ("contains_timeseries",
     re.compile(r"\b(time series|temporal|hourly|daily|monthly|annual|hydrograph|\d{4}[-–]\d{4})\b", re.I),
     0.05),
    ("contains_spatial_data",
     re.compile(r"\b(spatial|map|grid|raster|dem|elevation|lat(itude)?|lon(gitude)?|utm|epsg)\b", re.I),
     0.05),
    ("contains_remote_sensing",
     re.compile(r"\b(sentinel|landsat|modis|sar|radar|ndvi|ndwi|satellite|goes|gpm|trmm|chirps)\b", re.I),
     0.07),
]

# ── Semantic type → base scientific roles ─────────────────────────────────────

_TYPE_ROLES: dict[str, list[str]] = {
    SemanticType.HYDROGRAPH:        [ScientificRole.TIMESERIES_VIZ],
    SemanticType.FLOOD_EXTENT_MAP:  [ScientificRole.SPATIAL_OUTPUT, ScientificRole.FLOOD_RISK],
    SemanticType.SCATTER_PLOT:      [ScientificRole.EVALUATION_VIZ, ScientificRole.OBSERVED_VS_SIM],
    SemanticType.CALIBRATION_PLOT:  [ScientificRole.MODEL_CALIBRATION, ScientificRole.EVALUATION_VIZ],
    SemanticType.SATELLITE_IMAGE:   [ScientificRole.REMOTE_SENSING_INPUT],
    SemanticType.WATERSHED_MAP:     [ScientificRole.STUDY_AREA_DEF],
    SemanticType.FLOWCHART:         [ScientificRole.METHODOLOGY],
    SemanticType.BAR_CHART:         [ScientificRole.EVALUATION_VIZ],
    SemanticType.GENERIC_FIGURE:    [],
}

# ── Semantic type → candidate tasks ──────────────────────────────────────────

_TYPE_TASKS: dict[str, list[str]] = {
    SemanticType.HYDROGRAPH:        [HydroTask.STREAMFLOW_PRED, HydroTask.RAINFALL_RUNOFF],
    SemanticType.FLOOD_EXTENT_MAP:  [HydroTask.FLOOD_INUNDATION, HydroTask.FLOOD_FORECASTING],
    SemanticType.SCATTER_PLOT:      [HydroTask.STREAMFLOW_PRED],
    SemanticType.CALIBRATION_PLOT:  [HydroTask.RAINFALL_RUNOFF, HydroTask.STREAMFLOW_PRED],
    SemanticType.SATELLITE_IMAGE:   [HydroTask.FLOOD_INUNDATION, HydroTask.LULC_CLASSIFICATION],
    SemanticType.WATERSHED_MAP:     [HydroTask.DEM_ANALYSIS, HydroTask.RAINFALL_RUNOFF],
    SemanticType.FLOWCHART:         [],
    SemanticType.BAR_CHART:         [],
    SemanticType.GENERIC_FIGURE:    [],
}

# ── Semantic type → implied domain ────────────────────────────────────────────

_TYPE_DOMAIN: dict[str, str] = {
    SemanticType.HYDROGRAPH:        HydroDomain.HYDROLOGY,
    SemanticType.FLOOD_EXTENT_MAP:  HydroDomain.HYDROLOGY,
    SemanticType.WATERSHED_MAP:     HydroDomain.HYDROLOGY,
    SemanticType.SCATTER_PLOT:      HydroDomain.STATISTICS,
    SemanticType.CALIBRATION_PLOT:  HydroDomain.HYDROLOGY,
    SemanticType.SATELLITE_IMAGE:   HydroDomain.REMOTE_SENSING,
    SemanticType.FLOWCHART:         None,
    SemanticType.BAR_CHART:         None,
    SemanticType.GENERIC_FIGURE:    None,
}

# ── Model detection signals ───────────────────────────────────────────────────

_MODEL_SIGNALS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bhec.?ras\b",         re.I), "HEC-RAS"),
    (re.compile(r"\bhec.?hms\b",         re.I), "HEC-HMS"),
    (re.compile(r"\bswat\b",             re.I), "SWAT"),
    (re.compile(r"\blstm\b",             re.I), "LSTM"),
    (re.compile(r"\bvic\b",              re.I), "VIC"),
    (re.compile(r"\bhbv\b",              re.I), "HBV"),
    (re.compile(r"\btopmodel\b",         re.I), "TOPMODEL"),
    (re.compile(r"\bsac.?sma\b",         re.I), "SAC-SMA"),
    (re.compile(r"\bgr4j\b",             re.I), "GR4J"),
    (re.compile(r"\blisflood\b",         re.I), "LISFLOOD-FP"),
    (re.compile(r"\brandom.forest\b",    re.I), "RANDOM-FOREST"),
    (re.compile(r"\bxgboost\b",          re.I), "XGBOOST"),
]

_METRIC_SIGNALS: re.Pattern[str] = re.compile(
    r"\b(nse|kge|rmse|mae|pbias|r[²2]|mape|cc|bias|kling.gupta|nash.sutcliffe)\b", re.I
)


class FigureSemanticValidator:
    """Semantic validator for figure objects."""

    def validate(self, row: dict[str, Any], ctx: ObjectContext) -> SemanticAnnotation:
        """
        Build a SemanticAnnotation for one figure row.

        Parameters
        ----------
        row : Stage 1 figures.parquet row dict.
        ctx : ObjectContext assembled by ContextBuilder.
        """
        sem_type = (ctx.sci_object or {}).get("semantic_type") or row.get("semantic_type", "")
        base_conf = float((ctx.sci_object or {}).get("classifier_score") or 0.5)

        ann = SemanticAnnotation(
            object_id=        row["figure_id"],
            paper_id=         ctx.paper_id,
            object_type=      "figure",
            semantic_type=    sem_type,
            semantic_confidence=base_conf,
        )

        text = _fig_text(row, ctx)

        # ── 1. Base roles from semantic type ──────────────────────────────────
        for role in _TYPE_ROLES.get(sem_type, []):
            ann.add_role(role)

        # ── 2. Base tasks ─────────────────────────────────────────────────────
        for task in _TYPE_TASKS.get(sem_type, []):
            ann.add_task(task)

        # ── 3. Domain ─────────────────────────────────────────────────────────
        ann.domain = _TYPE_DOMAIN.get(sem_type)

        # ── 4. Content flags from text ────────────────────────────────────────
        signals_fired = 0
        for flag, pattern, delta in _CONTENT_FLAGS:
            m = pattern.search(text)
            if m:
                setattr(ann, flag, True)
                signals_fired += 1
                ann.add_trace(EvidenceTrace(
                    rule_triggered=f"FIGURE_CONTENT:{flag}",
                    evidence_source="caption",
                    matched_terms=(m.group(0),),
                    section=ctx.section_title,
                    confidence_delta=delta,
                ))
            else:
                if getattr(ann, flag) is None:
                    setattr(ann, flag, False)

        # ── 5. Model signals ──────────────────────────────────────────────────
        for pattern, model_name in _MODEL_SIGNALS:
            if pattern.search(text):
                ann.add_model(model_name)

        # Propagate candidate_models from Stage 2 if present
        if ctx.sci_object:
            _propagate_candidates(ann, ctx.sci_object)

        # ── 6. Metric signals ─────────────────────────────────────────────────
        for m in _METRIC_SIGNALS.finditer(text):
            ann.add_metric(m.group(1))

        # ── 7. Evidence strength ──────────────────────────────────────────────
        total_signals = len(_CONTENT_FLAGS)
        ann.evidence_strength = min(1.0, signals_fired / max(total_signals, 1)
                                    + (0.1 if ctx.caption else 0.0)
                                    + (0.05 if ctx.section_title else 0.0))

        return ann


# ── Helpers ───────────────────────────────────────────────────────────────────

def _fig_text(row: dict[str, Any], ctx: ObjectContext) -> str:
    parts = [
        row.get("label")   or "",
        row.get("caption") or "",
        ctx.caption        or "",
        ctx.section_text   or "",
    ]
    return " ".join(p for p in parts if p)


def _propagate_candidates(ann: SemanticAnnotation, sci_obj: dict[str, Any]) -> None:
    import json as _json
    for field_name, add_fn in (
        ("candidate_models",  ann.add_model),
        ("candidate_metrics", ann.add_metric),
    ):
        raw = sci_obj.get(field_name)
        if raw:
            try:
                for item in _json.loads(raw):
                    add_fn(item)
            except (ValueError, TypeError):
                pass
