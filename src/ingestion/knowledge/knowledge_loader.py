"""
knowledge_loader.py
-------------------
Loads and merges all JSON knowledge bases into a single unified structure.

Sources:
  glossary_acronyms.json              → entities, patterns, types
  ontology_methods.json               → methods, aliases, inputs/outputs
  floods_satelite.json                → flood-specific sensor/model mappings
  bfe_methods.json                    → BFE hydrology methods
  remote_sensing_water_resources.json → domain structure and pipelines
  flood_modeling_ontology_v2.json     → full semantic ontology (v2)
"""

from __future__ import annotations

import json
import re
import logging
from dataclasses import dataclass, field
from pathlib import Path
from functools import lru_cache
from typing import Optional

log = logging.getLogger(__name__)

# ─── data directory ───────────────────────────────────────────────────────────
_DATA_DIR = Path(__file__).resolve().parents[3] / "data"

# ─── type-group → semantic category ──────────────────────────────────────────
SATELLITE_TYPE_GROUPS = {"sensor"}
SATELLITE_TYPES       = {"mission", "satellite", "sensor", "instrument"}
DEM_KEYWORDS          = {"elevation", "terrain", "dem", "dtm", "dsm", "lidar",
                         "topograph", "altim", "bathymetr"}
METHOD_TYPE_GROUPS    = {"method", "model", "process"}
METRIC_TYPE_GROUPS    = {"metric"}

# Known short aliases that the regex won't catch automatically
SHORT_ALIASES: dict[str, str] = {
    "S1":  "Sentinel-1",
    "S-1": "Sentinel-1",
    "S1A": "Sentinel-1",
    "S1B": "Sentinel-1",
    "S2":  "Sentinel-2",
    "S-2": "Sentinel-2",
    "S2A": "Sentinel-2",
    "S2B": "Sentinel-2",
    "S3":  "Sentinel-3",
    "L8":  "Landsat",
    "L9":  "Landsat",
    "ETM+": "Landsat",
    "GEE": "Google Earth Engine",
    "HEC": "HEC-RAS",
    "RF":  "Random Forest",
    "SVM": "SVM",
    "CNN": "CNN",
    "DL":  "Deep learning",
    "ML":  "Machine learning",
}

