"""
research_query_service.py  —  Provenance-aware unified query layer for GeoHydroAI.

Routes an extended filter_state to the right backend:
  DuckDB / Parquet  → corpus stats, metric distribution, author influence, regional patterns
  Neo4j             → domain NSE performance, institution ranking, graph evidence, abstracts
  ChromaDB          → semantic passage retrieval (SPECTER2 768-dim)

Every returned evidence object carries: paper_id, doi, title, year, authors,
source_layer, provenance_path, and (where applicable) confidence score.

Extended filter_state keys:
    # DuckDB-native (inherited from data_loader)
    year_min, year_max, countries, study_types, min_citations, has_doi, has_openalex
    # New — DuckDB-routed
    topics          list[str]   topic_name values
    institutions    list[str]   institution display_name values
    authors         list[str]   author display_name values
    metrics         list[str]   canonical_id values (e.g. "metric.nse")
    metric_min      float       lower bound on NumericFact.value
    metric_max      float       upper bound on NumericFact.value
    # New — Neo4j-routed
    domains         list[str]   "AI / ML Forecasting", "Hydraulic Modeling", etc.
    # ChromaDB-routed
    semantic_query  str         free-text semantic query
"""
from __future__ import annotations

import logging
import os
from functools import lru_cache
from typing import Optional

import numpy as np
import pandas as pd

from src.dashboard_dash.duckdb_manager import query as _dbq, scalar as _dbs
from src.dashboard_dash import data_loader as dl
from src.dashboard_dash import data_neo4j as dn4j

log = logging.getLogger(__name__)

# Session-level evidence pack cache — avoids re-running 10 sub-queries when the
# user clicks "Generate AI Synthesis" right after "Search" with identical inputs.
_pack_cache: dict[str, dict] = {}

# ── Parquet paths ─────────────────────────────────────────────────────────────
_NUMERIC_FACTS = "data/analytics/numeric_facts.parquet"
_PAPER_TOPIC   = "data/analytics/paper_topic_edges.parquet"
_TOPICS        = "data/analytics/topics.parquet"
_PAPERS        = "data/analytics/papers.parquet"
_AUTHORS       = "data/analytics/authors.parquet"
_AUTHOR_UNIV   = "data/parquet/authors.parquet"      # has h_index, cited_by_count
_PAE           = "data/analytics/paper_author_edges.parquet"
_INSTITUTIONS  = "data/analytics/institutions.parquet"


# ─────────────────────────────────────────────────────────────────────────────
# Provenance helpers
# ─────────────────────────────────────────────────────────────────────────────

def _batch_paper_metadata(paper_ids: list[str]) -> dict[str, dict]:
    """
    DuckDB batch lookup: paper_id → {doi, title, year, journal, openalex_id}.
    Returns empty dict for unknown paper_ids.
    """
    if not paper_ids:
        return {}
    try:
        ids_sql = ",".join(f"'{pid}'" for pid in set(paper_ids))
        df = _dbq(f"""
            SELECT paper_id, doi, title, year, journal, openalex_id
            FROM '{_PAPERS}'
            WHERE paper_id IN ({ids_sql})
        """)
        return {
            row["paper_id"]: {
                "doi":         row.get("doi"),
                "title":       row.get("title"),
                "year":        row.get("year"),
                "journal":     row.get("journal"),
                "openalex_id": row.get("openalex_id"),
            }
            for _, row in df.iterrows()
        }
    except Exception as exc:
        log.warning("_batch_paper_metadata failed: %s", exc)
        return {}


def _batch_paper_authors(paper_ids: list[str]) -> dict[str, list[str]]:
    """
    DuckDB batch lookup: paper_id → list of author display_names (max 3 per paper).
    """
    if not paper_ids:
        return {}
    try:
        ids_sql = ",".join(f"'{pid}'" for pid in set(paper_ids))
        df = _dbq(f"""
            WITH ranked AS (
                SELECT pae.paper_id, au.display_name,
                       row_number() OVER (PARTITION BY pae.paper_id ORDER BY pae.author_id) AS rn
                FROM '{_PAE}' pae
                JOIN '{_AUTHORS}' au ON pae.author_id = au.author_id
                WHERE pae.paper_id IN ({ids_sql})
            )
            SELECT paper_id, display_name FROM ranked WHERE rn <= 3
        """)
        result: dict[str, list[str]] = {}
        for _, row in df.iterrows():
            result.setdefault(row["paper_id"], []).append(row["display_name"])
        return result
    except Exception as exc:
        log.warning("_batch_paper_authors failed: %s", exc)
        return {}


