"""Corpus index, citation edges and the corpus-coverage diagnostic.

Two jobs:

1. `build_index()` — one row per paper: identity (paper_id / doi / slug), which
   artefacts exist on disk, and which cohort it belongs to. Everything downstream
   resolves papers through this rather than guessing at filenames.

2. `build_coverage()` — the honesty diagnostic. For each thesis it compares what
   OpenAlex says exists worldwide against what this corpus actually holds, and
   labels the result adequate / thin / absent. Without it, "no supporting evidence
   found" is ambiguous between *the literature is silent* and *we could not see
   the literature*, and only the first of those is a research gap.

Citation edges come from `data/parquet/references.parquet`
(source_paper_id → referenced_doi, 320 633 rows). Note that
`data/analytics/references.parquet` is a different, currently empty table with a
reference-hash schema that cannot be resolved to DOIs — do not use it.
"""
from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

from src.paper_3._utils import (
    PARQUET_DIR,
    COHORT,
    ENRICHED_DIR,
    NORMALIZED_DIR,
    OUT_DIR,
    PAPER_JSON_DIR,
    PROJECT_ROOT,
    XML_DIR,
    doi_to_slug,
    get_con,
    normalize_doi,
)
from src.paper_3.theses import Thesis, load_theses

logger = logging.getLogger(__name__)

#: The populated citation table. Schema: source_paper_id, referenced_doi,
#: referenced_openalex_id, cited_by_count.
REFERENCES_PARQUET = PARQUET_DIR / "references.parquet"

INDEX_COLUMNS = [
    "paper_id", "doi", "slug", "title", "year", "journal", "cited_by_count",
    "cohort", "has_normalized", "has_enriched", "has_paper_json", "has_xml",
]

COVERAGE_COLUMNS = [
    "thesis_id", "openalex_worldwide_hits", "openalex_hits_in_corpus",
    "coverage_ratio", "n_papers_keyterm_fulltext", "n_chunks_matching",
    "best_semantic_distance", "n_oa_downloadable", "n_paywalled_skipped",
    "n_grobid_failed", "kg_expansion_available", "corpus_adequacy",
]

# ── adequacy thresholds ───────────────────────────────────────────────────────
# Stated here and printed in GAP_MATRIX.md so a reviewer can disagree with them
# rather than having to infer them from the output.
ABSENT_WORLDWIDE_MIN = 20     # enough published work that silence needs explaining
ABSENT_IN_CORPUS_MAX = 3      # but we hold almost none of it
THIN_COVERAGE_RATIO = 0.25
THIN_KEYTERM_PAPERS = 5

THRESHOLDS_NOTE = (
    "**Coverage thresholds.** `absent`: OpenAlex reports at least "
    f"{ABSENT_WORLDWIDE_MIN} candidate works while fewer than {ABSENT_IN_CORPUS_MAX} "
    "are present in this corpus, or no paper in the corpus hits two key-term "
    f"families in full text. `thin`: coverage ratio below {THIN_COVERAGE_RATIO} or "
    f"fewer than {THIN_KEYTERM_PAPERS} papers with full-text key-term hits. "
    "`adequate`: otherwise."
)


# ── index ─────────────────────────────────────────────────────────────────────

def _cohort_dois(out_dir: Path) -> set[str]:
    path = Path(out_dir) / "cohort_paper_3.csv"
    if not path.exists():
        return set()
    try:
        return {normalize_doi(d) for d in pd.read_csv(path)["doi"].dropna()}
    except Exception as exc:
        logger.warning("cohort file unreadable: %s", exc)
        return set()


