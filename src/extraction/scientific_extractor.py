"""
Section-aware scientific data extractor for flood mapping literature.

Public surface
──────────────
    extract_scientific_data(sections)  → (data_dict, provenance_dict)
    ScientificExtractor                → BaseExtractor adapter for the pipeline

Section-to-field mapping (STRICT — never relaxed)
──────────────────────────────────────────────────
    Satellite_Names  ← abstract + methods      (never results)
    Sensor_Type      ← derived from Satellite_Names
    Study_Type       ← abstract  (+title for review/dataset detection)
    Country          ← abstract + introduction  (Ukraine-first special rule)
    River_Basin      ← abstract + methods
    Methods          ← methods ONLY             (strict — never abstract)
    OA / F1 / IoU    ← results ONLY             (strict — validated in text)
    Near_Real_Time   ← abstract + methods

No LLM.  Numbers must appear literally in the source section.
All extracted values carry provenance: section + snippet + source.
"""
from __future__ import annotations

import logging
import re
from statistics import mean

from src.extraction.base import BaseExtractor, ExtractionResult
from src.extraction.regex_extractor import (
    OA_PATTERNS, F1_PATTERNS, IOU_PATTERNS, KAPPA_PATTERNS,
    RMSE_PATTERNS, MAE_PATTERNS, R2_PATTERNS,
    NSE_PATTERNS, KGE_PATTERNS,
    _UKRAINE_KW, _RIVER_BASINS, _KNOWN_COUNTRIES,
    _REVIEW_KW, _DATASET_KW, _HYDRO_FORE_KW, _HYDRAULIC_KW,
    _OPERATIONAL_KW, _DL_KW, _ML_KW,
    _METHODS_RULES,
)
from src.extraction.numbers import normalize_minus, parse_number, to_ratio
from src.extraction.section_parser import parse_document_sections, describe_parsed

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# 1. SATELLITE DETECTION
# ─────────────────────────────────────────────────────────────────────────────

_SAT_DEFS: list[tuple[str, str]] = [
    ("Sentinel-1",   r"sentinel[\s\-]?1[abc]?|\bs1[ab]?\b"),
    ("TerraSAR-X",   r"terrasar[\s\-]?x?|\btsx\b"),
    ("TanDEM-X",     r"tandem[\s\-]?x|\btdx\b"),
    ("COSMO-SkyMed", r"cosmo[\s\-]?skymed|\bcsk\b"),
    ("ALOS-2",       r"alos[\s\-]?2(?:\s*palsar)?|\bpalsar[\s\-]?2\b"),
    ("ALOS PALSAR",  r"\balos\s+palsar\b|\bpalsar\b"),
    ("RADARSAT-2",   r"radarsat[\s\-]?2"),
    ("RADARSAT-1",   r"radarsat[\s\-]?1"),
    ("UAVSAR",       r"\buavsar\b"),
    ("ICEYE",        r"\biceye\b"),
    ("Capella",      r"\bcapella\s+(?:space|sar)\b"),
    ("ERS-2",        r"\bers[\s\-]?2\b"),
    ("ERS-1",        r"\bers[\s\-]?1\b"),
    ("Envisat",      r"\benvisat\b"),
    ("NovaSAR",      r"\bnovasar\b"),
    # Optical
    ("Sentinel-2",   r"sentinel[\s\-]?2[abc]?|\bs2[ab]?\b"),
    ("Landsat-9",    r"landsat[\s\-]?9"),
    ("Landsat-8",    r"landsat[\s\-]?8"),
    ("Landsat-7",    r"landsat[\s\-]?7"),
    ("Landsat-5",    r"landsat[\s\-]?5"),
    ("Landsat",      r"\blandsat\b"),
    ("MODIS",        r"\bmodis\b"),
    ("VIIRS",        r"\bviirs\b"),
    ("WorldView-3",  r"worldview[\s\-]?3|\bwv[\s\-]?3\b"),
    ("WorldView-2",  r"worldview[\s\-]?2|\bwv[\s\-]?2\b"),
    ("Pleiades",     r"pl[eé]iades[\s\-]?\d?|\bphr\b"),
    ("PlanetScope",  r"\bplanetscope\b|\bplanet\s+labs?\b|\brapideye\b"),
    ("SPOT-7",       r"\bspot[\s\-]?7\b"),
    ("SPOT-6",       r"\bspot[\s\-]?6\b"),
    ("SPOT-5",       r"\bspot[\s\-]?5\b"),
    # LiDAR / altimetry
    ("ICESat-2",     r"\bicesat[\s\-]?2\b|\batl0[36]\b|\batl08\b"),
]

