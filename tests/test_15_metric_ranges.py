"""
test_15_metric_ranges.py — наукові діапазони метрик (Фаза 2.1 remediation).

Фіксує виправлення CRITICAL-бага F-EXT-1: NSE ∈ (−∞, 1] (від'ємний NSE =
"модель гірша за середнє спостережень" — валідний результат), Kappa/MCC ∈
[−1, 1], PBIAS — знаковий. Раніше від'ємні значення мовчки дропались →
систематичний optimism bias у корпусі.
"""
from __future__ import annotations

import re

import pytest

from src.extraction.metric_ontology import (
    METRIC_RANGES,
    RATIO_METRICS,
    SIGNED_RATIO_METRICS,
    EFFICIENCY_METRICS,
    metric_scale,
    normalize_metric_value,
    validate_metric_value,
)
from src.extraction.regex_extractor import (
    _extract_metric,
    KAPPA_PATTERNS, NSE_PATTERNS, KGE_PATTERNS, R2_PATTERNS, OA_PATTERNS,
)
from src.ingestion.knowledge.entity_extractor import METRIC_PATTERNS

NEG_INF = float("-inf")


# ── Онтологія: членство в класах ──────────────────────────────────────────────

def test_signed_metrics_not_in_strict_ratio():
    """kappa/mcc/r2/correlation МОЖУТЬ бути від'ємними — не в [0,1] ratio."""
    for m in ("kappa", "mcc", "r2", "correlation", "bias", "nse", "kge"):
        assert m not in RATIO_METRICS, f"{m} не повинен бути у RATIO_METRICS"


def test_class_partition():
    assert "kappa" in SIGNED_RATIO_METRICS
    assert {"nse", "kge", "r2"} <= EFFICIENCY_METRICS
    assert metric_scale("kappa") == "signed_ratio"
    assert metric_scale("nse") == "efficiency"
    assert metric_scale("f1_score") == "0_1"
    assert metric_scale("rmse") == "physical"


def test_ranges_scientifically_correct():
    assert METRIC_RANGES["nse"].lo == NEG_INF and METRIC_RANGES["nse"].hi == 1.0
    assert METRIC_RANGES["kge"].lo == NEG_INF
    assert METRIC_RANGES["kappa"].lo == -1.0 and METRIC_RANGES["kappa"].hi == 1.0
    assert METRIC_RANGES["mcc"].lo == -1.0
    assert METRIC_RANGES["pbias"].lo == NEG_INF and METRIC_RANGES["pbias"].hi == float("inf")


# ── Нормалізація ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("metric_id,raw,expected", [
    ("nse",   -0.27,  -0.27),   # ГОЛОВНИЙ КЕЙС: від'ємний NSE зберігається
    ("nse",    0.78,   0.78),
    ("kge",   -0.41,  -0.41),
    ("kappa", -0.12,  -0.12),
    ("kappa",  85,     0.85),   # відсоткова форма
    ("r2",     87,     0.87),   # R² у відсотках — поширене звітування
    ("r2",    -0.05,  -0.05),   # від'ємний R² (out-of-sample) валідний
    ("pbias", -12.5,  -12.5),   # знак = напрям систематичної похибки
    ("f1_score", 92.5, 0.925),
    ("nse",    1.7,    None),   # неможливе значення — НЕ "рятувати" як 1.7%
    ("f1_score", -0.3, None),   # від'ємний F1 неможливий
])
def test_normalize_metric_value(metric_id, raw, expected):
    assert normalize_metric_value(metric_id, raw) == expected


@pytest.mark.parametrize("metric_id,value,verdict", [
    ("nse",  -0.27, "ok"),
    ("nse",   1.7,  "suspect"),
    ("kappa", -0.5, "ok"),
    ("kappa", -1.5, "suspect"),
    ("rmse",  -2.0, "suspect"),
    ("unknown_xyz", 5.0, "unknown_metric"),
])
def test_validate_metric_value(metric_id, value, verdict):
    assert validate_metric_value(metric_id, value) == verdict


# ── Regex-екстракція (sign-aware) ─────────────────────────────────────────────

