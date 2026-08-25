"""
geo_extractor.py — Geo-entity extraction (countries, rivers, regions, study area).

Contains:
  - detect_countries() / detect_study_countries_from_text()
  - extract_rivers() / score_rivers() / enrich_river_country()
  - extract_regions() / normalize_regions()
  - extract_study_area_structured() / extract_data_geo()
  - validate_with_ner() / enrich_with_coordinates()
  - extract_geo()  — main entry point consumed by entity_pipeline.extract_entities()
"""
from __future__ import annotations

import re
import logging
from typing import Optional, Callable, TYPE_CHECKING

from src.ingestion.utils import clean_text, snippet, ensure_dict
from src.ingestion.stages.geo_stage import (
    COUNTRY_PATTERNS, RIVER_TO_COUNTRY, RIVER_PATTERNS, REGION_PATTERNS,
    geocode_place, geonames_lookup,
    extract_tei_countries, parse_ner_results,
    author_name_set, extract_author_geo,
    merge_countries, compute_geo_confidence,
    normalize_country_item, normalize_river_name,
    is_valid_place, is_valid_region_name, classify_location,
    ensure_country_dict, ensure_list_of_country_dicts, ensure_list_of_river_dicts,
)
from src.ingestion.stages.disambiguation import best_mention_context, compute_entity_score
from src.ingestion.stages.embedding_classifier import (
    region_context_score, refine_study_area_with_embeddings,
    is_study_area_context, detect_study_type, sanitize_study_type,
)
from src.ingestion.stages.judge_stage import default_study_geo

if TYPE_CHECKING:
    from src.ingestion.stages.entity_pipeline import PipelineContext

log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# COUNTRY DETECTION
# ─────────────────────────────────────────────────────────────────────────────

def detect_countries(text: str, root) -> list[dict]:
    """Detect countries using TEI affiliation elements + regex patterns.

    `root` is a legacy parameter (XML element); retained for API compatibility.
    """
    found = {}
    for c in extract_tei_countries(root):
        c = ensure_country_dict(c)
        found[c["name"]] = c
    for name, pattern in COUNTRY_PATTERNS.items():
        m = re.search(pattern, text, re.I)
        if m and name not in found:
            found[name] = {"name": name, "code": None, "source": "regex",
                           "confidence": 0.75,
                           "evidence": snippet(text, m.start(), m.end())}
    return ensure_list_of_country_dicts(list(found.values()))


def detect_study_countries_from_text(text: str) -> list[dict]:
    found = {}
    for name, pattern in COUNTRY_PATTERNS.items():
        m = re.search(pattern, text, re.I)
        if m and name not in found:
            found[name] = {"name": name, "code": None,
                           "source": "regex_study_text", "confidence": 0.75,
                           "evidence": snippet(text, m.start(), m.end())}
    return ensure_list_of_country_dicts(list(found.values()))


# ─────────────────────────────────────────────────────────────────────────────
# RIVER EXTRACTION & SCORING
# ─────────────────────────────────────────────────────────────────────────────

_RIVER_CITATION_SIGNALS = [
    "et al", "ismail (", "modi (", "by adopting", "modelled by",
    "previous study", "studied by", "proposed by", "introduced by",
    "according to", "in contrast to", "in the study of",
]
_RIVER_STUDY_SIGNALS = [
    "study area", "study basin", "study watershed", "located in",
    "situated in", "the watershed", "our study", "this study",
    "case study", "urban catchment", "the basin", "river basin is",
    "study site", "the study", "research area",
]


def extract_rivers(text: str) -> list[dict]:
    text     = text or ""
    entities = {}
    for pattern in RIVER_PATTERNS:
        for m in re.finditer(pattern, text):
            raw  = clean_text(m.group(1))
            name = normalize_river_name(raw)
            if len(name) < 3:
                continue
            if name not in entities:
                entities[name] = {
                    "name": name, "type": "river",
                    "evidence": snippet(text, m.start(), m.end()),
                    "sources": ["regex"],
                    "scores": {"pattern": 0.8, "context": 0.0, "embedding": 0.0, "llm": 0.0},
                    "final_score": 0.0, "accepted": None, "role": None,
                }
    return list(entities.values())


