"""
disambiguation.py — Acronym disambiguation for the entity extraction pipeline.

Contains:
  - RESOLVER_VERSION / ONTOLOGY_VERSION constants
  - best_mention_context()  — context window extraction
  - compute_entity_score()  — weighted score aggregation
  - SCS / SMA two-class disambiguators
  - _DISAMBIG_TABLE          — table-driven 10-entry registry
  - _extend_disambig_table_from_kb() — merges JSON rules at startup
  - disambiguate_entity()   — generic table-driven dispatch
  - compute_edge_confidence() / build_entity_lineage()
"""
from __future__ import annotations

import re
import logging
from typing import Optional

from src.ingestion.utils import snippet

log = logging.getLogger(__name__)

# ── Provenance versioning ─────────────────────────────────────────────────────
RESOLVER_VERSION = "v0.3.2"
ONTOLOGY_VERSION = "v2.0"


# ─────────────────────────────────────────────────────────────────────────────
# SHARED HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def best_mention_context(name: str, text: str, window: int = 500) -> str:
    if not name or not text:
        return ""
    matches = list(re.finditer(re.escape(name), text, flags=re.I))
    if not matches:
        return ""
    scored = []
    for m in matches[:10]:
        ctx   = snippet(text, m.start(), m.end(), window=window)
        score = sum(1 for k in [
            "study area", "study site", "tested on", "test site",
            "flood event", "flood events", "occurred in", "located in",
            "case study", "data sources", "ground truth", "validation data",
        ] if k in ctx.lower())
        scored.append((score, ctx))
    scored.sort(key=lambda x: x[0], reverse=True)
    return scored[0][1]


def compute_entity_score(entity: dict) -> dict:
    s     = entity["scores"]
    score = (
        s.get("pattern",   0) * 0.3 +
        s.get("context",   0) * 0.3 +
        s.get("embedding", 0) * 0.2 +
        s.get("llm",       0) * 0.2
    )
    entity["final_score"] = round(score, 4)
    return entity


# ─────────────────────────────────────────────────────────────────────────────
# SCS DISAMBIGUATION
# ─────────────────────────────────────────────────────────────────────────────

_SCS_HYDRO_SIGNALS = {
    "curve number", "scs-cn", "scs cn", "unit hydrograph",
    "soil conservation service", "nrcs", "rainfall-runoff",
    "loss estimation", "runoff estimation", "green and ampt",
    "muskingum", "hec-hms", "hec hms",
}

_SCS_HYDRO_META = {
    "full_name":  "Soil Conservation Service",
    "type":       "method",
    "type_group": "method",
    "domain":     "hydrology",
    "definition": "USDA method for estimating surface runoff from rainfall "
                  "using the Curve Number (CN) approach.",
    "used_for":   ["rainfall-runoff modelling", "loss estimation",
                   "runoff estimation", "hydrological modelling"],
    "related":    ["HEC-HMS", "SWAT", "CN", "NRCS"],
    "contexts":   ["hydrology", "watershed"],
    "source_kb":  "disambiguation",
    "disambiguated": "hydrology",
}


def disambiguate_scs(entity: dict, ctx_text: str) -> dict:
    """SCS: Soil Conservation Service (hydrology) vs Spectral Correlation Similarity (RS)."""
    if entity.get("name", "").upper() != "SCS":
        return entity
    ctx   = best_mention_context("SCS", ctx_text, window=400).lower()
    fired = [sig for sig in _SCS_HYDRO_SIGNALS if sig in ctx]
    if fired:
        conf = _disambiguation_confidence(len(fired), 0)
        entity.setdefault("kb_metadata", {}).update({
            **_SCS_HYDRO_META,
            "canonical_id":              "hydrology.soil_conservation_service",
            "disambiguation_confidence": conf,
            "disambiguation_evidence":   fired,
        })
    return entity


# ─────────────────────────────────────────────────────────────────────────────
# SMA DISAMBIGUATION
# ─────────────────────────────────────────────────────────────────────────────

_SMA_HYDRO_SIGNALS = {
    "soil moisture accounting", "hec-hms", "hec hms",
    "loss method", "continuous simulation", "infiltration rate",
    "soil storage", "tension storage", "percolation rate",
    "soil zone", "groundwater layer", "soil saturation",
    "watershed model", "rainfall-runoff", "runoff volume",
}

