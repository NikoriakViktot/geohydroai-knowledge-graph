"""
object_classifier.py — Heuristic semantic type classifier for Stage 2.

Classifies parsed Stage 1 row dicts into SemanticType values using
caption/title/label pattern matching.  No LLMs, no embeddings — rules only.

Returns (semantic_type: str, score: float, classifier_source: str, flags: dict).
"""
from __future__ import annotations

import json
import re
from typing import Any

from src.document.scientific_objects import SemanticType

# ── Pattern tables ────────────────────────────────────────────────────────────

# Ordered: first match wins (most specific first)
_TABLE_PATTERNS: list[tuple[re.Pattern[str], str, float]] = [
    (re.compile(r"\b(nse|kge|rmse|mae|pbias|mape|r[²2]|cc|bias)\b", re.I),
     SemanticType.METRICS_TABLE, 0.90),
    (re.compile(r"\b(performance|efficiency|error|accuracy|goodness.of.fit)\b", re.I),
     SemanticType.METRICS_TABLE, 0.80),
    (re.compile(r"\b(compar\w*|benchmark|evaluat\w*|vs\.?|versus)\b", re.I),
     SemanticType.COMPARISON_TABLE, 0.75),
    (re.compile(r"\b(parameters?|calibrat\w*|optimi[sz]\w*|thresholds?)\b", re.I),
     SemanticType.PARAMETER_TABLE, 0.75),
    (re.compile(r"\b(stations?|gauges?|catchments?|basins?|sites?|watersheds?)\b", re.I),
     SemanticType.STATION_TABLE, 0.70),
]

_FIGURE_PATTERNS: list[tuple[re.Pattern[str], str, float]] = [
    (re.compile(r"\b(scatter|observed.vs|simulated.vs|obs.vs)\b", re.I),
     SemanticType.SCATTER_PLOT, 0.88),
    (re.compile(r"\b(flood.?(inundation|extent|map|area)|inundat)\b", re.I),
     SemanticType.FLOOD_EXTENT_MAP, 0.90),
    (re.compile(r"\b(hydrograph|discharge|streamflow|runoff|flow rate)\b", re.I),
     SemanticType.HYDROGRAPH, 0.92),
    (re.compile(r"\b(calibrat(ion|ed))\b", re.I),
     SemanticType.CALIBRATION_PLOT, 0.75),
    (re.compile(r"\b(satellite|modis|landsat|sentinel|sar|radar)\b", re.I),
     SemanticType.SATELLITE_IMAGE, 0.85),
    (re.compile(r"\b(watershed|catchment|basin|dem|digital.elevation)\b", re.I),
     SemanticType.WATERSHED_MAP, 0.80),
    (re.compile(r"\b(flowchart|workflow|framework|architecture|methodology)\b", re.I),
     SemanticType.FLOWCHART, 0.80),
    (re.compile(r"\b(bar.chart|bar.graph|histogram|column.chart)\b", re.I),
     SemanticType.BAR_CHART, 0.75),
]

_EQUATION_PATTERNS: list[tuple[re.Pattern[str], str, float]] = [
    (re.compile(r"\b(nse|kge|kling.gupta|nash.sutcliffe|rmse|mae)\b", re.I),
     SemanticType.OBJECTIVE_FUNCTION, 0.92),
    (re.compile(r"\b(continuity|momentum|energy.balance|darcy|manning)\b", re.I),
     SemanticType.PHYSICAL_EQUATION, 0.88),
    (re.compile(r"\b(regression|linear|polynomial|fit|R\^?2)\b", re.I),
     SemanticType.REGRESSION_FORMULA, 0.75),
]

_SECTION_PATTERNS: list[tuple[re.Pattern[str], str, float]] = [
    (re.compile(r"^(method|approach|material|model|framework|experiment)", re.I),
     SemanticType.METHODS_SECTION, 0.90),
    (re.compile(r"^(result|finding|output|performance)", re.I),
     SemanticType.RESULTS_SECTION, 0.90),
    (re.compile(r"^(data|dataset|observ|measur|station|gauge)", re.I),
     SemanticType.DATA_SECTION, 0.85),
    (re.compile(r"^(study.area|case.study|site.description|watershed|basin)", re.I),
     SemanticType.STUDY_AREA_SECTION, 0.85),
    (re.compile(r"^(intro|background|motivation|overview)", re.I),
     SemanticType.INTRO_SECTION, 0.85),
    (re.compile(r"^(conclus|summary|discussion|future.work)", re.I),
     SemanticType.CONCLUSION_SECTION, 0.85),
]

# ── Domain signals ────────────────────────────────────────────────────────────

_MODEL_SIGNALS = re.compile(
    r"\b(lstm|swat|vic|hbv|topmodel|sacsma|hec.ras|xgboost|random.forest|"
    r"gradient.boost|ann|cnn|transformer|bert|gpt|xaj|"
    r"pdm|tank|simhyd|gr4j|sacramento|hymod|arima)\b", re.I
)

_METRIC_SIGNALS = re.compile(
    r"\b(nse|kge|rmse|mae|mse|pbias|r[²2]|mape|bias|correlation|cc|kling.gupta)\b", re.I
)

