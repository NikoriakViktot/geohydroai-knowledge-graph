"""
semantic_constraints.py — Semantic consistency rules for Stage 2.5.

A constraint violation does NOT block the annotation; it:
  1. Records the violation in SemanticAnnotation.constraint_violations
  2. Applies a confidence penalty (-0.10 per violation)
  3. Removes the conflicting role when the conflict is structural

Constraint types:
  ROLE_EXCLUSION  — two roles cannot coexist on the same object
  DOMAIN_LOCK     — semantic_type implies a domain; contradicting domain is flagged
  METRIC_IMPLIES  — presence of metric implies a required role
  TYPE_DOMAIN     — semantic_type forces a known domain
"""
from __future__ import annotations

from src.document.scientific_objects import SemanticType
from src.semantic_objects.semantic_evidence import (
    EvidenceTrace,
    HydroDomain,
    ScientificRole,
    SemanticAnnotation,
)

_CONFIDENCE_PENALTY = 0.10


# ── Role exclusion pairs ──────────────────────────────────────────────────────
# If role A is present, role B must NOT be.
# Listed once; the check is symmetric.

_ROLE_EXCLUSIONS: list[tuple[str, str]] = [
    (ScientificRole.MODEL_OUTPUT,       "image_segmentation"),
    (ScientificRole.FLOOD_RISK,         "water_quality"),
    (ScientificRole.REMOTE_SENSING_INPUT, ScientificRole.PHYSICAL_LAW),
    (ScientificRole.METHODOLOGY,        ScientificRole.MODEL_OUTPUT),
    (ScientificRole.STUDY_AREA_DEF,     ScientificRole.EVALUATION),
    (ScientificRole.UNCERTAINTY_VIZ,    ScientificRole.STUDY_AREA_DEF),
]

# ── Semantic_type → required domain ──────────────────────────────────────────

_TYPE_DOMAIN: dict[str, str] = {
    SemanticType.HYDROGRAPH:       HydroDomain.HYDROLOGY,
    SemanticType.FLOOD_EXTENT_MAP: HydroDomain.HYDROLOGY,
    SemanticType.WATERSHED_MAP:    HydroDomain.HYDROLOGY,
    SemanticType.SCATTER_PLOT:     HydroDomain.STATISTICS,
    SemanticType.CALIBRATION_PLOT: HydroDomain.HYDROLOGY,
    SemanticType.SATELLITE_IMAGE:  HydroDomain.REMOTE_SENSING,
    SemanticType.OBJECTIVE_FUNCTION: HydroDomain.STATISTICS,
    SemanticType.PHYSICAL_EQUATION:  HydroDomain.HYDRAULICS,
    SemanticType.METRICS_TABLE:      HydroDomain.STATISTICS,
}

# ── Metric → implied roles ────────────────────────────────────────────────────
# If the annotation has this metric in candidate_metrics, these roles MUST be present.

_METRIC_IMPLIES_ROLE: dict[str, list[str]] = {
    "NSE":   [ScientificRole.EVALUATION, ScientificRole.MODEL_VALIDATION],
    "KGE":   [ScientificRole.EVALUATION, ScientificRole.MODEL_VALIDATION],
    "RMSE":  [ScientificRole.EVALUATION],
    "MAE":   [ScientificRole.EVALUATION],
    "PBIAS": [ScientificRole.EVALUATION],
}

# ── Semantic_type → forbidden roles ──────────────────────────────────────────

_TYPE_FORBIDDEN_ROLES: dict[str, set[str]] = {
    SemanticType.SATELLITE_IMAGE: {
        ScientificRole.MODEL_OUTPUT,
        ScientificRole.PHYSICAL_LAW,
        ScientificRole.OBJECTIVE_FUNCTION,
    },
    SemanticType.WATERSHED_MAP: {
        ScientificRole.MODEL_OUTPUT,
        ScientificRole.EVALUATION,
        ScientificRole.OBJECTIVE_FUNCTION,
    },
    SemanticType.FLOWCHART: {
        ScientificRole.MODEL_OUTPUT,
        ScientificRole.EVALUATION_VIZ,
        ScientificRole.UNCERTAINTY_VIZ,
    },
}


