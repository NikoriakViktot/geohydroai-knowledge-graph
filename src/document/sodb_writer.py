"""
sodb_writer.py — Atomic, schema-validated Parquet writer for SODB objects.

One instance per paper per pipeline run.  All write methods are:
  - Atomic: write to .parquet.tmp, then os.rename (POSIX atomic on same fs)
  - Idempotent: calling write_* twice produces the same file
  - Schema-enforced: PyArrow rejects rows that don't match the declared schema

Usage::

    writer = SODBWriter(paper_id="2023_Smith", pipeline_hash="sha256:abc123")
    writer.write_regions(rows)
    writer.write_formulas(rows)
    writer.write_tables(rows)
    writer.write_numeric_facts(rows)

All row dicts must conform to the corresponding schema in parquet_schema.py.
The writer injects pipeline_hash and created_at into every row if absent.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from src.analytics.parquet_schema import (
    CHUNKS_SCHEMA,
    FORMULAS_SCHEMA,
    NUMERIC_FACTS_SCHEMA,
    REGIONS_SCHEMA,
    TABLES_SCHEMA,
)

_SODB_ROOT = Path(os.getenv("SODB_DIR",
    str(Path(__file__).resolve().parents[2] / "data" / "sodb")))


class SODBWriter:
    """
    Atomic per-paper Parquet writer for the SODB layer.

    Parameters
    ----------
    paper_id      : corpus paper identifier
    pipeline_hash : SHA-256 hash string identifying this pipeline version
    sodb_root     : root of the SODB directory tree (default: $SODB_DIR)
    """

    def __init__(
        self,
        paper_id:      str,
        pipeline_hash: str,
        sodb_root:     Path = _SODB_ROOT,
    ) -> None:
        self.paper_id      = paper_id
        self.pipeline_hash = pipeline_hash
        self.paper_dir     = sodb_root / paper_id
        self.paper_dir.mkdir(parents=True, exist_ok=True)

    # ── Public write methods ──────────────────────────────────────────────────

    def write_regions(self, rows: list[dict[str, Any]]) -> Path:
        return self._write("regions.parquet", rows, REGIONS_SCHEMA)

    def write_formulas(self, rows: list[dict[str, Any]]) -> Path:
        return self._write("formulas.parquet", rows, FORMULAS_SCHEMA)

    def write_tables(self, rows: list[dict[str, Any]]) -> Path:
        return self._write("tables.parquet", rows, TABLES_SCHEMA)

    def write_numeric_facts(self, rows: list[dict[str, Any]]) -> Path:
        return self._write("numeric_facts.parquet", rows, NUMERIC_FACTS_SCHEMA)

    def write_chunks(self, rows: list[dict[str, Any]]) -> Path:
        return self._write("chunks.parquet", rows, CHUNKS_SCHEMA)

    # ── Existence helpers ─────────────────────────────────────────────────────

    def exists(self, filename: str) -> bool:
        return (self.paper_dir / filename).exists()

    def path(self, filename: str) -> Path:
        return self.paper_dir / filename

    # ── Internal ─────────────────────────────────────────────────────────────

    def _write(
        self,
        filename: str,
        rows:     list[dict[str, Any]],
        schema:   pa.Schema,
    ) -> Path:
        """Write rows to filename atomically. Injects pipeline_hash and created_at."""
        out_path = self.paper_dir / filename
        now = datetime.now(timezone.utc)
        for row in rows:
            row.setdefault("pipeline_hash", self.pipeline_hash)
            row.setdefault("created_at",    now)

        table = pa.Table.from_pylist(rows, schema=schema)
        tmp   = out_path.with_suffix(".parquet.tmp")
        pq.write_table(table, tmp, compression="snappy")
        tmp.rename(out_path)
        return out_path
