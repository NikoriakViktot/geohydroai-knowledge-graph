"""
Flood mapping literature review — Markdown report generator.

Outputs
-------
  outputs/literature_review_report.md
  outputs/final_dataset.csv
"""
from __future__ import annotations

import logging
import os
import warnings
from datetime import date
from pathlib import Path

import pandas as pd

warnings.filterwarnings("ignore")
logging.basicConfig(level=logging.WARNING)

os.environ.setdefault("HF_HOME", "/tmp/hf_cache")
os.environ.setdefault("SENTENCE_TRANSFORMERS_HOME", "/tmp/hf_cache/sentence_transformers")

from src.pipeline.rag_pipeline import RAGPipeline
from src.processing.analytics import (
    classify_method_groups,
    classify_study_area,
    compute_summary,
    print_summary_tables,
    _NON_METRIC_TASK_TYPES,
    _METRIC_TASK_TYPES,
)

OUTPUTS      = Path("outputs")
OUTPUTS.mkdir(exist_ok=True)
REPORT_PATH  = OUTPUTS / "literature_review_report.md"
DATASET_PATH = OUTPUTS / "final_dataset.csv"


# ── 1. Run pipeline ───────────────────────────────────────────────────────────

print("Running RAG pipeline …")
pipeline = RAGPipeline()
df = pipeline.query(save_csv=False)
print(f"Extracted {len(df)} papers.")

# ── 2. Print console tables ───────────────────────────────────────────────────

print_summary_tables(df)
s = compute_summary(df)

# ── 3. Shared formatting helpers ──────────────────────────────────────────────

TODAY = date.today().isoformat()


def _fmt(val, decimals: int = 3) -> str:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return "—"
    if isinstance(val, float):
        return f"{val:.{decimals}f}"
    s = str(val).strip()
    return s if s not in ("", "nan", "None") else "—"


def _pct(n: int, total: int) -> str:
    return f"{round(100 * n / max(total, 1))}%"


def _md_table(headers: list[str], rows: list[list]) -> str:
    if not rows:
        return "*No data.*"
    col_w = [
        max(len(h), max((len(str(r[i])) for r in rows), default=0))
        for i, h in enumerate(headers)
    ]
    sep  = "| " + " | ".join("-" * w for w in col_w) + " |"
    head = "| " + " | ".join(h.ljust(col_w[i]) for i, h in enumerate(headers)) + " |"
    body = "\n".join(
        "| " + " | ".join(str(r[i]).ljust(col_w[i]) for i in range(len(headers))) + " |"
        for r in rows
    )
    return "\n".join([head, sep, body])


def _dist_table(dist: dict, total: int) -> str:
    rows = sorted(dist.items(), key=lambda x: -x[1])
    return _md_table(
        ["Category", "Count", "Share"],
        [[k, str(v), _pct(v, total)] for k, v in rows],
    )


def _stat_row(label: str, stats: dict) -> list:
    if not stats:
        return [label, "—", "—", "—", "—", "—"]
    return [
        label,
        str(stats["n"]),
        f"{stats['mean']:.3f}",
        f"{stats['median']:.3f}",
        f"{stats['min']:.3f}",
        f"{stats['max']:.3f}",
    ]


# ── Derived subsets ───────────────────────────────────────────────────────────

total       = s["total"]
doi_valid   = int(df["DOI_Valid"].sum())   if "DOI_Valid"   in df.columns else 0
abs_valid   = int(df["Abstract_Valid"].sum()) if "Abstract_Valid" in df.columns else 0

# Papers where metrics are expected vs. not
if "Study_Type" in df.columns:
    metric_eligible_df = df[df["Study_Type"].isin(_METRIC_TASK_TYPES)]
    non_metric_df      = df[df["Study_Type"].isin(_NON_METRIC_TASK_TYPES)]
else:
    metric_eligible_df = df
    non_metric_df      = df.iloc[0:0]

metrics_expected = len(metric_eligible_df)
metrics_reported = int(metric_eligible_df["Metrics_Reported"].sum()) \
                   if "Metrics_Reported" in metric_eligible_df.columns else 0

# Regional subsets
def _in_area(row: pd.Series, target: str) -> bool:
    return classify_study_area(
        row.get("Country", ""), row.get("Region", ""), row.get("River_Basin", "")
    ) == target

