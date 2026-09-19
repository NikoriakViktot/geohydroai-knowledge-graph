"""Adjudicate relations from OpenAlex abstracts, without ingesting a single PDF.

The full-text path needs GROBID, Ollama and ~8 hours. This one needs a network
connection and about half an hour, and it answers the question the manuscript
actually needs answering first: *which published work bears on each thesis?*

Everything downstream is unchanged — the same prompt, the same JSON parser, the
same verbatim quote verification, the same paper-level collapsing. Only the
source of the text differs, and that difference is recorded rather than glossed:

* `evidence_level = "abstract"` on every row;
* `quote_source_authority = "openalex_inverted_index"`, because
  `harvest_openalex.reconstruct_abstract` rebuilds the abstract from an inverted
  index — word order faithful, punctuation and spacing approximate. A verified
  quote here proves the model copied from the string OpenAlex gave us, not that
  the string is verbatim from the publisher.

`paper_id` is `doi_to_slug(doi)`, which is exactly the id `harvest_ingest` will
assign when the PDF eventually lands. That one choice is what makes the later
full-text pass an **upgrade** of these rows rather than a duplicate of them.
"""
from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

from src.paper_3._utils import HARVEST_DIR, OUT_DIR, doi_to_slug
from src.paper_3.evidence import EvidencePassage, build_passages
from src.paper_3.theses import Thesis, load_theses

logger = logging.getLogger(__name__)

EVIDENCE_LEVEL = "abstract"
QUOTE_SOURCE_AUTHORITY = "openalex_inverted_index"

CANDIDATES_FILE = "thesis_candidates_metadata.parquet"

#: Default screening depth per thesis. A screening budget, not a
#: systematic-review completeness criterion — see PAPER_3_GAP_PLAN.md §4.2.
TOP_N_PER_THESIS = 15

#: Below this an abstract has nothing quotable: `evidence.MIN_QUOTE_CHARS` is 25
#: and a quote has to be a whole sentence.
MIN_ABSTRACT_CHARS = 200

#: Six, not twelve. A 3 000-character abstract is ~12 sentences; offering more
#: than six passages would cover most of it and make the "a quote spanning two
#: passages matches neither" guard unreachable.
MAX_PASSAGES = 6

#: Distinct key-term families that admit an abstract when the primary family is
#: absent. The primary families were calibrated on full text, where a long paper
#: may contain a multi-word conjunction like "joint use" somewhere; an abstract
#: of ~2 000 characters essentially never does. Three *distinct* families is a
#: weaker signal than the primary one, and it is applied uniformly to all 24
#: theses rather than to the ones whose counts looked disappointing.
ABSTRACT_FALLBACK_FAMILIES = 3


def build_abstract_passages(
    title: str,
    abstract: str,
    paper_id: str,
    doi: str,
    key_terms: tuple[tuple[str, ...], ...],
    negative_terms: tuple[str, ...] = (),
    max_passages: int = MAX_PASSAGES,
) -> list[EvidencePassage]:
    """Passages from an abstract, using the same selector as full text.

    `context=0`, unlike the full-text path's ±1 sentence: in a text this short,
    context windows would overlap so heavily that adjacent passages would become
    near-duplicates and the anti-splicing check would stop discriminating.
    """
    sections = {}
    if title:
        sections["title"] = title.strip()
    if abstract:
        sections["abstract"] = abstract.strip()
    if not sections:
        return []

    return build_passages(
        sections,
        paper_id=paper_id,
        source_file=f"{QUOTE_SOURCE_AUTHORITY}:{doi}",
        key_terms=key_terms,
        negative_terms=negative_terms,
        max_passages=max_passages,
        context=0,
        min_families=1,
    )


# ── candidate selection ───────────────────────────────────────────────────────

def prefilter_abstract(thesis: Thesis, title: str, abstract: str
                       ) -> tuple[bool, str, float]:
    """Metadata analogue of `retrieve.prefilter`. Same (pass, reason, score) shape.

    Uses `Thesis.is_on_topic` unchanged, so the topical test is *identical* to
    the one the full-text path applies — only the text it runs on is shorter.
    """
    if not abstract or len(abstract) < MIN_ABSTRACT_CHARS:
        return False, (f"abstract missing or shorter than {MIN_ABSTRACT_CHARS} "
                       f"characters ({len(abstract or '')})"), 0.0

    text = f"{title} {abstract}"
    families = thesis.families_hit(text)
    primary = thesis.primary_hit(text)

    if not thesis.is_on_topic(text, fallback_families=ABSTRACT_FALLBACK_FAMILIES):
        return False, (f"not on topic: primary family "
                       f"{'hit' if primary else 'missed'}, "
                       f"{families} family/families total "
                       f"(needs primary + 1, or {ABSTRACT_FALLBACK_FAMILIES} "
                       f"families without it)"), 0.0

    if thesis.has_negative(text) and families < 3:
        return False, f"negative term with only {families} families", 0.0

    # The primary family is the stronger signal, so it scores higher. A
    # fallback match is admitted but ranked below a direct one.
    score = 2.0 * families + (2.0 if primary else 0.0) \
        + min(len(abstract), 4000) / 2000.0
    route = "primary family" if primary else \
        f"{ABSTRACT_FALLBACK_FAMILIES}-family fallback (no primary)"
    return True, f"{families} key-term families in title+abstract, via {route}", \
        round(score, 3)