def build_index(out_dir: Path | None = None, write: bool = True) -> pd.DataFrame:
    """One row per corpus paper, with artefact presence and cohort tag."""
    target = Path(out_dir) if out_dir else OUT_DIR
    papers_path = PARQUET_DIR / "papers.parquet"
    if not papers_path.exists():
        raise FileNotFoundError(
            f"{papers_path} missing — run `python -m src.enrichment.build_parquet_layer`")

    papers = pd.read_parquet(papers_path)
    cohort_dois = _cohort_dois(target)

    rows = []
    for p in papers.itertuples():
        doi = normalize_doi(getattr(p, "doi", "") or "")
        pid = str(p.paper_id)
        rows.append({
            "paper_id": pid,
            "doi": doi,
            "slug": doi_to_slug(doi) if doi else "",
            "title": getattr(p, "title", "") or "",
            "year": getattr(p, "year", None),
            "journal": getattr(p, "journal", "") or "",
            "cited_by_count": getattr(p, "cited_by_count", 0) or 0,
            "cohort": COHORT if doi and doi in cohort_dois else "base",
            "has_normalized": (NORMALIZED_DIR / f"{pid}.json").exists(),
            "has_enriched": (ENRICHED_DIR / f"{pid}.json").exists(),
            "has_paper_json": (PAPER_JSON_DIR / f"{pid}.tei.paper.json").exists(),
            "has_xml": (XML_DIR / f"{pid}.tei.xml").exists(),
        })

    index = pd.DataFrame(rows, columns=INDEX_COLUMNS)
    if write:
        target.mkdir(parents=True, exist_ok=True)
        index.to_parquet(target / "corpus_index.parquet", index=False)
    logger.info("Corpus index: %d papers (%d in cohort %s, %d with normalised text)",
                len(index), int((index["cohort"] == COHORT).sum()), COHORT,
                int(index["has_normalized"].sum()))
    return index


def load_index(out_dir: Path | None = None) -> pd.DataFrame:
    target = Path(out_dir) if out_dir else OUT_DIR
    path = target / "corpus_index.parquet"
    if not path.exists():
        return build_index(target, write=True)
    return pd.read_parquet(path)


def dois_in_corpus(index: pd.DataFrame | None = None) -> set[str]:
    index = index if index is not None else load_index()
    return {d for d in index["doi"] if d}


# ── citation edges ────────────────────────────────────────────────────────────

def load_citation_edges() -> pd.DataFrame:
    """source_paper_id → referenced_doi, DOI-bearing rows only.

    Roughly two thirds of reference rows carry no DOI (GROBID could not parse one).
    Those are dropped rather than guessed at, and the count is logged so the loss
    is visible.
    """
    if not REFERENCES_PARQUET.exists():
        logger.warning("%s missing — citation expansion unavailable", REFERENCES_PARQUET)
        return pd.DataFrame(columns=["source_paper_id", "referenced_doi"])

    edges = pd.read_parquet(REFERENCES_PARQUET,
                            columns=["source_paper_id", "referenced_doi"])
    total = len(edges)
    edges = edges.dropna(subset=["referenced_doi"])
    edges = edges.assign(referenced_doi=edges["referenced_doi"].map(normalize_doi))
    edges = edges[edges["referenced_doi"].str.startswith("10.")]
    logger.info("Citation edges: %d of %d rows carry a DOI (%d dropped as unresolvable)",
                len(edges), total, total - len(edges))
    return edges.drop_duplicates()


