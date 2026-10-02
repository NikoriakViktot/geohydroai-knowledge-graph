"""
graph_loader.py  —  Loads Stage 2/3 outputs into in-memory graph objects
                    ready for batch writing to Neo4j.

Data sources (read-only — never re-parses XML or calls APIs):
  • data/enriched/*.json       → OpenAlex-enriched paper dicts (post Stage 2)
  • data/literature/paper_json → Stage 1 paper dicts (fallback when enriched is empty)
  • data/analytics/*.parquet   → via DuckDB views (papers, authors, topics, …)
  • data/ontology/ontology_registry.json → method/sensor family classification
"""
from __future__ import annotations

import json
import logging
from collections import Counter, defaultdict
from functools import lru_cache
from pathlib import Path
from typing import Iterator

import pandas as pd

from src.services.identity import normalize_doi

log = logging.getLogger("geohydro.graph.loader")

_PROJECT_ROOT    = Path(__file__).resolve().parents[2]
_ENRICHED_DIR    = _PROJECT_ROOT / "data" / "enriched"
_PAPER_JSON_DIR  = _PROJECT_ROOT / "data" / "literature" / "paper_json"
_ONTOLOGY_REG    = _PROJECT_ROOT / "data" / "ontology" / "ontology_registry.json"

# Co-occurrence minimum threshold — below this, no edge is created
COOCCUR_THRESHOLD = 3

# ── Ontology registry (cached) ─────────────────────────────────────────────────

@lru_cache(maxsize=1)
def _registry() -> dict:
    with open(_ONTOLOGY_REG) as f:
        return json.load(f)


# ── Method family classification ───────────────────────────────────────────────
# Explicit override sets for IDs where domain alone is ambiguous.

_ML_IDS = {
    'method.artificial_neural_network', 'method.ann', 'method.ann_models',
    'method.machine_learning', 'method.random_forest', 'method.support_vector_machine',
    'method.dt', 'method.knn', 'method.self_organizing_map', 'method.ddm',
    'method.ml', 'method.nn', 'method.anfis', 'method.svmr',
}
_TERRAIN_IDS = {
    'method.hand', 'method.idw', 'method.ls', 'method.lm', 'method.rk',
    'method.ck', 'method.georeferencing', 'method.affine_transform',
    'method.space_geodesy', 'method.reprojection', 'method.block_adjustment',
}
_OPS_IDS = {
    'method.early_warning_system', 'method.efas', 'method.glofas', 'method.gfds',
    'method.glofris', 'method.fews_net', 'method.real_time_monitoring', 'method.dss',
}
_SAR_SENSOR_IDS = {
    'sensor.radar', 'sensor.alos_palsar', 'sensor.radarsat', 'sensor.envisat',
    'sensor.asar', 'sensor.cosmo_skymed', 'sensor.alos', 'sensor.ers',
    'sensor.ers_1', 'sensor.jers', 'sensor.jers_1', 'sensor.risat',
    'sensor.sars', 'sensor.polsar', 'sensor.sir', 'sensor.ifsar', 'sensor.scatterometer',
}
_OPT_SENSOR_IDS = {
    'sensor.sentinel', 'sensor.landsat', 'sensor.modis', 'sensor.landsat_8',
    'sensor.aster', 'sensor.spot', 'sensor.avhrr', 'sensor.viirs',
    'sensor.landsat_oli', 'sensor.etm', 'sensor.etm_plus', 'sensor.landsat_etm_plus',
    'sensor.tm', 'sensor.landsat_tm', 'sensor.ali', 'sensor.meris', 'sensor.hyperion',
    'sensor.aviris', 'sensor.casi', 'sensor.chris', 'sensor.polder', 'sensor.vhrr',
    'sensor.eo_1', 'sensor.irs', 'sensor.liss', 'sensor.awifs', 'sensor.avnir',
    'sensor.hrv', 'sensor.prisma', 'sensor.formosat', 'sensor.flex', 'sensor.tiros',
}
_PRECIP_SENSOR_IDS = {
    'sensor.trmm', 'sensor.tmpa', 'sensor.gmi', 'sensor.tmi', 'sensor.nexrad',
    'sensor.amsr_e', 'sensor.radiometer', 'sensor.microwave_radiometer',
    'sensor.atms', 'sensor.smos', 'sensor.smap', 'sensor.goes', 'sensor.abi',
    'sensor.glm', 'sensor.cloudsat',
}
_LIDAR_SENSOR_IDS = {'sensor.lidar', 'sensor.altimeter', 'sensor.lfc', 'sensor.swot', 'sensor.grace'}