def enrich_with_paper_metadata(
    rows: list[dict],
    paper_id_key: str = "paper_id",
    source_layer: str = "parquet",
    provenance_path: list[str] | None = None,
) -> list[dict]:
    """
    Adds doi, title, year, journal, openalex_id, authors to each row.
    Rows that have no matching paper_id get None for each field.
    """
    ids = [r.get(paper_id_key) for r in rows if r.get(paper_id_key)]
    meta    = _batch_paper_metadata(ids)
    authors = _batch_paper_authors(ids)
    prov    = provenance_path or ["papers.parquet", "paper_author_edges.parquet"]

    enriched = []
    for row in rows:
        pid  = row.get(paper_id_key)
        m    = meta.get(pid, {})
        enriched.append({
            **row,
            "doi":            m.get("doi"),
            "title":          m.get("title") or row.get("title"),
            "year":           m.get("year")  or row.get("year"),
            "journal":        m.get("journal"),
            "openalex_id":    m.get("openalex_id"),
            "authors":        authors.get(pid, []),
            "source_layer":   row.get("source_layer") or source_layer,
            "provenance_path": row.get("provenance_path") or prov,
        })
    return enriched


def _batch_paper_abstracts(paper_ids: list[str]) -> dict[str, str]:
    """Neo4j batch fetch: paper_id → abstract text (max 1200 chars)."""
    if not paper_ids:
        return {}
    try:
        rows = dn4j._run(
            "MATCH (p:Paper) WHERE p.paper_id IN $ids "
            "RETURN p.paper_id AS paper_id, p.abstract AS abstract",
            ids=list(set(paper_ids)),
        )
        return {
            r["paper_id"]: (r.get("abstract") or "")[:1200]
            for r in rows
            if r.get("paper_id")
        }
    except Exception as exc:
        log.warning("_batch_paper_abstracts failed: %s", exc)
        return {}


def _select_abstract_papers(pack: dict, max_n: int = 12) -> list[dict]:
    """
    Hybrid abstract selection:
    - 4 top semantic hits (by score)
    - 4 top quantitative evidence papers (by confidence)
    - 4 top scientometric papers (by cited_by_count)
    Deduplicates by paper_id.  Max 12 total.  Abstracts from Neo4j.
    """
    candidates: list[dict] = []

    for hit in (pack.get("semantic_evidence") or [])[:4]:
        pid = hit.get("paper_id")
        if pid:
            candidates.append({"paper_id": pid, "source_category": "semantic"})

    nf = pack.get("numeric_fact_evidence") or []
    sorted_nf = sorted(nf, key=lambda x: float(x.get("confidence") or 0), reverse=True)
    for fact in sorted_nf[:4]:
        pid = fact.get("paper_id")
        if pid:
            candidates.append({"paper_id": pid, "source_category": "quantitative"})

    sci = pack.get("scientometric_evidence") or []
    sorted_sci = sorted(sci, key=lambda x: int(x.get("cited_by_count") or 0), reverse=True)
    for rec in sorted_sci[:4]:
        pid = rec.get("paper_id")
        if pid:
            candidates.append({"paper_id": pid, "source_category": "scientometric"})

    # Deduplicate preserving order
    seen: set[str] = set()
    deduped: list[dict] = []
    for c in candidates:
        pid = c["paper_id"]
        if pid not in seen:
            seen.add(pid)
            deduped.append(c)
        if len(deduped) >= max_n:
            break

    if not deduped:
        return []

    paper_ids = [c["paper_id"] for c in deduped]
    meta      = _batch_paper_metadata(paper_ids)
    abstracts = _batch_paper_abstracts(paper_ids)

    result = []
    for c in deduped:
        pid = c["paper_id"]
        m   = meta.get(pid, {})
        ab  = abstracts.get(pid, "")
        if not ab:
            continue  # skip if no abstract available
        result.append({
            "paper_id":       pid,
            "doi":            m.get("doi"),
            "title":          m.get("title"),
            "year":           m.get("year"),
            "abstract":       ab,
            "source_category": c["source_category"],
        })
    return result