ukraine_df  = df[df.apply(lambda r: _in_area(r, "Ukraine"),       axis=1)]
e_europe_df = df[df.apply(lambda r: _in_area(r, "Eastern Europe"), axis=1)]
asia_df     = df[df.apply(lambda r: _in_area(r, "Asia"),           axis=1)]

# ── 4. Build Markdown ─────────────────────────────────────────────────────────

lines: list[str] = []

# ── Title ─────────────────────────────────────────────────────────────────────
lines += [
    "# Satellite-Based Flood Mapping: Systematic Literature Review",
    "",
    f"*Generated: {TODAY}  |  Pipeline: RAG + Regex + Metadata Validation*",
    "",
]

# ── 1. Introduction ───────────────────────────────────────────────────────────
lines += [
    "## 1. Introduction",
    "",
    f"This report presents the results of an automated systematic literature review "
    f"covering {total} papers on satellite-based flood mapping. "
    f"The review was conducted using a RAG (Retrieval-Augmented Generation) pipeline "
    f"that extracts structured metadata from PDF documents, including satellite and sensor "
    f"information, study area, processing methods, task type, and timeliness characteristics. "
    f"Accuracy metrics are analysed only for papers where their reporting is scientifically "
    f"expected (mapping and classification studies); their absence in review, forecasting, "
    f"or hydraulic modelling papers is not treated as a quality deficiency.",
    "",
]

# ── 2. Dataset Overview ───────────────────────────────────────────────────────
lines += [
    "## 2. Dataset Overview",
    "",
    _md_table(
        ["Attribute", "Value"],
        [
            ["Total papers processed",              str(total)],
            ["Papers with valid DOI",               f"{doi_valid} ({_pct(doi_valid, total)})"],
            ["Papers with valid abstract",          f"{abs_valid} ({_pct(abs_valid, total)})"],
            ["Papers where metrics are expected",   str(metrics_expected)],
            ["… that report numeric metrics",       f"{metrics_reported} ({_pct(metrics_reported, metrics_expected)})"],
            ["Non-metric paper types (review / hydraulic / forecasting)", str(len(non_metric_df))],
            ["Near-real-time capable papers",       f"{s['nrt_count']} ({_pct(s['nrt_count'], total)})"],
        ],
    ),
    "",
]

# ── 3. Satellite and Sensor Analysis ─────────────────────────────────────────
lines += ["## 3. Satellite and Sensor Analysis", ""]

# 3.1 Satellite distribution
sat_dist = s["satellite_distribution"]
lines += [
    "### 3.1 Satellite Usage",
    "",
    "> One paper may use multiple satellites. Counts reflect the number of papers citing each satellite.",
    "",
    _dist_table(sat_dist, total),
    "",
]

# Prose commentary
top_sats = list(sat_dist.items())[:3]
if top_sats:
    top_names = ", ".join(f"**{k}** ({v} papers, {_pct(v, total)})" for k, v in top_sats)
    lines += [
        f"The three most frequently used satellites are {top_names}. "
        f"Sentinel-1's dominance reflects its free and open access, short revisit cycle "
        f"(6 days at the equator), and all-weather SAR imaging capability — "
        f"critical advantages during active flood events when optical imagery is obscured "
        f"by cloud cover.",
        "",
    ]

# 3.2 Sensor type
sen_dist = s["sensor_distribution"]
sar_n    = sen_dist.get("SAR", 0)
opt_n    = sen_dist.get("Optical", 0)
multi_n  = sen_dist.get("Multi-sensor", 0)

lines += [
    "### 3.2 Sensor Type Distribution",
    "",
    _dist_table(sen_dist, total),
    "",
    f"SAR sensors are used in **{sar_n + multi_n}** of {total} papers "
    f"({_pct(sar_n + multi_n, total)}), either exclusively ({sar_n} papers) "
    f"or in combination with optical data ({multi_n} multi-sensor papers). "
    f"Pure optical studies account for {opt_n} papers ({_pct(opt_n, total)}), "
    f"primarily using Sentinel-2 and Landsat for index-based water detection "
    f"(NDWI/MNDWI) when cloud-free imagery is available. "
    f"Multi-sensor fusion studies ({multi_n}, {_pct(multi_n, total)}) indicate "
    f"growing interest in combining SAR flood masks with optical spectral indices "
    f"to improve accuracy and spatial detail.",
    "",
]

