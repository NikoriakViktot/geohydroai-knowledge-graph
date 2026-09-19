"""Phase 0a — turn theses.yaml into an auditable OpenAlex query set. No HTTP, no LLM.

The query set is written to disk *before* any network call, so the search strategy
can be read and disputed on its own terms rather than reverse-engineered from the
papers it happened to return.

Deliberately not filtered on open access: the coverage diagnostic needs the true
worldwide hit count, including the papers we will never be able to download.
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from src.paper_3._utils import HARVEST_DIR
from src.paper_3.theses import Thesis, load_theses

logger = logging.getLogger(__name__)

#: Version of the *harvest* query set — separate from freeze.RETRIEVAL_RULES_VERSION,
#: which only moves when retrieval logic changes. The query set changes the corpus
#: that every downstream literature claim is drawn from, so a reviewer must be able
#: to ask "which queries built this corpus?" and get a hash back.
HARVEST_QUERYSET_VERSION = "1.1.0"
QUERYSET_CHANGELOG = {
    "1.0.0": "theses.yaml openalex_queries + key-term pairs + cross-block conjunctions",
    "1.1.0": "Boolean slices S1–S4 (reviewer-facing searches with their own denominators)",
}

#: How the query text is sent. `search=` matches title, abstract and available
#: full text and accepts upper-case AND / OR / NOT, quoted phrases and parentheses.
OPENALEX_SEARCH_MODE = "works?search= (boolean AND/OR/NOT, quoted phrases; title+abstract+fulltext)"

#: OpenAlex filters applied to every query.
DEFAULT_FILTERS = {
    "from_publication_date": "2000-01-01",
    "type": "article|review|preprint",
}

#: Pages fetched per query (50 works each). Slices go deeper because their
#: screened sample is later used as a denominator; a 100-work cap on a search
#: returning several hundred would make every absence claim undersampled.
DEFAULT_MAX_PAGES = 2
SLICE_MAX_PAGES = 10

#: Reviewer-facing Boolean slices. Each names the theses it screens for, but its
#: candidates are NOT folded into the per-thesis coverage ratio —
#: corpus_index.build_coverage reads only theses.yaml openalex_queries — so a slice
#: adds papers without moving the coverage diagnostic it would otherwise inflate.
BOOLEAN_SLICES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("S1_kakhovka_status_quo",
     '("Kakhovka" OR "Kakhovske" OR "Nova Kakhovka") AND (dam OR reservoir OR HPP) '
     'AND (breach OR collapse OR destruction OR drainage)',
     ("T02", "T03", "T19")),
    ("S2_altimetry_vertical_datum",
     '("ICESat-2" OR SWOT OR "satellite altimetry") AND ("vertical datum" OR geoid OR '
     '"permanent tide" OR harmonisation OR harmonization OR "reference surface") '
     'AND ("inland water" OR reservoir OR river) NOT "SWOT analysis"',
     ("T08", "T09", "T10", "T11")),
    ("S3_hydraulic_transition_from_space",
     '("water surface slope" OR "hydraulic regime" OR hydrodynamic) AND (satellite OR '
     '"remote sensing") AND ("dam removal" OR "dam breach" OR "dam failure" OR '
     '"reservoir drawdown")',
     ("T02", "T03", "T23")),
    ("S4_historical_data_as_validation",
     '("historical bathymetry" OR "legacy data" OR "design documentation" OR archival) '
     'AND (validation OR calibration) AND (satellite OR DEM OR altimetry) '
     'AND (river OR reservoir OR hydraulic OR hydrology)',
     ("T15", "T16", "T17", "T18")),
    ("S5_vegetation_succession_drained_bed",
     '("Kakhovka" OR "drained reservoir" OR "reservoir drawdown" OR "dam removal") '
     'AND (revegetation OR succession OR "vegetation cover" OR willow OR "plant colonisation") '
     'AND ("exposed bed" OR "reservoir bed" OR "former reservoir" OR sediment)',
     ("T25", "T26", "T27")),
    ("S6_roughness_vegetation",
     '("Manning" OR "roughness coefficient" OR "flow resistance") AND (vegetation OR '
     'willow OR "riparian forest" OR reed) AND (floodplain OR "dam breach" OR '
     '"dam removal" OR "flood conveyance")',
     ("T33", "T34")),
    ("S7_channel_width_dam",
     '("channel width" OR "wetted width" OR braiding OR "bank erosion") AND '
     '("dam removal" OR "dam breach" OR "dam failure" OR "reservoir drawdown") '
     'AND (satellite OR "remote sensing" OR Sentinel OR Landsat)',
     ("T32", "T22")),
)

SLICE_SOURCE = "boolean_slice"


def slice_query(slice_id: str) -> str:
    for sid, query, _ in BOOLEAN_SLICES:
        if sid == slice_id:
            return query
    raise KeyError(f"no such slice: {slice_id}")

#: Cross-block conjunctions the manuscript actually owns. Each pairs terms that
#: individually return thousands of irrelevant works but together describe
#: precisely the intersection nobody has searched for.
CROSS_BLOCK: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("satellite altimetry", "vertical datum harmonization", ("T08", "T11")),
    ("dam breach", "water surface slope", ("T02", "T03")),
    ("reservoir", "longitudinal water surface profile", ("T01", "T04")),
    ("water connectivity", "water surface elevation geometry", ("T19", "T20")),
    ("historical bathymetry", "satellite laser altimetry", ("T15", "T17")),
)

#: Cap on generated (as opposed to hand-written) queries per thesis. Keeps the
#: HTTP budget bounded and the query set readable.
MAX_GENERATED_PER_THESIS = 3


def _pair_queries(t: Thesis, limit: int = MAX_GENERATED_PER_THESIS) -> list[str]:
    """Cross the first term of the two most specific key-term families.

    The first family is the thesis's distinguishing concept and the second its
    domain; pairing them is what keeps "water surface slope" from returning the
    whole of fluvial geomorphology.
    """
    if len(t.key_terms) < 2:
        return []
    heads = [fam[0] for fam in t.key_terms[:3]]
    out: list[str] = []
    for i in range(len(heads)):
        for j in range(i + 1, len(heads)):
            out.append(f"{heads[i]} {heads[j]}")
    return out[:limit]


def build_query_set(theses: list[Thesis] | None = None) -> list[dict]:
    """Return the full query set as a list of records.

    Each record: {query, thesis_ids, source, filters}. Queries repeated across
    theses are merged into one record whose thesis_ids lists every claimant, so
    the same search is never paid for twice.
    """
    theses = theses if theses is not None else load_theses()
    merged: dict[str, dict] = {}

    def add(query: str, thesis_ids: tuple[str, ...], source: str,
            label: str = "", max_pages: int = DEFAULT_MAX_PAGES) -> None:
        key = query.strip().lower()
        if not key:
            return
        if key in merged:
            existing = merged[key]
            existing["thesis_ids"] = sorted(set(existing["thesis_ids"]) | set(thesis_ids))
            if source not in existing["source"]:
                existing["source"] = f"{existing['source']}+{source}"
            if label and label not in existing["label"]:
                existing["label"] = f"{existing['label']}+{label}" if existing["label"] else label
            existing["max_pages"] = max(existing["max_pages"], max_pages)
            return
        merged[key] = {
            "query": query.strip(),
            "thesis_ids": sorted(thesis_ids),
            "source": source,
            "label": label,
            "max_pages": max_pages,
            "filters": dict(DEFAULT_FILTERS),
        }

    for t in theses:
        for q in t.openalex_queries:
            add(q, (t.id,), "yaml")
        for q in _pair_queries(t):
            add(q, (t.id,), "keyterm_pair")

    for left, right, thesis_ids in CROSS_BLOCK:
        add(f"{left} {right}", thesis_ids, "cross_block")

    for slice_id, query, thesis_ids in BOOLEAN_SLICES:
        add(query, thesis_ids, SLICE_SOURCE, label=slice_id, max_pages=SLICE_MAX_PAGES)

    records = sorted(merged.values(), key=lambda r: (r["thesis_ids"][0], r["query"]))
    return records


def _sha256_json(obj) -> str:
    payload = json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def queryset_provenance(queries: list[dict]) -> dict:
    """What a reviewer needs to reproduce the corpus: version, hashes, search mode."""
    return {
        "queryset_version": HARVEST_QUERYSET_VERSION,
        "queries_sha256": _sha256_json(queries),
        "boolean_slices_sha256": _sha256_json(list(BOOLEAN_SLICES)),
        "n_queries": len(queries),
        "n_boolean_slices": len(BOOLEAN_SLICES),
        "openalex_search_mode": OPENALEX_SEARCH_MODE,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def seed_dois(theses: list[Thesis] | None = None) -> dict[str, list[str]]:
    """DOI → thesis ids that seeded it. These bypass keyword search entirely."""
    theses = theses if theses is not None else load_theses()
    out: dict[str, list[str]] = {}
    for t in theses:
        for doi in t.seed_dois:
            out.setdefault(doi, []).append(t.id)
    return out


def run(out_dir: Path | None = None, theses: list[Thesis] | None = None) -> Path:
    """Write harvest/queries.json and return its path."""
    theses = theses if theses is not None else load_theses()
    target_dir = Path(out_dir) if out_dir else HARVEST_DIR
    target_dir.mkdir(parents=True, exist_ok=True)

    queries = build_query_set(theses)
    seeds = seed_dois(theses)
    payload = {
        "n_queries": len(queries),
        "n_seed_dois": len(seeds),
        "n_boolean_slices": len(BOOLEAN_SLICES),
        "provenance": queryset_provenance(queries),
        "default_filters": DEFAULT_FILTERS,
        "note": ("is_oa is deliberately NOT filtered: the coverage diagnostic needs "
                 "the worldwide hit count, including works we cannot download."),
        "seed_dois": seeds,
        "queries": queries,
    }
    path = target_dir / "queries.json"
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info("Wrote %d queries and %d seed DOIs → %s", len(queries), len(seeds), path)
    return path


if __name__ == "__main__":  # pragma: no cover
    logging.basicConfig(level=logging.INFO, format="%(levelname)-8s %(message)s")
    run()
