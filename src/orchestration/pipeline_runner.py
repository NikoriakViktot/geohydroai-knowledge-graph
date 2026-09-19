"""
pipeline_runner.py  —  GeoHydroAI Stage 1 distributed ingestion entry point
============================================================================

Resource-aware, idempotent orchestration: bounded sliding-window concurrency
so RAM stays proportional to MAX_IN_FLIGHT, and papers are never reprocessed
if their output already exists.

Idempotency (two-level skip)
----------------------------
Level 1 — Pre-filter (orchestrator, before Ray task creation):
    Before submitting any Ray task, the runner checks whether
    {out_dir}/{xml_stem}.paper.json exists.  Matching files are counted as
    SKIPPED and never enter the Ray scheduler.  This is the primary path:
    zero actor calls, zero serialisation, zero memory overhead for done papers.

Level 2 — Task-level guard (process_paper.py, before XML parsing):
    If a task is submitted despite the output existing (e.g., two pipeline
    runners hitting the same corpus concurrently), process_paper checks again
    at the very start — before spaCy, embeddings, or OllamaActor — and returns
    a {"_status": "SKIPPED"} sentinel.

Why two levels?
    Level 1 is efficient.  Level 2 is correct under concurrent execution.
    Together they guarantee that restarting an interrupted pipeline never
    reprocesses completed papers.

Memory budget (en_core_web_sm model, 4 in-flight tasks):
    SpacyActor      ~300 MB   (was ~2.4 GB with en_core_web_trf)
    EmbeddingActor  ~600 MB
    4 × task        ~2.0 GB   (~500 MB each)
    ─────────────────────────
    Total           ~2.9 GB   (was 15+ GB with 8 tasks + trf model)

Sliding-window guarantee
------------------------
At most MAX_IN_FLIGHT process_paper tasks are alive at any moment.
When one task finishes, one new task is submitted — keeping the pipeline
saturated without ever queuing thousands of Ray tasks simultaneously.

Usage (local):
    python -m src.orchestration.pipeline_runner

Usage (local, overwrite existing outputs):
    python -m src.orchestration.pipeline_runner --overwrite

Usage (Ray cluster):
    RAY_ADDRESS=ray://<head-node>:10001 python -m src.orchestration.pipeline_runner

Environment overrides:
    XML_DIR              Directory containing .tei.xml files
    OUT_DIR              Output directory for .paper.json files
    RAY_MAX_CONCURRENT   Maximum in-flight tasks (default: 4)
    OLLAMA_MODEL         Ollama model name
    OLLAMA_URL           Ollama base URL
    SPACY_MODEL          spaCy NER model (default: en_core_web_sm)

Architecture:
    Ray Runtime
    ├── SpacyActor      (1 instance, ~300 MB with en_core_web_sm)
    ├── EmbeddingActor  (1 instance, ~600 MB)
    ├── OllamaActor     (1 instance, max_concurrency=2)
    ├── VectorStoreActor (1 instance, ~4.5 GB — the only ChromaDB client)
    └── process_paper   (≤ MAX_IN_FLIGHT tasks active at once)
"""

from __future__ import annotations

import json
import logging
import os
import traceback
from collections import Counter
from pathlib import Path
from typing import Optional

import ray
from tqdm import tqdm

from src.actors.embedding_actor import EmbeddingActor
from src.actors.spacy_actor import SpacyActor
from src.actors.ollama_actor import OllamaActor
from src.actors.vectorstore_actor import VectorStoreActor
from src.config.settings import XML_DIR, OUT_DIR, RAY_MAX_CONCURRENT, SPACY_MODEL
from src.ingestion.pipeline import json_safe
from src.orchestration.process_paper import process_paper

log = logging.getLogger(__name__)

# Sentinel returned by process_paper when it detects an existing output
# at the task level (defense-in-depth).
_SKIPPED_STATUS = "SKIPPED"


# ─────────────────────────────────────────────────────────────────────────────
# Summary printer
# ─────────────────────────────────────────────────────────────────────────────

def pending_xml_files(xml_files, out_dir: Path, overwrite: bool = False) -> list:
    """
    Level-1 ідемпотентність: XML, для яких paper.json ще не існує.

    Винесено в окрему функцію (Фаза 3.3), щоб механізм був юніт-тестований:
    регресія тут означає масову GPU-переекстракцію корпусу.
    """
    if overwrite:
        return list(xml_files)
    return [f for f in xml_files
            if not (out_dir / f"{f.stem}.paper.json").exists()]


