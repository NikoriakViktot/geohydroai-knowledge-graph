"""
Rule-based extractor for satellite-based flood mapping literature.

Section-gated extraction (Task 2 / Task 5):
  - Methods   → methods section ONLY
  - Metrics   → results section ONLY
  - Satellites → abstract + introduction + methods (never results)
  - Country   → abstract + introduction
  - Study type → abstract (or first 3000 chars as fallback)

Extracts: bibliographic metadata, study type, satellite/sensor,
study area, processing methods, optional accuracy metrics, timeliness.
Works entirely offline — no LLM required.
"""
from __future__ import annotations

import logging
import re
from statistics import mean

from src.extraction.base import BaseExtractor, ExtractionResult
from src.extraction.numbers import SIGNED_NUMBER, parse_number, to_ratio

logger = logging.getLogger(__name__)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _r(pattern: str) -> re.Pattern:
    return re.compile(pattern, re.IGNORECASE)


# Sign-aware (Фаза 2.1): NSE/Kappa/KGE/R²/PBIAS бувають від'ємними —
# "NSE = -0.27" має витягуватись як -0.27, а не губитись. Журнали часто
# друкують мінус як U+2212 ("−0.27"), тому знак береться з numbers.SIGNED_NUMBER.
_NUM   = SIGNED_NUMBER
_PCT   = r"%?"
_SEP   = r"\s*(?:[=:>]|of|was|is|at|:|=)?\s*"
_RANGE = rf"{_NUM}\s*[-–]\s*{_NUM}"


# ── Accuracy metric patterns ──────────────────────────────────────────────────

OA_PATTERNS: list[re.Pattern] = [
    _r(rf"[Oo]verall\s+[Aa]ccuracy{_SEP}{_RANGE}{_PCT}"),
    _r(rf"\bOA{_SEP}{_RANGE}{_PCT}"),
    _r(rf"[Oo]verall\s+[Aa]ccuracy{_SEP}{_NUM}{_PCT}"),
    _r(rf"\bOA\b{_SEP}{_NUM}{_PCT}"),
    _r(rf"[Aa]ccuracy\s+of\s+{_NUM}{_PCT}"),
    _r(rf"[Aa]ccuracy\s+rate{_SEP}{_NUM}{_PCT}"),
    _r(rf"[Aa]ccuracy{_SEP}{_NUM}{_PCT}"),
]

F1_PATTERNS: list[re.Pattern] = [
    _r(rf"[Ff]1[-\s]*[Ss]core{_SEP}{_RANGE}"),
    _r(rf"\bF[-\s]?1{_SEP}{_RANGE}"),
    _r(rf"[Ff]1[-\s]*[Ss]core{_SEP}{_NUM}"),
    _r(rf"\bF[-\s]?1\b{_SEP}{_NUM}"),
    _r(rf"[Ff]-[Mm]easure{_SEP}{_NUM}"),
    _r(rf"[Ff][-\s]score{_SEP}{_NUM}"),
]

IOU_PATTERNS: list[re.Pattern] = [
    _r(rf"\bIoU{_SEP}{_RANGE}"),
    _r(rf"\bIoU\b{_SEP}{_NUM}"),
    _r(rf"[Ii]ntersection\s+[Oo]ver\s+[Uu]nion{_SEP}{_NUM}"),
    _r(rf"\bmIoU\b{_SEP}{_NUM}"),
    _r(rf"[Jj]accard\s+[Ii]ndex{_SEP}{_NUM}"),
    _r(rf"[Jj]accard{_SEP}{_NUM}"),
]

KAPPA_PATTERNS: list[re.Pattern] = [
    _r(rf"[Kk]appa\s+[Cc]oefficient{_SEP}{_RANGE}"),
    _r(rf"[Cc]ohen.?s\s+[Kk]appa{_SEP}{_RANGE}"),
    _r(rf"\b[Kk]appa{_SEP}{_RANGE}"),
    _r(rf"[Kk]appa\s+[Cc]oefficient{_SEP}{_NUM}"),
    _r(rf"[Cc]ohen.?s\s+[Kk]appa{_SEP}{_NUM}"),
    _r(rf"\b[Kk]appa\b{_SEP}{_NUM}"),
]

