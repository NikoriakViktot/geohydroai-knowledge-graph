"""Regression tests for the 2026-10-03 graph audit fixes.

* KB patterns: acronyms keep their case and never match inside words
* table cells with several numbers are not glued into one value
* stub merge key and NaN-safe Paper properties
"""
from __future__ import annotations

import math
import re

import pytest

from src.extraction.table_extractor import _cell_values, _map_all, _map_header_cells, _scaled
from src.graph.graph_loader import _clean_str, title_key
from src.ingestion.knowledge.knowledge_loader import _acronym_alt, _build_pattern, _name_alt


def _hits(pattern: str, text: str) -> bool:
    return re.search(pattern, text, re.IGNORECASE) is not None


# ── knowledge-base patterns ───────────────────────────────────────────────────

@pytest.mark.parametrize("acronym, text, expected", [
    ("HAND",  "The HAND method was applied",     True),
    ("HAND",  "on the other hand, the model",    False),
    ("ET",    "ET was estimated from MODIS",     True),
    ("ET",    "Smith et al. (2010) showed",      False),
    ("iRIC",  "we used the iRIC model",          True),
    ("iRIC",  "IRIC was applied",                True),
    ("iRIC",  "an empirical relationship",       False),
    ("SWAT",  "the SWAT model",                  True),
    ("SWAT",  "the Swat river basin",            False),
    ("ICESAT", "ICESat-2 ATL08 data",            True),   # long names stay case-free
    ("LANDSAT", "landsat 8 imagery",             True),
])
def test_acronym_alt_case_and_boundaries(acronym, text, expected):
    assert _hits(_acronym_alt(acronym), text) is expected


def test_build_pattern_hand_override_is_case_sensitive():
    pat = _build_pattern("HAND", "Height Above Nearest Drainage")
    assert _hits(pat, "height above nearest drainage")
    assert not _hits(pat, "on the other hand")


@pytest.mark.parametrize("name, text, expected", [
    ("artificial_neural_network", "artificial neural networks were trained", True),
    ("one_dimensional_hydrodynamic_model", "a one-dimensional hydrodynamic model", True),
    ("arima", "a varimax rotation", False),
    ("elman", "Asselman (2000)", False),
])
def test_name_alt(name, text, expected):
    assert _hits(_name_alt(name.replace("_", " ")), text) is expected


# ── table cells ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw, n, expected", [
    ("0.91", 1, [0.91]),
    ("−0.27", 1, [-0.27]),
    ("94.2%", 1, [94.2]),
    ("10 569", 1, [10569.0]),            # thousands grouping
    ("1 234 567.5", 1, [1234567.5]),
    ("0 .0 0 0136", 1, [0.000136]),      # digits broken by GROBID
    ("0.5 0.7", 2, [0.5, 0.7]),          # one value per metric of a merged header
    ("34 18 19 25", 1, None),            # was 34181925.0
    ("6605 10 569", 2, None),            # ambiguous: 3 tokens, 2 metrics
    ("0 1", 1, None),
    ("0.84 259", 1, None),
    ("", 1, None),
    ("n/a", 1, None),
])
def test_cell_values(raw, n, expected):
    assert _cell_values(raw, n) == expected


def test_map_all_keeps_header_order_and_dedups():
    assert [c for c, _, _ in _map_all("MAE RMSE (%)")] == ["metric.mae", "metric.rmse"]
    assert [c for c, _, _ in _map_all("Nash-Sutcliffe efficiency (NSE)")] == ["metric.nse"]
    assert _map_all("Station") == []


def test_map_header_cells_uses_lowest_header_row():
    cids = [c for c, _, _ in _map_header_cells(["Accuracy metrics", "Kappa"], "Accuracy metrics Kappa")]
    assert cids == ["metric.kappa"]


def test_percent_scaling_only_with_a_percent_hint():
    assert _scaled("metric.overall_accuracy", 94.2, True) == pytest.approx(0.942)
    assert _scaled("metric.overall_accuracy", 94.2, False) == 94.2
    assert _scaled("metric.nse", 94.2, True) == 94.2          # NSE is never a percentage


# ── graph loader ──────────────────────────────────────────────────────────────

def test_clean_str_drops_nan_and_blank():
    assert _clean_str(math.nan) is None
    assert _clean_str("nan") is None
    assert _clean_str("  ") is None
    assert _clean_str(" Flood mapping ") == "Flood mapping"


def test_title_key_merges_case_punctuation_and_rejects_short_titles():
    a = title_key("Flood Mapping with Sentinel-1: A Review")
    b = title_key("flood mapping with sentinel–1 — a review.")
    assert a == b == "flood mapping with sentinel 1 a review"
    assert title_key("Introduction") is None
    assert title_key(math.nan) is None
