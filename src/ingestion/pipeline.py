
"""
pipeline.py  —  GeoHydroAI Extraction Pipeline
================================================
Refactored version of tei_to_sections.py.

Architecture layers:
  1. Knowledge Layer   → KnowledgeBase (JSON-driven, no hardcoded patterns)
  2. Normalization     → Normalizer (alias resolution)
  3. Extraction        → EntityExtractor (KB-driven pattern matching)
  4. Method Matching   → MethodMatcher (ontology-driven)
  5. Geo / NER         → spaCy + Geonames (unchanged)
  6. Classification    → embedding cosine similarity (unchanged)
  7. Validation        → OllamaJudge LLM (unchanged)
  8. Output            → structured JSON (identical schema to v1)

What changed vs tei_to_sections.py:
  - SATELLITE_PATTERNS, DEM_DATASETS, METHOD_PATTERNS → KB-driven
  - All entities enriched with KB metadata (full_name, domain, used_for, etc.)
  - METRIC_PATTERNS kept (value-capture groups need explicit regex)
  - COUNTRY_PATTERNS kept (geo, not ontology)
  - All geo / embedding / LLM logic is preserved exactly
"""

from __future__ import annotations

import sys

# When run directly (python src/ingestion/pipeline.py), ensure the project root
# is on sys.path so that absolute imports of src.* packages work.
if __name__ == "__main__":
    _root = str(__import__("pathlib").Path(__file__).resolve().parents[2])
    if _root not in sys.path:
        sys.path.insert(0, _root)
import re
import json
import hashlib
import urllib.parse
import logging
import os
import traceback
from pathlib import Path
from typing import Optional, Callable

import numpy as np
import requests
import time
from sklearn.metrics.pairwise import cosine_similarity

# ── knowledge layer ───────────────────────────────────────────────────────────
if __name__ == "__main__":
    # support direct execution: python src/ingestion/pipeline.py
    import importlib, sys as _sys
    _pkg = importlib.import_module("src.ingestion.knowledge")
    KnowledgeBase      = _pkg.KnowledgeBase
    load_knowledge_base = _pkg.load_knowledge_base
    Normalizer         = _pkg.Normalizer
    EntityExtractor    = _pkg.EntityExtractor
    MethodMatcher      = _pkg.MethodMatcher
    _ref_mod           = importlib.import_module("src.ingestion.extract_references")
    extract_references = _ref_mod.extract_references
else:
    from .knowledge import (
        KnowledgeBase,
        load_knowledge_base,
        Normalizer,
        EntityExtractor,
        MethodMatcher,
    )
    from .extract_references import extract_references

log = logging.getLogger(__name__)

# ── paths (resolved from settings so env vars override) ───────────────────────
if __name__ == "__main__":
    import importlib as _il
    _cfg = _il.import_module("src.config.settings")
else:
    from src.config import settings as _cfg  # type: ignore[import]

XML_DIR = _cfg.XML_DIR
OUT_DIR = _cfg.OUT_DIR
OUT_DIR.mkdir(parents=True, exist_ok=True)

os.environ.setdefault("HF_HOME",            _cfg.HF_CACHE_DIR)
os.environ.setdefault("TRANSFORMERS_CACHE", _cfg.HF_CACHE_DIR)

