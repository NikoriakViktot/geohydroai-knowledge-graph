"""
merge_ontology.py  —  GeoHydroAI canonical ontology registry builder
=====================================================================

Reads 6 source ontology files, merges them into one canonical registry,
and writes 7 output files to data/ontology/.

Canonical identity is DETERMINISTIC — based on normalized text keys,
never on embeddings or clustering.

Run:
    python -m src.ontology.merge_ontology
"""

from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
OUT  = DATA / "ontology"


# ─────────────────────────────────────────────────────────────────────────────
# Type → (id_prefix, output_file_key)
# ─────────────────────────────────────────────────────────────────────────────

_TYPE_MAP: dict[str, tuple[str, str]] = {
    "method":           ("method",    "methods"),
    "process":          ("method",    "methods"),
    "model":            ("method",    "methods"),
    "software":         ("method",    "methods"),
    "sensor":           ("sensor",    "sensors"),
    "satellite":        ("sensor",    "sensors"),
    "satellite_mission":("sensor",    "sensors"),
    "metric":           ("metric",    "metrics"),
    "data":             ("data",      "data_sources"),
    "index":            ("data",      "data_sources"),
    "dataset":          ("data",      "data_sources"),
    "concept":          ("concept",   "concepts"),
    "scientific_concept":("concept",  "concepts"),
    "parameter":        ("parameter", "concepts"),
    "organization":     ("org",       "concepts"),
    "datum":            ("concept",   "concepts"),
    "academic_field":   ("concept",   "concepts"),
    "spectral_band":    ("concept",   "concepts"),
    "place":            ("concept",   "concepts"),
    "uncertainty":      ("uncertainty","concepts"),
    "system":           ("system",    "concepts"),
    "program":          ("org",       "concepts"),
    "project":          ("concept",   "concepts"),
}

# These raw type values should be normalised to a single canonical value
# for the entity "type" field
_TYPE_NORMALISE: dict[str, str] = {
    "process":           "method",
    "software":          "model",
    "satellite":         "sensor",
    "satellite_mission": "sensor",
    "index":             "data",
    "dataset":           "data",
    "scientific_concept":"concept",
    "datum":             "concept",
    "academic_field":    "concept",
    "spectral_band":     "concept",
    "place":             "concept",
    "program":           "organization",
    "project":           "concept",
}

# Hard override for IDs that would otherwise get wrong prefix
# key = raw entry key (uppercased), value = full canonical id
_ID_OVERRIDES: dict[str, str] = {
    "HEC-RAS":      "method.hec_ras",
    "HEC_RAS":      "method.hec_ras",
    "LISFLOOD":     "method.lisflood_fp",
    "LISFLOOD-FP":  "method.lisflood_fp",
    "LISFLOOD_FP":  "method.lisflood_fp",
    "HEC-HMS":      "method.hec_hms",
    "HEC_HMS":      "method.hec_hms",
    "NDVI":         "data.ndvi",
    "NDWI":         "data.ndwi",
    "MNDWI":        "data.mndwi",
    "NDSI":         "data.ndsi",
    "SRTM":         "data.srtm",
    "FIRM":         "concept.firm",
    "SAR":          "data.sar",
    "DEM":          "data.dem",
    "DTM":          "data.dtm",
    "DSM":          "data.dsm",
    "GIS":          "system.gis",
    "GPS":          "system.gps",
    "GNSS":         "system.gnss",
    "QRF":          "method.quantile_random_forest",
}

