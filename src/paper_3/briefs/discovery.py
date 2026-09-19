"""Layer 1 — find the candidates, from both corpora, without mixing them.

Two passes over two different things:

* **new_harvest** — 9 148 OpenAlex abstracts. Breadth. Answers *which methods
  exist at all*, including everything the flood-oriented corpus never indexed.
* **old_fulltext** — 3 675 normalised papers. Depth. These are the only records
  that can later carry a number, so they are the ones worth reading in full.

Nothing here calls a model. Screening is the same key-term machinery the thesis
path uses, so a candidate's admission is reproducible and arguable rather than
a model's opinion.

The output ranks full-text candidates for reading, because reading is the scarce
resource: a paper that is on topic, mentions a metric, and names a sensor is
worth opening before one that merely mentions the topic.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

import pandas as pd

from src.paper_3._utils import (
    HARVEST_DIR,
    OUT_DIR,
    doi_to_slug,
    load_paper_json,
    normalize_doi,
    paper_sections,
)
from src.paper_3.briefs.topics import Topic, load_topics

logger = logging.getLogger(__name__)

BRIEFS_DIR_NAME = "briefs"
CANDIDATES_FILE = "brief_candidates.parquet"
COVERAGE_FILE = "brief_source_coverage.csv"

MIN_ABSTRACT_CHARS = 200

#: Full text needs a density test, not a presence test. Over thirty pages almost
#: any paper mentions "reference frame" or "elevation" once, so presence admitted
#: flood-mapping papers as top candidates for the geoid topic. A paper that is
#: actually about an aspect returns to its vocabulary repeatedly.
MIN_PRIMARY_OCCURRENCES = 4
MIN_PRIMARY_PER_10K = 1.5

#: A document-wide density test punishes exactly the papers most worth reading:
#: a good geodesy paper can carry one dense Methods paragraph and mention the
#: vocabulary nowhere else, so its global density is low. So a paper is admitted
#: on EITHER whole-document density OR the density of its best window.
CHUNK_CHARS = 2_000
MIN_BEST_CHUNK_PER_10K = 8.0


def primary_occurrences(topic: Topic, text: str) -> int:
    """How many times the topic's distinguishing vocabulary actually appears."""
    low = (text or "").lower()
    return sum(low.count(term.lower()) for term in topic.primary_family)


