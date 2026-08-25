"""
pipeline_integration.py  —  Registry ↔ pipeline_runner bridge
==============================================================

Shows how PipelineRegistry integrates with the existing Ray-based
pipeline_runner.run_distributed() pattern.

The registry operates on the DRIVER side only.  Ray workers return
result dicts; the driver updates the registry in the result-processing
loop.  This is the correct DuckDB single-writer pattern.

Usage (in pipeline_runner.py or a new registry-aware runner):
-------------------------------------------------------------
    from src.registry.pipeline_integration import RegistryAwarePipelineRunner

    runner = RegistryAwarePipelineRunner(
        xml_dir=XML_DIR,
        out_dir=OUT_DIR,
        registry_dir=REGISTRY_DIR,
    )
    stats = runner.run(overwrite=False)
    runner.export_parquet()
"""

from __future__ import annotations

import hashlib
import logging
import os
import time
import traceback
from collections import Counter
from pathlib import Path
from typing import Optional

from src.config.settings import (
    XML_DIR, OUT_DIR, REGISTRY_DIR,
    PIPELINE_VERSION, RAY_MAX_CONCURRENT,
)
from src.registry.registry_db import PipelineRegistry
from src.registry.constants  import PaperStatus, HEARTBEAT_TIMEOUT_SEC

log = logging.getLogger(__name__)


def _md5(path: Path) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