# ── Non-normalised metrics (RMSE / MAE can be > 1) ────────────────────────────
# Capture groups: (value, unit?)
# Розширений словник одиниць (Фаза 2.3): додано km, ft, %, темпоральні
# (mm/day, mm/h), розхідні (m³/s з юнікодним ³, cms, cumecs) і m a.s.l.
# Порядок: довші форми першими (m³/s до m, mm/day до mm).
_UNIT = (
    r"\s*("
    r"m\s*a\.?s\.?l\.?"                       # m a.s.l. (elevation)
    r"|m[\^³]?3\s*/\s*s|m3/s|cms|cumecs"      # discharge
    r"|mm\s*/\s*(?:day|d|hr?|h)|cm\s*/\s*(?:day|d)"  # rates
    r"|km|cm|mm|ft|feet"
    r"|m(?:eters?)?"
    r"|%"
    r")?"
)

RMSE_PATTERNS: list[re.Pattern] = [
    _r(rf"\bRMSE{_SEP}({_NUM[1:-1]}){_UNIT}"),
    _r(rf"[Rr]oot[\s\-][Mm]ean[\s\-][Ss]quare(?:[d]?\s+[Ee]rror)?{_SEP}({_NUM[1:-1]}){_UNIT}"),
]

MAE_PATTERNS: list[re.Pattern] = [
    _r(rf"\bMAE{_SEP}({_NUM[1:-1]}){_UNIT}"),
    _r(rf"[Mm]ean\s+[Aa]bsolute\s+[Ee]rror{_SEP}({_NUM[1:-1]}){_UNIT}"),
]

# R² ∈ (−∞, 1] (від'ємний можливий out-of-sample); NSE/KGE — ОКРЕМІ метрики
# з діапазоном (−∞, 1]: від'ємний NSE = "модель гірша за середнє спостережень"
# — валідний науковий результат, який раніше помилково жив у R2_PATTERNS
# з коментарем "same 0–1 scale" і губився.
R2_PATTERNS: list[re.Pattern] = [
    _r(rf"[Rr][\^2²]\s*{_SEP}{_NUM}"),
    _r(rf"\bR2\b{_SEP}{_NUM}"),
    _r(rf"[Cc]oefficient\s+of\s+[Dd]etermination{_SEP}{_NUM}"),
]

NSE_PATTERNS: list[re.Pattern] = [
    _r(rf"\bNSE\b{_SEP}{_NUM}"),
    _r(rf"[Nn]ash[-–\s]+[Ss]utcliffe(?:\s+[Ee]fficiency)?(?:\s+[Cc]oefficient)?{_SEP}{_NUM}"),
]

KGE_PATTERNS: list[re.Pattern] = [
    _r(rf"\bKGE\b{_SEP}{_NUM}"),
    _r(rf"[Kk]ling[-–\s]+[Gg]upta(?:\s+[Ee]fficiency)?{_SEP}{_NUM}"),
]


# ── Study type keyword banks ──────────────────────────────────────────────────

_REVIEW_KW      = ["systematic review", "literature review", "state of the art",
                   "meta-analysis", "review of methods", "survey of", "review paper"]
_DATASET_KW     = ["benchmark dataset", "labeled dataset", "annotated dataset",
                   "training dataset", "ground truth dataset", "flood dataset",
                   "open dataset", "publicly available dataset"]
_HYDRO_FORE_KW  = ["hydrological model", "rainfall-runoff", "flood prediction",
                   "flood forecasting", "hec-hms", "swat model", "discharge forecast",
                   "streamflow"]
_HYDRAULIC_KW   = ["hydraulic model", "hydrodynamic model", "hec-ras", "lisflood",
                   "flo-2d", "mike flood", "mike 21", "2d flood simulation",
                   "inundation model", "2d hydraulic"]
