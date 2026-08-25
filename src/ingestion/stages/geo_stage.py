"""
geo_stage.py — Geographic lookup utilities extracted from pipeline.py.

Provides geo constants (COUNTRY_PATTERNS, RIVER_TO_COUNTRY, etc.),
GeoNames/Nominatim lookup, and normalization helpers.
pipeline.py re-exports these for backward compatibility.
"""
from __future__ import annotations

import re
import time
import logging
from typing import Optional

import requests

from src.ingestion.utils import clean_text, ensure_dict
from src.ingestion.stages.parse_stage import NS, normalize_country_name

log = logging.getLogger(__name__)

# ── Module-level geo cache ────────────────────────────────────────────────────

GEO_CACHE: dict[str, Optional[dict]] = {}
LAST_CALL = 0.0

# ── Geographic patterns ───────────────────────────────────────────────────────

COUNTRY_PATTERNS: dict[str, str] = {
    "Ukraine":        r"\bukraine\b|\bukrainian\b|\bkyiv\b|\bkherson\b|\bkakhovka\b|\bzakarpattia\b|\bcarpathians?\b",
    "Poland":         r"\bpoland\b|\bpolish\b",
    "Germany":        r"\bgermany\b|\bgerman\b",
    "France":         r"\bfrance\b|\bfrench\b",
    "Italy":          r"\bitaly\b|\bitalian\b",
    "Spain":          r"\bspain\b|\bspanish\b",
    "Portugal":       r"\bportugal\b|\bportuguese\b",
    "Netherlands":    r"\bnetherlands\b|\bdutch\b",
    "Belgium":        r"\bbelgium\b",
    "Austria":        r"\baustria\b",
    "Switzerland":    r"\bswitzerland\b",
    "Slovenia":       r"\bslovenia\b",
    "Croatia":        r"\bcroatia\b",
    "Hungary":        r"\bhungary\b",
    "Czech Republic": r"\bczech\b",
    "Slovakia":       r"\bslovakia\b",
    "Romania":        r"\bromania\b",
    "Bulgaria":       r"\bbulgaria\b",
    "Greece":         r"\bgreece\b",
    "Turkey":         r"\bturkey\b",
    "UK":             r"\buk\b|united kingdom|england|scotland|wales",
    "Ireland":        r"\bireland\b",
    "USA":            r"\busa\b|united states|louisiana|california|texas|florida",
    "Canada":         r"\bcanada\b",
    "Mexico":         r"\bmexico\b",
    "Brazil":         r"\bbrazil\b",
    "Argentina":      r"\bargentina\b",
    "Chile":          r"\bchile\b",
    "Peru":           r"\bperu\b",
    "India":          r"\bindia\b|\bkerala\b",
    "China":          r"\bchina\b",
    "Japan":          r"\bjapan\b",
    "South Korea":    r"\bkorea\b",
    "Vietnam":        r"\bvietnam\b",
    "Thailand":       r"\bthailand\b",
    "Indonesia":      r"\bindonesia\b",
    "Malaysia":       r"\bmalaysia\b",
    "Pakistan":       r"\bpakistan\b",
    "Bangladesh":     r"\bbangladesh\b",
    "Uzbekistan":     r"\buzbekistan\b",
    "Kazakhstan":     r"\bkazakhstan\b",
    "Iran":           r"\biran\b",
    "Iraq":           r"\biraq\b",
    "Australia":      r"\baustralia\b|new south wales|queensland",
    "New Zealand":    r"\bnew zealand\b",
    "South Africa":   r"\bsouth africa\b",
    "Egypt":          r"\begypt\b",
    "Morocco":        r"\bmorocco\b",
    "Madagascar":     r"\bmadagascar\b",
    "Nigeria":        r"\bnigeria\b",
    "Philippines":    r"\bphilippines\b|\bluzon\b",
}

