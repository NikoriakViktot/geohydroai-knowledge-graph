"""Runtime corpus index — counted from data/normalized at run time, never from the
stale registry or the analytics parquet.

Also builds the OpenAlex citation edges from data/enriched (``referenced_works``),
which reach far more papers than the 883-row ``data/parquet/references.parquet``.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path

import pandas as pd

from src.paper_3._utils import (ENRICHED_DIR, NORMALIZED_DIR, PROJECT_ROOT,
                                normalize_doi, normalize_title)
from tools.paper3_audit.config import WORK_DIR

logger = logging.getLogger(__name__)

COHORT_CSV = PROJECT_ROOT / "data" / "paper_3_audit" / "cohort_paper_3.csv"
INDEX_FILE = "corpus_index_runtime.parquet"
OPENALEX_INDEX_FILE = "openalex_index.parquet"
EDGES_FILE = "referenced_edges.parquet"

_TRAILING_YEAR = re.compile(r"\(\d{4}\)\.?$")
_KAKHOVKA = re.compile(r"kakhov|каховськ|каховск|nova kakhovka", re.IGNORECASE)
_SUFFIXES = (".tei.paper.json", ".paper.json", ".tei.xml", ".json", ".xml", ".pdf")

INDEX_COLUMNS = [
    "paper_id", "doi", "doi_clean", "title", "title_norm", "year", "year_source",
    "journal", "authors", "first_author", "openalex_id", "cited_by_count", "cohort",
    "has_normalized", "has_enriched", "text_chars", "n_sections", "kakhovka_mentions",
    "duplicate_group", "duplicate_of", "source_file",
]


def clean_doi(raw) -> str:
    """normalize_doi plus the corpus's known malformations: a trailing ``(2024).``
    (Shumilova) and stray terminal punctuation."""
    doi = normalize_doi(raw)
    doi = _TRAILING_YEAR.sub("", doi).strip()
    return doi.rstrip(".,;)")


def openalex_short(raw) -> str:
    """'https://openalex.org/W123' → 'W123'; '' when absent."""
    if not raw or isinstance(raw, float):
        return ""
    return str(raw).rsplit("/", 1)[-1].strip()


def _authors_str(meta: dict) -> tuple[str, str]:
    names = []
    for a in meta.get("authors") or []:
        if not isinstance(a, dict):
            continue
        fam, giv = (a.get("last_name") or "").strip(), (a.get("first_name") or "").strip()
        full = (a.get("full_name") or "").strip()
        if fam:
            names.append(f"{fam}, {giv}".rstrip(", "))
        elif full:
            names.append(full)
    first = names[0].split(",")[0] if names else ""
    return "; ".join(names), first


def _read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.debug("unreadable %s: %s", path.name, exc)
        return None


def _cohort_dois(path: Path = COHORT_CSV) -> set[str]:
    if not path.exists():
        return set()
    frame = pd.read_csv(path)
    return {clean_doi(d) for d in frame.get("doi", pd.Series(dtype=str)).dropna()}


def build_runtime_index(normalized_dir: Path = NORMALIZED_DIR,
                        enriched_dir: Path = ENRICHED_DIR,
                        work_dir: Path = WORK_DIR,
                        write: bool = True) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Scan every normalized record; join the enriched OpenAlex block.

    Returns (index, openalex_index, referenced_edges). Duplicate DOIs (the corpus
    holds Kadam 2024 twice) are grouped; the canonical member is the enriched one,
    then the one with the most text. Everything else is recorded, nothing dropped.
    """
    cohort = _cohort_dois()
    rows, oa_rows, edge_rows = [], [], []
    for path in sorted(normalized_dir.glob("*.json")):
        rec = _read_json(path)
        if not isinstance(rec, dict):
            continue
        pid = path.stem
        meta = rec.get("metadata") or {}
        sections = rec.get("sections") or {}
        text = " ".join(v for v in sections.values() if isinstance(v, str))
        authors, first = _authors_str(meta)
        year = meta.get("year")
        year_source = "normalized" if year not in (None, "", 0) else ""
        enriched = _read_json(enriched_dir / f"{pid}.json") if (enriched_dir / f"{pid}.json").exists() else None
        oa = (enriched or {}).get("openalex") or {}
        oa_id = openalex_short(oa.get("openalex_id"))
        if not year_source and oa.get("publication_year"):
            year, year_source = oa.get("publication_year"), "openalex"
        doi_clean = clean_doi(meta.get("doi"))
        rows.append({
            "paper_id": pid,
            "doi": meta.get("doi") or "",
            "doi_clean": doi_clean,
            "title": meta.get("title") or "",
            "title_norm": normalize_title(meta.get("title") or ""),
            "year": str(year) if year not in (None, "", 0) else "",
            "year_source": year_source,
            "journal": meta.get("journal") or "",
            "authors": authors,
            "first_author": first,
            "openalex_id": oa_id,
            "cited_by_count": int(oa.get("cited_by_count") or 0),
            "cohort": "paper_3" if doi_clean and doi_clean in cohort else "base",
            "has_normalized": True,
            "has_enriched": enriched is not None,
            "text_chars": len(text),
            "n_sections": sum(1 for v in sections.values() if isinstance(v, str) and v.strip()),
            "kakhovka_mentions": len(_KAKHOVKA.findall(text + " " + (meta.get("title") or ""))),
            "duplicate_group": "",
            "duplicate_of": "",
            "source_file": str(path.relative_to(PROJECT_ROOT)),
        })
        if oa_id:
            oa_rows.append({"openalex_id": oa_id, "paper_id": pid})
            for w in oa.get("referenced_works") or []:
                edge_rows.append({"paper_id": pid, "referenced_openalex_id": openalex_short(w)})

    index = pd.DataFrame(rows, columns=INDEX_COLUMNS)
    index = _mark_duplicates(index)
    canonical = set(index.loc[index["duplicate_of"] == "", "paper_id"])
    oa_index = pd.DataFrame(oa_rows, columns=["openalex_id", "paper_id"])
    oa_index = oa_index[oa_index["paper_id"].isin(canonical)].drop_duplicates("openalex_id")
    edges = pd.DataFrame(edge_rows, columns=["paper_id", "referenced_openalex_id"]).drop_duplicates()

    if write:
        work_dir.mkdir(parents=True, exist_ok=True)
        index.to_parquet(work_dir / INDEX_FILE, index=False)
        oa_index.to_parquet(work_dir / OPENALEX_INDEX_FILE, index=False)
        edges.to_parquet(work_dir / EDGES_FILE, index=False)
    logger.info("runtime index: %d papers, %d with openalex_id, %d reference edges, %d duplicate groups",
                len(index), len(oa_index), len(edges), index["duplicate_group"].replace("", pd.NA).nunique())
    return index, oa_index, edges