# High-quality pattern overrides for entities where auto-generation is weak
PATTERN_OVERRIDES: dict[str, str] = {
    # Satellites
    "Sentinel-1":   r"\bsentinel[\s\-]?1[abc]?\b|\bs[\-]?1[abc]?\b",
    "Sentinel-2":   r"\bsentinel[\s\-]?2[abc]?\b|\bs[\-]?2[abc]?\b",
    "Sentinel-3":   r"\bsentinel[\s\-]?3[abc]?\b|\bs[\-]?3[abc]?\b",
    "LANDSAT":      r"\blandsat[\s\-]?(4|5|7|8|9|ole)?\b",
    "MODIS":        r"\bmodis\b",
    "VIIRS":        r"\bviirs\b",
    "RADARSAT":     r"\bradarsat[\s\-]?\d?\b",
    "PALSAR":       r"\bpalsar[\s\-]?2?\b|\balos[\s\-]?palsar\b",
    "TERRASAR-X":   r"\bterrasar[\s\-]?x\b",
    "COSMO-SKYMED": r"\bcosmo[\s\-]?skymed\b",
    "ICESAT-2":     r"\bicesat[\s\-]?2\b|\batl0[368]\b",
    # DEMs
    "SRTM":         r"\bsrtm\b",
    "NASADEM":      r"\bnasadem\b",
    "FABDEM":       r"\bfabdem\b",
    "TANDEM-X":     r"\btandem[\-\s]?x\b",
    # Indices
    "NDVI":         r"\bndvi\b|normalized difference vegetation index",
    "NDWI":         r"\bndwi\b|normalized difference water index",
    "MNDWI":        r"\bmndwi\b|modified normalized difference water index",
    "NDII":         r"\bndii\b|normalized difference infrared index",
    "AWEI":         r"\bawei\b|automated water extraction index",
    "EVI":          r"\bevi\b|enhanced vegetation index",
    "SAVI":         r"\bsavi\b|soil adjusted vegetation index",
    # Hydro models
    "HEC-RAS":      r"\bhec[\s\-]?ras\b",
    "HEC-HMS":      r"\bhec[\s\-]?hms\b",
    "SWAT":         r"\bswat\+?\b|soil and water assessment tool",
    "LISFLOOD":     r"\blisflood\b",
    # Methods
    "SAR":          r"\bsar\b|synthetic aperture radar",
    "INSAR":        r"\binsar\b|\bifsar\b|interferometric synthetic aperture",
    "LIDAR":        r"\blidar\b|light detection and ranging",
    "HAND":         r"\bhand\b|height above nearest drainage|height above nearest stream",
    "TWI":          r"\btwi\b|topographic wetness index",
    "U-NET":        r"\bu[\-\s]?net\b|unet",
    "CNN":          r"\bcnn\b|convolutional neural network",
    "RANDOM-FOREST":r"\brandom forest\b|\brf\b(?=\s+classif)",
    "SVM":          r"\bsvm\b|support vector machine",
    "GEE":          r"\bgoogle earth engine\b|\bgee\b",
    # Disambiguation: MAP as a proper-noun method needs full phrase, not bare acronym
    "MAP":          r"\bmaximum[\s\-]?a[\s\-]?posteriori\b",
    # Metrics
    "RMSE":         r"\brmse\b",
    "MAE":          r"\bmae\b|mean absolute error",
    "NSE":          r"\bnse\b|nash[\-\s]?sutcliffe",
    "KGE":          r"\bkge\b|kling[\-\s]?gupta",
    # --- Noise suppression: single-letter / ultra-generic KB entries -----------
    # These match common English words and produce constant false positives.
    # Override with full-phrase-only patterns so the acronym alone is ignored.
    "J":            r"\bcost[\s\-]?function\b",             # \bJ\b is too broad
    "INDEX":        r"\bspectral[\s\-]?index\b",            # \bINDEX\b matches everywhere
    "SENSOR":       r"\bremote[\s\-]?sensing[\s\-]?sensor\b",
    "IMAGER":       r"\bsatellite[\s\-]?imager\b",
    "BRIDGE":       r"\bnetwork[\s\-]?bridge\b",            # not a geo entity
    "TOPOGRAPHY":   r"\btopographic[\s\-]?data\b|topography[\s\-]?dataset",
    "SENTINEL":     r"\bsentinel[\s\-]?[123][abc]?\b",      # require version number
    "TM":           r"\bthematic[\s\-]?mapper\b|\blandsat[\s\-]?tm\b",
    "ATLAS":        r"\bicesat[\s\-]?2\s+atlas\b|\batlas\s+instrument\b",
    "ASPECT":       r"\bterrain[\s\-]?aspect\b|\bslope[\s\-]?aspect\b",
    "GLOBE":        r"\bglobe[\s\-]?dem\b|global[\s\-]?land[\s\-]?one[\s\-]?km",
}

# ─── robust JSON loader for files with multiple concatenated objects ──────────

def _parse_json_file(path: Path) -> list[dict]:
    """
    Parse a JSON file that may contain multiple concatenated objects
    and/or markdown code fences.
    Returns a list of all parsed top-level objects.
    """
    raw = path.read_text(encoding="utf-8")
    # strip markdown fences
    raw = re.sub(r"```[^\n]*\n", "", raw)
    raw = re.sub(r"```", "", raw)

    # try plain parse first
    raw_stripped = raw.strip()
    if raw_stripped.startswith("{") or raw_stripped.startswith("["):
        try:
            obj = json.loads(raw_stripped)
            return obj if isinstance(obj, list) else [obj]
        except json.JSONDecodeError:
            pass

    # fall back to scanning for top-level objects
    results: list[dict] = []
    i = 0
    n = len(raw)
    while i < n:
        start = raw.find("{", i)
        if start == -1:
            break
        depth = 0
        in_str = False
        esc = False
        end = start
        for j in range(start, n):
            c = raw[j]
            if esc:
                esc = False
                continue
            if c == "\\" and in_str:
                esc = True
                continue
            if c == '"':
                in_str = not in_str
                continue
            if in_str:
                continue
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    end = j
                    break
        if end == start:
            i = start + 1
            continue
        fragment = raw[start : end + 1]
        try:
            obj = json.loads(fragment)
            if isinstance(obj, dict):
                results.append(obj)
        except json.JSONDecodeError:
            pass
        i = end + 1
    return results