def best_chunk_density(topic: Topic, text: str,
                       chunk_chars: int = CHUNK_CHARS) -> float:
    """Highest primary-term density in any single window, per 10k characters.

    Windows step by half their length so a paragraph split across a boundary is
    still seen whole by the neighbouring window.
    """
    if not text:
        return 0.0
    low = text.lower()
    step = max(chunk_chars // 2, 1)
    scale = 10_000.0 / chunk_chars
    best = 0.0
    for start in range(0, max(len(low) - chunk_chars, 0) + 1, step):
        window = low[start:start + chunk_chars]
        hits = sum(window.count(term.lower()) for term in topic.primary_family)
        best = max(best, hits * scale)
    return round(best, 3)


def cooccurring_passages(topic: Topic, text: str,
                         chunk_chars: int = CHUNK_CHARS) -> int:
    """Windows where at least two DIFFERENT key-term families co-occur.

    A better precision filter than any global count: "ICESat-2" and "geoid" both
    appearing somewhere in a paper means little; both appearing in the same two
    thousand characters means the paper is relating them.
    """
    if not text:
        return 0
    low = text.lower()
    step = max(chunk_chars // 2, 1)
    found = 0
    for start in range(0, max(len(low) - chunk_chars, 0) + 1, step):
        window = low[start:start + chunk_chars]
        families = sum(1 for fam in topic.key_terms
                       if any(term.lower() in window for term in fam))
        if families >= 2:
            found += 1
    return found

#: Signals that a full text probably carries a number worth extracting. Used only
#: to rank reading order, never to admit or reject.
_METRIC_HINT = re.compile(
    r"\b(rmse|mae|nmad|bias|standard deviation|r\.?m\.?s\.?e|"
    r"mean absolute error|median absolute)\b", re.IGNORECASE)
_NUMBER_HINT = re.compile(r"[-+]?\d+(?:[.,]\d+)?\s*(?:cm|mm|m\b|km|%)")

CANDIDATE_COLUMNS = [
    "topic", "paper_id", "doi", "title", "year", "journal",
    "source_corpus", "evidence_level", "max_role",
    "n_families_hit", "primary_hit", "n_primary_occurrences", "primary_per_10k",
    "best_chunk_per_10k", "n_cooccurring_passages", "admitted_by",
    "has_metric_hint", "n_number_hints",
    "chars", "read_priority", "screen_pass", "screen_reason",
]


# ── new harvest: abstracts ────────────────────────────────────────────────────

def screen_abstracts(topics: list[Topic], harvest: pd.DataFrame) -> list[dict]:
    """Screen every harvested abstract against every topic."""
    rows: list[dict] = []
    for row in harvest.itertuples():
        doi = normalize_doi(str(getattr(row, "doi", "") or ""))
        if not doi:
            continue
        title = str(getattr(row, "title", "") or "")
        abstract = str(getattr(row, "abstract", "") or "")
        text = f"{title} {abstract}"

        for topic in topics:
            families = topic.families_hit(text)
            primary = topic.primary_hit(text)

            if len(abstract) < MIN_ABSTRACT_CHARS:
                ok, reason = False, f"abstract shorter than {MIN_ABSTRACT_CHARS} chars"
            elif not topic.is_on_topic(text):
                ok, reason = False, (f"off topic: primary "
                                     f"{'hit' if primary else 'missed'}, "
                                     f"{families} families")
            elif topic.has_negative(text) and families < 3:
                ok, reason = False, "negative term with weak topical signal"
            else:
                ok, reason = True, f"{families} families, primary hit"

            rows.append({
                "topic": topic.id,
                "paper_id": doi_to_slug(doi),
                "doi": doi,
                "title": title,
                "year": getattr(row, "year", None),
                "journal": str(getattr(row, "journal", "") or ""),
                "source_corpus": "new_harvest",
                "evidence_level": "abstract",
                # The cap, applied here so it is visible in the candidate table
                # rather than appearing later as if the extractor decided it.
                "max_role": topic.abstract_role,
                "n_families_hit": families,
                "primary_hit": primary,
                "n_primary_occurrences": primary_occurrences(topic, text),
                "primary_per_10k": 0.0,
                "best_chunk_per_10k": 0.0,
                "n_cooccurring_passages": 0,
                "admitted_by": "abstract_screen",
                "has_metric_hint": bool(_METRIC_HINT.search(text)),
                "n_number_hints": len(_NUMBER_HINT.findall(text)),
                "chars": len(abstract),
                "read_priority": 0.0,
                "screen_pass": ok,
                "screen_reason": reason,
            })
    return rows


# ── old corpus: full text ─────────────────────────────────────────────────────

def screen_fulltext(topics: list[Topic], index: pd.DataFrame,
                    limit: int | None = None, log_every: int = 500) -> list[dict]:
    """Screen the normalised corpus once, against all topics in a single pass."""
    rows: list[dict] = []
    papers = index[index["has_normalized"]] if "has_normalized" in index else index
    if limit:
        papers = papers.head(limit)

    for i, record in enumerate(papers.itertuples(), 1):
        paper = load_paper_json(record.paper_id)
        if not paper:
            continue
        sections = paper_sections(paper)
        text = " ".join(sections.values())
        if not text:
            continue

        metric_hint = bool(_METRIC_HINT.search(text))
        number_hints = len(_NUMBER_HINT.findall(text))

        per_10k_divisor = max(len(text) / 10_000.0, 1.0)

        for topic in topics:
            families = topic.families_hit(text)
            primary = topic.primary_hit(text)
            occurrences = primary_occurrences(topic, text)
            density = round(occurrences / per_10k_divisor, 3)

            if not topic.is_on_topic(text):
                continue
            if topic.has_negative(text) and families < 3:
                continue
            # The density gate. Presence alone admitted flood-mapping papers to
            # the geoid topic because a long paper says "reference frame" once.
            if occurrences < MIN_PRIMARY_OCCURRENCES:
                continue

            chunk_density = best_chunk_density(topic, text)
            cooccurring = cooccurring_passages(topic, text)

            # Either the paper is about this throughout, or it has a passage
            # that is densely about it. A Methods section counts.
            if density >= MIN_PRIMARY_PER_10K:
                admitted_by = "document_density"
            elif chunk_density >= MIN_BEST_CHUNK_PER_10K:
                admitted_by = "best_chunk_density"
            else:
                continue

            # Co-occurrence is required where the topic says so: two concepts in
            # one window beats either concept anywhere in the document.
            if topic.require_cooccurrence and cooccurring == 0:
                continue

            reason = (f"{families} families, {occurrences} primary mentions "
                      f"({density}/10k doc, {chunk_density}/10k best chunk, "
                      f"{cooccurring} co-occurring passages)")

            rows.append({
                "topic": topic.id,
                "paper_id": str(record.paper_id),
                "doi": normalize_doi(str(getattr(record, "doi", "") or "")),
                "title": str(getattr(record, "title", "") or ""),
                "year": getattr(record, "year", None),
                "journal": str(getattr(record, "journal", "") or ""),
                "source_corpus": "old_fulltext",
                "evidence_level": "full_text",
                "max_role": "quantitative_support",
                "n_families_hit": families,
                "primary_hit": primary,
                "n_primary_occurrences": occurrences,
                "primary_per_10k": density,
                "best_chunk_per_10k": chunk_density,
                "n_cooccurring_passages": cooccurring,
                "admitted_by": admitted_by,
                "has_metric_hint": metric_hint,
                "n_number_hints": number_hints,
                "chars": len(text),
                "read_priority": 0.0,
                "screen_pass": True,
                "screen_reason": reason,
            })

        if log_every and i % log_every == 0:
            logger.info("  full-text screen … %d/%d papers", i, len(papers))
    return rows


def rank_for_reading(frame: pd.DataFrame) -> pd.DataFrame:
    """Score full-text candidates by how likely they are to carry a usable number.

    Reading is the scarce resource. A paper that is strongly on topic, names a
    metric and quotes figures in metres is worth opening before one that merely
    mentions the subject. Ranking only orders the queue — it never admits or
    rejects, so nothing is lost by a low score.
    """
    if frame.empty:
        return frame
    frame = frame.copy()
    full = frame["evidence_level"] == "full_text"
    # Density leads. Family counts saturate at three and gave every long paper
    # the same ceiling score, which is how flood papers reached the top of the
    # geoid queue.
    frame.loc[full, "read_priority"] = (
        3.0 * frame.loc[full, "primary_per_10k"].clip(upper=20)
        + 1.5 * frame.loc[full, "best_chunk_per_10k"].clip(upper=40)
        + 2.0 * frame.loc[full, "n_cooccurring_passages"].clip(upper=10)
        + 0.5 * frame.loc[full, "n_primary_occurrences"].clip(upper=40)
        + 3.0 * frame.loc[full, "has_metric_hint"].astype(float)
    ).round(3)
    return frame


# ── coverage ──────────────────────────────────────────────────────────────────

def coverage(frame: pd.DataFrame, topics: list[Topic]) -> pd.DataFrame:
    """Where each brief is strong and where the literature coverage is thin.

    Reported per topic so that a thin brief is visibly thin rather than looking
    like a settled answer with few rows.
    """
    rows = []
    for topic in topics:
        group = frame[frame["topic"] == topic.id]
        abstracts = group[group["evidence_level"] == "abstract"]
        fulltext = group[group["evidence_level"] == "full_text"]
        screened = abstracts[abstracts["screen_pass"]]
        rows.append({
            "topic": topic.id,
            "name": topic.name,
            "abstract_candidates": len(abstracts),
            "abstract_screened": len(screened),
            "fulltext_available": len(fulltext),
            "fulltext_with_metric_hint": int(fulltext["has_metric_hint"].sum())
            if len(fulltext) else 0,
            "abstract_role_cap": topic.abstract_role,
            # Filled in by the extraction step; present here so the file has a
            # stable shape from the first run.
            "fulltext_read": 0,
            "quantitative_records": 0,
            "verified_quotes": 0,
        })
    return pd.DataFrame(rows)


# ── the step ──────────────────────────────────────────────────────────────────

def run(topics: list[Topic] | None = None, out_dir: Path | None = None,
        fulltext_limit: int | None = None) -> pd.DataFrame:
    """Screen both corpora and write the candidate table plus coverage."""
    from src.paper_3.corpus_index import load_index

    topics = topics if topics is not None else load_topics()
    target = Path(out_dir) if out_dir else OUT_DIR
    briefs_dir = target / BRIEFS_DIR_NAME
    briefs_dir.mkdir(parents=True, exist_ok=True)

    harvest_path = target / "harvest" / "harvest_candidates.parquet"
    if not harvest_path.exists():
        harvest_path = HARVEST_DIR / "harvest_candidates.parquet"
    rows: list[dict] = []

    if harvest_path.exists():
        harvest = pd.read_parquet(
            harvest_path, columns=["doi", "title", "abstract", "year", "journal"])
        logger.info("Screening %d harvested abstracts against %d topics",
                    len(harvest), len(topics))
        rows.extend(screen_abstracts(topics, harvest))
    else:
        logger.warning("%s missing — no abstract breadth this run", harvest_path)

    logger.info("Screening the normalised corpus (full text)")
    rows.extend(screen_fulltext(topics, load_index(target), limit=fulltext_limit))

    frame = pd.DataFrame(rows, columns=CANDIDATE_COLUMNS)
    # The harvest stores year as an integer and corpus_index as a string, so the
    # mixed column cannot be written to parquet. Normalised to text rather than
    # to a number, because a missing year must stay distinguishable from year 0.
    frame["year"] = frame["year"].apply(
        lambda v: "" if pd.isna(v) else str(v).strip().split(".")[0])
    frame = rank_for_reading(frame)
    frame = frame.sort_values(["topic", "evidence_level", "read_priority"],
                              ascending=[True, True, False])
    frame.to_parquet(briefs_dir / CANDIDATES_FILE, index=False)

    cov = coverage(frame, topics)
    cov.to_csv(briefs_dir / COVERAGE_FILE, index=False)

    for row in cov.itertuples():
        logger.info("Topic %s (%s): %d abstracts screened in, %d full-text "
                    "papers (%d with a metric hint), abstract cap = %s",
                    row.topic, row.name, row.abstract_screened,
                    row.fulltext_available, row.fulltext_with_metric_hint,
                    row.abstract_role_cap)
    return frame
