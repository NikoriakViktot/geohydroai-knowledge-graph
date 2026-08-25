"""
ontology_refiner.py  —  GeoHydroAI ontology refinement orchestrator
====================================================================

Applies all patches in order:
    1. type_group assignments  (TYPE_GROUP_MAP)
    2. hierarchy injection     (HIERARCHY_PATCHES — new canonical entities)
    3. alias expansion         (ALIAS_PATCHES)
    4. generic-entity flagging (GENERIC_ENTITIES)

Then writes the refined registry back to disk alongside a diff summary
so the human can review what changed.

RULES:
    - Does NOT generate canonical IDs automatically.
    - Does NOT use embeddings or clustering for identity.
    - Does NOT touch neo4j / graph layer.
    - Generic entities are FLAGGED only — not deleted.
      Deletion requires human review of ontology_review/ files first.

Usage:
    python -m src.ontology.refinement.ontology_refiner          # dry-run
    python -m src.ontology.refinement.ontology_refiner --apply  # write files
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

_ONTOLOGY_DIR = Path(__file__).resolve().parents[3] / "data" / "ontology"
_QA_DIR       = Path(__file__).resolve().parents[3] / "data" / "ontology_qa"


# ─────────────────────────────────────────────────────────────────────────────
# Load base registry
# ─────────────────────────────────────────────────────────────────────────────

def _load_registry() -> dict[str, dict]:
    path = _ONTOLOGY_DIR / "ontology_registry.json"
    if not path.exists():
        raise FileNotFoundError(
            f"ontology_registry.json not found at {path}. "
            "Run: python -m src.ontology.merge_ontology"
        )
    return json.loads(path.read_text(encoding="utf-8"))


# ─────────────────────────────────────────────────────────────────────────────
# Step 1 — type_group assignments
# ─────────────────────────────────────────────────────────────────────────────

def _apply_type_groups(registry: dict[str, dict]) -> dict:
    from src.ontology.refinement.hierarchy_builder import TYPE_GROUP_MAP

    added   = 0
    updated = 0

    for eid, type_group in TYPE_GROUP_MAP.items():
        if eid not in registry:
            log.warning("TYPE_GROUP_MAP target not in registry: %s", eid)
            continue
        existing = registry[eid].get("type_group")
        if existing == type_group:
            continue
        if existing is None:
            added += 1
        else:
            updated += 1
            log.debug("  type_group changed: %s  %s → %s", eid, existing, type_group)
        registry[eid]["type_group"] = type_group

    log.info("type_group: %d added, %d updated", added, updated)
    return {"type_group_added": added, "type_group_updated": updated}


# ─────────────────────────────────────────────────────────────────────────────
# Step 2 — hierarchy injection
# ─────────────────────────────────────────────────────────────────────────────

def _apply_hierarchy_patches(registry: dict[str, dict]) -> dict:
    from src.ontology.refinement.hierarchy_builder import HIERARCHY_PATCHES

    injected   = 0
    skipped    = 0

    for patch in HIERARCHY_PATCHES:
        eid = patch["id"]
        if eid in registry:
            # Only update fields that are missing; don't overwrite curated data
            existing = registry[eid]
            for k, v in patch.items():
                if k not in existing or existing[k] in (None, "", []):
                    existing[k] = v
            skipped += 1
        else:
            # Brand-new entity from HIERARCHY_PATCHES
            entry = dict(patch)
            entry.setdefault("aliases", [])
            entry.setdefault("definition", "")
            entry.setdefault("sources", ["hierarchy_patch"])
            registry[eid] = entry
            injected += 1
            log.debug("  Injected: %s", eid)

    log.info("hierarchy_patches: %d injected, %d already-present (fields merged)", injected, skipped)
    return {"hierarchy_injected": injected, "hierarchy_merged": skipped}


# ─────────────────────────────────────────────────────────────────────────────
# Step 3 — alias expansion
# ─────────────────────────────────────────────────────────────────────────────

def _apply_alias_expansion(registry: dict[str, dict]) -> dict:
    from src.ontology.refinement.alias_expander import expand_aliases, expansion_report

    expand_aliases(registry)
    report = expansion_report(registry)
    log.info("alias_expansion complete: %s", report)
    return {"alias_counts_by_type": report}


# ─────────────────────────────────────────────────────────────────────────────
# Step 4 — generic-entity flagging
# ─────────────────────────────────────────────────────────────────────────────

def _apply_generic_flags(registry: dict[str, dict]) -> dict:
    from src.ontology.refinement.hierarchy_builder import GENERIC_ENTITIES

    flagged  = 0
    missing  = 0

    for eid, reason in GENERIC_ENTITIES.items():
        if eid not in registry:
            log.warning("GENERIC_ENTITIES target not in registry: %s", eid)
            missing += 1
            continue
        registry[eid]["is_generic"] = True
        registry[eid]["generic_reason"] = reason
        flagged += 1
        log.debug("  Flagged generic: %s", eid)

    log.info("generic_flags: %d flagged, %d missing", flagged, missing)
    return {"generic_flagged": flagged, "generic_missing": missing}


# ─────────────────────────────────────────────────────────────────────────────
# Diff summary
# ─────────────────────────────────────────────────────────────────────────────

def _compute_diff(before: dict[str, dict], after: dict[str, dict]) -> dict:
    new_ids     = sorted(set(after) - set(before))
    removed_ids = sorted(set(before) - set(after))

    alias_delta: dict[str, int] = {}
    for eid in set(before) & set(after):
        delta = len(after[eid].get("aliases", [])) - len(before[eid].get("aliases", []))
        if delta != 0:
            alias_delta[eid] = delta

    type_group_added = [
        eid for eid in set(before) & set(after)
        if "type_group" not in before[eid] and "type_group" in after[eid]
    ]
    generic_flagged = [
        eid for eid in set(before) & set(after)
        if not before[eid].get("is_generic") and after[eid].get("is_generic")
    ]

    return {
        "new_entities":    new_ids,
        "removed_entities": removed_ids,
        "alias_delta":     alias_delta,
        "type_group_added_to": type_group_added,
        "generic_flagged": generic_flagged,
        "total_before":    len(before),
        "total_after":     len(after),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Write refined registry
# ─────────────────────────────────────────────────────────────────────────────

def _write_refined_registry(registry: dict[str, dict]) -> None:
    path = _ONTOLOGY_DIR / "ontology_registry.json"
    path.write_text(
        json.dumps(registry, indent=2, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    log.info("Written: %s (%d entities)", path, len(registry))

    # Rebuild per-type JSON shards
    from src.ontology.merge_ontology import Registry as _Reg
    fake_reg = _Reg.__new__(_Reg)
    fake_reg._entities = registry  # type: ignore[attr-defined]

    shard_dir = _ONTOLOGY_DIR / "shards"
    shard_dir.mkdir(parents=True, exist_ok=True)

    by_file = fake_reg.by_file_key()
    for fname, entities in by_file.items():
        shard = shard_dir / f"{fname}.json"
        shard.write_text(
            json.dumps({"entities": entities}, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        log.info("  shard: %s (%d entities)", fname, len(entities))


def _write_diff_report(diff: dict, step_stats: list[dict]) -> Path:
    _QA_DIR.mkdir(parents=True, exist_ok=True)
    out = _QA_DIR / "refinement_diff.json"
    out.write_text(
        json.dumps({
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "diff": diff,
            "step_stats": step_stats,
        }, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    log.info("Diff report: %s", out)
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def refine_ontology(dry_run: bool = False) -> dict:
    """
    Run all 4 refinement passes and optionally write results to disk.

    Args:
        dry_run: If True, compute diff but do NOT write any files.

    Returns:
        Diff summary dict.
    """
    registry = _load_registry()
    before   = deepcopy(registry)

    step_stats: list[dict] = []
    step_stats.append({"step": "type_groups",       **_apply_type_groups(registry)})
    step_stats.append({"step": "hierarchy_patches", **_apply_hierarchy_patches(registry)})
    step_stats.append({"step": "alias_expansion",   **_apply_alias_expansion(registry)})
    step_stats.append({"step": "generic_flags",     **_apply_generic_flags(registry)})

    diff = _compute_diff(before, registry)

    if not dry_run:
        _write_refined_registry(registry)
        _write_diff_report(diff, step_stats)

    log.info(
        "Refinement complete: %d → %d entities, %d new, %d generics flagged",
        diff["total_before"], diff["total_after"],
        len(diff["new_entities"]), len(diff["generic_flagged"]),
    )
    return diff


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def _main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    parser = argparse.ArgumentParser(description="GeoHydroAI ontology refiner")
    parser.add_argument(
        "--apply", action="store_true",
        help="Write refined registry to disk (default: dry-run only)",
    )
    args = parser.parse_args()

    diff = refine_ontology(dry_run=not args.apply)

    print(f"\n{'DRY RUN — ' if not args.apply else ''}Refinement summary:")
    print(f"  Entities before : {diff['total_before']}")
    print(f"  Entities after  : {diff['total_after']}")
    print(f"  New injected    : {len(diff['new_entities'])}")
    print(f"  Generic flagged : {len(diff['generic_flagged'])}")
    print(f"  Alias delta     : {sum(diff['alias_delta'].values())} net aliases added")

    if diff["new_entities"]:
        print("\nNew entities:")
        for eid in diff["new_entities"]:
            print(f"  + {eid}")

    if diff["generic_flagged"]:
        print("\nFlagged as generic:")
        for eid in diff["generic_flagged"]:
            print(f"  ~ {eid}")

    if not args.apply:
        print("\nRe-run with --apply to write changes.")
    else:
        print("\nFiles written. Run ontology_validator.py to check for issues.")


if __name__ == "__main__":
    _main()