def _build_limitations(fs: dict) -> list[str]:
    lims = [
        "USES_METHOD edges not populated in Neo4j — domain classification uses topic keywords as proxy.",
        "Abstract text fetched from Neo4j; not all papers have abstract stored.",
        "ChromaDB paper_id filter capped at 500 IDs — very broad filters may miss chunks.",
        "pct_excellent threshold = 0.75 (calibrated for NSE/KGE); may be inappropriate for RMSE/MAPE.",
    ]
    if not fs.get("semantic_query"):
        lims.append("No semantic query provided — semantic evidence and abstract context absent.")
    if fs.get("metric_min") is not None or fs.get("metric_max") is not None:
        lims.append("Metric value range filter applies to Parquet only, not Neo4j graph evidence.")
    return lims


# ─────────────────────────────────────────────────────────────────────────────
# Filter helpers
# ─────────────────────────────────────────────────────────────────────────────

def _paper_where_ext(fs: dict, alias: str = "p") -> str:
    """
    Extended WHERE clause for the papers parquet.
    Superset of data_loader._paper_where(); adds topic/institution/author filters.
    `alias` is the table alias used in the query (default 'p').
    """
    a = f"{alias}." if alias else ""
    clauses: list[str] = [
        f"{a}year SIMILAR TO '[0-9]{{4}}'",
        f"CAST({a}year AS INT) BETWEEN 1990 AND 2025",
    ]
    if fs.get("year_min"):
        clauses.append(f"CAST({a}year AS INT) >= {int(fs['year_min'])}")
    if fs.get("year_max"):
        clauses.append(f"CAST({a}year AS INT) <= {int(fs['year_max'])}")
    if fs.get("countries"):
        vals = ",".join(f"'{c}'" for c in fs["countries"])
        clauses.append(f"{a}primary_country IN ({vals})")
    if fs.get("study_types"):
        vals = ",".join(f"'{s}'" for s in fs["study_types"])
        clauses.append(f"{a}study_type IN ({vals})")
    if fs.get("min_citations") is not None and int(fs.get("min_citations", 0)) > 0:
        clauses.append(f"{a}cited_by_count >= {int(fs['min_citations'])}")
    if fs.get("has_doi") is True:
        clauses.append(f"{a}has_doi = true")
    if fs.get("has_openalex") is True:
        clauses.append(f"{a}has_openalex = true")
    return " AND ".join(clauses) if clauses else "1=1"


def _get_paper_ids(fs: dict, limit: int = 2000) -> list[str]:
    """Return paper_id list matching filter — used to narrow ChromaDB queries."""
    where = _paper_where_ext(fs, alias="")
    rows = _dbq(f"""
        SELECT paper_id FROM '{_PAPERS}'
        WHERE {where}
        LIMIT {limit}
    """)
    return rows["paper_id"].tolist() if not rows.empty else []


# ─────────────────────────────────────────────────────────────────────────────
# 1. get_filtered_corpus
# ─────────────────────────────────────────────────────────────────────────────

def get_filtered_corpus(fs: dict) -> dict:
    """DuckDB. Corpus statistics for current filter state."""
    try:
        kpis        = dl.get_kpi_counts(fs)
        top_countries = dl.get_top_countries(fs, n=10)
        year_trend  = dl.get_papers_by_year(fs)

        # Top topics within filter
        where = _paper_where_ext(fs, alias="p")
        topic_rows = _dbq(f"""
            SELECT t.topic_name, count(DISTINCT pte.paper_id) AS papers
            FROM '{_PAPER_TOPIC}' pte
            JOIN '{_TOPICS}' t USING (topic_id)
            JOIN '{_PAPERS}' p ON pte.paper_id = p.paper_id
            WHERE {where}
            GROUP BY t.topic_name
            ORDER BY papers DESC
            LIMIT 10
        """)

        return {
            **kpis,
            "top_countries": top_countries,
            "year_trend":    year_trend,
            "top_topics":    topic_rows,
        }
    except Exception as exc:
        log.warning("get_filtered_corpus failed: %s", exc)
        return {}


# ─────────────────────────────────────────────────────────────────────────────
# 2. get_metric_distribution
# ─────────────────────────────────────────────────────────────────────────────