_OPERATIONAL_KW = ["copernicus ems", "emergency management service",
                   "operational flood", "early warning system",
                   "flood monitoring system", "rapid mapping service",
                   "near-real-time system", "automated flood detection pipeline"]
_DL_KW          = ["u-net", "unet", "convolutional neural network", "cnn",
                   "deep learning", "semantic segmentation", "encoder-decoder",
                   "resnet", "deeplab", "segnet", "vision transformer", "lstm",
                   "fully convolutional"]
_ML_KW          = ["random forest", "support vector machine", "svm",
                   "machine learning classification", "decision tree",
                   "gradient boosting", "xgboost", "maximum likelihood classification",
                   "naive bayes", "logistic regression"]


# ── Satellite / sensor keyword lists ─────────────────────────────────────────

_SAR_SATELLITES = [
    "sentinel-1", "terrasar-x", "tandem-x", "uavsar", "cosmo-skymed",
    "alos-2", "alos palsar", "radarsat-2", "radarsat-1", "radarsat",
    "ers-1", "ers-2", "envisat asar", "novasar", "iceye", "capella",
]
_OPT_SATELLITES = [
    "sentinel-2", "landsat-8", "landsat-9", "landsat-7", "landsat",
    "modis", "viirs", "worldview-2", "worldview-3", "worldview",
    "pleiades", "spot-6", "spot-7", "spot", "planet labs", "planet",
    "planetscope", "rapideye", "aerial photography", "google earth engine",
]

_DATA_PRODUCTS = [
    "grdh", "grds", "grd", "slc", "msi", "oli", "tirs", "oli/tirs",
    "level-1c", "level-2a", "level-1", "level-2", "ard",
    "iw grd", "iw mode", "sm mode",
]

# ── Methods keyword map ───────────────────────────────────────────────────────

_METHODS_RULES: list[tuple[str, list[str]]] = [
    ("Thresholding",            ["threshold", "thresholding", "otsu", "bimodal threshold",
                                 "empirical threshold", "histogram threshold",
                                 "constant threshold"]),
    ("Change detection",        ["change detection", "multi-temporal", "bitemporal",
                                 "bi-temporal", "pre-flood and post-flood", "pre/post-flood"]),
    ("NDWI/MNDWI",              ["ndwi", "mndwi", "normalized difference water index",
                                 "modified ndwi", "awei", "automated water extraction"]),
    ("Random Forest",           ["random forest", "rf classifier"]),
    ("SVM",                     ["support vector machine", r"\bsvm\b"]),
    ("Maximum likelihood",      ["maximum likelihood", r"\bmlc\b"]),
    ("U-Net",                   ["u-net", "unet", "attention u-net", "attention unet",
                                 "fsa-unet", "res-unet"]),
    ("CNN",                     ["convolutional neural network", r"\bcnn\b", "fcnn",
                                 "fully convolutional", "segnet", "deeplab", "basnet"]),
    ("LSTM",                    [r"\blstm\b", "long short-term memory"]),
    ("Transformer",             ["vision transformer", r"\bvit\b", "swin transformer"]),
    ("OBIA",                    ["obia", "object-based image analysis", "object-oriented"]),
    # Hydraulic / hydrological models — keep specific names separate so the graph
    # can distinguish HEC-RAS (hydraulic) from SWAT/HEC-HMS (hydrological)
    ("HEC-RAS",                 [r"\bhec[\s\-]ras\b", "hec ras"]),
    ("HEC-HMS",                 [r"\bhec[\s\-]hms\b", "hec hms"]),
    ("SWAT",                    [r"\bswat\b(?!\s+team)", "soil and water assessment tool"]),
    ("Hydrodynamic model",      ["hydrodynamic model", "hydraulic model",
                                 "lisflood", "flo-2d", "mike flood", "mike 21",
                                 "2d flood simulation", "inundation model"]),
    # Validation & analysis methods
    ("DEM validation",          ["dem validation", "dem accuracy", "elevation accuracy",
                                 "digital elevation model validation",
                                 "vertical accuracy assessment"]),
    ("ICESat-2 validation",     ["icesat-2 validation", "icesat-2 data", "atlas/icesat-2",
                                 "atl03", "atl06", "atl08", "icesat2"]),
    ("Flood frequency analysis",["flood frequency", "return period", "recurrence interval",
                                 "frequency analysis", "extreme value analysis"]),
    ("Operational workflow",    ["copernicus ems", "emergency mapping", "rapid mapping",
                                 "operational workflow", "automated pipeline"]),
]