_SAT_PATTERNS: list[tuple[str, re.Pattern]] = [
    (name, re.compile(pat, re.IGNORECASE))
    for name, pat in _SAT_DEFS
]

_SAR_SATS: frozenset[str] = frozenset({
    "Sentinel-1", "TerraSAR-X", "TanDEM-X", "COSMO-SkyMed",
    "ALOS-2", "ALOS PALSAR", "RADARSAT-2", "RADARSAT-1",
    "UAVSAR", "ICEYE", "Capella", "ERS-2", "ERS-1", "Envisat", "NovaSAR",
})
_OPT_SATS: frozenset[str] = frozenset({
    "Sentinel-2", "Landsat-9", "Landsat-8", "Landsat-7", "Landsat-5", "Landsat",
    "MODIS", "VIIRS", "WorldView-3", "WorldView-2",
    "Pleiades", "PlanetScope", "SPOT-7", "SPOT-6", "SPOT-5",
})
_LIDAR_SATS: frozenset[str] = frozenset({"ICESat-2"})

_SAR_GENERIC_RE = re.compile(r"\bsar\b|\bsynthetic[\s\-]aperture\s+radar\b", re.IGNORECASE)
_OPT_GENERIC_RE = re.compile(r"\boptical\s+(?:satellite|imagery|sensor|data|image)\b", re.IGNORECASE)


# ─────────────────────────────────────────────────────────────────────────────
# 2. NEAR-REAL-TIME
# ─────────────────────────────────────────────────────────────────────────────

_NRT_RE = re.compile(
    r"near[\s\-]?real[\s\-]?time|\bnrt\b"
    r"|real[\s\-]time\s+flood|rapid\s+(?:flood\s+)?mapping"
    r"|operational\s+flood\s+monitoring|within\s+\d+\s*h(?:ours?)?",
    re.IGNORECASE,
)


# ─────────────────────────────────────────────────────────────────────────────
# 3. METRIC VALIDATION HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _literal_in_text(value: float, text: str) -> bool:
    """Return True if *value* appears literally in *text* in at least one form."""
    text = normalize_minus(text)  # "−0.27" (U+2212) is the same literal as "-0.27"
    for raw in [f"{value:.4f}", f"{value:.3f}", f"{value:.2f}",
                f"{value * 100:.2f}", f"{value * 100:.1f}", f"{value * 100:.0f}"]:
        cleaned = raw.rstrip("0").rstrip(".")
        if not cleaned:
            continue
        if re.search(r"(?<!\d)" + re.escape(cleaned) + r"(?!\d)", text):
            return True
    return False


def _parse_float(raw: str, lo: float = 0.0, hi: float = 1.0) -> float:
    """Parse a metric value; 0–100 → 0–1 only for bounded ratio metrics (OA, F1,
    IoU, kappa). NSE/KGE/R² (lo = -inf) are never rescaled: NSE 1.7 stays 1.7
    and is rejected by the range check instead of becoming 0.017."""
    return round(to_ratio(parse_number(raw), lo, hi), 4)


def _snippet(text: str, start: int, end: int, context: int = 80) -> str:
    """Extract a cleaned context snippet around a match span."""
    s = max(0, start - context)
    e = min(len(text), end + context)
    return text[s:e].replace("\n", " ").strip()


# ─────────────────────────────────────────────────────────────────────────────
# 4. SECTION-SPECIFIC EXTRACTORS WITH PROVENANCE
# ─────────────────────────────────────────────────────────────────────────────

def _extract_satellites(
    abstract: str, methods: str
) -> tuple[list[str], str]:
    """
    Detect canonical satellite names from abstract + methods.
    Returns (names, first_snippet).
    """
    search = f"{abstract} {methods}"
    found: list[str] = []
    seen:  set[str]  = set()
    first_snippet    = ""

    for canonical, pat in _SAT_PATTERNS:
        m = pat.search(search)
        if m and canonical not in seen:
            found.append(canonical)
            seen.add(canonical)
            if not first_snippet:
                first_snippet = _snippet(search, m.start(), m.end())

    versioned_bases = {s.split("-")[0] for s in found if "-" in s and not s.startswith("ERS")}
    found = [s for s in found if s not in versioned_bases]

    return found, first_snippet


def _derive_sensor_type(satellite_names: list[str], search_text: str = "") -> str:
    has_sar   = any(s in _SAR_SATS for s in satellite_names)
    has_opt   = any(s in _OPT_SATS for s in satellite_names)
    has_lidar = any(s in _LIDAR_SATS for s in satellite_names)

    if not satellite_names:
        has_sar = bool(_SAR_GENERIC_RE.search(search_text))
        has_opt = bool(_OPT_GENERIC_RE.search(search_text))

    types = sum([has_sar, has_opt, has_lidar])
    if types > 1:
        return "Multi-sensor"
    if has_sar:
        return "SAR"
    if has_opt:
        return "Optical"
    if has_lidar:
        return "LiDAR"
    return ""