# Extra aliases for important entities that should be resolvable from many spellings
_EXTRA_ALIASES: dict[str, list[str]] = {
    "method.random_forest":               ["RF", "Random Forest", "RandomForest", "random forest", "random_forest"],
    "method.hec_ras":                     ["HEC-RAS", "HEC RAS", "hec_ras", "HECRAS", "Hydrologic Engineering Center River Analysis System"],
    "method.lisflood_fp":                 ["LISFLOOD-FP", "LISFLOOD", "lisflood", "LISFLOOD_FP"],
    "method.artificial_neural_network":   ["ANN", "MLP", "Neural Network", "neural network", "ANN model"],
    "method.two_dimensional_hydrodynamic_model": ["2D model", "2D hydraulic model", "shallow water model"],
    "method.one_dimensional_hydraulic_modeling": ["1D model", "1D hydraulic model", "HEC-RAS 1D"],
    "method.sar_thresholding":            ["SAR threshold", "backscatter thresholding", "water thresholding"],
    "method.sar_change_detection":        ["SAR change detection", "multi-temporal SAR", "temporal differencing"],
    "method.object_based_image_analysis": ["OBIA", "GEOBIA", "object based", "object-based"],
    "method.ensemble_kalman_filter":      ["EnKF", "Ensemble Kalman", "ensemble Kalman filter"],
    "method.arima":                       ["ARIMA", "autoregressive model"],
    "method.anfis":                       ["ANFIS", "neuro-fuzzy", "adaptive neuro-fuzzy"],
    "method.support_vector_machine":      ["SVM", "Support Vector Machine", "SVR", "support vector"],
    "method.convolutional_neural_network":["CNN", "ConvNet", "convolutional neural", "deep learning CNN"],
    "method.long_short_term_memory":      ["LSTM", "long short-term memory"],
    "method.u_net":                       ["U-Net", "UNet", "u-net"],
    "method.insar":                       ["InSAR", "INSAR", "SAR interferometry"],
    "sensor.sentinel_1":                  ["Sentinel-1", "S1", "Sentinel 1", "SENTINEL-1", "Sentinel1"],
    "sensor.sentinel_2":                  ["Sentinel-2", "S2", "Sentinel 2", "SENTINEL-2", "Sentinel2"],
    "sensor.modis":                       ["MODIS", "Terra MODIS", "Aqua MODIS"],
    "sensor.alos_palsar":                 ["ALOS PALSAR", "PALSAR", "ALOS-PALSAR", "PALSAR-2"],
    "sensor.landsat_8":                   ["Landsat-8", "Landsat 8", "OLI", "TIRS", "Landsat8"],
    "sensor.landsat_9":                   ["Landsat-9", "Landsat 9", "Landsat9"],
    "sensor.terrasar_x":                  ["TerraSAR-X", "TSX", "TerraSAR X"],
    "sensor.cosmo_skymed":                ["COSMO-SkyMed", "CSK", "COSMO SkyMed"],
    "sensor.radarsat":                    ["RADARSAT", "RADARSAT-2", "RADARSAT 2"],
    "sensor.iceye":                       ["ICEYE"],
    "sensor.grace":                       ["GRACE", "GRACE-FO", "GRACE FO"],
    "sensor.gpm":                         ["GPM", "IMERG", "Global Precipitation Measurement"],
    "sensor.trmm":                        ["TRMM", "TRMM 3B42", "Tropical Rainfall Measuring Mission"],
    "sensor.smap":                        ["SMAP", "Soil Moisture Active Passive"],
    "data.dem":                           ["DEM", "Digital Elevation Model", "SRTM DEM", "ALOS DEM", "elevation model"],
    "data.srtm":                          ["SRTM", "SRTM30", "SRTM3", "Shuttle Radar Topography Mission"],
    "data.sar":                           ["SAR imagery", "SAR image", "SAR data", "radar imagery"],
    "data.ndvi":                          ["NDVI", "Normalized Difference Vegetation Index"],
    "data.ndwi":                          ["NDWI", "Normalized Difference Water Index"],
    "metric.rmse":                        ["RMSE", "Root Mean Square Error", "Root Mean Squared Error"],
    "metric.mae":                         ["MAE", "Mean Absolute Error"],
    "metric.nse":                         ["NSE", "Nash-Sutcliffe Efficiency", "Nash-Sutcliffe"],
    "metric.f1_score":                    ["F1", "F1-score", "F1 score", "F-measure", "Dice score"],
    "metric.iou":                         ["IoU", "IOU", "Intersection over Union", "Jaccard index"],
    "metric.kappa":                       ["Kappa", "Cohen's Kappa", "Kappa coefficient", "kappa statistic"],
    "metric.overall_accuracy":            ["OA", "Overall Accuracy", "overall accuracy"],
    "metric.r_squared":                   ["R2", "R²", "coefficient of determination"],
    "metric.pod":                         ["POD", "Probability of Detection", "hit rate", "recall"],
    "metric.far":                         ["FAR", "False Alarm Rate", "false alarm ratio"],
    "concept.base_flood_elevation":       ["BFE", "base flood elevation", "Base Flood Elevation"],
    "concept.flood_extent":               ["flood extent", "inundation extent", "flood boundary"],
    "concept.flood_susceptibility":       ["flood susceptibility", "flood vulnerability"],
    "concept.firm":                       ["FIRM", "Flood Insurance Rate Map", "flood insurance rate map"],
    "concept.floodway":                   ["floodway", "regulatory floodway"],
    "concept.sfha":                       ["SFHA", "Special Flood Hazard Area", "special flood hazard area"],
    "org.fema":                           ["FEMA", "Federal Emergency Management Agency"],
    "org.usgs":                           ["USGS", "United States Geological Survey"],
    "org.nasa":                           ["NASA", "National Aeronautics and Space Administration"],
    "org.noaa":                           ["NOAA", "National Oceanic and Atmospheric Administration"],
    "org.wmo":                            ["WMO", "World Meteorological Organization"],
    "org.esa":                            ["ESA", "European Space Agency"],
    "org.usace":                          ["USACE", "US Army Corps of Engineers", "Army Corps"],
    # v2 ontology additions
    "method.hec_ras_1d":                  ["HEC-RAS 1D", "HEC-RAS-1D", "HEC RAS 1D", "1D HEC-RAS", "hec_ras_1d"],
    "method.hec_ras_2d":                  ["HEC-RAS 2D", "HEC-RAS-2D", "HEC RAS 2D", "2D HEC-RAS", "hec_ras_2d"],
    "method.hec_ras_1d_2d":               ["HEC-RAS 1D-2D", "HEC-RAS-1D-2D", "HEC-RAS coupled"],
    "method.hec_hms":                     ["HEC-HMS", "HEC HMS", "hec_hms", "Hydrologic Modeling System"],
    "method.mike_11":                     ["MIKE 11", "MIKE-11", "MIKE11", "mike_11"],
    "method.mike_21":                     ["MIKE 21", "MIKE-21", "MIKE21", "mike_21"],
    "method.mike_flood":                  ["MIKE FLOOD", "MIKE-FLOOD", "MIKEFLOOD", "mike_flood"],
    "method.mike_hydro":                  ["MIKE HYDRO", "MIKE-HYDRO", "MIKEHYDRO", "mike_hydro"],
    "method.lisflood_fp":                 ["LISFLOOD-FP", "LISFLOOD FP", "LISFLOOD", "lisflood_fp"],
    "method.telemac_mascaret":            ["TELEMAC-MASCARET", "TELEMAC MASCARET", "TELEMAC", "telemac"],
    "method.iric":                        ["iRIC", "IRIC", "i-RIC", "iric"],
    "method.rri":                         ["RRI", "Rainfall-Runoff-Inundation", "RRI model", "rri"],
    "method.swmm":                        ["SWMM", "Storm Water Management Model", "swmm"],
    "method.ihacres":                     ["IHACRES", "ihacres", "Identification of Hydrographs"],
    "method.sarima":                      ["SARIMA", "seasonal ARIMA", "Seasonal ARIMA", "sarima"],
    "method.holt_winters":                ["Holt-Winters", "Holt Winters", "Triple exponential smoothing"],
    "method.prophet":                     ["Prophet", "Facebook Prophet", "prophet"],
    "method.lstm":                        ["LSTM", "Long Short-Term Memory", "lstm"],
    "method.adaline":                     ["ADALINE", "adaline", "Adaptive Linear Neuron"],
    "method.elman":                       ["ELMAN", "elman", "Elman network"],
    "method.wa_lstm":                     ["WA-LSTM", "Wavelet-LSTM", "wavelet LSTM", "wa_lstm"],
    "method.wann":                        ["WANN", "Wavelet ANN", "wavelet ANN", "wann"],
    "method.sarima_ann":                  ["SARIMA-ANN", "SARIMA-ANN hybrid", "sarima_ann"],
    "method.sarima_prophet":              ["SARIMA-Prophet", "SARIMA Prophet hybrid", "sarima_prophet"],
    "method.wavelet_svr_prophet":         ["Wavelet-SVR-Prophet", "wavelet SVR Prophet"],
    "method.mlp":                         ["MLP", "multi-layer perceptron", "Multi-Layer Perceptron"],
    "method.bagging_lmt":                 ["Bagging-LMT", "Bagging LMT", "BaggingLMT"],
    "method.random_forest":               ["Random-Forest", "Random Forest", "RandomForest", "RF"],
    "method.svr":                         ["SVR", "Support Vector Regression"],
    "method.scs_cn":                      ["SCS-CN", "SCS CN", "Curve Number", "CN method"],
    "method.mamdani_fis":                 ["Mamdani-FIS", "Mamdani FIS", "Mamdani fuzzy", "mamdani"],
    "method.takagi_sugeno_fis":           ["Takagi-Sugeno-FIS", "Takagi-Sugeno", "TSK", "Sugeno FIS"],
    "method.larsen_fis":                  ["Larsen-FIS", "Larsen FIS", "Larsen fuzzy"],
    "method.wec_flood":                   ["WEC-FLOOD", "WEC FLOOD", "wec_flood"],
    "method.floodmap":                    ["FloodMap", "FLOODMAP", "Flood Map"],
    "metric.nse":                         ["NSE", "Nash-Sutcliffe Efficiency", "Nash-Sutcliffe"],
    "metric.kge":                         ["KGE", "Kling-Gupta Efficiency", "Kling Gupta"],
    "metric.pbias":                       ["PBIAS", "Percent Bias", "percent bias"],
    "metric.rsr":                         ["RSR", "RMSE-observations standard deviation Ratio"],
    "metric.csi":                         ["CSI", "Critical Success Index", "threat score"],
    "metric.ioa":                         ["IoA", "IOA", "Index of Agreement"],
    "metric.mape":                        ["MAPE", "Mean Absolute Percentage Error"],
    "metric.auc":                         ["AUC", "AUC-ROC", "area under curve", "ROC-AUC"],
    "metric.kappa":                       ["Kappa", "Cohen's Kappa", "kappa coefficient"],
}