_COORD_SIGNALS = re.compile(
    r"\b(lat(itude)?|lon(gitude)?|coord(inate)?|utm|georefer|spatial|gis|epsg)\b", re.I
)

_SATELLITE_SIGNALS = re.compile(
    r"\b(modis|landsat|sentinel|sar|radar|goes|grace|gpm|trmm|chirps|era5)\b", re.I
)

_TIMESERIES_SIGNALS = re.compile(
    r"\b(time.series|temporal|hourly|daily|monthly|annual|discharge|streamflow)\b", re.I
)

_HYDRO_SIGNALS = re.compile(
    r"\b(hydrograph|discharge|streamflow|runoff|flood|peak.flow|baseflow)\b", re.I
)


# ── Public classifier API ─────────────────────────────────────────────────────

class ScientificObjectClassifier:
    """Heuristic rule-based semantic type classifier."""

    def classify_table(self, row: dict[str, Any]) -> tuple[str, float, str, dict[str, Any]]:
        text = _table_text(row)
        sem, score = _match_patterns(text, _TABLE_PATTERNS, SemanticType.DATA_TABLE, 0.40)
        flags = _compute_flags(text)
        return sem, score, "rules", flags

    def classify_figure(self, row: dict[str, Any]) -> tuple[str, float, str, dict[str, Any]]:
        text = _fig_text(row)
        sem, score = _match_patterns(text, _FIGURE_PATTERNS, SemanticType.GENERIC_FIGURE, 0.35)
        flags = _compute_flags(text)
        return sem, score, "rules", flags

    def classify_equation(self, row: dict[str, Any]) -> tuple[str, float, str, dict[str, Any]]:
        text = (row.get("text") or "").strip()
        sem, score = _match_patterns(text, _EQUATION_PATTERNS, SemanticType.GENERIC_EQUATION, 0.30)
        flags = _compute_flags(text)
        flags["equation_name"] = _guess_equation_name(text)
        return sem, score, "rules", flags

    def classify_section(self, row: dict[str, Any]) -> tuple[str, float, str, dict[str, Any]]:
        title = (row.get("title") or "").strip()
        sem, score = _match_patterns(title, _SECTION_PATTERNS, SemanticType.GENERIC_SECTION, 0.30)
        text  = (row.get("text") or "")
        flags = _compute_flags(text)
        return sem, score, "rules", flags

    def classify_reference(self, row: dict[str, Any]) -> tuple[str, float, str, dict[str, Any]]:
        return SemanticType.REFERENCE, 1.0, "rules", {}


# ── Internal helpers ──────────────────────────────────────────────────────────

def _table_text(row: dict[str, Any]) -> str:
    parts = [
        row.get("label") or "",
        row.get("caption") or "",
        " ".join(row.get("header_row") or []),
    ]
    return " ".join(p for p in parts if p)


def _fig_text(row: dict[str, Any]) -> str:
    parts = [row.get("label") or "", row.get("caption") or ""]
    return " ".join(p for p in parts if p)


def _match_patterns(
    text:     str,
    patterns: list[tuple[re.Pattern[str], str, float]],
    default:  str,
    default_score: float,
) -> tuple[str, float]:
    for pat, semantic_type, score in patterns:
        if pat.search(text):
            return semantic_type, score
    return default, default_score


def _compute_flags(text: str) -> dict[str, Any]:
    candidates_models  = _MODEL_SIGNALS.findall(text)
    candidates_metrics = _METRIC_SIGNALS.findall(text)
    return {
        "contains_models":       bool(candidates_models),
        "contains_metrics":      bool(candidates_metrics),
        "contains_timeseries":   bool(_TIMESERIES_SIGNALS.search(text)),
        "contains_coordinates":  bool(_COORD_SIGNALS.search(text)),
        "contains_satellite":    bool(_SATELLITE_SIGNALS.search(text)),
        "contains_hydrograph":   bool(_HYDRO_SIGNALS.search(text)),
        "candidate_models":      json.dumps(list(dict.fromkeys(
                                    m.upper() for m in candidates_models
                                 ))) if candidates_models else None,
        "candidate_metrics":     json.dumps(list(dict.fromkeys(
                                    m.upper() for m in candidates_metrics
                                 ))) if candidates_metrics else None,
    }


def _guess_equation_name(text: str) -> str | None:
    _NAMED = [
        (re.compile(r"\bnse\b|\bnash.sutcliffe\b", re.I), "NSE"),
        (re.compile(r"\bkge\b|\bkling.gupta\b",    re.I), "KGE"),
        (re.compile(r"\brmse\b",                   re.I), "RMSE"),
        (re.compile(r"\bmae\b",                    re.I), "MAE"),
        (re.compile(r"\bpbias\b",                  re.I), "PBIAS"),
        (re.compile(r"\bdarcy\b",                  re.I), "Darcy"),
        (re.compile(r"\bmanning\b",                re.I), "Manning"),
        (re.compile(r"\bcontinuity\b",             re.I), "Continuity"),
    ]
    for pat, name in _NAMED:
        if pat.search(text):
            return name
    return None
