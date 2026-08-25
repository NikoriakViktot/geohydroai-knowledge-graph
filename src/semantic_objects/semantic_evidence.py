"""
semantic_evidence.py — Core domain types for Stage 2.5 semantic validation.

Layer hierarchy:
  EvidenceTrace      — one atomic inference step with full provenance
  ObjectContext      — read-only view: Stage1 row + Stage2 row + section context
  SemanticAnnotation — mutable semantic output accumulated across validators
  SemanticEdge       — typed directed edge between two annotated objects
  Stage25Result      — output contract of SemanticValidatorStage.run()

Design rules:
  - No parsing, no regex here — this module is pure data contracts.
  - SemanticAnnotation is intentionally mutable (built incrementally).
  - EvidenceTrace and SemanticEdge are frozen (immutable once created).
  - All list fields are serialised as JSON strings in to_row() for Parquet.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# ── Scientific role vocabulary ────────────────────────────────────────────────

class ScientificRole:
    MODEL_INPUT          = "model_input"
    MODEL_OUTPUT         = "model_output"
    MODEL_CALIBRATION    = "model_calibration"
    MODEL_VALIDATION     = "model_validation"
    MODEL_COMPARISON     = "model_comparison"
    TIMESERIES_VIZ       = "timeseries_visualization"
    SPATIAL_OUTPUT       = "spatial_output"
    EVALUATION_VIZ       = "evaluation_visualization"
    UNCERTAINTY_VIZ      = "uncertainty_visualization"
    STUDY_AREA_DEF       = "study_area_definition"
    DATA_DESCRIPTION     = "data_description"
    METHODOLOGY          = "methodology"
    EVALUATION           = "evaluation"
    VALIDATION           = "validation"
    SENSITIVITY          = "sensitivity_analysis"
    UNCERTAINTY_QUANT    = "uncertainty_quantification"
    REMOTE_SENSING_INPUT = "remote_sensing_input"
    RS_CLASSIFICATION    = "remote_sensing_classification"
    FLOOD_RISK           = "flood_risk"
    OBSERVED_VS_SIM      = "observed_vs_simulated"
    OBJECTIVE_FUNCTION   = "objective_function"
    PHYSICAL_LAW         = "physical_law"


# ── Hydrological task vocabulary ──────────────────────────────────────────────

class HydroTask:
    RAINFALL_RUNOFF     = "rainfall_runoff_modeling"
    STREAMFLOW_PRED     = "streamflow_prediction"
    FLOOD_INUNDATION    = "flood_inundation_mapping"
    FLOOD_FORECASTING   = "flood_forecasting"
    GROUNDWATER         = "groundwater_modeling"
    WATER_QUALITY       = "water_quality"
    DROUGHT_MONITORING  = "drought_monitoring"
    SEDIMENT_TRANSPORT  = "sediment_transport"
    EVAPOTRANSPIRATION  = "evapotranspiration_modeling"
    DEM_ANALYSIS        = "dem_analysis"
    LULC_CLASSIFICATION = "lulc_classification"
    HYDRAULIC_MODELING  = "hydraulic_modeling"
    UNCERTAINTY_ANALYSIS= "uncertainty_analysis"


# ── Domain vocabulary ─────────────────────────────────────────────────────────

class HydroDomain:
    HYDROLOGY        = "hydrology"
    HYDRODYNAMICS    = "hydrodynamics"
    REMOTE_SENSING   = "remote_sensing"
    STATISTICS       = "statistics"
    HYDRAULICS       = "hydraulics"
    HYDROGEOLOGY     = "hydrogeology"
    CLIMATOLOGY      = "climatology"


# ── Evidence provenance ───────────────────────────────────────────────────────

@dataclass(frozen=True)
class EvidenceTrace:
    """
    One atomic inference step linking a rule to observed signals.

    Stored as JSON in semantic_annotations.parquet so provenance is
    human-readable and survives pipeline version upgrades.
    """
    rule_triggered:   str              # rule ID, e.g. "R01_hydrograph_in_results"
    evidence_source:  str              # "caption" | "label" | "section_context" |
                                       # "cross_object" | "pattern" | "constraint"
    matched_terms:    tuple[str, ...]  # the actual strings that triggered the rule
    section:          str | None       # section title where signal was found
    confidence_delta: float            # amount added to semantic_confidence

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_triggered":   self.rule_triggered,
            "evidence_source":  self.evidence_source,
            "matched_terms":    list(self.matched_terms),
            "section":          self.section,
            "confidence_delta": round(self.confidence_delta, 4),
        }


# ── Object context ────────────────────────────────────────────────────────────

@dataclass
class ObjectContext:
    """
    Read-only contextual view assembled by ContextBuilder before validation.

    Combines the Stage 1 structural row, the Stage 2 semantic_type row,
    the nearest section's text, and the resolved caption.
    """
    parsed_row:        dict[str, Any]       # Stage 1 row (figures/tables/etc)
    sci_object:        dict[str, Any] | None # Stage 2 scientific_objects row
    section_title:     str | None           # closest containing section title
    section_text:      str | None           # text of that section (truncated)
    caption:           str | None           # resolved caption text
    label:             str | None           # object label ("Figure 1", "Table 2")
    all_section_titles:list[str]            # all section titles in paper
    paper_id:          str


# ── Semantic annotation ───────────────────────────────────────────────────────

@dataclass
class SemanticAnnotation:
    """
    Accumulated semantic output for a single scientific object.

    Validators and the reasoner mutate this in-place; it is serialised
    once at the end of Stage 2.5 via to_row().
    """
    object_id:   str
    paper_id:    str
    object_type: str   # "figure" | "table" | "equation" | "section" | "reference"
    semantic_type: str

    scientific_role:    list[str] = field(default_factory=list)

    # Fine-grained hydrological content flags
    contains_discharge:        bool | None = None
    contains_water_level:      bool | None = None
    contains_uncertainty_band: bool | None = None
    contains_flood_extent:     bool | None = None
    contains_timeseries:       bool | None = None
    contains_spatial_data:     bool | None = None
    contains_remote_sensing:   bool | None = None

    # Semantic candidates
    likely_models:     list[str] = field(default_factory=list)
    candidate_tasks:   list[str] = field(default_factory=list)
    candidate_metrics: list[str] = field(default_factory=list)
    domain:            str | None = None

    # Confidence
    evidence_strength:  float = 0.0   # fraction of expected signals that fired
    semantic_confidence:float = 0.0   # accumulated from classifier + rule deltas

    # Traceability
    evidence_trace:       list[EvidenceTrace] = field(default_factory=list)
    constraint_violations:list[str]           = field(default_factory=list)

    # ── Accumulation helpers ──────────────────────────────────────────────────

    def add_role(self, role: str) -> None:
        if role not in self.scientific_role:
            self.scientific_role.append(role)

    def add_task(self, task: str) -> None:
        if task not in self.candidate_tasks:
            self.candidate_tasks.append(task)

    def add_model(self, model: str) -> None:
        canon = model.upper()
        if canon not in self.likely_models:
            self.likely_models.append(canon)

    def add_metric(self, metric: str) -> None:
        canon = metric.upper()
        if canon not in self.candidate_metrics:
            self.candidate_metrics.append(canon)

    def add_trace(self, trace: EvidenceTrace) -> None:
        """Append a trace and propagate its confidence delta."""
        self.evidence_trace.append(trace)
        self.semantic_confidence = min(1.0, self.semantic_confidence + trace.confidence_delta)

    # ── Serialisation ─────────────────────────────────────────────────────────

    def to_row(self) -> dict[str, Any]:
        return {
            "annotation_id":           f"{self.paper_id}_ann_{self.object_id}",
            "object_id":               self.object_id,
            "paper_id":                self.paper_id,
            "object_type":             self.object_type,
            "semantic_type":           self.semantic_type,
            "scientific_role":         json.dumps(self.scientific_role),
            "contains_discharge":      self.contains_discharge,
            "contains_water_level":    self.contains_water_level,
            "contains_uncertainty_band":self.contains_uncertainty_band,
            "contains_flood_extent":   self.contains_flood_extent,
            "contains_timeseries":     self.contains_timeseries,
            "contains_spatial_data":   self.contains_spatial_data,
            "contains_remote_sensing": self.contains_remote_sensing,
            "likely_models":           json.dumps(self.likely_models),
            "candidate_tasks":         json.dumps(self.candidate_tasks),
            "candidate_metrics":       json.dumps(self.candidate_metrics),
            "domain":                  self.domain,
            "evidence_strength":       round(self.evidence_strength, 4),
            "semantic_confidence":     round(self.semantic_confidence, 4),
            "evidence_trace":          json.dumps([t.to_dict() for t in self.evidence_trace]),
            "constraint_violations":   json.dumps(self.constraint_violations),
        }


# ── Semantic graph edge ───────────────────────────────────────────────────────

@dataclass(frozen=True)
class SemanticEdge:
    """
    Typed directed relationship between two annotated scientific objects.

    Relations (beyond the structural FOLLOWS/IN_SECTION from Stage 2):
      EVALUATES   — metrics table evaluates a model/method mentioned in context
      VISUALIZES  — figure visualizes the output of a results section / equation
      VALIDATES   — metrics table validates a model's performance claim
      SUPPORTS    — object provides evidence for a conclusion/claim
      DERIVED_FROM— figure/table derived from equation or another object
    """
    edge_id:    str
    paper_id:   str
    source_id:  str    # object_id of source semantic annotation
    target_id:  str    # object_id of target semantic annotation
    relation:   str    # EVALUATES | VISUALIZES | VALIDATES | SUPPORTS | DERIVED_FROM
    confidence: float | None = None
    evidence:   str | None   = None    # brief human-readable rationale

    def to_row(self) -> dict[str, Any]:
        return {
            "edge_id":    self.edge_id,
            "paper_id":   self.paper_id,
            "source_id":  self.source_id,
            "target_id":  self.target_id,
            "relation":   self.relation,
            "confidence": self.confidence,
            "evidence":   self.evidence,
        }


# ── Stage result ──────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Stage25Result:
    """Output contract of SemanticValidatorStage.run()."""
    paper_id:         str
    status:           str       # "ok" | "skipped" | "error"
    sodb_root:        Path
    annotation_count: int = 0
    edge_count:       int = 0
    events:           list[str] = field(default_factory=list)

    @property
    def should_continue(self) -> bool:
        return self.status == "ok"
