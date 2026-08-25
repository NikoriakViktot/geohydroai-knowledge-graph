"""
Analytics module for satellite-based flood mapping literature review.

Analyses: satellite usage, sensor types, task types, geographic coverage,
methodological approaches, timeliness, and (optionally) accuracy metrics.

Metrics are only analysed for papers where they are applicable and reported.
Missing metrics in review, hydraulic, or forecasting papers are NOT treated
as failures.
"""
from __future__ import annotations

import re

import pandas as pd


# ── Canonical categories ──────────────────────────────────────────────────────

SATELLITE_CANONICAL = [
    "Sentinel-1", "Sentinel-2", "Landsat", "MODIS", "VIIRS",
    "TerraSAR-X", "COSMO-SkyMed", "ALOS-2", "RADARSAT-2",
    "UAVSAR", "WorldView", "Pleiades", "Planet",
]

# Task types where metric absence is scientifically expected
_NON_METRIC_TASK_TYPES = {
    "Review paper",
    "Dataset/benchmark paper",
    "Hydrological forecasting",
    "Hydraulic modeling",
}

# Task types where metrics ARE expected (ML/DL / mapping accuracy)
_METRIC_TASK_TYPES = {
    "Satellite flood mapping",
    "ML/DL classification",
    "Operational mapping system",
}


# ── Geographic classification ─────────────────────────────────────────────────

# 6 output levels (in priority order)
GEO_RELEVANCE_LEVELS = [
    "Ukraine-specific",
    "Eastern Europe",
    "Europe",
    "Global",
    "Other region",
    "Unspecified",
]

# Ukraine-specific keywords — same set used by regex_extractor._UKRAINE_KW
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

_E_EUROPE_KW = {
    "poland", "romania", "serbia", "croatia", "slovenia",
    "moldova", "bulgaria", "hungary", "slovakia", "czech",
    "bosnia", "albania", "north macedonia", "eastern europe",
    "baltic", "estonia", "latvia", "lithuania", "belarus",
    "vistula", "oder", "tisza",   # rivers shared with E. Europe
}
_W_EUROPE_KW = {
    "france", "germany", "netherlands", "italy", "spain",
    "uk", "united kingdom", "england", "portugal", "austria",
    "switzerland", "belgium", "sweden", "norway", "denmark",
    "finland", "ireland", "scotland", "europe",
    "rhine", "danube", "elbe", "po", "thames", "loire",
}
_OTHER_KW = {
    "bangladesh", "india", "china", "vietnam", "pakistan",
    "japan", "thailand", "myanmar", "indonesia", "iran", "iraq",
    "cambodia", "philippines", "malaysia", "korea", "laos",
    "nepal", "sri lanka", "asia",
    "usa", "united states", "conus", "texas", "california",
    "florida", "louisiana", "north america", "canada", "mexico",
    "brazil", "peru", "colombia", "argentina", "south america",
    "nigeria", "ghana", "mozambique", "kenya", "africa",
    "morocco", "egypt", "australia",
}
_GLOBAL_KW = {
    "global", "worldwide", "multi-country", "international",
    "world-wide",
}


def classify_geo_relevance(
    country: str,
    region: str,
    river_basin: str,
    river_name: str = "",
    ukraine_relevance: bool = False,
) -> str:
    """
    Assign one of 6 geographic relevance levels.

    Priority order:
        1. Ukraine-specific  — any Ukraine keyword OR ukraine_relevance flag
        2. Eastern Europe    — E. European countries / keywords
        3. Europe            — W./N. European countries
        4. Global            — global/worldwide scope (preserved, not demoted)
        5. Other region      — Asia, Americas, Africa, Oceania
        6. Unspecified       — nothing found
    """
    combined = " ".join(
        str(v).lower()
        for v in (country, region, river_basin, river_name)
        if v and str(v) not in ("nan", "None", "")
    )

    if ukraine_relevance or any(k in combined for k in _UKRAINE_KW):
        return "Ukraine-specific"
    if any(k in combined for k in _E_EUROPE_KW):
        return "Eastern Europe"
    if any(k in combined for k in _W_EUROPE_KW):
        return "Europe"
    if any(k in combined for k in _GLOBAL_KW):
        return "Global"
    if any(k in combined for k in _OTHER_KW):
        return "Other region"

    # Non-empty text that matched no known category
    if combined.strip():
        return "Other region"
    return "Unspecified"


