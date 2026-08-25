"""
sodb_manifest.py — Per-paper SODB stage completion tracker.

Written as JSON alongside the parquet files so it's human-readable and
doesn't require pyarrow to inspect pipeline state.

Format: data/sodb/{paper_id}/.sodb_manifest.json

Staleness: a stage is stale if the manifest's pipeline_hash differs from
the current pipeline_hash.  All stages must be re-run when the hash changes.

Usage::

    m = SODBManifest("2023_Smith", pipeline_hash="sha256:abc123")
    if not m.is_done("numeric_facts"):
        m.mark_running("numeric_facts")
        # ... extract ...
        m.mark_done("numeric_facts")
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

StageStatus = Literal["pending", "running", "done", "failed"]

# Canonical stage order — stages run in this sequence
STAGES: tuple[str, ...] = (
    "grobid_parse",
    "region_extract",
    "nougat_infer",
    "formula_extract",
    "table_extract",
    "numeric_facts",
    "chunk_build",
    "kg_sync",
    "vector_sync",
)

_SODB_ROOT = Path(os.getenv("SODB_DIR",
    str(Path(__file__).resolve().parents[2] / "data" / "sodb")))


class SODBManifest:
    """
    Tracks per-paper stage completion and pipeline-hash staleness.

    Parameters
    ----------
    paper_id      : corpus paper identifier
    pipeline_hash : current pipeline version hash; used for staleness detection
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
        self._path         = sodb_root / paper_id / ".sodb_manifest.json"
        self._data: dict   = self._load()

    # ── Stage queries ─────────────────────────────────────────────────────────

    def is_done(self, stage: str) -> bool:
        """True iff the stage completed under the current pipeline_hash."""
        if self.is_stale():
            return False
        return self._data["stages"].get(stage, {}).get("status") == "done"

    def is_stale(self) -> bool:
        """True if the manifest was written under a different pipeline_hash."""
        return self._data.get("pipeline_hash") != self.pipeline_hash

    def status(self, stage: str) -> StageStatus:
        return self._data["stages"].get(stage, {}).get("status", "pending")

    # ── Stage transitions ─────────────────────────────────────────────────────

    def mark_running(self, stage: str) -> None:
        self._data["stages"][stage] = {
            "status":     "running",
            "started_at": _now_iso(),
        }
        self._save()

    def mark_done(self, stage: str) -> None:
        self._data["stages"][stage] = {
            "status":       "done",
            "completed_at": _now_iso(),
        }
        self._save()

    def mark_failed(self, stage: str, error: str) -> None:
        self._data["stages"][stage] = {
            "status":    "failed",
            "failed_at": _now_iso(),
            "error":     error[:500],   # truncate to avoid unbounded JSON growth
        }
        self._save()

    # ── Internal ─────────────────────────────────────────────────────────────

    def _load(self) -> dict:
        if self._path.exists():
            try:
                with open(self._path, encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError):
                pass
        return {
            "paper_id":      self.paper_id,
            "pipeline_hash": self.pipeline_hash,
            "stages":        {s: {"status": "pending"} for s in STAGES},
        }

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self._data, f, indent=2, default=str)
        tmp.rename(self._path)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
