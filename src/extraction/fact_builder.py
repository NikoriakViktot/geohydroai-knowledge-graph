"""
Atomic fact builder — one ScientificFact per scientific concept.

Design
──────
Each call to build_facts() returns a list of TYPED atomic facts:

  data_source   — one per satellite
  method        — one per extracted method
  result        — one per extracted metric
  study_area    — at most one per paper (groups country/region/basin)
  task          — at most one per paper
  system_property — for near-real-time and other operational flags

After building all atomic facts, _link_facts() populates related_fact_ids
to record semantic connections:

  result   →  method   (metric evaluates method)
  method   →  data_source  (method applied to satellite)
  method   →  study_area
  task     →  method, data_source
  nrt      →  method, data_source
  data_source → study_area
"""
from __future__ import annotations

import uuid

from .models import (
    Evidence, Metric, Method, Satellite,
    StudyArea, ScientificFact,
)

# ── Method → category lookup ──────────────────────────────────────────────────

_METHOD_CATEGORIES: dict[str, str] = {
    "Thresholding":             "thresholding",
    "Change detection":         "change_detection",
    "NDWI/MNDWI":              "index-based",
    "Random Forest":            "ML",
    "SVM":                      "ML",
    "Maximum likelihood":       "ML",
    "U-Net":                    "DL",
    "CNN":                      "DL",
    "LSTM":                     "DL",
    "Transformer":              "DL",
    "OBIA":                     "object-based",
    "Hydrodynamic model":       "hydraulic_model",
    "HEC-RAS":                  "hydraulic_model",
    "HEC-HMS":                  "hydrological_model",
    "SWAT":                     "hydrological_model",
    "Flood frequency analysis": "hydrological_model",
    "DEM validation":           "DEM_validation",
    "ICESat-2 validation":      "DEM_validation",
    "Operational workflow":     "operational",
}


# ── Cross-linking ─────────────────────────────────────────────────────────────

def _link_facts(
    data_source_facts: list[ScientificFact],
    method_facts:      list[ScientificFact],
    result_facts:      list[ScientificFact],
    study_area_fact:   ScientificFact | None,
    task_fact:         ScientificFact | None,
    nrt_fact:          ScientificFact | None,
) -> None:
    """
    Populate related_fact_ids on each atomic fact.

    Reasoning chains enabled:
      result → method → data_source → study_area
      task   → method, data_source
    """
    ds_ids   = [f.id for f in data_source_facts]
    meth_ids = [f.id for f in method_facts]
    sa_id    = study_area_fact.id if study_area_fact else None

    # result ← evaluates ← method
    for rf in result_facts:
        rf.related_fact_ids.extend(meth_ids)

    # method → applied to → satellite; method → covers → study_area
    for mf in method_facts:
        mf.related_fact_ids.extend(ds_ids)
        if sa_id:
            mf.related_fact_ids.append(sa_id)

    # data_source → conducted in → study_area
    for dsf in data_source_facts:
        if sa_id:
            dsf.related_fact_ids.append(sa_id)

    # task links to all evidence-bearing fact types
    if task_fact:
        task_fact.related_fact_ids.extend(meth_ids)
        task_fact.related_fact_ids.extend(ds_ids)
        if sa_id:
            task_fact.related_fact_ids.append(sa_id)

    # NRT links to satellites and methods (operational chain)
    if nrt_fact:
        nrt_fact.related_fact_ids.extend(ds_ids)
        nrt_fact.related_fact_ids.extend(meth_ids)


# ── Public builder ────────────────────────────────────────────────────────────