RIVER_TO_COUNTRY: dict[str, str] = {
    "Dnipro":        "Ukraine", "Dnieper": "Ukraine",
    "Prut":          "Ukraine/Romania", "Dniester": "Ukraine/Moldova",
    "Tisza":         "Ukraine/Hungary", "Southern Bug": "Ukraine",
    "Desna":         "Ukraine", "Moshchunka": "Ukraine",
    "Danube":        "Multiple", "Rhine": "Germany/France/Netherlands",
    "Elbe":          "Germany/Czech Republic", "Seine": "France",
    "Loire":         "France", "Thames": "UK",
    "Po":            "Italy", "Ebro": "Spain",
    "Tagus":         "Spain/Portugal", "Duero": "Spain/Portugal",
    "Carrion":       "Spain", "Krka": "Slovenia",
    "Sava":          "Slovenia/Croatia", "Drava": "Austria/Slovenia/Croatia",
    "Vistula":       "Poland", "Oder": "Germany/Poland",
    "Ganges":        "India/Bangladesh", "Indus": "India/Pakistan",
    "Yangtze":       "China", "Yellow River": "China",
    "Mekong":        "Multiple", "Irrawaddy": "Myanmar",
    "Mississippi":   "USA", "Colorado": "USA",
    "Amazon":        "Brazil/Peru", "Orinoco": "Venezuela",
    "Parana":        "Argentina/Brazil",
    "Nile":          "Multiple", "Congo": "DR Congo",
    "Niger":         "Multiple", "Zambezi": "Multiple",
    "Darling":       "Australia", "Murray": "Australia",
    "Cagayan":       "Philippines", "Cagayan River": "Philippines",
}

RIVER_PATTERNS: list[str] = [
    r"\b([A-Z][a-zA-Z\-]+(?:\s+[A-Z][a-zA-Z\-]+){0,3})\s+River basin\b",
    r"\b([A-Z][a-zA-Z\-]+(?:\s+[A-Z][a-zA-Z\-]+){0,3})\s+River\b",
    r"\b(Dnipro|Dnieper|Prut|Dniester|Danube|Tisza|Moshchunka|Krka|Sava|Darling|Carrion|Duero|Indus|Ganges)\b",
]

REGION_PATTERNS: dict[str, str] = {
    "Luzon":                r"\bluzon\b",
    "Cagayan Valley":       r"\bcagayan valley\b",
    "Northern Philippines": r"\bnorthern philippines\b",
    "Steppe zone":          r"\bsteppe zone\b",
    "Forest-steppe zone":   r"\bforest[\-\s]?steppe\b",
    "Ukrainian Carpathians":r"\bukrainian carpathians\b|\bcarpathians\b",
    "Crimean Mountains":    r"\bcrimean mountains\b",
    "Kyiv Oblast":          r"\bkyiv oblast\b",
    "Bucha district":       r"\bbucha district\b",
    "Zakarpattia":          r"\bzakarpattia\b|\btranscarpathia\b",
    "Kherson":              r"\bkherson\b",
    "Krka floodplain":      r"\bkrka river floodplain\b|\bkrka floodplain\b",
    "Lower Krka":           r"\blower krka\b",
    "Krakovo forest":       r"\bkrakovo forest\b",
    "Kerala":               r"\bkerala\b",
    "Fishlake":             r"\bfishlake\b",
    "Pontypridd":           r"\bpontypridd\b",
    "Rhondda Cynon Taf":    r"\brhondda cynon taf\b",
}

INVALID_LOCATIONS: set[str] = {
    "earth", "world", "globe", "surface",
    "region", "area", "study", "model",
    "figure", "table", "section", "introduction",
    "results", "discussion", "dsm", "worlddem",
    "sentinel", "sentinel-1", "sentinel-2",
    "sar", "gee", "google earth engine",
    "north america", "western europe",
    "the middle east", "middle east",
}

GENERIC_REGION_NOISE: set[str] = {
    "specific region", "study region", "target region",
    "selected region", "this region", "the region",
    "region", "area", "geographical region",
    "geographical regions", "selected locations",
    "selected regions", "region a", "region b", "region c",
}

_GEO_NOT_COUNTRY: frozenset[str] = frozenset({
    "muskingum", "yangtze", "pamir", "manning", "darcy",
    "thiessen", "voronoi", "euler", "navier",
    "amazonia", "amazon basin", "himalaya", "hindukush",
})


