"""Metric numbers: Unicode minus and percent rescaling (API_PLAN_v1 О-8, О-28)."""

from __future__ import annotations

import pytest

from src.extraction import scientific_extractor as se
from src.extraction import table_extractor as te
from src.extraction.numbers import parse_number, to_ratio
from src.extraction.regex_extractor import (
    KAPPA_PATTERNS, KGE_PATTERNS, NSE_PATTERNS, OA_PATTERNS, _extract_metric,
)

NEG_INF = float("-inf")


@pytest.mark.parametrize("raw, expected", [
    ("−0.27", -0.27),       # U+2212 MINUS SIGN, as typeset by most journals
    ("-0.27", -0.27),
    ("0,85", 0.85),
    ("94.2 %", 94.2),
    ("1 234", 1234.0),      # thin space as thousands separator
])
def test_parse_number(raw, expected):
    assert parse_number(raw) == pytest.approx(expected)


def test_to_ratio_only_rescales_bounded_ratio_metrics():
    assert to_ratio(94.2, 0.0, 1.0) == pytest.approx(0.942)      # OA in percent
    assert to_ratio(85.0, -1.0, 1.0) == pytest.approx(0.85)      # kappa in percent
    assert to_ratio(1.7, NEG_INF, 1.0) == 1.7                     # NSE: an error, not a percentage
    assert to_ratio(250.0, 0.0, 1.0) == 250.0                     # not a percentage either


@pytest.mark.parametrize("text, patterns, lo, expected", [
    ("The model achieved NSE = −0.27 in validation.", NSE_PATTERNS, NEG_INF, -0.27),
    ("with KGE of −0.41", KGE_PATTERNS, NEG_INF, -0.41),
    ("Cohen's Kappa = −0.12 was obtained", KAPPA_PATTERNS, -1.0, -0.12),
    ("overall accuracy of 94.2%", OA_PATTERNS, 0.0, 0.942),
])
def test_regex_extractor_reads_unicode_minus_and_percent(text, patterns, lo, expected):
    assert _extract_metric(text, patterns, lo=lo) == pytest.approx(expected)


def test_regex_extractor_rejects_impossible_nse_instead_of_rescaling():
    assert _extract_metric("calibration NSE = 1.7", NSE_PATTERNS, lo=NEG_INF) is None


def test_scientific_extractor_rejects_impossible_nse_instead_of_rescaling():
    val, _ = se._scan_metric_with_snippet("The calibrated model gave NSE = 1.7.", NSE_PATTERNS, lo=NEG_INF)
    assert val is None


def test_scientific_extractor_keeps_negative_nse_with_unicode_minus():
    val, snippet = se._scan_metric_with_snippet("Validation NSE = −0.27 at the outlet.", NSE_PATTERNS, lo=NEG_INF)
    assert val == pytest.approx(-0.27)
    assert "NSE" in snippet


def test_table_cells_with_unicode_minus():
    assert te._parse_float("−0.27") == pytest.approx(-0.27)
    assert te._parse_float("0,91") == pytest.approx(0.91)
    assert te._parse_float("n/a") is None