def build_facts(paper_id: str, elements: dict) -> list[ScientificFact]:
    """
    Produce a list of atomic, typed ScientificFact objects.

    ``elements`` keys (from extract_scientific_elements())
    ─────────────────────────────────────────────────────
    satellites    list[dict]  {name, sensor_type, snippet}
    study_area    dict        {country, region, river_basin,
                               country_snippet, region_snippet, basin_snippet}
    methods       list[dict]  {name, snippet}
    metrics       list[dict]  {type, value, unit, snippet}
    task          str | None
    task_snippet  str
    task_section  str
    study_type    str | None
    near_real_time bool | None
    nrt_snippet   str
    """
    all_facts:          list[ScientificFact] = []
    data_source_facts:  list[ScientificFact] = []
    method_facts:       list[ScientificFact] = []
    result_facts:       list[ScientificFact] = []
    study_area_fact:    ScientificFact | None = None
    task_fact:          ScientificFact | None = None
    nrt_fact:           ScientificFact | None = None

    # ── 1. data_source facts — one per satellite ──────────────────────────────
    for sat_data in (elements.get("satellites") or []):
        ev: list[Evidence] = []
        if sat_data.get("snippet"):
            ev.append(Evidence(
                text    = sat_data["snippet"][:200],
                section = "abstract+methods",
                field   = "Satellite_Names",
                source  = "regex",
            ))
        fact = ScientificFact(
            id        = str(uuid.uuid4()),
            paper_id  = paper_id,
            fact_type = "data_source",
            satellite = Satellite(
                name        = sat_data["name"],
                sensor_type = sat_data.get("sensor_type"),
            ),
            evidence = ev,
        )
        data_source_facts.append(fact)
        all_facts.append(fact)

    # ── 2. method facts — one per method ─────────────────────────────────────
    for m_data in (elements.get("methods") or []):
        ev = []
        if m_data.get("snippet"):
            ev.append(Evidence(
                text    = m_data["snippet"][:200],
                section = "methods",
                field   = "Methods",
                source  = "regex",
            ))
        fact = ScientificFact(
            id        = str(uuid.uuid4()),
            paper_id  = paper_id,
            fact_type = "method",
            method    = Method(
                name     = m_data["name"],
                category = _METHOD_CATEGORIES.get(m_data["name"]),
            ),
            evidence = ev,
        )
        method_facts.append(fact)
        all_facts.append(fact)

    # ── 3. result facts — one per metric ─────────────────────────────────────
    for m_data in (elements.get("metrics") or []):
        ev = []
        if m_data.get("snippet"):
            ev.append(Evidence(
                text    = m_data["snippet"][:200],
                section = "results",
                field   = m_data["type"],
                source  = "regex",
            ))
        fact = ScientificFact(
            id        = str(uuid.uuid4()),
            paper_id  = paper_id,
            fact_type = "result",
            metric    = Metric(
                type  = m_data["type"],
                value = m_data["value"],
                unit  = m_data.get("unit"),
            ),
            evidence = ev,
        )
        result_facts.append(fact)
        all_facts.append(fact)

    # ── 4. study_area fact — at most one per paper ────────────────────────────
    area = elements.get("study_area") or {}
    if any(area.get(k) for k in ("country", "region", "river_basin")):
        ev = []
        for field_name, snippet_key, section in [
            ("Country",     "country_snippet", "abstract+introduction"),
            ("Region",      "region_snippet",  "abstract+introduction"),
            ("River_Basin", "basin_snippet",   "abstract+methods"),
        ]:
            if area.get(snippet_key):
                ev.append(Evidence(
                    text    = area[snippet_key][:200],
                    section = section,
                    field   = field_name,
                    source  = "regex",
                ))
        study_area_fact = ScientificFact(
            id         = str(uuid.uuid4()),
            paper_id   = paper_id,
            fact_type  = "study_area",
            study_area = StudyArea(
                country     = area.get("country"),
                region      = area.get("region"),
                river_basin = area.get("river_basin"),
            ),
            evidence = ev,
        )
        all_facts.append(study_area_fact)

    # ── 5. task fact ──────────────────────────────────────────────────────────
    task = elements.get("task")
    if task:
        ev = []
        if elements.get("task_snippet"):
            ev.append(Evidence(
                text    = elements["task_snippet"][:200],
                section = elements.get("task_section", "abstract"),
                field   = "Task",
                source  = "rule",
            ))
        task_fact = ScientificFact(
            id        = str(uuid.uuid4()),
            paper_id  = paper_id,
            fact_type = "task",
            task      = task,
            evidence  = ev,
        )
        all_facts.append(task_fact)

    # ── 6. system_property fact (NRT) ─────────────────────────────────────────
    if elements.get("near_real_time"):
        ev = []
        if elements.get("nrt_snippet"):
            ev.append(Evidence(
                text    = elements["nrt_snippet"][:200],
                section = "abstract+methods",
                field   = "Near_Real_Time",
                source  = "regex",
            ))
        nrt_fact = ScientificFact(
            id        = str(uuid.uuid4()),
            paper_id  = paper_id,
            fact_type = "system_property",
            value     = "near_real_time",
            evidence  = ev,
        )
        all_facts.append(nrt_fact)

    # ── 7. Cross-link all facts ───────────────────────────────────────────────
    _link_facts(
        data_source_facts, method_facts, result_facts,
        study_area_fact, task_fact, nrt_fact,
    )

    # ── 8. Return only facts with meaningful content ──────────────────────────
    return [f for f in all_facts if f.has_meaningful_content()]