def _sensor_type_for(name: str) -> str | None:
    """Return the sensor type for a single satellite name."""
    if name in _SAR_SATS:
        return "SAR"
    if name in _OPT_SATS:
        return "Optical"
    if name in _LIDAR_SATS:
        return "LiDAR"
    return None


def _classify_study_type(abstract: str, title: str) -> str:
    combined = (abstract + " " + title).lower()

    def _hits(kw_list: list[str]) -> int:
        return sum(1 for kw in kw_list if kw in combined)

    if _hits(_REVIEW_KW) >= 2 or "review" in (title or "").lower():
        return "Review paper"
    if _hits(_DATASET_KW) >= 2:
        return "Dataset/benchmark paper"
    if _hits(_HYDRAULIC_KW) >= 1:
        return "Hydraulic modeling"
    if _hits(_HYDRO_FORE_KW) >= 1:
        return "Hydrological forecasting"
    if _hits(_OPERATIONAL_KW) >= 1:
        return "Operational mapping system"
    if _hits(_DL_KW) >= 2:
        return "ML/DL classification"
    if _hits(_ML_KW) >= 2:
        return "ML/DL classification"
    return "Satellite flood mapping"


def _extract_country(
    abstract: str, introduction: str
) -> tuple[str, str]:
    """
    Detect country/region from abstract + introduction.
    Returns (country_name, snippet).
    Ukraine-first per spec.
    """
    combined = (abstract + " " + introduction).lower()

    for kw in _UKRAINE_KW:
        idx = combined.find(kw)
        if idx >= 0:
            snip = combined[max(0, idx - 30): idx + len(kw) + 50]
            return "Ukraine", snip

    for country in _KNOWN_COUNTRIES:
        idx = combined.find(country)
        if idx >= 0:
            snip = combined[max(0, idx - 30): idx + len(country) + 50]
            return country.title(), snip

    return "", ""


def _extract_river_basin(
    abstract: str, methods: str
) -> tuple[str, str]:
    """Extract the first matching basin name from abstract + methods. Returns (basin, snippet)."""
    combined = (abstract + " " + methods).lower()
    for basin in _RIVER_BASINS:
        idx = combined.find(basin.lower())
        if idx >= 0:
            snip = combined[max(0, idx - 30): idx + len(basin) + 50]
            return basin.title(), snip
    return "", ""


def _extract_methods(
    methods_text: str,
) -> tuple[list[str], str]:
    """
    Extract method names FROM THE METHODS SECTION ONLY.
    Returns (methods_list, first_snippet).
    Never reads the abstract — prevents false positives.
    """
    tl = methods_text.lower()
    found: list[str] = []
    seen:  set[str]  = set()
    first_snippet    = ""

    for canonical, triggers in _METHODS_RULES:
        for kw in triggers:
            m = re.search(kw, tl)
            if m:
                if canonical not in seen:
                    found.append(canonical)
                    seen.add(canonical)
                    if not first_snippet:
                        first_snippet = _snippet(methods_text, m.start(), m.end(), 50)
                break

    return found, first_snippet


def _scan_metric_with_snippet(
    text: str, patterns: list[re.Pattern],
    lo: float = 0.0, hi: float = 1.0,
) -> tuple[float | None, str]:
    """
    Extract the first metric value from *text* that:
    - matches a pattern
    - is literally present in text (anti-hallucination check)
    - lies within the metric's scientific range [lo, hi]
      (Фаза 2.1: Kappa → lo=-1; NSE/KGE/R² → lo=-inf — від'ємні валідні)

    Returns (float, snippet) or (None, "").
    Metrics MUST come from the results section — caller is responsible for scoping.
    """
    for pat in patterns:
        for m in pat.finditer(text):
            groups = [g for g in m.groups() if g is not None]
            if len(groups) == 2:
                try:
                    a = _parse_float(groups[0], lo, hi)
                    b = _parse_float(groups[1], lo, hi)
                    if lo <= a <= b <= hi:
                        val = round(mean([a, b]), 4)
                        if _literal_in_text(a, text) and _literal_in_text(b, text):
                            return val, _snippet(text, m.start(), m.end())
                except ValueError:
                    continue
            elif len(groups) == 1:
                try:
                    val = _parse_float(groups[0], lo, hi)
                    if lo <= val <= hi and _literal_in_text(val, text):
                        return val, _snippet(text, m.start(), m.end())
                except ValueError:
                    continue
    return None, ""