def get_metric_distribution(fs: dict) -> pd.DataFrame:
    """
    DuckDB. NumericFact metric statistics filtered by paper metadata + metric list
    + optional value range.
    """
    try:
        where_p = _paper_where_ext(fs, alias="p")

        metric_filter = ""
        if fs.get("metrics"):
            vals = ",".join(f"'{m}'" for m in fs["metrics"])
            metric_filter = f"AND nf.canonical_id IN ({vals})"

        val_filter = ""
        if fs.get("metric_min") is not None:
            val_filter += f" AND nf.value >= {float(fs['metric_min'])}"
        if fs.get("metric_max") is not None:
            val_filter += f" AND nf.value <= {float(fs['metric_max'])}"

        return _dbq(f"""
            SELECT
                nf.canonical_id                                     AS metric,
                count(*)                                            AS n,
                round(avg(nf.value), 3)                             AS mean,
                round(stddev(nf.value), 3)                          AS std,
                round(min(nf.value), 3)                             AS min_v,
                round(max(nf.value), 3)                             AS max_v,
                round(
                    sum(CASE WHEN nf.value >= 0.75 THEN 1.0 ELSE 0.0 END)
                    / count(*), 3
                )                                                   AS pct_excellent
            FROM '{_NUMERIC_FACTS}' nf
            JOIN '{_PAPERS}' p ON nf.paper_id = p.paper_id
            WHERE {where_p}
              AND nf.node_label = 'Metric'
              AND nf.value IS NOT NULL
              {metric_filter}
              {val_filter}
            GROUP BY nf.canonical_id
            HAVING count(*) >= 3
            ORDER BY n DESC
        """)
    except Exception as exc:
        log.warning("get_metric_distribution failed: %s", exc)
        return pd.DataFrame()


# ─────────────────────────────────────────────────────────────────────────────
# 3. get_domain_performance
# ─────────────────────────────────────────────────────────────────────────────

def get_domain_performance(fs: dict) -> pd.DataFrame:
    """Neo4j. Domain NSE performance with optional year/country/domain filters."""
    try:
        year_min = int(fs.get("year_min") or 1990)
        year_max = int(fs.get("year_max") or 2025)
        countries = fs.get("countries") or []
        domains   = fs.get("domains") or []

        country_clause = ""
        if countries:
            c_list = ", ".join(f'"{c}"' for c in countries)
            country_clause = f"AND p.primary_country IN [{c_list}]"

        rows = dn4j._run(f"""
            MATCH (p:Paper)-[:HAS_NUMERIC_FACT]->(nf:NumericFact)
            WHERE nf.canonical_id = 'metric.nse'
              AND nf.value >= -5.0 AND nf.value <= 1.0
              AND p.year >= {year_min} AND p.year <= {year_max}
              {country_clause}
            WITH p.paper_id AS pid, avg(nf.value) AS paper_nse
            MATCH (pp:Paper {{paper_id: pid}})-[:HAS_TOPIC]->(t:Topic)
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
            {'WHERE domain IN [' + ', '.join(f'"{d}"' for d in domains) + ']' if domains else ''}
            RETURN domain, n,
                   round(avg_nse * 1000) / 1000 AS avg_nse,
                   round(std_nse * 1000) / 1000 AS std_nse,
                   round(toFloat(excellent) / n * 1000) / 1000 AS pct_excellent
            ORDER BY avg_nse DESC
        """)
        return pd.DataFrame(rows)
    except Exception as exc:
        log.warning("get_domain_performance failed: %s", exc)
        return pd.DataFrame()


# ─────────────────────────────────────────────────────────────────────────────
# 4. get_method_by_region
# ─────────────────────────────────────────────────────────────────────────────

def get_method_by_region(fs: dict) -> pd.DataFrame:
    """
    DuckDB. Dominant topic (method proxy) per country within filter.
    NOTE: USES_METHOD edges are not populated in Neo4j — topic names are used as proxy.
    """
    try:
        where = _paper_where_ext(fs, alias="p")
        return _dbq(f"""
            WITH ranked AS (
                SELECT
                    p.primary_country                   AS country,
                    t.topic_name,
                    count(DISTINCT p.paper_id)          AS n,
                    row_number() OVER (
                        PARTITION BY p.primary_country
                        ORDER BY count(DISTINCT p.paper_id) DESC
                    ) AS rn
                FROM '{_PAPERS}' p
                JOIN '{_PAPER_TOPIC}' pte ON p.paper_id = pte.paper_id
                JOIN '{_TOPICS}' t USING (topic_id)
                WHERE {where}
                  AND p.primary_country IS NOT NULL
                GROUP BY p.primary_country, t.topic_name
            ),
            totals AS (
                SELECT primary_country AS country, count(DISTINCT paper_id) AS papers
                FROM '{_PAPERS}' p
                WHERE {where} AND primary_country IS NOT NULL
                GROUP BY primary_country
            )
            SELECT r.country, r.topic_name AS dominant_topic, r.n AS topic_papers,
                   tot.papers
            FROM ranked r
            JOIN totals tot ON r.country = tot.country
            WHERE r.rn = 1 AND tot.papers >= 5
            ORDER BY tot.papers DESC
            LIMIT 30
        """)
    except Exception as exc:
        log.warning("get_method_by_region failed: %s", exc)
        return pd.DataFrame()


