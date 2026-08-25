import json
import logging
import re
import requests
import ray


from src.config.settings import (
    OLLAMA_MODEL,
    OLLAMA_URL
)

log = logging.getLogger(__name__)

@ray.remote(max_concurrency=3, max_restarts=1)
class OllamaActor:

    def __init__(
            self,
            timeout=120,
    ):
        self.model = OLLAMA_MODEL
        self.url = f"{OLLAMA_URL.rstrip('/')}/api/generate"
        self.timeout = timeout
        self.session = requests.Session()

    def judge(
            self,
            candidate_json: dict,
            sections: dict
    ) -> dict:

        try:
            prompt = self._build_prompt(candidate_json, sections)
            response = self.session.post(
                self.url,
                json={
                    "model": self.model,
                    "prompt": prompt,
                    "stream": False,
                    "format": "json",
                    "options": {
                        "temperature": 0,
                        "top_p": 0.2,
                    },
                },
                timeout=self.timeout,
            )
            response.raise_for_status()
            parsed = self._parse_json(response.json().get("response", ""))
            if parsed and "status" not in parsed:
                parsed["status"] = "success"
            return parsed
        except requests.exceptions.ConnectionError as exc:
            log.warning("[OllamaActor] Ollama not reachable: %s", exc)
            return {"status": "skipped", "reason": "ollama_unavailable"}
        except requests.exceptions.Timeout:
            log.warning("[OllamaActor] Ollama request timed out (timeout=%ds)", self.timeout)
            return {"status": "skipped", "reason": "ollama_timeout"}
        except Exception as exc:
            log.error("[OllamaActor] judge failed: %s", exc)
            return {"status": "failed", "reason": str(exc)[:200]}

    def _parse_json(self, text: str) -> dict:

        text = text.strip()

        text = re.sub(
            r"<think>.*?</think>",
            "",
            text,
            flags=re.DOTALL
        ).strip()

        text = re.sub(
            r"```[a-z]*\n?",
            "",
            text
        ).strip()

        try:
            return json.loads(text)

        except json.JSONDecodeError:

            match = re.search(
                r"\{.*\}",
                text,
                re.DOTALL
            )

            if not match:
                return {}

            try:
                return json.loads(match.group(0))

            except json.JSONDecodeError:
                return {}

    def _build_prompt(self, candidate_json: dict, sections: dict) -> str:
        title    = candidate_json.get("title") or sections.get("title") or ""
        abstract = (sections.get("abstract") or "")[:2500]
        methods  = "\n".join(filter(None, [
            sections.get("methods", ""),
            sections.get("study_area", ""),
            sections.get("data_sources", ""),
        ]))[:3500]
        return f"""You are a scientific validation system for flood modeling literature.

                STRICT RULES:
                - Respond ONLY in English. Do NOT use any other language.
                - Return ONLY valid JSON. Do NOT include markdown. Do NOT include explanations outside JSON.
                - You are NOT allowed to invent new data.
                - You are ONLY allowed to validate or correct existing extracted data.
                - If uncertain, return "unknown".

                Allowed study_type values:
                  case_study | regional | multi_site | global_algorithmic | review | unknown

                Allowed task values (v2 taxonomy):
                  flood_forecasting | flood_inundation_mapping | flood_extent_mapping |
                  rainfall_runoff_modeling | hydraulic_simulation | flood_susceptibility_mapping |
                  streamflow_prediction | water_level_forecasting | real_time_flood_forecasting |
                  flood_frequency_analysis | dam_break_analysis | urban_flood_modeling |
                  coastal_flood_modeling | wetland_flood_mapping |
                  flood_mapping_satellite | flood_modeling_hydraulic | hydrological_modeling |
                  spectral_index_analysis | drought_monitoring | land_cover_classification |
                  dem_validation | review | unknown

                Allowed model_category values:
                  physical_based | time_series | machine_learning | hybrid |
                  fuzzy_logic | data_assimilation | remote_sensing | empirical | statistical

                Allowed dimension values:
                  1D | 2D | coupled_1D_2D | null

                Validation rules:
                1. Study country must come from study area/title/abstract/methods — NOT author affiliation.
                2. Rivers accepted only if they are the actual study watershed, not a citation reference.
                3. Sentinel/Landsat/MODIS/RADARSAT are satellites; SRTM/ASTER/FABDEM/Copernicus DEM are DEM datasets.
                4. If accepted=true, corrected_value MUST be null. If corrected_value differs from original_value, accepted MUST be false.
                5. For model_category: physical_based=uses governing equations (Saint-Venant, shallow water); time_series=statistical/ML on sequential data; hybrid=combines two approaches.
                6. For dimension: 1D=river cross-sections only; 2D=spatial grid/mesh; coupled_1D_2D=both linked.
                7. For coupling: detect if two models are explicitly linked (e.g. HEC-HMS + HEC-RAS, MIKE-11 + MIKE-21).

                INPUT:
                {json.dumps(candidate_json, ensure_ascii=False, indent=2)}

                EVIDENCE:
                TITLE: {title[:800]}
                ABSTRACT: {abstract}
                METHODS: {methods}

                OUTPUT JSON ONLY:
                {{
                  "paper_id": "...",
                  "study_type": {{"accepted": true, "original_value": "...", "corrected_value": null, "confidence": 0.0}},
                  "study_country": {{"accepted": true, "original_value": "...", "corrected_value": null, "confidence": 0.0}},
                  "rivers": [],
                  "data_sources": [],
                  "task": {{"accepted": true, "original_value": "...", "corrected_value": null, "confidence": 0.0}},
                  "model_category": {{"accepted": true, "original_value": "...", "corrected_value": null, "confidence": 0.0}},
                  "dimension": {{"accepted": true, "original_value": "...", "corrected_value": null, "confidence": 0.0}},
                  "coupled_models": []
                }}"""