def _detect_nrt(abstract: str, methods: str) -> bool | None:
    combined = abstract + " " + methods
    return True if _NRT_RE.search(combined) else None


def _detect_nrt_with_snippet(abstract: str, methods: str) -> tuple[bool | None, str]:
    """Return (flag, matched_snippet) so NRT facts have real evidence."""
    combined = abstract + " " + methods
    m = _NRT_RE.search(combined)
    if m:
        return True, _snippet(combined, m.start(), m.end(), 40)
    return None, ""


# ─────────────────────────────────────────────────────────────────────────────
# 4b. TASK & REGION CLASSIFIERS
# ─────────────────────────────────────────────────────────────────────────────

_TASK_RULES: list[tuple[str, list[str]]] = [
    ("flood susceptibility mapping",  ["flood susceptibility", "susceptibility mapping",
                                       "susceptibility assessment"]),
    ("DEM accuracy assessment",       ["dem validation", "dem accuracy", "elevation accuracy",
                                       "vertical accuracy", "icesat-2", "icesat 2",
                                       "lidar validation"]),
    ("hydrological simulation",       ["hydrological model", "rainfall-runoff",
                                       "hec-hms", "hec hms", "swat model",
                                       "flood forecasting", "streamflow"]),
    ("hydraulic simulation",          ["hydraulic model", "hydrodynamic model",
                                       "hec-ras", "hec ras", "lisflood", "flo-2d",
                                       "mike flood", "2d flood simulation"]),
    ("land cover change detection",   ["land cover change", "land use change",
                                       "land cover classification", "lulc"]),
    ("review / meta-analysis",        ["systematic review", "literature review",
                                       "meta-analysis", "state of the art",
                                       "review of methods"]),
    ("water detection",               ["water body detection", "water surface detection",
                                       "open water detection", "water extraction"]),
    ("flood mapping",                 ["flood mapping", "flood detection",
                                       "flood extent", "flooded area",
                                       "inundation mapping", "flood delineation"]),
]


def _classify_task(abstract: str, title: str, study_type: str) -> str:
    task, _, _ = _classify_task_with_evidence(abstract, title, study_type)
    return task


def _classify_task_with_evidence(
    abstract: str, title: str, study_type: str
) -> tuple[str, str, str]:
    """Return (task, matched_snippet, section) so task facts have real evidence."""
    combined = (abstract + " " + title).lower()
    for task, triggers in _TASK_RULES:
        for kw in triggers:
            idx = combined.find(kw)
            if idx >= 0:
                snip = combined[max(0, idx - 10): min(len(combined), idx + len(kw) + 50)]
                return task, snip, "abstract+title"
    if study_type == "Review paper":
        snip = abstract[:100] if abstract else title[:100]
        return "review / meta-analysis", snip, "abstract"
    snip = abstract[:100] if abstract else title[:100]
    return "flood mapping", snip, "abstract"


# Ukrainian administrative regions for granular region extraction
_UA_REGIONS: list[tuple[str, str]] = [
    ("Carpathians",    r"carpathian"),
    ("Zakarpattia",    r"zakarpatt|transcarpathi"),
    ("Bukovyna",       r"bukovin|bukovyn"),
    ("Danube delta",   r"danube\s+delta"),
    ("Polissia",       r"polissi"),
    ("Dnipro lowland", r"dnipro\s+lowland|dnieper\s+lowland"),
    ("Kherson region", r"kherson"),
    ("Kyiv region",    r"\bkyiv\b|\bkiev\b"),
    ("Odesa region",   r"\bodesa\b|\bodessa\b"),
    ("Zaporizhzhia",   r"zaporizhzhi|zaporizhia"),
    ("Poltava region", r"\bpoltava\b"),
    ("Sumy region",    r"\bsumy\b"),
    ("Chernihiv region", r"chernihiv|chernigov"),
]

_REGION_PATTERNS: list[tuple[str, re.Pattern]] = [
    (name, re.compile(pat, re.IGNORECASE))
    for name, pat in _UA_REGIONS
]


def _extract_region(abstract: str, introduction: str) -> tuple[str, str]:
    combined = abstract + " " + introduction
    for name, pat in _REGION_PATTERNS:
        m = pat.search(combined)
        if m:
            return name, _snippet(combined, m.start(), m.end(), 50)
    return "", ""


# ─────────────────────────────────────────────────────────────────────────────
# 4c. RAW-VALUE METRIC SCANNER (for RMSE / MAE — not capped at 1.0)
# ─────────────────────────────────────────────────────────────────────────────

