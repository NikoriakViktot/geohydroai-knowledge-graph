"""Per-paper structured extraction — the supplementary table.

One row per relevant paper, with the fields a reader of this manuscript would
want to compare against: which sensor, which vertical reference, which tide
convention, how slope was computed, what the independent statistical unit was,
and the verbatim sentence that supports the summary.

Every unknown is the literal string `"not_stated"`. That is the whole discipline
of this module: a paper that does not say which tide convention it used has not
implicitly used the common one, and a paper that mentions reaches has not thereby
computed slope per reach. Inferring either from topic vocabulary is the failure
mode this project has been bitten by before, so the prompt forbids it and the
defaults enforce it.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import pandas as pd

from src.paper_3._utils import OUT_DIR, load_paper_json, paper_sections, resolve_gemini_key
from src.paper_3.classify_relation import call_gemini, call_ollama, parse_response
from src.paper_3.evidence import build_passages, verify_quote
from src.paper_3.theses import RELATIONS, SYSTEM_CLASSES, Thesis, load_theses

logger = logging.getLogger(__name__)

NOT_STATED = "not_stated"

#: Free-text fields. Every one defaults to NOT_STATED, never to "" or None.
TEXT_FIELDS = (
    "study_area", "water_body_type", "system_class", "sensors", "sensor_detail",
    "vertical_reference", "geoid_model", "vertical_datum",
    "permanent_tide_convention", "datum_transform_spatial",
    "spatial_collocation_rule", "temporal_collocation_rule",
    "wse_accuracy_value", "wse_accuracy_unit", "wse_accuracy_metric",
    "longitudinal_slope_value", "longitudinal_slope_unit", "slope_computation_unit",
    "gauge_validation", "n_gauges", "bathymetry_source", "historical_data_used",
    "wind_setup_treated", "seiche_treated", "backwater_treated",
    "hydrodynamic_variability_note", "independent_statistical_unit",
    # 2026-09-18: surface-transformation families (vegetation, roughness,
    # channel, DEM validation, SAR) — same discipline, not_stated by default.
    "vegetation_metric", "vegetation_time_series", "woody_detection_method",
    "roughness_n_value", "roughness_n_source", "channel_width_method",
    "dem_validation_type", "sar_method", "false_positive_treatment",
    "major_result", "limitations",
)

BASE_COLUMNS = [
    "paper_id", "doi", "title", "year", "journal", "cited_by_count", "is_oa", "cohort",
]
PROVENANCE_COLUMNS = [
    "supporting_quote", "quote_section", "quote_page", "quote_chunk_id",
    "quote_source_file", "quote_verified", "quote_similarity",
    "extraction_model", "extraction_confidence", "n_theses_relevant", "run_id",
]

_GENERATION_CONFIG = {
    "temperature": 0.0,
    "max_output_tokens": 1600,
    "response_mime_type": "application/json",
}

PROMPT = """STRICT RULES - READ BEFORE EVERYTHING ELSE:
1. Answer ONLY from the PASSAGES below. Do not use outside knowledge about this
   paper or the field.
2. If the passages do not state a value, write exactly "not_stated". Never infer
   a value from related vocabulary. A paper that mentions "reaches" has NOT
   thereby computed slope per reach. A paper that does not name a tide convention
   has NOT used a default one.
3. supporting_quote MUST be copied character-for-character from ONE passage. It is
   checked against the source automatically.
4. Output a single JSON object and nothing else.

PAPER: {title} ({year}, {journal}) doi:{doi}

PASSAGES:
{passages}