# ─────────────────────────────────────────────────────────────────────────────
# ID generation
# ─────────────────────────────────────────────────────────────────────────────

def _slugify(text: str) -> str:
    t = text.lower().strip()
    t = re.sub(r"[^a-z0-9]+", "_", t)
    t = re.sub(r"_+", "_", t).strip("_")
    return t


def _make_id(raw_key: str, raw_type: str) -> str:
    upper_key = raw_key.upper().replace(" ", "_")
    if upper_key in _ID_OVERRIDES:
        return _ID_OVERRIDES[upper_key]
    also_raw = raw_key.upper()
    if also_raw in _ID_OVERRIDES:
        return _ID_OVERRIDES[also_raw]

    prefix, _ = _TYPE_MAP.get(raw_type, ("concept", "concepts"))
    slug      = _slugify(raw_key)
    return f"{prefix}.{slug}"


def _norm_type(raw_type: str) -> str:
    return _TYPE_NORMALISE.get(raw_type, raw_type)


# ─────────────────────────────────────────────────────────────────────────────
# Canonical entity skeleton
# ─────────────────────────────────────────────────────────────────────────────

def _empty_entity(entity_id: str, display_name: str, raw_type: str, domain: str, source: str) -> dict:
    return {
        "id":           entity_id,
        "display_name": display_name,
        "type":         _norm_type(raw_type),
        "type_group":   _TYPE_MAP.get(raw_type, ("concept", "concepts"))[1].rstrip("s"),
        "domain":       domain,
        "contexts":     [],
        "aliases":      [],
        "definition":   "",
        "used_for":     [],
        "inputs":       [],
        "outputs":      [],
        "limitations":  [],
        "related":      [],
        "source_files": [source],
    }


# ─────────────────────────────────────────────────────────────────────────────
# Merge helpers
# ─────────────────────────────────────────────────────────────────────────────

def _prefer_longer(a: str, b: str) -> str:
    return a if len(a) >= len(b) else b


def _merge_lists(a: list, b: list) -> list:
    seen = set()
    out  = []
    for item in a + b:
        key = str(item).lower().strip()
        if key not in seen:
            seen.add(key)
            out.append(item)
    return out


def _merge_into(target: dict, patch: dict) -> None:
    """Merge patch into target in-place, following ontology merge rules."""
    if patch.get("definition"):
        target["definition"] = _prefer_longer(
            target.get("definition", ""), patch["definition"]
        )
    for list_field in ("used_for", "inputs", "outputs", "limitations",
                       "related", "contexts", "aliases"):
        target[list_field] = _merge_lists(
            target.get(list_field, []), patch.get(list_field, [])
        )
    if patch.get("domain") and not target.get("domain"):
        target["domain"] = patch["domain"]
    for src in patch.get("source_files", []):
        if src not in target["source_files"]:
            target["source_files"].append(src)


# ─────────────────────────────────────────────────────────────────────────────
# Registry accumulator
# ─────────────────────────────────────────────────────────────────────────────

class Registry:
    def __init__(self) -> None:
        self._entities: dict[str, dict] = {}

    def upsert(self, entity: dict) -> None:
        eid = entity["id"]
        if eid not in self._entities:
            self._entities[eid] = entity
        else:
            _merge_into(self._entities[eid], entity)

    def add_alias(self, entity_id: str, alias: str) -> None:
        if entity_id in self._entities:
            ent = self._entities[entity_id]
            if alias not in ent["aliases"]:
                ent["aliases"].append(alias)

    def all_entities(self) -> list[dict]:
        return list(self._entities.values())

    def by_file_key(self) -> dict[str, list[dict]]:
        """Partition using the canonical ID prefix — ground truth over the type field."""
        _PREFIX_TO_FILE = {
            "method":      "methods",
            "sensor":      "sensors",
            "metric":      "metrics",
            "data":        "data_sources",
            "concept":     "concepts",
            "org":         "concepts",
            "system":      "concepts",
            "parameter":   "concepts",
            "uncertainty": "concepts",
        }
        # Also normalise entity type to match prefix so downstream is consistent
        _PREFIX_TO_TYPE = {
            "method":      "method",
            "sensor":      "sensor",
            "metric":      "metric",
            "data":        "data",
            "concept":     "concept",
            "org":         "organization",
            "system":      "system",
            "parameter":   "parameter",
            "uncertainty": "uncertainty",
        }

        groups: dict[str, list[dict]] = defaultdict(list)
        for ent in self._entities.values():
            prefix   = ent["id"].split(".")[0]
            file_key = _PREFIX_TO_FILE.get(prefix, "concepts")
            # Correct the type field to match the canonical prefix
            correct_type = _PREFIX_TO_TYPE.get(prefix)
            if correct_type:
                ent["type"] = correct_type
            groups[file_key].append(ent)
        return dict(groups)


# ─────────────────────────────────────────────────────────────────────────────
# Source file parsers
# ─────────────────────────────────────────────────────────────────────────────

def _parse_glossary(data: dict, registry: Registry, src: str) -> None:
    """Process glossary_acronyms.json — already in near-canonical form."""
    for key, entry in data.items():
        raw_type    = entry.get("type", "concept")
        display_name = entry.get("full_name", key.replace("_", " ").title())
        domain      = entry.get("domain", "")
        eid         = _make_id(key, raw_type)

        ent = _empty_entity(eid, display_name, raw_type, domain, src)
        ent["definition"]  = entry.get("definition", "")
        ent["used_for"]    = list(entry.get("used_for", []))
        ent["related"]     = [str(r) for r in entry.get("related", [])]
        ent["contexts"]    = list(entry.get("contexts", []))
        ent["limitations"] = list(entry.get("limitations", []))

        # Add the abbreviation key itself as an alias if it looks like an acronym
        if key.upper() == key and key != display_name:
            ent["aliases"].append(key)

        registry.upsert(ent)


