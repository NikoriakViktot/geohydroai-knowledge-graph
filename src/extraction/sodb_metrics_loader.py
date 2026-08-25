"""
sodb_metrics_loader.py — public utility for loading numeric_facts.parquet.

Used by both PaperStore and PaperAssembler; avoids duplicating private methods.
"""
from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_SODB_ROOT = _PROJECT_ROOT / "data" / "sodb"


def load_numeric_facts(
    paper_id: str,
    sodb_root: Path = _DEFAULT_SODB_ROOT,
) -> list[dict]:
    """
    Load numeric_facts.parquet for *paper_id*.

    Returns a list of row dicts (columns: fact_id, paper_id, table_id,
    metric, canonical_id, value, unit, confidence, col_header, row_context, …).
    Returns [] if the file is absent or unreadable.
    """
    path = sodb_root / paper_id / "numeric_facts.parquet"
    if not path.exists():
        return []
    try:
        import pyarrow.parquet as pq  # optional dependency — only imported when needed
        rows = pq.read_table(path).to_pylist()
        logger.debug("Loaded %d numeric facts for %s", len(rows), paper_id)
        return rows
    except Exception as exc:
        logger.warning("Failed to read numeric_facts for %s: %s", paper_id, exc)
        return []
