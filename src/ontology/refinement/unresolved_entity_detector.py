"""
unresolved_entity_detector.py  —  GeoHydroAI normalization gap reporter
=======================================================================

Walks the enriched paper corpus and identifies entity mentions that could
NOT be resolved by the alias + semantic matching pipeline.

Output files (data/ontology_qa/):
    unresolved_entities.json       — all unresolved mentions with paper context
    unresolved_by_type.json        — grouped by entity field / expected type

Rules:
    - This module READS existing enriched paper JSONs.  It does NOT modify them.
    - It does NOT write to the ontology registry or alias table.
    - Unresolved terms are for HUMAN REVIEW — not auto-import.

Usage:
    python -m src.ontology.refinement.unresolved_entity_detector
    python -m src.ontology.refinement.unresolved_entity_detector --papers-dir /path/to/enriched
"""

from __future__ import annotations

import argparse
import json
import logging
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

_ENRICHED_DIR = Path(__file__).resolve().parents[3] / "data" / "enriched"
_QA_DIR       = Path(__file__).resolve().parents[3] / "data" / "ontology_qa"

# Entity fields in paper JSON and their expected ontology type
_ENTITY_FIELD_TYPES: dict[str, str] = {
    "methods":          "method",
    "sensors":          "sensor",
    "satellites":       "sensor",
    "metrics":          "metric",
    "datasets":         "data",
    "data_sources":     "data",
    "study_area":       "concept",
    "flood_type":       "concept",
    "models":           "method",
    "algorithms":       "method",
    "remote_sensing":   "method",
    "indices":          "data",
}


# ─────────────────────────────────────────────────────────────────────────────
# Collection
# ─────────────────────────────────────────────────────────────────────────────

def _normalize(raw: str, expected_type: Optional[str]) -> dict:
    """Run full normalization pipeline and return result dict."""
    try:
        from src.normalization.ontology_matcher import normalize_entity
        return normalize_entity(raw, expected_type=expected_type)
    except Exception as exc:
        log.debug("normalize_entity failed for %r: %s", raw, exc)
        return {
            "raw_name":     raw,
            "canonical_id": None,
            "match_type":   "error",
            "confidence":   0.0,
        }


def collect_unresolved(papers_dir: Path) -> list[dict]:
    """
    Walk paper JSONs and collect every entity mention that failed normalization.

    Returns:
        List of records:
            {raw_mention, field, expected_type, paper_id, match_type, confidence}
    """
    unresolved: list[dict] = []
    paper_files = list(papers_dir.glob("*.json"))
    log.info("Scanning %d paper files in %s", len(paper_files), papers_dir)

    for path in paper_files:
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            log.debug("Skipping %s: %s", path.name, exc)
            continue

        paper    = doc.get("paper", doc)
        paper_id = paper.get("doi") or paper.get("id") or path.stem
        entities = paper.get("entities", {})

        for field_name, values in entities.items():
            expected_type = _ENTITY_FIELD_TYPES.get(field_name)
            if not isinstance(values, list):
                values = [values] if values else []

            for raw in values:
                if not isinstance(raw, str) or not raw.strip():
                    continue
                result = _normalize(raw.strip(), expected_type)
                if result["match_type"] in ("unknown", "error"):
                    unresolved.append({
                        "raw_mention":   raw,
                        "field":         field_name,
                        "expected_type": expected_type,
                        "paper_id":      paper_id,
                        "match_type":    result["match_type"],
                        "confidence":    result.get("confidence", 0.0),
                    })

    log.info("Found %d unresolved mentions across %d papers", len(unresolved), len(paper_files))
    return unresolved


# ─────────────────────────────────────────────────────────────────────────────
# Aggregation
# ─────────────────────────────────────────────────────────────────────────────

def _aggregate(records: list[dict]) -> dict:
    """Group and count unresolved mentions."""
    by_mention: Counter = Counter(r["raw_mention"].lower() for r in records)
    by_type:    Counter = Counter(r.get("expected_type", "unknown") for r in records)
    by_field:   Counter = Counter(r["field"] for r in records)

    # Group records by expected_type for the per-type output
    by_type_groups: dict[str, list[dict]] = defaultdict(list)
    seen: dict[str, set] = defaultdict(set)
    for r in records:
        t   = r.get("expected_type") or "unknown"
        key = r["raw_mention"].lower()
        if key not in seen[t]:
            seen[t].add(key)
            by_type_groups[t].append({
                "mention":    r["raw_mention"],
                "seen_count": by_mention[key],
                "fields":     [r["field"]],
                "example_paper": r["paper_id"],
            })
        else:
            # Update seen_count and fields for existing entry
            for entry in by_type_groups[t]:
                if entry["mention"].lower() == key:
                    if r["field"] not in entry["fields"]:
                        entry["fields"].append(r["field"])
                    break

    # Sort each group by frequency
    for t in by_type_groups:
        by_type_groups[t].sort(key=lambda x: -x["seen_count"])

    return {
        "by_mention_frequency": by_mention.most_common(100),
        "by_expected_type":     dict(by_type.most_common()),
        "by_field":             dict(by_field.most_common()),
        "by_type_groups":       dict(by_type_groups),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def detect_unresolved(papers_dir: Optional[Path] = None) -> dict:
    """
    Run unresolved entity detection and write QA files.

    Returns:
        Summary dict with counts and top unresolved terms.
    """
    if papers_dir is None:
        papers_dir = _ENRICHED_DIR

    records    = collect_unresolved(papers_dir)
    aggregated = _aggregate(records)

    _QA_DIR.mkdir(parents=True, exist_ok=True)

    # Full record list
    full_out = _QA_DIR / "unresolved_entities.json"
    full_out.write_text(
        json.dumps({
            "generated_at":   datetime.now(timezone.utc).isoformat(),
            "total_unresolved": len(records),
            "note": "For HUMAN REVIEW only. Do not auto-import.",
            "records": records,
        }, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    # Grouped by type
    by_type_out = _QA_DIR / "unresolved_by_type.json"
    by_type_out.write_text(
        json.dumps({
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "summary":      aggregated["by_expected_type"],
            "by_type":      aggregated["by_type_groups"],
        }, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    log.info("Unresolved: %d total, files written to %s", len(records), _QA_DIR)
    return {
        "total_unresolved":     len(records),
        "by_expected_type":     aggregated["by_expected_type"],
        "top_unresolved":       aggregated["by_mention_frequency"][:20],
    }


def _main() -> None:
    import logging as _logging
    _logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    parser = argparse.ArgumentParser(description="GeoHydroAI unresolved entity detector")
    parser.add_argument(
        "--papers-dir", type=Path, default=None,
        help="Path to enriched paper JSONs (default: data/enriched/)",
    )
    args = parser.parse_args()

    papers_dir = args.papers_dir or _ENRICHED_DIR
    if not papers_dir.exists():
        print(f"Papers directory not found: {papers_dir}")
        print("Nothing to scan — no enriched papers yet.")
        return

    summary = detect_unresolved(papers_dir)

    print(f"\nUnresolved Entity Detection")
    print(f"  Total unresolved: {summary['total_unresolved']}")
    print(f"\n  By expected type:")
    for t, c in summary["by_expected_type"].items():
        print(f"    {t:20s}: {c}")
    print(f"\n  Top 20 unresolved mentions:")
    for mention, count in summary["top_unresolved"]:
        print(f"    {count:4d}x  {mention}")
    print(f"\nFull report: data/ontology_qa/unresolved_entities.json")
    print(f"By-type report: data/ontology_qa/unresolved_by_type.json")


if __name__ == "__main__":
    _main()