# ─── dataclasses ─────────────────────────────────────────────────────────────

@dataclass
class EntityRecord:
    """Single entry from the unified knowledge base."""
    acronym:     str
    full_name:   str
    type:        str
    type_group:  str
    domain:      str
    definition:  str
    used_for:    list[str] = field(default_factory=list)
    related:     list[str] = field(default_factory=list)
    contexts:    list[str] = field(default_factory=list)
    pattern:     Optional[str] = None
    is_satellite: bool = False
    is_dem:       bool = False
    is_method:    bool = False
    is_metric:    bool = False
    source_kb:   str = "glossary"

    def matches(self, text: str) -> Optional[re.Match]:
        if not self.pattern:
            return None
        return re.search(self.pattern, text, re.IGNORECASE)


@dataclass
class MethodRecord:
    """Method from ontology_methods.json or flood_modeling_ontology_v2.json."""
    method_name: str
    aliases:     list[str]
    type:        str
    domain:      str
    subdomain:   str
    description: str
    # v1-compatible fields (loader reads primary_inputs/uses_data → inputs, etc.)
    inputs:      list[str] = field(default_factory=list)
    outputs:     list[str] = field(default_factory=list)
    related:     list[str] = field(default_factory=list)
    pattern:     Optional[str] = None
    source_kb:   str = "ontology"
    # v2 semantic fields (None = not known / not applicable)
    category:    Optional[str] = None
    subcategory: Optional[str] = None
    dimension:   Optional[str] = None
    physics_based:  Optional[bool] = None
    data_driven:    Optional[bool] = None
    evaluation_metrics:             list[str] = field(default_factory=list)
    coupled_with:                   list[str] = field(default_factory=list)
    equations:                      list[str] = field(default_factory=list)
    requires_calibration:           Optional[bool] = None
    supports_real_time_forecasting: Optional[bool] = None
    supports_ungauged_basins:       Optional[bool] = None
    strengths:        list[str] = field(default_factory=list)
    limitations:      list[str] = field(default_factory=list)
    tasks:            list[str] = field(default_factory=list)
    typical_use_cases: list[str] = field(default_factory=list)


@dataclass
class MetricRecord:
    """Evaluation metric from flood_modeling_ontology_v2.json."""
    id:        str
    name:      str
    full_name: str
    optimal:   float
    range:     str
    scale:     Optional[str] = None
    applicable_to:        list[str] = field(default_factory=list)
    threshold_acceptable: Optional[float] = None
    source_kb: str = "ontology_v2"


@dataclass
class TaskRecord:
    """Scientific task from flood_modeling_ontology_v2.json."""
    id:     str
    name:   str
    label:  str
    domain: str
    requires_DEM:           Optional[bool] = None
    requires_precipitation: Optional[bool] = None
    requires_real_time:     Optional[bool] = None
    output: Optional[str] = None
    source_kb: str = "ontology_v2"


@dataclass
class EquationRecord:
    """Governing equation from flood_modeling_ontology_v2.json."""
    id:        str
    name:      str
    full_name: str
    type:      str
    conserves: list[str] = field(default_factory=list)
    used_in:   list[str] = field(default_factory=list)
    source_kb: str = "ontology_v2"