# ── Public API ────────────────────────────────────────────────────────────────

def apply_constraints(ann: SemanticAnnotation) -> None:
    """
    Check and enforce all semantic constraints in-place.

    Records violations in ann.constraint_violations.
    Applies a confidence penalty per violation.
    Enforces required roles from metric implications.
    """
    _check_role_exclusions(ann)
    _check_domain_lock(ann)
    _check_metric_implications(ann)
    _check_type_forbidden_roles(ann)


# ── Internal checks ───────────────────────────────────────────────────────────

def _check_role_exclusions(ann: SemanticAnnotation) -> None:
    role_set = set(ann.scientific_role)
    for role_a, role_b in _ROLE_EXCLUSIONS:
        if role_a in role_set and role_b in role_set:
            violation = f"ROLE_EXCLUSION:{role_a}↔{role_b}"
            if violation not in ann.constraint_violations:
                ann.constraint_violations.append(violation)
                ann.semantic_confidence = max(0.0, ann.semantic_confidence - _CONFIDENCE_PENALTY)
                ann.add_trace(EvidenceTrace(
                    rule_triggered="CONSTRAINT_ROLE_EXCLUSION",
                    evidence_source="constraint",
                    matched_terms=(role_a, role_b),
                    section=None,
                    confidence_delta=-_CONFIDENCE_PENALTY,
                ))


def _check_domain_lock(ann: SemanticAnnotation) -> None:
    implied = _TYPE_DOMAIN.get(ann.semantic_type)
    if implied is None:
        return
    if ann.domain and ann.domain != implied:
        violation = f"DOMAIN_MISMATCH:{ann.domain}≠{implied}"
        if violation not in ann.constraint_violations:
            ann.constraint_violations.append(violation)
            ann.semantic_confidence = max(0.0, ann.semantic_confidence - _CONFIDENCE_PENALTY)
            ann.add_trace(EvidenceTrace(
                rule_triggered="CONSTRAINT_DOMAIN_LOCK",
                evidence_source="constraint",
                matched_terms=(ann.semantic_type, ann.domain, implied),
                section=None,
                confidence_delta=-_CONFIDENCE_PENALTY,
            ))
    elif not ann.domain:
        ann.domain = implied


def _check_metric_implications(ann: SemanticAnnotation) -> None:
    for metric in ann.candidate_metrics:
        required = _METRIC_IMPLIES_ROLE.get(metric.upper(), [])
        for role in required:
            if role not in ann.scientific_role:
                ann.add_role(role)
                ann.add_trace(EvidenceTrace(
                    rule_triggered="CONSTRAINT_METRIC_IMPLIES_ROLE",
                    evidence_source="constraint",
                    matched_terms=(metric, role),
                    section=None,
                    confidence_delta=0.03,
                ))


def _check_type_forbidden_roles(ann: SemanticAnnotation) -> None:
    forbidden = _TYPE_FORBIDDEN_ROLES.get(ann.semantic_type, set())
    role_set   = set(ann.scientific_role)
    for role in forbidden & role_set:
        violation = f"TYPE_FORBIDDEN_ROLE:{ann.semantic_type}⊥{role}"
        if violation not in ann.constraint_violations:
            ann.constraint_violations.append(violation)
            ann.scientific_role.remove(role)
            ann.semantic_confidence = max(0.0, ann.semantic_confidence - _CONFIDENCE_PENALTY)
            ann.add_trace(EvidenceTrace(
                rule_triggered="CONSTRAINT_FORBIDDEN_ROLE",
                evidence_source="constraint",
                matched_terms=(ann.semantic_type, role),
                section=None,
                confidence_delta=-_CONFIDENCE_PENALTY,
            ))