_SMA_RS_SIGNALS = {
    "spectral mixture", "endmember", "unmixing", "subpixel",
    "hyperspectral", "mesma", "bsma", "linear mixture",
    "fraction image", "abundance", "npv", "shade fraction",
}

_SMA_HYDRO_META = {
    "full_name":     "Soil Moisture Accounting",
    "type":          "method",
    "type_group":    "method",
    "domain":        "hydrology",
    "definition":    "HEC-HMS continuous-simulation loss method that tracks "
                     "water movement through canopy, surface, soil, and "
                     "groundwater layers.",
    "used_for":      ["continuous hydrological simulation", "loss estimation",
                      "soil moisture tracking", "flood forecasting"],
    "related":       ["HEC-HMS", "SCS-CN", "Green-Ampt"],
    "contexts":      ["hydrology", "watershed"],
    "source_kb":     "disambiguation",
    "disambiguated": "hydrology",
}

_SMA_RS_META = {
    "full_name":     "Spectral Mixture Analysis",
    "type":          "method",
    "type_group":    "method",
    "domain":        "remote_sensing",
    "definition":    "Technique decomposing mixed pixels into component "
                     "spectral fractions (endmembers).",
    "used_for":      ["subpixel analysis", "land cover fraction mapping"],
    "related":       ["MESMA", "BSMA", "MTMF"],
    "contexts":      ["remote_sensing", "hyperspectral"],
    "source_kb":     "disambiguation",
    "disambiguated": "remote_sensing",
}


def disambiguate_sma(entity: dict, ctx_text: str) -> dict:
    """SMA: Soil Moisture Accounting (HEC-HMS) vs Spectral Mixture Analysis (RS)."""
    if entity.get("name", "").upper() != "SMA":
        return entity
    ctx         = best_mention_context("SMA", ctx_text, window=400).lower()
    hydro_fired = [sig for sig in _SMA_HYDRO_SIGNALS if sig in ctx]
    rs_fired    = [sig for sig in _SMA_RS_SIGNALS    if sig in ctx]

    def _set(meta, canonical, winner_hits, runnerup_hits, evidence):
        conf = _disambiguation_confidence(winner_hits, runnerup_hits)
        entity.setdefault("kb_metadata", {}).update({
            **meta,
            "canonical_id":              canonical,
            "disambiguation_confidence": conf,
            "disambiguation_evidence":   evidence,
        })

    if hydro_fired and not rs_fired:
        _set(_SMA_HYDRO_META, "hydrology.soil_moisture_accounting",
             len(hydro_fired), 0, hydro_fired)
    elif rs_fired and not hydro_fired:
        _set(_SMA_RS_META, "remote_sensing.spectral_mixture_analysis",
             len(rs_fired), 0, rs_fired)
    elif hydro_fired and rs_fired:
        if len(hydro_fired) >= len(rs_fired):
            _set(_SMA_HYDRO_META, "hydrology.soil_moisture_accounting",
                 len(hydro_fired), len(rs_fired), hydro_fired)
        else:
            _set(_SMA_RS_META, "remote_sensing.spectral_mixture_analysis",
                 len(rs_fired), len(hydro_fired), rs_fired)
    elif "hec-hms" in ctx_text.lower():
        _set(_SMA_HYDRO_META, "hydrology.soil_moisture_accounting",
             1, 0, ["hec-hms (doc-level)"])
    return entity


# ─────────────────────────────────────────────────────────────────────────────
# TABLE-DRIVEN DISAMBIGUATION REGISTRY
# ─────────────────────────────────────────────────────────────────────────────