# ─────────────────────────────────────────────────────────────────────────────
# 5. get_institution_performance
# ─────────────────────────────────────────────────────────────────────────────

def get_institution_performance(fs: dict) -> pd.DataFrame:
    """Neo4j. Top institutions by avg NSE, filtered by year range."""
    try:
        year_min = int(fs.get("year_min") or 1990)
        year_max = int(fs.get("year_max") or 2025)
        institutions = fs.get("institutions") or []

        inst_clause = ""
        if institutions:
            i_list = ", ".join(f'"{i}"' for i in institutions)
            inst_clause = f"WHERE inst.display_name IN [{i_list}]"

        rows = dn4j._run(f"""
            MATCH (inst:Institution)<-[:AFFILIATED_WITH]-(a:Author)-[:AUTHORED]->(p:Paper)
            MATCH (p)-[:HAS_NUMERIC_FACT]->(nf:NumericFact)
            WHERE nf.canonical_id = 'metric.nse'
              AND nf.value >= 0.0 AND nf.value <= 1.0
              AND p.year >= {year_min} AND p.year <= {year_max}
            WITH inst, count(DISTINCT p) AS papers,
                 avg(nf.value) AS avg_nse,
                 sum(CASE WHEN nf.value >= 0.75 THEN 1 ELSE 0 END) AS excellent,
                 count(nf.value) AS total_facts
            WHERE papers >= 2
            {inst_clause}
            RETURN inst.display_name AS institution,
                   inst.country_code AS country,
                   papers,
                   round(avg_nse * 1000) / 1000 AS avg_nse,
                   round(toFloat(excellent) / total_facts * 1000) / 1000 AS pct_excellent
            ORDER BY avg_nse DESC
            LIMIT 20
        """)
        return pd.DataFrame(rows)
    except Exception as exc:
        log.warning("get_institution_performance failed: %s", exc)
        return pd.DataFrame()


# ─────────────────────────────────────────────────────────────────────────────
# 6. get_author_influence
# ─────────────────────────────────────────────────────────────────────────────

def get_author_influence(fs: dict) -> pd.DataFrame:
    """
    DuckDB. Author influence metrics joined with OpenAlex scientometrics.
    Filtered by year, country, min_citations, and optional author name list.
    """
    try:
        where_p = _paper_where_ext(fs, alias="p")

        author_filter = ""
        if fs.get("authors"):
            vals = ",".join(f"'{a}'" for a in fs["authors"])
            author_filter = f"AND au.display_name IN ({vals})"

        return _dbq(f"""
            SELECT
                au.display_name,
                count(DISTINCT pae.paper_id)    AS paper_count,
                univ.cited_by_count,
                univ.h_index,
                univ.works_count,
                au.orcid
            FROM '{_PAE}' pae
            JOIN '{_AUTHORS}' au ON pae.author_id = au.author_id
            LEFT JOIN '{_AUTHOR_UNIV}' univ ON pae.author_id = univ.author_id
            JOIN '{_PAPERS}' p ON pae.paper_id = p.paper_id
            WHERE {where_p}
              {author_filter}
            GROUP BY au.display_name, univ.cited_by_count, univ.h_index,
                     univ.works_count, au.orcid
            HAVING count(DISTINCT pae.paper_id) >= 1
            ORDER BY univ.cited_by_count DESC NULLS LAST, paper_count DESC
            LIMIT 20
        """)
    except Exception as exc:
        log.warning("get_author_influence failed: %s", exc)
        return pd.DataFrame()


# ─────────────────────────────────────────────────────────────────────────────
# 7. get_graph_evidence
# ─────────────────────────────────────────────────────────────────────────────