# ── 4. Task Type Analysis ─────────────────────────────────────────────────────
lines += ["## 4. Task Type Analysis", ""]
task_dist = s["task_distribution"]
lines += [
    _dist_table(task_dist, total),
    "",
]

# Task-type prose
mapping_n  = task_dist.get("Satellite flood mapping",   0)
ml_n       = task_dist.get("ML/DL classification",      0)
hydro_n    = task_dist.get("Hydrological forecasting",  0)
hydraul_n  = task_dist.get("Hydraulic modeling",        0)
ops_n      = task_dist.get("Operational mapping system", 0)
review_n   = task_dist.get("Review paper",              0)
dataset_n  = task_dist.get("Dataset/benchmark paper",   0)

lines += [
    f"The largest category is **satellite flood mapping** ({mapping_n} papers, "
    f"{_pct(mapping_n, total)}), covering studies that directly derive flood extent "
    f"from satellite observations. **ML/DL classification** papers ({ml_n}, "
    f"{_pct(ml_n, total)}) constitute a rapidly growing sub-domain, applying "
    f"supervised learning to flood segmentation. "
    f"Operational and near-real-time mapping systems represent {ops_n} studies "
    f"({_pct(ops_n, total)}), reflecting the applied demand for timely flood products. "
    f"Hydrological forecasting ({hydro_n}) and hydraulic modelling ({hydraul_n}) "
    f"papers address different phases of flood management — prediction and simulation — "
    f"and are typically evaluated without pixel-level accuracy metrics. "
    f"Review and benchmark papers ({review_n + dataset_n}) provide synthesis across "
    f"the literature and are not expected to report primary accuracy results.",
    "",
]

# ── 5. Geographic Coverage ────────────────────────────────────────────────────
lines += ["## 5. Geographic Coverage", ""]
area_dist = s["area_distribution"]
lines += [
    "### 5.1 Regional Distribution",
    "",
    _dist_table(area_dist, total),
    "",
]

# Basin coverage
basin_dist = s["basin_distribution"]
if basin_dist:
    lines += [
        "### 5.2 River Basins and Events",
        "",
        _dist_table(basin_dist, total),
        "",
    ]

# Geographic prose
top_area    = max(area_dist, key=area_dist.get) if area_dist else "unknown"
asia_n      = area_dist.get("Asia", 0)
ukraine_n   = area_dist.get("Ukraine", 0)
e_europe_n  = area_dist.get("Eastern Europe", 0)
n_am_n      = area_dist.get("North America", 0)

lines += [
    f"Asia — primarily Bangladesh, India, China, and Vietnam — represents the most "
    f"frequently studied region ({asia_n} papers, {_pct(asia_n, total)}), driven by "
    f"high flood frequency and severe socio-economic impacts in monsoon-affected river "
    f"basins. North America accounts for {n_am_n} papers ({_pct(n_am_n, total)}), "
    f"with studies concentrated on major riverine flood events (Mississippi, Missouri). "
    f"Ukraine and its river basins (Dnipro, Carpathians) are represented in "
    f"{ukraine_n} papers ({_pct(ukraine_n, total)}), and Eastern Europe more broadly "
    f"in {e_europe_n} ({_pct(e_europe_n, total)}). The relative under-representation "
    f"of Eastern Europe highlights a gap that warrants targeted future studies, "
    f"particularly given recent large-scale flood events in the region.",
    "",
]

# Ukraine details
lines += ["### 5.3 Ukraine and Eastern Europe Focus", ""]

if len(ukraine_df) > 0:
    lines += [
        f"**{len(ukraine_df)} papers** in this review reference Ukraine or its "
        f"sub-regions (Dnipro Basin, Carpathians):",
        "",
        _md_table(
            ["Source File", "Study Type", "Satellite", "Methods", "NRT"],
            [
                [
                    row.get("Source_File", "—")[:45],
                    _fmt(row.get("Study_Type")),
                    _fmt(row.get("Satellite_Names")),
                    _fmt(row.get("Methods")),
                    "Yes" if row.get("Near_Real_Time") is True else "—",
                ]
                for _, row in ukraine_df.iterrows()
            ],
        ),
        "",
    ]
else:
    lines += [
        "No papers in the current extraction were explicitly linked to Ukraine or "
        "its sub-regions through the retrieved text. This may reflect the collection "
        "coverage or chunk-retrieval not surfacing geographic mentions.",
        "",
    ]