def _parse_bfe_methods(data: dict, registry: Registry, src: str) -> None:
    """Process bfe_methods.json — two sections: bfe_estimation_methods and core_concepts_and_acronyms."""
    modules = data.get("modules", {})

    # --- BFE estimation methods ---
    for key, entry in modules.get("bfe_estimation_methods", {}).items():
        raw_type = entry.get("type", "method")
        eid      = _make_id(key, raw_type)
        display  = key.replace("_", " ").title()

        ent = _empty_entity(eid, display, raw_type, "flood_mapping", src)
        ent["definition"]  = entry.get("definition", "")
        ent["used_for"]    = list(entry.get("used_for", []))
        ent["inputs"]      = list(entry.get("inputs", []))
        ent["limitations"] = list(entry.get("limitations", []))
        ent["related"]     = list(entry.get("related", []))
        ent["contexts"]    = [entry.get("subdomain", "BFE_estimation")]

        registry.upsert(ent)

    # --- Core concepts and acronyms ---
    for key, entry in modules.get("core_concepts_and_acronyms", {}).items():
        raw_type = entry.get("type", "concept")
        display  = entry.get("full_name", key.upper())
        domain   = entry.get("domain", "")
        eid      = _make_id(key, raw_type)

        ent = _empty_entity(eid, display, raw_type, domain, src)
        ent["definition"]  = entry.get("definition", "")
        ent["used_for"]    = list(entry.get("used_for", []))
        ent["related"]     = [str(r) for r in entry.get("related", [])]

        if key.upper() == key:
            ent["aliases"].append(key)

        registry.upsert(ent)


def _parse_ontology_methods(data: dict, registry: Registry, src: str) -> None:
    """Process ontology_methods.json — nested methods list + data dict."""
    inner = data.get("methods", data)  # handle double-nesting
    if isinstance(inner, dict) and "methods" in inner:
        inner = inner

    methods_list = inner.get("methods", [])
    for entry in methods_list:
        name     = entry.get("name", "")
        raw_type = entry.get("type", "method")
        # map ontology type to our taxonomy
        if raw_type in ("ml", "statistical", "hydrological",
                        "hydraulic", "remote_sensing", "data_assimilation",
                        "time_series"):
            raw_type = "method"
        eid      = _make_id(name, raw_type)
        display  = name.replace("_", " ").title()

        ent = _empty_entity(eid, display, raw_type, "hydrology", src)
        ent["definition"]  = entry.get("description", "")
        ent["used_for"]    = list(entry.get("use_cases", []))
        ent["inputs"]      = list(entry.get("inputs", []))
        ent["outputs"]     = list(entry.get("outputs", []))
        ent["limitations"] = list(entry.get("limitations", []))
        ent["related"]     = list(entry.get("related", []))
        ent["aliases"]     = list(entry.get("aliases", []))

        registry.upsert(ent)

    # data section
    for key, dentry in inner.get("data", {}).items():
        raw_type = dentry.get("type", "data")
        if raw_type == "sensor":
            raw_type = "data"      # SAR as data product here
        if raw_type == "index":
            raw_type = "data"
        eid      = _make_id(key, raw_type)
        display  = key.upper()

        ent = _empty_entity(eid, display, raw_type, "hydrology", src)
        ent["used_for"] = list(dentry.get("used_for", []))
        registry.upsert(ent)


def _parse_floods_satelite(data: dict, registry: Registry, src: str) -> None:
    """Process floods_satelite.json — SAR sensors, flood detection methods, models."""

    # --- SAR and optical satellite categories ---
    for sat_type, sdef in data.get("data_sources", {}).get("satellites", {}).items():
        if sat_type == "SAR":
            for ex in sdef.get("examples", []):
                eid = _make_id(ex, "sensor")
                ent = _empty_entity(eid, ex, "sensor", "remote_sensing", src)
                ent["used_for"]  = list(sdef.get("used_for", []))
                ent["aliases"]   = [ex]
                ent["contexts"]  = ["flood_mapping"]
                registry.upsert(ent)
        elif sat_type == "optical":
            for ex in sdef.get("examples", []):
                eid = _make_id(ex, "sensor")
                ent = _empty_entity(eid, ex, "sensor", "remote_sensing", src)
                ent["used_for"]   = list(sdef.get("used_for", []))
                ent["limitations"]= list(sdef.get("limitations", []))
                ent["aliases"]    = [ex]
                registry.upsert(ent)
        elif sat_type == "precipitation":
            for ex in sdef.get("examples", []):
                eid = _make_id(ex, "sensor")
                ent = _empty_entity(eid, ex, "sensor", "hydrology", src)
                ent["used_for"] = list(sdef.get("used_for", []))
                registry.upsert(ent)

    # terrain DEM
    terrain = data.get("data_sources", {}).get("terrain", {})
    for ex in terrain.get("DEM", []):
        eid = _make_id(ex, "data")
        ent = _empty_entity(eid, ex, "data", "terrain_analysis", src)
        ent["used_for"] = list(terrain.get("used_for", []))
        registry.upsert(ent)

    # --- hydrological / monitoring models ---
    for _section, mdict in data.get("models", {}).items():
        for mkey, mdef in mdict.items():
            raw_type = mdef.get("type", "model")
            eid      = _make_id(mkey, "method")
            display  = mdef.get("full_name", mkey)
            ent      = _empty_entity(eid, display, "model", "hydrology", src)
            ent["used_for"] = list(mdef.get("used_for", []))
            ent["aliases"]  = [mkey]
            registry.upsert(ent)

    # --- flood detection methods ---
    for mkey, mdef in data.get("flood_detection", {}).get("methods", {}).items():
        eid = _make_id(mkey, "method")
        ent = _empty_entity(eid, mkey.replace("_", " ").title(), "method", "flood_mapping", src)
        ent["inputs"]   = list(mdef.get("input", []))
        ent["used_for"] = ["flood_detection"]
        ent["contexts"] = ["flood_mapping"]
        registry.upsert(ent)

    # --- SAR physics concepts ---
    for mech, mdef in data.get("sar_physics", {}).get("backscatter_mechanisms", {}).items():
        eid = _make_id(f"sar_{mech}", "concept")
        ent = _empty_entity(eid, mech.replace("_", " ").title(), "concept", "remote_sensing", src)
        ent["contexts"] = list(mdef.get("occurs_in", []))
        registry.upsert(ent)

    # SAR polarization as parameter
    pol = data.get("sar_physics", {}).get("parameters", {})
    for p in pol.get("polarization", []):
        eid = _make_id(f"sar_polarization_{p.lower()}", "parameter")
        ent = _empty_entity(eid, f"SAR Polarization {p}", "parameter", "remote_sensing", src)
        ent["contexts"] = ["sar_physics"]
        registry.upsert(ent)

    # --- Classification classes ---
    for code, label in data.get("classification_classes", {}).items():
        eid = _make_id(f"flood_class_{code.lower()}", "concept")
        ent = _empty_entity(eid, label, "concept", "flood_mapping", src)
        ent["aliases"]  = [code]
        ent["contexts"] = ["flood_classification"]
        registry.upsert(ent)