def get_graph_evidence(fs: dict) -> list[dict]:
    """Neo4j. Structured graph facts relevant to current filters."""
    year_min = int(fs.get("year_min") or 1990)
    year_max = int(fs.get("year_max") or 2025)
    evidence: list[dict] = []

    try:
        # Top papers by NSE within filter
        rows = dn4j._run(f"""
            MATCH (p:Paper)-[:HAS_NUMERIC_FACT]->(nf:NumericFact)
            WHERE nf.canonical_id = 'metric.nse'
              AND nf.value >= 0.0 AND nf.value <= 1.0
              AND p.year >= {year_min} AND p.year <= {year_max}
            WITH p, avg(nf.value) AS avg_nse, count(nf) AS n_facts
            WHERE n_facts >= 2
            RETURN p.paper_id AS paper_id,
                   p.title    AS title,
                   p.year     AS year,
                   round(avg_nse * 1000) / 1000 AS avg_nse,
                   n_facts
            ORDER BY avg_nse DESC LIMIT 10
        """)
        nse_paper_ids = [r.get("paper_id") for r in rows if r.get("paper_id")]
        nse_meta = _batch_paper_metadata(nse_paper_ids)
        for r in rows:
            pid  = r.get("paper_id")
            m    = nse_meta.get(pid, {})
            evidence.append({
                "type":           "top_nse_paper",
                "paper_id":       pid,
                "title":          (r.get("title") or m.get("title") or "")[:80],
                "doi":            m.get("doi"),
                "year":           r.get("year"),
                "avg_nse":        r.get("avg_nse"),
                "n_facts":        r.get("n_facts"),
                "source_layer":   "neo4j",
                "provenance_path": ["Neo4j:Paper-[:HAS_NUMERIC_FACT]->NumericFact", "papers.parquet"],
            })
    except Exception as exc:
        log.warning("get_graph_evidence (NSE papers) failed: %s", exc)

    try:
        # Metric distribution within year filter
        rows = dn4j._run(f"""
            MATCH (p:Paper)-[:HAS_NUMERIC_FACT]->(nf:NumericFact)
            WHERE nf.node_label = 'Metric'
              AND nf.value IS NOT NULL
              AND p.year >= {year_min} AND p.year <= {year_max}
            RETURN nf.canonical_id AS metric,
                   count(nf) AS n,
                   round(avg(nf.value) * 100) / 100 AS mean
            ORDER BY n DESC LIMIT 8
        """)
        for r in rows:
            evidence.append({
                "type":           "metric_count",
                "metric":         r.get("metric"),
                "n":              r.get("n"),
                "mean":           r.get("mean"),
                "source_layer":   "neo4j",
                "provenance_path": ["Neo4j:Paper-[:HAS_NUMERIC_FACT]->NumericFact-[:MEASURES]->Metric"],
            })
    except Exception as exc:
        log.warning("get_graph_evidence (metric counts) failed: %s", exc)

    return evidence


# ─────────────────────────────────────────────────────────────────────────────
# 8. get_semantic_evidence
# ─────────────────────────────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def _get_specter2():
    """Lazy-load SPECTER2 model. Cached for the process lifetime."""
    from sentence_transformers import SentenceTransformer
    model_name = os.getenv("EMBEDDING_MODEL", "allenai/specter2_base")
    log.info("Loading SPECTER2 encoder: %s", model_name)
    return SentenceTransformer(model_name)


def get_semantic_evidence(
    query: str,
    fs: dict,
    top_k: int = 10,
) -> list[dict]:
    """
    ChromaDB + SPECTER2. Retrieve top passages semantically similar to query,
    narrowed to papers matching the current filter state.
    """
    if not query or not query.strip():
        return []

    try:
        from src.vectorstore.chroma_store import VectorStore
        from src.config import COLLECTION_NAME

        model  = _get_specter2()
        emb    = model.encode([query], show_progress_bar=False, convert_to_numpy=True)
        vector = np.array(emb[0], dtype=np.float32)

        vs = VectorStore(collection_name=COLLECTION_NAME)

        # Narrow to filtered paper IDs (max 500 to stay within ChromaDB $in limit)
        paper_ids = _get_paper_ids(fs, limit=500)
        where_clause = {"paper_id": {"$in": paper_ids}} if paper_ids else None

        hits = vs.query(vector, top_k=top_k, where=where_clause)

        raw = []
        for h in hits:
            raw.append({
                "paper_id":      h.get("paper_id", ""),
                "chunk_text":    h.get("text", "")[:500],
                "section_title": h.get("section_title", ""),
                "chunk_type":    h.get("chunk_type", ""),
                "page":          h.get("page", 0),
                "distance":      round(float(h.get("distance", 1.0)), 4),
                "score":         round(1.0 - float(h.get("distance", 1.0)), 4),
                "source_layer":  "chromadb",
                "provenance_path": [f"ChromaDB:{COLLECTION_NAME}", "papers.parquet"],
            })

        return enrich_with_paper_metadata(
            raw,
            paper_id_key="paper_id",
            source_layer="chromadb",
            provenance_path=[f"ChromaDB:{COLLECTION_NAME}", "papers.parquet"],
        )

    except Exception as exc:
        log.warning("get_semantic_evidence failed: %s", exc)
        return []