def _method_family(cid: str) -> str:
    if cid in _ML_IDS:        return "ML / AI"
    if cid in _TERRAIN_IDS:   return "Terrain Analysis"
    if cid in _OPS_IDS:       return "Operational Systems"
    domain = _registry().get(cid, {}).get("domain", "")
    return {
        "hydrology":       "Hydrological Models",
        "terrain_analysis":"Terrain Analysis",
        "flood_mapping":   "Flood Mapping Methods",
        "remote_sensing":  "Image Processing",
    }.get(domain, "Other")


def _sensor_family(cid: str) -> str:
    if cid in _SAR_SENSOR_IDS:   return "SAR / Radar"
    if cid in _OPT_SENSOR_IDS:   return "Optical"
    if cid in _PRECIP_SENSOR_IDS: return "Precipitation / MW"
    if cid in _LIDAR_SENSOR_IDS:  return "LiDAR / Altimetry"
    if cid in {'sensor.uav', 'sensor.uas'}: return "UAV / Drone"
    # keyword fallback
    meta = _registry().get(cid, {})
    text = (" ".join(meta.get("used_for", [])) + " " + meta.get("definition", "")).lower()
    if any(k in text for k in ("sar", "synthetic aperture", "radar backscatter")):
        return "SAR / Radar"
    if any(k in text for k in ("precipitation", "rainfall")):
        return "Precipitation / MW"
    if any(k in text for k in ("lidar", "laser", "altim")):
        return "LiDAR / Altimetry"
    if any(k in text for k in ("multispectral", "land cover", "optical")):
        return "Optical"
    return "Other"


# ── Country centroid lookup (from theme.py) ────────────────────────────────────

_COUNTRY_CENTROIDS: dict[str, list[float]] = {
    "USA": [-95.7, 37.1], "India": [78.9, 20.6], "China": [104.2, 35.9],
    "Ukraine": [31.2, 48.4], "Germany": [10.5, 51.2], "Italy": [12.6, 41.9],
    "UK": [-3.4, 55.4], "France": [2.2, 46.2], "Indonesia": [113.9, -0.8],
    "Iran": [53.7, 32.4], "Australia": [133.8, -25.3], "Brazil": [-51.9, -14.2],
    "Canada": [-96.8, 56.1], "Spain": [-3.7, 40.2], "Pakistan": [69.3, 30.4],
    "Netherlands": [5.3, 52.1], "Bangladesh": [90.4, 23.7], "Turkey": [35.2, 39.0],
    "Japan": [138.3, 36.2], "Ethiopia": [40.5, 9.1], "Nigeria": [8.7, 9.1],
    "Mexico": [-102.6, 23.6], "South Korea": [127.8, 36.5], "Sweden": [17.0, 62.0],
    "Switzerland": [8.2, 46.8], "Poland": [19.1, 51.9], "Belgium": [4.5, 50.5],
    "Portugal": [-8.2, 39.4], "Denmark": [9.5, 56.3], "Norway": [10.2, 60.5],
    "Finland": [25.7, 61.9], "Austria": [14.6, 47.5], "Czech Republic": [15.5, 49.8],
    "Romania": [24.9, 45.9], "Greece": [21.8, 39.1], "Hungary": [19.5, 47.2],
    "Argentina": [-63.6, -38.4], "Colombia": [-74.3, 4.6], "Peru": [-75.0, -9.2],
    "Chile": [-71.5, -35.7], "Thailand": [100.9, 15.9], "Vietnam": [108.3, 14.1],
    "Malaysia": [109.7, 4.2], "Philippines": [121.8, 12.9], "Morocco": [-7.1, 31.8],
    "Egypt": [30.8, 26.8], "South Africa": [25.1, -29.0], "Kenya": [37.9, 0.0],
    "Tanzania": [34.9, -6.4], "Ghana": [-1.0, 7.9], "Algeria": [1.7, 28.0],
    "Tunisia": [9.6, 34.0], "Saudi Arabia": [44.7, 23.9], "Iraq": [43.7, 33.2],
    "Jordan": [37.0, 30.6], "Israel": [34.9, 31.5], "Afghanistan": [67.7, 33.9],
    "Kazakhstan": [66.9, 48.0], "New Zealand": [174.9, -40.9], "Russia": [105.3, 61.5],
    "Mozambique": [35.5, -18.7], "Serbia": [21.0, 44.0], "Croatia": [15.2, 45.1],
    "Slovakia": [19.7, 48.7],
}

