"""
data_loader.py  —  Parameterised DuckDB queries for all dashboard pages

All functions accept a filter_state dict (from dcc.Store) and return DataFrames.
Heavy results are cached via Flask-Caching.

Filter state shape:
    {
        "year_min":       int,
        "year_max":       int,
        "countries":      list[str],
        "topics":         list[str],
        "study_types":    list[str],
        "min_citations":  int,
        "has_doi":        bool | None,
        "has_openalex":   bool | None,
    }
"""
from __future__ import annotations

import hashlib
import json
from typing import Optional

import pandas as pd

from src.dashboard_dash.duckdb_manager import query, scalar

# ── Filter SQL builder ─────────────────────────────────────────────────────────

def _paper_where(fs: dict) -> str:
    """Build WHERE clause fragment for the papers view from a filter state."""
    clauses: list[str] = [
        "year SIMILAR TO '[0-9]{4}'",
        "CAST(year AS INT) BETWEEN 1990 AND 2025",
    ]
    if fs.get("year_min"):
        clauses.append(f"CAST(year AS INT) >= {int(fs['year_min'])}")
    if fs.get("year_max"):
        clauses.append(f"CAST(year AS INT) <= {int(fs['year_max'])}")
    if fs.get("countries"):
        vals = ",".join(f"'{c}'" for c in fs["countries"])
        clauses.append(f"primary_country IN ({vals})")
    if fs.get("study_types"):
        vals = ",".join(f"'{s}'" for s in fs["study_types"])
        clauses.append(f"study_type IN ({vals})")
    if fs.get("min_citations") is not None and int(fs["min_citations"]) > 0:
        clauses.append(f"cited_by_count >= {int(fs['min_citations'])}")
    if fs.get("has_doi") is True:
        clauses.append("has_doi = true")
    if fs.get("has_openalex") is True:
        clauses.append("has_openalex = true")
    return " AND ".join(clauses) if clauses else "1=1"


def _cache_key(name: str, fs: dict) -> str:
    payload = json.dumps({name: fs}, sort_keys=True)
    return hashlib.md5(payload.encode()).hexdigest()


# ── Overview ───────────────────────────────────────────────────────────────────

def get_kpi_counts(fs: dict) -> dict:
    where = _paper_where(fs)
    papers  = scalar(f"SELECT count(*) FROM papers WHERE {where}")
    authors = scalar("SELECT count(*) FROM authors")
    refs    = scalar("SELECT count(*) FROM paper_refs")
    methods = scalar("SELECT count(*) FROM methods")
    sensors = scalar("SELECT count(*) FROM sensors")
    topics  = scalar("SELECT count(*) FROM topics")
    doi_pct = scalar(f"SELECT round(100.0*count(*) FILTER(WHERE has_doi)/count(*),1) FROM papers WHERE {where}")
    oa_pct  = scalar(f"SELECT round(100.0*count(*) FILTER(WHERE has_openalex)/count(*),1) FROM papers WHERE {where}")
    return dict(
        papers=int(papers or 0),
        authors=int(authors or 0),
        references=int(refs or 0),
        methods=int(methods or 0),
        sensors=int(sensors or 0),
        topics=int(topics or 0),
        doi_pct=float(doi_pct or 0),
        oa_pct=float(oa_pct or 0),
    )


def get_papers_by_year(fs: dict) -> pd.DataFrame:
    where = _paper_where(fs)
    return query(
        f"SELECT CAST(year AS INT) AS year, count(*) AS papers "
        f"FROM papers WHERE {where} GROUP BY year ORDER BY year"
    )


def get_top_countries(fs: dict, n: int = 20) -> pd.DataFrame:
    where = _paper_where(fs)
    return query(
        f"SELECT primary_country AS country, count(*) AS papers "
        f"FROM papers WHERE {where} AND primary_country IS NOT NULL "
        f"GROUP BY primary_country ORDER BY papers DESC LIMIT {n}"
    )


def get_top_journals(fs: dict, n: int = 20) -> pd.DataFrame:
    where = _paper_where(fs)
    return query(
        f"SELECT journal, count(*) AS papers "
        f"FROM papers WHERE {where} AND journal IS NOT NULL "
        f"GROUP BY journal ORDER BY papers DESC LIMIT {n}"
    )


