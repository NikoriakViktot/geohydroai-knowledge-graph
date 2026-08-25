"""
Section-aware LLM extractor for satellite flood mapping literature.

Architecture:
    PDF chunks (page-ordered) → parse_document_sections → 3 targeted LLM calls → merge

Three LLM calls per paper:
    1. Abstract   → study_type, satellite_names, sensor_type, data_product,
                    country, region, river_basin, city_event, methods,
                    near_real_time, latency
    2. Methods    → methods, satellite_names, sensor_type, data_product (override)
    3. Results    → OA, F1, IoU, Kappa (validated: number must appear in text)

Merge policy (Task 4):
    - Section-based LLM values are NEVER overridden by regex fallback.
    - Regex fills ONLY empty fields (bibliographic + geography).
    - Metrics and methods are NEVER filled from full-text regex.

Uses src.extraction.section_parser (canonical, not the deprecated processing one).
"""
from __future__ import annotations

import json
import logging
import re
import textwrap
from typing import Any

import requests

from src.config import OLLAMA_BASE_URL, OLLAMA_MODEL, OLLAMA_TIMEOUT
from src.extraction.base import BaseExtractor, ExtractionResult, MetricFact
from src.extraction.metric_ontology import (
    metric_label as _metric_label,
    metric_group as _metric_group,
    normalize_metric_value as _ont_normalize,
    normalize_fact_value,
    resolve_sodb_canonical,
    resolve_sodb_fact,
    metric_scale,
    RATIO_METRICS,
)
from src.extraction.regex_extractor import RegexExtractor
from src.extraction.section_parser import parse_document_sections, describe_parsed

logger = logging.getLogger(__name__)

_FALLBACK = RegexExtractor()

# ── System prompt ─────────────────────────────────────────────────────────────

_SYS = textwrap.dedent("""\
    You are a scientific information extraction system for satellite-based flood mapping literature.
    Reply with valid JSON only — no prose, no markdown.
    Numeric accuracy values must be 0–1 scale (e.g. 0.95 not 95).
    Extract ONLY what is explicitly in the text. Never hallucinate.
""")

# ── Per-section prompts ───────────────────────────────────────────────────────

_ABSTRACT_PROMPT = textwrap.dedent("""\
    Extract from this paper ABSTRACT. Return JSON with exactly these keys:
      Study_Type, Satellite_Names, Sensor_Type, Data_Product,
      Country, Region, River_Basin, City_Event, Methods, Near_Real_Time, Latency

    Rules:
    - Study_Type: one of "Satellite flood mapping" | "ML/DL classification" |
      "Hydrological forecasting" | "Hydraulic modeling" |
      "Operational mapping system" | "Review paper" | "Dataset/benchmark paper"
    - Satellite_Names: comma-separated list; null if absent
    - Sensor_Type: "SAR" | "Optical" | "Multi-sensor"; null if absent
    - Data_Product: e.g. "GRD", "MSI", "OLI"; null if not mentioned
    - Country: country name; null if absent
    - Region: sub-national region or continent; null if absent
    - River_Basin: river or basin name; null if absent
    - City_Event: city name or specific flood event; null if absent
    - Methods: comma-separated list from:
        Thresholding | Change detection | NDWI/MNDWI | Random Forest | SVM |
        Maximum likelihood | U-Net | CNN | LSTM | Transformer | OBIA |
        Hydrodynamic model | Operational workflow
      null if absent
    - Near_Real_Time: one of "true" | "false" | "unclear"
        "true"    — paper explicitly describes operational/automated mapping with
                    latency < 24 h OR 'within hours/days of acquisition'
        "false"   — paper uses offline or batch processing without time constraint
        "unclear" — paper mentions 'rapid' or 'timely' but no explicit latency;
                    or paper is a review / methodology / benchmark paper
        Use "unclear" when you cannot confidently assign "true" or "false".
    - Latency: string like "<6 h" or "1–3 days"; null if absent

    ABSTRACT:
    {text}
""")

_METHODS_PROMPT = textwrap.dedent("""\
    Extract from this METHODS section. Return JSON with exactly these keys:
      Methods, Satellite_Names, Sensor_Type, Data_Product

    Rules:
    - Methods: comma-separated list of all processing approaches:
        Thresholding | Change detection | NDWI/MNDWI | Random Forest | SVM |
        Maximum likelihood | U-Net | CNN | LSTM | Transformer | OBIA |
        Hydrodynamic model | Operational workflow
    - Satellite_Names: comma-separated list; null if absent
    - Sensor_Type: "SAR" | "Optical" | "Multi-sensor"; null if absent
    - Data_Product: e.g. "GRD", "MSI", "OLI"; null if absent

    METHODS:
    {text}
""")