@dataclass
class UncertaintyRecord:
    """Source of uncertainty from flood_modeling_ontology_v2.json."""
    id:      str
    name:    str
    source:  str
    affects: list[str] = field(default_factory=list)
    source_kb: str = "ontology_v2"


# ─── knowledge base ───────────────────────────────────────────────────────────

class KnowledgeBase:
    """
    Unified, JSON-driven knowledge base for geospatial entity extraction.

    Provides:
      - entities dict       (ACRONYM → EntityRecord)
      - methods list        (MethodRecord instances from v1 ontology)
      - v2_models dict      (id → MethodRecord from v2 ontology)
      - v2_metrics dict     (id → MetricRecord)
      - v2_tasks dict       (id → TaskRecord)
      - v2_equations dict   (id → EquationRecord)
      - v2_uncertainties    (id → UncertaintyRecord)
      - bfe_methods dict    (BFE method entries)
      - floods dict         (flood-specific satellite/model mappings)
      - rs_domains dict     (remote-sensing domain structure)
      - aliases dict        (alias string → canonical acronym)
    """

    def __init__(self):
        self.entities:   dict[str, EntityRecord] = {}
        self.methods:    list[MethodRecord] = []
        self.v2_models:      dict[str, MethodRecord]    = {}
        self.v2_metrics:     dict[str, MetricRecord]    = {}
        self.v2_tasks:       dict[str, TaskRecord]      = {}
        self.v2_equations:   dict[str, EquationRecord]  = {}
        self.v2_uncertainties: dict[str, UncertaintyRecord] = {}
        self.bfe_methods: dict[str, dict] = {}
        self.floods:     dict = {}
        self.rs_domains: dict = {}
        self.aliases:    dict[str, str] = {}
        self.disambiguation_rules: list[dict] = []
        self._method_index: dict[str, MethodRecord] = {}

    # ── derived views ──────────────────────────────────────────────────────────

    def get_satellites(self) -> list[EntityRecord]:
        return [e for e in self.entities.values() if e.is_satellite]

    def get_dems(self) -> list[EntityRecord]:
        return [e for e in self.entities.values() if e.is_dem]

    def get_methods(self) -> list[EntityRecord]:
        return [e for e in self.entities.values() if e.is_method]

    def get_metrics(self) -> list[EntityRecord]:
        return [e for e in self.entities.values() if e.is_metric]

    def get_entities_by_type_group(self, *groups: str) -> list[EntityRecord]:
        return [e for e in self.entities.values() if e.type_group in groups]

    def get_entities_by_domain(self, domain: str) -> list[EntityRecord]:
        return [e for e in self.entities.values() if e.domain == domain]

    # v2 convenience views

    def get_v2_models_by_category(self, category: str) -> list[MethodRecord]:
        return [m for m in self.v2_models.values() if m.category == category]

    def get_v2_models_by_dimension(self, dimension: str) -> list[MethodRecord]:
        return [m for m in self.v2_models.values() if m.dimension == dimension]

    def get_v2_physical_models(self) -> list[MethodRecord]:
        return [m for m in self.v2_models.values() if m.physics_based]

    def get_v2_data_driven_models(self) -> list[MethodRecord]:
        return [m for m in self.v2_models.values() if m.data_driven]

    # ── alias resolution ───────────────────────────────────────────────────────

    def resolve(self, name: str) -> Optional[EntityRecord]:
        """Resolve a name (exact or alias) to an EntityRecord."""
        key = name.upper()
        if key in self.entities:
            return self.entities[key]
        canonical = self.aliases.get(name.lower())
        if canonical and canonical in self.entities:
            return self.entities[canonical]
        return None

    def resolve_method(self, name: str) -> Optional[MethodRecord]:
        return self._method_index.get(name.lower())

    def resolve_metric(self, name: str) -> Optional[MetricRecord]:
        # resolve() only searches EntityRecord glossary; v2_metrics holds canonical
        # MetricRecords (NSE, RMSE, KGE, ...) that resolve() misses.
        name_u = name.upper()
        return next((m for k, m in self.v2_metrics.items() if k.upper() == name_u), None)

    def resolve_v2_model(self, name: str) -> Optional[MethodRecord]:
        """Resolve a name/alias to a v2 MethodRecord."""
        slug = name.lower().strip()
        # direct id match
        if slug in self.v2_models:
            return self.v2_models[slug]
        # search aliases
        for mrec in self.v2_models.values():
            if mrec.method_name.lower() == slug:
                return mrec
            if any(a.lower() == slug for a in mrec.aliases):
                return mrec
        return None

    # ── internal builders ─────────────────────────────────────────────────────

    def _register_aliases(self):
        """Build the aliases dict from all entity records + SHORT_ALIASES."""
        for key, rec in self.entities.items():
            self.aliases[key.lower()] = key
            if rec.full_name:
                self.aliases[rec.full_name.lower()] = key
        for alias, canonical in SHORT_ALIASES.items():
            canon_key = canonical.upper()
            matched = next(
                (k for k in self.entities if k == canon_key
                 or self.entities[k].full_name.lower() == canonical.lower()),
                None
            )
            if matched:
                self.aliases[alias.lower()] = matched
            else:
                self.aliases[alias.lower()] = canonical.upper()

    def _build_method_index(self):
        for m in self.methods:
            self._method_index[m.method_name.lower()] = m
            for alias in m.aliases:
                self._method_index[alias.lower()] = m
        # also index v2 models
        for mrec in self.v2_models.values():
            self._method_index[mrec.method_name.lower()] = mrec
            for alias in mrec.aliases:
                self._method_index[alias.lower()] = mrec

    def _classify_entity(self, rec: EntityRecord):
        """Tag entity as satellite / DEM / method / metric."""
        fn = (rec.full_name or "").lower()
        tg = rec.type_group
        t  = (rec.type or "").lower()
        d  = rec.domain

        if tg in SATELLITE_TYPE_GROUPS and t in SATELLITE_TYPES and d == "remote_sensing":
            ground_kw = {"rain gauge", "gauge", "ground", "static physical"}
            if not any(kw in fn for kw in ground_kw):
                rec.is_satellite = True

        if any(kw in fn or kw in rec.acronym.lower() for kw in DEM_KEYWORDS):
            if tg in ("data", "sensor", "parameter", "concept"):
                rec.is_dem = True

        if tg in METHOD_TYPE_GROUPS:
            rec.is_method = True

        if tg in METRIC_TYPE_GROUPS:
            rec.is_metric = True


