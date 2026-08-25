"""
judge_stage.py — Ollama LLM judge and deterministic constraint layer.

Extracted from pipeline.py to keep that file under 300 lines.
pipeline.py re-exports everything from here for backward compatibility.
"""
from __future__ import annotations

import json
import logging
import os
import re

import requests

from src.ingestion.utils import ensure_dict

log = logging.getLogger(__name__)

# ── Controlled vocabularies ───────────────────────────────────────────────────

VALID_TASK_LABELS = {
    "flood_mapping_satellite", "flood_modeling_hydraulic", "hydrological_modeling",
    "spectral_index_analysis", "drought_monitoring", "land_cover_classification",
    "land_use_change_detection", "flood_damage_assessment", "flood_susceptibility_mapping",
    "terrain_dem_analysis", "dem_validation", "review", "unknown",
}

VALID_STUDY_TYPES = {
    "case_study", "regional", "multi_site",
    "global_algorithmic", "review", "unknown",
}

# ── Constraint constants ──────────────────────────────────────────────────────

_HARD_HYDRO_METHODS = {"HEC-HMS", "SWAT", "HEC-RAS"}
_SCS_CN_SIGNALS     = {"SCS", "SCS-CN"}
_FLOOD_MAPPING_SATS = {"SAR", "Sentinel-1", "RADARSAT", "COSMO-SKYMED", "TERRASAR-X"}


# ── Small factory helpers (used by judge + constraint layers) ─────────────────

def make_task(label: str, confidence: float, source: str = "rules") -> dict:
    if label not in VALID_TASK_LABELS:
        raise ValueError(f"Unknown task label: {label}")
    return {"label": label, "confidence": float(confidence), "source": source}


def default_study_geo() -> dict:
    return {
        "primary_country": None, "countries": [],
        "regions": [], "rivers": [], "river_country_links": [],
        "locations": [], "coordinates": [], "confidence": 0.0,
    }


# ── OllamaJudge ───────────────────────────────────────────────────────────────

class OllamaJudge:
    def __init__(self, base_url="http://localhost:11434",
                 model="llama3.1:8b", timeout=120):
        self.url     = f"{base_url.rstrip('/')}/api/generate"
        self.model   = model
        self.timeout = timeout

    def judge(self, candidate_json: dict, sections: dict) -> dict:
        try:
            prompt   = self._build_prompt(candidate_json, sections)
            response = requests.post(
                self.url,
                json={"model": self.model, "prompt": prompt, "stream": False,
                      "format": "json", "options": {"temperature": 0, "top_p": 0.2}},
                timeout=self.timeout,
            )
            response.raise_for_status()
            return self._parse_json(response.json().get("response", ""))
        except requests.exceptions.ConnectionError:
            log.warning("[OllamaJudge] Ollama not reachable at %s", self.url)
            return {"status": "skipped", "reason": "ollama_unavailable"}
        except requests.exceptions.Timeout:
            log.warning("[OllamaJudge] request timed out (timeout=%ds)", self.timeout)
            return {"status": "skipped", "reason": "ollama_timeout"}
        except Exception as exc:
            log.error("[OllamaJudge] judge failed: %s", exc)
            return {"status": "failed", "reason": str(exc)[:200]}

    def _parse_json(self, text: str) -> dict:
        text = text.strip()
        text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
        text = re.sub(r"```[a-z]*\n?", "", text).strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", text, re.DOTALL)
            if not match:
                log.warning("Ollama returned non-JSON: %s", text[:200])
                return {}
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                log.warning("Ollama JSON parse failed: %s", text[:200])
                return {}

    def _build_prompt(self, candidate_json: dict, sections: dict) -> str:
        title    = candidate_json.get("title") or sections.get("title") or ""
        abstract = (sections.get("abstract") or "")[:2500]
        methods  = "\n".join(filter(None, [
            sections.get("methods", ""),
            sections.get("study_area", ""),
            sections.get("data_sources", ""),
        ]))[:3500]
        return f"""You are a scientific validation system.

STRICT RULES:
- Respond ONLY in English. Do NOT use any other language.
- Return ONLY valid JSON. Do NOT include markdown. Do NOT include explanations outside JSON.
- You are NOT allowed to invent new data.
- You are ONLY allowed to validate or correct existing extracted data.
- If uncertain, return "unknown".

Allowed study_type values: case_study | regional | multi_site | global_algorithmic | review | unknown
Allowed task values: flood_mapping_satellite | flood_modeling_hydraulic | hydrological_modeling | spectral_index_analysis | drought_monitoring | land_cover_classification | land_use_change_detection | flood_damage_assessment | flood_susceptibility_mapping | terrain_dem_analysis | dem_validation | review | unknown

Validation rules:
1. Study country must come from study area/title/abstract/methods — NOT author affiliation.
2. Rivers accepted only if they are the actual study watershed, not a citation reference.
3. Sentinel/Landsat/MODIS/RADARSAT are satellites; SRTM/ASTER/FABDEM/Copernicus DEM are DEM datasets.
4. If accepted=true, corrected_value MUST be null. If corrected_value differs from original_value, accepted MUST be false.

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
  "task": {{"accepted": true, "original_value": "...", "corrected_value": null, "confidence": 0.0}}
}}"""