def expand_by_citation(
    seed_paper_ids: list[str],
    index: pd.DataFrame | None = None,
    edges: pd.DataFrame | None = None,
    direction: str = "both",
) -> pd.DataFrame:
    """Papers **one** citation hop from the seeds, in either direction.

    Depth is fixed at one and is not configurable. Two hops through a highly
    cited methodological paper reaches most of the corpus, and a neighbourhood
    that large has stopped being about any particular thesis. A deeper sweep is a
    separate, manual investigation, not part of the automated evidence base.

    Returns columns: paper_id, doi, relation, n_seed_links, where relation is:

      * `references`  — the paper appears in a seed's reference list
                        (the seed cites it; it is intellectual ancestry)
      * `cited_by`    — the paper cites a seed (it is descent)

    Only papers already in the corpus are returned; the rest are the business of
    `uncited_candidates`.
    """
    index = index if index is not None else load_index()
    edges = edges if edges is not None else load_citation_edges()
    seeds = set(seed_paper_ids)
    if edges.empty or not seeds:
        return pd.DataFrame(columns=["paper_id", "doi", "relation", "n_seed_links"])

    doi_to_pid = {d: p for d, p in zip(index["doi"], index["paper_id"]) if d}
    seed_dois = {d for d, p in doi_to_pid.items() if p in seeds}

    found: dict[str, dict] = {}

    def add(pid: str, relation: str) -> None:
        if pid in seeds:
            return
        row = found.setdefault(
            pid, {"paper_id": pid, "doi": "", "relation": relation, "n_seed_links": 0})
        row["n_seed_links"] += 1
        if relation not in row["relation"]:
            row["relation"] = f"{row['relation']}+{relation}"

    if direction in ("both", "backward"):
        # Papers the seeds cite: they are in the seeds' reference lists.
        backward = edges[edges["source_paper_id"].isin(seeds)]
        for doi in backward["referenced_doi"]:
            pid = doi_to_pid.get(doi)
            if pid:
                add(pid, "references")

    if direction in ("both", "forward"):
        # Papers that cite the seeds.
        forward = edges[edges["referenced_doi"].isin(seed_dois)]
        for pid in forward["source_paper_id"]:
            add(str(pid), "cited_by")

    out = pd.DataFrame(list(found.values()))
    if not out.empty:
        out["doi"] = out["paper_id"].map(
            dict(zip(index["paper_id"], index["doi"]))).fillna("")
    return out


def uncited_candidates(
    seed_paper_ids: list[str],
    index: pd.DataFrame | None = None,
    edges: pd.DataFrame | None = None,
    min_links: int = 3,
) -> pd.DataFrame:
    """DOIs the seeds cite repeatedly that the corpus does not hold.

    This is the second-round harvest list: literature-seeded rather than
    keyword-seeded, and therefore the best source of papers our queries missed.
    """
    index = index if index is not None else load_index()
    edges = edges if edges is not None else load_citation_edges()
    if edges.empty or not seed_paper_ids:
        return pd.DataFrame(columns=["doi", "n_seed_links"])

    have = dois_in_corpus(index)
    cited = edges[edges["source_paper_id"].isin(set(seed_paper_ids))]
    counts = (cited[~cited["referenced_doi"].isin(have)]
              .groupby("referenced_doi").size()
              .reset_index(name="n_seed_links")
              .rename(columns={"referenced_doi": "doi"}))
    return counts[counts["n_seed_links"] >= min_links].sort_values(
        "n_seed_links", ascending=False)


# ── coverage diagnostic ───────────────────────────────────────────────────────

def classify_adequacy(
    worldwide: int,
    in_corpus: int,
    n_keyterm_papers: int,
    coverage_ratio: float,
) -> str:
    """adequate | thin | absent — see THRESHOLDS_NOTE."""
    if n_keyterm_papers == 0:
        return "absent"
    if worldwide >= ABSENT_WORLDWIDE_MIN and in_corpus < ABSENT_IN_CORPUS_MAX:
        return "absent"
    if coverage_ratio < THIN_COVERAGE_RATIO or n_keyterm_papers < THIN_KEYTERM_PAPERS:
        return "thin"
    return "adequate"


def count_keyterm_papers(
    theses: list[Thesis],
    index: pd.DataFrame,
    min_other: int = 1,
    limit: int | None = None,
    log_every: int = 500,
) -> dict[str, int]:
    """Per thesis, how many papers are on topic in their own full text.

    On topic means `Thesis.is_on_topic`: the distinguishing concept appears, plus
    at least `min_other` further families. This is the measure that matters — an
    embedding hit says a paper is nearby in topic space, whereas this says the
    paper actually contains the vocabulary the thesis is about. A thesis scoring
    zero here cannot be answered by this corpus, whatever retrieval reports.

    All theses are counted in a single pass over the corpus — reading ~3 700 files
    once rather than once per thesis.
    """
    from src.paper_3._utils import load_paper_json, paper_sections

    rows = index[index["has_normalized"]]
    if limit:
        rows = rows.head(limit)

    counts = {t.id: 0 for t in theses}
    for i, pid in enumerate(rows["paper_id"], 1):
        paper = load_paper_json(pid)
        if not paper:
            continue
        text = " ".join(paper_sections(paper).values())
        if not text:
            continue
        for t in theses:
            if t.is_on_topic(text, min_other=min_other):
                counts[t.id] += 1
        if log_every and i % log_every == 0:
            logger.info("  key-term scan … %d/%d papers", i, len(rows))
    return counts