def score_rivers(rivers: list[dict], ctx: "PipelineContext") -> list[dict]:
    """Score and filter rivers; reject those appearing only in citation context."""
    core_text = " ".join(filter(None, [
        ctx.abstract, ctx.study_area, ctx.methods, ctx.data_sources,
    ])).lower()

    result = []
    for r in rivers:
        ev     = r.get("evidence", "").lower()
        name_l = r.get("name", "").lower()

        is_citation  = any(sig in ev for sig in _RIVER_CITATION_SIGNALS)
        is_study     = any(sig in ev for sig in _RIVER_STUDY_SIGNALS)
        in_core_text = name_l in core_text

        ctx_score = 0.0
        if is_study and not is_citation:
            ctx_score = 0.9
        elif in_core_text and not is_citation:
            ctx_score = 0.5

        r["scores"]["context"] = ctx_score
        r["source"] = "study_area" if (is_study and not is_citation) else (
            "citation" if is_citation else "unknown"
        )
        r = compute_entity_score(r)
        r["accepted"] = ctx_score > 0.4
        r["role"]     = "study_watershed" if r["accepted"] else "citation_reference"
        result.append(r)
    return result


def enrich_river_country(rivers: list[dict]) -> list[dict]:
    links = []
    for river in ensure_list_of_river_dicts(rivers):
        name = river.get("name")
        if name in RIVER_TO_COUNTRY:
            links.append({"river": name, "country": RIVER_TO_COUNTRY[name],
                          "source": "lookup", "confidence": 0.85})
    return links


# ─────────────────────────────────────────────────────────────────────────────
# REGION EXTRACTION
# ─────────────────────────────────────────────────────────────────────────────

def extract_regions(
    text: str,
    ner_locations: Optional[list[str]] = None,
    invalid_names: Optional[set[str]] = None,
    encode_fn: Optional[Callable] = None,
) -> list[dict]:
    text          = text or ""
    invalid_names = invalid_names or set()
    candidates    = {}

    for name, pattern in REGION_PATTERNS.items():
        m = re.search(pattern, text, re.I)
        if m and is_valid_region_name(name):
            candidates[name] = {"name": name, "type": "region",
                                "source": "regex_region", "confidence": 0.85,
                                "evidence": snippet(text, m.start(), m.end())}

    for loc in ner_locations or []:
        loc_name = clean_text(loc)
        if not loc_name or loc_name.lower() in invalid_names:
            continue
        if not is_valid_place(loc_name) or not is_valid_region_name(loc_name):
            continue
        loc_type = classify_location(loc_name)
        if loc_type not in {"region", "basin", "watershed"}:
            continue
        ctx = best_mention_context(loc_name, text, window=500)
        if not is_study_area_context(ctx):
            continue
        candidates.setdefault(loc_name, {
            "name": loc_name, "type": loc_type,
            "source": "ner_region_context", "confidence": 0.7, "evidence": ctx,
        })

    results = []
    for item in candidates.values():
        if encode_fn is not None:
            score = region_context_score(item["name"], text, encode_fn)
        else:
            score = 0.5 if item["source"] == "regex_region" else 0.0
        item["semantic_score"] = round(float(score), 4)
        if item["source"] == "regex_region":
            item["confidence"] = min(float(item["confidence"]) + 0.05, 0.95)
            results.append(item)
        elif score >= 0.35:
            item["confidence"] = min(float(item["confidence"]) + float(score), 0.9)
            results.append(item)

    return sorted(results, key=lambda x: x["confidence"], reverse=True)


