"""
Abstract base class for all extractors.
Defines the contract every extractor must satisfy.
"""
from __future__ import annotations

import dataclasses
import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class MetricFact:
    """
    Full evidence-first representation of a single extracted metric observation.

    Covers both ML classification metrics (OA/F1/IoU/Kappa) and domain-specific
    metrics: flood-detection confusion (POD/FAR/CSI), hydrological modelling
    (NSE/KGE/RMSE), spatial agreement (flood area), operational timeliness
    (latency), sensor resolution, DEM/topography.

    Fields
    ------
    metric_id        Canonical ID:  "f1_score", "rmse", "latency_hours"
    metric_label     Human label:   "F1-score", "RMSE", "Latency (hours)"
    metric_group     Taxonomy group: "classification_accuracy", "hydrological_modeling", …
    fact_type        "evaluation_metric" | "model_parameter" | "operational_parameter"
                     Determines normalisation: model_parameter values are NOT scaled to [0,1]
    value            Normalised float (ratio eval_metrics → [0,1]; model_params → as-is)
    raw_value        Original string: "97.2%", "0.25 m", "6.4 h"
    unit             "ratio", "meters", "hours", "km2", "percent", ""
    scale            "0_1", "percent", "physical", "unknown"
    method           Method context: "U-Net", "Random Forest", ""
    sensor           Sensor context: "Sentinel-1", ""
    target           Measurement target: "flood extent", "water body", ""
    dataset_or_event Study or event: "2020 Mekong flood", ""
    source           Extraction source: "llm" | "sodb_parquet" | "paper_entities" | "regex"
    section          Document section: "results" | "table" | "abstract" | ""
    evidence_text    Verbatim supporting snippet (≤ 200 chars)
    paper_id         Paper identifier for graph linking
    doi              DOI for cross-referencing
    confidence       Quality estimate [0.0, 1.0]
    """
    metric_id:        str         = ""
    metric_label:     str         = ""
    metric_group:     str         = ""
    fact_type:        str         = "evaluation_metric"   # "evaluation_metric" | "model_parameter" | "operational_parameter"
    value:            float | None = None
    raw_value:        str         = ""
    unit:             str         = ""
    scale:            str         = "unknown"
    method:           str         = ""
    sensor:           str         = ""
    target:           str         = ""
    dataset_or_event: str         = ""
    source:           str         = ""
    section:          str         = ""
    evidence_text:    str         = ""
    paper_id:         str         = ""
    doi:              str         = ""
    confidence:       float       = 0.85


