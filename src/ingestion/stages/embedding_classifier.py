"""
embedding_classifier.py — Study-type and task classification via embeddings.

Contains:
  - STUDY_TYPE_PROTOTYPES / TASK_PROTOTYPES  — prototype sentence sets
  - classify_with_embeddings() / classify_task_with_embeddings()
  - embedding_score() / region_context_score() / refine_study_area_with_embeddings()
  - is_valid_study_type_label() / is_study_area_context() / sanitize_study_type()
  - detect_study_type()
  - _HYDRO_MODEL_SIGNALS / classify_task()
"""
from __future__ import annotations

import re
import logging
from typing import Optional, Callable

import numpy as np
from sklearn.metrics.pairwise import cosine_similarity

from src.ingestion.utils import ensure_dict, snippet
from src.ingestion.stages.judge_stage import (
    VALID_STUDY_TYPES, VALID_TASK_LABELS, make_task,
)

log = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# CLASSIFICATION PROTOTYPES
# ─────────────────────────────────────────────────────────────────────────────

STUDY_TYPE_PROTOTYPES: dict[str, list[str]] = {
    "case_study": [
        "study conducted in a specific river basin",
        "analysis of a flood event in a region",
        "case study of a watershed",
    ],
    "multi_site": [
        "evaluation across multiple locations",
        "analysis on many flood events",
        "tested on various regions",
    ],
    "global_algorithmic": [
        "global flood mapping algorithm",
        "method applied worldwide",
        "large scale automatic system",
    ],
    "regional": [
        "analysis across a country",
        "study using multiple stations in one country",
        "regional climate analysis",
    ],
    "review": [
        "this paper reviews existing methods",
        "we survey the literature",
        "systematic review of flood mapping techniques",
        "comparison of different approaches",
        "literature review on remote sensing",
    ],
}

TASK_PROTOTYPES: dict[str, list[str]] = {
    "flood_mapping_satellite": [
        "flood mapping using Sentinel-1 SAR imagery",
        "water extent extraction using satellite data",
        "flood detection using remote sensing",
        "inundation mapping using SAR data",
        "satellite based flood monitoring",
        "flood extent mapping from SAR or optical satellite imagery",
    ],
    "flood_modeling_hydraulic": [
        "flood simulation using HEC-RAS",
        "2D hydraulic flood modeling",
        "hydrodynamic flood simulation",
        "river flow modeling using hydraulic equations",
        "flood depth and velocity simulation",
        "inundation modeling using hydraulic models",
    ],
    "hydrological_modeling": [
        "rainfall runoff modeling using SWAT",
        "hydrological simulation using HEC-HMS",
        "basin scale runoff modeling",
        "precipitation discharge modeling",
        "streamflow simulation",
        "catchment runoff forecasting",
    ],
    "spectral_index_analysis": [
        "NDVI NDWI NDII RDI spectral index analysis",
        "water and vegetation indices for land surface monitoring",
        "remote sensing indices for soil moisture and drought",
        "vegetation and moisture index analysis",
        "spectral indices for land surface analysis",
        "water index based surface condition monitoring",
    ],
    "drought_monitoring": [
        "drought monitoring using MODIS vegetation indices",
        "soil moisture assessment using NDII and RDI",
        "desertification monitoring using remote sensing",
        "climate change impact on terrestrial ecosystems",
        "aridity assessment using satellite indices",
        "soil moisture and vegetation stress monitoring",
    ],
    "land_cover_classification": [
        "land cover classification using remote sensing",
        "image classification using machine learning",
        "supervised classification of satellite imagery",
        "LULC classification using optical satellite data",
    ],
    "land_use_change_detection": [
        "land use land cover change detection",
        "land cover transition analysis using satellite imagery",
        "ecosystem change detection using remote sensing",
        "land use change monitoring in disaster affected regions",
        "vegetation and forest cover dynamics",
    ],
    "flood_damage_assessment": [
        "flood damage assessment using satellite imagery",
        "agricultural loss assessment after flood",
        "infrastructure damage mapping after flood",
        "rapid flood damage estimation using SAR data",
    ],
    "flood_susceptibility_mapping": [
        "flood susceptibility mapping using machine learning",
        "flood hazard mapping using topographic and hydrological factors",
        "urban flood susceptibility analysis",
        "flood risk mapping using GIS and remote sensing factors",
    ],
    "terrain_dem_analysis": [
        "digital elevation model analysis",
        "terrain analysis using DEM",
        "slope aspect elevation modeling",
        "topographic analysis for flood modeling",
        "geomorphometric analysis using elevation data",
    ],
    "dem_validation": [
        "digital elevation model validation using ICESat-2",
        "DEM vertical accuracy assessment",
        "terrain model comparison using reference elevation data",
        "elevation error assessment using LiDAR or ICESat-2",
    ],
    "review": [
        "review of flood mapping methods",
        "survey of remote sensing approaches",
        "overview of flood detection techniques",
        "literature review on flood monitoring",
        "systematic review of flood susceptibility mapping",
    ],
    "unknown": [
        "unknown task",
        "insufficient evidence to classify the scientific task",
    ],
}

