"""
LLM-based extractor using a locally running Ollama instance.

Falls back gracefully to the RegexExtractor when Ollama is unreachable
or returns unparseable output.

Usage (requires Ollama running):
    $ ollama pull llama3.2
    $ ollama serve
"""
from __future__ import annotations

import json
import logging
import re
import textwrap
from typing import Any

import requests

from src.config import OLLAMA_BASE_URL, OLLAMA_MODEL, OLLAMA_TIMEOUT
from src.extraction.base import BaseExtractor, ExtractionResult
from src.extraction.regex_extractor import RegexExtractor

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = textwrap.dedent("""\
    You are a scientific data extraction system for satellite-based flood mapping literature.
    Your role: extract what is present — never invent values.
    Always reply with valid JSON only — no prose, no markdown fences.
    Numeric accuracy values must be on a 0–1 scale (e.g. 0.95, not 95).
    NEVER hallucinate satellite names, methods, regions, or numeric metrics.
""")

_USER_TEMPLATE = textwrap.dedent("""\
    Review the paper below and return a JSON object with exactly these keys:
      Study_Type, Satellite_Names, Sensor_Type, Data_Product,
      Country, Region, River_Basin, City_Event, Methods,
      OA, F1, IoU, Kappa, Near_Real_Time, Latency,
      Missing_Data_Explanation

    INPUT:
    TITLE: {title}
    ABSTRACT: {abstract}

    FIELD RULES:

    Study_Type:
    - One of: "Satellite flood mapping" | "ML/DL classification" |
      "Hydrological forecasting" | "Hydraulic modeling" |
      "Operational mapping system" | "Review paper" | "Dataset/benchmark paper"

    Satellite_Names:
    - Comma-separated list of satellite/sensor names found
      (e.g. "Sentinel-1, Sentinel-2"); null if absent

    Sensor_Type:
    - "SAR" | "Optical" | "Multi-sensor"; null if absent

    Data_Product:
    - Data product codes e.g. "GRD", "MSI", "OLI"; null if absent

    Country:
    - Country name; if Ukraine mentioned → "Ukraine"; null if absent

    Region:
    - Sub-national area, continent, or geographic region; null if absent

    River_Basin:
    - River or basin name; null if absent

    City_Event:
    - Specific city or named flood event; null if absent

    Methods:
    - Comma-separated list of methods found from:
        Thresholding | Change detection | NDWI/MNDWI | Random Forest | SVM |
        Maximum likelihood | U-Net | CNN | LSTM | Transformer | OBIA |
        Hydrodynamic model | Operational workflow
    - null if absent

    OA / F1 / IoU / Kappa:
    - Float 0–1; range (0.84–0.95) → mean; null if not explicitly stated
    - Only extract if paper is about classification/mapping accuracy

    Near_Real_Time:
    - true | false | null

    Latency:
    - String like "<6 h" or "1–3 days"; null if absent

    Missing_Data_Explanation:
    - One sentence listing fields that could not be extracted and why; null if complete

    Return valid JSON only.
""")

_FALLBACK = RegexExtractor()