def _print_summary(
    stats:    Counter,
    total:    int,
    out_dir:  Path,
) -> None:
    bar = "─" * 56
    print(f"\n{bar}")
    print(f"  STAGE 1 COMPLETE")
    print(bar)
    print(f"  {'Total XML files':<24}: {total}")
    print(f"  {'SUCCESS':<24}: {stats['SUCCESS']}")
    print(f"  {'FAIL':<24}: {stats['FAIL']}")
    print(f"  {'SKIPPED (pre-filter)':<24}: {stats['SKIPPED_PRE']}")
    print(f"  {'SKIPPED (task guard)':<24}: {stats['SKIPPED_TASK']}")
    print(f"  {bar[2:]}")
    print(f"  {'Output dir':<24}: {out_dir}")
    if stats['FAIL']:
        print(f"  ⚠  {stats['FAIL']} paper(s) failed — check logs for details.")
    print(f"{bar}\n")


# ─────────────────────────────────────────────────────────────────────────────
# Public orchestration API
# ─────────────────────────────────────────────────────────────────────────────

def run_distributed(
    xml_dir:        Path = XML_DIR,
    out_dir:        Path = OUT_DIR,
    max_concurrent: int  = RAY_MAX_CONCURRENT,
    ray_address:    Optional[str] = None,
    overwrite:      bool = False,
) -> Counter:
    """
    Orchestrate Stage 1 distributed paper processing.

    Submits process_paper tasks in a sliding window of size `max_concurrent`
    so that:
      - Memory usage is bounded to max_concurrent × per-task footprint.
      - The Ray scheduler is never flooded with thousands of queued tasks.
      - Actors are shared and never recreated per paper.
      - Already-processed papers are skipped without touching Ray at all.

    Args:
        xml_dir:        Directory containing .tei.xml files.
        out_dir:        Destination for .paper.json output files.
        max_concurrent: Maximum simultaneously in-flight Ray tasks.
        ray_address:    Ray cluster address (None = local runtime).
        overwrite:      When False (default), skip papers whose .paper.json
                        already exists.  When True, reprocess everything.

    Returns:
        Counter with keys SUCCESS, FAIL, SKIPPED_PRE, SKIPPED_TASK.
    """
    # ── 1. Init Ray ───────────────────────────────────────────────────────
    if not ray.is_initialized():
        ray.init(address=ray_address, ignore_reinit_error=True)
        log.info("Ray initialised  address=%s", ray_address or "local")
        # Фаза 3.2: гарантоване прибирання акторів при аварійному виході
        # (kill процесу лишав акторів живими з зайнятою GPU/RAM — F-ORCH-6)
        import atexit
        atexit.register(ray.shutdown)

    out_dir.mkdir(parents=True, exist_ok=True)

    # ── 2. Create actors ONCE — shared by all tasks ───────────────────────
    embedding_actor   = EmbeddingActor.remote()
    spacy_actor       = SpacyActor.remote()
    ollama_actor      = OllamaActor.remote()
    vectorstore_actor = VectorStoreActor.remote()

    log.info(
        "Actors ready: EmbeddingActor | SpacyActor (model=%s) | OllamaActor "
        "| VectorStoreActor",
        SPACY_MODEL,
    )

    # ── 3. Discover XML files ─────────────────────────────────────────────
    xml_files   = sorted(xml_dir.glob("*.tei.xml"))
    total_found = len(xml_files)

    log.info("Found %d XML file(s) in %s", total_found, xml_dir)
    if not xml_files:
        log.warning("No .tei.xml files found — nothing to do.")
        return Counter({"SUCCESS": 0, "FAIL": 0, "SKIPPED_PRE": 0, "SKIPPED_TASK": 0})

    # ── 4. Level-1 pre-filter: skip already-done papers ──────────────────
    #
    # This is the primary idempotency mechanism.  We check the output path
    # on the driver (filesystem stat) before creating any Ray task.  No
    # actor call, no serialisation cost, no memory allocation per skipped paper.
    #
    # For 10 000 papers where 9 000 are already done: only 1 000 Ray tasks
    # are created, not 10 000.  The pre-filter runs in O(N) with one stat()
    # call per file — typically < 1 ms per file even on NFS.

    to_process    = pending_xml_files(xml_files, out_dir, overwrite)
    n_skipped_pre = total_found - len(to_process)

    if n_skipped_pre:
        log.info(
            "Pre-filter: %d/%d already processed (SKIPPED), %d to submit",
            n_skipped_pre, total_found, len(to_process),
        )
        tqdm.write(
            f"  Pre-filter : {n_skipped_pre}/{total_found} already done "
            f"(use --overwrite to reprocess)"
        )

    n_to_process = len(to_process)
    if n_to_process == 0:
        log.info("All %d paper(s) already processed — nothing to submit.", total_found)
        _print_summary(
            Counter({"SUCCESS": 0, "FAIL": 0,
                     "SKIPPED_PRE": n_skipped_pre, "SKIPPED_TASK": 0}),
            total_found, out_dir,
        )
        return Counter({"SUCCESS": 0, "FAIL": 0,
                        "SKIPPED_PRE": n_skipped_pre, "SKIPPED_TASK": 0})

    log.info(
        "Sliding window: MAX_IN_FLIGHT=%d  (memory budget ~%.1f GB)",
        max_concurrent,
        0.9 + max_concurrent * 0.5,
    )

    # ── 5. Sliding-window task submission ─────────────────────────────────
    #
    # Invariant: len(pending) <= max_concurrent at all times.
    # One task is submitted → when it finishes, one new task is submitted.
    # This prevents the anti-pattern of queuing all N tasks at once.

    pending:    dict[ray.ObjectRef, Path] = {}
    xml_iter    = iter(to_process)
    stats       = Counter()

    def _submit_next() -> bool:
        """Submit the next XML file as a Ray task. Returns False if exhausted."""
        try:
            xml_path = next(xml_iter)
        except StopIteration:
            return False
        ref = process_paper.remote(
            str(xml_path),
            embedding_actor,
            spacy_actor,
            ollama_actor,
            str(out_dir),   # passed as string — Ray serialises task args
            overwrite,
            vectorstore_actor=vectorstore_actor,
        )
        pending[ref] = xml_path
        log.info("[%d in-flight] submitted: %s", len(pending), xml_path.name)
        return True

    # Fill the initial sliding window
    for _ in range(min(max_concurrent, n_to_process)):
        _submit_next()

    # ── 6. Completion loop with tqdm ──────────────────────────────────────
    with tqdm(
        total=n_to_process,
        desc="Stage 1 ingestion",
        unit="paper",
        dynamic_ncols=True,
    ) as pbar:

        _heartbeat_s = float(os.getenv("RAY_WAIT_HEARTBEAT_S", "600"))
        while pending:
            # Чекаємо одну задачу, але з heartbeat (Фаза 3.2): якщо за
            # _heartbeat_s ніщо не завершилось — логуємо стан замість
            # мовчазного вічного блокування (F-ORCH-2).
            finished, _ = ray.wait(
                list(pending.keys()), num_returns=1, timeout=_heartbeat_s)
            if not finished:
                log.warning(
                    "[heartbeat] жодна задача не завершилась за %.0fs — "
                    "in-flight=%d (%s). Перевірте акторів (ray status).",
                    _heartbeat_s, len(pending),
                    ", ".join(p.name for p in list(pending.values())[:3]),
                )
                continue
            ref      = finished[0]
            xml_path = pending.pop(ref)

            try:
                result = ray.get(ref)

                # ── Level-2 sentinel: task-level skip (defense-in-depth) ──
                if isinstance(result, dict) and result.get("_status") == _SKIPPED_STATUS:
                    stats["SKIPPED_TASK"] += 1
                    paper_id = result.get("paper_id", xml_path.stem)
                    tqdm.write(f"⏩ [SKIPPED ]  {paper_id}")
                    log.info("[SKIPPED_TASK] %s", xml_path.name)

                else:
                    # ── Normal success: write output ──────────────────────
                    out_path = out_dir / f"{xml_path.stem}.paper.json"
                    with open(out_path, "w", encoding="utf-8") as fh:
                        json.dump(json_safe(result), fh, indent=2, ensure_ascii=False)
                    stats["SUCCESS"] += 1

                    # ── Judge-телеметрія (Фаза 2.5) ───────────────────────
                    prov = (result.get("provenance") or {}) if isinstance(result, dict) else {}
                    jst  = prov.get("judge_status")
                    if jst is not None:
                        key = {"success": "JUDGE_OK"}.get(jst)
                        stats[key or ("JUDGE_" + str(jst).upper())] = \
                            stats.get(key or ("JUDGE_" + str(jst).upper()), 0) + 1
                    stats["JUDGE_REPAIRS"] = (
                        stats.get("JUDGE_REPAIRS", 0)
                        + int(prov.get("judge_verdict_repairs", 0))
                    )
                    tqdm.write(f"✅ [SUCCESS ] {out_path.name}")
                    log.info(
                        "[%d in-flight | %d done] written: %s",
                        len(pending),
                        stats["SUCCESS"] + stats["FAIL"],
                        out_path.name,
                    )

            except Exception as exc:
                stats["FAIL"] += 1
                tqdm.write(
                    f"❌ [FAIL    ] {xml_path.name}  "
                    f"({type(exc).__name__}: {exc})"
                )
                log.error(
                    "[%d in-flight] FAILED: %s — %s: %s",
                    len(pending), xml_path.name, type(exc).__name__, exc,
                )
                traceback.print_exc()

            pbar.update(1)
            pbar.set_postfix(
                ok=stats["SUCCESS"],
                fail=stats["FAIL"],
                skip=stats["SKIPPED_TASK"],
                refresh=False,
            )

            # Keep the sliding window full
            _submit_next()

    # ── 7. Final stats ────────────────────────────────────────────────────
    stats["SKIPPED_PRE"] = n_skipped_pre

    total_skipped = stats["SKIPPED_PRE"] + stats["SKIPPED_TASK"]
    log.info(
        "Stage 1 complete — SUCCESS=%d | FAIL=%d | SKIPPED=%d "
        "(pre=%d task=%d) | total=%d → %s",
        stats["SUCCESS"], stats["FAIL"], total_skipped,
        stats["SKIPPED_PRE"], stats["SKIPPED_TASK"],
        total_found, out_dir,
    )

    # Judge-телеметрія (Фаза 2.5): здоров'я LLM-судді по прогону
    judge_keys = sorted(k for k in stats if k.startswith("JUDGE_"))
    if judge_keys:
        log.info(
            "Judge telemetry — %s",
            " | ".join(f"{k}={stats[k]}" for k in judge_keys),
        )
        n_ok = stats.get("JUDGE_OK", 0) + stats.get("JUDGE_SUCCESS", 0)
        if stats["SUCCESS"] and n_ok and stats.get("JUDGE_REPAIRS", 0) / max(n_ok, 1) > 2.0:
            log.warning(
                "Judge verdicts потребують у середньому >2 ремонтів — "
                "ймовірна деградація LLM-моделі (перевірте OLLAMA_MODEL)."
            )

    _print_summary(stats, total_found, out_dir)
    return stats