def _scan_raw_metric(
    text: str, patterns: list[re.Pattern]
) -> tuple[float | None, str, str | None]:
    """
    Like _scan_metric_with_snippet but accepts any non-negative value.
    Returns (value, snippet, unit_or_None).
    """
    for pat in patterns:
        for m in pat.finditer(text):
            groups = [g for g in m.groups() if g is not None]
            if groups:
                try:
                    val = parse_number(groups[0])
                    if val >= 0:
                        from src.extraction.metric_ontology import normalize_unit
                        unit = normalize_unit(groups[1] if len(groups) > 1 else None)
                        return round(val, 4), _snippet(text, m.start(), m.end()), unit
                except (ValueError, IndexError):
                    continue
    return None, "", None


# ─────────────────────────────────────────────────────────────────────────────
# 5. CONFIDENCE SCORING (Task 6)
# ─────────────────────────────────────────────────────────────────────────────

def _quality_score(sections: dict, satellite_names: list) -> float:
    """
    Structural completeness score: +1 each for title, abstract, methods,
    results, satellite found.  Normalized to 0–1.
    """
    score = 0
    score += 1 if sections.get("title")    else 0
    score += 1 if sections.get("abstract") else 0
    score += 1 if sections.get("methods")  else 0
    score += 1 if sections.get("results")  else 0
    score += 1 if satellite_names          else 0
    return score / 5.0


def _evidence_score(provenance: dict) -> int:
    """Count of fields with valid section-based provenance (snippet + section)."""
    return sum(
        1 for v in provenance.values()
        if isinstance(v, dict) and v.get("section") and v.get("snippet")
    )


def _compute_confidence(quality: float, evidence: int, max_evidence: int = 6) -> float:
    """
    60% quality_score + 40% normalized evidence_score.
    Reflects structural completeness AND traceability.
    """
    e_norm = evidence / max(max_evidence, 1)
    return round(min(0.6 * quality + 0.4 * e_norm, 1.0), 3)


# ─────────────────────────────────────────────────────────────────────────────
# 6. MAIN EXTRACTION FUNCTION
# ─────────────────────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────────────────────
# 6b. FACT-CENTRIC ELEMENT EXTRACTOR
# ─────────────────────────────────────────────────────────────────────────────

def extract_scientific_elements(sections: dict) -> dict:
    """
    Extract all scientific elements from parsed sections with full evidence.

    Returns a dict suitable for fact_builder.build_facts():
    {
      "satellites":     [{name, sensor_type, snippet}, ...],
      "study_area":     {country, region, river_basin,
                         country_snippet, region_snippet, basin_snippet},
      "methods":        [{name, snippet}, ...],
      "metrics":        [{type, value, unit, snippet}, ...],
      "task":           str | None,
      "study_type":     str | None,
      "near_real_time": bool | None,
    }
    """
    title        = sections.get("title")        or ""
    abstract     = sections.get("abstract")     or ""
    introduction = sections.get("introduction") or ""
    methods_sec  = sections.get("methods")      or ""
    results_sec  = sections.get("results")      or ""

    # ── Satellites (title + abstract + methods) ───────────────────────────────
    search = f"{title} {abstract} {methods_sec}"
    sat_entries: list[dict] = []
    seen_sats: set[str] = set()
    first_snippet = ""

    for canonical, pat in _SAT_PATTERNS:
        m = pat.search(search)
        if m and canonical not in seen_sats:
            snip = _snippet(search, m.start(), m.end())
            if not first_snippet:
                first_snippet = snip
            sat_entries.append({
                "name":        canonical,
                "sensor_type": _sensor_type_for(canonical),
                "snippet":     snip,
            })
            seen_sats.add(canonical)

    # Remove generic base names when versioned forms are present
    versioned_bases = {s["name"].split("-")[0] for s in sat_entries
                       if "-" in s["name"] and not s["name"].startswith("ERS")}
    sat_entries = [s for s in sat_entries if s["name"] not in versioned_bases]

    # ── Study area ────────────────────────────────────────────────────────────
    country, country_snip = _extract_country(abstract, introduction)
    basin, basin_snip     = _extract_river_basin(abstract, methods_sec)
    region, region_snip   = _extract_region(abstract, introduction)

    study_area = {
        "country":         country or None,
        "region":          region or None,
        "river_basin":     basin or None,
        "country_snippet": country_snip or None,
        "region_snippet":  region_snip or None,
        "basin_snippet":   basin_snip or None,
    }

    # ── Methods (methods section ONLY) ───────────────────────────────────────
    method_entries: list[dict] = []
    if methods_sec:
        tl = methods_sec.lower()
        seen_methods: set[str] = set()
        for canonical, triggers in _METHODS_RULES:
            if canonical in seen_methods:
                continue
            for kw in triggers:
                m = re.search(kw, tl)
                if m:
                    method_entries.append({
                        "name":    canonical,
                        "snippet": _snippet(methods_sec, m.start(), m.end(), 50),
                    })
                    seen_methods.add(canonical)
                    break

    # ── Metrics (results section ONLY) ───────────────────────────────────────
    metric_entries: list[dict] = []
    if results_sec:
        _NEG_INF = float("-inf")
        for metric_type, patterns, m_lo in [
            # (тип, патерни, нижня межа наукового діапазону; верхня = 1.0)
            ("OA",    OA_PATTERNS,    0.0),
            ("F1",    F1_PATTERNS,    0.0),
            ("IoU",   IOU_PATTERNS,   0.0),
            ("Kappa", KAPPA_PATTERNS, -1.0),
            ("R2",    R2_PATTERNS,    _NEG_INF),
            ("NSE",   NSE_PATTERNS,   _NEG_INF),
            ("KGE",   KGE_PATTERNS,   _NEG_INF),
        ]:
            val, snip = _scan_metric_with_snippet(results_sec, patterns, lo=m_lo)
            if val is not None:
                metric_entries.append({
                    "type":    metric_type,
                    "value":   val,
                    "unit":    None,
                    "snippet": snip,
                })

        for metric_type, patterns in [("RMSE", RMSE_PATTERNS), ("MAE", MAE_PATTERNS)]:
            val, snip, unit = _scan_raw_metric(results_sec, patterns)
            if val is not None:
                metric_entries.append({
                    "type":    metric_type,
                    "value":   val,
                    "unit":    unit,
                    "snippet": snip,
                })

    # ── Study type, task, NRT — with evidence snippets ───────────────────────
    study_type  = _classify_study_type(abstract, title)
    task, task_snippet, task_section = _classify_task_with_evidence(abstract, title, study_type)
    nrt, nrt_snippet = _detect_nrt_with_snippet(abstract, methods_sec)

    return {
        "satellites":     sat_entries,
        "study_area":     study_area,
        "methods":        method_entries,
        "metrics":        metric_entries,
        "task":           task,
        "task_snippet":   task_snippet,
        "task_section":   task_section,
        "study_type":     study_type,
        "near_real_time": nrt,
        "nrt_snippet":    nrt_snippet,
    }


