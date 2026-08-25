"""
Post-extraction metadata validation, cleaning, and normalization
for the satellite flood mapping schema.

Validates Abstract/DOI, normalizes sensor/method fields,
scores rows, and filters low-quality records.
"""
from __future__ import annotations

import logging
import re

import pandas as pd

logger = logging.getLogger(__name__)


# ── Abstract validation ───────────────────────────────────────────────────────

_ABSTRACT_KEYWORDS = ["study", "analysis", "results", "method", "approach", "objective"]
_BAD_STARTS        = ["introduction", "methods", "materials", "results"]


def is_valid_abstract(text: str | None) -> bool:
    if not text or not isinstance(text, str):
        return False
    text_clean = text.strip()
    text_low   = text_clean.lower()
    if len(text_clean) < 300 or len(text_clean) > 4000:
        return False
    if any(text_low.startswith(x) for x in _BAD_STARTS):
        return False
    return sum(k in text_low for k in _ABSTRACT_KEYWORDS) >= 2


def get_valid_abstract(
    text: str | None,
    fallback_text: str | None,
) -> tuple[str | None, bool]:
    if is_valid_abstract(text):
        return text, True
    fb = fallback_text if isinstance(fallback_text, str) else None
    return (fb[:1200] if fb else None), False


# ── DOI validation ────────────────────────────────────────────────────────────

_DOI_PATTERN = re.compile(r"10\.\d{4,9}/[-._;()/:A-Z0-9]+", re.IGNORECASE)


def is_valid_doi(doi: str | None) -> bool:
    if not doi or not isinstance(doi, str):
        return False
    return bool(_DOI_PATTERN.match(doi))


# ── Country force-detection ───────────────────────────────────────────────────

# Listed most-specific first: each entry maps a keyword → canonical country name.
# Ukraine-specific terms from the user spec are all included.
_COUNTRY_RULES: list[tuple[str, str]] = [
    # Ukraine — all specific geographic keywords that unambiguously indicate Ukraine
    ("kakhovka",          "Ukraine"),   # Kakhovka reservoir / dam
    ("dniester",          "Ukraine"),   # Dniester River
    ("dnister",           "Ukraine"),
    ("tisza",             "Ukraine"),   # Tisza / Tysa (Zakarpattia)
    ("tysa",              "Ukraine"),
    ("prut",              "Ukraine"),   # Prut River
    ("desna",             "Ukraine"),   # Desna River
    ("bukovyna",          "Ukraine"),
    ("bukovina",          "Ukraine"),
    ("zakarpattia",       "Ukraine"),
    ("transcarpathia",    "Ukraine"),
    ("carpathian",        "Ukraine"),
    ("dnipro",            "Ukraine"),
    ("dnieper",           "Ukraine"),
    ("ukrainian",         "Ukraine"),
    ("ukraine",           "Ukraine"),
    # Other countries
    ("bangladesh",        "Bangladesh"),
    ("vietnam",           "Vietnam"),
    ("pakistan",          "Pakistan"),
    ("eastern europe",    "Eastern Europe"),
]


def force_country_detection(text: str | None) -> str | None:
    """Scan raw text for geographic keywords; return canonical country/region name."""
    if not isinstance(text, str):
        return None
    text_low = text.lower()
    for keyword, country in _COUNTRY_RULES:
        if keyword in text_low:
            return country
    return None


# ── Sensor type normalization ─────────────────────────────────────────────────

_SAR_KEYWORDS = {
    "sentinel-1", "sar", "terrasar", "cosmo-skymed",
    "uavsar", "alos", "radarsat", "novasar", "ers", "envisat", "iceye",
}
_OPT_KEYWORDS = {
    "sentinel-2", "landsat", "modis", "viirs", "worldview",
    "pleiades", "spot", "planet", "aerial", "google earth",
}