# Backward-compat alias used by generate_report.py
def classify_study_area(country: str, region: str, river_basin: str) -> str:
    return classify_geo_relevance(country, region, river_basin)


# ── Method group classification ───────────────────────────────────────────────

_METHOD_GROUPS: list[tuple[str, set[str]]] = [
    ("Thresholding / Change detection", {
        "thresholding", "change detection",
    }),
    ("Index-based (NDWI/MNDWI)", {
        "ndwi/mndwi", "ndwi", "mndwi",
    }),
    ("Classical ML", {
        "random forest", "svm", "maximum likelihood", "obia",
    }),
    ("Deep Learning", {
        "u-net", "cnn", "lstm", "transformer",
    }),
    ("Hydrological / Hydraulic model", {
        "hydrodynamic model",
    }),
    ("Operational workflow", {
        "operational workflow",
    }),
]


def classify_method_groups(methods_csv: str | None) -> list[str]:
    """
    Return all method group labels that apply to a comma-separated methods string.
    One paper can belong to multiple groups.
    """
    if not isinstance(methods_csv, str) or not methods_csv.strip():
        return []
    parts = {p.strip().lower() for p in methods_csv.split(",")}
    found = []
    for group_name, triggers in _METHOD_GROUPS:
        if parts & triggers:
            found.append(group_name)
    return found


# ── Satellite usage ───────────────────────────────────────────────────────────

def count_satellite_usage(df: pd.DataFrame) -> dict[str, int]:
    """
    Count how many papers mention each canonical satellite.
    One paper can use multiple satellites.
    """
    counts: dict[str, int] = {}
    if "Satellite_Names" not in df.columns:
        return counts
    for names_csv in df["Satellite_Names"].dropna():
        for name in str(names_csv).split(","):
            name = name.strip()
            if not name or name.lower() in ("nan", "none", ""):
                continue
            # Map to canonical form
            canon = _canonicalize_satellite(name)
            counts[canon] = counts.get(canon, 0) + 1
    return dict(sorted(counts.items(), key=lambda x: -x[1]))


def _canonicalize_satellite(name: str) -> str:
    n = name.strip()
    nl = n.lower()
    # Sentinel-1 variants: "sentinel-1", "sentinel 1", "sentinel-1a", "sentinel-1b",
    # "s1a", "s1b", "s-1", "s1"
    if re.search(r"sentinel[\s\-]?1[abc]?", nl) or re.search(r"\bs1[ab]?\b", nl):
        return "Sentinel-1"
    # Sentinel-2 variants: "sentinel-2", "sentinel 2", "sentinel-2a", "sentinel-2b",
    # "s2a", "s2b", "s-2", "s2"
    if re.search(r"sentinel[\s\-]?2[abc]?", nl) or re.search(r"\bs2[ab]?\b", nl):
        return "Sentinel-2"
    if "landsat" in nl:
        return "Landsat"
    if "modis" in nl:
        return "MODIS"
    if "viirs" in nl:
        return "VIIRS"
    if "terrasar" in nl:
        return "TerraSAR-X"
    if "cosmo" in nl:
        return "COSMO-SkyMed"
    if "alos" in nl:
        return "ALOS-2"
    if "radarsat" in nl:
        return "RADARSAT-2"
    if "uavsar" in nl:
        return "UAVSAR"
    if "worldview" in nl:
        return "WorldView"
    if "pleiades" in nl:
        return "Pleiades"
    if "planet" in nl:
        return "PlanetScope"
    return n.title()


# ── Main summary computation ──────────────────────────────────────────────────