_DISAMBIG_TABLE: dict[str, dict] = {

    # ── ML: Machine Learning vs Maximum Likelihood ──────────────────────────
    "ML": {
        "senses": [
            {
                "id":        "machine_learning",
                "full_name": "Machine Learning",
                "domain":    "computer_science",
                "signals":   {
                    "machine learning", "deep learning", "neural network",
                    "convolutional", "lstm", "random forest", "xgboost",
                    "gradient boost", "training", "overfitting", "feature importance",
                    "accuracy", "precision", "recall", "f1", "hyperparameter",
                },
            },
            {
                "id":        "maximum_likelihood",
                "full_name": "Maximum Likelihood",
                "domain":    "statistics",
                "signals":   {
                    "maximum likelihood", "log-likelihood", "mle",
                    "likelihood ratio", "gamlss", "parameter estimation",
                    "genest", "copula",
                },
            },
        ],
        "default": "machine_learning",
    },

    # ── AI: Artificial Intelligence vs Aridity Index ─────────────────────────
    "AI": {
        "senses": [
            {
                "id":        "artificial_intelligence",
                "full_name": "Artificial Intelligence",
                "domain":    "computer_science",
                "signals":   {
                    "artificial intelligence", "deep learning", "machine learning",
                    "neural", "algorithm", "model", "prediction", "classification",
                },
            },
            {
                "id":        "aridity_index",
                "full_name": "Aridity Index",
                "domain":    "hydrology",
                "signals":   {
                    "aridity index", "aridity", "arid", "semi-arid",
                    "potential evapotranspiration", "precipitation ratio",
                    "de martonne", "thornthwaite",
                },
            },
        ],
        "default": "artificial_intelligence",
    },

    # ── RF: Random Forest vs Rainfall ────────────────────────────────────────
    "RF": {
        "senses": [
            {
                "id":        "random_forest",
                "full_name": "Random Forest",
                "domain":    "machine_learning",
                "signals":   {
                    "random forest", "decision tree", "ensemble", "bagging",
                    "breiman", "feature importance", "classifier", "regressor",
                    "xgb", "gradient boost",
                },
            },
            {
                "id":        "rainfall",
                "full_name": "Rainfall",
                "domain":    "hydrology",
                "signals":   {
                    "rainfall", "precipitation", "rain gauge", "mm/h",
                    "storm", "intensity", "runoff", "idf",
                },
            },
        ],
        "default": "random_forest",
    },

    # ── CC: Correlation Coefficient vs Canopy Cover vs Climate Change ─────────
    "CC": {
        "senses": [
            {
                "id":        "correlation_coefficient",
                "full_name": "Correlation Coefficient",
                "domain":    "statistics",
                "signals":   {
                    "correlation coefficient", "pearson", "spearman",
                    "taylor diagram", "bias", "rmse", "nse", "r²",
                    "goodness-of-fit",
                },
            },
            {
                "id":        "climate_change",
                "full_name": "Climate Change",
                "domain":    "hydrology",
                "signals":   {
                    "climate change", "global warming", "greenhouse",
                    "rcp", "ssp", "ipcc", "emission scenario",
                    "temperature rise",
                },
            },
            {
                "id":        "canopy_cover",
                "full_name": "Canopy Cover",
                "domain":    "remote_sensing",
                "signals":   {
                    "canopy cover", "canopy", "vegetation cover",
                    "tree cover", "forest cover", "lai",
                },
            },
        ],
        "default": "correlation_coefficient",
    },

    # ── CA: California (geo) vs Catchment Area vs Cellular Automata ──────────
    "CA": {
        "senses": [
            {
                "id":        "california",
                "full_name": "California",
                "domain":    "geography",
                "signals":   {
                    "california", "los angeles", "san francisco", "sacramento",
                    "ca, usa", "ca usa", "davis, ca", "san jose",
                },
            },
            {
                "id":        "catchment_area",
                "full_name": "Catchment Area",
                "domain":    "hydrology",
                "signals":   {
                    "catchment area", "drainage area", "watershed area",
                    "contributing area", "basin area",
                },
            },
            {
                "id":        "cellular_automata",
                "full_name": "Cellular Automata",
                "domain":    "modelling",
                "signals":   {
                    "cellular automata", "automaton", "grid cell",
                    "transition rule", "lisflood",
                },
            },
        ],
        "default": None,
    },

    # ── EM: Expectation Maximization vs Error Model vs Electromagnetic ────────
    "EM": {
        "senses": [
            {
                "id":        "expectation_maximization",
                "full_name": "Expectation Maximization",
                "domain":    "statistics",
                "signals":   {
                    "expectation-maximization", "expectation maximization",
                    "em algorithm", "gaussian mixture", "clustering",
                    "latent variable", "e-step", "m-step",
                },
            },
            {
                "id":        "error_model",
                "full_name": "Error Model",
                "domain":    "hydrology",
                "signals":   {
                    "error model", "model error", "stochastic error",
                    "residual", "uncertainty",
                },
            },
            {
                "id":        "electromagnetic",
                "full_name": "Electromagnetic",
                "domain":    "remote_sensing",
                "signals":   {
                    "electromagnetic", "em wave", "em radiation",
                    "em spectrum", "microwave",
                },
            },
        ],
        "default": None,
    },

    # ── SW: Southwest (geographic) vs Shortwave Radiation vs Soil Water ───────
    "SW": {
        "senses": [
            {
                "id":        "southwest",
                "full_name": "Southwest",
                "domain":    "geography",
                "signals":   {
                    "southwest", "sw algeria", "sw part", "sw region",
                    "sw corner", "sw direction",
                },
            },
            {
                "id":        "shortwave_radiation",
                "full_name": "Shortwave Radiation",
                "domain":    "remote_sensing",
                "signals":   {
                    "shortwave", "short-wave", "swir", "sw radiation",
                    "solar radiation", "downwelling",
                },
            },
            {
                "id":        "soil_water",
                "full_name": "Soil Water",
                "domain":    "hydrology",
                "signals":   {
                    "soil water", "soil moisture", "soil storage",
                    "vadose", "field capacity",
                },
            },
        ],
        "default": None,
    },

    # ── IR: Infrared (sensor band) ────────────────────────────────────────────
    "IR": {
        "senses": [
            {
                "id":        "infrared",
                "full_name": "Infrared",
                "domain":    "remote_sensing",
                "signals":   {
                    "infrared", "nir", "swir", "tir", "thermal",
                    "band", "reflectance", "wavelength", "spectral",
                },
            },
            {
                "id":        "infiltration_rate",
                "full_name": "Infiltration Rate",
                "domain":    "hydrology",
                "signals":   {
                    "infiltration rate", "infiltration", "green-ampt",
                    "sorptivity", "hydraulic conductivity",
                },
            },
        ],
        "default": "infrared",
    },

    # ── TM: Thematic Mapper (Landsat) vs Trademark suffix ────────────────────
    "TM": {
        "senses": [
            {
                "id":        "thematic_mapper",
                "full_name": "Thematic Mapper",
                "domain":    "remote_sensing",
                "signals":   {
                    "thematic mapper", "landsat", "etm", "tm band",
                    "landsat tm", "landsat-5", "oli",
                },
            },
        ],
        "default": "thematic_mapper",
    },

    # ── EC: EC-Earth (climate model) vs Electrical Conductivity ──────────────
    "EC": {
        "senses": [
            {
                "id":        "ec_earth",
                "full_name": "EC-Earth",
                "domain":    "climate_model",
                "signals":   {
                    "ec-earth", "ec earth", "climate model", "gcm",
                    "cmip", "coupled model",
                },
            },
            {
                "id":        "electrical_conductivity",
                "full_name": "Electrical Conductivity",
                "domain":    "hydrology",
                "signals":   {
                    "electrical conductivity", "salinity", "tds",
                    "water quality", "siemens",
                },
            },
        ],
        "default": None,
    },
}