if len(e_europe_df) > 0:
    lines += [
        f"**{len(e_europe_df)} papers** cover Eastern European countries (excluding Ukraine):",
        "",
        _md_table(
            ["Source File", "Study Type", "Country", "Satellite"],
            [
                [
                    row.get("Source_File", "—")[:45],
                    _fmt(row.get("Study_Type")),
                    _fmt(row.get("Country")),
                    _fmt(row.get("Satellite_Names")),
                ]
                for _, row in e_europe_df.iterrows()
            ],
        ),
        "",
    ]

# ── 6. Methodological Approaches ─────────────────────────────────────────────
lines += ["## 6. Methodological Approaches", ""]
mg_dist = s["method_group_dist"]

lines += [
    "> One paper may apply multiple method types. Counts reflect papers per group.",
    "",
    _dist_table(mg_dist, total),
    "",
]

# Method detail table (top-N individual methods)
detail = sorted(s["method_detail"].items(), key=lambda x: -x[1])[:14]
if detail:
    lines += [
        "**Individual method occurrence (top 14):**",
        "",
        _md_table(
            ["Method", "Count", "Share", "Group"],
            [
                [
                    m,
                    str(cnt),
                    _pct(cnt, total),
                    (classify_method_groups(m) or ["Other"])[0],
                ]
                for m, cnt in detail
            ],
        ),
        "",
    ]

# Method prose
thresh_n = mg_dist.get("Thresholding / Change detection", 0)
index_n  = mg_dist.get("Index-based (NDWI/MNDWI)",       0)
ml_g_n   = mg_dist.get("Classical ML",                    0)
dl_g_n   = mg_dist.get("Deep Learning",                   0)
hydro_g_n = mg_dist.get("Hydrological / Hydraulic model", 0)
ops_g_n  = mg_dist.get("Operational workflow",             0)

lines += [
    f"**Thresholding and change detection** ({thresh_n} papers, {_pct(thresh_n, total)}) "
    f"remain the backbone of SAR-based operational flood mapping, offering computational "
    f"efficiency and interpretability without requiring labelled training data. "
    f"**Index-based methods** (NDWI/MNDWI, {index_n}, {_pct(index_n, total)}) are the "
    f"dominant approach for optical sensors, exploiting the contrast between water and "
    f"non-water surfaces in near-infrared and shortwave-infrared bands. "
    f"**Deep learning architectures** ({dl_g_n}, {_pct(dl_g_n, total)}) — primarily "
    f"U-Net and CNN variants — have grown substantially in recent years, offering "
    f"state-of-the-art accuracy when sufficient labelled data are available. "
    f"**Classical ML** methods (Random Forest, SVM; {ml_g_n}, {_pct(ml_g_n, total)}) "
    f"occupy an intermediate position, requiring less data than DL but more "
    f"than threshold-based approaches. "
    f"**Hydrological and hydraulic models** ({hydro_g_n}, {_pct(hydro_g_n, total)}) "
    f"simulate flood dynamics from meteorological and terrain inputs, and are increasingly "
    f"coupled with satellite-derived flood masks for validation and assimilation. "
    f"**Operational workflow** implementations ({ops_g_n}, {_pct(ops_g_n, total)}) "
    f"describe production-grade systems such as Copernicus EMS and similar platforms "
    f"designed for rapid response during active flood events.",
    "",
]

# ── 7. Timeliness and Operational Capability ──────────────────────────────────
lines += ["## 7. Timeliness and Operational Capability", ""]

nrt_n = s["nrt_count"]
lines += [
    f"**{nrt_n} papers** ({_pct(nrt_n, total)}) describe near-real-time (NRT) or "
    f"rapid-mapping flood products. These systems are characterised by automated "
    f"processing chains that deliver flood extent maps within hours of satellite "
    f"acquisition, typically exploiting the short revisit cycles of Sentinel-1 "
    f"(6-day repeat, 12 days at mid-latitudes) and MODIS/VIIRS (daily).",
    "",
]

# NRT paper detail
if "Near_Real_Time" in df.columns:
    nrt_df = df[df["Near_Real_Time"] == True]  # noqa: E712
    if not nrt_df.empty:
        lines += [
            _md_table(
                ["Paper", "Satellite", "Methods", "Latency", "Revisit"],
                [
                    [
                        row.get("Source_File", "—")[:40],
                        _fmt(row.get("Satellite_Names")),
                        _fmt(row.get("Methods")),
                        _fmt(row.get("Latency")),
                        _fmt(row.get("Revisit_Time")),
                    ]
                    for _, row in nrt_df.iterrows()
                ],
            ),
            "",
        ]

