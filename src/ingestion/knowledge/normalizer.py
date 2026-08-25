"""
normalizer.py
-------------
Alias resolution and canonical naming for geospatial entities.

Responsibilities:
  - Resolve short aliases (S1 → Sentinel-1, RF → Random Forest)
  - Canonical naming: normalize entity name to KB canonical form
  - Fuzzy matching for near-matches
  - Context-aware disambiguation using ontology_disambiguation_rules.json
"""

from __future__ import annotations

import json
import re
import logging
from pathlib import Path
from typing import Optional, Tuple

from .knowledge_loader import KnowledgeBase, EntityRecord

log = logging.getLogger(__name__)

_DATA_DIR = Path(__file__).resolve().parents[3] / "data"

# Regex shortcuts used in text that map to canonical names
INLINE_ALIASES: list[Tuple[str, str]] = [
    # SAR sensor short names
    (r"\bS[\-\s]?1[ABC]?\b", "Sentinel-1"),
    (r"\bS[\-\s]?2[ABC]?\b", "Sentinel-2"),
    (r"\bS[\-\s]?3[ABC]?\b", "Sentinel-3"),
    (r"\bS[\-\s]?5P\b",       "Sentinel-5P"),
    (r"\bL[\-\s]?8\b|\bOLI\b|\bETM\+?\b", "LANDSAT"),
    (r"\bL[\-\s]?9\b",        "LANDSAT"),
    # Platforms
    (r"\bGEE\b",              "GEE"),
    (r"\bESRI\b",             "ArcGIS"),
    # Indices
    (r"\bNDVI\b",             "NDVI"),
    (r"\bNDWI\b",             "NDWI"),
    (r"\bMNDWI\b",            "MNDWI"),
    (r"\bEVI2\b",             "EVI"),
    # Hydro models
    (r"\bHEC[\-\s]?RAS\b",    "HEC-RAS"),
    (r"\bHEC[\-\s]?HMS\b",    "HEC-HMS"),
    (r"\bSWAT\+?\b",          "SWAT"),
    # ML
    (r"\bRF\b(?=\s+classif)", "RANDOM-FOREST"),
    (r"\bSVC\b",              "SVM"),
    # Metrics
    (r"\bKGE\b",              "KGE"),
    (r"\bNSE\b",              "NSE"),
    (r"\bNSCE\b",             "NSE"),
]


def _load_disambiguation_rules() -> list[dict]:
    """Load disambiguation rules from ontology_disambiguation_rules.json."""
    path = _DATA_DIR / "ontology_disambiguation_rules.json"
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data.get("rules", [])
    except Exception as exc:
        log.warning("Could not load disambiguation rules: %s", exc)
        return []


