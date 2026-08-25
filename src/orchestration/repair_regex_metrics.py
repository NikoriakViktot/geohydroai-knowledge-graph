"""
repair_regex_metrics.py — In-place patch: add top-level name/canonical_id/confidence/unit/context
to entities.metrics entries in existing paper.json files.

This is a SAFE, value-preserving repair:
  - Never changes "value", "type", "evidence", or "kb_metadata"
  - Re-resolves metric type → KB to populate name/canonical_id (same logic as entity_extractor)
  - Skips entries that already have a non-empty name
  - Idempotent: running twice produces no further changes

Run this after deploying the entity_extractor fix to backfill the ~4850 existing paper.json files.
"""
from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)

_PAPER_JSON_DIR = Path("data/literature/paper_json")
_ENRICHED_DIR   = Path("data/enriched")

_NORMALIZED_UNITS = {
    "OA", "F1", "IoU", "Kappa", "Precision", "Recall", "R", "R2", "NSE", "KGE", "AUC"
}

_CONTEXT_KEYWORDS = ["validation", "test", "calibration", "training"]


def _detect_context(snippet: str) -> str | None:
    s = snippet.lower()
    return next((k for k in _CONTEXT_KEYWORDS if k in s), None)


def _patch_paper(paper: dict, kb) -> int:
    """Patch entities.metrics in-place. Returns number of entries updated."""
    metrics = (paper.get("entities") or {}).get("metrics")
    if not metrics:
        return 0
    updated = 0
    for m in metrics:
        if m.get("name"):
            continue
        metric_type = m.get("type", "")
        if not metric_type:
            continue
        mrec = kb.resolve_metric(metric_type)
        kb_meta = m.get("kb_metadata", {})
        name = (mrec.full_name if mrec else None) or kb_meta.get("full_name") or metric_type
        canonical = (mrec.id if mrec else None) or kb_meta.get("canonical_id", "")
        m["name"] = name
        m["canonical_id"] = canonical
        m["confidence"] = 0.7 if canonical else 0.5
        m["unit"] = "-" if metric_type in _NORMALIZED_UNITS else m.get("unit")
        if m.get("context") is None:
            snippet = (m.get("evidence") or {}).get("snippet", "")
            m["context"] = _detect_context(snippet)
        updated += 1
    return updated


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Backfill name/canonical_id/confidence/unit/context in entities.metrics."
    )
    ap.add_argument(
        "--input-dir", default=str(_PAPER_JSON_DIR), type=Path,
        help="Directory of *.json paper files (default: data/literature/paper_json)",
    )
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    from src.ingestion.knowledge.knowledge_loader import load_knowledge_base
    kb = load_knowledge_base()

    # Collect (path, is_enriched_wrapper) for both source dirs
    file_queue: list[tuple[Path, bool]] = []
    file_queue += [(p, False) for p in sorted(args.input_dir.glob("*.json"))]
    for p in sorted(_ENRICHED_DIR.glob("*.json")):
        file_queue.append((p, True))

    log.info("Scanning %d files (%d paper_json + %d enriched)",
             len(file_queue),
             len([x for x in file_queue if not x[1]]),
             len([x for x in file_queue if x[1]]))

    total_files = total_entries = 0
    for path, is_enriched in file_queue:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            log.warning("Skip %s: %s", path.name, exc)
            continue

        # Enriched files wrap the paper under a "paper" key
        paper = raw.get("paper", raw) if is_enriched else raw

        n = _patch_paper(paper, kb)
        if n == 0:
            continue
        total_files += 1
        total_entries += n
        if not args.dry_run:
            path.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")

    action = "Would update" if args.dry_run else "Updated"
    log.info("%s %d entries across %d files", action, total_entries, total_files)


if __name__ == "__main__":
    main()
