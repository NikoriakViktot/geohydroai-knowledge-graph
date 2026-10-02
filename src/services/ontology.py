"""Ontology lookups for the API: normalise raw terms, list entities (src/normalization)."""

from __future__ import annotations

import ast

TYPES = ("method", "sensor", "metric", "parameter", "concept", "data", "organization", "system", "uncertainty")


def _aliases(entity: dict) -> list[str]:
    raw = entity.get("aliases") or []
    if isinstance(raw, str):
        try:
            raw = ast.literal_eval(raw)
        except (ValueError, SyntaxError):
            raw = [raw]
    return [str(a) for a in raw]


def normalize(terms: list[dict], allow_semantic: bool = False) -> list[dict]:
    from src.normalization.ontology_matcher import normalize_entity
    out = []
    for t in terms:
        r = normalize_entity(t["text"], expected_type=t.get("expected_type"), context=t.get("context"),
                             allow_semantic=allow_semantic)
        out.append({"text": t["text"], "canonical_id": r.get("canonical_id"), "display_name": r.get("display_name"),
                    "type": r.get("type"), "match_type": r.get("match_type") or "unknown",
                    "confidence": float(r.get("confidence") or 0.0)})
    return out


def entities(type_: str | None, q: str | None, limit: int, offset: int) -> tuple[list[dict], int]:
    from src.normalization.alias_resolver import load_ontology_registry
    registry = load_ontology_registry()
    needle = (q or "").strip().lower()
    hits = []
    for cid in sorted(registry):
        e = registry[cid]
        if type_ and e.get("type") != type_:
            continue
        aliases = _aliases(e)
        if needle and needle not in cid.lower() and needle not in (e.get("display_name") or "").lower() \
                and not any(needle in a.lower() for a in aliases):
            continue
        hits.append({"canonical_id": cid, "display_name": e.get("display_name"), "type": e.get("type"),
                     "aliases": aliases, "definition": e.get("definition") or None})
    return hits[offset:offset + limit], len(hits)