def get_study_type_dist(fs: dict) -> pd.DataFrame:
    where = _paper_where(fs)
    return query(
        f"SELECT study_type, count(*) AS papers "
        f"FROM papers WHERE {where} AND study_type IS NOT NULL "
        f"GROUP BY study_type ORDER BY papers DESC"
    )


def get_top_institutions(fs: dict, n: int = 20) -> pd.DataFrame:
    return query(
        f"SELECT display_name, country_code, paper_count "
        f"FROM institutions ORDER BY paper_count DESC LIMIT {n}"
    )


# ── Scientometrics ─────────────────────────────────────────────────────────────

def get_top_cited_papers(fs: dict, n: int = 20) -> pd.DataFrame:
    where = _paper_where(fs)
    return query(
        f"SELECT paper_id, title, year, journal, cited_by_count "
        f"FROM papers WHERE {where} AND cited_by_count IS NOT NULL "
        f"ORDER BY cited_by_count DESC LIMIT {n}"
    )


def get_citation_distribution(fs: dict) -> pd.DataFrame:
    where = _paper_where(fs)
    return query(
        f"SELECT cited_by_count FROM papers WHERE {where} AND cited_by_count IS NOT NULL"
    )


def get_top_authors(fs: dict, n: int = 20) -> pd.DataFrame:
    return query(
        f"SELECT author_id, display_name, orcid, paper_count "
        f"FROM authors ORDER BY paper_count DESC LIMIT {n}"
    )


def get_publication_heatmap(fs: dict) -> pd.DataFrame:
    """Papers by year × study_type for heatmap."""
    where = _paper_where(fs)
    return query(
        f"SELECT CAST(year AS INT) AS year, study_type, count(*) AS papers "
        f"FROM papers WHERE {where} AND study_type IS NOT NULL "
        f"GROUP BY year, study_type ORDER BY year"
    )


# ── Methods & Sensors ─────────────────────────────────────────────────────────

def get_top_methods(n: int = 30) -> pd.DataFrame:
    return query(
        f"SELECT canonical_id, display_name, type_group, paper_count, source_count "
        f"FROM methods ORDER BY paper_count DESC LIMIT {n}"
    )


def get_top_sensors(n: int = 30) -> pd.DataFrame:
    return query(
        f"SELECT canonical_id, display_name, sensor_family, paper_count "
        f"FROM sensors ORDER BY paper_count DESC LIMIT {n}"
    )


def get_method_year_evolution(fs: dict, method_ids: Optional[list] = None, n: int = 8) -> pd.DataFrame:
    """Paper count per year per method (top-n methods)."""
    where = _paper_where(fs)
    if method_ids is None:
        top = query(f"SELECT canonical_id FROM methods ORDER BY paper_count DESC LIMIT {n}")
        method_ids = top["canonical_id"].tolist()
    if not method_ids:
        return pd.DataFrame()
    ids_sql = ",".join(f"'{m}'" for m in method_ids)
    return query(
        f"""
        SELECT CAST(p.year AS INT) AS year, ne.canonical_id, ne.display_name,
               count(DISTINCT p.paper_id) AS papers
        FROM papers p
        JOIN paper_author_edges pae ON pae.paper_id = p.paper_id
        WHERE p.{where.replace('year', 'p.year').replace('primary_country', 'p.primary_country')
                    .replace('study_type', 'p.study_type').replace('cited_by_count', 'p.cited_by_count')
                    .replace('has_doi', 'p.has_doi').replace('has_openalex', 'p.has_openalex')}
        LIMIT 0
        """
    )