# ── Judge helpers ─────────────────────────────────────────────────────────────

def is_ollama_available(base_url="http://localhost:11434") -> bool:
    try:
        r = requests.get(f"{base_url.rstrip('/')}/api/tags", timeout=5)
        return r.status_code == 200
    except Exception:
        return False


def is_valid_judge_task_verdict(task_verdict: dict) -> bool:
    task_verdict = ensure_dict(task_verdict)
    corrected    = task_verdict.get("corrected_value")
    accepted     = task_verdict.get("accepted")
    original     = task_verdict.get("original_value")
    if isinstance(original,  dict): original  = original.get("label")
    if isinstance(corrected, dict): corrected = corrected.get("label")
    if corrected and corrected not in VALID_TASK_LABELS:
        return False
    if accepted is True and corrected not in {None, original}:
        return False
    return True


def is_valid_judge_study_type_verdict(verdict: dict) -> bool:
    verdict   = ensure_dict(verdict)
    accepted  = verdict.get("accepted")
    corrected = verdict.get("corrected_value")
    if isinstance(corrected, dict): corrected = corrected.get("label")
    if corrected and corrected not in VALID_STUDY_TYPES: return False
    if accepted is True and corrected is not None: return False
    return True


def needs_judge(paper: dict) -> bool:
    entities    = ensure_dict(paper.get("entities"))
    geo         = ensure_dict(entities.get("geo"))
    study_geo   = ensure_dict(geo.get("study_geo"))
    study_type  = ensure_dict(geo.get("study_type"))
    task        = ensure_dict(entities.get("task"))
    task_label  = task.get("label", "")
    methods     = entities.get("methods", [])
    paper_id    = paper.get("metadata", {}).get("paper_id", "?")

    accepted_method_names = {
        m.get("name", "").upper() for m in methods if m.get("accepted")
    }
    _HYDRO_METHODS = {"HEC-HMS", "SWAT", "SCS", "SCS-CN", "HEC-RAS"}

    def _trigger(reason: str) -> bool:
        log.debug("[judge] %s → NEEDS JUDGE: %s", paper_id, reason)
        return True

    def _skip(reason: str) -> None:
        log.debug("[judge] %s skip: %s", paper_id, reason)

    if "SCS" in accepted_method_names:
        scs_meta = next(
            (m.get("kb_metadata", {}) for m in methods if m.get("name","").upper() == "SCS"),
            {},
        )
        if scs_meta.get("disambiguated") != "hydrology":
            return _trigger("SCS not disambiguated to hydrology")
        else:
            _skip("SCS disambiguated=hydrology")
    else:
        _skip("no SCS in accepted methods")

    if task_label == "flood_mapping_satellite" and _HYDRO_METHODS & accepted_method_names:
        return _trigger(f"task={task_label} conflicts with hydro methods {_HYDRO_METHODS & accepted_method_names}")
    else:
        _skip(f"no task/method conflict (task={task_label}, methods={accepted_method_names})")

    st_label = study_type.get("label", "?")
    st_conf  = study_type.get("confidence", 1.0)
    sg_conf  = study_geo.get("confidence", 1.0)
    t_conf   = task.get("confidence", 1.0)

    if study_type.get("needs_judge") is True:
        return _trigger(f"study_type.needs_judge=True (label={st_label})")
    else:
        _skip(f"study_type.needs_judge=False (label={st_label})")

    if st_conf < 0.85:
        return _trigger(f"study_type.confidence={st_conf:.2f} < 0.85")
    else:
        _skip(f"study_type.confidence={st_conf:.2f} ok")

    if st_label in {"global_algorithmic", "multi_site"}:
        if study_geo.get("primary_country") or study_geo.get("rivers"):
            return _trigger(f"label={st_label} with specific geo data")
        else:
            _skip(f"label={st_label} but no specific geo")
    else:
        _skip(f"study_type={st_label} not global/multi")

    if sg_conf < 0.75:
        return _trigger(f"study_geo.confidence={sg_conf:.2f} < 0.75")
    else:
        _skip(f"study_geo.confidence={sg_conf:.2f} ok")

    accepted_rivers = [r for r in study_geo.get("rivers", []) if r.get("accepted")]
    if accepted_rivers and sg_conf < 0.9:
        return _trigger(f"{len(accepted_rivers)} accepted rivers but study_geo.confidence={sg_conf:.2f} < 0.9")
    else:
        _skip(f"rivers check ok (accepted={len(accepted_rivers)}, sg_conf={sg_conf:.2f})")

    # Only trigger for DEMs when paired with satellites — a DEM alone is expected
    # in nearly all hydrology papers and does not warrant LLM review.
    if entities.get("dems") and entities.get("satellites"):
        return _trigger(f"DEMs + satellites present: {[d.get('name') for d in entities['dems']]}")
    else:
        _skip(f"dems+satellites check: dems={bool(entities.get('dems'))}, satellites={bool(entities.get('satellites'))}")

    if t_conf < 0.85:
        return _trigger(f"task.confidence={t_conf:.2f} < 0.85")
    else:
        _skip(f"task.confidence={t_conf:.2f} ok")

    log.debug("[judge] %s → no judge needed (all checks passed)", paper_id)
    return False