def normalize_sensor_type(value: str | None) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    v = value.lower()
    has_sar = any(kw in v for kw in _SAR_KEYWORDS)
    has_opt = any(kw in v for kw in _OPT_KEYWORDS)
    if has_sar and has_opt:
        return "Multi-sensor"
    if has_sar:
        return "SAR"
    if has_opt:
        return "Optical"
    # already normalized value
    if value.strip() in ("SAR", "Optical", "Multi-sensor"):
        return value.strip()
    return value.strip()


# ── Study type normalization ──────────────────────────────────────────────────

_VALID_STUDY_TYPES = {
    "Satellite flood mapping",
    "ML/DL classification",
    "Hydrological forecasting",
    "Hydraulic modeling",
    "Operational mapping system",
    "Review paper",
    "Dataset/benchmark paper",
}


def normalize_study_type(value: str | None) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    v = value.strip()
    return v if v in _VALID_STUDY_TYPES else v


# ── Methods normalization ─────────────────────────────────────────────────────

_METHOD_ALIASES: dict[str, str] = {
    "u-net":                         "U-Net",
    "unet":                          "U-Net",
    "attention unet":                "U-Net",
    "attention u-net":               "U-Net",
    "random forest":                 "Random Forest",
    "rf":                            "Random Forest",
    "cnn":                           "CNN",
    "convolutional neural network":  "CNN",
    "fcnn":                          "CNN",
    "fully convolutional":           "CNN",
    "svm":                           "SVM",
    "support vector machine":        "SVM",
    "threshold":                     "Thresholding",
    "thresholding":                  "Thresholding",
    "otsu":                          "Thresholding",
    "ndwi":                          "NDWI/MNDWI",
    "mndwi":                         "NDWI/MNDWI",
    "change detection":              "Change detection",
    "hydrodynamic":                  "Hydrodynamic model",
    "hydraulic model":               "Hydrodynamic model",
    "hec-ras":                       "Hydrodynamic model",
    "lisflood":                      "Hydrodynamic model",
    "obia":                          "OBIA",
    "object-based":                  "OBIA",
    "operational workflow":          "Operational workflow",
    "copernicus ems":                "Operational workflow",
}


def normalize_methods(value: str | None) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    parts = [p.strip() for p in value.split(",") if p.strip()]
    normalized = []
    for p in parts:
        canon = _METHOD_ALIASES.get(p.lower(), p)
        normalized.append(canon)
    # deduplicate
    seen: set[str] = set()
    unique = [m for m in normalized if m not in seen and not seen.add(m)]  # type: ignore
    return ", ".join(unique) if unique else None


# ── DataFrame-level operations ────────────────────────────────────────────────

def clean_metadata_df(df: pd.DataFrame) -> pd.DataFrame:
    """Validate Abstract and DOI. Adds Abstract_Valid / DOI_Valid columns."""
    for col in ("Abstract", "DOI", "Full_Text"):
        if col not in df.columns:
            df[col] = None

    df["Abstract"], df["Abstract_Valid"] = zip(*df.apply(
        lambda row: get_valid_abstract(row["Abstract"], row.get("Full_Text")),
        axis=1,
    ))
    df["DOI_Valid"] = df["DOI"].apply(is_valid_doi)
    df["DOI"] = df.apply(
        lambda row: row["DOI"] if row["DOI_Valid"] else None,
        axis=1,
    )
    return df


