"""
entity_pipeline.py — KB singleton, PipelineContext, entity scoring, and pipeline runner.

Contains:
  - _KB / _get_kb()             — KnowledgeBase singleton
  - PipelineContext              — structured text views over paper sections
  - load_embedding_model()       — SentenceTransformer loader (CLI / test use)
  - load_spacy()                 — spaCy model loader (CLI / test use)
  - _ENTITY_BLOCKLIST            — hard-reject token set
  - _SECTION_BOOSTS / compute_section_boost() / add_context_score() / detect_role()
  - compute_entity_score() / decide_entity() / apply_llm_judge() / resolve_geo_entity()
  - _entity_is_blocked() / _deduplicate_by_canonical()
  - run_entity_pipeline()        — probabilistic scoring + disambiguation loop
  - extract_entities()           — main extraction entry point
"""
from __future__ import annotations

import os
import sys
import logging
from pathlib import Path
from typing import Optional, Callable

from src.ingestion.knowledge import (
    KnowledgeBase,
    load_knowledge_base,
    EntityExtractor,
)
from src.ingestion.utils import clean_text, snippet, ensure_dict, build_full_text
from src.ingestion.stages.disambiguation import (
    _extend_disambig_table_from_kb,
    best_mention_context,
    compute_entity_score,
    disambiguate_scs, disambiguate_sma, disambiguate_entity,
    RESOLVER_VERSION, ONTOLOGY_VERSION,
    compute_edge_confidence, build_entity_lineage,
)
from src.ingestion.stages.embedding_classifier import (
    classify_task, embedding_score,
)
from src.ingestion.stages.geo_extractor import extract_geo
from src.ingestion.stages.judge_stage import (
    VALID_TASK_LABELS, VALID_STUDY_TYPES,
    OllamaJudge, is_ollama_available,
    needs_judge, judge_paper_with_ollama,
    apply_judge_verdict, apply_constraints,
)
from src.ingestion.stages.geo_stage import (
    geonames_lookup,
)

log = logging.getLogger(__name__)

_SODB_DIR = Path(__file__).resolve().parents[3] / "data" / "sodb"

# ─────────────────────────────────────────────────────────────────────────────
# KB SINGLETON
# ─────────────────────────────────────────────────────────────────────────────

_KB: Optional[KnowledgeBase] = None


def _get_kb() -> KnowledgeBase:
    global _KB
    if _KB is None:
        _KB = load_knowledge_base()
        _extend_disambig_table_from_kb(_KB)
    return _KB


# ─────────────────────────────────────────────────────────────────────────────
# PIPELINE CONTEXT
# ─────────────────────────────────────────────────────────────────────────────

class PipelineContext:
    def __init__(self, sections: dict):
        self.sections     = ensure_dict(sections)
        self.abstract     = self.sections.get("abstract", "")
        self.introduction = self.sections.get("introduction", "")
        self.study_area   = self.sections.get("study_area", "")
        self.data_sources = self.sections.get("data_sources", "")
        self.methods      = self.sections.get("methods", "")
        self.results      = self.sections.get("results", "")
        self.conclusion   = self.sections.get("conclusion", "")
        self.other        = self.sections.get("other", "")
        self.full_text    = build_full_text(self.sections)

        self.study_country_text = " ".join([
            self.abstract, self.study_area,
            self.data_sources, self.methods, self.other,
        ])
        self.satellite_text = " ".join([
            self.abstract, self.data_sources, self.methods, self.other,
        ])
        self.method_text = " ".join([
            self.abstract, self.data_sources, self.methods,
            self.results, self.other,
        ])
        self.metric_text = " ".join([
            self.abstract, self.methods, self.results, self.conclusion,
        ])


# ─────────────────────────────────────────────────────────────────────────────
# MODEL LOADERS  (lazy — call explicitly, never at import time)
# ─────────────────────────────────────────────────────────────────────────────