CANDIDATE_COLUMNS = [
    "thesis_id", "paper_id", "doi", "title", "year", "journal",
    "stage_found", "retrieval_origin", "citation_depth", "is_positive_control",
    "in_existing_corpus", "evidence_level", "has_abstract", "abstract_chars",
    "semantic_best_distance", "n_semantic_chunks", "n_keyterm_families_hit",
    "cited_by_count", "n_query_hits",
    "prefilter_pass", "prefilter_reason", "prefilter_score",
]


def build_candidates(
    harvest: pd.DataFrame | None = None,
    theses: list[Thesis] | None = None,
    out_dir: Path | None = None,
    top_n_per_thesis: int = TOP_N_PER_THESIS,
    corpus_dois: set[str] | None = None,
) -> pd.DataFrame:
    """Turn harvested metadata into per-thesis candidates.

    Rejected rows are kept with `prefilter_pass=False` and a reason, exactly as
    `retrieve.run` does, so every count in the matrix stays reconstructible.
    """
    theses = theses if theses is not None else load_theses()
    target = Path(out_dir) if out_dir else OUT_DIR

    if harvest is None:
        path = target / "harvest" / "harvest_candidates.parquet"
        if not path.exists():
            path = HARVEST_DIR / "harvest_candidates.parquet"
        if not path.exists():
            raise FileNotFoundError(
                f"{path} missing — run `--step discover` first")
        harvest = pd.read_parquet(path)

    if corpus_dois is None:
        corpus_dois = _corpus_dois(target)

    by_id = {t.id: t for t in theses}
    rows: list[dict] = []

    for row in harvest.itertuples():
        abstract = str(getattr(row, "abstract", "") or "")
        title = str(getattr(row, "title", "") or "")
        doi = str(getattr(row, "doi", "") or "")
        if not doi:
            continue

        # Parquet returns this as a numpy array, whose truthiness is ambiguous,
        # so it is converted before any boolean test touches it.
        matched = getattr(row, "matched_thesis_ids", None)
        for thesis_id in ([] if matched is None else list(matched)):
            thesis = by_id.get(thesis_id)
            if thesis is None:
                continue
            passed, reason, score = prefilter_abstract(thesis, title, abstract)
            rows.append({
                "thesis_id": thesis_id,
                "paper_id": doi_to_slug(doi),
                "doi": doi,
                "title": title,
                "year": getattr(row, "year", None),
                "journal": str(getattr(row, "journal", "") or ""),
                "stage_found": "metadata",
                "retrieval_origin": "openalex_discovery",
                "citation_depth": 0,
                "is_positive_control": bool(getattr(row, "is_seed", False)),
                # Whether this paper was already in the knowledge graph. Without
                # it, a corpus of 3 680 papers could be quietly replaced by a
                # fresh OpenAlex search and nobody would see it happen.
                "in_existing_corpus": doi in corpus_dois,
                "evidence_level": EVIDENCE_LEVEL,
                "has_abstract": len(abstract) >= MIN_ABSTRACT_CHARS,
                "abstract_chars": len(abstract),
                "semantic_best_distance": None,
                "n_semantic_chunks": 0,
                "n_keyterm_families_hit": thesis.families_hit(f"{title} {abstract}"),
                "cited_by_count": getattr(row, "cited_by_count", 0) or 0,
                "n_query_hits": getattr(row, "n_query_hits", 0) or 0,
                "prefilter_pass": passed,
                "prefilter_reason": reason,
                "prefilter_score": score,
            })

    frame = pd.DataFrame(rows, columns=CANDIDATE_COLUMNS)
    if frame.empty:
        logger.error("no candidates built — is harvest_candidates.parquet empty?")
        return frame

    # Keep the top N passing candidates per thesis, but keep every rejected row
    # too: the coverage diagnostic needs to know what was looked at and dropped.
    kept: list[pd.DataFrame] = []
    for thesis_id, group in frame.groupby("thesis_id"):
        passing = group[group["prefilter_pass"]].nlargest(
            top_n_per_thesis, ["prefilter_score", "cited_by_count"])
        rejected = group[~group["prefilter_pass"]]
        kept.append(pd.concat([passing, rejected]))
    frame = pd.concat(kept).reset_index(drop=True)

    target.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(target / CANDIDATES_FILE, index=False)

    logger.info("Metadata candidates: %d rows, %d pass prefilter across %d theses",
                len(frame), int(frame["prefilter_pass"].sum()),
                frame["thesis_id"].nunique())
    logger.info("  already in the knowledge graph: %d of %d distinct papers",
                frame.loc[frame["in_existing_corpus"], "paper_id"].nunique(),
                frame["paper_id"].nunique())
    no_abstract = frame[~frame["has_abstract"]]["paper_id"].nunique()
    if no_abstract:
        logger.warning("  %d distinct paper(s) have no usable abstract — a "
                       "paywall must not read as a literature gap; this is "
                       "counted per thesis in the coverage diagnostic",
                       no_abstract)
    return frame