def normalize_regions(regions) -> list[dict]:
    if not regions:
        return []
    result = []
    for r in regions:
        if isinstance(r, dict):
            name = clean_text(r.get("name"))
            item = {**r, "name": name, "type": r.get("type", "region"),
                    "source": r.get("source", "unknown"),
                    "confidence": float(r.get("confidence", 0.5))}
        else:
            name = clean_text(str(r))
            item = {"name": name, "type": "region",
                    "source": "unknown", "confidence": 0.5}
        if not is_valid_region_name(name):
            continue
        result.append(item)
    return result


# ─────────────────────────────────────────────────────────────────────────────
# STUDY AREA STRUCTURED EXTRACTION
# ─────────────────────────────────────────────────────────────────────────────

def extract_study_area_structured(
    text: str, encode_fn: Optional[Callable] = None
) -> Optional[dict]:
    result = {}
    m = re.search(r"([A-Z][a-zA-Z\s\-]+(?:Reserve|Park|Basin|Catchment|Region))", text)
    if m:
        result["name"] = m.group(1).strip()
    for country, pattern in COUNTRY_PATTERNS.items():
        if re.search(pattern, text, re.I):
            result["country"] = country
            break
    region_match = re.search(r"([A-Z][a-z]+ region|[A-Z][a-z]+ oblast)", text, re.I)
    if region_match:
        result["region"] = region_match.group(1)
    coord_match = re.search(r"(\d{2})[°\s]+(\d{2}).*?N.*?(\d{2})[°\s]+(\d{2}).*?E", text)
    if coord_match:
        lat1, lat2, lon1, lon2 = coord_match.groups()
        result["coordinates"] = {
            "lat_min": float(f"{lat1}.{lat2}"),
            "lat_max": float(f"{lat1}.{int(lat2)+5}"),
            "lon_min": float(f"{lon1}.{lon2}"),
            "lon_max": float(f"{lon1}.{int(lon2)+5}"),
        }
    if result and result.get("name") and encode_fn is not None:
        result["name"] = refine_study_area_with_embeddings(result["name"], text, encode_fn)
    return result if result else None


def extract_data_geo(sections: dict) -> dict:
    text = " ".join([
        sections.get("abstract", ""), sections.get("introduction", ""),
        sections.get("methods", ""),  sections.get("results", ""),
    ]).lower()
    flags = []
    if "global coverage" in text or "earth's entire surface" in text or "global scale" in text:
        flags.append("global")
    if "multiple flood images" in text or "hundreds of" in text or "eight data sets" in text:
        flags.append("multi_site")
    if "modis archive" in text or "earth engine" in text:
        flags.append("global_satellite_archive")
    return {"scope": flags or ["unknown"], "source": "text_semantic_rules",
            "confidence": 0.85 if flags else 0.4}


# ─────────────────────────────────────────────────────────────────────────────
# NER VALIDATION & COORDINATE ENRICHMENT
# ─────────────────────────────────────────────────────────────────────────────

def validate_with_ner(study_geo: dict, ner_countries, ner_locations) -> dict:
    validated = ensure_dict(study_geo, default_study_geo()).copy()
    validated.pop("study_geo", None)
    if ner_countries:
        validated["countries"] = merge_countries(
            validated.get("countries", []), ner_countries
        )
    validated_locations = []
    for loc in validated.get("locations", []):
        loc = ensure_dict(loc)
        if not loc:
            continue
        if loc.get("source") == "ner_study_area_context" and loc.get("evidence"):
            loc["validated"]  = True
            loc["confidence"] = min(1.0, float(loc.get("confidence", 0.7)) + 0.1)
            validated_locations.append(loc)
    validated["locations"]  = validated_locations
    validated["confidence"] = compute_geo_confidence(validated)
    return validated