def _parse_node_methods(data: dict, registry: Registry, src: str) -> None:
    """Process node_methods.json — graph schema + sample nodes."""
    type_map = {"Method": "method", "Data": "data", "Metric": "metric",
                "Uncertainty": "uncertainty"}

    for node in data.get("nodes", []):
        nid      = node["id"]
        raw_type = type_map.get(node["type"], "concept")
        eid      = _make_id(nid, raw_type)
        display  = nid.replace("_", " ").title()

        ent = _empty_entity(eid, display, raw_type, "hydrology", src)
        ent["aliases"] = [nid]
        registry.upsert(ent)

    # Encode graph edges as `related` + semantic hints
    id_map: dict[str, str] = {}
    for node in data.get("nodes", []):
        raw_type = type_map.get(node["type"], "concept")
        id_map[node["id"]] = _make_id(node["id"], raw_type)

    for edge in data.get("edges", []):
        src_id = id_map.get(edge["source"])
        tgt_id = id_map.get(edge["target"])
        if src_id and tgt_id and src_id in registry._entities:
            ent = registry._entities[src_id]
            rel = f"{edge['type']}:{tgt_id}"
            if rel not in ent.get("related", []):
                ent.setdefault("related", []).append(rel)


def _parse_v2_ontology(data: dict, registry: Registry, src: str) -> None:
    """
    Process flood_modeling_ontology_v2.json — full semantic ontology with 46 models,
    metrics, tasks, equations, uncertainty types.
    """
    # ── models ────────────────────────────────────────────────────────────────
    for model_entry in data.get("models", []):
        if not isinstance(model_entry, dict):
            continue
        mid_raw   = model_entry.get("id", "")
        name_disp = model_entry.get("name", mid_raw.replace("_", " ").title())
        domain    = model_entry.get("domain", "hydrology")
        category  = model_entry.get("category", "")
        # map v2 category to our type taxonomy
        raw_type  = "model" if category in (
            "physical_based", "time_series", "machine_learning", "hybrid",
            "fuzzy_logic", "data_assimilation", "remote_sensing", "empirical", "statistical",
        ) else "method"

        eid = f"method.{_slugify(mid_raw)}" if mid_raw else _make_id(name_disp, raw_type)

        ent = _empty_entity(eid, name_disp, raw_type, domain, src)
        ent["definition"]  = model_entry.get("description", "")
        ent["inputs"]      = list(model_entry.get("primary_inputs", model_entry.get("inputs", [])))
        ent["outputs"]     = list(model_entry.get("output_variables", model_entry.get("outputs", [])))
        ent["limitations"] = list(model_entry.get("limitations", []))
        ent["related"]     = list(model_entry.get("related_methods", model_entry.get("related_models", [])))
        ent["aliases"]     = [a for a in model_entry.get("aliases", []) if isinstance(a, str)]
        ent["contexts"]    = list(model_entry.get("tasks", []))
        ent["used_for"]    = list(model_entry.get("typical_use_cases", []))
        # carry v2 semantic fields
        ent["category"]    = category
        ent["subcategory"] = model_entry.get("subcategory")
        ent["dimension"]   = model_entry.get("dimension")

        registry.upsert(ent)

    # ── evaluation metrics ────────────────────────────────────────────────────
    for mid, mdata in data.get("evaluation_metrics", {}).items():
        eid   = f"metric.{_slugify(mid)}"
        disp  = mdata.get("name", mid.upper())
        ent   = _empty_entity(eid, disp, "metric", "hydrology", src)
        ent["definition"] = mdata.get("full_name", "")
        ent["aliases"]    = [mdata.get("name", mid)]
        ent["used_for"]   = list(mdata.get("applicable_to", []))
        registry.upsert(ent)

    # ── tasks as concept nodes ────────────────────────────────────────────────
    for tid, tdata in data.get("task_types", {}).items():
        eid  = f"concept.task_{_slugify(tid)}"
        disp = tdata.get("label", tdata.get("name", tid.replace("_", " ").title()))
        ent  = _empty_entity(eid, disp, "concept", tdata.get("domain", "hydrology"), src)
        ent["definition"] = f"Scientific task: {disp}"
        ent["aliases"]    = [tdata.get("name", tid)]
        registry.upsert(ent)


def _parse_remote_sensing(data: dict, registry: Registry, src: str) -> None:
    """Process remote_sensing_water_resources.json — hydro/RS pipeline entries."""
    domains = data.get("domains", {})

    # Hydrology domain sensors and methods
    for param_key, pdef in domains.get("hydrology", {}).get("parameters", {}).items():
        # sensors
        for sensor_name in pdef.get("sensors", pdef.get("sources", [])):
            eid = _make_id(sensor_name, "sensor")
            ent = _empty_entity(eid, sensor_name, "sensor", "hydrology", src)
            ent["used_for"] = [param_key]
            ent["aliases"]  = [sensor_name]
            registry.upsert(ent)
        # ET models as methods
        for mname in pdef.get("models", []):
            eid = _make_id(mname, "method")
            ent = _empty_entity(eid, mname, "method", "hydrology", src)
            ent["used_for"] = [param_key]
            ent["aliases"]  = [mname]
            registry.upsert(ent)
        # methods
        for mname in pdef.get("methods", []):
            eid = _make_id(mname, "method")
            ent = _empty_entity(eid, mname.replace("_", " ").title(), "method", "hydrology", src)
            ent["used_for"] = [param_key]
            registry.upsert(ent)

    # terrain_analysis derived parameters
    for derived in domains.get("terrain_analysis", {}).get("derived", []):
        eid = _make_id(derived, "data")
        ent = _empty_entity(eid, derived.upper(), "data", "terrain_analysis", src)
        ent["used_for"] = ["hydrological_modeling", "flood_risk"]
        registry.upsert(ent)

    # flood_system sensors
    fs = domains.get("flood_system", {})
    for dtype, ddef in fs.get("data_sources", {}).items():
        if isinstance(ddef, list):
            continue
        for ex in ddef.get("examples", []):
            eid = _make_id(ex, "sensor")
            ent = _empty_entity(eid, ex, "sensor", "remote_sensing", src)
            ent["used_for"]    = list(ddef.get("used_for", []))
            ent["limitations"] = list(ddef.get("limitations", []))
            ent["aliases"]     = [ex]
            ent["contexts"]    = ["flood_mapping"]
            registry.upsert(ent)

    # flood detection methods
    for mkey, mdef in fs.get("methods", {}).items():
        eid = _make_id(mkey, "method")
        ent = _empty_entity(eid, mkey.replace("_", " ").title(), "method", "flood_mapping", src)
        ent["inputs"]   = list(mdef.get("input", []))
        ent["used_for"] = ["flood_detection", "flood_mapping"]
        registry.upsert(ent)

    # drought indices
    for idx in domains.get("drought_system", {}).get("indices", []):
        eid = _make_id(idx, "data")
        ent = _empty_entity(eid, idx, "data", "hydrology", src)
        ent["used_for"] = ["drought_monitoring"]
        registry.upsert(ent)

    # wetland methods
    for mkey in domains.get("wetlands", {}).get("methods", []):
        eid = _make_id(mkey, "method")
        ent = _empty_entity(eid, mkey.replace("_", " ").title(), "method", "remote_sensing", src)
        ent["used_for"] = ["wetland_mapping"]
        registry.upsert(ent)


