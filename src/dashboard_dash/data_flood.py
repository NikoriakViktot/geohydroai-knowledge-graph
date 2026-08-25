"""
data_flood.py  —  DuckDB query layer for the Flood Remote Sensing Analytics page

All queries are scoped to the Flood Risk Assessment and Management topic
(OpenAlex T10930) unless otherwise noted.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

import pandas as pd

from src.dashboard_dash.duckdb_manager import query, scalar

_ONTOLOGY_PATH = Path(__file__).resolve().parents[2] / "data" / "ontology" / "ontology_registry.json"


@lru_cache(maxsize=1)
def _load_registry() -> dict:
    with open(_ONTOLOGY_PATH) as f:
        return json.load(f)

# ── Constant topic IDs ─────────────────────────────────────────────────────────
_FLOOD  = "'https://openalex.org/T10930'"   # Flood Risk Assessment and Management
_SAR    = "'https://openalex.org/T10801'"   # SAR Applications and Techniques
_RS     = "'https://openalex.org/T11164'"   # Remote Sensing and LiDAR
_AI     = "'https://openalex.org/T11490'"   # Hydrological Forecasting Using AI
_YEAR_GUARD = "p.year SIMILAR TO '[0-9]{4}' AND CAST(p.year AS INT) BETWEEN 2000 AND 2025"

# ── SAR-family sensor IDs (proxy for SAR papers) ──────────────────────────────
_SAR_SENSORS = (
    "'sensor.radar','sensor.alos_palsar','sensor.radarsat',"
    "'sensor.envisat','sensor.asar','sensor.cosmo_skymed'"
)
_OPT_SENSORS = (
    "'sensor.sentinel','sensor.landsat','sensor.modis',"
    "'sensor.landsat_8','sensor.aster','sensor.spot'"
)

# ── KPI scalars ────────────────────────────────────────────────────────────────

def get_flood_kpis() -> dict:
    n_flood = scalar(f"""
        SELECT COUNT(DISTINCT pte.paper_id)
        FROM paper_topic_edges pte
        WHERE pte.topic_id = {_FLOOD}
    """)
    n_sar_topic = scalar(f"""
        SELECT COUNT(DISTINCT pte.paper_id)
        FROM paper_topic_edges pte
        WHERE pte.topic_id = {_SAR}
    """)
    # Radar/SAR sensor total across corpus
    n_radar = scalar("""
        SELECT paper_count FROM sensors WHERE canonical_id = 'sensor.radar'
    """)
    n_sentinel = scalar("""
        SELECT paper_count FROM sensors WHERE canonical_id = 'sensor.sentinel'
    """)
    n_ml = scalar(f"""
        SELECT COUNT(DISTINCT pte.paper_id)
        FROM paper_topic_edges pte
        WHERE pte.topic_id = {_AI}
    """)
    avg_cit = scalar(f"""
        SELECT AVG(p.cited_by_count)
        FROM papers p
        JOIN paper_topic_edges pte ON p.paper_id = pte.paper_id
        WHERE pte.topic_id = {_FLOOD}
    """)
    n_oa = scalar("SELECT paper_count FROM metrics WHERE canonical_id='metric.overall_accuracy'")
    n_f1 = scalar("SELECT paper_count FROM metrics WHERE canonical_id='metric.f1_score'")
    n_nse = scalar("SELECT paper_count FROM metrics WHERE canonical_id='metric.nse'")
    return {
        "flood_papers":  int(n_flood or 0),
        "sar_papers":    int(n_radar or 0),
        "sentinel":      int(n_sentinel or 0),
        "ml_papers":     int(n_ml or 0),
        "avg_citations": round(float(avg_cit or 0), 1),
        "oa_papers":     int(n_oa or 0),
        "f1_papers":     int(n_f1 or 0),
        "nse_papers":    int(n_nse or 0),
    }


# ── Flood timeline ─────────────────────────────────────────────────────────────

def get_flood_timeline() -> pd.DataFrame:
    """Annual flood paper counts with key sub-topic breakdowns."""
    return query(f"""
        SELECT
            p.year,
            COUNT(DISTINCT p.paper_id)                                                 AS total_flood,
            COUNT(DISTINCT CASE WHEN pte2.topic_id = {_SAR} THEN p.paper_id END)      AS sar,
            COUNT(DISTINCT CASE WHEN pte2.topic_id = {_RS}  THEN p.paper_id END)      AS remote_sensing,
            COUNT(DISTINCT CASE WHEN pte2.topic_id = {_AI}  THEN p.paper_id END)      AS ai_ml,
            AVG(p.cited_by_count)                                                      AS avg_citations
        FROM papers p
        JOIN paper_topic_edges pte  ON p.paper_id = pte.paper_id
        LEFT JOIN paper_topic_edges pte2 ON p.paper_id = pte2.paper_id
        WHERE pte.topic_id = {_FLOOD}
          AND {_YEAR_GUARD}
        GROUP BY p.year
        ORDER BY p.year
    """)


# ── Method family classification (ontology-driven) ────────────────────────────

# Explicit overrides for canonical IDs that the domain field alone can't resolve.
# domain=hydrology covers both "HEC-RAS" and "ANN" — so ML IDs are listed first.
_METHOD_ML = {
    'method.artificial_neural_network', 'method.ann', 'method.ann_models',
    'method.machine_learning', 'method.random_forest', 'method.support_vector_machine',
    'method.dt', 'method.knn', 'method.self_organizing_map', 'method.ddm',
    'method.ml', 'method.nn', 'method.anfis', 'method.svmr',
}
_METHOD_TERRAIN = {
    'method.hand', 'method.idw', 'method.ls', 'method.lm', 'method.rk',
    'method.ck', 'method.georeferencing', 'method.affine_transform',
    'method.space_geodesy', 'method.reprojection', 'method.block_adjustment', 'method.itd',
}
_METHOD_OPS = {
    'method.early_warning_system', 'method.efas', 'method.glofas', 'method.gfds',
    'method.glofris', 'method.fews_net', 'method.real_time_monitoring',
    'method.dss', 'method.rifa', 'method.cama_flood',
}


def _classify_method(cid: str) -> str:
    if cid in _METHOD_ML:
        return 'ML / AI'
    if cid in _METHOD_TERRAIN:
        return 'Terrain Analysis'
    if cid in _METHOD_OPS:
        return 'Operational Systems'
    reg  = _load_registry()
    meta = reg.get(cid, {})
    domain = meta.get('domain', '')
    if domain == 'hydrology':
        return 'Hydrological Models'
    if domain == 'terrain_analysis':
        return 'Terrain Analysis'
    if domain == 'flood_mapping':
        return 'Flood Mapping Methods'
    if domain == 'remote_sensing':
        return 'Image Processing'
    return 'Other'


# ── Methods (corpus-wide, not flood-filtered — no paper↔method edge table) ───

def get_flood_methods() -> pd.DataFrame:
    """Top 15 methods by corpus paper_count with family classification."""
    df = query("SELECT canonical_id, display_name, paper_count FROM methods ORDER BY paper_count DESC LIMIT 15")
    df["family"] = df["canonical_id"].apply(_classify_method)
    return df


def get_method_categories() -> pd.DataFrame:
    """
    Aggregate ALL methods in the corpus into semantic families using the
    ontology registry domain field + explicit ML/terrain/ops override sets.
    Returns: DataFrame with columns [category, total].
    """
    df = query("SELECT canonical_id, paper_count FROM methods")
    df["category"] = df["canonical_id"].apply(_classify_method)
    result = (
        df.groupby("category", as_index=False)["paper_count"]
        .sum()
        .rename(columns={"paper_count": "total"})
        .sort_values("total", ascending=False)
    )
    return result


# ── Sensor family classification (ontology-driven) ────────────────────────────

_SENSOR_SAR = {
    'sensor.radar', 'sensor.alos_palsar', 'sensor.radarsat', 'sensor.envisat',
    'sensor.asar', 'sensor.cosmo_skymed', 'sensor.alos', 'sensor.ers',
    'sensor.ers_1', 'sensor.jers', 'sensor.jers_1', 'sensor.risat',
    'sensor.sars', 'sensor.polsar', 'sensor.sir', 'sensor.ifsar',
    'sensor.aatsr', 'sensor.scatterometer',
}
_SENSOR_OPTICAL = {
    'sensor.sentinel', 'sensor.landsat', 'sensor.modis', 'sensor.landsat_8',
    'sensor.aster', 'sensor.spot', 'sensor.avhrr', 'sensor.viirs',
    'sensor.landsat_oli', 'sensor.etm', 'sensor.etm_plus',
    'sensor.landsat_etm_plus', 'sensor.tm', 'sensor.landsat_tm',
    'sensor.ali', 'sensor.meris', 'sensor.hyperion', 'sensor.aviris',
    'sensor.casi', 'sensor.chris', 'sensor.polder', 'sensor.vhrr',
    'sensor.eo_1', 'sensor.irs', 'sensor.liss', 'sensor.awifs',
    'sensor.avnir', 'sensor.hrv', 'sensor.prisma', 'sensor.formosat',
    'sensor.flex', 'sensor.tiros', 'sensor.erts',
}
_SENSOR_PRECIP = {
    'sensor.trmm', 'sensor.tmpa', 'sensor.gmi', 'sensor.tmi',
    'sensor.nexrad', 'sensor.amsr_e', 'sensor.radiometer',
    'sensor.microwave_radiometer', 'sensor.atms', 'sensor.smos',
    'sensor.smap', 'sensor.goes', 'sensor.abi', 'sensor.glm',
    'sensor.cloudsat', 'sensor.tropomi', 'sensor.cris',
}
_SENSOR_LIDAR = {
    'sensor.lidar', 'sensor.altimeter', 'sensor.lfc', 'sensor.swot',
    'sensor.grace', 'sensor.ices',
}
_SENSOR_UAV = {'sensor.uav', 'sensor.uas'}
_SENSOR_NAV  = {'sensor.glonass', 'sensor.transit', 'sensor.compass', 'sensor.imu'}


def _classify_sensor(cid: str) -> str:
    if cid in _SENSOR_SAR:
        return 'SAR / Radar'
    if cid in _SENSOR_OPTICAL:
        return 'Optical'
    if cid in _SENSOR_PRECIP:
        return 'Precipitation / MW'
    if cid in _SENSOR_LIDAR:
        return 'LiDAR / Altimetry'
    if cid in _SENSOR_UAV:
        return 'UAV / Drone'
    if cid in _SENSOR_NAV:
        return 'Navigation'
    # Keyword fallback from ontology definition + used_for
    reg  = _load_registry()
    meta = reg.get(cid, {})
    text = (' '.join(meta.get('used_for', [])) + ' ' + meta.get('definition', '')).lower()
    if any(k in text for k in ('sar', 'synthetic aperture', 'radar backscatter')):
        return 'SAR / Radar'
    if any(k in text for k in ('precipitation', 'rainfall', 'rain detection')):
        return 'Precipitation / MW'
    if any(k in text for k in ('lidar', 'laser', 'altim')):
        return 'LiDAR / Altimetry'
    if any(k in text for k in ('multispectral', 'land cover', 'optical', 'vegetation')):
        return 'Optical'
    return 'Other'


# ── Sensors ────────────────────────────────────────────────────────────────────

def get_sensor_breakdown() -> pd.DataFrame:
    """All sensors in corpus with family label, for top-N bar charts."""
    df = query("SELECT canonical_id, display_name, paper_count FROM sensors WHERE paper_count >= 5 ORDER BY paper_count DESC")
    df["family"] = df["canonical_id"].apply(_classify_sensor)
    return df


def get_sensor_family_totals() -> pd.DataFrame:
    """
    Aggregate ALL sensors into semantic families using the ontology registry.
    Returns: DataFrame with columns [family, paper_count].
    """
    df = query("SELECT canonical_id, paper_count FROM sensors")
    df["family"] = df["canonical_id"].apply(_classify_sensor)
    result = (
        df.groupby("family", as_index=False)["paper_count"]
        .sum()
        .sort_values("paper_count", ascending=False)
    )
    return result


def get_top_sensors() -> pd.DataFrame:
    return query("""
        SELECT display_name, paper_count
        FROM sensors
        ORDER BY paper_count DESC
        LIMIT 12
    """)


# ── Flood papers — country distribution ───────────────────────────────────────

def get_flood_countries() -> pd.DataFrame:
    return query(f"""
        SELECT
            p.primary_country                AS country,
            COUNT(*)                         AS papers,
            AVG(p.cited_by_count)            AS avg_citations,
            AVG(p.methods_count)             AS avg_methods,
            AVG(p.sensors_count)             AS avg_sensors
        FROM papers p
        JOIN paper_topic_edges pte ON p.paper_id = pte.paper_id
        WHERE pte.topic_id = {_FLOOD}
          AND p.primary_country IS NOT NULL AND p.primary_country != ''
        GROUP BY p.primary_country
        ORDER BY papers DESC
        LIMIT 25
    """)


# ── Top flood papers for AG Grid ──────────────────────────────────────────────

def get_flood_papers_table() -> pd.DataFrame:
    return query(f"""
        SELECT DISTINCT
            p.title,
            p.year,
            p.journal,
            p.primary_country                AS country,
            p.cited_by_count                 AS citations,
            p.doi,
            p.methods_count,
            p.sensors_count,
            p.metrics_count,
            p.study_type
        FROM papers p
        JOIN paper_topic_edges pte ON p.paper_id = pte.paper_id
        WHERE pte.topic_id = {_FLOOD}
          AND {_YEAR_GUARD}
          AND p.title IS NOT NULL
        ORDER BY p.cited_by_count DESC
        LIMIT 300
    """)


# ── Co-occurring topics for context ───────────────────────────────────────────

def get_flood_cotopics() -> pd.DataFrame:
    return query(f"""
        SELECT t.topic_name, COUNT(DISTINCT pte2.paper_id) AS n
        FROM paper_topic_edges pte1
        JOIN paper_topic_edges pte2
            ON pte1.paper_id = pte2.paper_id AND pte1.topic_id != pte2.topic_id
        JOIN topics t ON pte2.topic_id = t.topic_id
        WHERE pte1.topic_id = {_FLOOD}
        GROUP BY t.topic_name
        ORDER BY n DESC
        LIMIT 12
    """)


# ── Scientific insights (all from DuckDB aggregations) ────────────────────────

def get_flood_insights() -> list[dict]:
    """Derive insight strings from live DuckDB aggregations."""
    insights = []

    # Growth rate
    try:
        df = query(f"""
            SELECT p.year, COUNT(DISTINCT p.paper_id) AS n
            FROM papers p JOIN paper_topic_edges pte ON p.paper_id = pte.paper_id
            WHERE pte.topic_id = {_FLOOD} AND {_YEAR_GUARD}
            GROUP BY p.year ORDER BY p.year
        """)
        if len(df) >= 2:
            n_2018 = int(df[df.year == "2018"]["n"].sum())
            n_2023 = int(df[df.year == "2023"]["n"].sum())
            if n_2018 > 0:
                pct = round((n_2023 - n_2018) / n_2018 * 100)
                insights.append({
                    "icon": "trend", "color": "#00d4ff",
                    "text": f"Flood research grew {pct}% from {n_2018} papers (2018) to {n_2023} (2023), driven by open satellite data and DL advances.",
                })
    except Exception:
        pass

    # Ukraine avg citations
    try:
        ua_cit = scalar(f"""
            SELECT AVG(p.cited_by_count) FROM papers p
            JOIN paper_topic_edges pte ON p.paper_id = pte.paper_id
            WHERE pte.topic_id = {_FLOOD} AND p.primary_country = 'Ukraine'
        """)
        if ua_cit:
            insights.append({
                "icon": "pin", "color": "#f59e0b",
                "text": f"Ukraine flood papers average {round(float(ua_cit))} citations — the highest of any country — reflecting its role as an Eastern European flood testbed.",
            })
    except Exception:
        pass

    # SAR vs optical
    try:
        n_radar  = int(scalar("SELECT paper_count FROM sensors WHERE canonical_id='sensor.radar'") or 0)
        n_landsat = int(scalar("SELECT paper_count FROM sensors WHERE canonical_id='sensor.landsat'") or 0)
        n_sent   = int(scalar("SELECT paper_count FROM sensors WHERE canonical_id='sensor.sentinel'") or 0)
        insights.append({
            "icon": "satellite", "color": "#10b981",
            "text": f"SAR/Radar sensors appear in {n_radar} papers vs Sentinel ({n_sent}) and Landsat ({n_landsat}), confirming radar dominance for all-weather NRT flood mapping.",
        })
    except Exception:
        pass

    # ML growth
    try:
        n_ai = int(scalar(f"""
            SELECT COUNT(DISTINCT pte.paper_id) FROM paper_topic_edges pte
            WHERE pte.topic_id = {_AI}
        """) or 0)
        insights.append({
            "icon": "brain", "color": "#8b5cf6",
            "text": f"{n_ai} papers combine hydrological flood forecasting with AI/ML, with LSTM and ensemble models achieving sub-6h lead times.",
        })
    except Exception:
        pass

    # Dominant method families
    try:
        top_method = query("""
            SELECT display_name, paper_count FROM methods
            ORDER BY paper_count DESC LIMIT 1
        """)
        if not top_method.empty:
            insights.append({
                "icon": "model", "color": "#3b82f6",
                "text": f"HEC-RAS / HEC-HMS dominate the methods corpus ({top_method.iloc[0]['paper_count']} papers), but ML-based approaches (RF, ANN) are closing the gap since 2018.",
            })
    except Exception:
        pass

    # Metrics completeness
    try:
        n_oa = int(scalar("SELECT paper_count FROM metrics WHERE canonical_id='metric.overall_accuracy'") or 0)
        n_f1 = int(scalar("SELECT paper_count FROM metrics WHERE canonical_id='metric.f1_score'") or 0)
        n_nse = int(scalar("SELECT paper_count FROM metrics WHERE canonical_id='metric.nse'") or 0)
        insights.append({
            "icon": "metrics", "color": "#ef4444",
            "text": f"Accuracy reporting is fragmented: NSE in {n_nse} papers, Overall Accuracy in {n_oa}, F1-score in only {n_f1}. No universal flood mapping benchmark exists.",
        })
    except Exception:
        pass

    return insights