from src.ingestion.stages.parse_stage import (  # noqa: E402
    NS, INLINE_HEADINGS,
    normalize_country_name,
    first_text, all_text,
    parse_authors, parse_metadata,
    section_tags, split_inline_sections,
    parse_sections, _reclassify_from_other,
)
from src.ingestion.stages.geo_stage import (  # noqa: E402
    GEO_CACHE, LAST_CALL,
    COUNTRY_PATTERNS, RIVER_TO_COUNTRY, RIVER_PATTERNS, REGION_PATTERNS,
    INVALID_LOCATIONS, GENERIC_REGION_NOISE, _GEO_NOT_COUNTRY,
    ensure_country_dict, ensure_list_of_country_dicts,
    ensure_river_dict, ensure_list_of_river_dicts,
    is_valid_place, classify_location, is_valid_region_name,
    normalize_river_name, normalize_country_item, _country_name_is_valid,
    merge_countries, compute_geo_confidence,
    geonames_type, geonames_lookup, geocode_place,
    parse_ner_results, extract_tei_countries, author_name_set, extract_author_geo,
)
# ─────────────────────────────────────────────────────────────────────────────
# KNOWLEDGE BASE  (loaded once at module import)
# ─────────────────────────────────────────────────────────────────────────────
from src.ingestion.stages.entity_stage import (  # noqa: E402
    _KB, _get_kb,
    STUDY_TYPE_PROTOTYPES, TASK_PROTOTYPES,
    PipelineContext,
    _ENTITY_BLOCKLIST,
    load_embedding_model, load_spacy,
    classify_with_embeddings, classify_task_with_embeddings,
    embedding_score, region_context_score, refine_study_area_with_embeddings,
    best_mention_context,
    disambiguate_scs, disambiguate_sma,
    _disambiguation_confidence, _apply_sense,
    compute_edge_confidence, build_entity_lineage, disambiguate_entity,
    compute_section_boost, add_context_score, detect_role,
    compute_entity_score, decide_entity, apply_llm_judge, resolve_geo_entity,
    is_valid_study_type_label, is_study_area_context,
    sanitize_study_type, detect_study_type, classify_task,
    detect_countries, detect_study_countries_from_text,
    extract_rivers, score_rivers, enrich_river_country,
    extract_regions, normalize_regions,
    extract_study_area_structured, extract_data_geo,
    validate_with_ner, enrich_with_coordinates,
    extract_geo, extract_entities,
    _entity_is_blocked, _deduplicate_by_canonical,
    run_entity_pipeline,
    needs_judge, judge_paper_with_ollama, apply_judge_verdict, apply_constraints,
    OllamaJudge, is_ollama_available,
    VALID_TASK_LABELS, VALID_STUDY_TYPES,
)
from src.ingestion.utils import json_safe, make_hash
# SDOM BRIDGE HELPERS  (implementations live in stages/sdom_bridge.py)
# ─────────────────────────────────────────────────────────────────────────────

from src.ingestion.stages.sdom_bridge import (   # noqa: E402
    metadata_from_doc  as _metadata_from_doc_impl,
    sections_from_doc  as _sections_from_doc_impl,
    ref_to_pipeline_dict,
)


def _metadata_from_doc(doc, xml_path: Path) -> dict:
    """Delegate to stages.sdom_bridge (implementation extracted there)."""
    return _metadata_from_doc_impl(doc, xml_path)


def _sections_from_doc(doc) -> dict:
    """Delegate to stages.sdom_bridge (implementation extracted there)."""
    return _sections_from_doc_impl(doc)


def _ref_to_pipeline_dict(ref) -> dict:
    """Delegate to stages.sdom_bridge (implementation extracted there)."""
    return ref_to_pipeline_dict(ref)


# ─────────────────────────────────────────────────────────────────────────────
# MAIN BUILD
# ─────────────────────────────────────────────────────────────────────────────

def build_paper_json(
    xml_path: Path,
    doc=None,                           # Optional TEIDocument; skips XML re-parse
    encode_fn: Optional[Callable] = None,
    ner_entities: Optional[list] = None,
) -> dict:
    """
    Build the paper JSON through all deterministic + embedding layers.

    All parsing goes through TEIParser → TEIDocument (SDOM).  The legacy
    direct-lxml path has been removed; callers that previously passed only
    xml_path now get an automatic TEIParser parse, identical to the Ray path.

    LLM judge validation is intentionally NOT called here — the caller
    (process_paper Ray task or the CLI run()) is responsible for calling
    needs_judge() and apply_judge_verdict() if LLM validation is required.

    Args:
        xml_path:     Path to the .tei.xml file.
        doc:          Pre-parsed TEIDocument.  If None, parsed here via TEIParser.
        encode_fn:    Callable[[list[str]], array-like] — wraps embedding
                      inference (actor or local model).  None → skip
                      embedding-based features with graceful fallback.
        ner_entities: Pre-computed SpacyActor entity list
                      [{"text": str, "label": str}, ...].
                      None → NER-based features are skipped.

    Returns:
        JSON-serialisable paper dict (without llm_judge applied).
    """
    if doc is None:
        from src.document import TEIParser
        _paper_id = xml_path.stem.replace(".tei", "")
        doc = TEIParser().parse_file(xml_path, _paper_id)

    metadata   = _metadata_from_doc(doc, xml_path)
    sections   = _sections_from_doc(doc)
    references = [_ref_to_pipeline_dict(r) for r in doc.references]

    ctx   = PipelineContext(sections)
    title = metadata.get("title", "")

    entities = extract_entities(
        ctx, metadata,
        title=title,
        ner_entities=ner_entities,
        encode_fn=encode_fn,
    )
    kb        = _get_kb()
    extractor = EntityExtractor(kb)
    entities["sensor_types"] = extractor.infer_sensor_types(
        entities.get("satellites", []),
        entities.get("dems", []),
    )
    entities["task"] = classify_task(ctx, encode_fn=encode_fn)

    paper = {
        "metadata": {
            **metadata,
            "content_hash": make_hash(ctx.full_text),
        },
        "sections":   sections,
        "entities":   entities,
        "references": references,
        "llm_judge":  None,
        "study_type": (
            entities.get("geo", {}).get("study_type", {}).get("label")
        ),
        "task": entities.get("task"),
        "provenance": {
            "parser":      "grobid_tei_kb_v2",
            "source_xml":  str(xml_path),
            "has_geo":     bool(
                entities.get("geo", {})
                .get("study_geo", {})
                .get("primary_country")
            ),
            "judge_used":  False,
            "judge_model": None,
        },
    }

    # Deterministic constraint layer (always applied)
    paper = apply_constraints(paper)

    return json_safe(paper)