class RegistryAwarePipelineRunner:
    """
    Drop-in replacement for pipeline_runner.run_distributed() that adds:

    - Registry-based idempotency (supersedes filesystem stat() checks)
    - Retry control with exponential back-off
    - Per-paper telemetry (start time, runtime, stage timings)
    - Stale worker detection via heartbeats
    - Full audit trail via registry_events

    Concurrency model
    -----------------
    The runner owns the DuckDB connection on the DRIVER process.
    Ray tasks call process_paper.remote() and return result dicts.
    The driver calls mark_success() / mark_failure() from the
    result-collection loop — never from inside a Ray task.

    Integration with existing process_paper
    ----------------------------------------
    process_paper() must return one of:

        {"_status": "SUCCESS", "paper_id": ..., "output_json": ...,
         "output_hash": ..., "runtime_sec": ..., "stage_timings": {...}}

        {"_status": "SKIPPED", "paper_id": ...}

        {"_status": "FAIL", "paper_id": ..., "error_type": ...,
         "error_message": ..., "error_traceback": ...}

        {"_status": "INVALID_XML", "paper_id": ..., "error_message": ...}
    """

    def __init__(
        self,
        xml_dir:      Path = XML_DIR,
        out_dir:      Path = OUT_DIR,
        registry_dir: Path = REGISTRY_DIR,
        max_concurrent: int = RAY_MAX_CONCURRENT,
        pipeline_version: str = PIPELINE_VERSION,
    ) -> None:
        self._xml_dir   = Path(xml_dir)
        self._out_dir   = Path(out_dir)
        self._reg_dir   = Path(registry_dir)
        self._max_con   = max_concurrent
        self._ver       = pipeline_version
        self._reg: Optional[PipelineRegistry] = None

    def _registry(self) -> PipelineRegistry:
        if self._reg is None:
            self._reg = PipelineRegistry(
                self._reg_dir, pipeline_version=self._ver
            )
            self._reg.init_registry()
        return self._reg

    # ── Bootstrap ─────────────────────────────────────────────────────────────

    def bootstrap(self, xml_files: Optional[list[Path]] = None) -> tuple[int, int]:
        """
        Register all XML files in xml_dir as NEW (idempotent).
        Call once at pipeline start to populate the registry from the file system.
        """
        files = xml_files or sorted(self._xml_dir.glob("*.tei.xml"))
        papers = [
            {
                "paper_id":   f.stem.replace(".tei", ""),
                "source_xml": str(f),
                "input_hash": _md5(f),
            }
            for f in files
        ]
        return self._registry().bulk_register(papers, skip_existing=True)

    # ── Result handling ───────────────────────────────────────────────────────

    def handle_result(self, result: dict, xml_path: Path) -> str:
        """
        Process a single result dict returned by process_paper.remote().

        Returns the final status string.  Call this from the driver's
        result-collection loop (after ray.get()).

        The result dict contract:
            _status:         "SUCCESS" | "SKIPPED" | "FAIL" | "INVALID_XML" | "EMPTY_OUTPUT" | "JSON_ERROR"
            paper_id:        str
            output_json:     str          (SUCCESS only)
            output_hash:     str          (SUCCESS only)
            runtime_sec:     float        (SUCCESS/FAIL)
            stage_timings:   dict         (optional)
            error_type:      str          (FAIL only)
            error_message:   str          (FAIL only)
            error_traceback: str          (FAIL only)
        """
        reg       = self._registry()
        status    = result.get("_status", "FAIL")
        paper_id  = result.get("paper_id") or xml_path.stem.replace(".tei", "")

        if status == "SUCCESS":
            reg.mark_success(
                paper_id,
                output_json   = result.get("output_json", ""),
                output_hash   = result.get("output_hash"),
                runtime_sec   = result.get("runtime_sec"),
                stage_timings = result.get("stage_timings"),
            )

        elif status == "SKIPPED":
            reg.mark_skipped(paper_id, reason="output_exists")

        elif status == "INVALID_XML":
            reg.mark_failure(
                paper_id,
                error_type    = "INVALID_XML",
                error_message = result.get("error_message", ""),
                status        = PaperStatus.INVALID_XML,  # non-retriable
            )

        else:
            # FAIL, EMPTY_OUTPUT, JSON_ERROR — all retriable
            final_status = result.get("_status", "FAIL")
            if final_status not in {s.value for s in PaperStatus}:
                final_status = "FAIL"
            reg.mark_failure(
                paper_id,
                error_type       = result.get("error_type", "UNKNOWN_ERROR"),
                error_message    = result.get("error_message", ""),
                error_traceback  = result.get("error_traceback", ""),
                runtime_sec      = result.get("runtime_sec"),
                status           = final_status,
            )

        return status

    # ── Watchdog ──────────────────────────────────────────────────────────────

    def watchdog_tick(self) -> int:
        """
        Call periodically from the orchestrator (e.g. in the main loop).
        Resets stale workers back to RETRY.

        Returns count of reset papers.
        """
        return self._registry().reset_stale_workers(HEARTBEAT_TIMEOUT_SEC)

    # ── Resume logic ──────────────────────────────────────────────────────────

    def get_pending(self, limit: Optional[int] = None) -> list[Path]:
        """
        Return Path objects for papers that should be (re)processed.

        Supersedes the filesystem stat() pre-filter in pipeline_runner.py.
        The registry is the single source of truth for pipeline state.
        """
        rows  = self._registry().get_unprocessed_papers(limit=limit)
        paths = []
        for row in rows:
            xml = row.get("source_xml", "")
            if xml:
                p = Path(xml)
                if p.exists():
                    paths.append(p)
                else:
                    log.warning("[runner] XML file gone: %s — marking INVALID_XML", xml)
                    self._registry().mark_failure(
                        row["paper_id"], "MISSING_XML",
                        f"File not found: {xml}",
                        status=PaperStatus.INVALID_XML,
                    )
            else:
                # Source_xml not stored — fall back to xml_dir
                xml_path = self._xml_dir / f"{row['paper_id']}.tei.xml"
                if xml_path.exists():
                    paths.append(xml_path)
        return paths

    # ── Parquet export ────────────────────────────────────────────────────────

    def export_parquet(self) -> None:
        """Export full registry state to Hive-partitioned Parquet."""
        outputs = self._registry().export_parquet()
        for status, path in outputs.items():
            log.info("[runner] parquet: %s → %s", status, path)

    # ── Teardown ──────────────────────────────────────────────────────────────

    def close(self) -> None:
        if self._reg:
            self._reg.close()
            self._reg = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


