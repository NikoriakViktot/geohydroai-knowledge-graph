"""
ontology_collision_detector.py  —  GeoHydroAI alias collision detector
=======================================================================

Finds cases where the same normalized slug maps to more than one canonical
entity ID — which would make alias resolution non-deterministic.

Collision types:
    C01  Same alias slug → multiple canonical IDs (CRITICAL — must fix)
    C02  Same display_name slug → multiple IDs within the same type
    C03  Entity ID slug matches another entity's alias slug (shadow collision)

Output: data/ontology_qa/ontology_collisions.json

The report is for HUMAN REVIEW. Resolution requires manually deciding which
entity is canonical for the colliding alias and updating ALIAS_PATCHES or
the source ontology.

Usage:
    python -m src.ontology.refinement.ontology_collision_detector
"""

from __future__ import annotations

import json
import logging
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

_ONTOLOGY_DIR = Path(__file__).resolve().parents[3] / "data" / "ontology"
_QA_DIR       = Path(__file__).resolve().parents[3] / "data" / "ontology_qa"


def _slugify(text: str) -> str:
    t = text.lower().strip()
    t = re.sub(r"[^a-z0-9]+", "_", t)
    return re.sub(r"_+", "_", t).strip("_")


# ─────────────────────────────────────────────────────────────────────────────
# Collision checks
# ─────────────────────────────────────────────────────────────────────────────

def _check_alias_collisions(registry: dict[str, dict]) -> list[dict]:
    """C01: alias slug → multiple canonical IDs."""
    slug_to_eids: dict[str, list[str]] = defaultdict(list)

    for eid, ent in registry.items():
        # All slugs this entity claims
        slugs: set[str] = set()
        slugs.add(_slugify(eid))
        slugs.add(_slugify(eid.split(".", 1)[-1]))
        slugs.add(_slugify(ent.get("display_name", "")))
        for alias in ent.get("aliases", []):
            slugs.add(_slugify(alias))

        for slug in slugs:
            if slug:
                slug_to_eids[slug].append(eid)

    collisions = []
    for slug, eids in slug_to_eids.items():
        unique_eids = list(dict.fromkeys(eids))  # preserve order, remove dupes
        if len(unique_eids) > 1:
            collisions.append({
                "code":      "C01",
                "level":     "ERROR",
                "slug":      slug,
                "entity_ids": unique_eids,
                "message":   f"Alias slug '{slug}' maps to {len(unique_eids)} entities: {unique_eids}",
            })

    return collisions


def _check_display_name_collisions(registry: dict[str, dict]) -> list[dict]:
    """C02: same display_name slug within the same type."""
    type_name_eids: dict[tuple[str, str], list[str]] = defaultdict(list)

    for eid, ent in registry.items():
        t    = ent.get("type", "unknown")
        slug = _slugify(ent.get("display_name", ""))
        if slug:
            type_name_eids[(t, slug)].append(eid)

    collisions = []
    for (t, slug), eids in type_name_eids.items():
        if len(eids) > 1:
            collisions.append({
                "code":      "C02",
                "level":     "WARNING",
                "slug":      slug,
                "type":      t,
                "entity_ids": eids,
                "message":   f"display_name slug '{slug}' (type={t}) shared by: {eids}",
            })

    return collisions


def _check_shadow_collisions(registry: dict[str, dict]) -> list[dict]:
    """C03: one entity's canonical ID slug matches another entity's alias slug."""
    # Build: alias_slug → set of owning entity IDs
    alias_owners: dict[str, set[str]] = defaultdict(set)
    for eid, ent in registry.items():
        for alias in ent.get("aliases", []):
            alias_owners[_slugify(alias)].add(eid)

    collisions = []
    for eid in registry:
        id_slug = _slugify(eid.split(".", 1)[-1])  # just the suffix
        owners  = alias_owners.get(id_slug, set())
        shadow  = owners - {eid}
        if shadow:
            collisions.append({
                "code":      "C03",
                "level":     "WARNING",
                "slug":      id_slug,
                "entity_id":  eid,
                "shadowed_by": sorted(shadow),
                "message":   (
                    f"Entity '{eid}' ID slug '{id_slug}' is also claimed "
                    f"as an alias by: {sorted(shadow)}"
                ),
            })

    return collisions


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def detect_collisions(registry: dict[str, dict] | None = None) -> dict:
    """
    Detect all collision types and write the QA report.

    Returns:
        Summary dict with collision counts and lists.
    """
    if registry is None:
        reg_path = _ONTOLOGY_DIR / "ontology_registry.json"
        if not reg_path.exists():
            raise FileNotFoundError(f"Registry not found: {reg_path}")
        registry = json.loads(reg_path.read_text(encoding="utf-8"))

    c01 = _check_alias_collisions(registry)
    c02 = _check_display_name_collisions(registry)
    c03 = _check_shadow_collisions(registry)

    all_collisions = c01 + c02 + c03
    all_collisions.sort(key=lambda x: ({"ERROR": 0, "WARNING": 1}.get(x["level"], 9), x["slug"]))

    summary = {
        "generated_at":            datetime.now(timezone.utc).isoformat(),
        "entity_count":            len(registry),
        "c01_alias_collisions":    len(c01),
        "c02_display_name_collisions": len(c02),
        "c03_shadow_collisions":   len(c03),
        "total_collisions":        len(all_collisions),
        "note": (
            "C01 (ERROR) must be resolved before production use. "
            "C02/C03 (WARNING) should be reviewed."
        ),
        "collisions": all_collisions,
    }

    _QA_DIR.mkdir(parents=True, exist_ok=True)
    out = _QA_DIR / "ontology_collisions.json"
    out.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    log.info(
        "Collision report: C01=%d, C02=%d, C03=%d → %s",
        len(c01), len(c02), len(c03), out,
    )
    return summary


def _main() -> None:
    import logging as _logging
    _logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    summary = detect_collisions()

    print(f"\nOntology Collision Detection")
    print(f"  Entity count  : {summary['entity_count']}")
    print(f"  C01 (ERROR)   : {summary['c01_alias_collisions']} alias slug collisions")
    print(f"  C02 (WARNING) : {summary['c02_display_name_collisions']} display_name collisions")
    print(f"  C03 (WARNING) : {summary['c03_shadow_collisions']} shadow collisions")

    errors = [c for c in summary["collisions"] if c["level"] == "ERROR"]
    if errors:
        print(f"\n  ERROR collisions (must fix):")
        for c in errors[:20]:
            print(f"    [{c['code']}] slug='{c['slug']}' → {c['entity_ids']}")
        if len(errors) > 20:
            print(f"    ... and {len(errors) - 20} more")

    print(f"\nFull report: data/ontology_qa/ontology_collisions.json")

    import sys
    sys.exit(1 if summary["c01_alias_collisions"] > 0 else 0)


if __name__ == "__main__":
    _main()
