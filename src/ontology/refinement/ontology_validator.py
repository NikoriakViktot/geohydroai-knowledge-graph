"""
ontology_validator.py  —  GeoHydroAI ontology structural validator
===================================================================

Checks the refined ontology registry for structural and semantic integrity:

    V01  Missing required fields        (id, display_name, type, aliases)
    V02  Canonical ID format violations ({type_prefix}.{slug} pattern)
    V03  Type/ID prefix mismatches      (method.X must have type=method)
    V04  Orphaned parent_id references  (parent_id → non-existent entity)
    V05  Empty display_name
    V06  Alias list containing the canonical ID itself (redundant)
    V07  Generic entity without is_generic flag (by GENERIC_ENTITIES list)
    V08  Duplicate display_name across entities of the same type

Writes results to data/ontology_qa/ontology_validation.json.
Exits with code 1 if any ERROR-level violations are found.

Usage:
    python -m src.ontology.refinement.ontology_validator
    python -m src.ontology.refinement.ontology_validator --strict  # also warn on WARNING
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

_ONTOLOGY_DIR = Path(__file__).resolve().parents[3] / "data" / "ontology"
_QA_DIR       = Path(__file__).resolve().parents[3] / "data" / "ontology_qa"

_REQUIRED_FIELDS = ("id", "display_name", "type", "aliases")
_VALID_TYPES     = {
    "method", "sensor", "metric", "data", "concept",
    "organization", "system", "parameter", "uncertainty",
}
_PREFIX_TO_TYPE  = {
    "method":      "method",
    "sensor":      "sensor",
    "metric":      "metric",
    "data":        "data",
    "concept":     "concept",
    "org":         "organization",
    "system":      "system",
    "parameter":   "parameter",
    "uncertainty": "uncertainty",
}
_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$")


# ─────────────────────────────────────────────────────────────────────────────
# Violation record
# ─────────────────────────────────────────────────────────────────────────────

def _violation(code: str, level: str, entity_id: str, message: str) -> dict:
    return {"code": code, "level": level, "entity_id": entity_id, "message": message}


# ─────────────────────────────────────────────────────────────────────────────
# Individual checks
# ─────────────────────────────────────────────────────────────────────────────

def _check_required_fields(eid: str, ent: dict) -> list[dict]:
    violations = []
    for field in _REQUIRED_FIELDS:
        if field not in ent or ent[field] in (None, "", []):
            violations.append(_violation(
                "V01", "ERROR", eid,
                f"Missing or empty required field: '{field}'",
            ))
    return violations


def _check_id_format(eid: str, ent: dict) -> list[dict]:
    if not _ID_PATTERN.match(eid):
        return [_violation(
            "V02", "ERROR", eid,
            f"Canonical ID does not match pattern '{{prefix}}.{{slug}}': '{eid}'",
        )]
    return []


def _check_type_prefix(eid: str, ent: dict) -> list[dict]:
    prefix = eid.split(".")[0]
    expected_type = _PREFIX_TO_TYPE.get(prefix)
    if expected_type is None:
        return [_violation(
            "V03", "WARNING", eid,
            f"Unknown ID prefix '{prefix}' — no type mapping defined",
        )]
    actual = ent.get("type")
    if actual != expected_type:
        return [_violation(
            "V03", "ERROR", eid,
            f"Type mismatch: ID prefix '{prefix}' implies type='{expected_type}' "
            f"but entity has type='{actual}'",
        )]
    return []


def _check_orphaned_parents(eid: str, ent: dict, all_ids: set[str]) -> list[dict]:
    parent = ent.get("parent_id")
    if parent and parent not in all_ids:
        return [_violation(
            "V04", "ERROR", eid,
            f"parent_id '{parent}' does not exist in registry",
        )]
    return []


def _check_empty_display_name(eid: str, ent: dict) -> list[dict]:
    name = ent.get("display_name", "")
    if not name or not name.strip():
        return [_violation(
            "V05", "ERROR", eid,
            "display_name is empty or whitespace-only",
        )]
    return []


def _check_self_alias(eid: str, ent: dict) -> list[dict]:
    from src.normalization.alias_resolver import _slugify as slugify
    id_slug = slugify(eid)
    name_slug = slugify(ent.get("display_name", ""))
    violations = []
    for alias in ent.get("aliases", []):
        if slugify(alias) == id_slug:
            violations.append(_violation(
                "V06", "WARNING", eid,
                f"Alias '{alias}' is redundant (same slug as canonical ID)",
            ))
        elif slugify(alias) == name_slug:
            violations.append(_violation(
                "V06", "WARNING", eid,
                f"Alias '{alias}' is redundant (same slug as display_name)",
            ))
    return violations


def _check_generic_flags(eid: str, ent: dict, generic_ids: set[str]) -> list[dict]:
    if eid in generic_ids and not ent.get("is_generic"):
        return [_violation(
            "V07", "WARNING", eid,
            "Entity is in GENERIC_ENTITIES but is_generic flag is not set",
        )]
    return []


def _check_duplicate_display_names(registry: dict[str, dict]) -> list[dict]:
    from collections import defaultdict
    seen: dict[str, list[str]] = defaultdict(list)
    for eid, ent in registry.items():
        key = (ent.get("type", ""), (ent.get("display_name") or "").strip().lower())
        seen[str(key)].append(eid)

    violations = []
    for key_str, eids in seen.items():
        if len(eids) > 1:
            for eid in eids:
                violations.append(_violation(
                    "V08", "WARNING", eid,
                    f"Duplicate display_name within same type: shared with {[e for e in eids if e != eid]}",
                ))
    return violations


# ─────────────────────────────────────────────────────────────────────────────
# Main validation runner
# ─────────────────────────────────────────────────────────────────────────────

def validate_registry(registry: dict[str, dict]) -> list[dict]:
    """
    Run all checks against the registry.

    Returns:
        List of violation dicts, sorted by level (ERROR first) then entity_id.
    """
    from src.ontology.refinement.hierarchy_builder import GENERIC_ENTITIES

    all_ids     = set(registry.keys())
    generic_ids = set(GENERIC_ENTITIES.keys())
    violations: list[dict] = []

    for eid, ent in registry.items():
        violations += _check_required_fields(eid, ent)
        violations += _check_id_format(eid, ent)
        violations += _check_type_prefix(eid, ent)
        violations += _check_orphaned_parents(eid, ent, all_ids)
        violations += _check_empty_display_name(eid, ent)
        violations += _check_self_alias(eid, ent)
        violations += _check_generic_flags(eid, ent, generic_ids)

    violations += _check_duplicate_display_names(registry)

    # Sort: ERROR first, then WARNING, then by entity_id
    level_order = {"ERROR": 0, "WARNING": 1}
    violations.sort(key=lambda v: (level_order.get(v["level"], 9), v["entity_id"]))
    return violations


def run_validation(registry: dict[str, dict] | None = None) -> dict:
    """
    Load registry, validate, write report, return summary.
    """
    if registry is None:
        reg_path = _ONTOLOGY_DIR / "ontology_registry.json"
        if not reg_path.exists():
            raise FileNotFoundError(f"Registry not found: {reg_path}")
        registry = json.loads(reg_path.read_text(encoding="utf-8"))

    violations = validate_registry(registry)

    errors   = [v for v in violations if v["level"] == "ERROR"]
    warnings = [v for v in violations if v["level"] == "WARNING"]

    summary = {
        "generated_at":   datetime.now(timezone.utc).isoformat(),
        "entity_count":   len(registry),
        "error_count":    len(errors),
        "warning_count":  len(warnings),
        "violations":     violations,
    }

    _QA_DIR.mkdir(parents=True, exist_ok=True)
    out = _QA_DIR / "ontology_validation.json"
    out.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    log.info("Validation report: %s", out)

    return summary


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def _main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    parser = argparse.ArgumentParser(description="GeoHydroAI ontology validator")
    parser.add_argument(
        "--strict", action="store_true",
        help="Exit 1 if any WARNING-level violations found (default: only ERROR)",
    )
    args = parser.parse_args()

    summary = run_validation()
    errors   = summary["error_count"]
    warnings = summary["warning_count"]

    print(f"\nValidation complete: {summary['entity_count']} entities")
    print(f"  Errors  : {errors}")
    print(f"  Warnings: {warnings}")

    for v in summary["violations"][:30]:
        tag = f"[{v['level']}][{v['code']}]"
        print(f"  {tag:20s} {v['entity_id']:40s} {v['message']}")

    if len(summary["violations"]) > 30:
        print(f"  ... and {len(summary['violations']) - 30} more. See ontology_validation.json")

    fail = errors > 0 or (args.strict and warnings > 0)
    sys.exit(1 if fail else 0)


if __name__ == "__main__":
    _main()