class Normalizer:
    """
    Resolves aliases and normalises entity names against the knowledge base.
    Supports context-aware disambiguation via ontology_disambiguation_rules.json.

    Usage:
        norm = Normalizer(kb)
        canonical, record = norm.resolve("S1")
        # → "Sentinel-1", EntityRecord(...)

        result = norm.disambiguate("ANN", context="time series forecast lead time")
        # → {"resolved_entity": "ANN-TimeSeries", "category": "time_series", ...}
    """

    def __init__(self, kb: KnowledgeBase):
        self.kb = kb
        # pre-compile inline alias patterns
        self._compiled: list[Tuple[re.Pattern, str]] = [
            (re.compile(pat, re.IGNORECASE), canonical)
            for pat, canonical in INLINE_ALIASES
        ]
        self._dis_rules: list[dict] = _load_disambiguation_rules()

    # ── public API ─────────────────────────────────────────────────────────────

    def resolve(self, name: str) -> Tuple[str, Optional[EntityRecord]]:
        """
        Resolve a name to (canonical_name, EntityRecord|None).

        Resolution order:
          1. Exact match in KB
          2. KB alias lookup
          3. Inline alias regex
          4. Case-insensitive fuzzy
        """
        if not name:
            return name, None

        # 1. exact match
        rec = self.kb.resolve(name)
        if rec:
            return rec.acronym, rec

        # 2. inline alias patterns
        for pattern, canonical in self._compiled:
            if pattern.fullmatch(name.strip()):
                rec2 = self.kb.resolve(canonical)
                return canonical, rec2

        # 3. case-insensitive fuzzy against KB keys
        upper = name.upper()
        if upper in self.kb.entities:
            rec3 = self.kb.entities[upper]
            return rec3.acronym, rec3

        return name, None

    def disambiguate(self, token: str, context: str) -> Optional[dict]:
        """
        Apply context-aware disambiguation for an ambiguous token.

        Args:
            token:   The ambiguous acronym or phrase (e.g. "ANN", "HEC-RAS").
            context: Surrounding text (sentence or paragraph).

        Returns:
            Matched rule dict with resolved_entity, category, etc., or None.
        """
        token_lower   = token.lower()
        context_lower = context.lower()

        for rule_group in self._dis_rules:
            # check if this rule group handles the token
            ambig = rule_group.get("ambiguous_token", "").lower()
            if ambig != token_lower:
                continue

            for candidate_rule in rule_group.get("rules", []):
                includes = candidate_rule.get("context_includes", [])
                excludes = candidate_rule.get("context_excludes", [])

                inc_hit = any(kw.lower() in context_lower for kw in includes) if includes else True
                exc_hit = any(kw.lower() in context_lower for kw in excludes) if excludes else False

                if inc_hit and not exc_hit:
                    return candidate_rule

        return None

    def resolve_with_context(
        self, name: str, context: str
    ) -> Tuple[str, Optional[EntityRecord], Optional[dict]]:
        """
        Resolve a name with disambiguation context.

        Returns:
            (canonical_name, EntityRecord|None, disambiguation_result|None)
        """
        # try disambiguation first for known ambiguous tokens
        dis = self.disambiguate(name, context)
        if dis:
            resolved = dis.get("resolved_entity", name)
            # try to look up the resolved entity in KB
            rec = self.kb.resolve(resolved) or self.kb.resolve(resolved.replace("-", "_"))
            return resolved, rec, dis

        # fall back to regular resolution
        canonical, rec = self.resolve(name)
        return canonical, rec, None

    def normalize_used_for_item(self, item: str) -> str:
        """Normalize a single used_for phrase to canonical KB phrasing."""
        REPLACEMENTS = {
            "vegetation":                 "vegetation analysis",
            "floods":                     "flood mapping",
            "flood_detection":            "flood detection",
            "flood_modelling":            "flood modelling",
            "flood_modeling":             "flood modelling",
            "topography":                 "terrain analysis",
            "topographic_mapping":        "terrain analysis",
            "terrain":                    "terrain analysis",
            "precipitation":              "precipitation estimation",
            "drought_monitoring":         "drought monitoring",
            "drought":                    "drought monitoring",
            "ET_estimation":              "evapotranspiration estimation",
            "LST":                        "land surface temperature retrieval",
            "climate":                    "climate analysis",
            "climate_modelling":          "climate modelling",
            "climate_modeling":           "climate modelling",
            "hydrological_modeling":      "hydrological modelling",
            "hydraulic_modeling":         "hydraulic modelling",
        }
        cleaned = item.strip()
        return REPLACEMENTS.get(cleaned, REPLACEMENTS.get(cleaned.lower(), cleaned))

    def enrich_entity(self, entity: dict) -> dict:
        """
        Attach KB metadata to a detected entity dict.

        Input entity must have at least {"name": "..."}.
        Returns entity with added fields from KB.
        """
        name = entity.get("name", "")
        canonical, rec = self.resolve(name)
        entity["name"] = canonical

        if rec:
            entity.setdefault("kb_metadata", {
                "full_name":  rec.full_name,
                "type":       rec.type,
                "type_group": rec.type_group,
                "domain":     rec.domain,
                "definition": rec.definition,
                "used_for":   rec.used_for,
                "related":    rec.related,
                "contexts":   rec.contexts,
            })

        return entity