def _build_pattern(acronym: str, full_name: str) -> str:
    """Auto-generate a regex pattern from acronym + full_name."""
    for key, pat in PATTERN_OVERRIDES.items():
        if key.upper() == acronym.upper():
            return pat
        if full_name and key.lower() == full_name.lower():
            return pat

    parts: list[str] = []

    acr_esc = re.escape(acronym)
    parts.append(fr"\b{acr_esc}\b")

    if full_name and len(full_name) > 4:
        fn = full_name.lower()
        fn_esc = re.escape(fn)
        fn_esc = re.sub(r"\\ ", r"[\\s\\-]?", fn_esc)
        parts.append(fn_esc)

    return "|".join(parts)


# ─── loaders ─────────────────────────────────────────────────────────────────

def _load_glossary(kb: KnowledgeBase, path: Path):
    blocks = _parse_json_file(path)
    for block in blocks:
        for acronym, entry in block.items():
            if not isinstance(entry, dict):
                continue
            rec = EntityRecord(
                acronym    = acronym,
                full_name  = entry.get("full_name", ""),
                type       = entry.get("type", "concept"),
                type_group = entry.get("type_group", "concept"),
                domain     = entry.get("domain", "remote_sensing"),
                definition = entry.get("definition", ""),
                used_for   = entry.get("used_for", []),
                related    = entry.get("related", []),
                contexts   = entry.get("contexts", []),
                source_kb  = "glossary",
            )
            rec.pattern = _build_pattern(acronym, rec.full_name)
            kb._classify_entity(rec)
            kb.entities[acronym] = rec