# ── Normalization helpers ─────────────────────────────────────────────────────

def ensure_country_dict(c) -> dict:
    if isinstance(c, dict):
        return {**c, "name": normalize_country_name(c.get("name")),
                "source": c.get("source", "unknown"),
                "confidence": c.get("confidence", 0.5)}
    return {"name": normalize_country_name(c), "code": None,
            "source": "unknown", "confidence": 0.5}


def ensure_list_of_country_dicts(items) -> list[dict]:
    if not items:
        return []
    result, seen = [], set()
    for item in items:
        c = ensure_country_dict(item)
        name = c.get("name")
        if not name or name in seen:
            continue
        seen.add(name)
        result.append(c)
    return result


def ensure_river_dict(r) -> dict:
    if isinstance(r, dict):
        return {**r, "name": clean_text(r.get("name")),
                "type": r.get("type", "river"),
                "source": r.get("source", "unknown")}
    return {"name": clean_text(str(r)), "type": "river", "source": "unknown"}


def ensure_list_of_river_dicts(items) -> list[dict]:
    if not items:
        return []
    result, seen = [], set()
    for item in items:
        r = ensure_river_dict(item)
        name = r.get("name")
        if not name or name in seen:
            continue
        seen.add(name)
        result.append(r)
    return result


def is_valid_place(name: str) -> bool:
    if not name:
        return False
    n = name.strip().lower()
    if len(n) < 3:
        return False
    if n in INVALID_LOCATIONS:
        return False
    if any(k in n for k in ["earth", "world", "surface"]):
        return False
    return True


def classify_location(name: str) -> str:
    n = name.lower()
    if "river" in n:
        return "river"
    if "lake" in n:
        return "lake"
    if any(k in n for k in ["basin", "catchment", "delta"]):
        return "basin"
    if any(k in n for k in ["watershed"]):
        return "watershed"
    return "region"


def is_valid_region_name(name: str) -> bool:
    if not name:
        return False
    n = clean_text(name).lower()
    if len(n) < 3:
        return False
    if n in GENERIC_REGION_NOISE:
        return False
    return True


def normalize_river_name(name: str) -> str:
    name = clean_text(name)
    name = re.sub(r"^(the|a|an)\s+", "", name, flags=re.I)
    name = re.sub(r"'s$", "", name)
    name = name.replace(" River basin", "").replace(" river basin", "")
    name = name.replace(" River", "").replace(" river", "")
    name = clean_text(name)
    if name.lower() == "cagayan":
        return "Cagayan River"
    return name


def normalize_country_item(item):
    if isinstance(item, dict):
        name = clean_text(item.get("name"))
        if not name:
            return None
        return {**item, "name": name,
                "source": item.get("source", "unknown"),
                "confidence": float(item.get("confidence", 0.5))}
    name = clean_text(str(item))
    if not name:
        return None
    return {"name": name, "code": None, "source": "ner", "confidence": 0.55}


def _country_name_is_valid(name: str) -> bool:
    return name.lower() not in _GEO_NOT_COUNTRY


def merge_countries(existing, ner_countries) -> list[dict]:
    merged = {}
    for item in existing or []:
        c = normalize_country_item(item)
        if c and _country_name_is_valid(c["name"]):
            merged[c["name"]] = c
    for item in ner_countries or []:
        c = normalize_country_item(item)
        if not c or not _country_name_is_valid(c["name"]):
            continue
        name = c["name"]
        if name not in merged:
            merged[name] = c
        else:
            merged[name]["confidence"] = max(
                float(merged[name].get("confidence", 0.5)),
                float(c.get("confidence", 0.55)),
            )
    return list(merged.values())


def compute_geo_confidence(study_geo: dict) -> float:
    geo = ensure_dict(study_geo)
    score = 0.0
    if geo.get("primary_country"):   score += 0.35
    if geo.get("countries"):         score += 0.15
    if geo.get("regions"):           score += 0.20
    if geo.get("rivers"):            score += 0.15
    if geo.get("locations"):         score += 0.10
    if geo.get("coordinates"):       score += 0.05
    return round(min(score, 1.0), 4)