# ─────────────────────────────────────────────────────────────────────────────
# DISAMBIGUATION INTERNALS
# ─────────────────────────────────────────────────────────────────────────────

def _disambiguation_confidence(winner_hits: int, runnerup_hits: int) -> float:
    """Confidence in [0.50, 0.98]: combines dominance and absolute signal strength."""
    if winner_hits == 0:
        return 0.50
    total     = winner_hits + runnerup_hits
    dominance = winner_hits / total
    strength  = min(winner_hits / 4.0, 1.0)
    conf      = 0.60 * dominance + 0.35 * strength + 0.05
    return round(min(0.98, conf), 2)


def _apply_sense(entity: dict, sense: dict, confidence: float, evidence: list[str]) -> dict:
    domain = sense["domain"]
    s_id   = sense["id"]
    entity.setdefault("kb_metadata", {}).update({
        "full_name":                 sense["full_name"],
        "domain":                    domain,
        "disambiguated":             s_id,
        "canonical_id":              f"{domain}.{s_id}",
        "disambiguation_confidence": confidence,
        "disambiguation_evidence":   evidence,
        "resolver_version":          RESOLVER_VERSION,
        "ontology_version":          ONTOLOGY_VERSION,
        "source_kb":                 "disambiguation",
    })
    return entity