# ─────────────────────────────────────────────────────────────────────────────
# EMBEDDING CLASSIFIERS
# ─────────────────────────────────────────────────────────────────────────────

def classify_with_embeddings(text: str, encode_fn: Callable) -> tuple:
    text_emb = np.array(encode_fn([text or ""]))[0]
    scores   = {}
    for label, examples in STUDY_TYPE_PROTOTYPES.items():
        ex_embs    = np.array(encode_fn(examples))
        sim        = cosine_similarity([text_emb], ex_embs).mean()
        scores[label] = float(sim)
    best = max(scores, key=scores.get)
    return best, scores


def classify_task_with_embeddings(text: str, encode_fn: Callable) -> tuple:
    text_emb = np.array(encode_fn([text or ""]))[0]
    scores   = {}
    for label, examples in TASK_PROTOTYPES.items():
        ex_embs    = np.array(encode_fn(examples))
        sim        = cosine_similarity([text_emb], ex_embs).mean()
        scores[label] = float(sim)
    best = max(scores, key=scores.get)
    return best, scores


def embedding_score(text: str, label_examples: list, encode_fn: Callable) -> float:
    text_emb = np.array(encode_fn([text]))[0]
    ex_embs  = np.array(encode_fn(label_examples))
    sims     = cosine_similarity([text_emb], ex_embs)[0]
    # Clamp to [0, 1]: negative anti-similarity means "no evidence", not "evidence against"
    raw = float(max(sims))
    assert not np.isnan(raw), f"embedding_score NaN for text={text[:60]!r}"
    return max(0.0, raw)


def region_context_score(region_name: str, text: str, encode_fn: Callable) -> float:
    text        = text or ""
    region_name = region_name or ""
    matches     = list(re.finditer(re.escape(region_name), text, re.I))
    if not matches:
        return 0.0
    query  = f"actual study region or study area: {region_name}"
    emb_q  = np.array(encode_fn([query]))[0]
    best   = 0.0
    for m in matches[:5]:
        ctx   = snippet(text, m.start(), m.end(), window=500)
        emb_c = np.array(encode_fn([ctx]))[0]
        sim   = max(0.0, float(cosine_similarity([emb_q], [emb_c])[0][0]))
        best  = max(best, sim)
    return float(best)


def refine_study_area_with_embeddings(
    name: str, text: str, encode_fn: Callable
) -> Optional[str]:
    if not name:
        return name
    sim = cosine_similarity(
        [np.array(encode_fn([name]))[0]],
        [np.array(encode_fn([text]))[0]]
    )[0][0]
    return name if sim >= 0.2 else None


# ─────────────────────────────────────────────────────────────────────────────
# STUDY TYPE VALIDATORS & HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def is_valid_study_type_label(label: Optional[str]) -> bool:
    return label in VALID_STUDY_TYPES


def is_study_area_context(ctx: str) -> bool:
    ctx_l    = (ctx or "").lower()
    positive = any(k in ctx_l for k in [
        "study area", "study site", "test site", "tested on", "tested using",
        "we tested", "we applied", "case study", "flood event", "flood events",
        "occurred in", "located in", "ground truth data", "validation data",
        "data sources", "two urban flood events",
    ])
    negative = any(k in ctx_l for k in [
        "previous studies", "several studies", "many studies", "literature",
        "ref.", "et al.", "state of the art", "existing methods",
        "in contrast", "for example",
    ])
    return positive and not negative


def sanitize_study_type(study_type_obj: dict) -> dict:
    study_type_obj = ensure_dict(study_type_obj)
    label          = study_type_obj.get("label")
    if label not in VALID_STUDY_TYPES:
        return {"label": "unknown", "confidence": 0.4,
                "source": "sanitized_invalid_study_type",
                "previous": label, "needs_judge": True}
    return study_type_obj


# ─────────────────────────────────────────────────────────────────────────────
# STUDY TYPE DETECTION
# ─────────────────────────────────────────────────────────────────────────────

