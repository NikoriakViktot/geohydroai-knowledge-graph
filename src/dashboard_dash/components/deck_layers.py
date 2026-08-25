"""
deck_layers.py  —  DeckGL layer data builders for the geospatial page
"""
from __future__ import annotations

import math
import random

import pandas as pd

from src.dashboard_dash.theme import COUNTRY_CENTROIDS, COLORS


# ── Colour helpers ─────────────────────────────────────────────────────────────

def _cyan_rgba(t: float, base_alpha: int = 120) -> list[int]:
    """Cyan gradient RGBA from t∈[0,1]. Low t = dark blue, high t = bright cyan."""
    t = min(max(t, 0.0), 1.0)
    r = 0
    g = int(80  + t * (212 - 80))
    b = int(140 + t * (255 - 140))
    a = int(base_alpha + t * (255 - base_alpha))
    return [r, g, b, a]


def _green_rgba(t: float, base_alpha: int = 120) -> list[int]:
    """Teal-green gradient RGBA from t∈[0,1]."""
    t = min(max(t, 0.0), 1.0)
    r = 0
    g = int(100 + t * (185 - 100))
    b = int(80  + t * (129 - 80))
    a = int(base_alpha + t * (255 - base_alpha))
    return [r, g, b, a]


def _log_t(value: float, max_val: float) -> float:
    """Logarithmic normalisation for better visual balance across magnitudes."""
    if max_val <= 0:
        return 0.0
    return math.log1p(value) / math.log1p(max_val)


# ── ScatterplotLayer data ──────────────────────────────────────────────────────

def country_scatter_data(country_df: pd.DataFrame) -> list[dict]:
    """
    Build ScatterplotLayer + glow ring features from country paper counts.
    Uses log-scaling so small countries remain visible next to dominant ones.
    """
    rows = []
    if country_df.empty:
        return rows
    max_papers = country_df["papers"].max()
    for _, row in country_df.iterrows():
        coord = COUNTRY_CENTROIDS.get(str(row["country"]))
        if coord is None:
            continue
        t      = _log_t(row["papers"], max_papers)
        radius = int(60_000 + t * 380_000)
        rows.append({
            "position":      coord,
            "radius":        radius,
            "glow_radius":   int(radius * 0.55),
            "color":         _cyan_rgba(t, base_alpha=140),
            "glow_color":    [0, 212, 255, int(35 + t * 55)],
            "line_color":    [0, 212, 255, int(60 + t * 120)],
            "country":       str(row["country"]),
            "papers":        int(row["papers"]),
            "avg_citations": round(float(row.get("avg_citations") or 0), 1),
        })
    return rows


# ── HexagonLayer data ─────────────────────────────────────────────────────────

def country_hex_data(country_df: pd.DataFrame) -> list[dict]:
    """
    Expand country centroids to jittered lon/lat points for HexagonLayer density.
    Cap per-country count to keep total point count manageable.
    """
    points = []
    if country_df.empty:
        return points
    for _, row in country_df.iterrows():
        coord = COUNTRY_CENTROIDS.get(str(row["country"]))
        if coord is None:
            continue
        count = min(int(row["papers"]), 60)
        rng   = random.Random(hash(str(row["country"])))
        for _ in range(count):
            points.append({
                "lon": coord[0] + rng.uniform(-2.0, 2.0),
                "lat": coord[1] + rng.uniform(-1.2, 1.2),
            })
    return points


# ── TextLayer data ────────────────────────────────────────────────────────────

def country_text_data(country_df: pd.DataFrame, min_papers: int = 20) -> list[dict]:
    """Country name labels with paper count for TextLayer."""
    rows = []
    for _, row in country_df.iterrows():
        if row["papers"] < min_papers:
            continue
        coord = COUNTRY_CENTROIDS.get(str(row["country"]))
        if coord is None:
            continue
        rows.append({
            "position": [coord[0], coord[1], 80_000],
            "text":     str(row["country"]),
            "papers":   int(row["papers"]),
        })
    return rows


# ── Institution scatter ───────────────────────────────────────────────────────

_COUNTRY_CODE_TO_NAME: dict[str, str] = {
    "US": "USA",           "IN": "India",         "CN": "China",
    "UA": "Ukraine",       "DE": "Germany",       "IT": "Italy",
    "GB": "UK",            "FR": "France",        "ID": "Indonesia",
    "IR": "Iran",          "AU": "Australia",     "BR": "Brazil",
    "CA": "Canada",        "ES": "Spain",         "PK": "Pakistan",
    "NL": "Netherlands",   "BD": "Bangladesh",    "TR": "Turkey",
    "JP": "Japan",         "ET": "Ethiopia",      "NG": "Nigeria",
    "MX": "Mexico",        "KR": "South Korea",   "SE": "Sweden",
    "CH": "Switzerland",   "PL": "Poland",        "BE": "Belgium",
    "PT": "Portugal",      "DK": "Denmark",       "NO": "Norway",
    "FI": "Finland",       "AT": "Austria",       "CZ": "Czech Republic",
    "RO": "Romania",       "GR": "Greece",        "HU": "Hungary",
    "ZA": "South Africa",  "NZ": "New Zealand",   "RU": "Russia",
    "AR": "Argentina",     "CO": "Colombia",      "PE": "Peru",
    "CL": "Chile",         "TH": "Thailand",      "VN": "Vietnam",
    "MY": "Malaysia",      "PH": "Philippines",   "MA": "Morocco",
    "EG": "Egypt",         "KE": "Kenya",         "TZ": "Tanzania",
    "GH": "Ghana",         "DZ": "Algeria",       "TN": "Tunisia",
    "SA": "Saudi Arabia",  "IQ": "Iraq",          "JO": "Jordan",
    "IL": "Israel",        "AF": "Afghanistan",   "KZ": "Kazakhstan",
}


def institution_scatter_data(inst_df: pd.DataFrame) -> list[dict]:
    """Institution scatter points positioned at jittered country centroids."""
    rows = []
    if inst_df.empty:
        return rows
    max_count = inst_df["paper_count"].max() if "paper_count" in inst_df.columns else 1
    for _, row in inst_df.iterrows():
        code    = str(row.get("country_code", ""))
        country = _COUNTRY_CODE_TO_NAME.get(code, code)
        coord   = COUNTRY_CENTROIDS.get(country)
        if coord is None:
            continue
        rng = random.Random(hash(str(row.get("institution_id", ""))))
        t   = _log_t(row["paper_count"], max_count)
        rows.append({
            "position":     [coord[0] + rng.uniform(-3.5, 3.5),
                             coord[1] + rng.uniform(-2.5, 2.5)],
            "radius":       int(20_000 + t * 180_000),
            "color":        _green_rgba(t, base_alpha=130),
            "line_color":   [16, 185, 129, int(80 + t * 100)],
            "name":         str(row.get("display_name", "")),
            "paper_count":  int(row["paper_count"]),
            "country_code": code,
        })
    return rows