def build_coverage(
    theses: list[Thesis] | None = None,
    index: pd.DataFrame | None = None,
    worldwide_counts: dict[str, int] | None = None,
    candidates: pd.DataFrame | None = None,
    keyterm_counts: dict[str, int] | None = None,
    semantic_stats: dict[str, dict] | None = None,
    kg_expansion_available: bool = True,
    out_dir: Path | None = None,
    write: bool = True,
) -> pd.DataFrame:
    """Assemble the per-thesis coverage table.

    `worldwide_counts` maps query text → OpenAlex meta.count (written by
    discovery); `candidates` is harvest_candidates.parquet; `keyterm_counts` and
    `semantic_stats` come from the retrieval step. Anything absent degrades to a
    conservative value rather than an optimistic one.
    """
    theses = theses if theses is not None else load_theses()
    index = index if index is not None else load_index(out_dir)
    worldwide_counts = worldwide_counts or {}
    semantic_stats = semantic_stats or {}
    have = dois_in_corpus(index)

    if not keyterm_counts:
        # No retrieval run to borrow from — scan the corpus directly rather than
        # defaulting every thesis to zero, which would mark them all 'absent'.
        logger.info("No key-term counts supplied; scanning the corpus once")
        keyterm_counts = count_keyterm_papers(theses, index)

    rows = []
    for t in theses:
        # Worldwide: the largest single-query count, not the sum. Queries for one
        # thesis overlap heavily, so summing would inflate the denominator and
        # make coverage look worse than it is.
        counts = [worldwide_counts.get(q, 0) for q in t.openalex_queries]
        worldwide = max(counts) if counts else 0

        in_corpus = 0
        n_oa = n_paywalled = 0
        if candidates is not None and not candidates.empty:
            mine = candidates[candidates["matched_thesis_ids"].apply(
                lambda ids: t.id in list(ids))]
            in_corpus = int(mine["doi"].isin(have).sum())
            new = mine[mine.get("drop_reason", "").eq("")] if "drop_reason" in mine else mine
            n_oa = int((new["is_oa"] & new["pdf_url"].ne("")).sum()) if len(new) else 0
            n_paywalled = int(len(new) - n_oa)

        n_keyterm = int(keyterm_counts.get(t.id, 0))
        ratio = (in_corpus / worldwide) if worldwide else 0.0
        stats = semantic_stats.get(t.id, {})

        rows.append({
            "thesis_id": t.id,
            "openalex_worldwide_hits": worldwide,
            "openalex_hits_in_corpus": in_corpus,
            "coverage_ratio": round(ratio, 4),
            "n_papers_keyterm_fulltext": n_keyterm,
            "n_chunks_matching": int(stats.get("n_chunks", 0)),
            "best_semantic_distance": stats.get("best_distance"),
            "n_oa_downloadable": n_oa,
            "n_paywalled_skipped": n_paywalled,
            "n_grobid_failed": 0,
            "kg_expansion_available": kg_expansion_available,
            "corpus_adequacy": classify_adequacy(worldwide, in_corpus, n_keyterm, ratio),
        })

    coverage = pd.DataFrame(rows, columns=COVERAGE_COLUMNS)
    if write:
        target = Path(out_dir) if out_dir else OUT_DIR
        target.mkdir(parents=True, exist_ok=True)
        coverage.to_parquet(target / "corpus_coverage.parquet", index=False)
        coverage.to_csv(target / "CORPUS_COVERAGE.csv", index=False)
    logger.info("Coverage: %s", coverage["corpus_adequacy"].value_counts().to_dict())
    return coverage
