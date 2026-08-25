"""
Fact-centric data models — atomic, typed, fully provenance-traced.

Design contract
───────────────
Each ScientificFact describes exactly ONE concept (one satellite, one method,
one metric, one study area, one task, or one system property).  Facts are
never mixed.  Every accepted fact must carry at least one Evidence entry.

Fact types
──────────
  "data_source"     — satellite / sensor used in the study
  "method"          — processing algorithm, model, or technique
  "result"          — numeric metric (F1, OA, RMSE …)
  "study_area"      — geographic scope (country, region, basin)
  "task"            — scientific task (flood mapping, DEM validation …)
  "system_property" — operational property (near-real-time, automated …)
"""
from __future__ import annotations

from dataclasses import dataclass, field


# ── Atomic provenance unit ────────────────────────────────────────────────────

@dataclass
class Evidence:
    text: str
    section: str
    field: str
    source: str           # "regex" | "rule" | "llm"
    chunk_id: str | None = None

    def to_dict(self) -> dict:
        return {
            "text":     self.text[:200],
            "section":  self.section,
            "field":    self.field,
            "source":   self.source,
            "chunk_id": self.chunk_id,
        }


# ── Domain entity nodes ───────────────────────────────────────────────────────

_GENERIC_AREA_TERMS = frozenset({
    "study area", "the study area", "study region",
    "case study", "region", "area", "location",
})


@dataclass
class StudyArea:
    country:     str | None = None
    region:      str | None = None
    river_basin: str | None = None

    def is_empty(self) -> bool:
        return not any([self.country, self.region, self.river_basin])

    def is_generic(self) -> bool:
        for val in (self.country, self.region, self.river_basin):
            if val and val.lower().strip().rstrip(".") in _GENERIC_AREA_TERMS:
                return True
        return False

    def to_dict(self) -> dict:
        return {
            "country":     self.country,
            "region":      self.region,
            "river_basin": self.river_basin,
        }


@dataclass
class Satellite:
    name: str
    sensor_type: str | None = None   # SAR | Optical | LiDAR | Multi-sensor

    def to_dict(self) -> dict:
        return {"name": self.name, "sensor_type": self.sensor_type}


@dataclass
class Sensor:
    """Vocabulary node — shared across satellites with the same sensor type."""
    type: str | None     = None
    platform: str | None = None

    def to_dict(self) -> dict:
        return {"type": self.type, "platform": self.platform}


@dataclass
class Method:
    name: str
    category: str | None = None

    def to_dict(self) -> dict:
        return {"name": self.name, "category": self.category}


@dataclass
class Metric:
    type: str
    value: float
    unit: str | None = None

    def to_dict(self) -> dict:
        return {"type": self.type, "value": self.value, "unit": self.unit}


# ── Atomic fact model ─────────────────────────────────────────────────────────

_VALID_FACT_TYPES = frozenset({
    "data_source", "method", "result",
    "study_area", "task", "system_property",
})


@dataclass
class ScientificFact:
    """
    One atomic scientific claim.

    Exactly one type-specific payload field is populated per fact,
    determined by ``fact_type``.

    fact_type        primary field
    ─────────────    ─────────────
    data_source   →  satellite
    method        →  method
    result        →  metric
    study_area    →  study_area
    task          →  task  (string)
    system_property→ value (string, e.g. "near_real_time")
    """
    id:       str
    paper_id: str
    fact_type: str

    # Type-specific payload — at most ONE is set per fact
    satellite:  Satellite | None   = None   # data_source
    method:     Method | None      = None   # method
    metric:     Metric | None      = None   # result
    study_area: StudyArea | None   = None   # study_area
    task:       str | None         = None   # task
    value:      str | None         = None   # system_property

    # IDs of related atomic facts (enables graph reasoning chains)
    related_fact_ids: list[str] = field(default_factory=list)

    # Provenance — required for acceptance
    evidence: list[Evidence] = field(default_factory=list)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def has_meaningful_content(self) -> bool:
        if self.fact_type == "data_source":
            return self.satellite is not None
        if self.fact_type == "method":
            return self.method is not None
        if self.fact_type == "result":
            return self.metric is not None
        if self.fact_type == "study_area":
            return self.study_area is not None and not self.study_area.is_empty()
        if self.fact_type == "task":
            return bool(self.task)
        if self.fact_type == "system_property":
            return bool(self.value)
        return False

    def label(self) -> str:
        """Human-readable one-liner for logging."""
        if self.fact_type == "data_source" and self.satellite:
            return f"data_source:{self.satellite.name}"
        if self.fact_type == "method" and self.method:
            return f"method:{self.method.name}"
        if self.fact_type == "result" and self.metric:
            return f"result:{self.metric.type}={self.metric.value}"
        if self.fact_type == "study_area" and self.study_area:
            return f"study_area:{self.study_area.country or self.study_area.region}"
        if self.fact_type == "task":
            return f"task:{self.task}"
        if self.fact_type == "system_property":
            return f"system_property:{self.value}"
        return f"{self.fact_type}:empty"

    def to_dict(self) -> dict:
        d: dict = {
            "id":              self.id,
            "paper_id":        self.paper_id,
            "fact_type":       self.fact_type,
            "related_fact_ids": self.related_fact_ids,
            "evidence":        [e.to_dict() for e in self.evidence],
        }
        if self.fact_type == "data_source" and self.satellite:
            d["satellite"] = self.satellite.to_dict()
        elif self.fact_type == "method" and self.method:
            d["method"] = self.method.to_dict()
        elif self.fact_type == "result" and self.metric:
            d["metric"] = self.metric.to_dict()
        elif self.fact_type == "study_area" and self.study_area:
            d["study_area"] = self.study_area.to_dict()
        elif self.fact_type == "task":
            d["task"] = self.task
        elif self.fact_type == "system_property":
            d["value"] = self.value
        return d


# ── Top-level paper result ────────────────────────────────────────────────────

@dataclass
class FactExtractionResult:
    paper_id:       str
    title:          str
    facts:          list[ScientificFact] = field(default_factory=list)
    rejected_facts: list[dict]           = field(default_factory=list)
    fallback_used:  bool                 = False
    debug:          dict                 = field(default_factory=dict)

    @property
    def fact_count(self) -> int:
        return len(self.facts)

    def facts_by_type(self) -> dict[str, list[ScientificFact]]:
        result: dict[str, list[ScientificFact]] = {}
        for f in self.facts:
            result.setdefault(f.fact_type, []).append(f)
        return result

    def to_dict(self) -> dict:
        return {
            "paper_id":       self.paper_id,
            "title":          self.title,
            "facts":          [f.to_dict() for f in self.facts],
            "fact_count":     self.fact_count,
            "rejected_facts": self.rejected_facts,
            "fallback_used":  self.fallback_used,
            "debug":          self.debug,
        }