def get_method_evolution_simple(fs: dict, n: int = 8) -> pd.DataFrame:
    """Year × method paper counts using JOIN on papers."""
    where = _paper_where(fs)
    top = query(f"SELECT canonical_id FROM methods ORDER BY paper_count DESC LIMIT {n}")
    if top.empty:
        return pd.DataFrame()
    ids_sql = ",".join(f"'{m}'" for m in top["canonical_id"].tolist())

    # Use paper_reference_edges as bridge — actually we need normalized_entities
    # Since normalized_entities are in the paper JSONs (not parquet directly),
    # approximate using methods.paper_count and year from papers.
    # Best we can do from current parquet: return methods static table.
    return query(
        f"SELECT canonical_id, display_name, type_group, paper_count "
        f"FROM methods WHERE canonical_id IN ({ids_sql}) ORDER BY paper_count DESC"
    )


def get_top_metrics() -> pd.DataFrame:
    return query("SELECT canonical_id, display_name, metric_type, paper_count FROM metrics ORDER BY paper_count DESC")


# ── Topics ─────────────────────────────────────────────────────────────────────

def get_top_topics(n: int = 20) -> pd.DataFrame:
    return query(
        f"SELECT topic_id, topic_name, paper_count, max_score, avg_score "
        f"FROM topics ORDER BY paper_count DESC LIMIT {n}"
    )


def get_topic_year_evolution(fs: dict, n: int = 8) -> pd.DataFrame:
    """Papers per year per top topic."""
    where = _paper_where(fs)
    top = query(f"SELECT topic_id FROM topics ORDER BY paper_count DESC LIMIT {n}")
    if top.empty:
        return pd.DataFrame()
    ids_sql = ",".join(f"'{t}'" for t in top["topic_id"].tolist())
    return query(
        f"""
        SELECT CAST(p.year AS INT) AS year, t.topic_name, count(DISTINCT p.paper_id) AS papers
        FROM papers p
        JOIN paper_topic_edges pte ON pte.paper_id = p.paper_id
        JOIN topics t ON t.topic_id = pte.topic_id
        WHERE {where.replace('year', 'p.year').replace('primary_country','p.primary_country')
                    .replace('study_type','p.study_type').replace('cited_by_count','p.cited_by_count')
                    .replace('has_doi','p.has_doi').replace('has_openalex','p.has_openalex')}
          AND t.topic_id IN ({ids_sql})
        GROUP BY p.year, t.topic_name
        ORDER BY year, papers DESC
        """
    )


def get_topic_score_distribution() -> pd.DataFrame:
    return query(
        "SELECT topic_name, paper_count, avg_score, max_score "
        "FROM topics ORDER BY paper_count DESC LIMIT 20"
    )


# ── Citations ─────────────────────────────────────────────────────────────────

def get_paper_references(paper_id: str, max_refs: int = 100) -> pd.DataFrame:
    return query(
        f"""
        SELECT r.reference_id, r.title, r.doi, r.journal, r.year, r.authors_count
        FROM paper_reference_edges pre
        JOIN paper_refs r ON r.reference_id = pre.reference_id
        WHERE pre.paper_id = '{paper_id}'
        LIMIT {max_refs}
        """
    )


def search_papers(search_term: str, limit: int = 30) -> pd.DataFrame:
    term = search_term.replace("'", "''")
    return query(
        f"""
        SELECT paper_id, title, year, journal, cited_by_count, has_doi, has_openalex
        FROM papers
        WHERE title ILIKE '%{term}%'
        ORDER BY cited_by_count DESC NULLS LAST
        LIMIT {limit}
        """
    )


# ── Geospatial ────────────────────────────────────────────────────────────────

def get_country_paper_counts(fs: dict) -> pd.DataFrame:
    where = _paper_where(fs)
    return query(
        f"SELECT primary_country AS country, count(*) AS papers, "
        f"avg(cited_by_count) AS avg_citations "
        f"FROM papers WHERE {where} AND primary_country IS NOT NULL "
        f"GROUP BY primary_country ORDER BY papers DESC"
    )


def get_institution_geo() -> pd.DataFrame:
    return query(
        "SELECT institution_id, display_name, country_code, institution_type, "
        "paper_count, author_count FROM institutions "
        "WHERE country_code IS NOT NULL ORDER BY paper_count DESC LIMIT 200"
    )


# ── Quality ───────────────────────────────────────────────────────────────────

