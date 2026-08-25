"""
normalization_utils.py  —  GeoHydroAI ontology normalisation utilities
=======================================================================

Canonical home for the three normalisation functions shared by:
    - src/orchestration/process_paper.py   (Stage 1 Ray task)
    - src/orchestration/normalization_runner.py (Stage 2 backfill CLI)

DO NOT duplicate these functions.  Import from here only.

Entity-name extraction
----------------------
Paper JSON entity lists contain dicts (not raw strings):
    methods/satellites/dems → {"name": ..., "scores": ..., ...}
    metrics                  → {"type": ..., "value": ..., ...}

This module extracts the normalizable name correctly from each shape.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Optional

from src.ingestion.utils import json_safe

log = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Entity field → ontology type mapping
# ─────────────────────────────────────────────────────────────────────────────

ENTITY_FIELD_TYPES: dict[str, Optional[str]] = {
    "methods":       "method",
    "satellites":    "sensor",
    "dems":          "data",
    "sensors":       "sensor",
    "metrics":       "metric",
    "datasets":      "data",
    "data_sources":  "data",
    "indices":       "data",
    "models":        "method",
    "algorithms":    "method",
    # v2 ontology fields
    "tasks":         "task",
    "equations":     "equation",
    "uncertainties": "uncertainty",
    "couplings":     "method",
}

# Metrics use 'type' as the entity name; all other entity dicts use 'name'
_METRIC_FIELD = "metrics"


# ─────────────────────────────────────────────────────────────────────────────
# Entity-name extraction (handles both str and dict shapes)
# ─────────────────────────────────────────────────────────────────────────────

def _extract_entity_name(entity: Any, field: str) -> Optional[str]:
    """
    Pull the normalizable text label from any entity shape.

    Pipeline entities are always dicts:
        - methods / satellites / dems → entity["name"]
        - metrics                     → entity["type"]

    Plain strings are also accepted for forward compatibility.
    """
    if isinstance(entity, str):
        return entity.strip() or None
    if isinstance(entity, dict):
        key = "type" if field == _METRIC_FIELD else "name"
        val = entity.get(key, "")
        return val.strip() if isinstance(val, str) and val.strip() else None
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Core normalisation functions
# ─────────────────────────────────────────────────────────────────────────────

def normalize_paper_entities(paper: dict) -> dict:
    """
    Run ontology normalisation over all recognised entity fields in paper.

    Adds paper["normalized_entities"] — a dict mapping field_name →
    list of NormalizedEntity-compatible dicts.  Raw entity lists are
    preserved unchanged.

    Handles both string and dict entity shapes (see _extract_entity_name).
    Never raises: per-entity failures are recorded as match_type="error".

    Args:
        paper: Paper dict (loaded from *.paper.json or built in-memory).

    Returns:
        The same paper dict, mutated in place.
    """
    try:
        from src.normalization.ontology_matcher import normalize_entity
    except Exception as exc:
        log.warning("[normalize] import failed: %s", exc)
        return paper

    entities   = paper.get("entities", {})
    normalized: dict[str, list[dict]] = {}
    unmatched:  list[str] = []

    # Build a paper-level context string for disambiguation.
    # abstract + methods covers the sections where ambiguous acronyms
    # (SCS, ANN, RRI, VIC, …) appear with their disambiguating keywords.
    sections = paper.get("sections", {})
    _ctx_parts = [
        sections.get("abstract", "") if isinstance(sections, dict) else "",
        sections.get("methods", "") if isinstance(sections, dict) else "",
        sections.get("introduction", "") if isinstance(sections, dict) else "",
    ]
    _dis_context: str = " ".join(p for p in _ctx_parts if p)[:2000]

    for field, expected_type in ENTITY_FIELD_TYPES.items():
        raw_list = entities.get(field, [])
        if not isinstance(raw_list, list):
            raw_list = [raw_list] if raw_list else []

        results: list[dict] = []
        for raw_entity in raw_list:
            raw_name = _extract_entity_name(raw_entity, field)
            if not raw_name:
                continue

            try:
                result = normalize_entity(
                    raw_name,
                    expected_type=expected_type,
                    context=_dis_context or None,
                )
            except Exception as exc:
                log.debug("[normalize] error on %r: %s", raw_name, exc)
                result = {
                    "raw_name":     raw_name,
                    "canonical_id": None,
                    "display_name": None,
                    "type":         None,
                    "match_type":   "error",
                    "confidence":   0.0,
                }

            result["source_field"] = field   # required by NormalizedEntity schema
            results.append(result)

            if result.get("match_type") == "unknown":
                unmatched.append(raw_name)

        if results:
            normalized[field] = results

    paper["normalized_entities"] = normalized
    if unmatched:
        paper.setdefault("provenance", {})["unmatched_entities"] = unmatched

    log.debug(
        "[normalize] %s: %d fields, %d total results, %d unmatched",
        paper.get("metadata", {}).get("paper_id", "?"),
        len(normalized), sum(len(v) for v in normalized.values()), len(unmatched),
    )
    return paper


def validate_normalized_schema(paper: dict) -> bool:
    """
    Validate paper against NormalizedPaper Pydantic schema.

    Sets paper["provenance"]["normalized_schema_valid"] to True/False.
    Never raises — always returns a bool.

    Args:
        paper: Paper dict after normalize_paper_entities() has run.

    Returns:
        True if schema-valid, False otherwise.
    """
    try:
        from src.schemas.normalized_paper import validate_paper_dict
        ok, msg = validate_paper_dict(paper)
        paper.setdefault("provenance", {})["normalized_schema_valid"] = ok
        if not ok:
            log.warning(
                "[schema] %s failed validation: %s",
                paper.get("metadata", {}).get("paper_id", "?"), msg,
            )
        return ok
    except Exception as exc:
        log.warning("[schema] validation runtime error: %s", exc)
        paper.setdefault("provenance", {})["normalized_schema_valid"] = False
        return False


def derive_output_path(paper: dict, output_dir: Path, source_path: Optional[Path] = None) -> Path:
    """
    Determine the canonical output path for a normalized paper.

    Priority:
        1. paper["metadata"]["paper_id"] if present
        2. source_path stem with common suffixes stripped
        3. "unknown"

    Args:
        paper:       Paper dict.
        output_dir:  Target directory (e.g. data/normalized/).
        source_path: Optional path to the source *.paper.json file.

    Returns:
        Path: output_dir / "{paper_id}.json"
    """
    paper_id: str = paper.get("metadata", {}).get("paper_id", "")

    if not paper_id and source_path is not None:
        # Strip .tei.paper.json / .paper.json / .tei.json / .json suffixes
        stem = source_path.name
        for suffix in (".tei.paper.json", ".paper.json", ".tei.json", ".json"):
            if stem.endswith(suffix):
                stem = stem[: -len(suffix)]
                break
        paper_id = stem

    paper_id = (paper_id or "unknown").strip()
    return output_dir / f"{paper_id}.json"


def save_normalized(
    paper: dict,
    output_dir: Path,
    source_path: Optional[Path] = None,
) -> Path:
    """
    Write the normalized paper to output_dir/{paper_id}.json.

    Args:
        paper:       Paper dict to serialize.
        output_dir:  Target directory.
        source_path: Optional hint for deriving the filename.

    Returns:
        Path to the written file.

    Raises:
        OSError: If the file cannot be written (caller decides how to handle).
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    out = derive_output_path(paper, output_dir, source_path)
    out.write_text(json.dumps(json_safe(paper), ensure_ascii=False, indent=2), encoding="utf-8")
    return out