# ─────────────────────────────────────────────────────────────────────────────
# Inject manually curated entities not present in source files
# ─────────────────────────────────────────────────────────────────────────────

_CURATED: list[dict] = [
    # Methods not in any source file but referenced in examples
    {"id": "method.sar_thresholding",
     "display_name": "SAR Backscatter Thresholding",
     "type": "method", "type_group": "method",
     "domain": "flood_mapping",
     "contexts": ["flood_mapping"],
     "aliases": ["SAR threshold", "backscatter thresholding", "water thresholding"],
     "definition": "Classifies water by applying a fixed or adaptive threshold to SAR backscatter intensity.",
     "used_for": ["flood extent mapping", "water detection"],
     "inputs": ["SAR_backscatter"],
     "outputs": ["flood_extent"],
     "limitations": ["wind roughening", "vegetation masking", "urban shadow"],
     "related": ["data.sar", "method.sar_change_detection"],
     "source_files": ["curated"]},

    {"id": "method.sar_change_detection",
     "display_name": "SAR Change Detection",
     "type": "method", "type_group": "method",
     "domain": "flood_mapping",
     "contexts": ["flood_mapping"],
     "aliases": ["multi-temporal SAR", "SAR change detection", "temporal differencing"],
     "definition": "Compares multi-temporal SAR images to detect land cover changes including flood inundation.",
     "used_for": ["flood extent mapping", "change analysis"],
     "inputs": ["multi_temporal_SAR"],
     "outputs": ["flood_extent"],
     "limitations": ["requires reference image"],
     "related": ["data.sar", "method.sar_thresholding"],
     "source_files": ["curated"]},

    {"id": "method.object_based_image_analysis",
     "display_name": "Object-Based Image Analysis",
     "type": "method", "type_group": "method",
     "domain": "remote_sensing",
     "contexts": ["flood_mapping"],
     "aliases": ["OBIA", "GEOBIA", "object based", "object-based"],
     "definition": "Segments imagery into spatially homogeneous objects and classifies based on spectral, textural, and contextual attributes.",
     "used_for": ["flood mapping", "land cover classification", "wetland mapping"],
     "inputs": ["SAR", "optical_imagery"],
     "outputs": ["classified_map"],
     "limitations": ["scale dependency", "complex parameterization"],
     "related": ["method.sar_thresholding"],
     "source_files": ["curated"]},

    {"id": "sensor.sentinel_1",
     "display_name": "Sentinel-1",
     "type": "sensor", "type_group": "sensor",
     "domain": "remote_sensing",
     "contexts": ["flood_mapping"],
     "aliases": ["Sentinel-1", "S1", "Sentinel 1", "SENTINEL-1", "Sentinel1"],
     "definition": "ESA C-band SAR satellite constellation providing systematic flood-relevant radar imagery.",
     "used_for": ["flood extent mapping", "soil moisture retrieval", "displacement monitoring"],
     "inputs": [],
     "outputs": ["SAR_imagery"],
     "limitations": ["wind-roughened water confusion", "layover in mountainous terrain"],
     "related": ["data.sar", "sensor.sentinel_2", "org.esa"],
     "source_files": ["curated"]},

    {"id": "sensor.sentinel_2",
     "display_name": "Sentinel-2",
     "type": "sensor", "type_group": "sensor",
     "domain": "remote_sensing",
     "contexts": ["flood_mapping"],
     "aliases": ["Sentinel-2", "S2", "Sentinel 2", "SENTINEL-2", "Sentinel2"],
     "definition": "ESA multispectral optical satellite with 10-60 m resolution for land surface monitoring.",
     "used_for": ["flood mapping", "land cover", "vegetation monitoring", "water detection"],
     "limitations": ["cloud contamination"],
     "related": ["sensor.sentinel_1", "data.ndwi", "data.ndvi", "org.esa"],
     "source_files": ["curated"]},

    {"id": "sensor.alos_palsar",
     "display_name": "ALOS PALSAR",
     "type": "sensor", "type_group": "sensor",
     "domain": "remote_sensing",
     "contexts": ["flood_mapping"],
     "aliases": ["ALOS PALSAR", "PALSAR", "ALOS-PALSAR", "PALSAR-2"],
     "definition": "Japanese L-band SAR sensor capable of penetrating dense vegetation for flood detection.",
     "used_for": ["flood extent under vegetation", "DEM generation", "forest mapping"],
     "limitations": ["lower spatial resolution than C-band"],
     "related": ["data.sar", "data.dem"],
     "source_files": ["curated"]},

    {"id": "sensor.landsat_8",
     "display_name": "Landsat-8",
     "type": "sensor", "type_group": "sensor",
     "domain": "remote_sensing",
     "contexts": [],
     "aliases": ["Landsat-8", "Landsat 8", "OLI/TIRS", "Landsat8"],
     "definition": "USGS/NASA optical satellite with 30 m resolution used for land cover and flood mapping.",
     "used_for": ["flood mapping", "land cover", "water detection"],
     "limitations": ["cloud contamination", "16-day revisit"],
     "related": ["data.ndwi", "data.ndvi", "org.usgs", "org.nasa"],
     "source_files": ["curated"]},

    {"id": "sensor.landsat_9",
     "display_name": "Landsat-9",
     "type": "sensor", "type_group": "sensor",
     "domain": "remote_sensing",
     "contexts": [],
     "aliases": ["Landsat-9", "Landsat 9", "Landsat9"],
     "definition": "USGS/NASA optical satellite continuation of Landsat-8.",
     "used_for": ["land cover", "flood mapping"],
     "related": ["sensor.landsat_8"],
     "source_files": ["curated"]},

    {"id": "metric.f1_score",
     "display_name": "F1 Score",
     "type": "metric", "type_group": "metric",
     "domain": "flood_mapping",
     "contexts": [],
     "aliases": ["F1", "F1-score", "F1 score", "F-measure", "Dice score", "Dice coefficient"],
     "definition": "Harmonic mean of precision and recall, balancing false positives and false negatives.",
     "used_for": ["classification evaluation", "flood mapping accuracy", "binary classification"],
     "related": ["metric.overall_accuracy", "metric.iou", "metric.kappa"],
     "source_files": ["curated"]},

    {"id": "metric.iou",
     "display_name": "Intersection over Union",
     "type": "metric", "type_group": "metric",
     "domain": "flood_mapping",
     "contexts": [],
     "aliases": ["IoU", "IOU", "Intersection over Union", "Jaccard index", "Jaccard coefficient"],
     "definition": "Ratio of the intersection to the union of predicted and reference flood extents.",
     "used_for": ["flood extent accuracy", "segmentation evaluation"],
     "related": ["metric.f1_score", "metric.overall_accuracy"],
     "source_files": ["curated"]},

    {"id": "metric.overall_accuracy",
     "display_name": "Overall Accuracy",
     "type": "metric", "type_group": "metric",
     "domain": "remote_sensing",
     "contexts": [],
     "aliases": ["OA", "Overall Accuracy", "overall accuracy"],
     "definition": "Proportion of correctly classified pixels or samples out of total samples.",
     "used_for": ["classification evaluation"],
     "related": ["metric.kappa", "metric.f1_score"],
     "source_files": ["curated"]},

    {"id": "metric.auc",
     "display_name": "Area Under the ROC Curve",
     "type": "metric", "type_group": "metric",
     "domain": "hydrology",
     "contexts": [],
     "aliases": ["AUC", "AUC-ROC", "area under curve"],
     "definition": "Aggregate performance measure across all classification thresholds.",
     "used_for": ["classifier evaluation", "flood susceptibility assessment"],
     "related": ["metric.f1_score"],
     "source_files": ["curated"]},

    {"id": "concept.base_flood_elevation",
     "display_name": "Base Flood Elevation",
     "type": "concept", "type_group": "concept",
     "domain": "flood_mapping",
     "contexts": ["regulatory", "BFE_estimation"],
     "aliases": ["BFE", "base flood elevation", "Base Flood Elevation", "100-year flood elevation"],
     "definition": "Elevation of the 1%-annual-chance flood (100-year flood), used as regulatory standard for floodplain management.",
     "used_for": ["flood insurance rating", "floodplain permitting", "structure elevation requirements"],
     "related": ["concept.firm", "concept.sfha", "org.fema"],
     "source_files": ["curated"]},

    {"id": "concept.sfha",
     "display_name": "Special Flood Hazard Area",
     "type": "concept", "type_group": "concept",
     "domain": "flood_mapping",
     "contexts": ["regulatory"],
     "aliases": ["SFHA", "Special Flood Hazard Area", "Zone A", "Zone AE", "flood zone"],
     "definition": "Land that is subject to inundation by the 1%-annual-chance flood as delineated on FIRMs.",
     "used_for": ["mandatory flood insurance purchase", "building code requirements"],
     "related": ["concept.firm", "concept.base_flood_elevation", "org.fema"],
     "source_files": ["curated"]},

    {"id": "concept.flood_extent",
     "display_name": "Flood Extent",
     "type": "concept", "type_group": "concept",
     "domain": "flood_mapping",
     "contexts": ["flood_mapping"],
     "aliases": ["flood extent", "inundation extent", "flood boundary", "flood inundation area"],
     "definition": "Spatial delineation of the area covered by floodwater during an event.",
     "used_for": ["damage assessment", "emergency response", "risk mapping"],
     "related": ["data.sar", "data.dem", "concept.flood_susceptibility"],
     "source_files": ["curated"]},

    {"id": "concept.flood_susceptibility",
     "display_name": "Flood Susceptibility",
     "type": "concept", "type_group": "concept",
     "domain": "flood_mapping",
     "contexts": ["flood_mapping"],
     "aliases": ["flood susceptibility", "flood vulnerability", "flood hazard potential"],
     "definition": "Likelihood of a location being inundated, derived from topographic and hydrological conditioning factors.",
     "used_for": ["flood risk mapping", "land use planning"],
     "related": ["concept.flood_extent", "data.dem"],
     "source_files": ["curated"]},

    {"id": "concept.firm",
     "display_name": "Flood Insurance Rate Map",
     "type": "concept", "type_group": "concept",
     "domain": "flood_mapping",
     "contexts": ["regulatory"],
     "aliases": ["FIRM", "Flood Insurance Rate Map", "flood insurance rate map", "DFIRM"],
     "definition": "Official map produced by FEMA delineating flood hazard zones and BFEs for insurance and regulatory purposes.",
     "used_for": ["flood insurance requirements", "floodplain management"],
     "related": ["concept.base_flood_elevation", "concept.sfha", "org.fema"],
     "source_files": ["curated"]},

    {"id": "org.esa",
     "display_name": "European Space Agency",
     "type": "organization", "type_group": "concept",
     "domain": "remote_sensing",
     "contexts": [],
     "aliases": ["ESA", "European Space Agency"],
     "definition": "European intergovernmental organization for space activities and Earth observation.",
     "used_for": ["satellite operations", "Copernicus programme"],
     "related": ["sensor.sentinel_1", "sensor.sentinel_2"],
     "source_files": ["curated"]},

    {"id": "org.usace",
     "display_name": "US Army Corps of Engineers",
     "type": "organization", "type_group": "concept",
     "domain": "flood_mapping",
     "contexts": [],
     "aliases": ["USACE", "US Army Corps of Engineers", "Army Corps"],
     "definition": "US federal agency providing civil engineering and flood risk management services.",
     "used_for": ["flood control infrastructure", "water resource management"],
     "related": ["org.fema", "method.hec_ras"],
     "source_files": ["curated"]},

    {"id": "uncertainty.overfitting",
     "display_name": "Overfitting",
     "type": "uncertainty", "type_group": "concept",
     "domain": "hydrology",
     "contexts": ["machine_learning"],
     "aliases": ["overfitting", "over-fitting", "model overfitting"],
     "definition": "When a model learns noise in training data and fails to generalize to unseen data.",
     "used_for": [],
     "related": ["method.random_forest", "method.artificial_neural_network"],
     "source_files": ["curated"]},

    {"id": "uncertainty.dem_error",
     "display_name": "DEM Error",
     "type": "uncertainty", "type_group": "concept",
     "domain": "terrain_analysis",
     "contexts": [],
     "aliases": ["DEM error", "DEM uncertainty", "elevation error"],
     "definition": "Vertical positional inaccuracies in digital elevation models propagating through flood modeling.",
     "used_for": [],
     "related": ["data.dem", "method.hec_ras"],
     "source_files": ["curated"]},

    {"id": "method.sebal",
     "display_name": "SEBAL",
     "type": "method", "type_group": "method",
     "domain": "hydrology",
     "contexts": ["agriculture_water"],
     "aliases": ["SEBAL", "Surface Energy Balance Algorithm for Land"],
     "definition": "Remote sensing algorithm for estimating actual evapotranspiration from land surface energy balance.",
     "used_for": ["evapotranspiration estimation", "water productivity mapping"],
     "inputs": ["LST", "NDVI", "radiation"],
     "outputs": ["ETa"],
     "source_files": ["curated"]},

    {"id": "method.support_vector_machine",
     "display_name": "Support Vector Machine",
     "type": "method", "type_group": "method",
     "domain": "flood_mapping",
     "contexts": [],
     "aliases": ["SVM", "Support Vector Machine", "SVR", "support vector"],
     "definition": "Supervised machine learning algorithm finding an optimal hyperplane separating classes.",
     "used_for": ["flood mapping", "land cover classification"],
     "related": ["method.random_forest"],
     "source_files": ["curated"]},

    {"id": "method.convolutional_neural_network",
     "display_name": "Convolutional Neural Network",
     "type": "method", "type_group": "method",
     "domain": "flood_mapping",
     "contexts": [],
     "aliases": ["CNN", "ConvNet", "convolutional neural network", "deep learning CNN"],
     "definition": "Deep learning architecture using convolutional layers for spatial feature extraction from imagery.",
     "used_for": ["image classification", "flood mapping", "semantic segmentation"],
     "related": ["method.u_net", "method.artificial_neural_network"],
     "source_files": ["curated"]},

    {"id": "method.long_short_term_memory",
     "display_name": "Long Short-Term Memory",
     "type": "method", "type_group": "method",
     "domain": "hydrology",
     "contexts": [],
     "aliases": ["LSTM", "long short-term memory"],
     "definition": "Recurrent neural network variant designed to learn long-term temporal dependencies.",
     "used_for": ["streamflow prediction", "flood forecasting", "time series modeling"],
     "related": ["method.artificial_neural_network"],
     "source_files": ["curated"]},

    {"id": "method.u_net",
     "display_name": "U-Net",
     "type": "method", "type_group": "method",
     "domain": "flood_mapping",
     "contexts": [],
     "aliases": ["U-Net", "UNet", "u-net"],
     "definition": "Encoder-decoder convolutional neural network architecture for semantic segmentation.",
     "used_for": ["flood segmentation", "semantic segmentation"],
     "related": ["method.convolutional_neural_network"],
     "source_files": ["curated"]},

    {"id": "method.random_forest",
     "display_name": "Random Forest",
     "type": "method", "type_group": "method",
     "domain": "flood_mapping",
     "contexts": [],
     "aliases": ["RF", "Random Forest", "RandomForest", "random forest", "random_forest"],
     "definition": "Ensemble of decision trees trained with bootstrap aggregation and random feature selection.",
     "used_for": ["flood classification", "land cover mapping", "DEM error modeling"],
     "related": ["method.artificial_neural_network", "method.support_vector_machine"],
     "source_files": ["curated"]},
]


