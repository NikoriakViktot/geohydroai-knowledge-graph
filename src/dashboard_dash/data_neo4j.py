"""
data_neo4j.py  —  Neo4j analytics queries for the dashboard.

All functions return DataFrames and are cached in-process for the session.
"""
from __future__ import annotations

import logging
from functools import lru_cache

import pandas as pd

log = logging.getLogger(__name__)

_driver = None


def _get_driver():
    global _driver
    if _driver is None:
        from neo4j import GraphDatabase
        from src.config import settings
        _driver = GraphDatabase.driver(
            settings.NEO4J_URI,
            auth=(settings.NEO4J_USER, settings.require_env("NEO4J_PASSWORD")),
        )
    return _driver


def _run(cypher: str, **params) -> list[dict]:
    import warnings
    warnings.filterwarnings("ignore", category=UserWarning)
    try:
        with _get_driver().session() as s:
            return [dict(r) for r in s.run(cypher, **params)]
    except Exception as exc:
        log.warning("Neo4j query failed: %s", exc)
        return []


# ─────────────────────────────────────────────────────────────────────────────
# Q1 — Dominant topic by country
# ─────────────────────────────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def get_regional_dominance() -> pd.DataFrame:
    rows = _run("""
        MATCH (p:Paper)-[:FROM_COUNTRY]->(c:Country)
        MATCH (p)-[:HAS_TOPIC]->(t:Topic)
        WITH c.name AS country, t.topic_name AS topic, count(p) AS n
        ORDER BY country, n DESC
        WITH country,
             collect({topic: topic, n: n})[0] AS top,
             sum(n) AS total
        MATCH (pp:Paper)-[:FROM_COUNTRY]->(cc:Country {name: country})
        WITH country, top, count(DISTINCT pp) AS papers
        WHERE papers >= 5
        RETURN country, papers,
               top.topic AS dominant_topic,
               top.n     AS topic_papers
        ORDER BY papers DESC
        LIMIT 30
    """)
    return pd.DataFrame(rows)


# ─────────────────────────────────────────────────────────────────────────────
# Q2 — NSE performance by modeling domain
# ─────────────────────────────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def get_domain_nse() -> pd.DataFrame:
    rows = _run("""
        MATCH (p:Paper)-[:HAS_NUMERIC_FACT]->(nf:NumericFact)
        WHERE nf.canonical_id = 'metric.nse'
          AND nf.value >= -5.0 AND nf.value <= 1.0
        WITH p.paper_id AS pid, avg(nf.value) AS paper_nse
        MATCH (pp:Paper {paper_id: pid})-[:HAS_TOPIC]->(t:Topic)
        WITH pid, paper_nse, collect(t.topic_name) AS topics
        WITH pid, paper_nse,
             CASE
               WHEN any(t IN topics WHERE t CONTAINS 'AI' OR t CONTAINS 'Neural'
                 OR t CONTAINS 'Machine' OR t CONTAINS 'LSTM'
                 OR t CONTAINS 'deep' OR t CONTAINS 'Deep') THEN 'AI / ML Forecasting'
               WHEN any(t IN topics WHERE t CONTAINS 'Hydraulic'
                 OR t CONTAINS 'HEC' OR t CONTAINS 'inundation') THEN 'Hydraulic Modeling'
               WHEN any(t IN topics WHERE t CONTAINS 'SAR' OR t CONTAINS 'Remote Sens'
                 OR t CONTAINS 'Sentinel' OR t CONTAINS 'Landsat'
                 OR t CONTAINS 'Satellite') THEN 'Remote Sensing'
               ELSE 'Hydrological Modeling'
             END AS domain
        WITH domain,
             count(pid) AS n,
             avg(paper_nse) AS avg_nse,
             stdev(paper_nse) AS std_nse,
             sum(CASE WHEN paper_nse >= 0.75 THEN 1 ELSE 0 END) AS excellent
        RETURN domain, n,
               round(avg_nse * 1000) / 1000 AS avg_nse,
               round(std_nse * 1000) / 1000 AS std_nse,
               round(toFloat(excellent) / n * 1000) / 1000 AS pct_excellent
        ORDER BY avg_nse DESC
    """)
    return pd.DataFrame(rows)


# ─────────────────────────────────────────────────────────────────────────────
# Q3 — Institutions by model performance
# ─────────────────────────────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def get_institution_performance() -> pd.DataFrame:
    rows = _run("""
        MATCH (inst:Institution)<-[:AFFILIATED_WITH]-(a:Author)-[:AUTHORED]->(p:Paper)
        MATCH (p)-[:HAS_NUMERIC_FACT]->(nf:NumericFact)
        WHERE nf.canonical_id = 'metric.nse'
          AND nf.value >= 0.0 AND nf.value <= 1.0
        WITH inst.display_name AS institution,
             inst.country_code AS country,
             count(DISTINCT p) AS papers,
             avg(nf.value)     AS avg_nse,
             sum(CASE WHEN nf.value >= 0.75 THEN 1 ELSE 0 END) AS excellent,
             count(nf.value)   AS total_facts
        WHERE papers >= 3
        RETURN institution, country, papers,
               round(avg_nse * 1000) / 1000       AS avg_nse,
               round(toFloat(excellent) / total_facts * 1000) / 1000 AS pct_excellent
        ORDER BY avg_nse DESC
        LIMIT 20
    """)
    return pd.DataFrame(rows)