def judge_paper_with_ollama(paper: dict) -> dict:
    base_url   = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    model_name = os.getenv("OLLAMA_MODEL", "mistral-nemo:12b")
    if not is_ollama_available(base_url):
        return {"status": "skipped", "reason": f"Ollama not available at {base_url}"}
    sections  = ensure_dict(paper.get("sections"))
    metadata  = ensure_dict(paper.get("metadata"))
    entities  = ensure_dict(paper.get("entities"))
    candidate = {
        "paper_id":    metadata.get("paper_id"),
        "title":       metadata.get("title"),
        "study_type":  ensure_dict(ensure_dict(entities.get("geo")).get("study_type")).get("label", "unknown"),
        "study_geo":   ensure_dict(ensure_dict(entities.get("geo")).get("study_geo")),
        "task":        ensure_dict(entities.get("task")).get("label", "unknown"),
        "author_geo":  ensure_dict(entities.get("geo")).get("author_geo", []),
        "satellites":  entities.get("satellites", []),
        "dems":        entities.get("dems", []),
        "methods":     entities.get("methods", []),
    }
    judge = OllamaJudge(base_url=base_url, model=model_name, timeout=180)
    return judge.judge(candidate, sections)


def apply_judge_verdict(paper: dict, verdict: dict) -> dict:
    verdict  = ensure_dict(verdict)
    if verdict.get("status") in {"skipped", "failed"}:
        return paper
    entities  = paper.setdefault("entities", {})
    geo       = entities.setdefault("geo", {})
    geo["study_geo"] = ensure_dict(geo.get("study_geo"), default_study_geo())
    study_geo = geo["study_geo"]
    entities.setdefault("validation_warnings", [])

    st_verdict = ensure_dict(verdict.get("study_type"))
    if st_verdict:
        if not is_valid_judge_study_type_verdict(st_verdict):
            entities["validation_warnings"].append(
                {"type": "invalid_llm_study_type_verdict", "verdict": st_verdict}
            )
        elif not st_verdict.get("accepted", True):
            corrected = st_verdict.get("corrected_value")
            conf      = float(st_verdict.get("confidence", 0.7))
            if corrected and conf >= 0.8:
                geo["study_type"] = {
                    "label": corrected, "confidence": conf,
                    "source": "ollama_judge",
                    "previous": st_verdict.get("original_value"),
                    "reason": st_verdict.get("reason"),
                }

    c_verdict = ensure_dict(verdict.get("study_country"))
    if c_verdict:
        original      = c_verdict.get("original_value")
        corrected     = c_verdict.get("corrected_value")
        conf          = float(c_verdict.get("confidence", 0.7))
        final_country = corrected if corrected else original
        if isinstance(final_country, list):
            final_country = (
                final_country[0].get("name") if final_country and isinstance(final_country[0], dict)
                else (final_country[0] if final_country and isinstance(final_country[0], str) else None)
            )
        if isinstance(final_country, dict):
            final_country = final_country.get("name")
        if isinstance(final_country, str) and final_country and conf >= 0.8 and not study_geo.get("primary_country"):
            study_geo["primary_country"] = final_country
            study_geo["countries"]       = [{
                "name": final_country, "source": "ollama_judge",
                "confidence": conf, "reason": c_verdict.get("reason"),
            }]

    t_verdict = ensure_dict(verdict.get("task"))
    if t_verdict:
        if not is_valid_judge_task_verdict(t_verdict):
            entities["validation_warnings"].append(
                {"type": "invalid_llm_task_verdict", "verdict": t_verdict}
            )
        elif not t_verdict.get("accepted", True):
            corrected = t_verdict.get("corrected_value")
            conf      = float(t_verdict.get("confidence", 0.7))
            if corrected and conf >= 0.8:
                entities["task"] = {
                    "label": corrected, "confidence": conf,
                    "source": "ollama_judge",
                    "previous": t_verdict.get("original_value"),
                    "reason": t_verdict.get("reason"),
                }
    paper["task"] = entities.get("task", paper.get("task", {}))
    return paper