def run():
    """
    CLI entry point — single-machine sequential processing.

    Models are loaded ONCE here (lazy) and reused across all files.
    For distributed multi-machine processing use pipeline_runner.py instead.
    """
    xml_files = sorted(XML_DIR.glob("*.tei.xml"))
    print(f"Found {len(xml_files)} XML files")
    if not xml_files:
        return

    # ── load models ONCE (CLI mode) ───────────────────────────────────────
    spacy_model_name = os.getenv("SPACY_MODEL", "en_core_web_trf")
    _nlp   = load_spacy(spacy_model_name)
    _model = load_embedding_model()

    def encode_fn(texts: list) -> list:
        return _model.encode(texts)

    for xml_file in xml_files:
        try:
            print(f"Building JSON: {xml_file.name}")

            # Pre-parse through SDOM so build_paper_json skips the second XML parse.
            # Also drives NER on the canonical body_text() rather than a raw XML dump.
            from src.document import TEIParser
            _paper_id = xml_file.stem.replace(".tei", "")
            tei_doc   = TEIParser().parse_file(xml_file, _paper_id)
            full_txt  = tei_doc.body_text()
            _spacy_d  = _nlp(full_txt)
            ner_ents  = [{"text": e.text.strip(), "label": e.label_} for e in _spacy_d.ents]
            del _spacy_d, full_txt

            paper = build_paper_json(xml_file, doc=tei_doc, encode_fn=encode_fn, ner_entities=ner_ents)
            del tei_doc

            # LLM judge (direct HTTP — CLI mode only)
            if needs_judge(paper):
                from src.validation.judge_normalizer import normalize_judge_verdict
                _pid = paper.get("metadata", {}).get("paper_id", xml_file.stem)
                log.info("[judge-dispatch] paper_id=%s (CLI mode)", _pid)
                _t0  = time.time()
                raw_verdict = judge_paper_with_ollama(paper)
                verdict     = normalize_judge_verdict(raw_verdict)
                paper       = apply_judge_verdict(paper, verdict)
                _lat = round(time.time() - _t0, 3)
                _st  = verdict.get("status")
                if _st not in {"skipped", "failed", None}:
                    paper["llm_judge"]                 = verdict
                    paper["provenance"]["judge_used"]  = True
                    paper["provenance"]["judge_model"] = os.getenv("OLLAMA_MODEL", "mistral-nemo:12b")
                    paper["provenance"]["judge_latency_s"] = _lat
                    log.info("[judge-success] paper_id=%s latency=%.3fs", _pid, _lat)
                elif _st == "skipped":
                    log.info("[judge-skipped] paper_id=%s latency=%.3fs reason=%s",
                             _pid, _lat, verdict.get("reason", "unknown"))
                else:
                    log.warning("[judge-failed] paper_id=%s latency=%.3fs reason=%s",
                                _pid, _lat, verdict.get("reason", "unknown"))

            out_path = OUT_DIR / f"{xml_file.stem}.paper.json"
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(json_safe(paper), f, indent=2, ensure_ascii=False)
            print(f"Saved: {out_path.name}")
        except Exception as e:
            print(f"Error in {xml_file.name}: {type(e).__name__}: {e}")
            traceback.print_exc()


if __name__ == "__main__":
    run()
