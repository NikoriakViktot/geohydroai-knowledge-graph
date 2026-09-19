"""Slice screening — the denominators behind any absence or prevalence claim.

"None of the Kakhovka studies reports a water-surface gradient" is only a
statement if it comes with how many were looked at. This module counts them:
for each Boolean slice in `harvest_queries.BOOLEAN_SLICES`, every harvested
candidate the slice matched is screened for the attribute vocabularies in
`literature_claims.yaml`, over full text when the paper is in the corpus and over
the OpenAlex abstract otherwise.

It is a keyword screen, ranked for reading — not a reading. `human_checked` is
the column a reader flips; until then a claim built on these numbers is a claim
about the screen, and the L-matrix words it that way.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import yaml

from src.paper_3._utils import (
    HARVEST_DIR,
    OUT_DIR,
    load_paper_json,
    normalize_doi,
    paper_sections,
)
from src.paper_3.harvest_queries import BOOLEAN_SLICES, slice_query

logger = logging.getLogger(__name__)

CLAIMS_PATH = Path(__file__).resolve().parent / "literature_claims.yaml"

SCREENING_STEM = "SLICE_SCREENING"
DENOMINATORS_CSV = "SLICE_DENOMINATORS.csv"

ABSTRACT, FULL_TEXT = "abstract", "full_text"

#: Below this a record has no screenable text (a bare title is not a screen).
MIN_TEXT_CHARS = 200

#: Denominator columns every slice row carries, before the per-attribute `_n`s.
DENOMINATOR_COLUMNS = (
    "slice_id", "worldwide_count", "n_candidates", "n_off_topic", "n_screened",
    "n_fulltext", "n_abstract_only", "screened_fraction", "on_topic_fraction",
)


@dataclass(frozen=True)
class Slice:
    id: str
    attributes: tuple[tuple[str, tuple[str, ...]], ...]
    #: The slice's distinguishing concept. OpenAlex `search=` ranks rather than
    #: enforcing a strict AND, so a Boolean slice returns works that satisfy only
    #: part of it: S1 came back 50 % non-Kakhovka, S4 98 % non-bathymetry. A
    #: denominator containing them would make "0 of 447" look far stronger than
    #: "0 of 239". Same rule as Thesis.primary_family, applied to the slice.
    topic_terms: tuple[str, ...] = ()

    @property
    def attribute_names(self) -> tuple[str, ...]:
        return tuple(name for name, _ in self.attributes)

    def on_topic(self, text: str) -> bool:
        low = text.lower()
        return any(term.lower() in low for term in self.topic_terms)

    def terms(self, name: str) -> tuple[str, ...]:
        for attr, terms in self.attributes:
            if attr == name:
                return terms
        raise KeyError(f"{self.id}: no attribute {name!r}")


def load_slices(path: Path | None = None) -> dict[str, Slice]:
    raw = yaml.safe_load(Path(path or CLAIMS_PATH).read_text(encoding="utf-8")) or {}
    out: dict[str, Slice] = {}
    for slice_id, body in (raw.get("slices") or {}).items():
        attrs = tuple(
            (str(name), tuple(str(t) for t in (terms or [])))
            for name, terms in ((body or {}).get("attributes") or {}).items()
        )
        out[str(slice_id)] = Slice(
            id=str(slice_id), attributes=attrs,
            topic_terms=tuple(str(t) for t in ((body or {}).get("topic_terms") or [])),
        )
    return out


def validate_slices(slices: dict[str, Slice]) -> list[str]:
    known = {sid for sid, _, _ in BOOLEAN_SLICES}
    problems: list[str] = []
    for sid, slc in slices.items():
        if sid not in known:
            problems.append(f"{sid}: not a Boolean slice in harvest_queries.BOOLEAN_SLICES")
        if not slc.attributes:
            problems.append(f"{sid}: no attributes")
        if not slc.topic_terms:
            problems.append(f"{sid}: no topic_terms — without them the denominator "
                            f"counts works the slice is not about")
        for name, terms in slc.attributes:
            if not terms:
                problems.append(f"{sid}.{name}: empty term list")
            if not name.replace("_", "").isalnum() or name != name.lower():
                problems.append(f"{sid}.{name}: attribute names are snake_case")
    return problems


# ── the screen ────────────────────────────────────────────────────────────────

def hits(text: str, terms: tuple[str, ...]) -> int:
    """Case-insensitive occurrence count, summed over the family's terms."""
    low = text.lower()
    return sum(low.count(term.lower()) for term in terms if term)