def compute_edge_confidence(entity: dict) -> float:
    """Combined Paper→Entity edge confidence, clamped to [0.01, 0.99]."""
    extraction = float(entity.get("final_score", 0.5))
    kb         = entity.get("kb_metadata", {})
    disambig   = float(kb.get("disambiguation_confidence", 1.0))
    combined   = extraction if disambig == 1.0 else extraction * disambig
    return round(min(0.99, max(0.01, combined)), 4)


def build_entity_lineage(entity: dict) -> dict:
    """Flat provenance record for a Neo4j edge or parquet audit table."""
    kb = entity.get("kb_metadata", {})
    return {
        "surface_form":        entity.get("name", ""),
        "canonical_id":        kb.get("canonical_id", ""),
        "resolver_version":    kb.get("resolver_version", RESOLVER_VERSION),
        "ontology_version":    kb.get("ontology_version", ONTOLOGY_VERSION),
        "extraction_score":    round(float(entity.get("final_score", 0.5)), 4),
        "disambig_confidence": round(float(kb.get("disambiguation_confidence", 1.0)), 4),
        "disambig_evidence":   (kb.get("disambiguation_evidence") or [])[:10],
        "edge_confidence":     compute_edge_confidence(entity),
    }


def disambiguate_entity(entity: dict, ctx_text: str) -> dict:
    """Table-driven acronym disambiguator with confidence propagation."""
    name = entity.get("name", "").upper()
    spec = _DISAMBIG_TABLE.get(name)
    if not spec:
        return entity

    ctx = best_mention_context(name, ctx_text, window=400).lower()
    if not ctx:
        ctx = ctx_text[:1200].lower()

    scored: list[tuple[int, list[str], dict]] = []
    for sense in spec["senses"]:
        fired    = [sig for sig in sense.get("signals", set()) if sig in ctx]
        excluded = [ex for ex in sense.get("exclude", set())   if ex in ctx]
        net_hits = max(0, len(fired) - len(excluded))
        scored.append((net_hits, fired, sense))
    scored.sort(key=lambda x: x[0], reverse=True)

    winner_hits, winner_evidence, winner_sense = scored[0]
    runnerup_hits = scored[1][0] if len(scored) > 1 else 0

    if winner_hits > 0:
        conf = _disambiguation_confidence(winner_hits, runnerup_hits)
        return _apply_sense(entity, winner_sense, conf, winner_evidence)

    # Document-level fallback for CA / california
    if name == "CA" and "california" in ctx_text.lower():
        ca_sense = next(s for s in spec["senses"] if s["id"] == "california")
        return _apply_sense(entity, ca_sense, 0.65, ["california (doc-level)"])

    default_id = spec.get("default")
    if default_id:
        default_sense = next((s for s in spec["senses"] if s["id"] == default_id), None)
        if default_sense:
            return _apply_sense(entity, default_sense, 0.50, [])

    return entity


# ─────────────────────────────────────────────────────────────────────────────
# JSON RULE MERGER  (called once at _KB load time)
# ─────────────────────────────────────────────────────────────────────────────

def _extend_disambig_table_from_kb(kb) -> None:
    """Merge ontology_disambiguation_rules.json entries into _DISAMBIG_TABLE.

    Converts JSON context_includes/context_excludes into signals/exclude sets.
    Hardcoded entries in _DISAMBIG_TABLE are never overwritten.
    """
    for rule in kb.disambiguation_rules:
        token = rule.get("ambiguous_token", "").upper()
        if not token or token in _DISAMBIG_TABLE:
            continue
        candidates = rule.get("candidates", [])
        json_rules = rule.get("rules", [])
        if not candidates or not json_rules:
            continue

        senses = []
        for jr in json_rules:
            sense_id = jr.get("resolved_entity") or (candidates[0] if candidates else token)
            senses.append({
                "id":        sense_id,
                "full_name": candidates[0] if candidates else sense_id,
                "domain":    jr.get("domain", "unknown"),
                "signals":   set(jr.get("context_includes", [])),
                "exclude":   set(jr.get("context_excludes", [])),
            })
        if len(candidates) > 1:
            senses.append({
                "id":        candidates[1],
                "full_name": candidates[1],
                "domain":    "unknown",
                "signals":   set(),
                "exclude":   set(),
            })

        _DISAMBIG_TABLE[token] = {"senses": senses, "default": senses[0]["id"]}
        log.debug("[disambig-extend] loaded token=%s senses=%d", token, len(senses))
