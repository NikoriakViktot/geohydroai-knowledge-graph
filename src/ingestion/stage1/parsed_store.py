"""
parsed_store.py — Immutable parsed artifact store for Stage 1.

Layout::

    data/parsed/{paper_id}/
        tei.xml              — raw GROBID TEI output (immutable)
        sections.parquet     — structured body sections
        figures.parquet      — figure metadata + layout
        tables.parquet       — table metadata + layout
        equations.parquet    — formula text + layout
        references.parquet   — back-matter bibliography entries
        captions.parquet     — unified caption index (figures + tables)
        parsing_manifest.json — stage completion record

Design rules:
  - All writes are atomic (write .tmp → rename).
  - Parquet files are schema-validated via PyArrow.
  - pipeline_hash and created_at are injected into every parquet row.
  - Re-runs skip files that already exist (idempotent).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from src.analytics.parquet_schema import (
    CAPTIONS_SCHEMA,
    EQUATIONS_SCHEMA,
    PARSED_FIGURES_SCHEMA,
    PARSED_REFS_SCHEMA,
    SECTIONS_SCHEMA,
    TABLES_SCHEMA,
)
from src.config.settings import PARSED_DIR


class ParsedStore:
    """
    Reads and writes Stage 1 artifacts for a single paper.

    Parameters
    ----------
    paper_id      : Canonical paper identifier (SHA-256 hex from Stage 0).
    pipeline_hash : Current pipeline version hash for staleness detection.
    parsed_root   : Root directory for all parsed stores (default: $PARSED_DIR).
    """

    def __init__(
        self,
        paper_id:      str,
        pipeline_hash: str,
        parsed_root:   Path = PARSED_DIR,
    ) -> None:
        self.paper_id      = paper_id
        self.pipeline_hash = pipeline_hash
        self.root          = parsed_root / paper_id
        self.root.mkdir(parents=True, exist_ok=True)

    # ── Existence checks ──────────────────────────────────────────────────────

    @property
    def is_complete(self) -> bool:
        """True when all mandatory Stage 1 artifacts are present."""
        return (
            self.tei_path.exists()
            and self.sections_path.exists()
            and self.figures_path.exists()
            and self.tables_path.exists()
            and self.equations_path.exists()
            and self.refs_path.exists()
            and self.captions_path.exists()
            and self.manifest_path.exists()
        )

    # ── Path accessors ────────────────────────────────────────────────────────

    @property
    def tei_path(self) -> Path:
        return self.root / "tei.xml"

    @property
    def sections_path(self) -> Path:
        return self.root / "sections.parquet"

    @property
    def figures_path(self) -> Path:
        return self.root / "figures.parquet"

    @property
    def tables_path(self) -> Path:
        return self.root / "tables.parquet"

    @property
    def equations_path(self) -> Path:
        return self.root / "equations.parquet"

    @property
    def refs_path(self) -> Path:
        return self.root / "references.parquet"

    @property
    def captions_path(self) -> Path:
        return self.root / "captions.parquet"

    @property
    def manifest_path(self) -> Path:
        return self.root / "parsing_manifest.json"

    # ── Write API ─────────────────────────────────────────────────────────────

    def write_tei(self, xml_text: str) -> None:
        """Atomically write raw GROBID TEI XML."""
        _atomic_write(self.tei_path, xml_text)

    def write_sections(self, rows: list[dict[str, Any]]) -> Path:
        return self._write_parquet(self.sections_path, rows, SECTIONS_SCHEMA)

    def write_figures(self, rows: list[dict[str, Any]]) -> Path:
        return self._write_parquet(self.figures_path, rows, PARSED_FIGURES_SCHEMA)

    def write_tables(self, rows: list[dict[str, Any]]) -> Path:
        return self._write_parquet(self.tables_path, rows, TABLES_SCHEMA)

    def write_equations(self, rows: list[dict[str, Any]]) -> Path:
        return self._write_parquet(self.equations_path, rows, EQUATIONS_SCHEMA)

    def write_references(self, rows: list[dict[str, Any]]) -> Path:
        return self._write_parquet(self.refs_path, rows, PARSED_REFS_SCHEMA)

    def write_captions(self, rows: list[dict[str, Any]]) -> Path:
        return self._write_parquet(self.captions_path, rows, CAPTIONS_SCHEMA)

    def write_manifest(self, info: dict[str, Any]) -> None:
        """Write parsing_manifest.json with stage completion record."""
        payload = {
            "paper_id":      self.paper_id,
            "pipeline_hash": self.pipeline_hash,
            "completed_at":  _now_iso(),
            **info,
        }
        _atomic_json(self.manifest_path, payload)

    # ── Read API ──────────────────────────────────────────────────────────────

    def read_tei(self) -> str | None:
        if not self.tei_path.exists():
            return None
        return self.tei_path.read_text(encoding="utf-8")

    def read_manifest(self) -> dict[str, Any]:
        if not self.manifest_path.exists():
            return {}
        return json.loads(self.manifest_path.read_text(encoding="utf-8"))

    def read_parquet(self, filename: str) -> list[dict[str, Any]]:
        """Read any parquet artifact in this store as a list of row dicts."""
        path = self.root / filename
        if not path.exists():
            return []
        table = pq.read_table(path)
        return table.to_pylist()

    # ── Internal ──────────────────────────────────────────────────────────────

    def _write_parquet(
        self,
        path:   Path,
        rows:   list[dict[str, Any]],
        schema: pa.Schema,
    ) -> Path:
        now = datetime.now(timezone.utc)
        for row in rows:
            row.setdefault("pipeline_hash", self.pipeline_hash)
            row.setdefault("created_at",    now)
        table = pa.Table.from_pylist(rows, schema=schema)
        tmp   = path.with_suffix(".parquet.tmp")
        pq.write_table(table, tmp, compression="snappy")
        tmp.rename(path)
        return path


# ── Helpers ───────────────────────────────────────────────────────────────────

def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.rename(path)


def _atomic_json(path: Path, data: Any) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(data, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    tmp.rename(path)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