# ── Constraint layer ──────────────────────────────────────────────────────────

def apply_constraints(paper: dict) -> dict:
    """
    Deterministic constraint layer applied after all extraction and scoring.

    Rules (in priority order):
    1. HEC-HMS / SWAT accepted and used → task = hydrological_modeling
    2. SCS-CN / SCS (hydro disambiguated) accepted → task = hydrological_modeling
    3. DEM accepted alongside a hydrological model → DEM role = terrain_input
    4. task = flood_mapping_satellite but no flood-extent satellite accepted
       → downgrade to hydrological_modeling if hydro methods present
    """
    entities = paper.setdefault("entities", {})
    methods  = entities.get("methods", [])
    dems     = entities.get("dems", [])
    sats     = entities.get("satellites", [])
    task     = entities.get("task", {})

    accepted_methods = {
        m["name"].upper()
        for m in methods
        if m.get("accepted") and m.get("role") == "used"
    }
    scs_hydro = any(
        m.get("name","").upper() == "SCS"
        and m.get("kb_metadata", {}).get("disambiguated") == "hydrology"
        and m.get("accepted")
        for m in methods
    )

    has_hard_hydro = bool(_HARD_HYDRO_METHODS & accepted_methods)
    has_scs_cn     = bool(_SCS_CN_SIGNALS & accepted_methods) or scs_hydro

    if (has_hard_hydro or has_scs_cn) and task.get("label") != "hydrological_modeling":
        entities["task"] = make_task("hydrological_modeling", 0.95, source="constraint")
        task = entities["task"]

    if has_hard_hydro:
        for dem in dems:
            if dem.get("accepted") and dem.get("role") in {"used", "mentioned", None}:
                dem["role"] = "terrain_input"

    if task.get("label") == "flood_mapping_satellite":
        accepted_sat_names = {s["name"] for s in sats if s.get("accepted")}
        has_flood_sat = bool(_FLOOD_MAPPING_SATS & accepted_sat_names)
        if not has_flood_sat and (has_hard_hydro or has_scs_cn):
            entities["task"] = make_task("hydrological_modeling", 0.88, source="constraint")

    paper["task"] = entities.get("task", paper.get("task", {}))
    return paper