# ─────────────────────────────────────────────────────────────────────────────
# Standalone example: registry-aware run loop (no Ray)
# ─────────────────────────────────────────────────────────────────────────────

def run_with_registry(
    xml_dir:      Path = XML_DIR,
    out_dir:      Path = OUT_DIR,
    registry_dir: Path = REGISTRY_DIR,
    overwrite:    bool = False,
) -> Counter:
    """
    Example of a registry-aware run loop using build_paper_json directly
    (no Ray).  Shows the full integration pattern for the registry:

        bootstrap → get_pending → start_processing → handle_result → export

    For production with Ray, replace the inner build_paper_json call with
    process_paper.remote() and use ray.wait() / ray.get().
    """
    from src.ingestion.pipeline import build_paper_json
    from tqdm import tqdm

    stats  = Counter({"SUCCESS": 0, "FAIL": 0, "SKIPPED": 0})
    runner = RegistryAwarePipelineRunner(xml_dir, out_dir, registry_dir)

    try:
        reg = runner._registry()

        # 1. Bootstrap — register all XML files (idempotent)
        inserted, already_done = runner.bootstrap()
        log.info("Bootstrap: %d new, %d already registered", inserted, already_done)

        # 2. Get pending papers from registry (supersedes filesystem stat)
        pending = runner.get_pending()
        log.info("Pending: %d papers to process", len(pending))

        for xml_path in tqdm(pending, desc="Processing", unit="paper"):
            paper_id = xml_path.stem.replace(".tei", "")

            # 3. Claim the paper (optimistic lock)
            if not reg.start_processing(paper_id):
                log.debug("Already claimed: %s", paper_id)
                stats["SKIPPED"] += 1
                continue

            # 4. Check if output already exists (Level-2 guard)
            out_path = out_dir / f"{xml_path.stem}.paper.json"
            if not overwrite and out_path.exists():
                reg.mark_skipped(paper_id, reason="output_exists")
                stats["SKIPPED"] += 1
                continue

            # 5. Process
            t0 = time.perf_counter()
            try:
                paper    = build_paper_json(xml_path, encode_fn=None, ner_entities=None)
                rt       = time.perf_counter() - t0
                out_hash = hashlib.md5(
                    str(paper).encode(), usedforsecurity=False
                ).hexdigest()

                import json as _json
                out_path.parent.mkdir(parents=True, exist_ok=True)
                out_path.write_text(_json.dumps(paper, ensure_ascii=False), encoding="utf-8")

                runner.handle_result({
                    "_status":     "SUCCESS",
                    "paper_id":    paper_id,
                    "output_json": str(out_path),
                    "output_hash": out_hash,
                    "runtime_sec": rt,
                }, xml_path)
                stats["SUCCESS"] += 1

            except Exception as exc:
                rt  = time.perf_counter() - t0
                tb  = traceback.format_exc()
                etype = type(exc).__name__
                runner.handle_result({
                    "_status":         "FAIL",
                    "paper_id":        paper_id,
                    "error_type":      etype,
                    "error_message":   str(exc)[:500],
                    "error_traceback": tb[:2000],
                    "runtime_sec":     rt,
                }, xml_path)
                stats["FAIL"] += 1
                log.error("[runner] FAIL %s: %s", paper_id, exc)

        # 6. Export Parquet on completion
        runner.export_parquet()

        # 7. Print summary
        report = reg.build_runtime_report()
        log.info(
            "Done: %d ok / %d fail / %d skip  avg=%.2fs",
            stats["SUCCESS"], stats["FAIL"], stats["SKIPPED"],
            report["runtime"]["avg_sec"],
        )

    finally:
        runner.close()

    return stats