# ── 8. Accuracy Metrics ───────────────────────────────────────────────────────
lines += ["## 8. Accuracy Metrics", ""]
lines += [
    "> Metrics are analysed **only** for papers classified as *Satellite flood mapping*, "
    "> *ML/DL classification*, or *Operational mapping system*. "
    "> Review papers, hydrological forecasting papers, and hydraulic modelling studies "
    "> are excluded from this section — their absence of pixel-level accuracy metrics "
    "> reflects the nature of their contribution, not a quality deficiency.",
    "",
    _md_table(
        ["Category", "Value"],
        [
            ["Papers where metrics are expected",  str(metrics_expected)],
            ["Papers reporting at least one metric", f"{metrics_reported} ({_pct(metrics_reported, metrics_expected)})"],
            ["Papers without metrics (expected types)", f"{metrics_expected - metrics_reported}"],
        ],
    ),
    "",
]

# Metric statistics
stat_rows = [
    _stat_row("Overall Accuracy (OA)", s["oa_stats"]),
    _stat_row("F1 Score",              s["f1_stats"]),
    _stat_row("IoU",                   s["iou_stats"]),
    _stat_row("Cohen's Kappa",         s["kappa_stats"]),
]
lines += [
    "### 8.1 Metric Statistics (across papers that report each metric)",
    "",
    _md_table(
        ["Metric", "n", "Mean", "Median", "Min", "Max"],
        stat_rows,
    ),
    "",
]

# Metrics by method group
if s["metric_by_group"]:
    mbg_rows = []
    for grp, stats in s["metric_by_group"].items():
        oa_str = f"{stats['oa_mean']:.3f}" if stats.get("oa_mean") is not None else "—"
        f1_str = f"{stats['f1_mean']:.3f}" if stats.get("f1_mean") is not None else "—"
        mbg_rows.append([grp, str(stats["n"]), oa_str, f1_str])

    lines += [
        "### 8.2 Mean Metrics by Method Group",
        "",
        _md_table(
            ["Method Group", "n papers", "Mean OA", "Mean F1"],
            mbg_rows,
        ),
        "",
        "**Interpretation:** Deep learning architectures (U-Net, CNN) generally achieve "
        "higher mean F1 scores than threshold-based approaches, reflecting their capacity "
        "to learn complex spectral and spatial patterns. However, threshold-based methods "
        "often perform comparably on high-quality SAR data for clear open-water floods, "
        "with the advantage of requiring no training data.",
        "",
    ]

# Individual papers with metrics
if not metric_eligible_df.empty and "Metrics_Reported" in metric_eligible_df.columns:
    with_metrics = metric_eligible_df[metric_eligible_df["Metrics_Reported"] == True]  # noqa: E712
    if not with_metrics.empty:
        lines += [
            "### 8.3 Per-Paper Metric Summary",
            "",
            _md_table(
                ["Paper", "Study Type", "Satellite", "Methods", "OA", "F1", "IoU", "Kappa"],
                [
                    [
                        row.get("Source_File", "—")[:38],
                        _fmt(row.get("Study_Type")),
                        _fmt(row.get("Satellite_Names")),
                        _fmt(row.get("Methods")),
                        _fmt(row.get("OA")),
                        _fmt(row.get("F1")),
                        _fmt(row.get("IoU")),
                        _fmt(row.get("Kappa")),
                    ]
                    for _, row in with_metrics.sort_values(
                        "OA", ascending=False, na_position="last"
                    ).iterrows()
                ],
            ),
            "",
        ]

# ── 9. Per-Study Profiles ─────────────────────────────────────────────────────
lines += ["## 9. Per-Study Profiles", ""]