# ── Study area keyword lists ──────────────────────────────────────────────────

_KNOWN_COUNTRIES = [
    "australia", "bangladesh", "brazil", "china", "croatia", "england",
    "france", "germany", "india", "indonesia", "iran", "italy", "japan",
    "mexico", "moldova", "morocco", "myanmar", "netherlands", "nigeria",
    "pakistan", "poland", "romania", "serbia", "slovenia", "spain",
    "thailand", "ukraine", "united kingdom", "united states", "usa", "uk",
    "vietnam", "global",
]

_UKRAINE_KW = {
    "ukraine", "ukrainian",
    "dnipro", "dnieper",
    "kakhovka",
    "prut",
    "dniester", "dnister",
    "tisza", "tysa",
    "carpathian", "carpathians",
    "bukovyna", "bukovina",
    "zakarpattia", "transcarpathia",
    "kherson", "kyiv", "odesa", "mykolaiv",
    "zaporizhzhia", "poltava", "vinnytsia",
    "chernihiv", "sumy", "zhytomyr",
    "desna",
    "pivdennyi buh", "southern bug",
}

_RIVER_BASINS = [
    "amazon basin", "congo basin", "ganges", "brahmaputra", "mekong",
    "nile basin", "mississippi", "missouri river", "ohio river",
    "rhine basin", "danube basin", "po valley", "elbe", "oder",
    "loire", "thames", "indus basin", "yangtze", "yellow river",
    "niger basin", "zambezi", "murray-darling", "colorado river",
    "columbia river", "volga", "dnipro basin", "dnieper basin",
    "dniester basin", "prut basin", "prut river basin", "tisza basin",
    "tisza river basin", "dnipro river basin", "dniester river basin",
    "carpathian basin", "irrawaddy", "red river",
]

_RIVER_NAMES = [
    "dnipro", "dnieper", "dniester", "dnister", "prut", "tisza", "tysa",
    "desna", "southern bug", "pivdennyi buh", "siversky donets",
    "danube", "rhine", "elbe", "oder", "vistula", "thames", "seine",
    "po", "tiber", "rhone", "loire", "meuse", "main", "mosel",
    "drava", "sava", "tisa", "morava",
    "ganges", "brahmaputra", "mekong", "yangtze", "yellow river",
    "irrawaddy", "indus", "godavari", "mahanadi",
    "mississippi", "missouri", "amazon", "colorado", "columbia",
    "nile", "niger", "zambezi", "congo",
    "volga", "ob", "lena", "yenisei",
]

_REGION_CONTEXT_PAT = _r(
    r"(?:study\s+area|located\s+in|region\s+of|located\s+at|in\s+the)"
    r"[^.]{0,100}?(?:" + "|".join(re.escape(c) for c in _KNOWN_COUNTRIES) + r")"
)


# ── Timeliness patterns ───────────────────────────────────────────────────────