# ─────────────────────────────────────────────────────────────────────────────
# 9. get_numeric_fact_evidence
# ─────────────────────────────────────────────────────────────────────────────

def get_numeric_fact_evidence(fs: dict) -> list[dict]:
    """
    DuckDB. Individual NumericFact rows with full provenance: doi, title, value,
    confidence, extraction source, page. Not aggregated — one row per measurement.
    """
    try:
        where_p = _paper_where_ext(fs, alias="p")

        metric_filter = ""
        if fs.get("metrics"):
            vals = ",".join(f"'{m}'" for m in fs["metrics"])
            metric_filter = f"AND nf.canonical_id IN ({vals})"

        val_filter = ""
        if fs.get("metric_min") is not None:
            val_filter += f" AND nf.value >= {float(fs['metric_min'])}"
        if fs.get("metric_max") is not None:
            val_filter += f" AND nf.value <= {float(fs['metric_max'])}"

        df = _dbq(f"""
            SELECT
                nf.paper_id,
                nf.canonical_id                     AS metric,
                nf.value,
                nf.unit,
                nf.confidence,
                nf.source                           AS extraction_source,
                nf.page,
                nf.period_type,
                p.doi,
                p.title,
                p.year,
                p.journal
            FROM '{_NUMERIC_FACTS}' nf
            JOIN '{_PAPERS}' p ON nf.paper_id = p.paper_id
            WHERE {where_p}
              AND nf.node_label = 'Metric'
              AND nf.value IS NOT NULL
              {metric_filter}
              {val_filter}
            ORDER BY nf.confidence DESC NULLS LAST, nf.value DESC
            LIMIT 50
        """)

        if df.empty:
            return []

        result = []
        for _, row in df.iterrows():
            result.append({
                "paper_id":          row.get("paper_id"),
                "metric":            row.get("metric"),
                "value":             row.get("value"),
                "unit":              row.get("unit"),
                "confidence":        row.get("confidence"),
                "extraction_source": row.get("extraction_source"),
                "page":              row.get("page"),
                "period_type":       row.get("period_type"),
                "doi":               row.get("doi"),
                "title":             row.get("title"),
                "year":              row.get("year"),
                "journal":           row.get("journal"),
                "source_layer":      "numeric_fact",
                "provenance_path":   ["numeric_facts.parquet", "papers.parquet"],
            })
        return result
    except Exception as exc:
        log.warning("get_numeric_fact_evidence failed: %s", exc)
        return []


# ─────────────────────────────────────────────────────────────────────────────
# 10. get_paper_detail
# ─────────────────────────────────────────────────────────────────────────────