for i, (_, row) in enumerate(df.iterrows(), start=1):
    fname      = row.get("Source_File", "Unknown")
    title      = _fmt(row.get("Title"))
    authors    = _fmt(row.get("Authors"))
    year       = _fmt(row.get("Year"), 0)
    doi_raw    = row.get("DOI")
    doi_str    = f"[{doi_raw}](https://doi.org/{doi_raw})" if pd.notna(doi_raw) else "—"
    study_type = _fmt(row.get("Study_Type"))
    satellites = _fmt(row.get("Satellite_Names"))
    sensor     = _fmt(row.get("Sensor_Type"))
    data_prod  = _fmt(row.get("Data_Product"))
    country    = _fmt(row.get("Country"))
    region     = _fmt(row.get("Region"))
    basin      = _fmt(row.get("River_Basin"))
    event      = _fmt(row.get("City_Event"))
    methods    = _fmt(row.get("Methods"))
    nrt        = "Yes" if row.get("Near_Real_Time") is True else "—"
    latency    = _fmt(row.get("Latency"))
    revisit    = _fmt(row.get("Revisit_Time"))
    oa         = _fmt(row.get("OA"))
    f1         = _fmt(row.get("F1"))
    iou        = _fmt(row.get("IoU"))
    kappa      = _fmt(row.get("Kappa"))
    score      = _fmt(row.get("Extraction_Score"), 0)

    # Build metric row only for applicable papers
    has_metric_context = study_type in _METRIC_TASK_TYPES or study_type == "—"
    metric_rows = []
    if has_metric_context:
        metric_rows = [
            ["OA",    oa],
            ["F1",    f1],
            ["IoU",   iou],
            ["Kappa", kappa],
        ]

    table_rows = [
        ["Title",          title],
        ["Authors",        authors],
        ["Year",           year],
        ["DOI",            doi_str],
        ["Study Type",     study_type],
        ["Satellite(s)",   satellites],
        ["Sensor Type",    sensor],
        ["Data Product",   data_prod],
        ["Country",        country],
        ["Region",         region],
        ["River Basin",    basin],
        ["Event",          event],
        ["Methods",        methods],
        ["Near-Real-Time", nrt],
        ["Latency",        latency],
        ["Revisit Time",   revisit],
    ] + metric_rows + [
        ["Extraction Score", score],
    ]

    lines += [
        f"### Paper {i} — `{fname}`",
        "",
        _md_table(["Field", "Value"], table_rows),
        "",
    ]

# ── 10. Key Findings ──────────────────────────────────────────────────────────
lines += [
    "## 10. Key Findings",
    "",
    "### 10.1 SAR Dominance in Operational Flood Mapping",
    "",
    f"Synthetic Aperture Radar sensors are used in "
    f"**{sar_n + multi_n}** of {total} papers "
    f"({_pct(sar_n + multi_n, total)}), either exclusively or in "
    f"multi-sensor configurations. Sentinel-1 is the single most-used "
    f"satellite, reflecting its free and open data policy, all-weather "
    f"capability, and systematic global acquisition mode. SAR-based "
    f"thresholding and change detection remain the most widely deployed "
    f"approaches for near-real-time flood mapping.",
    "",
    "### 10.2 Rise of Deep Learning for Classification",
    "",
    f"Deep learning methods ({dl_g_n} papers, {_pct(dl_g_n, total)}) — "
    f"primarily U-Net encoder-decoder architectures — have become the "
    f"dominant approach in academic flood classification research. They "
    f"consistently achieve higher F1 and IoU scores than classical ML or "
    f"threshold-based methods when trained on large annotated datasets. "
    f"However, their deployment in operational systems remains limited "
    f"due to data requirements and computational overhead.",
    "",
    "### 10.3 Optical Sensors for Index-Based Detection",
    "",
    f"Optical sensors (Sentinel-2, Landsat; {opt_n} pure-optical papers) "
    f"are used primarily through water indices (NDWI/MNDWI), which offer "
    f"straightforward thresholdable water masks without training data. "
    f"Their main limitation — cloud cover during active flood events — "
    f"drives the preference for SAR in operational contexts.",
    "",
    "### 10.4 Multi-Sensor Fusion",
    "",
    f"Multi-sensor studies ({multi_n}, {_pct(multi_n, total)}) combine "
    f"SAR flood masks with optical imagery or ancillary datasets "
    f"(DEMs, land cover) to improve accuracy, reduce commission errors "
    f"in urban areas, and estimate flood depth. This fusion approach "
    f"is expected to grow as cloud-native platforms (Google Earth Engine, "
    f"Copernicus DIAS) lower the barrier to multi-source analysis.",
    "",
    "### 10.5 Geographic Gaps — Eastern Europe",
    "",
    f"Asia ({asia_n} papers) and North America ({n_am_n}) dominate the "
    f"geographic coverage. Ukraine ({ukraine_n}) and Eastern Europe "
    f"({e_europe_n}) are under-represented relative to their flood risk, "
    f"highlighting a need for studies targeting the Dnipro Basin, "
    f"Carpathian catchments, and Danube tributaries using existing "
    f"free-access Sentinel-1/2 archives.",
    "",
    "### 10.6 Timeliness as a Distinct Research Dimension",
    "",
    f"Only {nrt_n} papers ({_pct(nrt_n, total)}) explicitly address "
    f"near-real-time delivery. Latency and revisit time are rarely "
    f"reported as primary performance indicators, despite being critical "
    f"for emergency response. Future studies should adopt timeliness "
    f"metrics alongside accuracy metrics as first-class evaluation criteria.",
    "",
    "### 10.7 Metric Reporting Applies to a Subset",
    "",
    f"Numeric accuracy metrics (OA, F1, IoU, Kappa) are scientifically "
    f"expected for {metrics_expected} of the {total} papers (mapping and "
    f"classification studies). Of these, {metrics_reported} "
    f"({_pct(metrics_reported, metrics_expected)}) actually report them. "
    f"The remaining {metrics_expected - metrics_reported} papers in these "
    f"categories present results visually or descriptively — a gap that "
    f"limits cross-study benchmarking. Review, forecasting, and hydraulic "
    f"papers ({len(non_metric_df)}) are not subject to this expectation.",
    "",
]