# Strong NRT signals — unambiguous near-real-time operation
_NRT_STRONG_PAT = _r(
    r"near[\s-]?real[\s-]?time|near\s+real\s+time|\bNRT\b"
    r"|real[\s-]time\s+flood"
)
# Weak NRT signals — require explicit latency to assert True;
# alone they only indicate "possible NRT" (caller must check for latency)
_NRT_WEAK_PAT = _r(
    r"rapid\s+(?:flood\s+)?mapping|operational\s+flood\s+monitoring"
    r"|automated\s+(?:flood\s+)?mapping|near\s+real\s+time\s+(?:flood|mapping)"
)
# Keep backward-compat alias used elsewhere in the module
_NRT_PAT = _NRT_STRONG_PAT
_LATENCY_PAT = _r(
    r"(?:latency|delivered\s+within|available\s+within|within\s+less\s+than|"
    r"processing\s+time\s+of)\s*[:\-]?\s*(\d+(?:\.\d+)?)\s*(hours?|days?|h\b|d\b)"
)
_REVISIT_PAT = _r(
    r"(?:revisit\s+time|repeat\s+pass|repeat\s+cycle)\s*(?:of\s*)?(\d+(?:\.\d+)?)\s*(days?|hours?)"
)


# ── Bibliographic helpers ─────────────────────────────────────────────────────

_ABSTRACT_HEADER = re.compile(r"\bAbstract\b\s*[:\-—]?\s*", re.IGNORECASE)
_DOI_RE          = re.compile(r"10\.\d{4,9}/[-._;()/:A-Za-z0-9]+")
_YEAR_RE         = re.compile(r"\b(19[89]\d|20[0-3]\d)\b")
_AUTHOR_PATTERN  = _r(
    r"^([A-Z][a-zA-ZÀ-ɏ\-]+(?:\s+[A-Z][a-zA-ZÀ-ɏ\-]+)?"
    r"(?:\s+et\s+al\.?)?)\s*[,\(]?\s*(?:19|20)\d{2}"
)


def _extract_title(text: str, source_file: str) -> str:
    for line in text[:600].splitlines():
        line = line.strip()
        if 10 < len(line) < 200 and not line.lower().startswith(
            ("abstract", "introduction", "doi", "http", "www", "©", "keywords")
        ):
            return line
    return source_file.replace(".pdf", "").replace("_", " ")


def _extract_abstract(text: str) -> str:
    m = _ABSTRACT_HEADER.search(text)
    if not m:
        return ""
    after = text[m.end(): m.end() + 2000].strip()
    paras = re.split(r"\n\s*\n|\n(?=[1-9A-Z][.\s])", after)
    candidate = paras[0].strip() if paras else ""
    return candidate[:1200]


def _extract_doi(text: str) -> str:
    m = _DOI_RE.search(text[:3000])
    return m.group(0) if m else ""


def _extract_year(text: str, source_file: str) -> str:
    m = _YEAR_RE.search(text[:1000])
    if m:
        return m.group(1)
    m = _YEAR_RE.search(source_file)
    return m.group(1) if m else ""


def _extract_authors(text: str, source_file: str) -> str:
    head = text[:1500]
    for line in head.splitlines():
        m = _AUTHOR_PATTERN.match(line.strip())
        if m:
            return m.group(1).strip()
    stem = source_file.replace(".pdf", "").replace("_", " ").replace("-", " ")
    parts = stem.split()
    return " ".join(parts[:3]) if parts else "Unknown"


# ── Study type ────────────────────────────────────────────────────────────────

def _classify_study_type(text: str) -> str:
    tl = text.lower()

    def _hits(kw_list: list[str]) -> int:
        return sum(1 for kw in kw_list if kw in tl)

    if _hits(_REVIEW_KW) >= 2:
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


# ── Satellite / sensor ────────────────────────────────────────────────────────

def _detect_satellites(text: str) -> tuple[str, str]:
    """Return (satellite_names_csv, sensor_type) from section-gated text.

    Дворівнева детекція (Фаза 2.6):
      1. curated-банки — точність + SAR/optical типізація (KB цього не кодує);
      2. KB-аугментація — 152 сенсори з \\b-патернами для сутностей поза
         банками (KB-знахідки НЕ впливають на sensor_type).
    """
    tl = text.lower()
    sar_found = _dedup([s for s in _SAR_SATELLITES if s in tl])
    opt_found = _dedup([s for s in _OPT_SATELLITES if s in tl])
    all_found = [s.title() for s in sar_found + opt_found]

    # KB-аугментація — додаткові сенсори, яких немає в curated-банках
    try:
        from src.extraction.kb_keywords import detect_kb_satellites
        kb_extra = detect_kb_satellites(text, exclude=set(sar_found + opt_found))
        all_found += [a for a in kb_extra if a.title() not in all_found]
    except Exception as exc:   # KB недоступна — curated-банки достатні
        logger.debug("KB satellite augmentation unavailable: %s", exc)

    names_csv = ", ".join(all_found[:6]) if all_found else ""

    if sar_found and opt_found:
        sensor_type = "Multi-sensor"
    elif sar_found:
        sensor_type = "SAR"
    elif opt_found:
        sensor_type = "Optical"
    else:
        sensor_type = ""

    return names_csv, sensor_type


