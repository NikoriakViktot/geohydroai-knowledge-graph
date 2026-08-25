"""
method_matcher.py
-----------------
Ontology-driven method detection and enrichment.

Responsibilities:
  - Match methods via ontology_methods.json aliases and descriptions
  - Map detected methods to their inputs/outputs from the ontology
  - Attach full method metadata to matches
  - Identify method pipelines from the remote_sensing_water_resources domain structure
"""

from __future__ import annotations

import re
import logging
from typing import Optional

from .knowledge_loader import KnowledgeBase, MethodRecord

log = logging.getLogger(__name__)

_POSITIVE_CONTEXT = [
    "used", "applied", "we used", "this study", "using",
    "was applied", "is applied", "were used",
    "we employ", "we adopt", "we utilize",
    "method", "algorithm", "model",
]
_NEGATIVE_CONTEXT = [
    "previous studies", "other studies", "review", "e.g.", "et al",
    "for example", "such as", "proposed by", "introduced by",
]


def _snippet(text: str, start: int, end: int, window: int = 250) -> str:
    return re.sub(
        r"\s+", " ",
        text[max(0, start - window): min(len(text), end + window)]
    ).strip()


def _is_real_usage(ctx: str) -> bool:
    ctx_l = ctx.lower()
    return (
        any(k in ctx_l for k in _POSITIVE_CONTEXT) and
        not any(k in ctx_l for k in _NEGATIVE_CONTEXT)
    )