# ── 11. Limitations ───────────────────────────────────────────────────────────
lines += [
    "## 11. Limitations",
    "",
    "### 11.1 Chunk-Based Retrieval May Miss Geographic Context",
    "",
    "The RAG pipeline retrieves chunks based on semantic similarity to "
    "predefined queries. Study area descriptions, which often appear in "
    "dedicated sections not always surface-ranked by flood-specific queries, "
    "may be missed, leading to underdetection of geographic coverage.",
    "",
    "### 11.2 Satellite Name Parsing from Free Text",
    "",
    "Satellite names are detected via keyword matching in extracted text. "
    "Abbreviations, acronyms, or non-standard naming (e.g. 'S1', 'GRD IW') "
    "may be missed or incorrectly attributed. Future work should incorporate "
    "entity recognition models trained on remote sensing vocabulary.",
    "",
    "### 11.3 Multi-Method Papers Counted in All Applicable Groups",
    "",
    "A paper applying both U-Net and thresholding is counted in both the "
    "Deep Learning and Thresholding groups. Method group counts therefore "
    "sum to more than the total number of papers and should not be added.",
    "",
    "### 11.4 Metric Extraction for Non-Standard Notation",
    "",
    "Accuracy values reported in tables, supplementary figures, or as "
    "per-class metrics may not be captured by the current regex patterns. "
    "Table-aware and figure-caption extraction would recover additional "
    "performance information.",
    "",
]

# ── 12. Conclusion ────────────────────────────────────────────────────────────
lines += [
    "## 12. Conclusion",
    "",
    f"This automated review of {total} satellite-based flood mapping papers "
    f"reveals a field dominated by SAR sensors (led by Sentinel-1), with "
    f"thresholding and change detection as the operational workhorses alongside "
    f"a rapidly growing deep learning component. Geographic coverage is skewed "
    f"towards Asia and North America, with Eastern Europe — including Ukraine "
    f"and its major river basins — under-represented. Accuracy metrics are "
    f"relevant to {metrics_expected} papers and reported by {metrics_reported} "
    f"of them; their absence in review, forecasting, and hydraulic papers is "
    f"not a deficiency but a feature of those paper types. Near-real-time "
    f"capability is explicitly described in {nrt_n} papers, suggesting "
    f"timeliness deserves greater prominence as a reporting standard. "
    f"Future reviews should expand geographic coverage, integrate table "
    f"extraction, and adopt standardised timeliness metrics alongside "
    f"traditional accuracy indicators.",
    "",
    "---",
    f"*Report auto-generated by the Flood-Paper RAG Pipeline on {TODAY}.*",
]

# ── 5. Save ───────────────────────────────────────────────────────────────────

report_text = "\n".join(lines)
REPORT_PATH.write_text(report_text, encoding="utf-8")
print(f"\nMarkdown report saved → {REPORT_PATH}")

df.to_csv(DATASET_PATH, index=False)
print(f"Final dataset CSV    → {DATASET_PATH}")
print(f"\nReport: {len(report_text):,} chars  |  {report_text.count(chr(10))} lines")
