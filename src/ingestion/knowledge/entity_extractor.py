"""
entity_extractor.py
-------------------
Generic, KB-driven entity extraction engine.

Replaces all hardcoded SATELLITE_PATTERNS, DEM_DATASETS, METHOD_PATTERNS.

Core idea:
  - All entities come from the KnowledgeBase
  - Patterns are generated from entity acronym + full_name (+ overrides)
  - Each match is enriched with KB metadata (domain, used_for, related, etc.)
  - Context scoring filters out mere mentions vs. actual usage
"""

from __future__ import annotations

import re
import logging
from typing import Optional

from .knowledge_loader import KnowledgeBase, EntityRecord, MethodRecord
from .normalizer import Normalizer

log = logging.getLogger(__name__)

# ─── metric patterns (value-capture aware) ───────────────────────────────────
# These keep their numeric-capture groups and are NOT replaced by KB.
METRIC_PATTERNS: dict[str, str] = {
    # OA: plural form, "classification accuracy", verb connectors (is/was/are above/around/of)
    "OA":      r"(overall accura(?:cy|cies)|global accuracy|\boa\b|classification accuracy)[^.,]{0,60}?(\d+(?:\.\d+)?)\s*%",
    "F1":      r"(f1[\-\s]?score|\bf1\b|f[\-\s]?measure)\s*(?:=|:|of|values?)?\s*(\d+(?:\.\d+)?)\s*%?",
    # IoU: add "score of" as connector (e.g. "IoU score of 86.33%")
    "IoU":     r"(\biou\b|intersection over union|jaccard)\s*(?:=|:|of|values?|score\s+of)?\s*(\d+(?:\.\d+)?)\s*%?",
    # Kappa ∈ [-1, 1] — sign-aware (Фаза 2.1)
    "Kappa":     r"(kappa|kp)\s*(?:coefficient)?\s*(?:=|:|of|values?)?\s*(-?\d+(?:\.\d+)?)\s*%?",
    # Precision/Recall: possessive forms "user's accuracy", "producer's accuracy"
    "Precision": r"(precision|user.?s?\s+accuracy|\bua\b|producer.?s?\s+accuracy|\bpa\b)\s*(?:=|:|of|values?)?\s*(\d+(?:\.\d+)?)\s*%?",
    "Recall":    r"(recall|sensitivity|\btpr\b)\s*(?:=|:|of|values?)?\s*(\d+(?:\.\d+)?)\s*%?",
    "RMSE":    r"\brmse\b\s*(?:=|:|of)?\s*(\d+(?:\.\d+)?)",
    "MAE":     r"\bmae\b\s*(?:=|:|of)?\s*(\d+(?:\.\d+)?)",
    # Знакові метрики (Фаза 2.1): NSE/KGE/R² ∈ (−∞,1], R ∈ [−1,1],
    # PBIAS — знак = напрям систематичної похибки. Патерни без "-?" мовчки
    # губили від'ємні значення → optimism bias у корпусі.
    "R":       r"\br\s*=\s*(-?\d+(?:\.\d+)?)",
    "p_value": r"\bp\s*[<=>]\s*(\d+(?:\.\d+)?)",
    "Percent": r"(up to|around|approximately|about)?\s*(\d+(?:\.\d+)?)\s*%",
    "NSE":     r"\bnse\b\s*(?:=|:|of)?\s*(-?\d+(?:\.\d+)?)",
    "KGE":     r"\bkge\b\s*(?:=|:|of)?\s*(-?\d+(?:\.\d+)?)",
    "R2":      r"\br[\^²]?2\b\s*(?:=|:|of)?\s*(-?\d+(?:\.\d+)?)",
    "PBIAS":   r"\bpbias\b\s*(?:=|:|of)?\s*(-?\d+(?:\.\d+)?)\s*%?",
}

INVALID_METRIC_CONTEXT = [
    "aep", "annual exceedance probability",
    "world settlement footprint", "wsf", "table",
]

