"""
normalization_summary.py  —  GeoHydroAI normalization quality report
=====================================================================

Reads data/normalized/*.json and produces a corpus-wide summary of how well
the ontology normalization pipeline is performing.

Output: data/ontology_qa/normalization_summary.json

Coverage definitions
---------------------
    total_normalized_entities  = all entries in normalized_entities lists
    coverage_percent           = entries with canonical_id / total
    unknown_percent            = match_type=="unknown" / total
    alias_hit_percent          = match_type=="alias"   / total
    exact_hit_percent          = match_type=="exact"   / total
    semantic_fallback_percent  = match_type=="semantic"/ total
    error_percent              = match_type=="error"   / total

QA warnings are raised when:
    - unknown_percent > 20%
    - semantic_fallback_percent > 30%
    - any entity type coverage < 70%
    - canonical_id missing but match_type not unknown/error
    - confidence outside [0, 1]
    - duplicate raw mentions map to conflicting canonical IDs

Usage:
    python -m src.ontology.refinement.normalization_summary
    python -m src.ontology.refinement.normalization_summary --dir data/normalized
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

_NORMALIZED_DIR = Path(__file__).resolve().parents[3] / "data" / "normalized"
_QA_DIR         = Path(__file__).resolve().parents[3] / "data" / "ontology_qa"

_UNKNOWN_THRESHOLD   = 20.0   # % — warn if unknown rate exceeds this
_SEMANTIC_THRESHOLD  = 30.0   # % — warn if semantic fallback rate exceeds this
_COVERAGE_FLOOR      = 70.0   # % — warn if any type drops below this


# ─────────────────────────────────────────────────────────────────────────────
# Data collection
# ─────────────────────────────────────────────────────────────────────────────

def _collect_normalized_entities(normalized_dir: Path) -> list[dict]:
    """
    Walk all normalized paper JSONs and collect every NormalizedEntity record.

    Returns a flat list; each record carries 'source_field' and 'paper_id'
    so we can group/filter downstream.
    """
    records: list[dict] = []
    files = list(normalized_dir.glob("*.json"))
    log.info("Reading %d normalized papers from %s", len(files), normalized_dir)

    for path in files:
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            log.debug("Skipping %s: %s", path.name, exc)
            continue

        paper_id = doc.get("metadata", {}).get("paper_id", path.stem)
        norm_ents = doc.get("normalized_entities", {})

        if not isinstance(norm_ents, dict):
            continue

        for field_name, ent_list in norm_ents.items():
            if not isinstance(ent_list, list):
                continue
            for ent in ent_list:
                if not isinstance(ent, dict):
                    continue
                record = dict(ent)
                record.setdefault("source_field", field_name)
                record["_paper_id"] = paper_id
                records.append(record)

    return records


# ─────────────────────────────────────────────────────────────────────────────
# QA warning detection
# ─────────────────────────────────────────────────────────────────────────────

def _check_qa_warnings(records: list[dict], by_type: dict[str, dict]) -> list[str]:
    warnings: list[str] = []

    total = len(records)
    if total == 0:
        return warnings

    unknown_pct  = sum(1 for r in records if r.get("match_type") == "unknown") / total * 100
    semantic_pct = sum(1 for r in records if r.get("match_type") == "semantic") / total * 100

    if unknown_pct > _UNKNOWN_THRESHOLD:
        warnings.append(
            f"unknown_percent={unknown_pct:.1f}% exceeds threshold of {_UNKNOWN_THRESHOLD}%"
        )
    if semantic_pct > _SEMANTIC_THRESHOLD:
        warnings.append(
            f"semantic_fallback_percent={semantic_pct:.1f}% exceeds threshold "
            f"of {_SEMANTIC_THRESHOLD}%"
        )

    # Per-type coverage floor
    for etype, stats in by_type.items():
        cov = stats.get("coverage_percent", 100.0)
        if cov < _COVERAGE_FLOOR:
            warnings.append(
                f"type='{etype}' coverage={cov:.1f}% below floor of {_COVERAGE_FLOOR}%"
            )

    # canonical_id missing but match_type is not unknown/error
    bad_id = [
        r for r in records
        if r.get("canonical_id") is None
        and r.get("match_type") not in ("unknown", "error", None)
    ]
    if bad_id:
        warnings.append(
            f"{len(bad_id)} entities have canonical_id=null but "
            f"match_type not in {{unknown, error}}"
        )

    # Confidence out of range
    bad_conf = [
        r for r in records
        if not (0.0 <= (r.get("confidence") or 0.0) <= 1.0)
    ]
    if bad_conf:
        warnings.append(f"{len(bad_conf)} entities have confidence outside [0, 1]")

    # Conflicting canonical IDs for the same raw mention
    mention_to_ids: dict[str, set[str]] = defaultdict(set)
    for r in records:
        raw  = (r.get("raw_name") or "").strip().lower()
        cid  = r.get("canonical_id")
        if raw and cid:
            mention_to_ids[raw].add(cid)
    conflicts = {m: ids for m, ids in mention_to_ids.items() if len(ids) > 1}
    if conflicts:
        warnings.append(
            f"{len(conflicts)} raw mentions map to conflicting canonical IDs: "
            + ", ".join(f"'{m}'→{ids}" for m, ids in list(conflicts.items())[:3])
        )

    return warnings


# ─────────────────────────────────────────────────────────────────────────────
# Core aggregation
# ─────────────────────────────────────────────────────────────────────────────

def _pct(numerator: int, denominator: int) -> float:
    return round(numerator / denominator * 100, 2) if denominator else 0.0


def _aggregate(records: list[dict], paper_ids: list[str]) -> dict:
    total = len(records)

    match_counts: Counter = Counter(r.get("match_type", "unknown") for r in records)
    has_id = sum(1 for r in records if r.get("canonical_id") is not None)

    # By entity type (derived from 'type' field or 'source_field')
    # Group by 'type' as reported in the normalization result
    type_records: dict[str, list[dict]] = defaultdict(list)
    for r in records:
        t = r.get("type") or _source_field_to_type(r.get("source_field", ""))
        type_records[t].append(r)

    by_type: dict[str, dict] = {}
    for etype, group in type_records.items():
        g_total  = len(group)
        g_has_id = sum(1 for r in group if r.get("canonical_id") is not None)
        g_counts = Counter(r.get("match_type", "unknown") for r in group)
        by_type[etype] = {
            "total":             g_total,
            "with_canonical_id": g_has_id,
            "coverage_percent":  _pct(g_has_id, g_total),
            "alias_count":       g_counts["alias"],
            "exact_count":       g_counts["exact"],
            "semantic_count":    g_counts["semantic"],
            "unknown_count":     g_counts["unknown"],
            "error_count":       g_counts.get("error", 0),
        }

    # Top unresolved
    unresolved_counts: Counter = Counter(
        (r.get("raw_name") or "").strip().lower()
        for r in records
        if r.get("match_type") == "unknown"
    )
    top_unresolved = [
        {"mention": mention, "count": count}
        for mention, count in unresolved_counts.most_common(30)
        if mention
    ]

    # Top semantic matches (uncertain but accepted 0.68–0.82 already filtered by matcher)
    semantic_records = [r for r in records if r.get("match_type") == "semantic"]
    sem_canonical_counts: Counter = Counter(
        r.get("canonical_id") for r in semantic_records if r.get("canonical_id")
    )
    top_semantic = [
        {"canonical_id": cid, "count": count}
        for cid, count in sem_canonical_counts.most_common(15)
    ]

    return {
        "total":            total,
        "has_id":           has_id,
        "match_counts":     dict(match_counts),
        "by_type":          by_type,
        "top_unresolved":   top_unresolved,
        "top_semantic":     top_semantic,
    }


def _source_field_to_type(source_field: str) -> str:
    _MAP = {
        "methods":       "method",
        "satellites":    "sensor",
        "sensors":       "sensor",
        "metrics":       "metric",
        "datasets":      "data",
        "data_sources":  "data",
        "indices":       "data",
        "models":        "method",
        "algorithms":    "method",
    }
    return _MAP.get(source_field, source_field or "unknown")


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def generate_summary(normalized_dir: Optional[Path] = None) -> dict:
    """
    Generate the normalization quality summary report.

    Args:
        normalized_dir: Directory of normalized paper JSONs.
                        Defaults to data/normalized/.

    Returns:
        Summary dict (also written to data/ontology_qa/normalization_summary.json).
    """
    if normalized_dir is None:
        normalized_dir = _NORMALIZED_DIR

    files    = list(normalized_dir.glob("*.json"))
    paper_ids = [p.stem for p in files]
    records   = _collect_normalized_entities(normalized_dir)
    agg       = _aggregate(records, paper_ids)

    total = agg["total"]
    alias_count    = agg["match_counts"].get("alias",    0)
    exact_count    = agg["match_counts"].get("exact",    0)
    semantic_count = agg["match_counts"].get("semantic", 0)
    unknown_count  = agg["match_counts"].get("unknown",  0)
    error_count    = agg["match_counts"].get("error",    0)
    has_id         = agg["has_id"]

    # Compute by_entity_type with canonical type keys
    canonical_types = {"method", "sensor", "metric", "data"}
    by_entity_type: dict[str, dict] = {}
    other_stats: dict[str, int] = {"total": 0, "with_canonical_id": 0}

    for etype, stats in agg["by_type"].items():
        if etype in canonical_types:
            by_entity_type[etype] = stats
        else:
            for k in ("total", "with_canonical_id"):
                other_stats[k] = other_stats.get(k, 0) + stats.get(k, 0)

    # Fill missing canonical types with zeroes
    for t in canonical_types:
        by_entity_type.setdefault(t, {
            "total": 0, "with_canonical_id": 0, "coverage_percent": 0.0,
            "alias_count": 0, "exact_count": 0, "semantic_count": 0,
            "unknown_count": 0, "error_count": 0,
        })

    warnings = _check_qa_warnings(records, by_entity_type)

    summary = {
        "generated_at":              datetime.now(timezone.utc).isoformat(),
        "total_papers":              len(files),
        "total_raw_entities":        total,
        "total_normalized_entities": total,
        "coverage_percent":          _pct(has_id, total),
        "unknown_percent":           _pct(unknown_count, total),
        "alias_hit_percent":         _pct(alias_count, total),
        "exact_hit_percent":         _pct(exact_count, total),
        "semantic_fallback_percent": _pct(semantic_count, total),
        "error_percent":             _pct(error_count, total),
        "top_unresolved_entities":   agg["top_unresolved"],
        "top_semantic_matches":      agg["top_semantic"],
        "by_entity_type":            by_entity_type,
        "match_type_counts":         agg["match_counts"],
        "qa_warnings":               warnings,
        "qa_passed":                 len(warnings) == 0,
    }

    _QA_DIR.mkdir(parents=True, exist_ok=True)
    out = _QA_DIR / "normalization_summary.json"
    out.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    log.info(
        "Summary: %d papers, %d entities, coverage=%.1f%%, unknown=%.1f%%",
        len(files), total, summary["coverage_percent"], summary["unknown_percent"],
    )
    return summary


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def _main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    parser = argparse.ArgumentParser(description="GeoHydroAI normalization summary")
    parser.add_argument(
        "--dir", type=Path, default=None,
        help="Path to normalized paper directory (default: data/normalized/)",
    )
    args = parser.parse_args()

    norm_dir = args.dir or _NORMALIZED_DIR

    if not norm_dir.exists():
        print(f"Directory not found: {norm_dir}")
        print("No normalized papers to summarize yet.")
        return

    summary = generate_summary(norm_dir)

    print(f"\nNormalization Summary — {summary['total_papers']} papers")
    print(f"  Total entities          : {summary['total_normalized_entities']}")
    print(f"  Coverage (has canon. ID): {summary['coverage_percent']}%")
    print(f"  Alias hits              : {summary['alias_hit_percent']}%")
    print(f"  Exact hits              : {summary['exact_hit_percent']}%")
    print(f"  Semantic fallback       : {summary['semantic_fallback_percent']}%")
    print(f"  Unknown                 : {summary['unknown_percent']}%")
    print(f"  Errors                  : {summary['error_percent']}%")

    print(f"\n  By entity type:")
    for etype in ("method", "sensor", "metric", "data"):
        st = summary["by_entity_type"].get(etype, {})
        print(
            f"    {etype:8s}: total={st.get('total',0):4d}  "
            f"coverage={st.get('coverage_percent',0):.1f}%  "
            f"unknown={st.get('unknown_count',0)}"
        )

    if summary["qa_warnings"]:
        print(f"\n  QA WARNINGS ({len(summary['qa_warnings'])}):")
        for w in summary["qa_warnings"]:
            print(f"    ! {w}")
    else:
        print(f"\n  QA: all checks passed")

    if summary["top_unresolved_entities"]:
        print(f"\n  Top unresolved:")
        for e in summary["top_unresolved_entities"][:10]:
            print(f"    {e['count']:4d}x  {e['mention']}")

    print(f"\nReport: data/ontology_qa/normalization_summary.json")


if __name__ == "__main__":
    _main()