def get_quality_stats(fs: dict) -> dict:
    where = _paper_where(fs)
    total     = scalar(f"SELECT count(*) FROM papers WHERE {where}") or 1
    doi_pct   = scalar(f"SELECT round(100.0*count(*) FILTER(WHERE has_doi)/count(*),2) FROM papers WHERE {where}") or 0
    oa_pct    = scalar(f"SELECT round(100.0*count(*) FILTER(WHERE has_openalex)/count(*),2) FROM papers WHERE {where}") or 0
    abs_pct   = scalar(f"SELECT round(100.0*count(*) FILTER(WHERE has_abstract)/count(*),2) FROM papers WHERE {where}") or 0
    m0_pct    = scalar(f"SELECT round(100.0*count(*) FILTER(WHERE methods_count=0)/count(*),2) FROM papers WHERE {where}") or 0
    s0_pct    = scalar(f"SELECT round(100.0*count(*) FILTER(WHERE sensors_count=0)/count(*),2) FROM papers WHERE {where}") or 0
    no_ref    = scalar(f"SELECT round(100.0*count(*) FILTER(WHERE references_count=0)/count(*),2) FROM papers WHERE {where}") or 0
    inst_null = scalar("SELECT round(100.0*count(*) FILTER(WHERE country_code IS NULL)/count(*),2) FROM institutions") or 0
    ref_doi   = scalar("SELECT round(100.0*count(*) FILTER(WHERE doi IS NOT NULL)/count(*),2) FROM paper_refs") or 0
    return dict(
        total_papers=int(total),
        doi_coverage=float(doi_pct),
        openalex_coverage=float(oa_pct),
        abstract_coverage=float(abs_pct),
        papers_no_methods=float(m0_pct),
        papers_no_sensors=float(s0_pct),
        papers_no_refs=float(no_ref),
        institutions_no_country=float(inst_null),
        refs_with_doi=float(ref_doi),
    )


def get_coverage_by_year(fs: dict) -> pd.DataFrame:
    where = _paper_where(fs)
    return query(
        f"""
        SELECT CAST(year AS INT) AS year,
               count(*) AS total,
               count(*) FILTER(WHERE has_doi) AS with_doi,
               count(*) FILTER(WHERE has_openalex) AS with_openalex,
               count(*) FILTER(WHERE methods_count > 0) AS with_methods,
               count(*) FILTER(WHERE sensors_count > 0) AS with_sensors
        FROM papers WHERE {where}
        GROUP BY year ORDER BY year
        """
    )


# ── Explorer ──────────────────────────────────────────────────────────────────

def get_paper_detail(paper_id: str) -> Optional[dict]:
    df = query(
        f"SELECT * FROM papers WHERE paper_id = '{paper_id.replace(chr(39), chr(39)*2)}' LIMIT 1"
    )
    if df.empty:
        return None
    return df.iloc[0].to_dict()


def get_paper_topics(paper_id: str) -> pd.DataFrame:
    return query(
        f"""
        SELECT t.topic_name, pte.score
        FROM paper_topic_edges pte
        JOIN topics t ON t.topic_id = pte.topic_id
        WHERE pte.paper_id = '{paper_id}'
        ORDER BY pte.score DESC
        """
    )


def get_paper_authors_detail(paper_id: str) -> pd.DataFrame:
    return query(
        f"""
        SELECT a.display_name, pae.author_position, pae.is_corresponding,
               a.paper_count, a.orcid
        FROM paper_author_edges pae
        JOIN authors a ON a.author_id = pae.author_id
        WHERE pae.paper_id = '{paper_id}'
        ORDER BY pae.author_position
        """
    )


def get_filter_options() -> dict:
    """Return option lists for filter dropdowns."""
    countries = query(
        "SELECT DISTINCT primary_country AS v FROM papers "
        "WHERE primary_country IS NOT NULL ORDER BY primary_country"
    )["v"].tolist()

    study_types = query(
        "SELECT DISTINCT study_type AS v FROM papers "
        "WHERE study_type IS NOT NULL ORDER BY study_type"
    )["v"].tolist()

    return dict(
        countries=[{"label": c, "value": c} for c in countries],
        study_types=[{"label": s.replace("_", " ").title(), "value": s} for s in study_types],
    )