def _load_ontology_methods(kb: KnowledgeBase, path: Path):
    blocks = _parse_json_file(path)
    for block in blocks:
        # Resolve the methods list, handling the double-nesting bug:
        # Malformed: {"methods": {"ontology": "...", "methods": [...]}}
        # Correct:   {"methods": [{...}, {...}]}
        methods_val = block.get("methods")
        if isinstance(methods_val, dict):
            # double-nesting bug — unwrap inner dict
            inner = methods_val.get("methods")
            if isinstance(inner, list):
                log.warning(
                    "ontology_methods.json: detected double-nesting bug "
                    "({'methods': {'methods': [...]}}) — unwrapping inner list. "
                    "Fix the source file to use {'methods': [...]} directly."
                )
                methods_list = inner
            else:
                methods_list = []
        elif isinstance(methods_val, list):
            methods_list = methods_val
        else:
            methods_list = None

        if methods_list is not None:
            for m in methods_list:
                if not isinstance(m, dict):
                    continue
                aliases = [a for a in m.get("aliases", []) if isinstance(a, str)]
                rec = MethodRecord(
                    method_name = m.get("method_name", m.get("name", "")),
                    aliases     = aliases,
                    type        = m.get("type", ""),
                    domain      = m.get("domain", ""),
                    subdomain   = m.get("subdomain", ""),
                    description = m.get("description", ""),
                    inputs      = m.get("primary_inputs", m.get("inputs", m.get("uses_data", []))),
                    outputs     = m.get("output_variables", m.get("outputs", m.get("predicts", []))),
                    related     = m.get("related_methods", m.get("related", [])),
                    source_kb   = "ontology",
                )
                parts = [fr"\b{re.escape(aliases[0])}\b"] if aliases else []
                nm = rec.method_name.replace("_", " ")
                if len(nm) > 3:
                    parts.append(re.escape(nm.lower()))
                rec.pattern = "|".join(parts) if parts else None
                kb.methods.append(rec)
            continue

        # block with {source, target, relation}: skip (graph edges)
        if "source" in block and "target" in block:
            continue

        # flat method dict (bfe-style orphan blocks)
        for key, val in block.items():
            if isinstance(val, dict) and "type" in val:
                if val.get("type") in ("method", "model"):
                    rec = MethodRecord(
                        method_name = key,
                        aliases     = val.get("aliases", []),
                        type        = val.get("type", ""),
                        domain      = val.get("domain", ""),
                        subdomain   = val.get("subdomain", ""),
                        description = val.get("description", ""),
                        inputs      = val.get("used_for", []),
                        outputs     = val.get("outputs", []),
                        related     = val.get("related", []),
                        source_kb   = "ontology_flat",
                    )
                    kb.methods.append(rec)


def _load_bfe_methods(kb: KnowledgeBase, path: Path):
    blocks = _parse_json_file(path)
    for block in blocks:
        for key, val in block.items():
            if isinstance(val, dict):
                kb.bfe_methods[key] = val


def _load_floods_satelite(kb: KnowledgeBase, path: Path):
    blocks = _parse_json_file(path)
    if blocks:
        kb.floods = blocks[0]


def _load_rs_water_resources(kb: KnowledgeBase, path: Path):
    blocks = _parse_json_file(path)
    if blocks:
        raw = blocks[0]
        kb.rs_domains = raw.get("domains", raw)


