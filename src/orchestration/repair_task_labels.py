"""
repair_task_labels.py — Retroactive task label propagation fix for GeoHydroAI.

The judge_stage.py::apply_judge_verdict() bug caused ~2,660 paper.json files
to have a stale paper["task"]["label"] (pre-judge value) even though
paper["entities"]["task"]["label"] was correctly updated by the LLM judge.

This script aligns the two fields using a surgical, provenance-preserving update:
  - paper["task"]["label"]          ← corrected label from entities["task"]
  - paper["task"]["original_label"] ← what it was before (audit trail)
  - paper["task"]["source"]         ← "llm_judge_sync"
  - paper["task"]["repaired"]       ← True

Papers where the two labels already agree are silently skipped (idempotent).
Never modifies paper.json files in dry-run mode.
"""
from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Retroactively sync paper['task']['label'] from entities['task']."
    )
    ap.add_argument(
        "--input-dir",
        default="data/literature/paper_json",
        type=Path,
        help="Directory of *.tei.paper.json files (default: data/literature/paper_json)",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="Report changes without writing any files",
    )
    ap.add_argument("--log-level", default="INFO")
    args = ap.parse_args()

    logging.basicConfig(
        level=args.log_level,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    files = sorted(args.input_dir.glob("*.tei.paper.json"))
    log.info("Scanning %d paper.json files  (dry_run=%s)", len(files), args.dry_run)

    updated = skipped = errors = 0

    for fp in files:
        try:
            paper    = json.loads(fp.read_text(encoding="utf-8"))
            ent_task = (paper.get("entities") or {}).get("task") or {}
            top_task = paper.get("task") or {}

            ent_label = ent_task.get("label")
            top_label = top_task.get("label")

            # Skip if entities["task"] has no label, or labels already agree
            if not ent_label or ent_label == top_label:
                skipped += 1
                continue

            # Surgical update — preserve existing task metadata, only patch label
            paper.setdefault("task", {})
            paper["task"]["original_label"] = top_label
            paper["task"]["label"]          = ent_label
            paper["task"]["source"]         = "llm_judge_sync"
            paper["task"]["repaired"]       = True
            paper.setdefault("provenance", {})["task_label_repaired"] = True

            if not args.dry_run:
                fp.write_text(json.dumps(paper, ensure_ascii=False), encoding="utf-8")

            log.info(
                "[fixed] %-40s  %s → %s",
                fp.stem[:40], top_label, ent_label,
            )
            updated += 1

        except Exception as exc:
            log.error("[error] %s  %s", fp.name, exc)
            errors += 1

    log.info(
        "DONE  updated=%d  skipped=%d  errors=%d  (dry_run=%s)",
        updated, skipped, errors, args.dry_run,
    )


if __name__ == "__main__":
    main()