# ─── real-usage context filter ────────────────────────────────────────────────
_USAGE_POSITIVE = [
    "used", "applied", "we used", "this study uses", "data used",
    "method used", "we applied", "this paper uses", "using",
    "was applied", "is applied", "are used", "were used",
    "we employ", "employed", "we adopt", "adopted",
    "we utilize", "utilized",
    # acquisition verbs — satellite/DEM data is "acquired/obtained/downloaded from"
    "acquired", "acquire", "acquiring",
    "obtained", "obtain",
    "downloaded", "download",
    "retrieved", "retrieve",
    "collected", "collect",
    "sourced from", "provided by",
    "incorporate", "incorporated",
    "leverage", "leveraged",
]
_USAGE_NEGATIVE = [
    "previous studies", "other studies", "review", "for example",
    "such as", "e.g.", "et al", "proposed by", "introduced by",
    "according to", "in contrast to",
]


_METRIC_CONTEXT_KEYWORDS = ["validation", "test", "calibration", "training"]


def _detect_metric_context(snippet: str) -> str | None:
    s = snippet.lower()
    return next((k for k in _METRIC_CONTEXT_KEYWORDS if k in s), None)


def _is_real_usage(ctx: str) -> bool:
    ctx_l = ctx.lower()
    pos = any(k in ctx_l for k in _USAGE_POSITIVE)
    neg = any(k in ctx_l for k in _USAGE_NEGATIVE)
    return pos and not neg


def _snippet(text: str, start: int, end: int, window: int = 200) -> str:
    return re.sub(r"\s+", " ", text[max(0, start - window): min(len(text), end + window)]).strip()


# ─── main extractor class ─────────────────────────────────────────────────────