def _detect_data_product(text: str) -> str:
    tl = text.lower()
    found = _dedup([p for p in _DATA_PRODUCTS if p.lower() in tl])
    return ", ".join(p.upper() for p in found[:4]) if found else ""


# ── Methods ───────────────────────────────────────────────────────────────────

def _detect_methods(text: str) -> str:
    """Extract methods from *text* (caller must pass methods section text only)."""
    tl = text.lower()
    found = []
    for canonical, triggers in _METHODS_RULES:
        for kw in triggers:
            if re.search(kw, tl):
                found.append(canonical)
                break
    return ", ".join(_dedup(found)) if found else ""


# ── Geography ─────────────────────────────────────────────────────────────────

def _detect_country(text: str) -> str:
    """Detect country from abstract + introduction text (caller-scoped)."""
    tl = text.lower()
    if any(kw in tl for kw in _UKRAINE_KW):
        return "Ukraine"
    found = [c.title() for c in _KNOWN_COUNTRIES if c in tl]
    m = _REGION_CONTEXT_PAT.search(text)
    if m:
        return m.group(0).strip()[:80]
    return ", ".join(_dedup(found)[:4]) if found else ""


def _detect_river_basin(text: str) -> str:
    tl = text.lower()
    found = [b.title() for b in _RIVER_BASINS if b in tl]
    return ", ".join(_dedup(found)[:3]) if found else ""


_CITY_EVENT_PAT = _r(
    r"(?:flooding\s+(?:in|of|event\s+in)|flood\s+event\s+(?:in|of)|"
    r"flood\s+of|case\s+study\s+(?:in|of))\s+([A-Z][a-zA-Z\s]{2,40})"
)


def _detect_city_event(text: str) -> str:
    m = _CITY_EVENT_PAT.search(text[:3000])
    return m.group(1).strip()[:60] if m else ""


def _detect_ukraine_relevance(text: str) -> bool:
    tl = text.lower()
    return any(kw in tl for kw in _UKRAINE_KW)


_RIVER_PATTERNS: list[tuple[str, re.Pattern]] = [
    (name, re.compile(
        r"\b" + re.escape(name) + r"\b" if len(name) <= 3
        else re.escape(name),
        re.IGNORECASE,
    ))
    for name in _RIVER_NAMES
]


def _detect_river_name(text: str) -> str:
    found = []
    for canonical, pat in _RIVER_PATTERNS:
        if pat.search(text):
            found.append(canonical.title())
    return ", ".join(_dedup(found)[:5]) if found else ""


# ── Timeliness ────────────────────────────────────────────────────────────────

def _detect_timeliness(text: str) -> tuple[str, str, bool | None, str]:
    """
    Returns (latency, revisit, nrt_bool, nrt_label).

    nrt_label: "true" | "false" | "unclear" | ""
      "true"    — strong NRT signal (near-real-time, NRT, real-time flood)
                  OR weak signal + explicit latency detected
      "unclear" — weak signal without explicit latency (rapid, operational)
      ""        — no NRT signal at all
    nrt_bool: True | None (never sets False — that requires explicit evidence)
    """
    latency = ""
    m = _LATENCY_PAT.search(text)
    if m:
        latency = f"{m.group(1)} {m.group(2)}"

    revisit = ""
    m = _REVISIT_PAT.search(text)
    if m:
        revisit = f"{m.group(1)} {m.group(2)}"

    has_strong = bool(_NRT_STRONG_PAT.search(text))
    has_weak   = bool(_NRT_WEAK_PAT.search(text))

    if has_strong or (has_weak and latency):
        nrt_bool, nrt_label = True, "true"
    elif has_weak:
        nrt_bool, nrt_label = None, "unclear"
    else:
        nrt_bool, nrt_label = None, ""

    return latency, revisit, nrt_bool, nrt_label