@pytest.mark.parametrize("text,patterns,lo,expected", [
    ("The model achieved NSE = -0.27 in validation.", NSE_PATTERNS, NEG_INF, -0.27),
    ("calibration NSE = 0.78",                        NSE_PATTERNS, NEG_INF,  0.78),
    ("Nash-Sutcliffe efficiency of -0.15",            NSE_PATTERNS, NEG_INF, -0.15),
    ("with KGE of -0.41",                             KGE_PATTERNS, NEG_INF, -0.41),
    ("Cohen's Kappa = -0.12 was obtained",            KAPPA_PATTERNS, -1.0,  -0.12),
    ("R2 = 0.91 for the test set",                    R2_PATTERNS,  NEG_INF,  0.91),
    ("overall accuracy of 0.94",                      OA_PATTERNS,   0.0,     0.94),
])
def test_regex_sign_aware_extraction(text, patterns, lo, expected):
    assert _extract_metric(text, patterns, lo=lo) == expected


def test_nse_no_longer_in_r2_patterns():
    """NSE — окрема метрика, не 'same 0–1 scale' як R² (старий баг)."""
    assert _extract_metric("NSE = 0.5", R2_PATTERNS, lo=NEG_INF) is None


def test_negative_oa_rejected():
    """Sign-aware _NUM не дозволяє від'ємним значенням просочитись у [0,1]-метрики."""
    assert _extract_metric("overall accuracy of -0.5", OA_PATTERNS) is None


# ── KB-шлях (entity_extractor METRIC_PATTERNS) ────────────────────────────────

@pytest.mark.parametrize("metric,text,expected_raw", [
    ("NSE",   "the NSE = -0.27 here",   "-0.27"),
    ("KGE",   "KGE of -0.41",           "-0.41"),
    ("PBIAS", "PBIAS = -12.5%",         "-12.5"),
    ("Kappa", "kappa coefficient -0.2", "-0.2"),
    ("R2",    "R2 = -0.05",             "-0.05"),
])
def test_kb_patterns_capture_sign(metric, text, expected_raw):
    m = re.search(METRIC_PATTERNS[metric], text, re.IGNORECASE)
    assert m is not None, f"{metric} pattern не матчить: {text}"
    groups = [g for g in m.groups() if g is not None]
    assert groups[-1] == expected_raw


# ── Одиниці виміру (Фаза 2.3) ─────────────────────────────────────────────────

from src.extraction.metric_ontology import normalize_unit, units_comparable, UNIT_UNKNOWN
from src.extraction.regex_extractor import RMSE_PATTERNS


@pytest.mark.parametrize("raw,canonical", [
    ("m", "m"), ("meters", "m"), ("cm", "cm"), ("km", "km"), ("ft", "ft"),
    ("m3/s", "m³/s"), ("cms", "m³/s"), ("cumecs", "m³/s"),
    ("mm/day", "mm/day"), ("mm/h", "mm/h"),
    (None, UNIT_UNKNOWN), ("", UNIT_UNKNOWN), ("-", "dimensionless"),
])
def test_normalize_unit(raw, canonical):
    assert normalize_unit(raw) == canonical


def test_units_comparable_policy():
    assert units_comparable("m", "meters")
    assert units_comparable("cms", "m3/s")
    assert not units_comparable("m", "cm")
    # unknown НІКОЛИ не порівнюється — навіть сам із собою
    assert not units_comparable(None, None)
    assert not units_comparable("m", None)


@pytest.mark.parametrize("text,expected_unit", [
    ("RMSE = 145 m3/s at the gauge", "m³/s"),
    ("RMSE = 3.2 mm/day", "mm/day"),
    ("RMSE = 2.1 km", "km"),
    ("RMSE = 0.38", UNIT_UNKNOWN),
])
def test_rmse_unit_extraction(text, expected_unit):
    from src.extraction.scientific_extractor import _scan_raw_metric
    val, _, unit = _scan_raw_metric(text, RMSE_PATTERNS)
    assert val is not None
    assert unit == expected_unit