# ── ISO-2 → country name (for institution.country_code lookup) ────────────────

_ISO2_TO_NAME: dict[str, str] = {
    "US": "USA", "IN": "India", "CN": "China", "UA": "Ukraine",
    "DE": "Germany", "IT": "Italy", "GB": "UK", "FR": "France",
    "ID": "Indonesia", "IR": "Iran", "AU": "Australia", "BR": "Brazil",
    "CA": "Canada", "ES": "Spain", "PK": "Pakistan", "NL": "Netherlands",
    "BD": "Bangladesh", "TR": "Turkey", "JP": "Japan", "ET": "Ethiopia",
    "NG": "Nigeria", "MX": "Mexico", "KR": "South Korea", "SE": "Sweden",
    "CH": "Switzerland", "PL": "Poland", "BE": "Belgium", "PT": "Portugal",
    "DK": "Denmark", "NO": "Norway", "FI": "Finland", "AT": "Austria",
    "CZ": "Czech Republic", "RO": "Romania", "GR": "Greece", "HU": "Hungary",
    "ZA": "South Africa", "NZ": "New Zealand", "RU": "Russia",
    "AR": "Argentina", "CO": "Colombia", "PE": "Peru", "CL": "Chile",
    "TH": "Thailand", "VN": "Vietnam", "MY": "Malaysia", "PH": "Philippines",
    "MA": "Morocco", "EG": "Egypt", "KE": "Kenya", "TZ": "Tanzania",
    "GH": "Ghana", "DZ": "Algeria", "TN": "Tunisia", "SA": "Saudi Arabia",
    "IQ": "Iraq", "JO": "Jordan", "IL": "Israel", "AF": "Afghanistan",
    "KZ": "Kazakhstan", "MZ": "Mozambique", "RS": "Serbia",
}


# ── Parquet loaders ────────────────────────────────────────────────────────────

def _duckdb_query(sql: str) -> pd.DataFrame:
    """Execute SQL against parquet views via thread-local DuckDB connection."""
    from src.dashboard_dash.duckdb_manager import query
    return query(sql)


def load_paper_nodes() -> list[dict]:
    df = _duckdb_query("""
        SELECT paper_id, title, doi, year, cited_by_count,
               openalex_id, journal, study_type, primary_country
        FROM papers
    """)
    rows = []
    for _, r in df.iterrows():
        year = None
        if r.year and str(r.year).isdigit():
            y = int(r.year)
            year = y if 1900 <= y <= 2030 else None
        rows.append({
            "paper_id":       str(r.paper_id),
            "title":          r.title or None,
            "doi":            r.doi or None,
            "year":           year,
            "cited_by_count": int(r.cited_by_count) if pd.notna(r.cited_by_count) else None,
            "openalex_id":    r.openalex_id or None,
            "journal":        r.journal or None,
            "study_type":     r.study_type or None,
            "primary_country":r.primary_country or None,
        })
    return rows


def load_author_nodes() -> list[dict]:
    df = _duckdb_query("SELECT author_id, display_name, orcid, paper_count FROM authors")
    return [
        {
            "author_id":    str(r.author_id),
            "display_name": r.display_name or "",
            "orcid":        r.orcid or None,
            "paper_count":  int(r.paper_count) if pd.notna(r.paper_count) else None,
        }
        for _, r in df.iterrows()
    ]