def compute_summary(df: pd.DataFrame) -> dict:
    total = len(df)

    # ── Satellite usage ───────────────────────────────────────────────────────
    satellite_dist = count_satellite_usage(df)

    # ── Sensor type distribution ──────────────────────────────────────────────
    sensor_dist: dict[str, int] = {}
    if "Sensor_Type" in df.columns:
        sensor_dist = (
            df["Sensor_Type"]
            .dropna()
            .replace("", None)
            .dropna()
            .value_counts()
            .to_dict()
        )

    # ── Task type distribution ────────────────────────────────────────────────
    task_dist: dict[str, int] = {}
    if "Study_Type" in df.columns:
        task_dist = df["Study_Type"].dropna().value_counts().to_dict()

    # ── Geographic coverage ───────────────────────────────────────────────────
    def _area(row: pd.Series) -> str:
        return classify_geo_relevance(
            row.get("Country",           ""),
            row.get("Region",            ""),
            row.get("River_Basin",       ""),
            row.get("River_Name",        ""),
            bool(row.get("Ukraine_Relevance", False)),
        )

    area_dist: dict[str, int] = df.apply(_area, axis=1).value_counts().to_dict()

    # Ukraine relevance count (papers with the flag set)
    ukraine_relevance_count = 0
    if "Ukraine_Relevance" in df.columns:
        ukraine_relevance_count = int((df["Ukraine_Relevance"] == True).sum())  # noqa: E712

    # ── Basin / event coverage ────────────────────────────────────────────────
    basin_dist: dict[str, int] = {}
    for col_name in ("River_Basin", "River_Name"):
        if col_name in df.columns:
            for v in df[col_name].dropna():
                for part in str(v).split(","):
                    part = part.strip()
                    if part and part.lower() not in ("nan", "none", ""):
                        basin_dist[part] = basin_dist.get(part, 0) + 1
    basin_dist = dict(sorted(basin_dist.items(), key=lambda x: -x[1]))

    # ── Method group distribution ─────────────────────────────────────────────
    # Count papers per group (one paper can appear in multiple groups)
    method_group_dist: dict[str, int] = {}
    col = "Methods" if "Methods" in df.columns else None
    if col:
        for methods_val in df[col]:
            for group in classify_method_groups(methods_val):
                method_group_dist[group] = method_group_dist.get(group, 0) + 1

    # ── Individual method frequency ───────────────────────────────────────────
    method_detail: dict[str, int] = {}
    if col:
        for methods_val in df[col].dropna():
            for m in str(methods_val).split(","):
                m = m.strip()
                if m and m.lower() not in ("nan", "none", ""):
                    method_detail[m] = method_detail.get(m, 0) + 1

    # ── Timeliness ────────────────────────────────────────────────────────────
    nrt_count = 0
    if "Near_Real_Time" in df.columns:
        nrt_count = int((df["Near_Real_Time"] == True).sum())  # noqa: E712

    # ── Metrics — ONLY for applicable papers ──────────────────────────────────
    # Identify papers where metrics are scientifically expected
    if "Study_Type" in df.columns:
        metric_eligible = df[df["Study_Type"].isin(_METRIC_TASK_TYPES)].copy()
    else:
        metric_eligible = df.copy()

    metrics_reported   = int(metric_eligible["Metrics_Reported"].sum()) \
                         if "Metrics_Reported" in metric_eligible.columns else 0
    metrics_expected   = len(metric_eligible)

    # Numeric metric stats (only from eligible papers that actually report them)
    if "Metrics_Reported" in metric_eligible.columns:
        has_metrics_df = metric_eligible[metric_eligible["Metrics_Reported"] == True]  # noqa: E712
    else:
        has_metrics_df = metric_eligible

    def _stat(col_name: str) -> dict:
        if col_name not in has_metrics_df.columns:
            return {}
        vals = pd.to_numeric(has_metrics_df[col_name], errors="coerce").dropna()
        if vals.empty:
            return {}
        return {
            "n":    int(len(vals)),
            "mean": round(float(vals.mean()), 3),
            "min":  round(float(vals.min()), 3),
            "max":  round(float(vals.max()), 3),
            "median": round(float(vals.median()), 3),
        }

    # Metric stats per method group (for papers that have metrics)
    metric_by_group: dict[str, dict] = {}
    if col and not has_metrics_df.empty:
        for group_name, _ in _METHOD_GROUPS:
            mask = has_metrics_df[col].apply(
                lambda v: group_name in classify_method_groups(v)
            )
            grp_df = has_metrics_df[mask]
            if grp_df.empty:
                continue
            oa_vals = pd.to_numeric(grp_df.get("OA"), errors="coerce").dropna()
            f1_vals = pd.to_numeric(grp_df.get("F1"), errors="coerce").dropna()
            if oa_vals.empty and f1_vals.empty:
                continue
            metric_by_group[group_name] = {
                "n": len(grp_df),
                "oa_mean": round(float(oa_vals.mean()), 3) if not oa_vals.empty else None,
                "f1_mean": round(float(f1_vals.mean()), 3) if not f1_vals.empty else None,
            }

    return {
        "total":                  total,
        # satellite / sensor
        "satellite_distribution": satellite_dist,
        "sensor_distribution":    sensor_dist,
        # task types
        "task_distribution":      task_dist,
        # geographic
        "area_distribution":          area_dist,
        "basin_distribution":         basin_dist,
        "ukraine_relevance_count":    ukraine_relevance_count,
        # methods
        "method_group_dist":      method_group_dist,
        "method_detail":          dict(sorted(method_detail.items(), key=lambda x: -x[1])),
        # timeliness
        "nrt_count":              nrt_count,
        # metrics
        "metrics_expected":       metrics_expected,
        "metrics_reported":       metrics_reported,
        "oa_stats":               _stat("OA"),
        "f1_stats":               _stat("F1"),
        "iou_stats":              _stat("IoU"),
        "kappa_stats":            _stat("Kappa"),
        "metric_by_group":        metric_by_group,
    }