@dataclass
class ExtractionResult:
    """Structured output for one flood-mapping paper."""
    source_file: str = ""

    # ── Bibliographic ─────────────────────────────────────────────────────────
    title:     str = ""
    authors:   str = ""
    doi:       str = ""
    year:      str = ""
    abstract:  str = ""
    full_text: str = ""

    # ── Study type ────────────────────────────────────────────────────────────
    study_type: str = ""

    # ── Satellite / sensor ────────────────────────────────────────────────────
    satellite_names: str = ""    # comma-separated
    sensor_type:     str = ""    # SAR | Optical | Multi-sensor
    data_product:    str = ""

    # ── Study area ────────────────────────────────────────────────────────────
    country:     str = ""
    region:      str = ""
    river_basin: str = ""
    river_name:  str = ""
    city_event:  str = ""

    # ── Geographic relevance ──────────────────────────────────────────────────
    geo_relevance:     str  = ""
    ukraine_relevance: bool = False

    # ── Method / processing ───────────────────────────────────────────────────
    methods: str = ""            # comma-separated list

    # ── Metrics ───────────────────────────────────────────────────────────────
    oa:    float | None = None
    f1:    float | None = None
    iou:   float | None = None
    kappa: float | None = None
    metrics_reported: bool = False

    # ── Timeliness ────────────────────────────────────────────────────────────
    latency:             str         = ""
    revisit_time:        str         = ""
    near_real_time:      bool | None = None
    near_real_time_label: str        = ""   # "true" | "false" | "unclear" | ""

    # ── Provenance (Task 3) ───────────────────────────────────────────────────
    # field_name → {section, snippet, source, extractor_mode, value}
    provenance: dict = field(default_factory=dict)

    # ── Extraction mode flags (Task 7) ────────────────────────────────────────
    extractor_mode: str  = ""     # "section" | "fallback" | "mixed"
    llm_used:       bool = False
    fallback_used:  bool = False

    # ── Quality scores (Task 6) ───────────────────────────────────────────────
    # quality_score: structural completeness (title, abstract, methods, results, satellite)
    # evidence_score: count of fields with valid section-based provenance
    quality_score:  float = 0.0
    evidence_score: int   = 0

    # ── QA ────────────────────────────────────────────────────────────────────
    confidence:               float          = 0.0
    evidence:                 list[str]      = field(default_factory=list)
    sections_used:            list[str]      = field(default_factory=list)
    missing_data_explanation: str            = ""

    # ── Full evidence layer ───────────────────────────────────────────────────
    # Complete list of all extracted metric observations before headline agg.
    # Covers all metric groups: classification / flood-detection / hydrology /
    # spatial / timeliness / resolution.  flat oa/f1/iou/kappa are derived from
    # metric_facts in finalize().
    metric_facts: list[MetricFact] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "Source_File":               self.source_file,
            "Title":                     self.title,
            "Authors":                   self.authors,
            "DOI":                       self.doi,
            "Year":                      self.year,
            "Abstract":                  self.abstract,
            "Full_Text":                 self.full_text,
            "Study_Type":                self.study_type,
            "Satellite_Names":           self.satellite_names,
            "Sensor_Type":               self.sensor_type,
            "Data_Product":              self.data_product,
            "Country":                   self.country,
            "Region":                    self.region,
            "River_Basin":               self.river_basin,
            "River_Name":                self.river_name,
            "City_Event":                self.city_event,
            "Geo_Relevance":             self.geo_relevance,
            "Ukraine_Relevance":         self.ukraine_relevance,
            "Methods":                   self.methods,
            "OA":                        self.oa,
            "F1":                        self.f1,
            "IoU":                       self.iou,
            "Kappa":                     self.kappa,
            "Metrics_Reported":          self.metrics_reported,
            "Latency":                   self.latency,
            "Revisit_Time":              self.revisit_time,
            "Near_Real_Time":            self.near_real_time,
            "Near_Real_Time_Label":      self.near_real_time_label,
            "Missing_Data_Explanation":  self.missing_data_explanation,
            "Sections_Used":             ", ".join(self.sections_used) if self.sections_used else "",
            # Task 7: extraction mode flags
            "Extractor_Mode":            self.extractor_mode,
            "LLM_Used":                  self.llm_used,
            "Fallback_Used":             self.fallback_used,
            # Task 6: quality scores
            "Quality_Score":             round(self.quality_score, 3),
            "Evidence_Score":            self.evidence_score,
            "Confidence":                round(self.confidence, 3),
            # Task 3: provenance (JSON string — nested dict not flat-CSV-safe)
            "Provenance_JSON":           _provenance_summary(self.provenance),
            # Full metric evidence layer
            "Metric_Facts_JSON":         json.dumps(
                [dataclasses.asdict(f) for f in self.metric_facts],
                ensure_ascii=False,
            ) if self.metric_facts else "",
        }

    def _num_metrics(self) -> int:
        return sum(v is not None for v in [self.oa, self.f1, self.iou, self.kappa])

    def finalize(self) -> "ExtractionResult":
        """Compute derived fields before returning."""
        # Promote headline values from metric_facts if flat fields still empty
        _HEADLINE = {
            "overall_accuracy": "oa",
            "f1_score":         "f1",
            "iou":              "iou",
            "kappa":            "kappa",
        }
        for metric_id, attr in _HEADLINE.items():
            if getattr(self, attr) is None:
                val = _best_fact_value(self.metric_facts, metric_id)
                if val is not None:
                    setattr(self, attr, val)

        self.metrics_reported = self._num_metrics() > 0
        # evidence_score = fields that have section-level provenance with a snippet
        self.evidence_score = sum(
            1 for v in self.provenance.values()
            if isinstance(v, dict) and v.get("section") and v.get("snippet")
        )
        return self


def _best_fact_value(facts: list, metric_id: str) -> float | None:
    """
    Return the highest-confidence normalized value for *metric_id* from metric_facts.

    Matches both bare IDs ("f1_score") and prefixed IDs ("metric.f1_score").
    Returns None if no matching fact with a non-None value exists.
    """
    norm_id = metric_id.lower().strip()
    if norm_id.startswith("metric."):
        norm_id = norm_id[len("metric."):]

    matching = [
        f for f in facts
        if f.value is not None
        and (
            f.metric_id.lower().lstrip("metric.") == norm_id
            or f.metric_id.lower() == norm_id
            or f.metric_id.lower() == f"metric.{norm_id}"
        )
    ]
    if not matching:
        return None
    return max(matching, key=lambda f: f.confidence).value


def _provenance_summary(provenance: dict) -> str:
    """Compact single-line summary of field→section mapping for CSV storage."""
    if not provenance:
        return ""
    parts = []
    for field_name, prov in provenance.items():
        if isinstance(prov, dict):
            section = prov.get("section", "?")
            source  = prov.get("source", "?")
            parts.append(f"{field_name}@{section}[{source}]")
    return "; ".join(parts)


class BaseExtractor(ABC):
    """All extractors implement this interface."""

    @abstractmethod
    def extract(
        self,
        chunks: list[dict],
        source_file: str,
    ) -> ExtractionResult:
        """
        Extract structured information from a list of retrieved chunks.

        Parameters
        ----------
        chunks:
            Each dict has at least {"text": str, "filename": str}
        source_file:
            Original PDF filename

        Returns
        -------
        ExtractionResult (finalized)
        """
