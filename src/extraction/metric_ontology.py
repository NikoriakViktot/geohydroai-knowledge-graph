"""
metric_ontology.py — Metric taxonomy for flood mapping / remote sensing / hydrology.

Covers:
  - Classification accuracy metrics (OA, F1, IoU, Kappa, …)
  - Flood-detection confusion metrics (POD, FAR, CSI, …)
  - Hydrological modelling metrics (NSE, KGE, RMSE, …)
  - Spatial agreement metrics (flood extent area, overlap, …)
  - DEM/topography metrics (vertical RMSE, resolution, …)
  - Operational timeliness metrics (latency, processing time, …)
  - Sensor resolution metrics (spatial/temporal resolution, …)

Key exports
-----------
METRIC_GROUPS      dict[group_name → list[metric_id]]
METRIC_TO_GROUP    dict[metric_id → group_name]
RATIO_METRICS      set of metric_ids that live on [0, 1] or [0, 100%]
CANONICAL_LABELS   dict[metric_id → human-readable label]
normalize_metric_value(metric_id, raw_value, unit) → float | None
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# ── Taxonomy ──────────────────────────────────────────────────────────────────

METRIC_GROUPS: dict[str, list[str]] = {
    "classification_accuracy": [
        "overall_accuracy", "f1_score", "iou", "kappa",
        "precision", "recall", "specificity", "auc",
        "mcc", "balanced_accuracy",
    ],
    "flood_detection_confusion": [
        "pod",               # Probability of Detection
        "far",               # False Alarm Ratio
        "csi",               # Critical Success Index
        "hit_rate",
        "false_positive_rate",
        "false_negative_rate",
        "omission_error",
        "commission_error",
    ],
    "hydrological_modeling": [
        "rmse", "mae", "nse", "kge",
        "r2", "bias", "pbias", "correlation",
    ],
    "spatial_agreement": [
        "area_agreement",
        "flood_extent_area",
        "overlap_area",
        "intersection_area",
        "mapped_flood_area",
        "reference_flood_area",
    ],
    "dem_topography": [
        "vertical_rmse",
        "horizontal_resolution",
        "dem_resolution",
        "elevation_error",
        "slope_error",
        "hand_threshold",
    ],
    "operational_timeliness": [
        "latency_hours",
        "processing_time",
        "revisit_time",
        "acquisition_delay",
        "delivery_time",
        "time_to_product",
    ],
    "sensor_resolution": [
        "spatial_resolution",
        "temporal_resolution",
        "revisit_frequency",
        "swath_width",
    ],
    "hydrological_parameter": [
        "scs_cn",           # SCS Curve Number
        "manning_n",        # Manning's roughness coefficient
        "initial_loss",     # Initial abstraction loss
        "cn_value",         # Curve Number (alias)
    ],
}

# Reverse map: metric_id → group_name
METRIC_TO_GROUP: dict[str, str] = {
    metric: group
    for group, metrics in METRIC_GROUPS.items()
    for metric in metrics
}

# Human-readable labels
CANONICAL_LABELS: dict[str, str] = {
    "overall_accuracy":    "Overall Accuracy",
    "f1_score":            "F1-score",
    "iou":                 "IoU (Intersection over Union)",
    "kappa":               "Cohen's Kappa",
    "precision":           "Precision",
    "recall":              "Recall",
    "specificity":         "Specificity",
    "auc":                 "AUC-ROC",
    "mcc":                 "Matthews Correlation Coefficient",
    "balanced_accuracy":   "Balanced Accuracy",
    "pod":                 "Probability of Detection",
    "far":                 "False Alarm Ratio",
    "csi":                 "Critical Success Index",
    "hit_rate":            "Hit Rate",
    "false_positive_rate": "False Positive Rate",
    "false_negative_rate": "False Negative Rate",
    "omission_error":      "Omission Error",
    "commission_error":    "Commission Error",
    "rmse":                "RMSE",
    "mae":                 "MAE",
    "nse":                 "Nash-Sutcliffe Efficiency",
    "kge":                 "Kling-Gupta Efficiency",
    "r2":                  "R²",
    "bias":                "Bias",
    "pbias":               "Percent Bias",
    "correlation":         "Correlation Coefficient",
    "area_agreement":      "Area Agreement",
    "flood_extent_area":   "Flood Extent Area (km²)",
    "overlap_area":        "Overlap Area (km²)",
    "intersection_area":   "Intersection Area (km²)",
    "mapped_flood_area":   "Mapped Flood Area (km²)",
    "reference_flood_area":"Reference Flood Area (km²)",
    "vertical_rmse":       "Vertical RMSE",
    "horizontal_resolution":"Horizontal Resolution",
    "dem_resolution":      "DEM Resolution",
    "elevation_error":     "Elevation Error",
    "slope_error":         "Slope Error",
    "hand_threshold":      "HAND Threshold",
    "latency_hours":       "Latency (hours)",
    "processing_time":     "Processing Time",
    "revisit_time":        "Revisit Time",
    "acquisition_delay":   "Acquisition Delay",
    "delivery_time":       "Delivery Time",
    "time_to_product":     "Time-to-Product",
    "spatial_resolution":  "Spatial Resolution",
    "temporal_resolution": "Temporal Resolution",
    "revisit_frequency":   "Revisit Frequency",
    "swath_width":         "Swath Width",
    # hydrological model parameters
    "scs_cn":              "SCS Curve Number",
    "cn_value":            "SCS Curve Number",
    "manning_n":           "Manning's n",
    "initial_loss":        "Initial Loss",
}

# Metrics that live strictly on [0, 1] (or [0, 100%]) — normalised to [0, 1].
# НАУКОВА ПРИМІТКА (Фаза 2.1): kappa, mcc, r2, correlation, bias видалені
# звідси — вони МОЖУТЬ бути від'ємними; стара класифікація мовчки дропала
# від'ємні значення (systematic optimism bias у корпусі).
RATIO_METRICS: frozenset[str] = frozenset({
    "overall_accuracy", "f1_score", "iou",
    "precision", "recall", "specificity", "auc", "balanced_accuracy",
    "pod", "far", "csi", "hit_rate",
    "false_positive_rate", "false_negative_rate",
    "omission_error", "commission_error",
    "area_agreement",
})

# Знакові обмежені метрики: [-1, 1]
SIGNED_RATIO_METRICS: frozenset[str] = frozenset({
    "kappa", "mcc", "correlation",
})

# Efficiency-скори: (−∞, 1]. NSE/KGE < 0 — валідний результат
# ("модель гірша за середнє спостережень"), його НЕ МОЖНА відкидати.
EFFICIENCY_METRICS: frozenset[str] = frozenset({
    "nse", "kge", "r2",
})

# Знакові необмежені: знак несе зміст (напрям систематичної похибки)
SIGNED_UNBOUNDED_METRICS: frozenset[str] = frozenset({
    "bias", "pbias",
})


# ── Валідні діапазони (наукова семантика кожної метрики) ─────────────────────

@dataclass(frozen=True)
class MetricRange:
    lo: float
    hi: float
    percent_form: bool = False   # чи буває звітування у формі 0–100 %


_INF = float("inf")

METRIC_RANGES: dict[str, MetricRange] = {
    # [0, 1] ratios (відсоткова форма звичайна)
    **{m: MetricRange(0.0, 1.0, percent_form=True) for m in RATIO_METRICS},
    # [-1, 1]
    "kappa":       MetricRange(-1.0, 1.0, percent_form=True),
    "mcc":         MetricRange(-1.0, 1.0),
    "correlation": MetricRange(-1.0, 1.0),
    # (−∞, 1]. percent_form=False для NSE/KGE: відсоткове звітування рідкісне,
    # а "NSE = 1.7" — найімовірніше помилка екстракції; ділити її на 100
    # означало б фабрикувати значення. R² у відсотках ("R² = 87") — поширений.
    "nse": MetricRange(-_INF, 1.0),
    "kge": MetricRange(-_INF, 1.0),
    "r2":  MetricRange(-_INF, 1.0, percent_form=True),
    # знакові необмежені
    "bias":  MetricRange(-_INF, _INF),
    "pbias": MetricRange(-_INF, _INF),   # вже у %; не масштабувати
    # невід'ємні величини (фізичні)
    **{m: MetricRange(0.0, _INF) for m in (
        "rmse", "mae", "vertical_rmse", "elevation_error", "slope_error",
        "latency_hours", "processing_time", "revisit_time",
        "acquisition_delay", "delivery_time", "time_to_product",
        "spatial_resolution", "temporal_resolution", "revisit_frequency",
        "swath_width", "flood_extent_area", "overlap_area",
        "intersection_area", "mapped_flood_area", "reference_flood_area",
        "dem_resolution", "horizontal_resolution", "hand_threshold",
    )},
}


def validate_metric_value(metric_id: str, value: float) -> str:
    """
    Перевірити значення проти наукового діапазону метрики.

    Returns: "ok" | "suspect" | "unknown_metric".
    Політика Фази 2.1: out-of-range значення НЕ відкидаються мовчки —
    вони позначаються "suspect" і лишаються видимими для аудиту.
    """
    rng = METRIC_RANGES.get(_strip_prefix(metric_id))
    if rng is None:
        return "unknown_metric"
    return "ok" if rng.lo <= value <= rng.hi else "suspect"

# Ontology-ID aliases (canonical_id from SODB → normalised metric_id)
SODB_CANONICAL_MAP: dict[str, str] = {
    "metric.oa":           "overall_accuracy",
    "metric.f1":           "f1_score",
    "metric.f1_score":     "f1_score",
    "metric.iou":          "iou",
    "metric.kappa":        "kappa",
    "metric.precision":    "precision",
    "metric.recall":       "recall",
    "metric.rmse":         "rmse",
    "metric.mae":          "mae",
    "metric.nse":          "nse",
    "metric.kge":          "kge",
    "metric.r2":           "r2",
    "metric.pbias":        "pbias",
    "metric.pod":          "pod",
    "metric.far":          "far",
    "metric.csi":          "csi",
}


# ── Fact taxonomy ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class FactSpec:
    """
    Type specification for a SODB canonical ID.

    fact_type: "evaluation_metric" | "model_parameter" | "operational_parameter"
    group:     metric_group or "hydrological_parameter"
    label:     human-readable label
    unit_hint: expected unit string (informational only)
    """
    fact_type:  str
    group:      str
    label:      str
    unit_hint:  str = ""


FACT_CANONICAL_MAP: dict[str, FactSpec] = {
    # ── Evaluation metrics (classification accuracy) ──────────────────────────
    "metric.oa":            FactSpec("evaluation_metric", "classification_accuracy",   "Overall Accuracy"),
    "metric.f1":            FactSpec("evaluation_metric", "classification_accuracy",   "F1-score"),
    "metric.f1_score":      FactSpec("evaluation_metric", "classification_accuracy",   "F1-score"),
    "metric.iou":           FactSpec("evaluation_metric", "classification_accuracy",   "IoU"),
    "metric.kappa":         FactSpec("evaluation_metric", "classification_accuracy",   "Cohen's Kappa"),
    "metric.precision":     FactSpec("evaluation_metric", "classification_accuracy",   "Precision"),
    "metric.recall":        FactSpec("evaluation_metric", "classification_accuracy",   "Recall"),
    "metric.specificity":   FactSpec("evaluation_metric", "classification_accuracy",   "Specificity"),
    "metric.auc":           FactSpec("evaluation_metric", "classification_accuracy",   "AUC-ROC"),
    # ── Evaluation metrics (flood detection confusion) ────────────────────────
    "metric.pod":           FactSpec("evaluation_metric", "flood_detection_confusion", "Probability of Detection"),
    "metric.far":           FactSpec("evaluation_metric", "flood_detection_confusion", "False Alarm Ratio"),
    "metric.csi":           FactSpec("evaluation_metric", "flood_detection_confusion", "Critical Success Index"),
    # ── Evaluation metrics (hydrological modelling) ───────────────────────────
    "metric.rmse":          FactSpec("evaluation_metric", "hydrological_modeling",     "RMSE"),
    "metric.mae":           FactSpec("evaluation_metric", "hydrological_modeling",     "MAE"),
    "metric.nse":           FactSpec("evaluation_metric", "hydrological_modeling",     "Nash-Sutcliffe Efficiency"),
    "metric.kge":           FactSpec("evaluation_metric", "hydrological_modeling",     "Kling-Gupta Efficiency"),
    "metric.r2":            FactSpec("evaluation_metric", "hydrological_modeling",     "R²"),
    "metric.pbias":         FactSpec("evaluation_metric", "hydrological_modeling",     "Percent Bias"),
    # ── Model parameters (hydrological) ──────────────────────────────────────
    # Values are on domain-specific scales, NOT ratio metrics — never /100
    "method.scs_cn":        FactSpec("model_parameter",   "hydrological_parameter",   "SCS Curve Number",     "dimensionless 0–100"),
    "method.cn":            FactSpec("model_parameter",   "hydrological_parameter",   "SCS Curve Number",     "dimensionless 0–100"),
    "method.manning_n":     FactSpec("model_parameter",   "hydrological_parameter",   "Manning's n",          "s/m^(1/3)"),
    "method.initial_loss":  FactSpec("model_parameter",   "hydrological_parameter",   "Initial Loss",         "mm"),
    "method.ia":            FactSpec("model_parameter",   "hydrological_parameter",   "Initial Abstraction",  "mm"),
    "method.retention":     FactSpec("model_parameter",   "hydrological_parameter",   "Maximum Retention",    "mm"),
}


def resolve_sodb_fact(canonical_id: str) -> FactSpec | None:
    """
    Look up the FactSpec for a SODB canonical_id.

    Returns None for unrecognised IDs (not an error — many are simply outside
    the current taxonomy).  The caller should log.debug on None, not log.warning.
    """
    return FACT_CANONICAL_MAP.get((canonical_id or "").lower())


# ── Normalisation ─────────────────────────────────────────────────────────────

def _normalize_ratio(raw_value) -> float | None:
    """
    Convert a ratio/accuracy value to [0, 1] scale.

    Accepts:
      - Already normalised float: 0.972 → 0.972
      - Percent string/float:     97.2  → 0.972
    Returns None if the value is outside both ranges or unparseable.
    """
    try:
        val = float(str(raw_value).strip().rstrip("%"))
    except (TypeError, ValueError):
        return None
    if 0.0 <= val <= 1.0:
        return round(val, 4)
    if 1.0 < val <= 100.0:
        return round(val / 100.0, 4)
    return None


def normalize_metric_value(
    metric_id: str,
    raw_value,
    unit: str = "",
) -> float | None:
    """
    Normalise *raw_value* according to the metric's scientific range.

    - RATIO [0,1]: percent input (1,100] → /100
    - SIGNED RATIO [-1,1] (kappa/mcc/correlation): від'ємні валідні;
      відсоткова форма (±(1,100]) → /100
    - EFFICIENCY (−∞,1] (nse/kge/r2): від'ємні валідні, ніколи не дропаються;
      (1,100] → /100 (відсоткове звітування)
    - signed unbounded (bias/pbias): pass-through зі знаком
    - magnitudes [0,∞): від'ємні → None
    Returns None лише якщо значення непарсибельне або поза всіма
    інтерпретаціями діапазону.
    """
    norm_id = _strip_prefix(metric_id)
    try:
        val = float(str(raw_value).strip().rstrip("%"))
    except (TypeError, ValueError):
        return None

    rng = METRIC_RANGES.get(norm_id)
    if rng is None:
        # Невідома метрика — pass-through (рішення про suspect у викликача)
        return round(val, 4)

    if rng.lo <= val <= rng.hi:
        return round(val, 4)

    # Відсоткова форма: |val| ∈ (1, 100] → /100, якщо результат у діапазоні
    if rng.percent_form and 1.0 < abs(val) <= 100.0:
        scaled = val / 100.0
        if rng.lo <= scaled <= rng.hi:
            return round(scaled, 4)

    return None


def normalize_fact_value(
    raw_value: object,
    spec: "FactSpec | None",
    canonical_id: str,
) -> float | None:
    """
    Normalise *raw_value* based on the FactSpec type, NOT just the metric scale.

    Critical invariant: **call resolve_sodb_fact() first**, then this function.
    Never call normalize_metric_value() directly for SODB facts — it lacks
    fact_type context and would wrongly scale model parameters like SCS-CN.

    Rules:
    - model_parameter / operational_parameter → return raw float as-is (no /100)
      e.g. SCS-CN = 49.3 stays 49.3; Manning's n = 0.035 stays 0.035
    - evaluation_metric in RATIO_METRICS → normalise to [0, 1]:
      0.0–1.0 → as-is;  1.0–100.0 → /100  (e.g. F1 = 97.2 → 0.972)
    - evaluation_metric not in RATIO_METRICS → pass through (RMSE, NSE, etc.)
    - spec is None (unknown canonical_id) → pass raw float through unchanged
    """
    try:
        val = float(raw_value)
    except (TypeError, ValueError):
        return None

    if spec is None:
        # Unknown canonical ID — return raw value; caller emits debug log
        return val

    if spec.fact_type in ("model_parameter", "operational_parameter"):
        # Never scale — domain values have their own range (SCS-CN ∈ [0,100])
        return val

    # evaluation_metric — діапазонна нормалізація (Фаза 2.1: знакові метрики
    # kappa/NSE/KGE/r2 більше не дропаються при val < 0)
    return normalize_metric_value(canonical_id, val)


# ── Канонізація одиниць (Фаза 2.3) ────────────────────────────────────────────

# Канонічні форми; ключі — lower-case без пробілів
_UNIT_CANONICAL: dict[str, str] = {
    "m": "m", "meter": "m", "meters": "m",
    "cm": "cm", "mm": "mm", "km": "km", "ft": "ft", "feet": "ft",
    "m3/s": "m³/s", "m^3/s": "m³/s", "m³/s": "m³/s", "cms": "m³/s", "cumecs": "m³/s",
    "mm/day": "mm/day", "mm/d": "mm/day",
    "mm/h": "mm/h", "mm/hr": "mm/h",
    "cm/day": "cm/day", "cm/d": "cm/day",
    "masl": "m a.s.l.", "ma.s.l.": "m a.s.l.",
    "%": "%",
    "-": "dimensionless",
}

UNIT_UNKNOWN = "unknown"


def normalize_unit(raw_unit: str | None) -> str:
    """
    Канонічна форма одиниці виміру.

    Політика Фази 2.3: відсутня одиниця — це ЯВНИЙ стан "unknown", а не
    порожній рядок. Факти з unit=unknown не можна порівнювати крос-paper
    із розмірними (RMSE 0.45 m ≠ RMSE 0.45 [?]).
    """
    if raw_unit is None or not str(raw_unit).strip():
        return UNIT_UNKNOWN
    key = str(raw_unit).strip().lower().replace(" ", "")
    return _UNIT_CANONICAL.get(key, str(raw_unit).strip())


def units_comparable(unit_a: str | None, unit_b: str | None) -> bool:
    """Чи можна порівнювати два факти за одиницями (unknown — ніколи)."""
    a, b = normalize_unit(unit_a), normalize_unit(unit_b)
    if UNIT_UNKNOWN in (a, b):
        return False
    return a == b


def metric_scale(metric_id: str) -> str:
    """Шкала метрики: '0_1' | 'signed_ratio' | 'efficiency' | 'physical'."""
    norm_id = _strip_prefix(metric_id)
    if norm_id in RATIO_METRICS:
        return "0_1"
    if norm_id in SIGNED_RATIO_METRICS:
        return "signed_ratio"
    if norm_id in EFFICIENCY_METRICS:
        return "efficiency"
    return "physical"


def metric_label(metric_id: str) -> str:
    """Return human-readable label for a metric_id."""
    norm_id = _strip_prefix(metric_id)
    return CANONICAL_LABELS.get(norm_id, norm_id.replace("_", " ").title())


def metric_group(metric_id: str) -> str:
    """Return group name for a metric_id, or 'unknown'."""
    norm_id = _strip_prefix(metric_id)
    return METRIC_TO_GROUP.get(norm_id, "unknown")


def resolve_sodb_canonical(canonical_id: str) -> str | None:
    """
    Map a SODB canonical_id (e.g. 'metric.f1') to a normalised metric_id
    (e.g. 'f1_score').  Returns None if unrecognised.
    """
    return SODB_CANONICAL_MAP.get(canonical_id.lower())


def _strip_prefix(metric_id: str) -> str:
    """Remove 'metric.' prefix if present."""
    s = (metric_id or "").lower().strip()
    return s[len("metric."):] if s.startswith("metric.") else s