# ─────────────────────────────────────────────────────────────────────────────
# Alias table builder
# ─────────────────────────────────────────────────────────────────────────────

def _build_alias_table(registry: Registry) -> dict[str, str]:
    """Build a flat alias → canonical_id lookup."""
    slug_to_id: dict[str, str] = {}

    for ent in registry.all_entities():
        eid = ent["id"]
        # register the ID itself and its slug
        slug_to_id[eid] = eid
        slug_to_id[_slugify(eid.split(".", 1)[-1])] = eid
        # register display_name
        slug_to_id[_slugify(ent["display_name"])] = eid
        # register all aliases
        for alias in ent.get("aliases", []):
            slug_to_id[_slugify(alias)] = eid

    # inject curated extras
    for eid, aliases in _EXTRA_ALIASES.items():
        for alias in aliases:
            slug_to_id[_slugify(alias)] = eid

    return slug_to_id


# ─────────────────────────────────────────────────────────────────────────────
# Post-processing: inject extra aliases from _EXTRA_ALIASES
# ─────────────────────────────────────────────────────────────────────────────

def _inject_extra_aliases(registry: Registry) -> None:
    for eid, aliases in _EXTRA_ALIASES.items():
        if eid in registry._entities:
            for alias in aliases:
                if alias not in registry._entities[eid]["aliases"]:
                    registry._entities[eid]["aliases"].append(alias)