# ─────────────────────────────────────────────────────────────────────────────
# Q4 — AI forecasting NSE trend 2015–2024
# ─────────────────────────────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def get_ai_nse_trend() -> pd.DataFrame:
    rows = _run("""
        MATCH (p:Paper)-[:HAS_TOPIC]->(t:Topic)
        WHERE t.topic_name CONTAINS 'AI'
           OR t.topic_name CONTAINS 'Neural'
           OR t.topic_name CONTAINS 'Machine Learning'
           OR t.topic_name CONTAINS 'LSTM'
           OR t.topic_name CONTAINS 'deep'
           OR t.topic_name CONTAINS 'Deep'
        MATCH (p)-[:HAS_NUMERIC_FACT]->(nf:NumericFact)
        WHERE nf.canonical_id = 'metric.nse'
          AND nf.value >= -1.0 AND nf.value <= 1.0
        WITH p.year AS year,
             avg(nf.value)  AS avg_nse,
             stdev(nf.value) AS std_nse,
             count(DISTINCT p) AS papers,
             count(nf) AS facts,
             sum(CASE WHEN nf.value >= 0.75 THEN 1 ELSE 0 END) AS excellent
        WHERE year >= 2015 AND year IS NOT NULL
        RETURN year, papers, facts,
               round(avg_nse  * 1000) / 1000 AS avg_nse,
               round(std_nse  * 1000) / 1000 AS std_nse,
               round(toFloat(excellent) / facts * 1000) / 1000 AS pct_excellent
        ORDER BY year
    """)
    return pd.DataFrame(rows)


# ─────────────────────────────────────────────────────────────────────────────
# Q5 — Metric dominance by domain
# ─────────────────────────────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def get_metric_by_domain() -> pd.DataFrame:
    rows = _run("""
        MATCH (p:Paper)-[:HAS_TOPIC]->(t:Topic)
        MATCH (p)-[:HAS_NUMERIC_FACT]->(nf:NumericFact)
        WHERE nf.node_label = 'Metric'
        WITH p.paper_id AS pid,
             CASE
               WHEN t.topic_name CONTAINS 'SAR' OR t.topic_name CONTAINS 'Remote Sens'
                 OR t.topic_name CONTAINS 'Sentinel' OR t.topic_name CONTAINS 'Landsat'
                 OR t.topic_name CONTAINS 'Satellite' THEN 'Remote Sensing'
               WHEN t.topic_name CONTAINS 'Hydraulic' OR t.topic_name CONTAINS 'HEC'
                 OR t.topic_name CONTAINS 'inundation' THEN 'Hydraulic'
               WHEN t.topic_name CONTAINS 'AI' OR t.topic_name CONTAINS 'Neural'
                 OR t.topic_name CONTAINS 'Machine' OR t.topic_name CONTAINS 'LSTM' THEN 'AI / ML'
               WHEN t.topic_name CONTAINS 'Watershed' OR t.topic_name CONTAINS 'Hydrology'
                 OR t.topic_name CONTAINS 'Runoff' THEN 'Hydrological'
               ELSE NULL
             END AS domain,
             nf.canonical_id AS metric
        WHERE domain IS NOT NULL
        WITH domain, metric, count(DISTINCT pid) AS papers
        RETURN domain, metric, papers
        ORDER BY domain, papers DESC
    """)
    return pd.DataFrame(rows)


# ─────────────────────────────────────────────────────────────────────────────
# KPI summary
# ─────────────────────────────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def get_analytics_kpis() -> dict:
    rows = _run("""
        MATCH (nf:NumericFact)
        WHERE nf.canonical_id = 'metric.nse'
          AND nf.value >= -5.0 AND nf.value <= 1.0
        RETURN count(nf) AS total_facts,
               round(avg(nf.value) * 1000) / 1000 AS mean_nse,
               sum(CASE WHEN nf.value >= 0.75 THEN 1 ELSE 0 END) AS excellent,
               sum(CASE WHEN nf.value >= 0.5 AND nf.value < 0.75 THEN 1 ELSE 0 END) AS good
    """)
    r = rows[0] if rows else {}

    top_inst = _run("""
        MATCH (inst:Institution)<-[:AFFILIATED_WITH]-(a:Author)-[:AUTHORED]->(p:Paper)
        MATCH (p)-[:HAS_NUMERIC_FACT]->(nf:NumericFact)
        WHERE nf.canonical_id = 'metric.nse' AND nf.value >= 0.0 AND nf.value <= 1.0
        WITH inst.display_name AS inst, count(DISTINCT p) AS n, avg(nf.value) AS avg_nse
        WHERE n >= 3
        RETURN inst ORDER BY avg_nse DESC LIMIT 1
    """)

    total = r.get("total_facts", 0) or 1
    exc   = r.get("excellent", 0) or 0
    return {
        "total_facts":    r.get("total_facts", 0),
        "mean_nse":       r.get("mean_nse",    0.0),
        "pct_excellent":  round(exc / total * 100, 1),
        "top_institution": top_inst[0]["inst"][:30] if top_inst else "—",
    }
