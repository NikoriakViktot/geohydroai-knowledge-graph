"""
ontology_matcher.py  —  GeoHydroAI ontology normalisation gateway
==================================================================

Single entry point for converting raw extracted text mentions into
canonical ontology entities.

Resolution order (deterministic first, semantic fallback last):
    0. Context-aware disambiguation pre-pass (for ambiguous tokens)
    1. Alias table exact slug match            → match_type "alias"
    2. Display-name exact match               → match_type "exact"
    3. Semantic embedding nearest-neighbour   → match_type "semantic"
    4. No match above threshold               → match_type "unknown"

Usage:
    from src.normalization.ontology_matcher import normalize_entity

    result = normalize_entity("HEC RAS")
    # {
    #   "raw_name":     "HEC RAS",
    #   "canonical_id": "method.hec_ras",
    #   "display_name": "Hydrologic Engineering Center-River Analysis System",
    #   "type":         "model",
    #   "match_type":   "alias",
    #   "confidence":   1.0
    # }

    result = normalize_entity("ANN", context="time series forecast lead time")
    # {
    #   "raw_name":      "ANN",
    #   "canonical_id":  "method.ann_time_series",
    #   "display_name":  "ANN-TimeSeries",
    #   "type":          "model",
    #   "match_type":    "disambiguation",
    #   "confidence":    0.95,
    #   "category":      "time_series",
    #   "subcategory":   "machine_learning",
    # }
"""

from __future__ import annotations

from typing import Optional

from src.normalization.alias_resolver import (
    find_candidates,
    get_entity_by_id,
    load_ontology_registry,
    normalize_alias,
)

_UNKNOWN_RESULT_TEMPLATE = {
    "raw_name":     "",
    "canonical_id": None,
    "display_name": None,
    "type":         None,
    "match_type":   "unknown",
    "confidence":   0.0,
    "category":     None,
    "subcategory":  None,
    "dimension":    None,
}


