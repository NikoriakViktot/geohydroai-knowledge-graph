"""
paper_assembler.py — Assemble paper.json from SODB parquet files.

paper.json is a derived artifact.  The SODB parquets are the source of truth.
This module generates (or regenerates) paper.json from the completed SODB state
without requiring any model inference or external API calls.

Usage::

    from src.assembly.paper_assembler import PaperAssembler
    from pathlib import Path

    assembler = PaperAssembler(sodb_root=Path("data/sodb"))
    paper = assembler.assemble("2023_Smith")
    # paper["metrics"] == ["NSE", "KGE"]
    # paper["numeric_facts"] == [...]
"""
from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger(__name__)

_SODB_ROOT = Path("data/sodb")


class PaperAssembler:
    """
    Assemble a paper dict from SODB parquet tables.

    The assembled dict is a subset of the full paper.json produced by the
    extraction pipeline — it contains only what is already stored in SODB.
    Use it to reconstruct or augment paper.json after SODB is populated.

    Parameters
    ----------
    sodb_root : root of the SODB directory tree (default: data/sodb)
    """

    def __init__(self, sodb_root: Path = _SODB_ROOT) -> None:
        self.sodb_root = Path(sodb_root)

    # ── Public API ────────────────────────────────────────────────────────────

    def assemble(self, paper_id: str) -> dict:
        """
        Build a paper summary dict from all available SODB parquets.

        Returns a dict with keys present only when the corresponding parquet
        exists.  Missing parquets are silently skipped.
        """
        paper: dict = {"paper_id": paper_id}
        paper_dir   = self.sodb_root / paper_id

        paper.update(self._load_numeric_facts(paper_dir))
        paper.update(self._load_formulas(paper_dir))
        paper.update(self._load_tables(paper_dir))
        paper.update(self._load_regions(paper_dir))

        return paper

    def assemble_batch(
        self,
        paper_filter: str | None = None,
    ) -> list[dict]:
        """Assemble all papers in sodb_root, returning list of paper dicts."""
        results: list[dict] = []
        for paper_dir in sorted(self.sodb_root.iterdir()):
            if not paper_dir.is_dir():
                continue
            if paper_filter and not paper_dir.name.startswith(paper_filter):
                continue
            results.append(self.assemble(paper_dir.name))
        return results

    # ── Internal ──────────────────────────────────────────────────────────────

    def _read_parquet(self, path: Path) -> list[dict]:
        if not path.exists():
            return []
        try:
            import pyarrow.parquet as pq
            return pq.read_table(path).to_pylist()
        except Exception as exc:
            log.warning("[PaperAssembler] failed to read %s: %s", path, exc)
            return []

    def _load_numeric_facts(self, paper_dir: Path) -> dict:
        # Delegate to the public utility so both PaperStore and PaperAssembler
        # share the same reading/error-handling logic.
        from src.extraction.sodb_metrics_loader import load_numeric_facts
        paper_id = paper_dir.name
        facts = load_numeric_facts(paper_id, sodb_root=paper_dir.parent)
        if not facts:
            return {}
        return {
            "numeric_facts": facts,
            "metrics":        sorted({f["metric"] for f in facts if f.get("metric")}),
            "metric_values": {
                m: [f["value"] for f in facts if f.get("metric") == m and f.get("value") is not None]
                for m in {f["metric"] for f in facts if f.get("metric")}
            },
        }

    def _load_formulas(self, paper_dir: Path) -> dict:
        rows = self._read_parquet(paper_dir / "formulas.parquet")
        if not rows:
            return {}
        return {
            "formulas": [
                {
                    "formula_id":    r["formula_id"],
                    "latex":         r.get("latex"),
                    "text":          r.get("text"),
                    "formula_class": r.get("formula_class"),
                    "page":          r.get("page"),
                    "confidence":    r.get("confidence"),
                }
                for r in rows
            ],
        }

    def _load_tables(self, paper_dir: Path) -> dict:
        rows = self._read_parquet(paper_dir / "tables.parquet")
        if not rows:
            return {}
        return {
            "tables": [
                {
                    "table_id":   r["table_id"],
                    "label":      r.get("label"),
                    "caption":    r.get("caption"),
                    "page":       r.get("page"),
                    "row_count":  r.get("row_count"),
                    "col_count":  r.get("col_count"),
                    "has_numeric_data": r.get("has_numeric_data"),
                }
                for r in rows
            ],
        }

    def _load_regions(self, paper_dir: Path) -> dict:
        rows = self._read_parquet(paper_dir / "regions.parquet")
        if not rows:
            return {}
        return {
            "regions_count":      len(rows),
            "formula_regions":    sum(1 for r in rows if r.get("region_type") == "FORMULA_REGION"),
            "table_regions":      sum(1 for r in rows if r.get("region_type") == "TABLE_REGION"),
            "has_nougat_content": any(r.get("nougat_latex") or r.get("nougat_text") for r in rows),
        }