class OllamaExtractor(BaseExtractor):
    """
    LLM-backed extractor using Ollama's /api/generate endpoint.
    Input is title + abstract for focused, reliable extraction.
    Falls back to RegexExtractor on any error.
    """

    def __init__(
        self,
        base_url: str = OLLAMA_BASE_URL,
        model: str = OLLAMA_MODEL,
        timeout: int = OLLAMA_TIMEOUT,
    ) -> None:
        self._url     = f"{base_url.rstrip('/')}/api/generate"
        self._model   = model
        self._timeout = timeout
        self._available: bool | None = None

    # ── public ────────────────────────────────────────────────────────────────

    def extract(
        self,
        chunks: list[dict],
        source_file: str,
    ) -> ExtractionResult:
        regex_result = _FALLBACK.extract(chunks, source_file)

        if not self._is_available():
            logger.warning(
                "Ollama not reachable at %s — falling back to RegexExtractor",
                self._url,
            )
            return regex_result

        try:
            raw_json = self._call_ollama(regex_result.title, regex_result.abstract)
            result   = self._parse_response(raw_json, source_file)
        except Exception as exc:
            logger.warning("Ollama extraction failed (%s) — falling back to regex", exc)
            return regex_result

        return _fill_from_regex(result, regex_result).finalize()

    # ── private ───────────────────────────────────────────────────────────────

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
            logger.info("Ollama is available at %s (model: %s)", self._url, self._model)
        return self._available

    def _call_ollama(self, title: str, abstract: str) -> str:
        prompt = _USER_TEMPLATE.format(
            title=title or "(not available)",
            abstract=abstract or "(not available)",
        )
        payload = {
            "model":  self._model,
            "prompt": prompt,
            "system": _SYSTEM_PROMPT,
            "stream": False,
            "format": "json",
            "options": {"temperature": 0.0, "num_predict": 500},
        }
        resp = requests.post(self._url, json=payload, timeout=self._timeout)
        resp.raise_for_status()
        return resp.json().get("response", "")

    def _parse_response(self, raw: str, source_file: str) -> ExtractionResult:
        raw = re.sub(r"```(?:json)?", "", raw).strip()
        data: dict[str, Any] = json.loads(raw)

        def _s(key: str) -> str:
            val = data.get(key) or data.get(key.lower())
            return str(val).strip() if val and str(val) != "null" else ""

        def _f(key: str) -> float | None:
            val = data.get(key) or data.get(key.lower())
            if val is None or str(val) == "null":
                return None
            try:
                num = float(val)
                return round(num / 100.0 if num > 1.0 else num, 4)
            except (TypeError, ValueError):
                return None

        def _b(key: str) -> bool | None:
            val = data.get(key) or data.get(key.lower())
            if val is None or str(val) == "null":
                return None
            if isinstance(val, bool):
                return val
            s = str(val).lower()
            if s in ("true", "yes", "1"):
                return True
            if s in ("false", "no", "0"):
                return False
            return None

        return ExtractionResult(
            source_file              = source_file,
            study_type               = _s("Study_Type"),
            satellite_names          = _s("Satellite_Names"),
            sensor_type              = _s("Sensor_Type"),
            data_product             = _s("Data_Product"),
            country                  = _s("Country"),
            region                   = _s("Region"),
            river_basin              = _s("River_Basin"),
            city_event               = _s("City_Event"),
            methods                  = _s("Methods"),
            oa                       = _f("OA"),
            f1                       = _f("F1"),
            iou                      = _f("IoU"),
            kappa                    = _f("Kappa"),
            near_real_time           = _b("Near_Real_Time"),
            latency                  = _s("Latency"),
            missing_data_explanation = _s("Missing_Data_Explanation"),
            confidence               = 0.85,
        )


# ── merge helper ──────────────────────────────────────────────────────────────

def _fill_from_regex(
    llm: ExtractionResult,
    regex: ExtractionResult,
) -> ExtractionResult:
    """Fill blank LLM fields with regex values."""
    scalar_fields = (
        "study_type", "satellite_names", "sensor_type", "data_product",
        "country", "region", "river_basin", "city_event", "methods",
        "near_real_time", "latency", "revisit_time",
        "oa", "f1", "iou", "kappa",
    )
    for field in scalar_fields:
        if not getattr(llm, field, None) and getattr(regex, field, None):
            setattr(llm, field, getattr(regex, field))

    # bibliographic fields always come from regex
    llm.title     = regex.title
    llm.abstract  = regex.abstract
    llm.doi       = regex.doi
    llm.year      = regex.year
    llm.authors   = regex.authors
    llm.full_text = regex.full_text
    llm.evidence  = regex.evidence

    # Geography fields derived from keyword scan — always use regex
    llm.ukraine_relevance = regex.ukraine_relevance
    if not llm.river_name:
        llm.river_name = regex.river_name or regex.river_basin

    if not llm.missing_data_explanation and regex.missing_data_explanation:
        llm.missing_data_explanation = regex.missing_data_explanation

    return llm
