"""
entity_stage.py — backward-compatible re-export shim.

Implementations have been split into four focused modules:
  - stages/disambiguation.py       (acronym disambiguation, edge confidence)
  - stages/embedding_classifier.py (study-type / task classification)
  - stages/geo_extractor.py        (country / river / region / study-area extraction)
  - stages/entity_pipeline.py      (KB singleton, PipelineContext, entity scoring, run_entity_pipeline)

All public names that existed in the original entity_stage.py are re-exported
here so that external callers (pipeline.py, process_paper.py, pipeline_runner.py,
tests) require no changes.
"""
from __future__ import annotations

# ── disambiguation ────────────────────────────────────────────────────────────
from src.ingestion.stages.disambiguation import (
    RESOLVER_VERSION,
    ONTOLOGY_VERSION,
    best_mention_context,
    compute_entity_score,
    disambiguate_scs,
    disambiguate_sma,
    _disambiguation_confidence,
    _apply_sense,
    compute_edge_confidence,
    build_entity_lineage,
    disambiguate_entity,
    _extend_disambig_table_from_kb,
    _DISAMBIG_TABLE,
    _SCS_HYDRO_SIGNALS,
    _SCS_HYDRO_META,
    _SMA_HYDRO_SIGNALS,
    _SMA_RS_SIGNALS,
    _SMA_HYDRO_META,
    _SMA_RS_META,
)

# ── embedding_classifier ──────────────────────────────────────────────────────
from src.ingestion.stages.embedding_classifier import (
    STUDY_TYPE_PROTOTYPES,
    TASK_PROTOTYPES,
    classify_with_embeddings,
    classify_task_with_embeddings,
    embedding_score,
    region_context_score,
    refine_study_area_with_embeddings,
    is_valid_study_type_label,
    is_study_area_context,
    sanitize_study_type,
    detect_study_type,
    _HYDRO_MODEL_SIGNALS,
    classify_task,
)

# ── geo_extractor ─────────────────────────────────────────────────────────────
from src.ingestion.stages.geo_extractor import (
    detect_countries,
    detect_study_countries_from_text,
    extract_rivers,
    score_rivers,
    enrich_river_country,
    extract_regions,
    normalize_regions,
    extract_study_area_structured,
    extract_data_geo,
    validate_with_ner,
    enrich_with_coordinates,
    extract_geo,
    _RIVER_CITATION_SIGNALS,
    _RIVER_STUDY_SIGNALS,
)

# ── entity_pipeline ───────────────────────────────────────────────────────────
from src.ingestion.stages.entity_pipeline import (
    _KB,
    _get_kb,
    PipelineContext,
    load_embedding_model,
    load_spacy,
    _ENTITY_BLOCKLIST,
    _SECTION_BOOSTS,
    compute_section_boost,
    add_context_score,
    detect_role,
    decide_entity,
    apply_llm_judge,
    resolve_geo_entity,
    _entity_is_blocked,
    _deduplicate_by_canonical,
    run_entity_pipeline,
    extract_entities,
)

# ── judge_stage pass-through (unchanged) ─────────────────────────────────────
from src.ingestion.stages.judge_stage import (
    VALID_TASK_LABELS,
    VALID_STUDY_TYPES,
    OllamaJudge,
    is_ollama_available,
    needs_judge,
    judge_paper_with_ollama,
    apply_judge_verdict,
    apply_constraints,
)