def screen_text(slc: Slice, text: str) -> dict[str, int]:
    return {name: hits(text, terms) for name, terms in slc.attributes}


def _fulltext(paper_id: str) -> str:
    paper = load_paper_json(paper_id)
    if not paper:
        return ""
    parts = [str(paper.get("title") or ""), str(paper.get("abstract") or "")]
    parts += list(paper_sections(paper).values())
    return "\n".join(p for p in parts if p)


def _paper_ids_by_doi(corpus_index: pd.DataFrame | None) -> dict[str, str]:
    if corpus_index is None or corpus_index.empty or "doi" not in corpus_index:
        return {}
    out: dict[str, str] = {}
    for doi, pid in zip(corpus_index["doi"], corpus_index["paper_id"]):
        doi = normalize_doi(str(doi or ""))
        if doi and doi not in out:
            out[doi] = str(pid)
    return out


def _labels(value) -> set[str]:
    if value is None:
        return set()
    if isinstance(value, str):
        return {value} if value else set()
    try:
        return {str(v) for v in value if str(v)}
    except TypeError:
        return set()


def screen(
    candidates: pd.DataFrame,
    corpus_index: pd.DataFrame | None,
    slices: dict[str, Slice],
    worldwide: dict[str, int] | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Per-paper screening rows and per-slice denominators.

    A harvest made before the slices existed has no `matched_labels` column;
    every slice then screens zero candidates and the denominators say so, which
    is what makes the L-matrix report SLICE_NOT_RUN instead of an absence.
    """
    worldwide = worldwide or {}
    paper_ids = _paper_ids_by_doi(corpus_index)
    has_labels = "matched_labels" in candidates
    if not has_labels:
        logger.warning("harvest_candidates has no matched_labels column — the harvest "
                       "predates the Boolean slices; re-run --step discover --force")

    all_attrs = sorted({name for s in slices.values() for name in s.attribute_names})
    rows: list[dict] = []
    denominators: list[dict] = []

    for sid, slc in sorted(slices.items()):
        if has_labels:
            mask = candidates["matched_labels"].apply(lambda v: sid in _labels(v))
            subset = candidates[mask]
        else:
            subset = candidates.iloc[0:0]

        n_screened = n_full = n_abstract = n_off_topic = 0
        attr_n = {name: 0 for name in slc.attribute_names}
        for r in subset.itertuples():
            doi = normalize_doi(str(getattr(r, "doi", "") or ""))
            pid = paper_ids.get(doi, "")
            text, level = (_fulltext(pid), FULL_TEXT) if pid else ("", ABSTRACT)
            if not text:
                text = f"{getattr(r, 'title', '') or ''}\n{getattr(r, 'abstract', '') or ''}"
                level = ABSTRACT
            # The topic gate is judged on title+abstract for every candidate, so
            # that being in the corpus cannot change whether a work is on topic.
            meta = f"{getattr(r, 'title', '') or ''}\n{getattr(r, 'abstract', '') or ''}"
            on_topic = slc.on_topic(meta) or (level == FULL_TEXT and slc.on_topic(text))
            screened = on_topic and len(text.strip()) >= MIN_TEXT_CHARS
            counts = screen_text(slc, text) if screened else {n: 0 for n in slc.attribute_names}
            if not on_topic:
                n_off_topic += 1
            if screened:
                n_screened += 1
                if level == FULL_TEXT:
                    n_full += 1
                else:
                    n_abstract += 1
                for name, c in counts.items():
                    if c > 0:
                        attr_n[name] += 1
            row = {
                "slice_id": sid,
                "doi": doi,
                "title": getattr(r, "title", "") or "",
                "year": getattr(r, "year", None),
                "in_corpus": bool(pid),
                "evidence_level": level,
                "on_topic": on_topic,
                "screened": screened,
            }
            row.update({f"{name}_hits": counts.get(name, 0) for name in all_attrs})
            row.update({"human_checked": False, "human_note": ""})
            rows.append(row)

        wc = int(worldwide.get(slice_query(sid), 0)) if sid in {s for s, _, _ in BOOLEAN_SLICES} else 0
        den = {
            "slice_id": sid,
            "worldwide_count": wc,
            "n_candidates": int(len(subset)),
            "n_off_topic": n_off_topic,
            "n_screened": n_screened,
            "n_fulltext": n_full,
            "n_abstract_only": n_abstract,
            # Against the worldwide count, which is what the slice could have
            # returned — deliberately not against the on-topic subset, which
            # would hide an over-broad query behind its own filtering.
            "screened_fraction": round(n_screened / wc, 4) if wc else 0.0,
            "on_topic_fraction": round(n_screened / len(subset), 4) if len(subset) else 0.0,
        }
        den.update({f"{name}_n": attr_n.get(name, 0) for name in all_attrs})
        denominators.append(den)

    screening_cols = (["slice_id", "doi", "title", "year", "in_corpus", "evidence_level",
                       "on_topic", "screened"] + [f"{n}_hits" for n in all_attrs]
                      + ["human_checked", "human_note"])
    screening = pd.DataFrame(rows, columns=screening_cols)
    den_cols = list(DENOMINATOR_COLUMNS) + [f"{n}_n" for n in all_attrs]
    return screening, pd.DataFrame(denominators, columns=den_cols)


def run(out_dir: Path | None = None, harvest_dir: Path | None = None,
        slices: dict[str, Slice] | None = None) -> tuple[Path, Path]:
    target = Path(out_dir) if out_dir else OUT_DIR
    harvest = Path(harvest_dir) if harvest_dir else (target / "harvest" if out_dir else HARVEST_DIR)
    slices = slices if slices is not None else load_slices()
    problems = validate_slices(slices)
    if problems:
        raise ValueError("literature_claims.yaml slices: " + "; ".join(problems))

    cand_path = harvest / "harvest_candidates.parquet"
    if not cand_path.exists():
        raise FileNotFoundError(f"{cand_path} missing — run --step discover first")
    candidates = pd.read_parquet(cand_path)

    index_path = target / "corpus_index.parquet"
    corpus_index = pd.read_parquet(index_path) if index_path.exists() else None
    if corpus_index is None:
        logger.warning("corpus_index.parquet missing — every candidate is screened "
                       "on its abstract only")

    worldwide: dict[str, int] = {}
    ww_path = harvest / "worldwide_counts.json"
    if ww_path.exists():
        worldwide = json.loads(ww_path.read_text(encoding="utf-8"))

    screening, denominators = screen(candidates, corpus_index, slices, worldwide)
    target.mkdir(parents=True, exist_ok=True)
    screening.to_parquet(target / f"{SCREENING_STEM}.parquet", index=False)
    screening.to_csv(target / f"{SCREENING_STEM}.csv", index=False)
    den_path = target / DENOMINATORS_CSV
    denominators.to_csv(den_path, index=False)
    for r in denominators.itertuples():
        logger.info("slice %s: %d candidates, %d off topic, %d screened (%d full text), "
                    "worldwide %d", r.slice_id, r.n_candidates, r.n_off_topic,
                    r.n_screened, r.n_fulltext, r.worldwide_count)
        if r.n_candidates and r.on_topic_fraction < 0.25:
            logger.warning("  %s: only %.0f%% of what the query returned is on topic — "
                           "the Boolean query is too broad to carry a denominator",
                           r.slice_id, 100 * r.on_topic_fraction)
    return target / f"{SCREENING_STEM}.csv", den_path