def _corpus_dois(out_dir: Path) -> set[str]:
    """DOIs already in the knowledge graph, for the `in_existing_corpus` flag."""
    from src.paper_3._utils import normalize_doi

    path = Path(out_dir) / "corpus_index.parquet"
    if not path.exists():
        logger.warning("corpus_index.parquet missing — in_existing_corpus will "
                       "be false everywhere")
        return set()
    try:
        index = pd.read_parquet(path, columns=["doi"])
    except Exception as exc:
        logger.warning("corpus_index unreadable: %s", exc)
        return set()
    return {normalize_doi(d) for d in index["doi"] if d}


def abstracts_missing_by_thesis(candidates: pd.DataFrame) -> dict[str, float]:
    """Share of each thesis's candidates with no usable abstract.

    Roughly a quarter to a third of OpenAlex works carry no
    `abstract_inverted_index` because the publisher strips it. Those papers
    cannot be adjudicated here and would otherwise vanish from the counts,
    reading identically to "no such literature exists".
    """
    if candidates.empty:
        return {}
    out: dict[str, float] = {}
    for thesis_id, group in candidates.groupby("thesis_id"):
        out[str(thesis_id)] = round(1.0 - group["has_abstract"].mean(), 4)
    return out


# ── the step ──────────────────────────────────────────────────────────────────

def run(
    theses: list[Thesis] | None = None,
    out_dir: Path | None = None,
    llm: str = "gemini",
    limit: int | None = None,
    top_n_per_thesis: int = TOP_N_PER_THESIS,
    delay: float | None = None,
) -> Path:
    """Adjudicate every passing metadata candidate. Resumable, append-only."""
    from src.paper_3 import classify_relation

    theses = theses if theses is not None else load_theses()
    target = Path(out_dir) if out_dir else OUT_DIR

    candidates_path = target / CANDIDATES_FILE
    if not candidates_path.exists():
        build_candidates(theses=theses, out_dir=target,
                         top_n_per_thesis=top_n_per_thesis)
    candidates = pd.read_parquet(candidates_path)

    by_id = {t.id: t for t in theses}

    def passages_for(row) -> list[EvidencePassage]:
        thesis = by_id.get(row.thesis_id)
        if thesis is None:
            return []
        return build_abstract_passages(
            title=str(getattr(row, "title", "") or ""),
            abstract=_abstract_for(candidates, row),
            paper_id=row.paper_id,
            doi=row.doi,
            key_terms=thesis.key_terms,
            negative_terms=thesis.negative_terms,
        )

    return classify_relation.run(
        candidates=candidates,
        theses=theses,
        out_dir=target,
        llm=llm,
        limit=limit,
        evidence_level=EVIDENCE_LEVEL,
        quote_source_authority=QUOTE_SOURCE_AUTHORITY,
        passage_builder=passages_for,
        **({"delay": delay} if delay is not None else {}),
    )


_ABSTRACTS: dict[str, str] | None = None


def _abstract_for(candidates: pd.DataFrame, row) -> str:
    """Abstract text for a candidate row, read back from the harvest once."""
    global _ABSTRACTS
    if _ABSTRACTS is None:
        path = HARVEST_DIR / "harvest_candidates.parquet"
        if path.exists():
            harvest = pd.read_parquet(path, columns=["doi", "abstract"])
            _ABSTRACTS = {str(d): str(a or "")
                          for d, a in zip(harvest["doi"], harvest["abstract"])}
        else:
            _ABSTRACTS = {}
    return _ABSTRACTS.get(str(row.doi), "")
