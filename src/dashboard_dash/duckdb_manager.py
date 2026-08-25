"""
duckdb_manager.py  —  Thread-local DuckDB connections with parquet views
"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Optional

import duckdb
import pandas as pd

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_ANALYTICS    = _PROJECT_ROOT / "data" / "analytics"

_local = threading.local()

# Tables that map to parquet files (DuckDB reserved word "references" is quoted)
_VIEWS: dict[str, str] = {
    "papers":               "papers.parquet",
    "authors":              "authors.parquet",
    "paper_author_edges":   "paper_author_edges.parquet",
    "topics":               "topics.parquet",
    "paper_topic_edges":    "paper_topic_edges.parquet",
    "paper_refs":           "references.parquet",          # avoid reserved word
    "paper_reference_edges":"paper_reference_edges.parquet",
    "methods":              "methods.parquet",
    "sensors":              "sensors.parquet",
    "metrics":              "metrics.parquet",
    "institutions":         "institutions.parquet",
}


def get_con() -> duckdb.DuckDBPyConnection:
    """Return the thread-local DuckDB connection, creating it if needed."""
    if not hasattr(_local, "con") or _local.con is None:
        con = duckdb.connect()
        for view_name, filename in _VIEWS.items():
            parquet_path = str(_ANALYTICS / filename).replace("\\", "/")
            con.execute(
                f"CREATE OR REPLACE VIEW {view_name} AS "
                f"SELECT * FROM read_parquet('{parquet_path}')"
            )
        _local.con = con
    return _local.con


def query(sql: str, params: Optional[list] = None) -> pd.DataFrame:
    """Execute SQL against the thread-local connection. Returns a DataFrame."""
    con = get_con()
    if params:
        return con.execute(sql, params).df()
    return con.execute(sql).df()


def scalar(sql: str, params: Optional[list] = None) -> object:
    """Return the first cell of the first result row."""
    con = get_con()
    if params:
        result = con.execute(sql, params).fetchone()
    else:
        result = con.execute(sql).fetchone()
    return result[0] if result else None


def verify_views() -> dict[str, bool]:
    """Check which views are accessible (parquet files exist)."""
    status = {}
    for view_name in _VIEWS:
        try:
            query(f"SELECT 1 FROM {view_name} LIMIT 1")
            status[view_name] = True
        except Exception:
            status[view_name] = False
    return status