# ── Console display ───────────────────────────────────────────────────────────

def _pct(n: int, total: int) -> str:
    return f"{round(100 * n / max(total, 1))}%" if total else "N/A"


def _table(title: str, data: dict, total: int, width: int = 50) -> str:
    lines = [f"\n{'─' * width}", f"  {title}", f"{'─' * width}"]
    for key, count in sorted(data.items(), key=lambda x: -x[1]):
        lines.append(f"  {key:<35} {count:>4}  ({_pct(count, total)})")
    return "\n".join(lines)


def _stat_line(label: str, stats: dict) -> str:
    if not stats:
        return f"  {label}: no data"
    return (
        f"  {label}: n={stats['n']}  "
        f"mean={stats['mean']:.3f}  "
        f"[{stats['min']:.3f} – {stats['max']:.3f}]  "
        f"median={stats['median']:.3f}"
    )


def print_summary_tables(df: pd.DataFrame) -> None:
    s     = compute_summary(df)
    total = s["total"]
    W     = 55

    print("\n" + "=" * W)
    print("  SATELLITE FLOOD MAPPING — LITERATURE REVIEW")
    print("=" * W)
    print(f"  Total papers processed: {total}")

    print(_table("TABLE 1 — Satellite Usage (papers per satellite)",
                 s["satellite_distribution"], total, W))
    print(_table("TABLE 2 — Sensor Type Distribution",
                 s["sensor_distribution"], total, W))
    print(_table("TABLE 3 — Task Type Distribution",
                 s["task_distribution"], total, W))
    print(_table("TABLE 4 — Geographic Relevance (6-level)",
                 s["area_distribution"], total, W))

    ukr = s["ukraine_relevance_count"]
    print(f"\n  ► Ukraine-relevant papers (keyword flag): {ukr}  ({_pct(ukr, total)})")

    if s["basin_distribution"]:
        print(_table("TABLE 4b — River Basins / Events",
                     s["basin_distribution"], total, W))

    print(_table("TABLE 5 — Method Group Distribution (papers per group)",
                 s["method_group_dist"], total, W))

    print(f"\n{'─' * W}")
    print("  TABLE 6 — Timeliness")
    print(f"{'─' * W}")
    print(f"  Near-real-time capable papers: {s['nrt_count']}  ({_pct(s['nrt_count'], total)})")

    print(f"\n{'─' * W}")
    print("  TABLE 7 — Accuracy Metrics")
    print(f"  (only for task types where metrics are expected)")
    print(f"{'─' * W}")
    me = s["metrics_expected"]
    mr = s["metrics_reported"]
    print(f"  Papers where metrics are expected:  {me}")
    print(f"  Papers that report metrics:         {mr}  ({_pct(mr, me)})")
    print()
    print(_stat_line("OA",    s["oa_stats"]))
    print(_stat_line("F1",    s["f1_stats"]))
    print(_stat_line("IoU",   s["iou_stats"]))
    print(_stat_line("Kappa", s["kappa_stats"]))

    if s["metric_by_group"]:
        print(f"\n{'─' * W}")
        print("  TABLE 7b — Mean Metrics by Method Group")
        print(f"{'─' * W}")
        for grp, stats in s["metric_by_group"].items():
            oa_str = f"{stats['oa_mean']:.3f}" if stats.get("oa_mean") else "—"
            f1_str = f"{stats['f1_mean']:.3f}" if stats.get("f1_mean") else "—"
            print(f"  {grp:<40} n={stats['n']}  OA={oa_str}  F1={f1_str}")

    print("=" * W)