# ─────────────────────────────────────────────────────────────────────────────
# Main build
# ─────────────────────────────────────────────────────────────────────────────

def build() -> None:
    OUT.mkdir(parents=True, exist_ok=True)

    registry = Registry()

    # 1. Process each source file
    sources = {
        "glossary_acronyms.json":         _parse_glossary,
        "bfe_methods.json":               _parse_bfe_methods,
        "ontology_methods.json":          _parse_ontology_methods,
        "floods_satelite.json":           _parse_floods_satelite,
        "node_methods.json":              _parse_node_methods,
        "remote_sensing_water_resources.json": _parse_remote_sensing,
        "flood_modeling_ontology_v2.json": _parse_v2_ontology,
    }

    for filename, parser in sources.items():
        path = DATA / filename
        if not path.exists():
            print(f"  skipping {filename} (not found)")
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        parser(data, registry, filename)
        print(f"  parsed {filename}: registry now {len(registry._entities)} entities")

    # 2. Inject curated entities (fills gaps and fixes critical entries)
    for entity in _CURATED:
        registry.upsert(entity)
    print(f"  injected curated: registry now {len(registry._entities)} entities")

    # 3. Inject extra aliases
    _inject_extra_aliases(registry)

    # 4. Build alias table
    alias_table = _build_alias_table(registry)
    print(f"  alias table: {len(alias_table)} entries")

    # 5. Partition into output files
    by_file = registry.by_file_key()

    output_map = {
        "methods":      by_file.get("methods", []),
        "sensors":      by_file.get("sensors", []),
        "metrics":      by_file.get("metrics", []),
        "data_sources": by_file.get("data_sources", []),
        "concepts":     by_file.get("concepts", []),
    }

    # 6. Write per-type files
    for file_key, entities in output_map.items():
        if not entities:
            continue
        entities_sorted = sorted(entities, key=lambda e: e["id"])
        out_path = OUT / f"{file_key}.json"
        out_path.write_text(
            json.dumps(entities_sorted, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        print(f"  wrote {out_path.name}: {len(entities_sorted)} entities")

    # 7. Write ontology_registry.json (all entities, keyed by canonical id)
    all_entities = sorted(registry.all_entities(), key=lambda e: e["id"])
    registry_map = {ent["id"]: ent for ent in all_entities}
    (OUT / "ontology_registry.json").write_text(
        json.dumps(registry_map, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"  wrote ontology_registry.json: {len(registry_map)} total entities")

    # 8. Write aliases.json  (slug → canonical_id, with full display_name for lookup)
    aliases_out = {}
    for slug, eid in sorted(alias_table.items()):
        ent = registry._entities.get(eid, {})
        aliases_out[slug] = {
            "canonical_id":  eid,
            "display_name":  ent.get("display_name", ""),
            "type":          ent.get("type", ""),
        }
    (OUT / "aliases.json").write_text(
        json.dumps(aliases_out, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"  wrote aliases.json: {len(aliases_out)} alias entries")

    # 9. Summary
    print("\n=== Ontology Registry Summary ===")
    for file_key, entities in output_map.items():
        print(f"  {file_key:20s}: {len(entities):4d} entities")
    print(f"  {'total':20s}: {len(all_entities):4d} entities")
    print(f"  {'aliases':20s}: {len(aliases_out):4d} lookups")


if __name__ == "__main__":
    build()
