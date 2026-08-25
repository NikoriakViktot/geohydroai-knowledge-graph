"""
ontology_statistics.py  —  GeoHydroAI ontology coverage statistics
===================================================================

Computes and writes data/ontology_qa/ontology_statistics.json with:

    - Entity counts by type and type_group
    - Alias coverage (min/max/mean aliases per entity)
    - Definition coverage (% of entities with non-empty definition)
    - Parent/child hierarchy depth distribution
    - Generic entity summary
    - Sources distribution

Usage:
    from src.ontology.refinement.ontology_statistics import compute_statistics

    stats = compute_statistics(registry)
    # or run as script:
    python -m src.ontology.refinement.ontology_statistics
"""

from __future__ import annotations

import json
import logging
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

_ONTOLOGY_DIR = Path(__file__).resolve().parents[3] / "data" / "ontology"
_QA_DIR       = Path(__file__).resolve().parents[3] / "data" / "ontology_qa"


def compute_statistics(registry: dict[str, dict]) -> dict:
    """
    Compute comprehensive statistics over the ontology registry.

    Args:
        registry: Mapping canonical_id → entity dict.

    Returns:
        Statistics dict (also written to data/ontology_qa/ontology_statistics.json).
    """
    entities = list(registry.values())
    n = len(entities)

    # ── Counts by type ──────────────────────────────────────────────────────
    by_type: Counter = Counter(e.get("type", "unknown") for e in entities)

    # ── Counts by type_group ────────────────────────────────────────────────
    by_type_group: Counter = Counter(
        e.get("type_group", "unassigned") for e in entities
    )

    # ── Alias statistics ────────────────────────────────────────────────────
    alias_counts = [len(e.get("aliases", [])) for e in entities]
    zero_alias   = sum(1 for c in alias_counts if c == 0)
    alias_stats  = {
        "min":  min(alias_counts) if alias_counts else 0,
        "max":  max(alias_counts) if alias_counts else 0,
        "mean": round(statistics.mean(alias_counts), 2) if alias_counts else 0,
        "median": statistics.median(alias_counts) if alias_counts else 0,
        "entities_with_zero_aliases": zero_alias,
        "total_alias_strings": sum(alias_counts),
    }

    # ── Definition coverage ─────────────────────────────────────────────────
    has_definition = sum(
        1 for e in entities
        if e.get("definition") and e["definition"].strip()
    )
    definition_coverage = {
        "entities_with_definition": has_definition,
        "entities_without_definition": n - has_definition,
        "coverage_pct": round(has_definition / n * 100, 1) if n else 0,
    }

    # ── Hierarchy depth ─────────────────────────────────────────────────────
    parent_map: dict[str, Optional[str]] = {
        e["id"]: e.get("parent_id") for e in entities if "id" in e
    }
    children_map: dict[str, list[str]] = defaultdict(list)
    for eid, parent in parent_map.items():
        if parent:
            children_map[parent].append(eid)

    def _depth(eid: str, visited: set) -> int:
        if eid in visited:
            return 0
        visited.add(eid)
        parent = parent_map.get(eid)
        if not parent:
            return 0
        return 1 + _depth(parent, visited)

    depths = [_depth(eid, set()) for eid in parent_map]
    hierarchy_stats = {
        "root_entities":    sum(1 for d in depths if d == 0),
        "child_entities":   sum(1 for d in depths if d > 0),
        "max_depth":        max(depths) if depths else 0,
        "entities_with_children": len(children_map),
    }

    # ── Generic entities ────────────────────────────────────────────────────
    generic = [e["id"] for e in entities if e.get("is_generic")]

    # ── Sources distribution ─────────────────────────────────────────────────
    sources_counter: Counter = Counter()
    for e in entities:
        for src in e.get("sources", []):
            sources_counter[src] += 1

    # ── type_group coverage per type ────────────────────────────────────────
    type_group_by_type: dict[str, dict] = defaultdict(lambda: {"total": 0, "with_type_group": 0})
    for e in entities:
        t = e.get("type", "unknown")
        type_group_by_type[t]["total"] += 1
        if e.get("type_group"):
            type_group_by_type[t]["with_type_group"] += 1
    for t, d in type_group_by_type.items():
        d["coverage_pct"] = round(d["with_type_group"] / d["total"] * 100, 1) if d["total"] else 0

    output = {
        "generated_at":         datetime.now(timezone.utc).isoformat(),
        "total_entities":       n,
        "by_type":              dict(by_type.most_common()),
        "by_type_group":        dict(by_type_group.most_common()),
        "alias_statistics":     alias_stats,
        "definition_coverage":  definition_coverage,
        "hierarchy_statistics": hierarchy_stats,
        "generic_entities":     {"count": len(generic), "ids": generic},
        "sources_distribution": dict(sources_counter.most_common()),
        "type_group_coverage":  dict(type_group_by_type),
    }

    _QA_DIR.mkdir(parents=True, exist_ok=True)
    out = _QA_DIR / "ontology_statistics.json"
    out.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")
    log.info("Statistics: %d entities, %d types, %d type_groups",
             n, len(by_type), len(by_type_group))
    return output


def _main() -> None:
    import logging as _logging
    _logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    reg_path = _ONTOLOGY_DIR / "ontology_registry.json"
    if not reg_path.exists():
        print(f"ERROR: registry not found at {reg_path}")
        raise SystemExit(1)

    registry = json.loads(reg_path.read_text(encoding="utf-8"))
    stats    = compute_statistics(registry)

    print(f"\nOntology Statistics")
    print(f"  Total entities : {stats['total_entities']}")
    print(f"\n  By type:")
    for t, c in stats["by_type"].items():
        print(f"    {t:20s}: {c}")
    print(f"\n  By type_group (top 15):")
    for tg, c in list(stats["by_type_group"].items())[:15]:
        print(f"    {tg:35s}: {c}")
    print(f"\n  Alias statistics:")
    for k, v in stats["alias_statistics"].items():
        print(f"    {k:35s}: {v}")
    print(f"\n  Definition coverage: {stats['definition_coverage']['coverage_pct']}%")
    print(f"  Generic entities  : {stats['generic_entities']['count']}")
    print(f"\nFull report: data/ontology_qa/ontology_statistics.json")


if __name__ == "__main__":
    _main()