def load_institution_nodes() -> list[dict]:
    df = _duckdb_query("""
        SELECT institution_id, display_name, country_code, institution_type, paper_count
        FROM institutions
    """)
    return [
        {
            "institution_id":   str(r.institution_id),
            "display_name":     r.display_name or "",
            "country_code":     r.country_code or None,
            "institution_type": r.institution_type or None,
            "paper_count":      int(r.paper_count) if pd.notna(r.paper_count) else None,
        }
        for _, r in df.iterrows()
    ]


def load_topic_nodes() -> list[dict]:
    df = _duckdb_query("SELECT topic_id, topic_name, paper_count, avg_score FROM topics")
    return [
        {
            "topic_id":    str(r.topic_id),
            "topic_name":  r.topic_name or "",
            "paper_count": int(r.paper_count) if pd.notna(r.paper_count) else None,
            "avg_score":   float(r.avg_score) if pd.notna(r.avg_score) else None,
        }
        for _, r in df.iterrows()
    ]


def load_method_nodes() -> list[dict]:
    df = _duckdb_query("SELECT canonical_id, display_name, type_group, paper_count FROM methods")
    return [
        {
            "canonical_id": str(r.canonical_id),
            "display_name": r.display_name or "",
            "family":       _method_family(str(r.canonical_id)),
            "type_group":   r.type_group or "method",
            "paper_count":  int(r.paper_count) if pd.notna(r.paper_count) else None,
        }
        for _, r in df.iterrows()
    ]


def load_sensor_nodes() -> list[dict]:
    df = _duckdb_query("SELECT canonical_id, display_name, paper_count FROM sensors")
    return [
        {
            "canonical_id": str(r.canonical_id),
            "display_name": r.display_name or "",
            "family":       _sensor_family(str(r.canonical_id)),
            "type_group":   "sensor",
            "paper_count":  int(r.paper_count) if pd.notna(r.paper_count) else None,
        }
        for _, r in df.iterrows()
    ]


def load_metric_nodes() -> list[dict]:
    df = _duckdb_query("SELECT canonical_id, display_name, metric_type, paper_count FROM metrics")
    return [
        {
            "canonical_id": str(r.canonical_id),
            "display_name": r.display_name or "",
            "metric_type":  r.metric_type or None,
            "paper_count":  int(r.paper_count) if pd.notna(r.paper_count) else None,
        }
        for _, r in df.iterrows()
    ]


def load_country_nodes(paper_rows: list[dict], institution_rows: list[dict]) -> list[dict]:
    """Build Country nodes from all country names that appear across papers and institutions."""
    names: set[str] = set()
    for p in paper_rows:
        cn = p.get("primary_country")
        if cn and isinstance(cn, str):
            names.add(cn)
    for inst in institution_rows:
        cc = inst.get("country_code")
        if cc:
            cn = _ISO2_TO_NAME.get(cc)
            if cn:
                names.add(cn)
    rows = []
    for name in names:
        coord = _COUNTRY_CENTROIDS.get(name)
        rows.append({
            "name":     name,
            "iso_code": None,
            "lat":      coord[1] if coord else None,
            "lon":      coord[0] if coord else None,
        })
    return rows


def load_flood_event_nodes() -> list[dict]:
    from src.graph.graph_schema import FLOOD_EVENTS
    return [
        {"name": e.name, "country": e.country, "year": e.year, "lat": e.lat, "lon": e.lon}
        for e in FLOOD_EVENTS
    ]


# ── Parquet relationship loaders ───────────────────────────────────────────────

def load_author_paper_edges() -> list[dict]:
    df = _duckdb_query("""
        SELECT paper_id, author_id, author_position, is_corresponding
        FROM paper_author_edges
    """)
    return [
        {
            "author_id":        str(r.author_id),
            "paper_id":         str(r.paper_id),
            "position":         r.author_position or None,
            "is_corresponding": bool(r.is_corresponding),
        }
        for _, r in df.iterrows()
    ]


def load_paper_topic_edges() -> list[dict]:
    df = _duckdb_query("SELECT paper_id, topic_id, score FROM paper_topic_edges")
    return [
        {
            "paper_id": str(r.paper_id),
            "topic_id": str(r.topic_id),
            "score":    float(r.score) if pd.notna(r.score) else 0.0,
        }
        for _, r in df.iterrows()
    ]