# ── GeoNames / Nominatim lookup ───────────────────────────────────────────────

def geonames_type(fcode: str) -> str:
    if not fcode:           return "unknown"
    if fcode.startswith("H"): return "river"
    if fcode.startswith("P"): return "city"
    if fcode.startswith("A"): return "admin"
    if fcode.startswith("T"): return "terrain"
    return "other"


def geonames_lookup(name: str) -> Optional[dict]:
    global LAST_CALL
    if not name:
        return None
    delay   = 1.0
    elapsed = time.time() - LAST_CALL
    if elapsed < delay:
        time.sleep(delay - elapsed)
    LAST_CALL = time.time()
    from src.config import settings
    url    = "http://api.geonames.org/searchJSON"
    params = {"q": name, "maxRows": 5, "username": settings.require_env("GEONAMES_USER")}
    try:
        r = requests.get(url, params=params, timeout=5)
        if r.status_code != 200:
            return None
        results = r.json().get("geonames", [])
        if not results:
            return None
        best = next(
            (g for g in results if g.get("fcode", "").startswith(("P","A","H","T"))),
            results[0]
        )
        lat, lon = best.get("lat"), best.get("lng")
        if lat is None or lon is None:
            return None
        return {
            "name": best.get("name"), "lat": float(lat), "lon": float(lon),
            "type": geonames_type(best.get("fcode")),
            "country": best.get("countryName"),
            "feature": best.get("fcodeName"),
            "feature_code": best.get("fcode"), "source": "geonames",
        }
    except Exception:
        return None


def geocode_place(name: str) -> Optional[dict]:
    if not is_valid_place(name):
        return None
    if name in GEO_CACHE:
        return GEO_CACHE[name]
    try:
        g = geonames_lookup(name)
        if g:
            res = {"lat": g["lat"], "lon": g["lon"],
                   "country": g.get("country"), "feature": g.get("feature"),
                   "source": "geonames"}
            GEO_CACHE[name] = res
            return res
    except Exception:
        pass
    try:
        url    = "https://nominatim.openstreetmap.org/search"
        params = {"q": name, "format": "json", "limit": 1}
        r      = requests.get(url, params=params, headers={"User-Agent": "geo-parser"})
        data   = r.json()
        if data:
            res = {"lat": float(data[0]["lat"]), "lon": float(data[0]["lon"]),
                   "source": "nominatim"}
            GEO_CACHE[name] = res
            return res
    except Exception:
        pass
    GEO_CACHE[name] = None
    return None


# ── NER helpers ───────────────────────────────────────────────────────────────

def parse_ner_results(ner_entities: list[dict]) -> tuple[list[str], list[str]]:
    """Convert SpacyActor entity dicts into (countries, locations)."""
    countries, locations = set(), set()
    for ent in ner_entities or []:
        name = (ent.get("text") or "").strip()
        if not is_valid_place(name):
            continue
        label = ent.get("label", "")
        if label == "GPE":
            countries.add(name)
        elif label == "LOC":
            locations.add(name)
    return list(countries), list(locations)


def extract_tei_countries(root) -> list[dict]:
    found = {}
    for c in root.xpath("//tei:country", namespaces=NS):
        name = normalize_country_name(clean_text(" ".join(c.xpath(".//text()"))))
        code = c.get("key")
        if name:
            found[name] = {"name": name, "code": code,
                           "source": "tei", "confidence": 0.95}
    return list(found.values())


def author_name_set(metadata: dict) -> set[str]:
    names = set()
    for author in metadata.get("authors", []):
        for key in ["first_name", "last_name", "full_name"]:
            value = author.get(key)
            if not value:
                continue
            for part in str(value).split():
                p = clean_text(part).lower()
                if len(p) > 2:
                    names.add(p)
    return names


def extract_author_geo(metadata: dict) -> list[dict]:
    countries = {}
    for author in metadata.get("authors", []):
        for country in author.get("affiliation_countries", []):
            name = normalize_country_name(country)
            if name:
                countries[name] = {"name": name,
                                   "source": "tei_author_affiliation",
                                   "confidence": 0.95}
    return list(countries.values())