_OUTPUT_KEYS = (
    "Title", "Satellite_Names", "Sensor_Type", "Study_Type",
    "Country", "River_Basin", "Methods",
    "OA", "F1", "IoU", "Kappa",
    "Near_Real_Time",
)

_METRIC_STUDY_TYPES = {
    "Satellite flood mapping",
    "ML/DL classification",
    "Operational mapping system",
}


def extract_scientific_data(sections: dict) -> tuple[dict, dict]:
    """
    Extract structured scientific data from parsed document sections.

    Parameters
    ----------
    sections : dict
        Output of parse_document_sections() — keys: title, abstract,
        introduction, methods, results, discussion.  Values may be None.

    Returns
    -------
    (data_dict, provenance_dict)
        data_dict: keys defined in _OUTPUT_KEYS.
        provenance_dict: field_name → {section, snippet, source, extractor_mode, value}
    """
    title        = sections.get("title")        or ""
    abstract     = sections.get("abstract")     or ""
    introduction = sections.get("introduction") or ""
    methods      = sections.get("methods")      or ""
    results      = sections.get("results")      or ""

    provenance: dict = {}

    # ── Satellites (abstract + methods) ──────────────────────────────────────
    satellite_names, sat_snippet = _extract_satellites(abstract, methods)
    if satellite_names:
        provenance["Satellite_Names"] = {
            "value":          satellite_names,
            "section":        "abstract+methods",
            "snippet":        sat_snippet[:200],
            "source":         "regex",
            "extractor_mode": "section",
        }

    sensor_type = _derive_sensor_type(satellite_names, abstract + " " + methods)
    study_type  = _classify_study_type(abstract, title)

    # ── Country (abstract + introduction, Ukraine-first) ─────────────────────
    country, country_snippet = _extract_country(abstract, introduction)
    if country:
        provenance["Country"] = {
            "value":          country,
            "section":        "abstract+introduction",
            "snippet":        country_snippet[:200],
            "source":         "regex",
            "extractor_mode": "section",
        }

    # ── River basin (abstract + methods) ─────────────────────────────────────
    river_basin, basin_snippet = _extract_river_basin(abstract, methods)
    if river_basin:
        provenance["River_Basin"] = {
            "value":          river_basin,
            "section":        "abstract+methods",
            "snippet":        basin_snippet[:200],
            "source":         "regex",
            "extractor_mode": "section",
        }

    # ── Methods — STRICT: methods section ONLY ───────────────────────────────
    if methods:
        extracted_methods, methods_snippet = _extract_methods(methods)
    else:
        extracted_methods, methods_snippet = [], ""

    if extracted_methods:
        provenance["Methods"] = {
            "value":          extracted_methods,
            "section":        "methods",
            "snippet":        methods_snippet[:200],
            "source":         "regex",
            "extractor_mode": "section",
        }

    # ── Metrics — STRICT: results section ONLY ───────────────────────────────
    oa = f1 = iou = kappa = None
    if results and study_type in _METRIC_STUDY_TYPES:
        oa,    oa_snip    = _scan_metric_with_snippet(results, OA_PATTERNS)
        f1,    f1_snip    = _scan_metric_with_snippet(results, F1_PATTERNS)
        iou,   iou_snip   = _scan_metric_with_snippet(results, IOU_PATTERNS)
        kappa, kappa_snip = _scan_metric_with_snippet(results, KAPPA_PATTERNS)

        for fname, fval, fsnip in [
            ("OA", oa, oa_snip), ("F1", f1, f1_snip),
            ("IoU", iou, iou_snip), ("Kappa", kappa, kappa_snip),
        ]:
            if fval is not None:
                provenance[fname] = {
                    "value":          fval,
                    "section":        "results",
                    "snippet":        fsnip[:200],
                    "source":         "regex",
                    "extractor_mode": "section",
                }

    # ── Near-real-time (abstract + methods) ──────────────────────────────────
    nrt = _detect_nrt(abstract, methods)

    data = {
        "Title":           title or None,
        "Satellite_Names": satellite_names,
        "Sensor_Type":     sensor_type or None,
        "Study_Type":      study_type or None,
        "Country":         country or None,
        "River_Basin":     river_basin or None,
        "Methods":         extracted_methods,
        "OA":              oa,
        "F1":              f1,
        "IoU":             iou,
        "Kappa":           kappa,
        "Near_Real_Time":  nrt,
    }

    return data, provenance