def _load_v2_ontology(kb: KnowledgeBase, path: Path):
    """
    Load flood_modeling_ontology_v2.json into the v2_* dicts and extend
    the methods list with fully-enriched MethodRecord entries.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        log.error("Failed to parse v2 ontology (%s): %s", path, exc)
        return

    # ── metrics ───────────────────────────────────────────────────────────────
    for mid, mdata in data.get("evaluation_metrics", {}).items():
        kb.v2_metrics[mid] = MetricRecord(
            id        = mid,
            name      = mdata.get("name", mid),
            full_name = mdata.get("full_name", ""),
            optimal   = float(mdata.get("optimal", 0)),
            range     = str(mdata.get("range", "")),
            scale     = mdata.get("scale"),
            applicable_to        = mdata.get("applicable_to", []),
            threshold_acceptable = mdata.get("threshold_acceptable"),
        )

    # ── tasks ─────────────────────────────────────────────────────────────────
    for tid, tdata in data.get("task_types", {}).items():
        kb.v2_tasks[tid] = TaskRecord(
            id     = tid,
            name   = tdata.get("name", tid),
            label  = tdata.get("label", tdata.get("name", tid)),
            domain = tdata.get("domain", "hydrology"),
            requires_DEM           = tdata.get("requires_DEM"),
            requires_precipitation = tdata.get("requires_precipitation"),
            requires_real_time     = tdata.get("requires_real_time"),
            output = tdata.get("output"),
        )

    # ── equations ────────────────────────────────────────────────────────────
    for eid, edata in data.get("equations", {}).items():
        kb.v2_equations[eid] = EquationRecord(
            id        = eid,
            name      = edata.get("name", eid),
            full_name = edata.get("full_name", ""),
            type      = edata.get("type", ""),
            conserves = edata.get("conserves", []),
            used_in   = edata.get("used_in", []),
        )

    # ── uncertainty concepts ──────────────────────────────────────────────────
    for uid, udata in data.get("uncertainty_concepts", {}).items():
        kb.v2_uncertainties[uid] = UncertaintyRecord(
            id      = uid,
            name    = udata.get("name", uid),
            source  = udata.get("source", ""),
            affects = udata.get("affects", []),
        )

    # ── models ────────────────────────────────────────────────────────────────
    for model_entry in data.get("models", []):
        if not isinstance(model_entry, dict):
            continue
        mid = model_entry.get("id", "")
        if not mid:
            continue

        # v1-compat field mapping: loader reads primary_inputs → inputs etc.
        inputs  = model_entry.get("primary_inputs", model_entry.get("inputs", []))
        outputs = model_entry.get("output_variables", model_entry.get("outputs", []))
        related = model_entry.get("related_methods", model_entry.get("related_models", []))

        aliases = [a for a in model_entry.get("aliases", []) if isinstance(a, str)]

        mrec = MethodRecord(
            method_name = model_entry.get("method_name", model_entry.get("name", mid)),
            aliases     = aliases,
            type        = model_entry.get("type", model_entry.get("category", "")),
            domain      = model_entry.get("domain", "hydrology"),
            subdomain   = model_entry.get("subdomain", ""),
            description = model_entry.get("description", ""),
            inputs      = inputs,
            outputs     = outputs,
            related     = related,
            source_kb   = "ontology_v2",
            # v2 semantic fields
            category    = model_entry.get("category"),
            subcategory = model_entry.get("subcategory"),
            dimension   = model_entry.get("dimension"),
            physics_based  = model_entry.get("physics_based"),
            data_driven    = model_entry.get("data_driven"),
            evaluation_metrics             = model_entry.get("evaluation_metrics", []),
            coupled_with                   = model_entry.get("coupled_with", []),
            equations                      = model_entry.get("equations", []),
            requires_calibration           = model_entry.get("requires_calibration"),
            supports_real_time_forecasting = model_entry.get("supports_real_time_forecasting"),
            supports_ungauged_basins       = model_entry.get("supports_ungauged_basins"),
            strengths         = model_entry.get("strengths", []),
            limitations       = model_entry.get("limitations", []),
            tasks             = model_entry.get("tasks", []),
            typical_use_cases = model_entry.get("typical_use_cases", []),
        )

        # Build regex pattern from name + aliases
        name_str = mrec.method_name.replace("_", " ")
        parts: list[str] = []
        if aliases:
            parts.append(fr"\b{re.escape(aliases[0])}\b")
        if len(name_str) > 3:
            parts.append(re.escape(name_str.lower()))
        mrec.pattern = "|".join(parts) if parts else None

        kb.v2_models[mid] = mrec
        # also add to the flat methods list so entity_extractor picks them up
        kb.methods.append(mrec)

    log.info(
        "v2 ontology loaded: %d models, %d metrics, %d tasks, %d equations, %d uncertainty types",
        len(kb.v2_models),
        len(kb.v2_metrics),
        len(kb.v2_tasks),
        len(kb.v2_equations),
        len(kb.v2_uncertainties),
    )


def _load_disambiguation_rules(kb: KnowledgeBase, path: Path) -> None:
    """Load ontology_disambiguation_rules.json into kb.disambiguation_rules."""
    data = json.loads(path.read_text(encoding="utf-8"))
    rules = data.get("rules", [])
    kb.disambiguation_rules.extend(rules)
    log.debug("Loaded %d disambiguation rules from %s", len(rules), path.name)


# ─── public factory ───────────────────────────────────────────────────────────

def load_knowledge_base(data_dir: Path | str | None = None) -> KnowledgeBase:
    """
    Load and merge all knowledge bases.

    Args:
        data_dir: Path to the data directory. Defaults to project data/.

    Returns:
        Populated KnowledgeBase instance.
    """
    data_dir = Path(data_dir) if data_dir else _DATA_DIR
    kb = KnowledgeBase()

    files = {
        "glossary":       data_dir / "glossary_acronyms.json",
        "ontology":       data_dir / "ontology_methods.json",
        "bfe":            data_dir / "bfe_methods.json",
        "floods":         data_dir / "floods_satelite.json",
        "rs_water":       data_dir / "remote_sensing_water_resources.json",
        "v2":             data_dir / "flood_modeling_ontology_v2.json",
        "disambiguation": data_dir / "ontology_disambiguation_rules.json",
    }

    for name, fpath in files.items():
        if not fpath.exists():
            log.warning("Knowledge base file not found: %s", fpath)
            continue
        try:
            if name == "glossary":
                _load_glossary(kb, fpath)
            elif name == "ontology":
                _load_ontology_methods(kb, fpath)
            elif name == "bfe":
                _load_bfe_methods(kb, fpath)
            elif name == "floods":
                _load_floods_satelite(kb, fpath)
            elif name == "rs_water":
                _load_rs_water_resources(kb, fpath)
            elif name == "v2":
                _load_v2_ontology(kb, fpath)
            elif name == "disambiguation":
                _load_disambiguation_rules(kb, fpath)
        except Exception as exc:
            log.error("Failed to load %s (%s): %s", name, fpath, exc)

    kb._register_aliases()
    kb._build_method_index()

    # ── cross-populate: add floods satellite names as satellite entities ──
    sat_names = []
    if kb.floods:
        for sat_type, sat_data in kb.floods.get("data_sources", {}).get("satellites", {}).items():
            sat_names.extend(sat_data.get("examples", []))
    for name in sat_names:
        key = name.upper()
        if key not in kb.entities:
            rec = EntityRecord(
                acronym    = key,
                full_name  = name,
                type       = "sensor",
                type_group = "sensor",
                domain     = "remote_sensing",
                definition = "Satellite sensor used for flood and Earth observation.",
                source_kb  = "floods_satelite",
            )
            rec.pattern = _build_pattern(key, name)
            rec.is_satellite = True
            kb.entities[key] = rec

    log.info(
        "KB loaded: %d entities (%d satellites, %d DEMs, %d methods, %d metrics), "
        "%d method records (%d v2 models), %d BFE methods",
        len(kb.entities),
        len(kb.get_satellites()),
        len(kb.get_dems()),
        len(kb.get_methods()),
        len(kb.get_metrics()),
        len(kb.methods),
        len(kb.v2_models),
        len(kb.bfe_methods),
    )
    return kb


@lru_cache(maxsize=1)
def get_global_kb() -> KnowledgeBase:
    """Singleton KB — loaded once per process."""
    return load_knowledge_base()
