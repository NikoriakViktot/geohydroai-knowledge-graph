"""
scientific_objects.py — Stage 2 Scientific Object layer (SODB).

Canonical domain types for the object-centric view of a scientific paper.
Every structural element extracted in Stage 1 is promoted to a ScientificObject
with a semantic type, typed boolean flags, and optional candidate extractions.

Design rules:
  - No lxml, no NLP, no embeddings — pure data containers.
  - All types are frozen dataclasses (immutable after construction).
  - JSON-serialisable fields only (no numpy, no pyarrow).
  - SemanticType values are the stable vocabulary for downstream KG and UI.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# ── Semantic type vocabulary ──────────────────────────────────────────────────
# String constants rather than an enum so that new types can be added without
# breaking existing Parquet files that store the string value.

class SemanticType:
    # Tables
    METRICS_TABLE       = "metrics_table"        # NSE/KGE/RMSE performance rows
    COMPARISON_TABLE    = "comparison_table"     # model A vs model B
    PARAMETER_TABLE     = "parameter_table"      # calibration parameters
    STATION_TABLE       = "station_table"        # gauge/station metadata
    DATA_TABLE          = "data_table"           # generic tabular data
    # Figures
    HYDROGRAPH          = "hydrograph"           # time-series discharge plot
    FLOOD_EXTENT_MAP    = "flood_extent_map"     # spatial inundation map
    SCATTER_PLOT        = "scatter_plot"         # observed vs simulated scatter
    CALIBRATION_PLOT    = "calibration_plot"     # calibration curve
    SATELLITE_IMAGE     = "satellite_image"      # raw satellite imagery
    WATERSHED_MAP       = "watershed_map"        # basin / catchment boundary
    FLOWCHART           = "flowchart"            # methodology / workflow diagram
    BAR_CHART           = "bar_chart"            # grouped bar chart
    GENERIC_FIGURE      = "generic_figure"       # unclassified figure
    # Equations
    OBJECTIVE_FUNCTION  = "objective_function"   # NSE, KGE, RMSE definition
    PHYSICAL_EQUATION   = "physical_equation"    # mass/momentum/energy balance
    REGRESSION_FORMULA  = "regression_formula"   # statistical regression
    GENERIC_EQUATION    = "generic_equation"     # unclassified
    # Sections
    METHODS_SECTION     = "methods_section"
    RESULTS_SECTION     = "results_section"
    DATA_SECTION        = "data_section"
    STUDY_AREA_SECTION  = "study_area_section"
    INTRO_SECTION       = "intro_section"
    CONCLUSION_SECTION  = "conclusion_section"
    GENERIC_SECTION     = "generic_section"
    # References
    REFERENCE           = "reference"
    # Fallback
    UNKNOWN             = "unknown"


# ── Base object ───────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ScientificObject:
    """
    Base class for all Stage 2 scientific objects.

    Every object carries:
      - Identity: object_id, paper_id, object_type
      - Semantic classification: semantic_type, classifier_score, classifier_source
      - Typed boolean flags about domain content
      - Optional candidate extractions (JSON-serialisable lists stored as strings)
    """
    object_id:         str
    paper_id:          str
    object_type:       str          # "table" | "figure" | "equation" | "section" | "reference"
    semantic_type:     str          # SemanticType constant
    source_id:         str | None   # FK → sections/figures/etc. in parsed layer
    page:              int | None

    raw_text:          str | None
    classifier_score:  float | None
    classifier_source: str | None   # "rules" | "embedding" | "llm"

    # Typed flags
    contains_models:       bool | None = None
    contains_metrics:      bool | None = None
    contains_timeseries:   bool | None = None
    contains_coordinates:  bool | None = None
    contains_satellite:    bool | None = None
    contains_hydrograph:   bool | None = None

    # Candidate extractions (stored as JSON strings in parquet)
    candidate_models:  str | None = None   # JSON list of model name strings
    candidate_metrics: str | None = None   # JSON list of metric name strings
    candidate_vars:    str | None = None   # JSON list of variable names
    variables:         str | None = None   # equation variables
    related_models:    str | None = None   # JSON list
    domain:            str | None = None
    equation_name:     str | None = None

    def to_row(self) -> dict[str, Any]:
        """Convert to a parquet-compatible dict (no nested objects)."""
        return {
            "object_id":           self.object_id,
            "paper_id":            self.paper_id,
            "object_type":         self.object_type,
            "semantic_type":       self.semantic_type,
            "source_id":           self.source_id,
            "page":                self.page,
            "raw_text":            self.raw_text,
            "classifier_score":    self.classifier_score,
            "classifier_source":   self.classifier_source,
            "contains_models":     self.contains_models,
            "contains_metrics":    self.contains_metrics,
            "contains_timeseries": self.contains_timeseries,
            "contains_coordinates":self.contains_coordinates,
            "contains_satellite":  self.contains_satellite,
            "contains_hydrograph": self.contains_hydrograph,
            "candidate_models":    self.candidate_models,
            "candidate_metrics":   self.candidate_metrics,
            "candidate_vars":      self.candidate_vars,
            "variables":           self.variables,
            "related_models":      self.related_models,
            "domain":              self.domain,
            "equation_name":       self.equation_name,
        }


# ── Object graph edge ─────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ObjectEdge:
    """
    A directed semantic relationship between two ScientificObjects.

    Relations:
      CAPTION_OF   — caption text object is the caption of a figure/table
      IN_SECTION   — figure/table appears within a section
      CITES        — section cites a reference
      FOLLOWS      — section_n immediately precedes section_{n+1}
    """
    edge_id:    str
    paper_id:   str
    source_id:  str      # object_id of source node
    target_id:  str      # object_id of target node
    relation:   str      # CAPTION_OF | IN_SECTION | CITES | FOLLOWS
    confidence: float | None = None

    def to_row(self) -> dict[str, Any]:
        return {
            "edge_id":    self.edge_id,
            "paper_id":   self.paper_id,
            "source_id":  self.source_id,
            "target_id":  self.target_id,
            "relation":   self.relation,
            "confidence": self.confidence,
        }