Return exactly this JSON shape, every value a string except the numbers noted:
{{"study_area": "", "water_body_type": "", "system_class": "{systems}",
  "sensors": "comma-separated, e.g. ICESat-2 ATL13, SWOT PIXC, Sentinel-2",
  "sensor_detail": "",
  "vertical_reference": "ellipsoidal|orthometric|not_stated",
  "geoid_model": "e.g. EGM2008, EGM96, EGG2015, or not_stated",
  "vertical_datum": "EPSG code or datum name, or not_stated",
  "permanent_tide_convention": "mean-tide|zero-tide|tide-free|not_stated",
  "datum_transform_spatial": "spatially_varying|single_constant|not_applicable|not_stated",
  "spatial_collocation_rule": "", "temporal_collocation_rule": "",
  "wse_accuracy_value": "", "wse_accuracy_unit": "",
  "wse_accuracy_metric": "RMSE|MAD|std|bias|not_stated",
  "longitudinal_slope_value": "", "longitudinal_slope_unit": "",
  "slope_computation_unit": "per_reach|per_pass|individual_points|not_computed|not_stated",
  "gauge_validation": "yes|no|not_stated", "n_gauges": "",
  "bathymetry_source": "", "historical_data_used": "",
  "wind_setup_treated": "yes|no|not_stated", "seiche_treated": "yes|no|not_stated",
  "backwater_treated": "yes|no|not_stated", "hydrodynamic_variability_note": "",
  "independent_statistical_unit": "overpass|pass|photon|pixel|date|not_stated",
  "vegetation_metric": "e.g. NDVI, cover fraction, class area, species count, or not_stated",
  "vegetation_time_series": "multi-year|single-date|not_applicable|not_stated",
  "woody_detection_method": "e.g. field survey, Dynamic World, lidar canopy height, or not_stated",
  "roughness_n_value": "Manning n value(s) stated, with class, or not_stated",
  "roughness_n_source": "measured|calibrated|literature_prior|not_stated",
  "channel_width_method": "e.g. transects on water mask, RivWidthCloud, field, or not_stated",
  "dem_validation_type": "independent_soundings|blocked_cv|random_cv|pseudo_points|none|not_stated",
  "sar_method": "e.g. thresholding, change detection, discriminant, random forest, or not_stated",
  "false_positive_treatment": "e.g. HAND/terrain veto, land-cover mask, none, or not_stated",
  "major_result": "at most 50 words", "limitations": "at most 50 words",
  "supporting_quote": "", "confidence": 0.0}}"""


def build_prompt(paper_meta: dict, passages) -> str:
    return PROMPT.format(
        title=paper_meta.get("title") or "(title unavailable)",
        year=paper_meta.get("year") or "n.d.",
        journal=paper_meta.get("journal") or "(journal unavailable)",
        doi=paper_meta.get("doi") or "(no DOI)",
        passages="\n\n".join(p.as_prompt_line() for p in passages),
        systems="|".join(SYSTEM_CLASSES),
    )


def normalise_extraction(parsed: dict | None) -> dict:
    """Coerce a response into the fixed shape with not_stated defaults."""
    out = {field: NOT_STATED for field in TEXT_FIELDS}
    out["supporting_quote"] = ""
    out["extraction_confidence"] = 0.0
    if not parsed:
        return out

    for field in TEXT_FIELDS:
        value = parsed.get(field)
        if value is None:
            continue
        text = str(value).strip()
        # Empty strings and null-ish placeholders are unknowns, not answers.
        if not text or text.lower() in {"none", "null", "n/a", "na", "-", "—", "unknown"}:
            continue
        out[field] = text

    if out["system_class"] not in SYSTEM_CLASSES:
        out["system_class"] = NOT_STATED

    out["supporting_quote"] = str(parsed.get("supporting_quote", "") or "")
    try:
        out["extraction_confidence"] = min(max(float(parsed.get("confidence", 0.0)), 0.0), 1.0)
    except (TypeError, ValueError):
        out["extraction_confidence"] = 0.0
    return out


def relevant_papers(relations: pd.DataFrame) -> pd.DataFrame:
    """Papers with at least one non-NOT_RELEVANT relation, with their thesis map."""
    if relations.empty:
        return pd.DataFrame(columns=["paper_id", "doi", "title", "year"])
    judged = relations[relations["relation"].isin(RELATIONS)]
    if judged.empty:
        return pd.DataFrame(columns=["paper_id", "doi", "title", "year"])
    return (judged.groupby("paper_id")
            .agg(doi=("doi", "first"), title=("title", "first"),
                 year=("year", "first"), n_theses_relevant=("thesis_id", "nunique"))
            .reset_index())


def relation_columns(relations: pd.DataFrame, paper_id: str,
                     theses: list[Thesis]) -> dict:
    """The wide relation_T01…relation_T24 columns for one paper."""
    rows = relations[relations["paper_id"] == paper_id]
    by_thesis = dict(zip(rows["thesis_id"], rows["relation"]))
    return {f"relation_{t.id}": by_thesis.get(t.id, "") for t in theses}


def _all_key_terms(theses: list[Thesis]) -> tuple[tuple[str, ...], ...]:
    """Union of every thesis's key-term families, for passage selection.

    Extraction is per paper rather than per thesis, so the passages offered must
    cover any thesis the paper was matched to.
    """
    return tuple(fam for t in theses for fam in t.key_terms)


def run(
    theses: list[Thesis] | None = None,
    out_dir: Path | None = None,
    llm: str = "gemini",
    limit: int | None = None,
    max_passages: int = 16,
    run_id: str = "",
) -> pd.DataFrame:
    """Extract the structured table for every relevant paper."""
    theses = theses if theses is not None else load_theses()
    target = Path(out_dir) if out_dir else OUT_DIR
    relations_path = target / "relations.parquet"
    if not relations_path.exists():
        raise FileNotFoundError(
            f"{relations_path} missing — run --step finalize first")
    relations = pd.read_parquet(relations_path)

    papers = relevant_papers(relations)
    if limit:
        papers = papers.head(limit)
    logger.info("Extracting fields for %d papers with %s", len(papers), llm)

    api_key = resolve_gemini_key() if llm == "gemini" else ""
    if llm == "gemini" and not api_key:
        raise RuntimeError("No Gemini API key: set GEMINI_API_KEY or GOOGLE_API_KEY")

    families = _all_key_terms(theses)
    index_path = target / "corpus_index.parquet"
    meta_by_id = {}
    if index_path.exists():
        meta_by_id = {r["paper_id"]: r
                      for r in pd.read_parquet(index_path).to_dict("records")}

    rows = []
    for i, p in enumerate(papers.itertuples(), 1):
        paper = load_paper_json(p.paper_id)
        if not paper:
            logger.warning("%s: not loadable — skipped", p.paper_id)
            continue
        passages = build_passages(paper_sections(paper), p.paper_id,
                                  f"{p.paper_id}.json", families,
                                  max_passages=max_passages)
        if not passages:
            logger.warning("%s: no usable passages — skipped", p.paper_id)
            continue

        info = meta_by_id.get(p.paper_id, {})
        meta = {"title": p.title, "year": p.year, "doi": p.doi,
                "journal": info.get("journal", "")}
        prompt = build_prompt(meta, passages)

        text, model = (call_ollama(prompt) if llm == "ollama"
                       else call_gemini(prompt, api_key))
        fields = normalise_extraction(parse_response(text))

        offered = {q.passage_id: q.text for q in passages}
        verdict = verify_quote(fields["supporting_quote"], offered)
        source = next((q for q in passages if q.passage_id == verdict.passage_id), None)

        row = {
            "paper_id": p.paper_id,
            "doi": p.doi or "",
            "title": p.title or "",
            "year": p.year,
            "journal": info.get("journal", ""),
            "cited_by_count": info.get("cited_by_count", 0),
            "is_oa": info.get("is_oa", ""),
            "cohort": info.get("cohort", "base"),
            **{k: v for k, v in fields.items()
               if k not in ("supporting_quote", "extraction_confidence")},
            "supporting_quote": verdict.text if verdict.verified else "",
            "quote_section": source.section if source else "",
            "quote_page": source.page if source else None,
            "quote_chunk_id": source.chunk_id if source else "",
            "quote_source_file": source.source_file if source else "",
            "quote_verified": verdict.verified,
            "quote_similarity": verdict.similarity,
            "extraction_model": model,
            "extraction_confidence": fields["extraction_confidence"],
            "n_theses_relevant": int(p.n_theses_relevant),
            "run_id": run_id,
        }
        row.update(relation_columns(relations, p.paper_id, theses))
        rows.append(row)
        if i % 20 == 0:
            logger.info("  … %d/%d", i, len(papers))

    columns = (BASE_COLUMNS + list(TEXT_FIELDS) + PROVENANCE_COLUMNS
               + [f"relation_{t.id}" for t in theses])
    frame = pd.DataFrame(rows, columns=columns)
    target.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(target / "paper_extraction.parquet", index=False)
    frame.to_csv(target / "PAPER_EXTRACTION.csv", index=False)

    if not frame.empty:
        stated = {f: int((frame[f] != NOT_STATED).sum())
                  for f in ("permanent_tide_convention", "geoid_model",
                            "slope_computation_unit", "independent_statistical_unit")}
        logger.info("Extraction: %d papers; fields actually stated: %s",
                    len(frame), stated)
    return frame