# ─────────────────────────────────────────────────────────────────────────────
# CLI entry point
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    import os

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    parser = argparse.ArgumentParser(
        description="GeoHydroAI Stage 1 — distributed TEI XML → paper.json ingestion",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--xml-dir", type=Path, default=XML_DIR,
        help="Directory containing .tei.xml files",
    )
    parser.add_argument(
        "--out-dir", type=Path, default=OUT_DIR,
        help="Output directory for .paper.json files",
    )
    parser.add_argument(
        "--workers", type=int, default=RAY_MAX_CONCURRENT,
        help="Maximum in-flight Ray tasks (memory budget ≈ workers × 500 MB)",
    )
    parser.add_argument(
        "--overwrite", action="store_true", default=False,
        help="Reprocess all papers even if .paper.json already exists",
    )
    parser.add_argument(
        "--ray-address", type=str, default=None,
        help="Ray cluster address (omit for local runtime)",
    )
    args = parser.parse_args()

    stats = run_distributed(
        xml_dir=args.xml_dir,
        out_dir=args.out_dir,
        max_concurrent=args.workers,
        ray_address=args.ray_address or os.getenv("RAY_ADDRESS"),
        overwrite=args.overwrite,
    )

    raise SystemExit(1 if stats["FAIL"] > 0 else 0)