def _mark_duplicates(index: pd.DataFrame) -> pd.DataFrame:
    index = index.copy()
    dup = index[index["doi_clean"] != ""].groupby("doi_clean")["paper_id"].apply(list)
    for doi, pids in dup.items():
        if len(pids) < 2:
            continue
        members = index[index["paper_id"].isin(pids)].sort_values(
            ["has_enriched", "text_chars"], ascending=[False, False])
        canonical = members.iloc[0]["paper_id"]
        index.loc[index["paper_id"].isin(pids), "duplicate_group"] = doi
        index.loc[index["paper_id"].isin(pids) & (index["paper_id"] != canonical),
                  "duplicate_of"] = canonical
    return index


def load_runtime_index(work_dir: Path = WORK_DIR) -> pd.DataFrame:
    path = work_dir / INDEX_FILE
    if not path.exists():
        raise FileNotFoundError(f"{path} missing — run `prepare` first")
    return pd.read_parquet(path)


def load_openalex(work_dir: Path = WORK_DIR) -> tuple[pd.DataFrame, pd.DataFrame]:
    return (pd.read_parquet(work_dir / OPENALEX_INDEX_FILE),
            pd.read_parquet(work_dir / EDGES_FILE))


class PaperResolver:
    """Map whatever id a retrieval route returns onto a canonical corpus paper_id."""

    def __init__(self, index: pd.DataFrame):
        self._canon = dict(zip(index["paper_id"], index["duplicate_of"]))
        self._by_doi = {}
        self._by_title = {}
        self._doi_by_pid = dict(zip(index["paper_id"], index["doi_clean"]))
        for r in index.itertuples():
            if r.duplicate_of:
                continue
            if r.doi_clean:
                self._by_doi.setdefault(r.doi_clean, r.paper_id)
            if r.title_norm:
                self._by_title.setdefault(r.title_norm, r.paper_id)
        self.unmapped: list[str] = []

    def doi_of(self, paper_id: str) -> str:
        return self._doi_by_pid.get(paper_id, "")

    def canonical(self, paper_id: str) -> str | None:
        if paper_id not in self._canon:
            return None
        return self._canon[paper_id] or paper_id

    def from_hit(self, raw: str) -> str | None:
        """Chroma hit ids sometimes equal the source filename; strip and retry."""
        if not raw:
            return None
        pid = self.canonical(raw)
        if pid:
            return pid
        stem = raw.rsplit("/", 1)[-1]
        for suffix in _SUFFIXES:
            if stem.endswith(suffix):
                stem = stem[: -len(suffix)]
                break
        pid = self.canonical(stem)
        if pid is None:
            self.unmapped.append(raw)
        return pid

    def from_doi(self, doi: str) -> str | None:
        return self._by_doi.get(clean_doi(doi))

    def from_title(self, title: str, min_ratio: float = 0.95) -> str | None:
        import difflib
        norm = normalize_title(title)
        if not norm:
            return None
        if norm in self._by_title:
            return self._by_title[norm]
        best, best_pid = 0.0, None
        for t, pid in self._by_title.items():
            if abs(len(t) - len(norm)) > 0.3 * len(norm):
                continue
            r = difflib.SequenceMatcher(None, norm, t).ratio()
            if r > best:
                best, best_pid = r, pid
        return best_pid if best >= min_ratio else None
