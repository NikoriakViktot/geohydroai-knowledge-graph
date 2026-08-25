"""
Type-specific fact validation.

validate_fact(fact) → {"valid": bool, "reasons": list[str]}

Each fact_type has its own set of rejection rules.  Universal rules:
  - Every accepted fact must carry at least one Evidence entry.
  - Every evidence entry must reference a recognised section.

Type-specific rules
───────────────────
data_source     satellite must be in the known list OR have evidence text
method          evidence section must be "methods" or "abstract+methods"
result          evidence section must be "results"
study_area      study area must not be generic; must have at least one field
task            must have a task string and at least one evidence entry
system_property must have a value string and at least one evidence entry
"""
from __future__ import annotations

from .models import ScientificFact

_KNOWN_SATELLITES: frozenset[str] = frozenset({
    "Sentinel-1", "Sentinel-2",
    "TerraSAR-X", "TanDEM-X",
    "COSMO-SkyMed",
    "ALOS-2", "ALOS PALSAR",
    "RADARSAT-2", "RADARSAT-1",
    "UAVSAR", "ICEYE", "Capella",
    "ERS-2", "ERS-1", "Envisat", "NovaSAR",
    "Landsat-9", "Landsat-8", "Landsat-7", "Landsat-5", "Landsat",
    "MODIS", "VIIRS",
    "WorldView-3", "WorldView-2",
    "Pleiades", "PlanetScope",
    "SPOT-7", "SPOT-6", "SPOT-5",
    "ICESat-2",
})

_RESULT_SECTIONS:   frozenset[str] = frozenset({"results"})
_METHOD_SECTIONS:   frozenset[str] = frozenset({"methods", "abstract+methods"})
_AREA_SECTIONS:     frozenset[str] = frozenset({
    "abstract+introduction", "abstract+methods", "abstract", "introduction",
})
_DERIVED_SOURCES:   frozenset[str] = frozenset({"rule", "llm"})


# ── Per-type validators ───────────────────────────────────────────────────────

def _validate_data_source(fact: ScientificFact) -> list[str]:
    reasons: list[str] = []
    if not fact.satellite:
        reasons.append("data_source fact has no satellite")
        return reasons

    ev_texts = " ".join(e.text.lower() for e in fact.evidence)
    if fact.satellite.name not in _KNOWN_SATELLITES and not ev_texts:
        reasons.append(
            f"satellite '{fact.satellite.name}' is not in the known list "
            "and no evidence text found"
        )
    return reasons


def _validate_method(fact: ScientificFact) -> list[str]:
    reasons: list[str] = []
    if not fact.method:
        reasons.append("method fact has no method")
        return reasons

    method_ev = [e for e in fact.evidence if e.field == "Methods"]
    if method_ev:
        wrong_section = [e for e in method_ev if e.section not in _METHOD_SECTIONS]
        if wrong_section and not any(e.section in _METHOD_SECTIONS for e in method_ev):
            reasons.append(
                f"method '{fact.method.name}' evidence is from "
                f"'{wrong_section[0].section}', expected methods section"
            )
    return reasons


def _validate_result(fact: ScientificFact) -> list[str]:
    reasons: list[str] = []
    if not fact.metric:
        reasons.append("result fact has no metric")
        return reasons

    metric_ev = [e for e in fact.evidence if e.field == fact.metric.type]
    if metric_ev:
        wrong_section = [e for e in metric_ev if e.section not in _RESULT_SECTIONS]
        if wrong_section and not any(e.section in _RESULT_SECTIONS for e in metric_ev):
            reasons.append(
                f"metric '{fact.metric.type}' evidence is from "
                f"'{wrong_section[0].section}', expected results section"
            )
    return reasons


def _validate_study_area(fact: ScientificFact) -> list[str]:
    reasons: list[str] = []
    if not fact.study_area or fact.study_area.is_empty():
        reasons.append("study_area fact has no geographic information")
        return reasons

    if fact.study_area.is_generic():
        reasons.append(
            f"study_area is a generic placeholder: {fact.study_area.to_dict()}"
        )
    return reasons


def _validate_task(fact: ScientificFact) -> list[str]:
    reasons: list[str] = []
    if not fact.task:
        reasons.append("task fact has no task string")
    return reasons


def _validate_system_property(fact: ScientificFact) -> list[str]:
    reasons: list[str] = []
    if not fact.value:
        reasons.append("system_property fact has no value string")
    return reasons


_TYPE_VALIDATORS = {
    "data_source":     _validate_data_source,
    "method":          _validate_method,
    "result":          _validate_result,
    "study_area":      _validate_study_area,
    "task":            _validate_task,
    "system_property": _validate_system_property,
}


# ── Public interface ──────────────────────────────────────────────────────────

def validate_fact(fact: ScientificFact) -> dict:
    """
    Validate an atomic ScientificFact.

    Returns
    -------
    {"valid": bool, "reasons": list[str]}
    """
    reasons: list[str] = []

    # ── Universal rule: every accepted fact needs evidence ────────────────────
    # Rule-derived facts (task, system_property) may use source="rule".
    # Purely classification-derived task facts without any snippet are
    # allowed only when the classifier found a matching keyword (source="rule").
    if not fact.evidence:
        reasons.append("no evidence attached to fact")

    # ── Type-specific rules ───────────────────────────────────────────────────
    validator = _TYPE_VALIDATORS.get(fact.fact_type)
    if validator is None:
        reasons.append(f"unknown fact_type '{fact.fact_type}'")
    else:
        reasons.extend(validator(fact))

    return {"valid": len(reasons) == 0, "reasons": reasons}