_RESULTS_PROMPT = textwrap.dedent("""\
    Extract ONLY explicit numeric accuracy metrics from this RESULTS section.
    Return JSON with keys: OA, F1, IoU, Kappa

    Rules:
    - OA / F1 / IoU / Kappa: float 0–1 only; range (0.84–0.95) → mean
    - Return null if the number is NOT explicitly stated in the text
    - NEVER guess or infer; "high accuracy" → null

    RESULTS:
    {text}
""")


# ── Metric validation ─────────────────────────────────────────────────────────

def _number_in_text(val: float, text: str) -> bool:
    candidates = [
        f"{val:.4f}", f"{val:.3f}", f"{val:.2f}",
        f"{val * 100:.2f}", f"{val * 100:.1f}",
        f"{val * 100:.0f}",
    ]
    for raw in candidates:
        s = raw.rstrip("0").rstrip(".")
        if not s:
            continue
        if re.search(r"(?<!\d)" + re.escape(s) + r"(?!\d)", text):
            return True
    return False


def _validate_metrics(result: ExtractionResult, text: str) -> ExtractionResult:
    for attr in ("oa", "f1", "iou", "kappa"):
        val = getattr(result, attr)
        if val is not None and not _number_in_text(val, text):
            logger.debug("Discarded hallucinated %s=%s (not in text)", attr, val)
            setattr(result, attr, None)
    return result


# ── Quality scoring (mirrors scientific_extractor, kept local to avoid circular import) ──

def _quality_score(sections: dict, satellite_csv: str) -> float:
    score  = 1 if sections.get("title")    else 0
    score += 1 if sections.get("abstract") else 0
    score += 1 if sections.get("methods")  else 0
    score += 1 if sections.get("results")  else 0
    score += 1 if satellite_csv.strip()    else 0
    return score / 5.0


def _compute_confidence(quality: float, n_sections_used: int) -> float:
    sec_norm = min(n_sections_used / 3.0, 1.0)
    return round(min(0.6 * quality + 0.4 * sec_norm, 1.0), 3)


# ── SectionExtractor ──────────────────────────────────────────────────────────

# ── Section aliases for paper_json keys ──────────────────────────────────────

_SECTION_ALIASES: dict[str, list[str]] = {
    "methods":      ["methods", "study_area", "data_sources"],
    "results":      ["results"],
    "introduction": ["introduction"],
    "discussion":   ["discussion"],
}

# Canonical IDs from ontology → ExtractionResult attribute name
_SODB_METRIC_MAP: dict[str, str] = {
    "metric.oa":        "oa",
    "metric.f1":        "f1",
    "metric.f1_score":  "f1",
    "metric.iou":       "iou",
    "metric.kappa":     "kappa",
}


def _get_section_text(sections: dict, canonical: str) -> str:
    """Return first non-empty text matching canonical section or its aliases."""
    for alias in _SECTION_ALIASES.get(canonical, [canonical]):
        v = (sections.get(alias) or "").strip()
        if v:
            return v
    return ""


def normalize_metric_value(value, metric_id: str = "overall_accuracy") -> float | None:
    """
    Normalise a metric value за науковим діапазоном метрики (metric_ontology).

    Фаза 2.1: делегує в range-aware нормалізатор — від'ємні Kappa/NSE/KGE/R²
    більше не дропаються. Дефолтний metric_id — строгий [0,1] ratio
    (історична поведінка для викликів без metric-контексту).
    """
    return _ont_normalize(metric_id, value)


def _format_authors(authors) -> str:
    """Format paper_json authors list into a readable string."""
    if not authors:
        return ""
    if isinstance(authors, str):
        return authors
    parts = []
    for a in authors:
        if isinstance(a, dict):
            name = a.get("name") or a.get("full_name") or a.get("display_name") or ""
            if name:
                parts.append(name)
        elif isinstance(a, str):
            parts.append(a)
    return "; ".join(parts)