class MethodMatcher:
    """
    Detects analysis methods, models, and algorithms from text using
    the ontology knowledge base.

    Usage:
        matcher = MethodMatcher(kb)
        methods = matcher.match(text)
        # Each item: {name, type, inputs, outputs, domain, evidence, ...}
    """

    def __init__(self, kb: KnowledgeBase):
        self.kb = kb

    def match(self, text: str, strict: bool = True) -> list[dict]:
        """
        Detect and enrich methods from text.

        Returns list of dicts with full ontology metadata.
        """
        results: dict[str, dict] = {}

        for mrec in self.kb.methods:
            if not mrec.pattern:
                continue
            try:
                for m in re.finditer(mrec.pattern, text, re.IGNORECASE):
                    ctx = _snippet(text, m.start(), m.end())
                    if strict and not _is_real_usage(ctx):
                        continue

                    key = mrec.method_name.lower()
                    if key not in results:
                        results[key] = self._build_result(mrec, ctx)
            except re.error as exc:
                log.debug("Pattern error %s: %s", mrec.method_name, exc)

        return list(results.values())

    def get_method_inputs(self, method_name: str) -> list[str]:
        """Return input variables for a method."""
        rec = self.kb.resolve_method(method_name)
        return rec.inputs if rec else []

    def get_method_outputs(self, method_name: str) -> list[str]:
        """Return output variables for a method."""
        rec = self.kb.resolve_method(method_name)
        return rec.outputs if rec else []

    def get_related_methods(self, method_name: str) -> list[str]:
        """Return related methods from ontology."""
        rec = self.kb.resolve_method(method_name)
        return rec.related if rec else []

    def infer_pipeline(self, methods: list[dict]) -> list[str]:
        """
        Identify the processing pipeline implied by detected methods.
        Uses v2 category + task taxonomy when available; falls back to keywords.
        """
        pipelines: set[str] = set()

        # v2 category-based classification
        categories: set[str] = set()
        tasks: set[str] = set()
        for m in methods:
            meta = m.get("kb_metadata", {})
            cat = meta.get("category")
            if cat:
                categories.add(cat)
            for t in meta.get("tasks", []):
                tasks.add(t)

        if "physical_based" in categories:
            pipelines.add("flood_modeling_pipeline")
        if "time_series" in categories:
            pipelines.add("time_series_forecasting_pipeline")
        if "machine_learning" in categories or "hybrid" in categories:
            pipelines.add("ml_prediction_pipeline")
        if "remote_sensing" in categories:
            pipelines.add("flood_mapping_pipeline")
        if "fuzzy_logic" in categories:
            pipelines.add("fuzzy_inference_pipeline")

        if "flood_forecasting" in tasks or "water_level_forecasting" in tasks:
            pipelines.add("flood_forecasting_pipeline")
        if "flood_inundation_mapping" in tasks or "flood_extent_mapping" in tasks:
            pipelines.add("flood_mapping_pipeline")

        # keyword fallback for non-v2 methods
        if not pipelines:
            detected_names = {m["name"].lower() for m in methods}
            flood_kw    = {"runoff", "routing", "hec", "swat", "lisflood", "precipitation", "hydrograph"}
            veg_kw      = {"ndvi", "evi", "savi", "vci", "pdsi", "spi"}
            terrain_kw  = {"dem", "slope", "twi", "hand", "srtm", "dtm"}
            if any(any(kw in n for kw in flood_kw) for n in detected_names):
                pipelines.add("flood_modeling_pipeline")
            if any(any(kw in n for kw in veg_kw) for n in detected_names):
                pipelines.add("drought_vegetation_pipeline")
            if any(any(kw in n for kw in terrain_kw) for n in detected_names):
                pipelines.add("terrain_analysis_pipeline")

        return sorted(pipelines)

    @staticmethod
    def _build_result(mrec: MethodRecord, ctx: str) -> dict:
        return {
            "name":        mrec.method_name,
            "type":        "method",
            "source":      "methods+results",
            "confidence":  0.88,
            "evidence":    ctx,
            "scores": {
                "pattern":   0.88,
                "context":   0.0,
                "embedding": 0.0,
                "llm":       0.0,
            },
            "final_score": 0.0,
            "accepted":    None,
            "role":        None,
            "kb_metadata": {
                "full_name":  mrec.method_name.replace("_", " ").title(),
                "type":       mrec.type,
                "type_group": "method",
                "domain":     mrec.domain,
                "subdomain":  mrec.subdomain,
                "definition": mrec.description,
                "inputs":     mrec.inputs,
                "outputs":    mrec.outputs,
                "related":    mrec.related,
                "aliases":    mrec.aliases,
                "source_kb":  mrec.source_kb,
                # v2 semantic fields
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
            },
        }

    def detect_coupled_models(self, text: str, methods: list[dict]) -> list[dict]:
        """
        Detect explicitly coupled model pairs in text.
        Looks for patterns like 'HEC-HMS + HEC-RAS', 'coupled with', 'linked to'.
        """
        COUPLING_RE = re.compile(
            r"coupled\s+with|coupled\s+to|linked\s+to|linked\s+with|"
            r"integrated\s+with|combined\s+with|chained\s+with",
            re.IGNORECASE,
        )
        detected_names = {m["name"].lower() for m in methods}
        couplings: list[dict] = []

        for mrec in self.kb.v2_models.values():
            if not mrec.coupled_with:
                continue
            a_name = mrec.method_name.lower()
            a_hit  = a_name in detected_names or any(
                a.lower() in detected_names for a in mrec.aliases
            )
            if not a_hit:
                continue
            for partner_id in mrec.coupled_with:
                partner = self.kb.resolve_v2_model(partner_id)
                if not partner:
                    continue
                b_name = partner.method_name.lower()
                b_hit  = b_name in detected_names or any(
                    a.lower() in detected_names for a in partner.aliases
                )
                if b_hit and COUPLING_RE.search(text):
                    couplings.append({
                        "model_a":      mrec.method_name,
                        "model_b":      partner.method_name,
                        "category_a":   mrec.category,
                        "category_b":   partner.category,
                        "dimension_a":  mrec.dimension,
                        "dimension_b":  partner.dimension,
                        "coupling_type": (
                            "hydrological_hydrodynamic"
                            if mrec.subcategory == "hydrological" and partner.subcategory == "hydrodynamic"
                            else "1D_2D" if mrec.dimension == "1D" and partner.dimension == "2D"
                            else "detected"
                        ),
                        "source": "ontology_v2",
                    })
        return couplings

    def detect_hybrid_models(self, methods: list[dict]) -> list[dict]:
        """
        Identify hybrid model configurations from detected methods.
        E.g., wavelet + LSTM = WA-LSTM; SARIMA + ANN = SARIMA-ANN.
        """
        names = {m["name"].lower() for m in methods}
        hybrids: list[dict] = []

        # explicit v2 hybrid models
        for mrec in self.kb.v2_models.values():
            if mrec.category != "hybrid":
                continue
            m_name = mrec.method_name.lower()
            if m_name in names or any(a.lower() in names for a in mrec.aliases):
                hybrids.append({
                    "name":        mrec.method_name,
                    "category":    mrec.category,
                    "subcategory": mrec.subcategory,
                    "components":  mrec.related,
                    "source_kb":   mrec.source_kb,
                })

        # signal-based inference: wavelet + ML → potential hybrid
        WAVELET_KW = {"wavelet", "wt", "dwt", "swt", "wa-"}
        ML_KW = {"lstm", "ann", "svr", "mlp", "rbf"}
        has_wavelet = any(any(kw in n for kw in WAVELET_KW) for n in names)
        has_ml      = any(any(kw in n for kw in ML_KW) for n in names)
        if has_wavelet and has_ml:
            hybrids.append({
                "name":     "wavelet_ml_hybrid_inferred",
                "category": "hybrid",
                "subcategory": "wavelet_ml",
                "components": list(names),
                "source_kb": "inferred",
            })

        return hybrids

    def group_by_family(self, methods: list[dict]) -> dict[str, list[dict]]:
        """
        Group detected methods by their v2 category.
        Unknown methods land in 'other'.
        """
        groups: dict[str, list[dict]] = {}
        for m in methods:
            cat = m.get("kb_metadata", {}).get("category") or "other"
            groups.setdefault(cat, []).append(m)
        return groups