def normalize_fields_df(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize Sensor_Type, Methods, Study_Type; fill null Country from Full_Text."""
    if "Sensor_Type" in df.columns:
        df["Sensor_Type"] = df["Sensor_Type"].apply(normalize_sensor_type)

    if "Methods" in df.columns:
        df["Methods"] = df["Methods"].apply(normalize_methods)

    if "Study_Type" in df.columns:
        df["Study_Type"] = df["Study_Type"].apply(normalize_study_type)

    if "Country" in df.columns:
        if "Full_Text" in df.columns:
            null_mask = df["Country"].isna() | (df["Country"].astype(str).str.strip() == "")
            df.loc[null_mask, "Country"] = (
                df.loc[null_mask, "Full_Text"].apply(force_country_detection)
            )

    return df


# ── Consistency filter + extraction scoring ───────────────────────────────────

_METRIC_COLS = ("OA", "F1", "IoU", "Kappa")


def _has_value(series_or_val) -> bool:
    if isinstance(series_or_val, pd.Series):
        val = series_or_val.iloc[0] if not series_or_val.empty else None
    else:
        val = series_or_val
    return bool(pd.notna(val) and str(val).strip() not in ("", "None", "nan", "Unknown"))


def _score_row(row: pd.Series) -> int:
    score = 0
    if _has_value(row.get("Satellite_Names")):  score += 2
    if _has_value(row.get("Country")):           score += 1
    if _has_value(row.get("Methods")):           score += 1
    if _has_value(row.get("Study_Type")):        score += 1
    if any(_has_value(row.get(m)) for m in _METRIC_COLS): score += 1
    return score


def apply_consistency_filter(
    df: pd.DataFrame,
    min_score: int = 2,
) -> pd.DataFrame:
    """
    Add Extraction_Score column and keep rows where
    (title AND satellite) OR score >= min_score.
    """
    df = df.copy()
    df["Extraction_Score"] = df.apply(_score_row, axis=1)

    def _col_has(series: pd.Series) -> pd.Series:
        return series.apply(lambda v: _has_value(v))

    has_title    = (_col_has(df["Title"])          if "Title"          in df.columns
                    else pd.Series(False, index=df.index))
    has_satellit = (_col_has(df["Satellite_Names"]) if "Satellite_Names" in df.columns
                    else pd.Series(False, index=df.index))

    # Global studies are methodological background — never filter them out
    is_global = (
        df["Country"].apply(lambda v: str(v).strip().lower() == "global")
        if "Country" in df.columns
        else pd.Series(False, index=df.index)
    )

    # Ukraine-relevant papers are always preserved
    is_ukraine = (
        df["Ukraine_Relevance"].apply(lambda v: v is True or v == True)  # noqa: E712
        if "Ukraine_Relevance" in df.columns
        else pd.Series(False, index=df.index)
    )

    keep = (has_title & has_satellit) | (df["Extraction_Score"] >= min_score) | is_global | is_ukraine
    dropped = int((~keep).sum())
    if dropped:
        logger.info(
            "Consistency filter removed %d low-quality rows (min_score=%d).",
            dropped, min_score,
        )
    return df[keep].reset_index(drop=True)


# ── Report ────────────────────────────────────────────────────────────────────

def print_report(df: pd.DataFrame) -> None:
    total           = len(df)
    valid_abstracts = int(df["Abstract_Valid"].sum()) if "Abstract_Valid" in df.columns else 0
    valid_dois      = int(df["DOI_Valid"].sum())      if "DOI_Valid"      in df.columns else 0
    has_metrics     = int(df["Metrics_Reported"].sum()) if "Metrics_Reported" in df.columns else 0
    nrt_count       = (int((df["Near_Real_Time"] == True).sum())  # noqa: E712
                       if "Near_Real_Time" in df.columns else 0)

    sar_count = opt_count = multi_count = 0
    if "Sensor_Type" in df.columns:
        sar_count   = int((df["Sensor_Type"] == "SAR").sum())
        opt_count   = int((df["Sensor_Type"] == "Optical").sum())
        multi_count = int((df["Sensor_Type"] == "Multi-sensor").sum())

    print("=" * 55)
    print(f"  Total papers:          {total}")
    print(f"  Valid abstracts:       {valid_abstracts}")
    print(f"  Valid DOIs:            {valid_dois}")
    print(f"  Papers with metrics:   {has_metrics}")
    print(f"  Near-real-time:        {nrt_count}")
    print(f"  SAR / Optical / Multi: {sar_count} / {opt_count} / {multi_count}")
    print("=" * 55)