def detect_study_type(
    sections: dict,
    title: str = "",
    encode_fn: Optional[Callable] = None,
) -> dict:
    sections  = ensure_dict(sections)
    title_abs = f"{title} {sections.get('abstract','')}".lower()
    strong    = (
        f"{title} {sections.get('abstract','')} {sections.get('study_area','')} "
        f"{sections.get('methods','')[:1500]} {sections.get('results','')[:1000]}"
    ).lower()

    flood_case   = "flood" in strong and any(k in strong for k in [
        "tested on","tested using","we tested","we applied","applied to",
        "validated on","evaluated on","study area","study site","case study",
        "case area","test site","pilot area","event occurred","occurred in",
        "flood event","flood events","flooded area","flood extent",
        "ground truth data","post-flood","preflood","pre-flood",
    ])
    general_case = any(k in strong for k in [
        "tested on","tested using","we tested","we applied","applied to",
        "validated on","evaluated on","study area","study site","case study",
        "case area","test site","pilot area","event occurred","occurred in",
        "study was conducted","dataset was collected",
    ])
    regional     = any(k in strong for k in [
        "regional scale","national scale","country scale","across the country",
        "entire country","whole country","territory of","large region",
        "administrative region","natural zones","multiple stations",
        "weather stations","meteorological stations",
    ])
    multi_site   = any(k in strong for k in [
        "multiple case studies","several case studies","five case studies",
        "multiple locations","multiple sites","different locations",
        "different regions","various regions","multiple flood events",
        "several flood events","two flood events","different test sites",
        "benchmark sites",
    ])
    global_ev    = any(k in strong for k in [
        "global scale","global-scale","worldwide","anywhere in the world",
        "global application","global flood monitoring","global basis",
        "near real-time on a global basis","globally available",
        "global datasets","worldwide application",
    ])
    review_ev    = any(k in title_abs for k in [
        "systematic review","literature review","review paper","this review",
        "we review","review of","state-of-the-art review","survey of",
        "overview of","meta-analysis",
    ])
    weak_review  = any(k in strong for k in [
        "previous studies","several studies","many studies","existing methods",
        "state of the art","related work","literature","literature review",
    ])

    if review_ev and not (flood_case or general_case):
        return {"label": "review", "confidence": 0.95,
                "source": "rules_title_abstract", "needs_judge": False}
    if multi_site:
        return {"label": "multi_site", "confidence": 0.88,
                "source": "rules", "needs_judge": False}
    if flood_case:
        return {"label": "case_study", "confidence": 0.92,
                "source": "rules_flood_case", "needs_judge": False}
    if regional and not general_case:
        return {"label": "regional", "confidence": 0.88,
                "source": "rules", "needs_judge": False}
    if general_case:
        return {"label": "case_study", "confidence": 0.9,
                "source": "rules", "needs_judge": False}
    if global_ev:
        return {"label": "global_algorithmic", "confidence": 0.85,
                "source": "rules", "needs_judge": bool(weak_review)}

    if encode_fn is None:
        return {"label": "unknown", "confidence": 0.4,
                "source": "no_encode_fn", "needs_judge": True}
    label, scores = classify_with_embeddings(strong, encode_fn)
    confidence    = float(scores[label])
    if label not in VALID_STUDY_TYPES:
        return {"label": "unknown", "confidence": 0.4,
                "source": "embeddings_invalid_label",
                "needs_judge": True, "scores": scores}
    return {"label": label, "confidence": confidence, "source": "embeddings",
            "needs_judge": confidence < 0.85 or weak_review, "scores": scores}


# ─────────────────────────────────────────────────────────────────────────────
# TASK CLASSIFICATION
# ─────────────────────────────────────────────────────────────────────────────

# Strong hydrological modelling signals — checked BEFORE generic flood rules.
_HYDRO_MODEL_SIGNALS = [
    "hec-hms", "hec hms",
    "scs-cn", "scs cn", "curve number",
    "unit hydrograph", "muskingum",
    "rainfall-runoff modelling", "rainfall runoff modelling",
    "rainfall-runoff modeling", "rainfall runoff modeling",
]


def classify_task(ctx, encode_fn: Optional[Callable] = None) -> dict:
    """Classify the paper task from PipelineContext (duck-typed; avoids circular import)."""
    text = ctx.full_text.lower()

    if any(k in text for k in _HYDRO_MODEL_SIGNALS):
        return make_task("hydrological_modeling", 0.92)
    if any(k in text for k in ["hec-ras", "2d flood model", "hydraulic simulation",
                                "hydraulic flood", "1d/2d"]):
        return make_task("flood_modeling_hydraulic", 0.88)
    if any(k in text for k in ["swat"]) or (
        "runoff" in text and any(k in text for k in ["watershed", "streamflow", "hydrograph"])
    ):
        return make_task("hydrological_modeling", 0.88)
    if "flood" in text and any(k in text for k in [
        "mapping", "extent", "inundation", "water extent"
    ]):
        return make_task("flood_mapping_satellite", 0.9)
    if any(k in text for k in ["ndvi", "ndwi", "ndii", "rdi"]):
        return make_task("spectral_index_analysis", 0.88)

    if encode_fn is None:
        return make_task("unknown", 0.4)
    label, scores = classify_task_with_embeddings(text, encode_fn)
    if label not in VALID_TASK_LABELS:
        return make_task("unknown", 0.4)
    return make_task(label, scores[label], source="embeddings")