def normalize_entity(
    raw_name: str,
    expected_type: Optional[str] = None,
    context: Optional[str] = None,
    allow_semantic: bool = True,
) -> dict:
    """
    Normalise a raw extracted mention to a canonical ontology entity.

    Args:
        raw_name:      Raw text as extracted from a paper (e.g. "RF classifier").
        expected_type: Optional type hint ("method", "sensor", "metric", …).
                       Filters alias/semantic results to that type only when provided.
        context:       Optional surrounding text for disambiguation (sentence/paragraph).
        allow_semantic: False skips the embedding fallback (deterministic matches only;
                       the API's default, so no model is loaded).

    Returns:
        {
          "raw_name":     str,
          "canonical_id": str | None,
          "display_name": str | None,
          "type":         str | None,
          "match_type":   "alias" | "exact" | "semantic" | "disambiguation" | "unknown",
          "confidence":   float,
          "category":     str | None,   # v2 model category
          "subcategory":  str | None,
          "dimension":    str | None,
        }
    """
    if not raw_name or not raw_name.strip():
        return {**_UNKNOWN_RESULT_TEMPLATE, "raw_name": raw_name}

    load_ontology_registry()  # ensure warm

    # ── 0a. Canonical-ID self-lookup ──────────────────────────────────────
    # If the raw input is already a canonical ID (e.g. "metric.rmse"),
    # _slugify turns the dot to underscore and the alias table misses it.
    # Short-circuit: if the input matches the canonical ID pattern and
    # exists in the registry, return it directly.
    stripped = raw_name.strip()
    if "." in stripped:
        entity = get_entity_by_id(stripped)
        if entity and _type_ok(entity, expected_type):
            return {
                "raw_name":     raw_name,
                "canonical_id": stripped,
                "display_name": entity.get("display_name"),
                "type":         entity.get("type"),
                "match_type":   "alias",
                "confidence":   1.0,
                "category":     entity.get("category"),
                "subcategory":  entity.get("subcategory"),
                "dimension":    entity.get("dimension"),
            }

    # ── 0c. Context-aware disambiguation pre-pass ──────────────────────────
    if context:
        dis = _try_disambiguate(raw_name, context)
        if dis:
            resolved_name = dis.get("resolved_entity", raw_name)
            canonical_id  = normalize_alias(resolved_name) or normalize_alias(
                resolved_name.lower().replace("-", "_").replace(" ", "_")
            )
            entity = get_entity_by_id(canonical_id) if canonical_id else None
            if canonical_id:
                return {
                    "raw_name":     raw_name,
                    "canonical_id": canonical_id,
                    "display_name": entity.get("display_name") if entity else resolved_name,
                    "type":         entity.get("type") if entity else dis.get("type"),
                    "match_type":   "disambiguation",
                    "confidence":   0.95,
                    "category":     dis.get("category"),
                    "subcategory":  dis.get("subcategory"),
                    "dimension":    dis.get("dimension"),
                }
            # Disambiguation resolved a sense but couldn't map to a canonical ID —
            # fall through to alias/exact/semantic rather than returning None canonical_id.

    # ── 1. Alias table lookup (deterministic) ─────────────────────────────
    canonical_id = normalize_alias(raw_name)

    if canonical_id:
        entity = get_entity_by_id(canonical_id)
        if entity and _type_ok(entity, expected_type):
            return {
                "raw_name":     raw_name,
                "canonical_id": canonical_id,
                "display_name": entity.get("display_name"),
                "type":         entity.get("type"),
                "match_type":   "alias",
                "confidence":   1.0,
                "category":     entity.get("category"),
                "subcategory":  entity.get("subcategory"),
                "dimension":    entity.get("dimension"),
            }

    # ── 2. Display-name exact match ───────────────────────────────────────
    registry = load_ontology_registry()
    raw_lower = raw_name.strip().lower()

    for eid, entity in registry.items():
        if entity.get("display_name", "").lower() == raw_lower:
            if _type_ok(entity, expected_type):
                return {
                    "raw_name":     raw_name,
                    "canonical_id": eid,
                    "display_name": entity.get("display_name"),
                    "type":         entity.get("type"),
                    "match_type":   "exact",
                    "confidence":   1.0,
                    "category":     entity.get("category"),
                    "subcategory":  entity.get("subcategory"),
                    "dimension":    entity.get("dimension"),
                }

    # ── 3. Semantic embedding fallback ────────────────────────────────────
    if not allow_semantic:
        return {**_UNKNOWN_RESULT_TEMPLATE, "raw_name": raw_name}
    try:
        from src.normalization.embedding_matcher import semantic_match
        sem_result = semantic_match(raw_name, expected_type=expected_type)
        if sem_result and sem_result.get("confidence", 0.0) >= 0.82:
            sem_result["match_type"] = "semantic"
            sem_result.setdefault("category", None)
            sem_result.setdefault("subcategory", None)
            sem_result.setdefault("dimension", None)
            return sem_result
    except Exception:
        pass  # embedding model not available — continue to unknown

    # ── 4. Unknown ────────────────────────────────────────────────────────
    return {**_UNKNOWN_RESULT_TEMPLATE, "raw_name": raw_name}


def normalize_entities_batch(
    raw_names: list[str],
    expected_type: Optional[str] = None,
    context: Optional[str] = None,
) -> list[dict]:
    """Normalise a list of raw mentions, preserving order."""
    return [normalize_entity(name, expected_type, context) for name in raw_names]


def _try_disambiguate(token: str, context: str) -> Optional[dict]:
    """
    Apply ontology_disambiguation_rules.json context-aware disambiguation.
    Returns the first matching candidate rule dict, or None.
    """
    try:
        from src.ingestion.knowledge.normalizer import _load_disambiguation_rules
        rules = _load_disambiguation_rules()
    except Exception:
        return None

    token_lower   = token.strip().lower()
    context_lower = context.lower()

    for rule_group in rules:
        ambig = rule_group.get("ambiguous_token", "").lower()
        if ambig != token_lower:
            continue
        for candidate in rule_group.get("rules", []):
            includes = candidate.get("context_includes", [])
            excludes = candidate.get("context_excludes", [])
            inc_ok = any(kw.lower() in context_lower for kw in includes) if includes else True
            exc_ok = any(kw.lower() in context_lower for kw in excludes) if excludes else False
            if inc_ok and not exc_ok:
                return candidate

    return None


def _type_ok(entity: dict, expected_type: Optional[str]) -> bool:
    """Return True if entity type satisfies the expected_type filter (or no filter)."""
    if expected_type is None:
        return True
    etype = entity.get("type", "")
    if expected_type == "method" and etype in ("method", "model", "process", "software"):
        return True
    return etype == expected_type