class SectionExtractor(BaseExtractor):
    """
    Section-aware extractor for flood mapping papers.
    Falls back to RegexExtractor when Ollama is unreachable.

    Merge policy:
    - LLM section calls win for their respective fields.
    - Regex fallback fills ONLY bibliographic + geography gaps.
    - Metrics (OA/F1/IoU/Kappa) are NEVER filled from regex full-text scan.
    - Methods are NEVER filled from regex full-text scan.

    Two extraction paths:
      extract_from_paper(paper)  — preferred; reads full structured paper_json
      extract(chunks, source)    — fallback; reconstructs sections from chunk text
    """

    def __init__(
        self,
        base_url: str = OLLAMA_BASE_URL,
        model:    str = OLLAMA_MODEL,
        timeout:  int = OLLAMA_TIMEOUT,
        abstract_max: int = 1500,
        methods_max:  int = 3000,
        results_max:  int = 2000,
    ) -> None:
        self._url          = f"{base_url.rstrip('/')}/api/generate"
        self._model        = model
        self._timeout      = timeout
        self._abstract_max = abstract_max
        self._methods_max  = methods_max
        self._results_max  = results_max
        self._available: bool | None = None

    # ── Primary path: full structured paper ──────────────────────────────────

    def extract_from_paper(
        self,
        paper: dict,
        source_file: str,
    ) -> ExtractionResult:
        """
        Extract structured information from a full paper_json / enriched JSON dict.

        Reads sections directly from paper["sections"] (no chunk parsing needed).
        Metric fill order:
          1. LLM call on results section text
          2. SODB numeric_facts.parquet (table-extracted metrics)
          3. paper_json entities["metrics"] (regex-extracted during ingestion)

        Falls back to extract(chunks=[]) only when Ollama is unavailable.
        """
        sections = paper.get("sections") or {}
        meta     = paper.get("metadata") or {}

        abstract_text = paper.get("abstract") or ""   # pre-resolved by PaperStore
        methods_text  = _get_section_text(sections, "methods")
        results_text  = _get_section_text(sections, "results")

        logger.info(
            "[%s] paper sections: abstract=%d methods=%d results=%d chars",
            source_file, len(abstract_text), len(methods_text), len(results_text),
        )

        # Fallback: Ollama unavailable — use regex on available section texts
        if not self._is_available():
            logger.warning(
                "Ollama unavailable — regex fallback for %s (paper_json path)", source_file
            )
            result = ExtractionResult(source_file=source_file)
            result.title         = meta.get("title", "")
            result.doi           = meta.get("doi", "")
            result.year          = str(meta.get("year", ""))
            result.authors       = _format_authors(meta.get("authors", []))
            result.abstract      = abstract_text
            result.llm_used      = False
            result.fallback_used = True
            result.extractor_mode = "paper_json_no_llm"
            self._fill_from_sodb_metrics(result, paper)
            self._fill_from_paper_entities(result, paper)
            return result.finalize()

        result        = ExtractionResult(source_file=source_file)
        sections_used: list[str] = []

        # ── Call 1: Abstract ─────────────────────────────────────────────────
        if abstract_text:
            abs_r = self._call_section(
                _ABSTRACT_PROMPT, abstract_text[:self._abstract_max], source_file
            )
            _merge(result, abs_r, (
                "study_type", "satellite_names", "sensor_type", "data_product",
                "country", "region", "river_basin", "city_event",
                "methods", "near_real_time", "near_real_time_label", "latency",
            ))
            sections_used.append("abstract")
            for field_name in ("satellite_names", "country", "river_basin", "methods"):
                if getattr(result, field_name, None):
                    result.provenance[field_name.title().replace("_", "")] = {
                        "section": "abstract", "snippet": abstract_text[:120],
                        "source": "llm", "extractor_mode": "paper_json",
                    }

        # ── Call 2: Methods ──────────────────────────────────────────────────
        if methods_text:
            meth_r = self._call_section(
                _METHODS_PROMPT, methods_text[:self._methods_max], source_file
            )
            _merge(result, meth_r,
                   ("methods", "satellite_names", "sensor_type", "data_product"),
                   override=True)
            sections_used.append("methods")
            if result.methods:
                result.provenance["Methods"] = {
                    "section": "methods", "snippet": methods_text[:120],
                    "source": "llm", "extractor_mode": "paper_json",
                }

        # ── Call 3: Results → metrics ────────────────────────────────────────
        if results_text and result.study_type in (
            "ML/DL classification", "Satellite flood mapping",
            "Operational mapping system", "",
        ):
            res_r = self._call_section(
                _RESULTS_PROMPT, results_text[:self._results_max], source_file
            )
            res_r = _validate_metrics(res_r, results_text)
            _merge(result, res_r, ("oa", "f1", "iou", "kappa"))
            sections_used.append("results")
            # Provenance + MetricFacts for LLM-extracted metrics
            for fname, attr, metric_id in [
                ("OA",    "oa",    "overall_accuracy"),
                ("F1",    "f1",    "f1_score"),
                ("IoU",   "iou",   "iou"),
                ("Kappa", "kappa", "kappa"),
            ]:
                val = getattr(result, attr)
                if val is not None:
                    result.provenance[fname] = {
                        "section": "results", "snippet": results_text[:120],
                        "source": "llm", "extractor_mode": "paper_json",
                    }
                    result.metric_facts.append(MetricFact(
                        metric_id    = metric_id,
                        metric_label = _metric_label(metric_id),
                        metric_group = _metric_group(metric_id),
                        fact_type    = "evaluation_metric",
                        value        = val,
                        raw_value    = str(val),
                        unit         = "ratio",
                        scale        = "0_1",
                        source       = "llm",
                        section      = "results",
                        evidence_text= results_text[:200],
                        paper_id     = source_file,
                        doi          = meta.get("doi", ""),
                        confidence   = 0.85,
                    ))

        # ── Metadata always from structured paper (never regex) ───────────────
        result.title    = meta.get("title", "")
        result.doi      = meta.get("doi", "")
        result.year     = str(meta.get("year", ""))
        result.authors  = _format_authors(meta.get("authors", []))
        result.abstract = abstract_text

        # ── Metric fills: SODB parquet → paper entities ───────────────────────
        self._fill_from_sodb_metrics(result, paper)
        self._fill_from_paper_entities(result, paper)

        # ── Regex fills ONLY geography + timeliness gaps ──────────────────────
        # Build a minimal chunk list from section texts for the regex extractor
        _section_chunks = [
            {"text": t, "filename": source_file, "page_start": 0, "chunk_id": f"__sect_{i}__"}
            for i, t in enumerate([abstract_text, methods_text, results_text])
            if t
        ]
        if _section_chunks:
            rr = _FALLBACK.extract(_section_chunks, source_file)
            _merge(result, rr, (
                "country", "river_basin", "city_event", "region",
                "near_real_time", "latency", "revisit_time",
                "ukraine_relevance",
            ))
            result.river_name = result.river_name or result.river_basin
            result.evidence   = rr.evidence

        result.sections_used  = sections_used
        result.llm_used       = True
        result.fallback_used  = len(sections_used) < 2
        result.extractor_mode = "paper_json"

        sections_for_quality = {
            "title":    result.title,
            "abstract": abstract_text,
            "methods":  methods_text,
            "results":  results_text,
        }
        quality = _quality_score(sections_for_quality, result.satellite_names or "")
        result.quality_score = quality
        result.confidence    = _compute_confidence(quality, len(sections_used))

        return result.finalize()

    # ── Metric fill helpers ───────────────────────────────────────────────────

    def _fill_from_sodb_metrics(
        self,
        result: ExtractionResult,
        paper: dict,
    ) -> None:
        """
        Fill empty metric fields from SODB numeric_facts.parquet rows.

        canonical_id examples: metric.f1, metric.iou, metric.oa, metric.kappa.
        Values are normalised to 0–1 scale (handles percent input).
        Source provenance: "sodb_parquet".

        Always appends MetricFact for every valid fact (regardless of headline fill).
        """
        for fact in paper.get("sodb_metrics") or []:
            canonical_id = (fact.get("canonical_id") or "").lower()
            attr         = _SODB_METRIC_MAP.get(canonical_id)
            raw_val      = fact.get("value")

            # 1. Resolve type FIRST — determines normalisation strategy
            spec = resolve_sodb_fact(canonical_id)
            if spec is None:
                logger.debug("Unknown SODB canonical ID (not in FACT_CANONICAL_MAP): %s", canonical_id)

            # 2. Normalise based on fact_type (NOT the old ratio-only logic)
            val = normalize_fact_value(raw_val, spec, canonical_id)
            if val is None:
                continue

            # Spec-derived metadata
            if spec is not None:
                m_label  = spec.label
                m_group  = spec.group
                m_ftype  = spec.fact_type
            else:
                metric_id_tmp = resolve_sodb_canonical(canonical_id) or canonical_id
                m_label  = _metric_label(metric_id_tmp)
                m_group  = _metric_group(metric_id_tmp)
                m_ftype  = "evaluation_metric"

            # Headline metric_id (bare form, e.g. "f1_score")
            metric_id = resolve_sodb_canonical(canonical_id) or canonical_id

            # Fill flat headline field only for evaluation metrics
            if attr and getattr(result, attr) is None and m_ftype == "evaluation_metric":
                setattr(result, attr, val)
                result.provenance[attr.upper()] = {
                    "section": "table",
                    "source":  "sodb_parquet",
                    "snippet": (fact.get("col_header") or "")[:80],
                }

            # Always record full MetricFact evidence (all types)
            result.metric_facts.append(MetricFact(
                metric_id    = metric_id,
                metric_label = m_label,
                metric_group = m_group,
                fact_type    = m_ftype,
                value        = val,
                raw_value    = str(raw_val),
                unit         = fact.get("unit", ""),
                scale        = metric_scale(metric_id),
                source       = "sodb_parquet",
                section      = "table",
                evidence_text= (fact.get("col_header") or "")[:200],
                paper_id     = result.source_file,
                doi          = result.doi,
                confidence   = float(fact.get("confidence") or 0.8),
            ))

    def _fill_from_paper_entities(
        self,
        result: ExtractionResult,
        paper: dict,
    ) -> None:
        """
        Fill empty metric fields from paper["entities"]["metrics"].

        These are regex-extracted during the ingestion pipeline and have
        {type, value, evidence:{section, snippet}} structure.
        Source provenance: "paper_entities".

        Always appends MetricFact for every valid entity (regardless of headline fill).
        """
        _ENTITY_TYPE_MAP: dict[str, tuple[str, str]] = {
            "OA":    ("oa",    "overall_accuracy"),
            "F1":    ("f1",    "f1_score"),
            "IoU":   ("iou",   "iou"),
            "Kappa": ("kappa", "kappa"),
        }
        for m in (paper.get("entities") or {}).get("metrics") or []:
            mtype = (m.get("type") or "").strip()
            mapping = _ENTITY_TYPE_MAP.get(mtype)
            if not mapping:
                continue
            attr, metric_id = mapping
            raw_val = m.get("value")
            val     = normalize_metric_value(raw_val, metric_id)
            if val is None:
                continue
            ev      = m.get("evidence") or {}
            section = ev.get("section", "results")
            if section not in ("results", ""):
                continue   # only trust results-section provenance

            # Fill flat headline field if not yet set
            if getattr(result, attr) is None:
                setattr(result, attr, val)
                result.provenance[attr.upper()] = {
                    "section": section or "results",
                    "source":  "paper_entities",
                    "snippet": (ev.get("snippet") or "")[:80],
                }

            # Always record full MetricFact evidence
            result.metric_facts.append(MetricFact(
                metric_id    = metric_id,
                metric_label = _metric_label(metric_id),
                metric_group = _metric_group(metric_id),
                fact_type    = "evaluation_metric",
                value        = val,
                raw_value    = str(raw_val),
                unit         = "ratio",
                scale        = "0_1",
                source       = "paper_entities",
                section      = section or "results",
                evidence_text= (ev.get("snippet") or "")[:200],
                paper_id     = result.source_file,
                doi          = result.doi,
                confidence   = 0.75,
            ))

    def extract(
        self,
        chunks: list[dict],
        source_file: str,
    ) -> ExtractionResult:
        ordered   = sorted(chunks, key=lambda c: (c.get("page_start", 0), c.get("chunk_id", "")))
        full_text = "\n\n".join(c["text"] for c in ordered)

        # Use canonical section parser (Task 1)
        sections = parse_document_sections(full_text)
        found, missing = describe_parsed(sections)
        _debug_sections(source_file, found, missing)

        # Always prepare the regex baseline for bibliographic fields
        regex_result = _FALLBACK.extract(chunks, source_file)

        if not self._is_available():
            logger.warning("Ollama unavailable — regex fallback for %s", source_file)
            regex_result.llm_used      = False
            regex_result.fallback_used = True
            regex_result.extractor_mode = "fallback"
            return regex_result

        result = ExtractionResult(source_file=source_file)
        sections_used: list[str] = []

        # ── Call 1: Abstract → full thematic profile ─────────────────────────
        abstract_text = sections.get("abstract") or regex_result.abstract or ""
        if abstract_text:
            abs_r = self._call_section(
                _ABSTRACT_PROMPT, abstract_text[:self._abstract_max], source_file
            )
            _merge(result, abs_r, (
                "study_type", "satellite_names", "sensor_type", "data_product",
                "country", "region", "river_basin", "city_event",
                "methods", "near_real_time", "near_real_time_label", "latency",
            ))
            sections_used.append("abstract")
            # Record provenance for LLM-sourced fields
            for field_name in ("satellite_names", "country", "river_basin", "methods"):
                if getattr(result, field_name, None):
                    result.provenance[field_name.title().replace("_", "")] = {
                        "section":        "abstract",
                        "snippet":        abstract_text[:120],
                        "source":         "llm",
                        "extractor_mode": "section",
                    }

        # ── Call 2: Methods → precise methods + sensor (override abstract) ────
        methods_text = sections.get("methods") or ""
        if methods_text:
            meth_r = self._call_section(
                _METHODS_PROMPT, methods_text[:self._methods_max], source_file
            )
            _merge(result, meth_r,
                   ("methods", "satellite_names", "sensor_type", "data_product"),
                   override=True)
            sections_used.append("methods")
            if result.methods:
                result.provenance["Methods"] = {
                    "section":        "methods",
                    "snippet":        methods_text[:120],
                    "source":         "llm",
                    "extractor_mode": "section",
                }

        # ── Call 3: Results → metrics ONLY ───────────────────────────────────
        results_text = sections.get("results") or ""
        if results_text and result.study_type in (
            "ML/DL classification", "Satellite flood mapping",
            "Operational mapping system", "",
        ):
            res_r = self._call_section(
                _RESULTS_PROMPT, results_text[:self._results_max], source_file
            )
            res_r = _validate_metrics(res_r, results_text)
            _merge(result, res_r, ("oa", "f1", "iou", "kappa"))
            sections_used.append("results")
            # Provenance + MetricFacts for LLM-extracted metrics
            for fname, attr, metric_id in [
                ("OA",    "oa",    "overall_accuracy"),
                ("F1",    "f1",    "f1_score"),
                ("IoU",   "iou",   "iou"),
                ("Kappa", "kappa", "kappa"),
            ]:
                val = getattr(result, attr)
                if val is not None:
                    result.provenance[fname] = {
                        "section":        "results",
                        "snippet":        results_text[:120],
                        "source":         "llm",
                        "extractor_mode": "section",
                    }
                    result.metric_facts.append(MetricFact(
                        metric_id    = metric_id,
                        metric_label = _metric_label(metric_id),
                        metric_group = _metric_group(metric_id),
                        fact_type    = "evaluation_metric",
                        value        = val,
                        raw_value    = str(val),
                        unit         = "ratio",
                        scale        = "0_1",
                        source       = "llm",
                        section      = "results",
                        evidence_text= results_text[:200],
                        paper_id     = source_file,
                        doi          = result.doi,
                        confidence   = 0.85,
                    ))

        # ── Regex fills ONLY bibliographic + geography gaps ───────────────────
        # NEVER fills metrics or methods — those must come from section calls.
        _merge(result, regex_result, (
            "study_type", "satellite_names", "sensor_type", "data_product",
            "country", "river_basin", "city_event",
            "near_real_time", "latency", "revisit_time",
        ))

        # Bibliographic fields always from regex (reliable, section-independent)
        result.title     = regex_result.title
        result.abstract  = regex_result.abstract
        result.doi       = regex_result.doi
        result.year      = regex_result.year
        result.authors   = regex_result.authors
        result.full_text = regex_result.full_text
        result.evidence  = regex_result.evidence

        result.ukraine_relevance = regex_result.ukraine_relevance
        result.river_name        = regex_result.river_name or result.river_basin

        result.sections_used  = sections_used
        result.llm_used       = True
        result.fallback_used  = len(sections_used) < 2
        result.extractor_mode = "section" if sections_used else "mixed"

        quality = _quality_score(sections, result.satellite_names)
        result.quality_score = quality
        result.confidence    = _compute_confidence(quality, len(sections_used))

        return result.finalize()

    # ── LLM helpers ───────────────────────────────────────────────────────────

    def _is_available(self) -> bool:
        if self._available is not None:
            return self._available
        try:
            resp = requests.get(
                self._url.replace("/api/generate", "/api/tags"),
                timeout=3,
            )
            self._available = resp.status_code == 200
        except requests.RequestException:
            self._available = False
        if self._available:
            logger.info("Ollama available at %s (model: %s)", self._url, self._model)
        return self._available

    def _call_section(
        self,
        prompt_template: str,
        text: str,
        source_file: str,
    ) -> ExtractionResult:
        if not text.strip():
            return ExtractionResult(source_file=source_file)

        payload = {
            "model":  self._model,
            "prompt": prompt_template.format(text=text),
            "system": _SYS,
            "stream": False,
            "format": "json",
            "options": {"temperature": 0.0, "num_predict": 400},
        }
        try:
            resp = requests.post(self._url, json=payload, timeout=self._timeout)
            resp.raise_for_status()
            raw = resp.json().get("response", "")
            return self._parse(raw, source_file)
        except Exception as exc:
            logger.warning("LLM call failed for %s: %s", source_file, exc)
            return ExtractionResult(source_file=source_file)

    def _parse(self, raw: str, source_file: str) -> ExtractionResult:
        raw = re.sub(r"```(?:json)?", "", raw).strip()
        try:
            data: dict[str, Any] = json.loads(raw)
        except json.JSONDecodeError:
            return ExtractionResult(source_file=source_file)

        def _s(key: str) -> str:
            val = data.get(key) or data.get(key.lower())
            return str(val).strip() if val and val != "null" else ""

        def _f(key: str) -> float | None:
            val = data.get(key) or data.get(key.lower())
            if val is None or val == "null":
                return None
            try:
                n = float(val)
                return round(n / 100.0 if n > 1.0 else n, 4)
            except (TypeError, ValueError):
                return None

        # ── Near_Real_Time 3-state parsing ──────────────────────────────────
        _nrt_raw = data.get("Near_Real_Time") or data.get("near_real_time")
        if _nrt_raw is None or _nrt_raw == "null":
            _nrt_bool  = None
            _nrt_label = ""
        elif isinstance(_nrt_raw, bool):
            _nrt_bool  = _nrt_raw
            _nrt_label = "true" if _nrt_raw else "false"
        else:
            _s_nrt = str(_nrt_raw).lower().strip()
            if _s_nrt in ("true", "yes", "1"):
                _nrt_bool, _nrt_label = True,  "true"
            elif _s_nrt in ("false", "no", "0"):
                _nrt_bool, _nrt_label = False, "false"
            elif _s_nrt == "unclear":
                _nrt_bool, _nrt_label = None,  "unclear"
            else:
                _nrt_bool, _nrt_label = None,  ""

        return ExtractionResult(
            source_file          = source_file,
            study_type           = _s("Study_Type"),
            satellite_names      = _s("Satellite_Names"),
            sensor_type          = _s("Sensor_Type"),
            data_product         = _s("Data_Product"),
            country              = _s("Country"),
            region               = _s("Region"),
            river_basin          = _s("River_Basin"),
            city_event           = _s("City_Event"),
            methods              = _s("Methods"),
            near_real_time       = _nrt_bool,
            near_real_time_label = _nrt_label,
            latency              = _s("Latency"),
            oa                   = _f("OA"),
            f1                   = _f("F1"),
            iou                  = _f("IoU"),
            kappa                = _f("Kappa"),
        )


# ── Debug ─────────────────────────────────────────────────────────────────────

def _debug_sections(
    source_file: str,
    found: list[str],
    missing: list[str],
) -> None:
    logger.info("[%s]  found=%s  missing=%s", source_file, found, missing)
    print(f"\n  [{source_file}]")
    print(f"    Sections found:   {found or '—'}")
    print(f"    Missing sections: {missing or '—'}")


# ── Merge helper (Task 4) ─────────────────────────────────────────────────────

def _merge(
    target: ExtractionResult,
    source: ExtractionResult,
    fields: tuple[str, ...],
    override: bool = False,
) -> None:
    """
    Copy fields from source → target.

    With override=False (default): only fills EMPTY target fields.
    With override=True: overwrites target even if non-empty.
    NEVER called with override=True for regex fallback — only for
    the Methods LLM call which is more authoritative than Abstract.
    """
    for f in fields:
        src_val = getattr(source, f, None)
        if src_val is None or src_val == "" or src_val is False:
            continue
        tgt_val = getattr(target, f, None)
        if override or not tgt_val:
            setattr(target, f, src_val)