# ── Confidence scoring (Task 6) ───────────────────────────────────────────────

def _score_confidence(r: ExtractionResult) -> float:
    """
    Weighted confidence: 60% quality (structural) + 40% field evidence.
    Replaces the old completeness-only formula.
    """
    quality = 0
    quality += 1 if r.title          else 0
    quality += 1 if r.abstract       else 0
    quality += 1 if r.methods        else 0
    quality += 1 if any(v is not None for v in [r.oa, r.f1, r.iou, r.kappa]) else 0
    quality += 1 if r.satellite_names else 0
    q_norm = quality / 5.0

    evidence = 0
    evidence += 1 if r.satellite_names else 0
    evidence += 1 if r.country         else 0
    evidence += 1 if r.methods         else 0
    evidence += 1 if r.study_type      else 0
    evidence += 1 if r._num_metrics() > 0 else 0
    evidence += 1 if r.river_basin     else 0
    e_norm = evidence / 6.0

    return round(min(0.6 * q_norm + 0.4 * e_norm, 1.0), 3)


# ── Evidence collection ───────────────────────────────────────────────────────

_EVIDENCE_PAT = _r(
    r"(?:accuracy|OA|F1|IoU|kappa|precision|recall|near-real-time|"
    r"Sentinel-1|Sentinel-2|SAR|flood mapping)"
    r"[^.]{0,150}(?:[0-9]+\.?[0-9]*\s*%?)"
)


def _collect_evidence(text: str) -> list[str]:
    return [m.group(0).strip() for m in _EVIDENCE_PAT.finditer(text)][:6]


# ── Metric parsing ────────────────────────────────────────────────────────────

def _parse_number(raw: str, lo: float = 0.0, hi: float = 1.0) -> float:
    """Parse a metric value; percentages become ratios only for bounded metrics."""
    return to_ratio(parse_number(raw), lo, hi)


def _extract_metric(
    text: str,
    patterns: list[re.Pattern],
    lo: float = 0.0,
    hi: float = 1.0,
) -> float | None:
    """
    Extract metric from *text*. Caller must scope to results section.

    lo/hi — науковий діапазон метрики (Фаза 2.1): Kappa → lo=-1,
    NSE/KGE/R² → lo=-inf. Дефолт [0,1] для строгих ratio-метрик.
    """
    for pat in patterns:
        for m in pat.finditer(text):
            groups = [g for g in m.groups() if g is not None]
            if len(groups) == 2:
                try:
                    a, b = _parse_number(groups[0], lo, hi), _parse_number(groups[1], lo, hi)
                    if lo <= a <= b <= hi:
                        return round(mean([a, b]), 4)
                except ValueError:
                    continue
            elif len(groups) == 1:
                try:
                    val = _parse_number(groups[0], lo, hi)
                    if lo <= val <= hi:
                        return round(val, 4)
                except ValueError:
                    continue
    return None


# ── Dedup helper ──────────────────────────────────────────────────────────────

def _dedup(lst: list[str]) -> list[str]:
    seen: set[str] = set()
    return [x for x in lst if x not in seen and not seen.add(x)]  # type: ignore


# ── Main extractor class ──────────────────────────────────────────────────────