class EntityExtractor:
    """
    Generic KB-driven entity extractor.

    All domain knowledge (satellite names, DEM names, method names, etc.)
    comes from the KnowledgeBase. No hardcoded patterns.

    Usage:
        ex = EntityExtractor(kb)
        satellites = ex.extract_satellites(text)
        dems       = ex.extract_dems(text)
        methods    = ex.extract_methods(text)
        metrics    = ex.extract_metrics(text)
    """

    def __init__(self, kb: KnowledgeBase):
        self.kb   = kb
        self.norm = Normalizer(kb)

    # ── generic KB-driven extraction ──────────────────────────────────────────

    def extract_from_knowledge_base(
        self,
        text: str,
        entities: list[EntityRecord],
        entity_type: str,
        source: str,
        strict: bool = True,
    ) -> list[dict]:
        """
        Extract entities from text using KB patterns.

        Args:
            text:        Input text to search.
            entities:    List of EntityRecord instances to match.
            entity_type: Label for the extracted entity type (e.g. "satellite").
            source:      Source section label (e.g. "data_sources+methods").
            strict:      If True, apply usage-context filter.

        Returns:
            List of entity dicts with name, type, source, confidence,
            evidence, and kb_metadata.
        """
        results: dict[str, dict] = {}

        for rec in entities:
            if not rec.pattern:
                continue
            try:
                for m in re.finditer(rec.pattern, text, re.IGNORECASE):
                    ctx = _snippet(text, m.start(), m.end(), window=200)

                    if strict and not _is_real_usage(ctx):
                        continue

                    key = rec.acronym.lower()
                    if key not in results:
                        results[key] = self._make_entity(rec, entity_type, source, ctx, m)
            except re.error as exc:
                log.debug("Pattern error for %s: %s", rec.acronym, exc)

        return list(results.values())

    def _make_entity(
        self,
        rec: EntityRecord,
        entity_type: str,
        source: str,
        ctx: str,
        match: re.Match,
    ) -> dict:
        return {
            "name":       rec.acronym,
            "type":       entity_type,
            "source":     source,
            "confidence": 0.90,
            "evidence":   ctx,
            "scores": {
                "pattern":   0.9,
                "context":   0.0,
                "embedding": 0.0,
                "llm":       0.0,
            },
            "final_score": 0.0,
            "accepted":    None,
            "role":        None,
            "kb_metadata": {
                "full_name":  rec.full_name,
                "type":       rec.type,
                "type_group": rec.type_group,
                "domain":     rec.domain,
                "definition": rec.definition,
                "used_for":   rec.used_for,
                "related":    rec.related,
                "contexts":   rec.contexts,
                "source_kb":  rec.source_kb,
            },
        }

    # ── typed extractors (mirror old pipeline API) ────────────────────────────

    def extract_satellites(self, text: str, strict: bool = True) -> list[dict]:
        """Detect satellite/sensor names from text."""
        return self.extract_from_knowledge_base(
            text,
            self.kb.get_satellites(),
            entity_type="satellite",
            source="data_sources+methods",
            strict=strict,
        )

    def extract_dems(self, text: str, strict: bool = True) -> list[dict]:
        """Detect DEM/terrain dataset names from text."""
        return self.extract_from_knowledge_base(
            text,
            self.kb.get_dems(),
            entity_type="dem",
            source="data_sources+methods",
            strict=strict,
        )

    def extract_methods(self, text: str, strict: bool = True) -> list[dict]:
        """
        Detect analysis methods, algorithms, and models from text.
        Combines KB entity extraction + ontology method records.
        """
        kb_methods = self.extract_from_knowledge_base(
            text,
            self.kb.get_methods(),
            entity_type="method",
            source="methods+results",
            strict=strict,
        )

        # also scan ontology method records
        onto_methods = self._extract_ontology_methods(text, strict)

        # merge, deduplicate by name
        merged: dict[str, dict] = {}
        for ent in kb_methods + onto_methods:
            key = ent["name"].lower()
            if key not in merged:
                merged[key] = ent
            else:
                # merge used_for / related from both hits
                existing = merged[key]
                for field in ("used_for", "related"):
                    existing_meta = existing.get("kb_metadata", {})
                    new_meta = ent.get("kb_metadata", {})
                    combined = list(set(
                        existing_meta.get(field, []) + new_meta.get(field, [])
                    ))
                    existing_meta[field] = combined

        return list(merged.values())

    def _enrich_v2_metadata(self, mrec: MethodRecord) -> dict:
        """Build the v2-extended kb_metadata block for a MethodRecord."""
        return {
            "full_name":  mrec.method_name.replace("_", " ").title(),
            "type":       mrec.type,
            "type_group": "method",
            "domain":     mrec.domain,
            "definition": mrec.description,
            "used_for":   mrec.inputs,
            "related":    mrec.related,
            "inputs":     mrec.inputs,
            "outputs":    mrec.outputs,
            "source_kb":  mrec.source_kb,
            # v2 fields
            "category":    mrec.category,
            "subcategory": mrec.subcategory,
            "dimension":   mrec.dimension,
            "physics_based":  mrec.physics_based,
            "data_driven":    mrec.data_driven,
            "evaluation_metrics":             mrec.evaluation_metrics,
            "coupled_with":                   mrec.coupled_with,
            "equations":                      mrec.equations,
            "requires_calibration":           mrec.requires_calibration,
            "supports_real_time_forecasting": mrec.supports_real_time_forecasting,
            "supports_ungauged_basins":       mrec.supports_ungauged_basins,
            "tasks":          mrec.tasks,
            "typical_use_cases": mrec.typical_use_cases,
        }

    def _extract_ontology_methods(self, text: str, strict: bool) -> list[dict]:
        results: dict[str, dict] = {}
        for mrec in self.kb.methods:
            if not mrec.pattern:
                continue
            try:
                for m in re.finditer(mrec.pattern, text, re.IGNORECASE):
                    ctx = _snippet(text, m.start(), m.end(), window=200)
                    if strict and not _is_real_usage(ctx):
                        continue
                    key = mrec.method_name.lower()
                    if key not in results:
                        results[key] = {
                            "name":       mrec.method_name,
                            "type":       "method",
                            "source":     "methods+results",
                            "confidence": 0.88,
                            "evidence":   ctx,
                            "scores": {
                                "pattern": 0.88,
                                "context": 0.0,
                                "embedding": 0.0,
                                "llm": 0.0,
                            },
                            "final_score": 0.0,
                            "accepted":    None,
                            "role":        None,
                            "kb_metadata": self._enrich_v2_metadata(mrec),
                        }
            except re.error:
                pass
        return list(results.values())

    # ── metric extraction (value-capture aware, not purely KB-driven) ─────────

    def extract_metrics(self, text: str) -> list[dict]:
        """
        Extract performance metrics with their numeric values.
        Uses METRIC_PATTERNS (value-capture aware) combined with KB enrichment.
        """
        metrics: list[dict] = []
        seen: set[tuple] = set()

        for metric_type, pattern in METRIC_PATTERNS.items():
            for m in re.finditer(pattern, text, re.IGNORECASE):
                ctx = _snippet(text, m.start(), m.end(), window=120)
                if not self._is_valid_metric_context(ctx):
                    continue

                # extract numeric value
                raw = self._extract_metric_value(m, metric_type)
                if raw is None:
                    continue

                key = (metric_type, raw)
                if key in seen:
                    continue
                seen.add(key)

                value = self._normalize_metric_value(raw, metric_type)

                # enrich with KB if we have an entity for it
                kb_meta = {}
                rec = self.kb.resolve(metric_type)
                if rec:
                    kb_meta = {
                        "full_name":  rec.full_name,
                        "definition": rec.definition,
                        "domain":     rec.domain,
                    }
                # resolve() checks EntityRecord glossary only and never populates
                # canonical_id even when the metric exists in v2_metrics (e.g.
                # NSE, RMSE, KGE are in both glossary AND v2_metrics). Always
                # supplement from v2_metrics so canonical_id is set.
                mrec = self.kb.resolve_metric(metric_type)
                if mrec:
                    kb_meta.setdefault("full_name", mrec.full_name)
                    kb_meta.setdefault("domain",    "hydrology")
                    kb_meta["canonical_id"]  = mrec.id
                    kb_meta["optimal"]       = mrec.optimal
                    kb_meta["applicable_to"] = mrec.applicable_to

                metrics.append({
                    "type":         metric_type,
                    "name":         kb_meta.get("full_name") or metric_type,
                    "canonical_id": kb_meta.get("canonical_id", ""),
                    "confidence":   0.7 if kb_meta.get("canonical_id") else 0.5,
                    "value":        value,
                    "unit":         "-" if metric_type in {"OA", "F1", "IoU", "Kappa", "Precision", "Recall", "R", "R2", "NSE", "KGE", "AUC"} else None,
                    "context":      _detect_metric_context(ctx),
                    "evidence": {
                        "field":   metric_type,
                        "section": "results",
                        "snippet": ctx,
                        "source":  "regex",
                    },
                    "kb_metadata": kb_meta,
                })

        # PATCH 3: "F1 = 92.5%" fires both F1 and Percent patterns for the same
        # value; remove Percent entries that duplicate a named metric's value.
        named_values = {m["value"] for m in metrics if m["type"] != "Percent"}
        metrics = [m for m in metrics if m["type"] != "Percent" or m["value"] not in named_values]

        return metrics

    @staticmethod
    def _extract_metric_value(m: re.Match, metric_type: str) -> Optional[str]:
        groups = m.groups()
        if not groups:
            return None
        if metric_type in {"RMSE", "MAE", "R", "p_value", "NSE", "KGE", "R2", "PBIAS"}:
            return groups[-1] if groups[-1] else None
        # For OA, F1, IoU, Kappa, Precision, Recall, Percent: value is in group[1]
        if len(groups) >= 2:
            return groups[1] if groups[1] else None
        return groups[0] if groups[0] else None

    @staticmethod
    def _normalize_metric_value(value: str, metric_type: str) -> float:
        v = float(value)
        # Відсоткова форма → [0,1]; для знакових (Kappa) і |v|>1 теж /100
        # ("kappa = -15%" → -0.15). NSE/KGE/R2/PBIAS не масштабуються.
        if metric_type in {"OA", "F1", "IoU", "Kappa", "Precision", "Recall", "R", "Percent"} \
                and abs(v) > 1:
            return round(v / 100, 4)
        return round(v, 4)

    @staticmethod
    def _is_valid_metric_context(snippet: str) -> bool:
        s = snippet.lower()
        return not any(k in s for k in INVALID_METRIC_CONTEXT)

    # ── v2 extraction methods ─────────────────────────────────────────────────

    # Keyword sets that signal a task even when the full label is absent.
    # Each entry: task_id → list of trigger phrases (any match → task detected).
    _TASK_KEYWORDS: dict[str, list[str]] = {
        "flood_forecasting":         ["flood forecast", "flood prediction", "flood early warning",
                                       "flood warning", "real-time flood"],
        "flood_inundation_mapping":  ["flood inundation", "inundation map", "inundation simulation",
                                       "flood simulation", "inundation modelling", "inundation modeling"],
        "rainfall_runoff_modeling":  ["rainfall-runoff", "rainfall runoff", "runoff model",
                                       "runoff simulation", "rainfall runoff model"],
        "hydrological_modeling":     ["hydrological model", "hydrologic model", "catchment model"],
        "hydrodynamic_simulation":   ["hydrodynamic", "hydraulic simulation", "hydrodynamic model",
                                       "hydrodynamic simulation"],
        "hydraulic_simulation":      ["hydraulic model", "hydraulic analysis", "hydraulic routing"],
        "water_level_forecasting":   ["water level forecast", "water level prediction",
                                       "water level", "stage forecast", "stage prediction"],
        "streamflow_prediction":     ["streamflow", "stream flow", "discharge prediction",
                                       "discharge forecast", "flow prediction"],
        "flood_susceptibility_mapping": ["flood susceptibility", "flood hazard map", "flood hazard zone"],
        "flood_extent_mapping":      ["flood extent", "inundation extent", "flood boundary"],
        "flood_frequency_analysis":  ["flood frequency", "return period", "recurrence interval",
                                       "annual exceedance"],
        "discharge_estimation":      ["discharge estimation", "discharge calculation",
                                       "peak discharge", "peak flow estimation"],
        "hydrograph_generation":     ["hydrograph", "unit hydrograph", "flood hydrograph"],
        "flood_risk_assessment":     ["flood risk", "flood damage assessment", "flood vulnerability"],
        "flood_warning":             ["early warning", "flood alert", "warning system"],
    }

    # Tasks that must be inferred when a specific model category+subcategory is detected,
    # even without direct text evidence.
    # Key: (category, subcategory_or_None) → [(task_id, confidence)]
    _CATEGORY_TASK_CONSTRAINTS: dict[tuple, list[tuple]] = {
        ("physical_based", "hydrological"):  [("rainfall_runoff_modeling", 0.85),
                                               ("hydrological_modeling", 0.82),
                                               ("hydrograph_generation", 0.80)],
        ("physical_based", "hydrodynamic"):  [("hydrodynamic_simulation", 0.85),
                                               ("flood_inundation_mapping", 0.85),
                                               ("hydraulic_simulation", 0.82)],
        ("time_series",    "statistical"):   [("flood_forecasting", 0.80),
                                               ("water_level_forecasting", 0.78)],
        ("time_series",    "machine_learning"): [("flood_forecasting", 0.82),
                                                  ("water_level_forecasting", 0.82),
                                                  ("streamflow_prediction", 0.80)],
        ("time_series",    "hybrid"):        [("water_level_forecasting", 0.85),
                                               ("flood_forecasting", 0.83),
                                               ("streamflow_prediction", 0.80)],
        ("machine_learning", None):          [("flood_susceptibility_mapping", 0.78)],
        ("fuzzy_logic",    None):            [("flood_forecasting", 0.78),
                                               ("streamflow_prediction", 0.75)],
    }

    def extract_tasks(
        self,
        text: str,
        methods: Optional[list[dict]] = None,
        couplings: Optional[list[dict]] = None,
    ) -> list[dict]:
        """
        Detect flood modeling tasks by combining three evidence sources:
          1. Text-phrase matching (exact label + keyword patterns)
          2. Method-derived tasks from accepted method.tasks ontology fields
          3. Coupling-derived task inference

        Args:
            text:      Input text to search.
            methods:   Optional list of detected method dicts (from extract_methods /
                       MethodMatcher.match). When provided, method.tasks fields are
                       harvested and category constraints are applied.
            couplings: Optional list of coupling dicts (from extract_model_couplings /
                       MethodMatcher.detect_coupled_models). When hydrological+hydrodynamic
                       coupling is found, flood_inundation_mapping is reinforced.

        Returns:
            Deduplicated list of task dicts, highest-confidence first:
            {task_id, name, label, domain, output, evidence, source, confidence}
        """
        accumulated: dict[str, dict] = {}   # task_id → best result so far
        text_lower = text.lower()

        def _upsert(tid: str, confidence: float, source: str, evidence: str = "") -> None:
            trec = self.kb.v2_tasks.get(tid)
            name  = trec.name  if trec else tid.replace("_", " ").title()
            label = trec.label if trec else name
            dom   = trec.domain if trec else "hydrology"
            out   = trec.output if trec else None
            if tid not in accumulated or accumulated[tid]["confidence"] < confidence:
                accumulated[tid] = {
                    "task_id":    tid,
                    "name":       name,
                    "label":      label,
                    "domain":     dom,
                    "output":     out,
                    "evidence":   evidence,
                    "source":     source,
                    "confidence": round(confidence, 3),
                }

        # ── 1. Exact label / name matching against task_types ─────────────────
        for tid, trec in self.kb.v2_tasks.items():
            for needle in (trec.label.lower(), trec.name.lower()):
                if needle and needle in text_lower:
                    idx = text_lower.find(needle)
                    ctx = _snippet(text, idx, idx + len(needle))
                    _upsert(tid, 0.92, "text_phrase", ctx)
                    break

        # ── 2. Keyword-pattern matching ───────────────────────────────────────
        for tid, keywords in self._TASK_KEYWORDS.items():
            if tid in accumulated:
                continue          # already found via exact match
            for kw in keywords:
                if kw in text_lower:
                    idx = text_lower.find(kw)
                    ctx = _snippet(text, idx, idx + len(kw))
                    _upsert(tid, 0.88, "text_keyword", ctx)
                    break

        # ── 3. Method-derived tasks ───────────────────────────────────────────
        if methods:
            for mdict in methods:
                meta  = mdict.get("kb_metadata", {})
                mname = mdict.get("name", "")

                # 3a. Harvest ontology .tasks field directly
                for tid in meta.get("tasks", []):
                    _upsert(tid, 0.85, "method_task_field",
                            f"method {mname} has task {tid}")

                # 3b. Apply category+subcategory constraints
                cat  = meta.get("category")
                sub  = meta.get("subcategory")
                for key in ((cat, sub), (cat, None)):
                    for tid, conf in self._CATEGORY_TASK_CONSTRAINTS.get(key, []):
                        _upsert(tid, conf, "method_constraint",
                                f"category={cat} subcategory={sub} → {tid}")

        # ── 4. Coupling-derived task inference ────────────────────────────────
        if couplings:
            for coup in couplings:
                ctype = coup.get("coupling_type", "")
                # hydrological + hydrodynamic coupling → coupled flood modeling
                if ctype == "hydrological_hydrodynamic":
                    _upsert("flood_inundation_mapping", 0.92, "coupling_context",
                            f"{coup['model_a']} coupled with {coup['model_b']}")
                    _upsert("rainfall_runoff_modeling", 0.88, "coupling_context",
                            f"{coup['model_a']} (hydrological model)")
                    _upsert("hydrodynamic_simulation", 0.88, "coupling_context",
                            f"{coup['model_b']} (hydrodynamic model)")
                elif ctype in ("1D_2D", "detected"):
                    _upsert("flood_inundation_mapping", 0.88, "coupling_context",
                            f"{coup.get('model_a','')} + {coup.get('model_b','')}")

        # ── 5. Sort by confidence descending ──────────────────────────────────
        return sorted(accumulated.values(), key=lambda x: -x["confidence"])

    def infer_tasks_from_methods(self, methods: list[dict]) -> list[dict]:
        """
        Convenience wrapper: infer tasks purely from a list of detected methods
        (no text required). Useful when called after extract_methods().
        """
        return self.extract_tasks("", methods=methods)

    def extract_dimensions(self, text: str) -> list[str]:
        """
        Detect spatial dimensionality mentions (1D, 2D, coupled_1D_2D).
        Returns list of detected dimension strings.
        """
        found: set[str] = set()
        patterns = [
            (r"\b1[\s\-]?D\b|one[\s\-]?dimensional",           "1D"),
            (r"\b2[\s\-]?D\b|two[\s\-]?dimensional",           "2D"),
            (r"\b3[\s\-]?D\b|three[\s\-]?dimensional",         "3D"),
            (r"\bcoupled[\s\-]?1[\s\-]?D[\s\-]?2[\s\-]?D\b|"
             r"1D[\s\-]?2D[\s\-]?coupled|1D\s+and\s+2D",       "coupled_1D_2D"),
        ]
        for pat, label in patterns:
            if re.search(pat, text, re.IGNORECASE):
                found.add(label)
        # if both 1D and 2D found separately, infer coupled unless already set
        if "1D" in found and "2D" in found and "coupled_1D_2D" not in found:
            found.add("coupled_1D_2D")
        return sorted(found)

    def extract_equations(self, text: str) -> list[dict]:
        """
        Match governing equations mentioned in text against v2 ontology equations.
        Returns list of {equation_id, name, type, evidence}.
        """
        results: list[dict] = []
        text_lower = text.lower()
        for eid, erec in self.kb.v2_equations.items():
            name_lower     = erec.name.lower()
            fullname_lower = erec.full_name.lower()
            hit = None
            if name_lower and name_lower in text_lower:
                hit = name_lower
            elif fullname_lower and len(fullname_lower) > 5 and fullname_lower in text_lower:
                hit = fullname_lower
            if hit:
                idx = text_lower.find(hit)
                ctx = _snippet(text, idx, idx + len(hit))
                results.append({
                    "equation_id": eid,
                    "name":        erec.name,
                    "full_name":   erec.full_name,
                    "type":        erec.type,
                    "conserves":   erec.conserves,
                    "evidence":    ctx,
                    "source":      "ontology_v2",
                })
        return results

    def extract_uncertainties(self, text: str) -> list[dict]:
        """
        Match uncertainty sources mentioned in text against v2 ontology.
        Returns list of {uncertainty_id, name, source, affects, evidence}.
        """
        results: list[dict] = []
        text_lower = text.lower()
        for uid, urec in self.kb.v2_uncertainties.items():
            # try both the stored name and a space-normalized version of the id
            candidates = [urec.name.lower(), uid.replace("_", " ").lower()]
            for needle in candidates:
                if needle and needle in text_lower:
                    idx = text_lower.find(needle)
                    ctx = _snippet(text, idx, idx + len(needle))
                    results.append({
                        "uncertainty_id": uid,
                        "name":           urec.name,
                        "source":         urec.source,
                        "affects":        urec.affects,
                        "evidence":       ctx,
                        "source_ontology": "ontology_v2",
                    })
                    break
        return results

    def extract_model_couplings(self, text: str) -> list[dict]:
        """
        Detect coupled model pairs (e.g., 'HEC-HMS coupled with HEC-RAS').
        Matches against v2 model coupled_with fields.
        coupled_with values in v2 ontology use display names like 'HEC-RAS-2D',
        not slug IDs — so we resolve by name.
        """
        results: list[dict] = []
        text_lower = text.lower()
        COUPLING_RE = re.compile(
            r"coupled\s+with|coupled\s+to|linked\s+to|linked\s+with|"
            r"integrated\s+with|combined\s+with|\+",
            re.IGNORECASE,
        )
        has_coupling = bool(COUPLING_RE.search(text))

        for mrec in self.kb.v2_models.values():
            if not mrec.coupled_with:
                continue
            # check if model A appears in text
            a_names = [mrec.method_name.lower()] + [a.lower() for a in mrec.aliases]
            if not any(n in text_lower for n in a_names):
                continue
            for partner_name_raw in mrec.coupled_with:
                # resolve partner by display name
                partner = self.kb.resolve_v2_model(partner_name_raw)
                if not partner:
                    continue
                b_names = [partner.method_name.lower()] + [a.lower() for a in partner.aliases]
                if any(n in text_lower for n in b_names):
                    if has_coupling:
                        results.append({
                            "model_a":   mrec.method_name,
                            "model_b":   partner.method_name,
                            "coupling_type": "detected",
                            "source":    "ontology_v2",
                        })
        return results

    # ── sensor type inference ─────────────────────────────────────────────────

    def infer_sensor_types(
        self,
        satellites: list[dict],
        dems: list[dict],
    ) -> list[str]:
        """
        Classify detected sensors into broad categories: SAR, Optical, LiDAR, DEM.
        Uses KB type information rather than hardcoded sets.
        """
        found: set[str] = set()

        SAR_SIGNALS     = {"sar", "radar", "synthetic aperture"}
        LIDAR_SIGNALS   = {"lidar", "laser", "icesat"}
        OPTICAL_SIGNALS = {"optical", "multispectral", "hyperspectral",
                           "vis", "nir", "swir"}

        for sat in satellites:
            name = sat["name"].lower()
            meta = sat.get("kb_metadata", {})
            defn = (meta.get("definition") or "").lower()
            fn   = (meta.get("full_name") or "").lower()

            combined = f"{name} {fn} {defn}"

            if any(sig in combined for sig in SAR_SIGNALS):
                found.add("SAR")
            elif any(sig in combined for sig in LIDAR_SIGNALS):
                found.add("LiDAR")
            elif any(sig in combined for sig in OPTICAL_SIGNALS):
                found.add("Optical")
            else:
                # fallback: check domain
                if meta.get("type") == "sensor":
                    found.add("Optical")

        if dems:
            found.add("DEM")

        return sorted(found)