def load_embedding_model():
    """Load and return a local SentenceTransformer instance (CLI / test use)."""
    from dotenv import load_dotenv
    from sentence_transformers import SentenceTransformer
    load_dotenv()
    hf_token   = os.getenv("HF_TOKEN", "")
    model_name = os.getenv("EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
    return SentenceTransformer(
        model_name,
        device="cpu",
        use_auth_token=hf_token or None,
    )


def load_spacy(model_name: str = "en_core_web_md"):
    """Load and return a local spaCy model (CLI / test use)."""
    import subprocess
    import spacy
    try:
        return spacy.load(model_name)
    except OSError:
        print(f"spaCy model '{model_name}' not found — installing...")
        subprocess.run(
            [sys.executable, "-m", "spacy", "download", model_name], check=True
        )
        import spacy as _spacy
        return _spacy.load(model_name)


# ─────────────────────────────────────────────────────────────────────────────
# ENTITY BLOCKLIST
# ─────────────────────────────────────────────────────────────────────────────

_ENTITY_BLOCKLIST: frozenset[str] = frozenset({
    "HTTP", "HTTPS", "URL", "WWW",
    "J", "R", "T", "K", "S", "M", "N",
    "NNT", "NET", "REF", "TAG", "ALS",
    "CLASSIFICATION", "REMOTE", "SENSING", "ANALYSIS",
    "PROCESS", "PROCESSING", "DATA", "MAP", "MAPPING",
    "STUDY", "AREA", "REGION", "TABLE", "FIGURE", "FIG",
    "MM", "CM", "KM", "M2", "KM2", "HA", "HZ", "DB",
})

# ─────────────────────────────────────────────────────────────────────────────
# SECTION-AWARE IMPORTANCE BOOSTING
# ─────────────────────────────────────────────────────────────────────────────

_SECTION_BOOSTS: dict[str, float] = {
    "abstract":     0.20,
    "methods":      0.25,
    "conclusion":   0.15,
    "study_area":   0.10,
    "data_sources": 0.10,
}


def compute_section_boost(name: str, ctx: PipelineContext, title: str = "") -> float:
    """Additive score boost based on how many key sections mention the entity."""
    name_l = name.lower()
    boost  = 0.0
    if title and name_l in title.lower():
        boost += 0.25
    for section, weight in _SECTION_BOOSTS.items():
        text = getattr(ctx, section, "")
        if text and name_l in text.lower():
            boost += weight
    return boost


def add_context_score(entity: dict, ctx_text: str) -> dict:
    if not entity.get("name") or not ctx_text:
        return entity
    ctx   = best_mention_context(entity["name"], ctx_text)
    score = 0
    ctx_l = ctx.lower()
    if any(k in ctx_l for k in ["study area", "case study", "located in",
                                  "study site", "basin"]):
        score += 0.5
    if any(k in ctx_l for k in ["used", "applied", "analysis", "simulation"]):
        score += 0.3
    entity["scores"]["context"] = score
    entity["evidence"] = ctx or entity["evidence"]
    return entity


def detect_role(entity: dict, ctx_text: str) -> dict:
    ctx   = best_mention_context(entity["name"], ctx_text)
    ctx_l = ctx.lower()
    if any(k in ctx_l for k in ["used", "applied", "model", "analysis",
                                  "derived from", "calculated"]):
        entity["role"] = "used"
    else:
        entity["role"] = "mentioned"
    return entity


def decide_entity(entity: dict, threshold: float = 0.6) -> dict:
    entity = compute_entity_score(entity)
    entity["accepted"] = entity["final_score"] >= threshold
    return entity


def apply_llm_judge(entity: dict, judge_result: dict) -> dict:
    if not judge_result:
        return entity
    accepted   = judge_result.get("accepted", True)
    confidence = judge_result.get("confidence", 0.5)
    entity["scores"]["llm"] = confidence
    if not accepted:
        entity["final_score"] *= 0.5
        entity["accepted"]     = False
    return entity


def resolve_geo_entity(entity: dict, context_text: str) -> dict:
    geo = geonames_lookup(entity["name"])
    if not geo:
        return entity
    score = 0
    if geo["country"] and geo["country"].lower() in context_text.lower():
        score += 0.5
    if geo["type"] == entity.get("type"):
        score += 0.3
    entity["geo"] = geo
    entity["scores"]["context"] += score
    return entity


# ─────────────────────────────────────────────────────────────────────────────
# ENTITY PIPELINE INTERNALS
# ─────────────────────────────────────────────────────────────────────────────

def _entity_is_blocked(name: str) -> bool:
    stripped = (name or "").strip()
    if len(stripped) <= 1:
        return True
    return stripped.upper() in _ENTITY_BLOCKLIST


def _deduplicate_by_canonical(entities: list[dict]) -> list[dict]:
    """Keep highest-scoring entity when multiple share the same KB full_name."""
    by_canonical: dict[str, dict] = {}
    passthrough: list[dict] = []
    for e in entities:
        full_name = (e.get("kb_metadata") or {}).get("full_name", "")
        if not full_name:
            passthrough.append(e)
            continue
        key = full_name.lower()
        if key not in by_canonical:
            by_canonical[key] = e
        else:
            existing = by_canonical[key]
            if (e.get("final_score") or 0) > (existing.get("final_score") or 0):
                e.setdefault("alt_evidence", existing.get("evidence", ""))
                by_canonical[key] = e
            else:
                existing.setdefault("alt_evidence", e.get("evidence", ""))
    return passthrough + list(by_canonical.values())


def run_entity_pipeline(
    entities: list[dict],
    ctx: PipelineContext,
    encode_fn: Optional[Callable] = None,
    title: str = "",
) -> list[dict]:
    result = []
    for e in entities:
        name = (e.get("name") or "").strip()
        if _entity_is_blocked(name):
            log.debug("[entity-fp] blocked entity name=%r", name)
            continue

        e = add_context_score(e, ctx.full_text)
        e = detect_role(e, ctx.full_text)
        e = disambiguate_scs(e, ctx.full_text)
        e = disambiguate_sma(e, ctx.full_text)
        e = disambiguate_entity(e, ctx.full_text)

        if e["scores"].get("embedding", 0) == 0 and ctx.method_text and encode_fn is not None:
            e["scores"]["embedding"] = embedding_score(
                e["name"], [ctx.method_text[:2000]], encode_fn
            )

        e = compute_entity_score(e)
        boost = compute_section_boost(e["name"], ctx, title=title)
        e["final_score"] = min(1.0, round(e["final_score"] + boost, 4))
        e["accepted"] = e["final_score"] >= 0.3
        result.append(e)

    return _deduplicate_by_canonical(result)


# ─────────────────────────────────────────────────────────────────────────────
# NOUGAT SEMANTIC BRIDGE  (PATCH 5)
# ─────────────────────────────────────────────────────────────────────────────

def _augment_metric_text_from_nougat(ctx: PipelineContext, paper_id: str) -> None:
    # Metrics inside equations/tables are never in TEI prose; Nougat OCR output
    # stored in SODB regions.parquet is the only source for them.
    regions_file = _SODB_DIR / paper_id / "regions.parquet"
    if not regions_file.exists():
        return
    try:
        import pandas as pd
        rdf = pd.read_parquet(regions_file)
        if "nougat_status" not in rdf.columns:
            # written before the Nougat gate: unchecked generative output is not a
            # metric source (rescore with src.orchestration.rescore_nougat_regions)
            log.debug("[nougat-bridge] %s: regions not gated, skipped", paper_id)
            return
        mask = (
            rdf["region_type"].isin(["FORMULA_REGION", "TABLE_REGION"]) &
            (rdf["nougat_status"] == "accepted") &
            rdf["nougat_text"].notna()
        )
        supplement = " ".join(rdf.loc[mask, "nougat_text"])
        if supplement.strip():
            ctx.metric_text = ctx.metric_text + " " + supplement
            log.debug("[nougat-bridge] %s: appended %d chars from regions.parquet",
                      paper_id, len(supplement))
    except Exception as exc:
        log.debug("[nougat-bridge] %s: skipped (%s)", paper_id, exc)


# ─────────────────────────────────────────────────────────────────────────────
# MAIN EXTRACTION ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────

def extract_entities(
    ctx: PipelineContext,
    metadata: dict,
    title: str = "",
    ner_entities: Optional[list] = None,
    encode_fn: Optional[Callable] = None,
) -> dict:
    """
    Main extraction entry point.
    Layers: deterministic (KB patterns) → probabilistic (scoring).
    LLM judge validation is intentionally NOT called here — the caller is responsible.
    """
    kb        = _get_kb()
    extractor = EntityExtractor(kb)

    geo = extract_geo(ctx, metadata, ner_entities=ner_entities, encode_fn=encode_fn)

    # PATCH 5: widen metric scan window with Nougat formula/table text from SODB
    paper_id = metadata.get("paper_id", metadata.get("id", ""))
    if paper_id:
        _augment_metric_text_from_nougat(ctx, paper_id)

    satellites = extractor.extract_satellites(ctx.satellite_text)
    dems       = extractor.extract_dems(ctx.satellite_text)
    methods    = extractor.extract_methods(ctx.method_text)
    metrics    = extractor.extract_metrics(ctx.metric_text)
    # PATCH 1: metrics bypass run_entity_pipeline() so they never get the
    # accepted flag; downstream filters on entity["accepted"] silently drop them.
    metrics    = [dict(m, accepted=True) for m in metrics]

    satellites = run_entity_pipeline(satellites, ctx, encode_fn=encode_fn, title=title)
    dems       = run_entity_pipeline(dems,       ctx, encode_fn=encode_fn, title=title)
    methods    = run_entity_pipeline(methods,    ctx, encode_fn=encode_fn, title=title)

    return {
        "geo":        geo,
        "satellites": satellites,
        "dems":       dems,
        "methods":    methods,
        "metrics":    metrics,
    }