def load_paper_country_edges(paper_rows: list[dict]) -> list[dict]:
    return [
        {"paper_id": p["paper_id"], "country_name": p["primary_country"]}
        for p in paper_rows
        if p.get("primary_country")
    ]


def load_institution_country_edges(institution_rows: list[dict]) -> list[dict]:
    rows = []
    for inst in institution_rows:
        cc = inst.get("country_code")
        if cc:
            cn = _ISO2_TO_NAME.get(cc)
            if cn:
                rows.append({"institution_id": inst["institution_id"], "country_name": cn})
    return rows


# ── Enriched JSON loaders ─────────────────────────────────────────────────────

def _flatten_enriched(doc: dict) -> dict:
    """Return one flat view of an enriched or Stage-1 document.

    data/enriched/*.json nest the paper under ``doc["paper"]`` and keep ``openalex`` /
    ``enrichment_meta`` beside it; Stage-1 paper.json has the paper keys at the top.
    Readers below use flat keys (``normalized_entities``, ``references``, ``openalex``),
    which silently found nothing in the nested files before this view existed.
    """
    paper = doc.get("paper")
    if not isinstance(paper, dict):
        return doc
    flat = dict(paper)
    for key, value in doc.items():
        if key != "paper":
            flat.setdefault(key, value)
    return flat


def _iter_enriched(limit: int | None = None) -> Iterator[tuple[str, dict]]:
    """Yield (paper_id, doc) for every enriched-or-Stage-1 JSON.

    Resolution order:
        1. data/enriched/*.json  (post-OpenAlex enrichment, Stage 2 output)
        2. data/literature/paper_json/*.paper.json  (Stage 1 output, fallback)

    The paper_id is taken from doc["metadata"]["paper_id"] (Stage 1 shape)
    or doc["paper"]["metadata"]["paper_id"] (legacy nested shape), falling
    back to the file stem when neither key is present.
    """
    enriched = sorted(_ENRICHED_DIR.glob("*.json"))
    paper_json = sorted(_PAPER_JSON_DIR.glob("*.paper.json"))
    files = enriched if enriched else paper_json
    if limit:
        files = files[:limit]
    for f in files:
        try:
            with open(f) as fh:
                doc = json.load(fh)
            # Support both flat (Stage 1) and nested (legacy) key layouts
            pid = (
                doc.get("metadata", {}).get("paper_id")
                or doc.get("paper", {}).get("metadata", {}).get("paper_id")
                or f.stem
            )
            yield pid, _flatten_enriched(doc)
        except Exception as exc:
            log.warning("Skipping %s: %s", f.name, exc)


def _edge_row(paper_id: str, ent: dict) -> dict:
    """
    Build a full provenance-carrying edge row from a normalized entity dict.
    Fields beyond canonical_id/confidence are written to Neo4j relationship
    properties for trust-aware graph queries.

    Coordinate provenance (page, section, bbox) is extracted from
    entity["provenance"] when present — set by entity_grounder.py.
    """
    prov = ent.get("provenance") or {}
    return {
        "paper_id":          paper_id,
        "canonical_id":      ent.get("canonical_id", ""),
        "confidence":        float(ent.get("edge_confidence") or ent.get("confidence", 1.0)),
        "surface_form":      ent.get("surface_form", ent.get("name", "")),
        "extraction_score":  float(ent.get("extraction_score", ent.get("confidence", 1.0))),
        "disambig_conf":     float(ent.get("disambig_confidence", 1.0)),
        "evidence":          ent.get("disambig_evidence") or [],
        "resolver_version":  ent.get("resolver_version", ""),
        "ontology_version":  ent.get("ontology_version", ""),
        # PDF grounding
        "page":              prov.get("page", 0),
        "section":           prov.get("section", ""),
        "bbox":              prov.get("bbox"),          # [x, y, w, h] or None
        "coord_match":       prov.get("match", ""),    # "exact" | "partial" | ""
    }


