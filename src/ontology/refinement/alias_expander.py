"""
alias_expander.py  —  GeoHydroAI ontology alias expansion
==========================================================

Applies ALIAS_PATCHES from hierarchy_builder to an in-memory registry dict,
adding curated aliases to existing entities without touching canonical IDs.

Rules:
    - Only adds aliases; never removes or renames.
    - Skips duplicate alias strings (case-insensitive slug equality).
    - Logs a warning for every patch targeting an entity that doesn't exist.

Usage:
    from src.ontology.refinement.alias_expander import expand_aliases

    updated_registry = expand_aliases(registry)
"""

from __future__ import annotations

import logging
from typing import Optional

from src.ontology.refinement.hierarchy_builder import ALIAS_PATCHES

log = logging.getLogger(__name__)


def _slug(text: str) -> str:
    import re
    t = text.lower().strip()
    t = re.sub(r"[^a-z0-9]+", "_", t)
    return re.sub(r"_+", "_", t).strip("_")


def expand_aliases(
    registry: dict[str, dict],
    patches: Optional[dict[str, list[str]]] = None,
) -> dict[str, dict]:
    """
    Apply alias patches to the registry.

    Args:
        registry: Mapping canonical_id → entity dict (modified in place).
        patches:  Override for ALIAS_PATCHES (useful for testing).

    Returns:
        The same registry dict, mutated.
    """
    if patches is None:
        patches = ALIAS_PATCHES

    added_total = 0
    skipped_missing = 0
    skipped_dupes = 0

    for entity_id, new_aliases in patches.items():
        if entity_id not in registry:
            log.warning("ALIAS_PATCH target not in registry: %s", entity_id)
            skipped_missing += 1
            continue

        ent = registry[entity_id]
        existing_slugs: set[str] = {_slug(a) for a in ent.get("aliases", [])}
        # Also include the display_name slug so we don't add it as an alias
        existing_slugs.add(_slug(ent.get("display_name", "")))
        existing_slugs.add(_slug(entity_id))

        added_for_entity = 0
        for alias in new_aliases:
            if not alias or not alias.strip():
                continue
            if _slug(alias) in existing_slugs:
                skipped_dupes += 1
                continue
            ent.setdefault("aliases", []).append(alias)
            existing_slugs.add(_slug(alias))
            added_for_entity += 1

        if added_for_entity:
            added_total += added_for_entity
            log.debug("  %s: +%d aliases", entity_id, added_for_entity)

    log.info(
        "expand_aliases: %d aliases added, %d missing targets, %d dupes skipped",
        added_total, skipped_missing, skipped_dupes,
    )
    return registry


def expansion_report(registry: dict[str, dict]) -> dict:
    """
    Return a summary dict of alias counts per entity type after expansion.
    """
    from collections import Counter
    counts: Counter = Counter()
    for ent in registry.values():
        counts[ent.get("type", "unknown")] += len(ent.get("aliases", []))
    return dict(counts)
