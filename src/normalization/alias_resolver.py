"""
alias_resolver.py  —  GeoHydroAI deterministic ontology alias resolution
=========================================================================

Provides a fast, cache-warm lookup from any raw mention to a canonical
ontology entity.  Matching is:
    - case-insensitive
    - punctuation-insensitive
    - whitespace-normalised
    - underscore/hyphen tolerant

No embeddings, no clustering.  Deterministic only.

Usage:
    from src.normalization.alias_resolver import (
        load_ontology_registry,
        normalize_alias,
        get_entity_by_id,
        find_candidates,
    )

    registry = load_ontology_registry()
    canonical_id = normalize_alias("HEC RAS")    # → "method.hec_ras"
    entity       = get_entity_by_id("method.hec_ras")
    candidates   = find_candidates("random forest classifier")
"""

from __future__ import annotations

import json
import re
import threading
from functools import lru_cache
from pathlib import Path
from typing import Optional

_ONTOLOGY_DIR = Path(__file__).resolve().parents[2] / "data" / "ontology"


# ─────────────────────────────────────────────────────────────────────────────
# Normalisation primitive
# ─────────────────────────────────────────────────────────────────────────────

def _slugify(text: str) -> str:
    """Canonical slug: lowercase, non-alnum → underscore, collapse runs."""
    t = text.lower().strip()
    t = re.sub(r"[^a-z0-9]+", "_", t)
    t = re.sub(r"_+", "_", t).strip("_")
    return t


# ─────────────────────────────────────────────────────────────────────────────
# Registry loader (singleton, lazy)
# ─────────────────────────────────────────────────────────────────────────────

_REGISTRY:      Optional[dict[str, dict]] = None
_ALIAS_TABLE:   Optional[dict[str, str]]  = None    # slug → canonical_id
_REGISTRY_LOCK: threading.Lock            = threading.Lock()


def load_ontology_registry() -> dict[str, dict]:
    """
    Load and cache the canonical ontology registry.

    Returns:
        Dict mapping canonical_id → entity dict.
    """
    global _REGISTRY, _ALIAS_TABLE

    if _REGISTRY is not None:
        return _REGISTRY

    with _REGISTRY_LOCK:
        if _REGISTRY is not None:   # re-check after acquiring lock
            return _REGISTRY

        reg_path   = _ONTOLOGY_DIR / "ontology_registry.json"
        alias_path = _ONTOLOGY_DIR / "aliases.json"

        if not reg_path.exists():
            raise FileNotFoundError(
                f"ontology_registry.json not found at {reg_path}. "
                "Run: python -m src.ontology.merge_ontology"
            )

        _REGISTRY = json.loads(reg_path.read_text(encoding="utf-8"))

        # Load pre-built alias table
        if alias_path.exists():
            raw = json.loads(alias_path.read_text(encoding="utf-8"))
            # alias table is slug → {canonical_id, display_name, type}
            _ALIAS_TABLE = {slug: entry["canonical_id"] for slug, entry in raw.items()}
        else:
            # Build in-memory from registry
            _ALIAS_TABLE = _build_alias_table_from_registry(_REGISTRY)

    return _REGISTRY


def _build_alias_table_from_registry(registry: dict[str, dict]) -> dict[str, str]:
    table: dict[str, str] = {}
    for eid, ent in registry.items():
        # register by id, by id suffix, by display_name
        table[_slugify(eid)]                     = eid
        table[_slugify(eid.split(".", 1)[-1])]   = eid
        table[_slugify(ent.get("display_name", ""))] = eid
        for alias in ent.get("aliases", []):
            table[_slugify(alias)] = eid
    return table


def _get_alias_table() -> dict[str, str]:
    if _ALIAS_TABLE is None:
        load_ontology_registry()
    return _ALIAS_TABLE  # type: ignore[return-value]


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def normalize_alias(text: str) -> Optional[str]:
    """
    Map any raw mention to its canonical ontology ID.

    Matching is deterministic:
        1. Exact slug match against the alias table.
        2. Slug match after stripping common noise suffixes.

    Args:
        text: Raw mention, e.g. "HEC RAS", "hec_ras", "HEC-RAS".

    Returns:
        Canonical ID string (e.g. "method.hec_ras") or None if not found.
    """
    if not text or not text.strip():
        return None

    table = _get_alias_table()
    slug  = _slugify(text)

    if slug in table:
        return table[slug]

    # Try progressive truncation (remove trailing noise/type descriptor words)
    _NOISE_SUFFIXES = (
        "model", "algorithm", "method", "approach", "technique",
        "system", "index", "data", "analysis", "sar", "imagery",
        "image", "sensor", "satellite", "mapping", "based", "network",
        "classifier", "detector", "estimator",
    )
    parts = slug.split("_")
    while parts and parts[-1] in _NOISE_SUFFIXES:
        parts.pop()
        candidate = "_".join(parts)
        if candidate in table:
            return table[candidate]

    # Try all contiguous sub-phrases (longest first) in case the slug
    # is a compound like "sentinel_1_sar" → try "sentinel_1"
    tokens = slug.split("_")
    for length in range(len(tokens) - 1, 1, -1):
        for start in range(len(tokens) - length + 1):
            sub = "_".join(tokens[start : start + length])
            if sub in table:
                return table[sub]

    return None


def get_entity_by_id(entity_id: str) -> Optional[dict]:
    """
    Retrieve a full ontology entity by canonical ID.

    Args:
        entity_id: Canonical ID, e.g. "method.hec_ras".

    Returns:
        Entity dict or None if not found.
    """
    registry = load_ontology_registry()
    return registry.get(entity_id)


def find_candidates(text: str, max_results: int = 5) -> list[dict]:
    """
    Find ontology entities whose aliases overlap with tokens in `text`.

    Useful for multi-term extractions like "Random Forest classifier applied
    to Sentinel-1 SAR data".

    Returns:
        List of candidate entity dicts, ordered by specificity (longer match first).
    """
    table    = _get_alias_table()
    registry = load_ontology_registry()
    slug     = _slugify(text)

    # Collect all entity IDs that have ANY slug that is a substring of text
    matches: dict[str, int] = {}  # eid → match_length

    for alias_slug, eid in table.items():
        if alias_slug and alias_slug in slug:
            length = len(alias_slug)
            if matches.get(eid, 0) < length:
                matches[eid] = length

    # Sort by match length descending (most specific first)
    ranked = sorted(matches.items(), key=lambda x: -x[1])[:max_results]
    return [registry[eid] for eid, _ in ranked if eid in registry]


def list_entities_by_type(entity_type: str) -> list[dict]:
    """
    Return all entities of a given canonical type.

    Args:
        entity_type: One of method|sensor|metric|data|concept|
                     model|organization|parameter|uncertainty|system.
    """
    registry = load_ontology_registry()
    return [e for e in registry.values() if e.get("type") == entity_type]