def load_entity_edges_from_enriched(
    limit: int | None = None,
) -> tuple[list[dict], list[dict], list[dict], list[dict]]:
    """
    Single pass over all enriched JSONs.

    Returns:
        paper_method_edges, paper_sensor_edges, paper_metric_edges,
        author_institution_edges
    """
    pm_edges: list[dict] = []   # paper → method
    ps_edges: list[dict] = []   # paper → sensor (from 'satellites' key)
    pmet_edges: list[dict] = [] # paper → metric
    ai_edges: list[dict] = []   # author → institution

    # Seen sets to avoid duplicates within the same paper
    seen_pm:   set[tuple] = set()
    seen_ps:   set[tuple] = set()
    seen_pmet: set[tuple] = set()
    seen_ai:   set[tuple] = set()

    n = 0
    for pid, doc in _iter_enriched(limit):
        ne = doc.get("normalized_entities", {})

        # Methods
        for ent in ne.get("methods", []):
            cid = ent.get("canonical_id")
            if cid and (pid, cid) not in seen_pm:
                seen_pm.add((pid, cid))
                pm_edges.append(_edge_row(pid, ent))

        # Satellites → mapped to Sensor nodes (canonical_id is sensor.*)
        for ent in ne.get("satellites", []):
            cid = ent.get("canonical_id")
            if cid and cid.startswith("sensor.") and (pid, cid) not in seen_ps:
                seen_ps.add((pid, cid))
                ps_edges.append(_edge_row(pid, ent))

        # Metrics
        for ent in ne.get("metrics", []):
            cid = ent.get("canonical_id")
            if cid and (pid, cid) not in seen_pmet:
                seen_pmet.add((pid, cid))
                pmet_edges.append(_edge_row(pid, ent))

        # Author → Institution (from openalex)
        oa = doc.get("openalex") or {}
        for author in oa.get("authors", []):
            aid = author.get("id")
            if not aid:
                continue
            for inst in author.get("institutions", []):
                iid = inst.get("id")
                if iid and (aid, iid) not in seen_ai:
                    seen_ai.add((aid, iid))
                    ai_edges.append({"author_id": aid, "institution_id": iid})

        n += 1
        if n % 500 == 0:
            log.info("  Enriched scan: %d files so far …", n)

    log.info("  Paper→Method: %d  Paper→Sensor: %d  Paper→Metric: %d  Author→Inst: %d",
             len(pm_edges), len(ps_edges), len(pmet_edges), len(ai_edges))
    return pm_edges, ps_edges, pmet_edges, ai_edges


def load_within_corpus_references(limit: int | None = None) -> list[dict]:
    """
    Build Paper→Paper REFERENCES edges by joining:
      paper_reference_edges (paper_id → reference_id hash)
      → paper_refs (reference_id hash → openalex_id)
      → enriched JSON openalex_id → paper_id mapping
    """
    # Build openalex_id → paper_id from enriched JSONs
    oa_to_pid: dict[str, str] = {}
    for pid, doc in _iter_enriched(limit):
        oa = doc.get("openalex") or {}
        oa_id = oa.get("openalex_id")
        if oa_id:
            oa_to_pid[oa_id] = pid

    log.info("  OA ID → paper_id map: %d entries", len(oa_to_pid))

    # Pull paper_refs that have an openalex_id we recognise
    df = _duckdb_query("""
        SELECT pre.paper_id, pr.openalex_id
        FROM paper_reference_edges pre
        JOIN paper_refs pr ON pre.reference_id = pr.reference_id
        WHERE pr.openalex_id IS NOT NULL
    """)

    edges = []
    seen:  set[tuple] = set()
    for _, row in df.iterrows():
        src = str(row.paper_id)
        oa_id = str(row.openalex_id)
        tgt = oa_to_pid.get(oa_id)
        if tgt and src != tgt and (src, tgt) not in seen:
            seen.add((src, tgt))
            edges.append({"source_paper_id": src, "target_paper_id": tgt})

    log.info("  Within-corpus REFERENCES edges: %d", len(edges))
    return edges


# ── Co-occurrence computation ─────────────────────────────────────────────────

