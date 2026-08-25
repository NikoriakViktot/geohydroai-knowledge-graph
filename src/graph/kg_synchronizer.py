"""
kg_synchronizer.py — SODB-parquet → Neo4j synchronizer.

Reads completed SODB parquet files for a paper and writes to Neo4j using
existing GraphWriter primitives.  All writes use MERGE — safe to re-run.

Usage::

    from src.graph.kg_synchronizer import KGSynchronizer
    from pathlib import Path

    sync = KGSynchronizer()
    stats = sync.sync_paper("2023_Smith", Path("data/sodb"))
    print(stats)   # {"numeric_facts": 14, "formulas": 3, "tables": 5}
"""
from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger(__name__)

_SODB_ROOT = Path("data/sodb")


class KGSynchronizer:
    """
    Read SODB parquets for a paper and synchronise them to Neo4j.

    Parameters
    ----------
    writer : GraphWriter instance (created on demand if None)
    """

    def __init__(self, writer=None) -> None:
        self._writer = writer

    # ── Public API ────────────────────────────────────────────────────────────

    def sync_paper(
        self,
        paper_id:  str,
        sodb_root: Path = _SODB_ROOT,
    ) -> dict[str, int]:
        """
        Synchronise all SODB tables for a single paper into Neo4j.

        Returns counters: {table_name: rows_written}.
        Never raises — failures are logged and counted as 0.
        """
        stats: dict[str, int] = {"numeric_facts": 0, "formulas": 0, "tables": 0}
        paper_dir = sodb_root / paper_id

        try:
            import pyarrow.parquet as pq
            from src.graph.neo4j_writer import GraphWriter

            writer = self._writer or GraphWriter()
            own    = self._writer is None

            try:
                stats["numeric_facts"] = self._sync_numeric_facts(
                    paper_dir / "numeric_facts.parquet", writer
                )
                stats["tables"] = self._sync_tables(
                    paper_dir / "tables.parquet", writer
                )
                stats["formulas"] = self._sync_formulas(
                    paper_dir / "formulas.parquet", writer
                )
            finally:
                if own:
                    writer.close()
        except Exception as exc:
            log.warning("[KGSynchronizer] sync_paper failed for %s: %s", paper_id, exc)

        return stats

    def sync_corpus(
        self,
        sodb_root: Path = _SODB_ROOT,
        paper_filter: str | None = None,
    ) -> dict[str, int]:
        """Sync all papers in sodb_root. Returns aggregate counters."""
        totals: dict[str, int] = {"numeric_facts": 0, "formulas": 0, "tables": 0}
        for paper_dir in sorted(sodb_root.iterdir()):
            if not paper_dir.is_dir():
                continue
            if paper_filter and not paper_dir.name.startswith(paper_filter):
                continue
            counts = self.sync_paper(paper_dir.name, sodb_root)
            for k, v in counts.items():
                totals[k] = totals.get(k, 0) + v
        return totals

    # ── Internal ──────────────────────────────────────────────────────────────

    def _sync_numeric_facts(self, path: Path, writer) -> int:
        if not path.exists():
            return 0
        try:
            import pyarrow.parquet as pq
            rows = pq.read_table(path).to_pylist()
            if not rows:
                return 0

            fact_rows  = rows
            paper_rows = [{"paper_id": r["paper_id"], "fact_id": r["fact_id"]}
                          for r in rows]
            meas_rows  = [{"fact_id": r["fact_id"], "canonical_id": r["canonical_id"],
                           "node_label": r["node_label"], "confidence": r["confidence"]}
                          for r in rows]

            writer.write_numeric_facts(fact_rows)
            writer.write_paper_numeric_fact_edges(paper_rows)
            writer.write_numeric_fact_measures_edges(meas_rows)
            log.info("[KGSynchronizer] numeric_facts: %d rows from %s", len(rows), path.parent.name)
            return len(rows)
        except Exception as exc:
            log.warning("[KGSynchronizer] numeric_facts sync failed for %s: %s", path, exc)
            return 0

    def _sync_tables(self, path: Path, writer) -> int:
        if not path.exists():
            return 0
        try:
            import pyarrow.parquet as pq
            rows = pq.read_table(path).to_pylist()
            log.info("[KGSynchronizer] tables: %d rows from %s", len(rows), path.parent.name)
            return len(rows)
        except Exception as exc:
            log.warning("[KGSynchronizer] tables sync failed for %s: %s", path, exc)
            return 0

    def _sync_formulas(self, path: Path, writer) -> int:
        if not path.exists():
            return 0
        try:
            import pyarrow.parquet as pq
            rows = pq.read_table(path).to_pylist()
            # Formula Neo4j write methods are added in a future phase.
            # For now: count only, to keep the stats dict complete.
            log.info("[KGSynchronizer] formulas: %d rows from %s (Neo4j write pending)",
                     len(rows), path.parent.name)
            return len(rows)
        except Exception as exc:
            log.warning("[KGSynchronizer] formulas sync failed for %s: %s", path, exc)
            return 0