# ─────────────────────────────────────────────────────────────────────────────
# 7. PIPELINE ADAPTER
# ─────────────────────────────────────────────────────────────────────────────

class ScientificExtractor(BaseExtractor):
    """
    Full section-aware extraction pipeline (no LLM).

    Pipeline per document:
        1. Reconstruct full text from ordered chunks.
        2. parse_document_sections()  → structured sections dict.
        3. extract_scientific_data()  → validated flat fields + provenance.
           OR extract_scientific_elements() → typed elements for fact building.
        4. (fact path) fact_builder.build_facts() → list[ScientificFact].
        5. (fact path) fact_validator.validate_fact() → accept / reject.
    """

    def extract(
        self,
        chunks: list[dict],
        source_file: str,
    ) -> ExtractionResult:
        ordered = sorted(
            chunks,
            key=lambda c: (c.get("page_start", 0), c.get("chunk_id", "")),
        )
        full_text = "\n\n".join(c["text"] for c in ordered)

        sections = parse_document_sections(full_text)
        found, missing = describe_parsed(sections)

        # Debug output (Task 9)
        print(f"\n[{source_file}]")
        print(f"  Sections detected: {found or '—'}")
        print(f"  Missing sections:  {missing or '—'}")
        logger.info("[%s]  sections found=%s  missing=%s", source_file, found, missing)

        data, provenance = extract_scientific_data(sections)

        result = self._to_result(data, provenance, source_file, full_text, sections)

        logger.debug(
            "  → satellite=%s  country=%s  methods=%s  OA=%s  F1=%s  NRT=%s"
            "  mode=%s  quality=%.2f  evidence=%d",
            data["Satellite_Names"], data["Country"], data["Methods"],
            data["OA"], data["F1"], data["Near_Real_Time"],
            result.extractor_mode, result.quality_score, result.evidence_score,
        )
        return result

    def extract_facts(
        self,
        chunks: list[dict],
        source_file: str,
    ) -> "FactExtractionResult":
        """
        Fact-centric extraction path.

        Returns a FactExtractionResult whose facts list contains validated
        ScientificFact objects with full Evidence provenance.
        """
        from src.extraction.models import FactExtractionResult
        from src.extraction.fact_builder import build_facts
        from src.extraction.fact_validator import validate_fact

        ordered   = sorted(chunks, key=lambda c: (c.get("page_start", 0), c.get("chunk_id", "")))
        full_text = "\n\n".join(c["text"] for c in ordered)

        sections           = parse_document_sections(full_text)
        found, missing     = describe_parsed(sections)
        fallback_used      = not (sections.get("methods") or sections.get("results"))

        title = sections.get("title") or source_file.replace(".pdf", "").replace("_", " ")

        # ── Debug output (Task 9) ──────────────────────────────────────────────
        print(f"\n[{source_file}]")
        print(f"  Sections detected: {found or '—'}")
        print(f"  Missing sections:  {missing or '—'}")
        logger.info("[%s]  sections found=%s  missing=%s", source_file, found, missing)

        elements = extract_scientific_elements(sections)

        sats     = elements["satellites"]
        area     = elements["study_area"]
        methods  = elements["methods"]
        metrics  = elements["metrics"]
        task     = elements["task"]
        stype    = elements["study_type"]
        nrt      = elements["near_real_time"]

        # ── Debug log (Task 9) ────────────────────────────────────────────────
        print(f"  Satellites:  {[s['name'] for s in sats] or '—'}")
        print(f"  Study area:  country={area.get('country')}  "
              f"region={area.get('region')}  basin={area.get('river_basin')}")
        print(f"  Methods:     {[m['name'] for m in methods] or '—'}")
        print(f"  Metrics:     {[(m['type'], m['value']) for m in metrics] or '—'}")
        print(f"  Task:        {task}")
        print(f"  Study type:  {stype}")
        print(f"  NRT:         {nrt}")
        print(f"  Fallback:    {fallback_used}")

        logger.info(
            "[%s]  satellites=%s  country=%s  region=%s  basin=%s  "
            "methods=%s  metrics=%s  task=%s  nrt=%s  fallback=%s",
            source_file,
            [s["name"] for s in sats],
            area.get("country"), area.get("region"), area.get("river_basin"),
            [m["name"] for m in methods],
            [(m["type"], m["value"]) for m in metrics],
            task, nrt, fallback_used,
        )

        raw_facts = build_facts(paper_id=source_file, elements=elements)

        valid_facts   = []
        rejected_facts = []
        for fact in raw_facts:
            result = validate_fact(fact)
            if result["valid"]:
                valid_facts.append(fact)
            else:
                logger.warning(
                    "[%s] FACT REJECTED: %s", source_file, result["reasons"]
                )
                print(f"  [REJECTED FACT] reasons: {result['reasons']}")
                rejected_facts.append({
                    "fact_id": fact.id,
                    "fact_type": fact.fact_type,
                    "reasons": result["reasons"],
                })

        print(f"  Facts accepted: {len(valid_facts)} / "
              f"rejected: {len(rejected_facts)}")

        return FactExtractionResult(
            paper_id       = source_file,
            title          = title,
            facts          = valid_facts,
            rejected_facts = rejected_facts,
            fallback_used  = fallback_used,
            debug={
                "sections_found":   found,
                "sections_missing": missing,
                "satellites":       [s["name"] for s in sats],
                "study_area":       {k: v for k, v in area.items()
                                     if not k.endswith("_snippet")},
                "methods":          [m["name"] for m in methods],
                "metrics":          [(m["type"], m["value"]) for m in metrics],
                "task":             task,
                "study_type":       stype,
                "nrt":              nrt,
            },
        )

    @staticmethod
    def _to_result(
        data: dict,
        provenance: dict,
        source_file: str,
        full_text: str,
        sections: dict,
    ) -> ExtractionResult:
        satellite_names = data.get("Satellite_Names") or []
        quality  = _quality_score(sections, satellite_names)
        evidence = _evidence_score(provenance)
        conf     = _compute_confidence(quality, evidence)

        result = ExtractionResult(
            source_file     = source_file,
            title           = data.get("Title")          or "",
            satellite_names = ", ".join(satellite_names),
            sensor_type     = data.get("Sensor_Type")    or "",
            study_type      = data.get("Study_Type")     or "",
            country         = data.get("Country")        or "",
            river_basin     = data.get("River_Basin")    or "",
            methods         = ", ".join(data.get("Methods") or []),
            oa              = data.get("OA"),
            f1              = data.get("F1"),
            iou             = data.get("IoU"),
            kappa           = data.get("Kappa"),
            near_real_time  = data.get("Near_Real_Time"),
            ukraine_relevance = (data.get("Country") == "Ukraine"),
            full_text       = full_text,
            abstract        = sections.get("abstract") or "",
            sections_used   = [k for k, v in sections.items() if v and k != "title"],
            provenance      = provenance,
            extractor_mode  = "section",
            llm_used        = False,
            fallback_used   = False,
            quality_score   = quality,
            confidence      = conf,
        )
        return result.finalize()