def compute_cooccurrence_edges(
    pm_edges: list[dict],
    ps_edges: list[dict],
    threshold: int = COOCCUR_THRESHOLD,
) -> tuple[list[dict], list[dict], list[dict]]:
    """
    Compute method↔method, sensor↔sensor, and method↔sensor co-occurrence edges.
    Only pairs with count >= threshold are returned.
    """
    # Index: paper_id → set of canonical_ids
    paper_methods: defaultdict[str, set] = defaultdict(set)
    paper_sensors: defaultdict[str, set] = defaultdict(set)
    for e in pm_edges:
        paper_methods[e["paper_id"]].add(e["canonical_id"])
    for e in ps_edges:
        paper_sensors[e["paper_id"]].add(e["canonical_id"])

    mm_counter: Counter = Counter()
    ss_counter: Counter = Counter()
    ms_counter: Counter = Counter()

    all_pids = set(paper_methods.keys()) | set(paper_sensors.keys())
    for pid in all_pids:
        ms = sorted(paper_methods.get(pid, set()))
        ss = sorted(paper_sensors.get(pid, set()))

        # method × method
        for i, a in enumerate(ms):
            for b in ms[i+1:]:
                pair = (a, b) if a < b else (b, a)
                mm_counter[pair] += 1

        # sensor × sensor
        for i, a in enumerate(ss):
            for b in ss[i+1:]:
                pair = (a, b) if a < b else (b, a)
                ss_counter[pair] += 1

        # method × sensor (directed: method → sensor)
        for m in ms:
            for s in ss:
                ms_counter[(m, s)] += 1

    def _filter(counter: Counter) -> list[dict]:
        return [
            {"id_a": a, "id_b": b, "count": c}
            for (a, b), c in counter.items()
            if c >= threshold
        ]

    mm_edges = _filter(mm_counter)
    ss_edges = _filter(ss_counter)
    ms_edges = _filter(ms_counter)

    log.info("  Co-occurrence edges (threshold=%d): method↔method=%d  sensor↔sensor=%d  method→sensor=%d",
             threshold, len(mm_edges), len(ss_edges), len(ms_edges))
    return mm_edges, ss_edges, ms_edges


# ── CITES edge loader (bibliography stubs) ────────────────────────────────────

def load_cites_edges(limit: int | None = None) -> list[dict]:
    """
    Build CITES edge rows from bibliography entries in enriched JSONs.

    Produces rows compatible with GraphWriter.write_cites_edges():
        source_paper_id, target_doi, target_title, target_year

    DOI-keyed rows are separated from title-only rows by the writer so that
    DOI MERGE always creates/updates the authoritative stub and the title-only
    fallback only fires when no DOI is present.
    """
    rows: list[dict] = []
    for pid, doc in _iter_enriched(limit):
        refs = doc.get("references", [])
        for ref in refs:
            # MERGE matches the DOI string exactly; 27 % of GROBID reference DOIs
            # carry upper case, which would split one cited work into several stubs.
            doi   = normalize_doi(ref.get("doi"))
            title = ref.get("title") or None
            if not doi and not title:
                continue
            rows.append({
                "source_paper_id": pid,
                "target_doi":      doi,
                "target_title":    title,
                "target_year":     ref.get("year"),
            })
    log.info("  CITES edge candidates: %d", len(rows))
    return rows


# ── Flood event → paper linking ───────────────────────────────────────────────

def load_paper_flood_event_edges(paper_rows: list[dict]) -> list[dict]:
    """
    Link papers to FloodEvent nodes by matching primary_country and year window.
    Each FloodEvent has a country and optional year; papers from that country
    published ±3 years of the event year are linked.
    """
    from src.graph.graph_schema import FLOOD_EVENTS

    edges = []
    for event in FLOOD_EVENTS:
        for p in paper_rows:
            if p.get("primary_country") != event.country:
                continue
            py = p.get("year")
            # Year-window filter: if event has a year, require paper within ±3 years
            if event.year and py:
                if abs(py - event.year) > 3:
                    continue
            edges.append({"paper_id": p["paper_id"], "event_name": event.name})

    log.info("  Paper→FloodEvent edges: %d", len(edges))
    return edges