class RegexExtractor(BaseExtractor):
    """
    Section-aware offline rule-based extractor.

    All fields are gated to the appropriate section:
    - Methods: methods section only
    - Metrics: results section only
    - Satellites/Country: abstract + introduction
    When sections cannot be parsed, those fields are left empty.
    """

    def extract(
        self,
        chunks: list[dict],
        source_file: str,
    ) -> ExtractionResult:
        from src.extraction.section_parser import parse_document_sections, describe_parsed

        combined = "\n\n".join(c["text"] for c in chunks)
        result   = ExtractionResult(source_file=source_file)

        # Parse sections for gated extraction (Task 2)
        sections     = parse_document_sections(combined)
        found, missing = describe_parsed(sections)
        logger.info("[%s]  sections found=%s  missing=%s", source_file, found, missing)

        abstract_text     = sections.get("abstract")     or ""
        introduction_text = sections.get("introduction") or ""
        methods_text      = sections.get("methods")      or ""
        results_text      = sections.get("results")      or ""
        has_sections      = bool(methods_text or results_text)

        # ── Bibliographic (always from full text — section-independent) ───────
        result.title     = _extract_title(combined, source_file)
        result.abstract  = abstract_text or _extract_abstract(combined)
        result.doi       = _extract_doi(combined)
        result.year      = _extract_year(combined, source_file)
        result.authors   = _extract_authors(combined, source_file)
        result.full_text = combined

        # ── Study type — from abstract (fallback: first 3000 chars) ──────────
        type_text         = abstract_text or combined[:3000]
        result.study_type = _classify_study_type(type_text)

        # ── Satellites — abstract + introduction + methods (never results) ────
        sat_text = f"{abstract_text} {introduction_text} {methods_text}"
        if not sat_text.strip():
            sat_text = combined[:6000]
        result.satellite_names, result.sensor_type = _detect_satellites(sat_text)
        result.data_product = _detect_data_product(sat_text)

        # ── Geography — abstract + introduction ───────────────────────────────
        geo_text = f"{abstract_text} {introduction_text}"
        if not geo_text.strip():
            geo_text = combined[:5000]
        result.country           = _detect_country(geo_text)
        result.river_basin       = _detect_river_basin(geo_text)
        result.river_name        = _detect_river_name(geo_text)
        result.city_event        = _detect_city_event(geo_text[:3000])
        result.ukraine_relevance = _detect_ukraine_relevance(geo_text)

        # ── Methods — STRICT: methods section ONLY (Task 2) ──────────────────
        if methods_text:
            result.methods = _detect_methods(methods_text)
            logger.debug("[%s] Methods from methods section: %s", source_file, result.methods)
        else:
            logger.debug("[%s] No methods section found — methods left empty", source_file)

        # ── Metrics — STRICT: results section ONLY (Task 5) ──────────────────
        if results_text and result.study_type in (
            "ML/DL classification", "Satellite flood mapping", "Operational mapping system"
        ):
            result.oa    = _extract_metric(results_text, OA_PATTERNS)
            result.f1    = _extract_metric(results_text, F1_PATTERNS)
            result.iou   = _extract_metric(results_text, IOU_PATTERNS)
            result.kappa = _extract_metric(results_text, KAPPA_PATTERNS, lo=-1.0)
            logger.debug("[%s] Metrics from results section: OA=%s F1=%s IoU=%s K=%s",
                         source_file, result.oa, result.f1, result.iou, result.kappa)
        else:
            logger.debug("[%s] No results section or non-metric paper — metrics left None",
                         source_file)

        # ── Timeliness — abstract + methods ──────────────────────────────────
        timeliness_text = f"{abstract_text} {methods_text}"
        if not timeliness_text.strip():
            timeliness_text = combined[:5000]
        (result.latency, result.revisit_time,
         result.near_real_time, result.near_real_time_label) = _detect_timeliness(timeliness_text)

        # ── QA ────────────────────────────────────────────────────────────────
        result.evidence       = _collect_evidence(results_text or combined[:5000])
        result.confidence     = _score_confidence(result)
        result.extractor_mode = "section" if has_sections else "fallback"
        result.fallback_used  = not has_sections
        result.llm_used       = False

        if not has_sections:
            logger.warning(
                "[%s] No methods/results sections found — methods and metrics unavailable",
                source_file,
            )

        return result.finalize()