def get_paper_detail(paper_id: str) -> dict:
    """
    Full detail for paper detail panel:
    metadata (DuckDB) + authors (DuckDB) + abstract (Neo4j)
    + numeric facts (DuckDB) + semantic passages (ChromaDB).
    """
    detail: dict = {"paper_id": paper_id}

    # ── Paper metadata ────────────────────────────────────────────────────────
    try:
        df = _dbq(f"""
            SELECT doi, title, year, journal, openalex_id, cited_by_count,
                   abstract_length, has_abstract
            FROM '{_PAPERS}'
            WHERE paper_id = '{paper_id}'
            LIMIT 1
        """)
        if not df.empty:
            detail.update(df.iloc[0].to_dict())
    except Exception as exc:
        log.warning("get_paper_detail metadata failed: %s", exc)

    # ── Authors ───────────────────────────────────────────────────────────────
    detail["authors"] = _batch_paper_authors([paper_id]).get(paper_id, [])

    # ── Abstract from Neo4j ───────────────────────────────────────────────────
    detail["abstract"] = _batch_paper_abstracts([paper_id]).get(paper_id, "")

    # ── Numeric facts ─────────────────────────────────────────────────────────
    try:
        nf_df = _dbq(f"""
            SELECT canonical_id AS metric, value, unit, confidence,
                   source AS extraction_source, page, period_type
            FROM '{_NUMERIC_FACTS}'
            WHERE paper_id = '{paper_id}'
              AND node_label = 'Metric'
              AND value IS NOT NULL
            ORDER BY confidence DESC NULLS LAST
            LIMIT 20
        """)
        detail["metrics"] = nf_df.to_dict("records") if not nf_df.empty else []
    except Exception as exc:
        log.warning("get_paper_detail metrics failed: %s", exc)
        detail["metrics"] = []

    # ── Semantic passages ─────────────────────────────────────────────────────
    try:
        from src.vectorstore.chroma_store import VectorStore
        from src.config import COLLECTION_NAME
        vs   = VectorStore(collection_name=COLLECTION_NAME)
        # Retrieve top 5 chunks for this specific paper (no query vector — use dummy)
        # Use metadata-only filter by fetching the collection directly
        col   = vs._collection  # type: ignore[attr-defined]
        res   = col.get(
            where={"paper_id": paper_id},
            include=["documents", "metadatas"],
            limit=5,
        )
        chunks = []
        for doc, meta in zip(res.get("documents", []) or [], res.get("metadatas", []) or []):
            chunks.append({
                "text":          (doc or "")[:400],
                "section_title": (meta or {}).get("section_title", ""),
                "chunk_type":    (meta or {}).get("chunk_type", ""),
                "page":          (meta or {}).get("page", 0),
            })
        detail["semantic_chunks"] = chunks
    except Exception as exc:
        log.warning("get_paper_detail semantic chunks failed: %s", exc)
        detail["semantic_chunks"] = []

    return detail


# ─────────────────────────────────────────────────────────────────────────────
# 11. build_evidence_pack
# ─────────────────────────────────────────────────────────────────────────────

def build_evidence_pack(query: str, fs: dict) -> dict:
    """
    Assemble a provenance-aware evidence pack from all backends.
    Every evidence item carries: paper_id, doi, title, source_layer, provenance_path.
    Used by ai_gateway.build_research_prompt() before calling Gemini.

    Results are cached in _pack_cache for the lifetime of the server process so
    that clicking "Generate AI Synthesis" immediately after "Search" with the same
    query/filters reuses the already-fetched data instead of re-running all backends.
    """
    import hashlib, json as _json

    fs = fs or {}
    cache_key = hashlib.md5(
        ((query or "") + _json.dumps(fs, sort_keys=True, default=str)).encode()
    ).hexdigest()
    if cache_key in _pack_cache:
        log.debug("build_evidence_pack cache hit  key=%s", cache_key)
        return _pack_cache[cache_key]

    log.info("Building evidence pack  query=%r  filters=%s", query[:60] if query else "", fs)

    sem        = get_semantic_evidence(query, fs) if query else []
    nf         = get_numeric_fact_evidence(fs)
    auth_df    = get_author_influence(fs)
    auth_recs  = auth_df.to_dict("records") if not auth_df.empty else []
    corpus     = get_filtered_corpus(fs)
    graph_ev   = get_graph_evidence(fs)

    pack = {
        "query":   query,
        "filters": fs,
        # ── Named evidence sections (provenance-aware) ────────────────────────
        "corpus_summary":         corpus,
        "numeric_fact_evidence":  nf,
        "graph_evidence":         graph_ev,
        "semantic_evidence":      sem,
        "scientometric_evidence": auth_recs,
        "limitations":            _build_limitations(fs),
        # ── Backward-compat keys (dashboard charts still use these) ───────────
        "corpus":       corpus,
        "metrics":      get_metric_distribution(fs),
        "domains":      get_domain_performance(fs),
        "regions":      get_method_by_region(fs),
        "institutions": get_institution_performance(fs),
        "authors":      auth_df,
        "graph":        graph_ev,
        "semantic":     sem,
    }

    # Abstract context — hybrid selection after pack is assembled
    pack["abstract_context"] = _select_abstract_papers(pack, max_n=12)

    _pack_cache[cache_key] = pack
    return pack