def enrich_with_coordinates(geo: dict) -> dict:
    enriched, seen = [], set()
    geo["countries"] = ensure_list_of_country_dicts(geo.get("countries", []))
    geo["rivers"]    = ensure_list_of_river_dicts(geo.get("rivers", []))

    for c in geo["countries"]:
        name = c.get("name")
        if not is_valid_place(name) or name in seen:
            continue
        seen.add(name)
        res = geocode_place(name)
        if res:
            enriched.append({"name": name, "type": "country",
                             "lat": res["lat"], "lon": res["lon"],
                             "source": res.get("source")})

    for loc in geo.get("locations", []):
        if not isinstance(loc, dict):
            continue
        name = loc.get("name")
        conf = float(loc.get("confidence", 0.0))
        if conf < 0.7:
            continue
        if loc.get("source") == "ner" and not loc.get("evidence"):
            continue
        if not is_valid_place(name) or name in seen:
            continue
        seen.add(name)
        res = geocode_place(name)
        if res:
            enriched.append({"name": name, "type": loc.get("type"),
                             "lat": res["lat"], "lon": res["lon"],
                             "source": res.get("source")})

    for r in geo["rivers"]:
        name = r.get("name")
        if not is_valid_place(name) or name in seen:
            continue
        seen.add(name)
        res = geocode_place(name)
        if res:
            enriched.append({"name": name, "type": "river",
                             "lat": res["lat"], "lon": res["lon"],
                             "source": res.get("source")})

    geo["coordinates"] = enriched
    return geo


# ─────────────────────────────────────────────────────────────────────────────
# MAIN GEO ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────

def extract_geo(
    ctx: "PipelineContext",
    metadata: dict,
    ner_entities: Optional[list] = None,
    encode_fn: Optional[Callable] = None,
) -> dict:
    text           = ctx.full_text
    study_type_obj = sanitize_study_type(
        detect_study_type(ctx.sections, metadata.get("title", ""), encode_fn=encode_fn)
    )
    author_geo  = ensure_list_of_country_dicts(extract_author_geo(metadata))
    data_geo    = extract_data_geo(ctx.sections)
    authors     = author_name_set(metadata)

    ner_countries, ner_locations = parse_ner_results(ner_entities or [])

    detected_countries = ensure_list_of_country_dicts(
        detect_study_countries_from_text(ctx.study_country_text)
    )
    study_area_struct = extract_study_area_structured(ctx.study_area, encode_fn=encode_fn)
    rivers      = ensure_list_of_river_dicts(extract_rivers(text))
    rivers      = score_rivers(rivers, ctx)
    river_links = enrich_river_country([r for r in rivers if r.get("accepted")])
    regions     = normalize_regions(
        extract_regions(text, ner_locations, invalid_names=authors, encode_fn=encode_fn)
    )

    locations = []
    for loc in ner_locations:
        loc_name = clean_text(loc)
        if not loc_name or loc_name.lower() in authors:
            continue
        if not is_valid_place(loc_name):
            continue
        ctx_snippet = best_mention_context(loc_name, text, window=500)
        if not is_study_area_context(ctx_snippet):
            continue
        locations.append({"name": loc_name, "type": classify_location(loc_name),
                          "source": "ner_study_area_context", "confidence": 0.75,
                          "evidence": ctx_snippet})

    primary_country = None
    if study_area_struct and study_area_struct.get("country"):
        primary_country = study_area_struct["country"]
    elif detected_countries:
        primary_country = detected_countries[0]["name"]
    elif river_links:
        primary_country = river_links[0]["country"]

    study_geo = default_study_geo()
    study_geo.update({
        "primary_country":     primary_country,
        "countries":           detected_countries,
        "regions":             regions,
        "rivers":              rivers,
        "river_country_links": river_links,
        "locations":           locations,
        "coordinates":         [],
        "confidence":          0.0,
    })
    study_geo = validate_with_ner(study_geo, ner_countries, ner_locations)

    return {
        "study_type": study_type_obj,
        "author_geo": author_geo,
        "study_geo":  study_geo,
        "data_geo":   data_geo,
        "note":       "context-aware geo extraction",
    }
