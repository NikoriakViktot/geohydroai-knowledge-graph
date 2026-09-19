"""Phase 0b — query-seeded discovery against OpenAlex. HTTP only.

`recover_missing.py` resolves works one DOI at a time; here we do not yet know the
DOIs, so this module drives the /works search endpoint instead. Everything else —
the polite-pool parameters, the rate limit, the user-agent discipline — is taken
from that module so the two behave identically against OpenAlex.

This module writes nothing outside `harvest/`, parses no XML and touches no
database, so it can be re-run at any point without disturbing the corpus.
"""
from __future__ import annotations

import json
import logging
import math
import time
from datetime import date
from pathlib import Path

import pandas as pd
import requests

from src.config import settings
from src.paper_3._utils import (
    HARVEST_DIR,
    PAPER_JSON_DIR,
    doi_to_slug,
    get_con,
    normalize_doi,
    normalize_title,
)
from src.paper_3.harvest_queries import (
    DEFAULT_MAX_PAGES,
    build_query_set,
    queryset_provenance,
    seed_dois,
)
from src.paper_3.theses import Thesis, load_theses

logger = logging.getLogger(__name__)

_OPENALEX_SEARCH = "https://api.openalex.org/works"
_OPENALEX_WORKS = "https://api.openalex.org/works/doi:{doi}"
_HTTP_TIMEOUT = 30
#: Same courtesy delay as recover_missing.py — OpenAlex asks for it and we honour it.
_DELAY = 2.0

#: Default per-thesis download budget. The full deduped frame is always written;
#: this only decides which rows carry selected=True.
TOP_N_PER_THESIS = 15

#: Which query set produced harvest_candidates.parquet. Published beside it.
PROVENANCE_FILE = "harvest_provenance.json"


def make_session() -> requests.Session:
    """API session with the polite-pool user agent (not the PDF-fetch one)."""
    session = requests.Session()
    session.headers["User-Agent"] = (
        f"GeoHydroAI/1.0 ({settings.OPEN_ALEX_EMAIL})"
        if settings.OPEN_ALEX_EMAIL else "GeoHydroAI/1.0"
    )
    return session


def _polite_params() -> dict:
    params = {}
    if settings.OPEN_ALEX_EMAIL:
        params["mailto"] = settings.OPEN_ALEX_EMAIL
    if settings.OPEN_ALEX_API:
        params["api_key"] = settings.OPEN_ALEX_API
    return params


def reconstruct_abstract(inverted: dict | None) -> str:
    """OpenAlex stores abstracts as an inverted index; rebuild the running text."""
    if not inverted:
        return ""
    positions: list[tuple[int, str]] = []
    for word, idxs in inverted.items():
        for i in idxs:
            positions.append((i, word))
    positions.sort()
    return " ".join(word for _, word in positions)


def openalex_search(
    query: str,
    session: requests.Session,
    per_page: int = 50,
    max_pages: int = 2,
    filters: dict | None = None,
) -> tuple[list[dict], int]:
    """Search /works. Returns (records, worldwide_count).

    `worldwide_count` is meta.count — how many works exist for this query at all,
    not how many we retrieved. The coverage diagnostic depends on that distinction.
    """
    params = _polite_params()
    params["search"] = query
    params["per-page"] = per_page
    if filters:
        params["filter"] = ",".join(f"{k}:{v}" for k, v in filters.items())

    records: list[dict] = []
    total = 0
    for page in range(1, max_pages + 1):
        params["page"] = page
        try:
            r = session.get(_OPENALEX_SEARCH, params=params, timeout=_HTTP_TIMEOUT)
        except requests.RequestException as exc:
            logger.warning("OpenAlex search %r page %d → %s", query, page, exc)
            break
        if r.status_code != 200:
            logger.warning("OpenAlex search %r page %d → HTTP %d",
                           query, page, r.status_code)
            break
        payload = r.json()
        total = payload.get("meta", {}).get("count", total)
        results = payload.get("results") or []
        records.extend(results)
        if len(results) < per_page:
            break
        time.sleep(_DELAY)
    return records, total


def fetch_by_doi(doi: str, session: requests.Session) -> dict | None:
    """Single work by DOI — how seed papers enter without relying on search."""
    try:
        r = session.get(_OPENALEX_WORKS.format(doi=normalize_doi(doi)),
                        params=_polite_params(), timeout=_HTTP_TIMEOUT)
    except requests.RequestException as exc:
        logger.warning("OpenAlex doi:%s → %s", doi, exc)
        return None
    if r.status_code != 200:
        logger.warning("OpenAlex doi:%s → HTTP %d", doi, r.status_code)
        return None
    return r.json()


def _oa_pdf_url(work: dict) -> str | None:
    """Open-access PDF URL for a work, or None. Paywalled works are never fetched."""
    best = work.get("best_oa_location") or {}
    if best.get("pdf_url"):
        return best["pdf_url"]
    for loc in work.get("locations") or []:
        if loc.get("is_oa") and loc.get("pdf_url"):
            return loc["pdf_url"]
    return None


def _venue(work: dict) -> str:
    primary = work.get("primary_location") or {}
    source = primary.get("source") or {}
    return source.get("display_name") or ""


def to_record(work: dict) -> dict | None:
    """Flatten an OpenAlex work. Returns None for works without a usable DOI."""
    doi = normalize_doi(work.get("doi") or "")
    if not doi or not doi.startswith("10."):
        return None
    return {
        "doi": doi,
        "slug": doi_to_slug(doi),
        "openalex_id": work.get("id") or "",
        "title": work.get("display_name") or work.get("title") or "",
        "abstract": reconstruct_abstract(work.get("abstract_inverted_index")),
        "year": work.get("publication_year"),
        "journal": _venue(work),
        "cited_by_count": work.get("cited_by_count") or 0,
        "type": work.get("type") or "",
        "is_oa": bool((work.get("open_access") or {}).get("is_oa")),
        "pdf_url": _oa_pdf_url(work) or "",
        "referenced_works_count": work.get("referenced_works_count") or 0,
    }


# ── dedup against the existing corpus ─────────────────────────────────────────

def _known_dois() -> set[str]:
    try:
        con = get_con()
        rows = con.execute(
            "SELECT DISTINCT doi FROM papers WHERE doi IS NOT NULL"
        ).fetchall()
    except Exception as exc:
        logger.warning("papers.parquet unreadable (%s) — DOI dedup skipped", exc)
        return set()
    return {normalize_doi(r[0]) for r in rows if r[0]}


def _known_titles() -> set[str]:
    try:
        con = get_con()
        rows = con.execute(
            "SELECT DISTINCT title FROM papers WHERE title IS NOT NULL"
        ).fetchall()
    except Exception as exc:
        logger.warning("papers.parquet unreadable (%s) — title dedup skipped", exc)
        return set()
    return {normalize_title(r[0]) for r in rows if r[0]}


def slug_already_ingested(slug: str) -> bool:
    """True when the corpus already holds this paper under its DOI-slug stem.

    This is the check `recover_missing.step_resolve` exists for: 14 of 18 papers
    once believed missing were already present under exactly this convention.
    Running it before any download is what stops us re-fetching them.
    """
    for suffix in (".tei.paper.json", ".paper.json"):
        if (PAPER_JSON_DIR / f"{slug}{suffix}").exists():
            return True
    return False


def dedup(frame: pd.DataFrame) -> pd.DataFrame:
    """Annotate and drop rows already represented in the corpus.

    Order matters: DOI, then slug-on-disk, then normalised title. The slug check
    must happen before anything is downloaded.
    """
    if frame.empty:
        return frame.assign(drop_reason=pd.Series(dtype=str))

    known_dois = _known_dois()
    known_titles = _known_titles()

    reasons: list[str] = []
    for row in frame.itertuples():
        if row.doi in known_dois:
            reasons.append("doi_in_corpus")
        elif slug_already_ingested(row.slug):
            reasons.append("slug_already_ingested")
        elif normalize_title(row.title) in known_titles:
            reasons.append("title_in_corpus")
        else:
            reasons.append("")
    frame = frame.assign(drop_reason=reasons)

    dropped = (frame["drop_reason"] != "").sum()
    logger.info("Dedup: %d of %d rows already in corpus (%s)",
                dropped, len(frame),
                frame.loc[frame["drop_reason"] != "", "drop_reason"]
                .value_counts().to_dict())
    return frame


# ── selection ─────────────────────────────────────────────────────────────────

def score_row(row) -> float:
    """Rank a candidate for the limited download budget.

    Query hits dominate (a paper found by several thesis queries is on-topic in
    more than one way), citations contribute logarithmically so a single famous
    paper cannot crowd out a thesis, and seeds are effectively guaranteed.
    """
    return (
        2.0 * float(row.n_query_hits)
        + math.log1p(float(row.cited_by_count or 0))
        + (3.0 if row.is_seed else 0.0)
        + (2.0 if (row.year or 0) >= 2018 else 0.0)
    )


def select(frame: pd.DataFrame, top_n_per_thesis: int = TOP_N_PER_THESIS) -> pd.DataFrame:
    """Mark rows for download. Never drops rows — unselected ones feed coverage."""
    if frame.empty:
        return frame.assign(score=pd.Series(dtype=float), selected=pd.Series(dtype=bool))

    frame = frame.assign(score=[score_row(r) for r in frame.itertuples()])
    eligible = frame["drop_reason"].eq("") & frame["is_oa"] & frame["pdf_url"].ne("")

    chosen: set[str] = set()
    for tid in sorted({t for ids in frame["matched_thesis_ids"] for t in ids}):
        mask = eligible & frame["matched_thesis_ids"].apply(lambda ids: tid in ids)
        top = frame.loc[mask].nlargest(top_n_per_thesis, "score")
        chosen.update(top["doi"].tolist())

    # Seeds are not subject to the budget: they are the papers the analysis is
    # explicitly designed to be checked against.
    chosen.update(frame.loc[frame["is_seed"] & eligible, "doi"].tolist())

    return frame.assign(selected=frame["doi"].isin(chosen))


def extend_selection(frame: pd.DataFrame, dois: set[str], reason: str) -> pd.DataFrame:
    """Add papers to the download set outside the per-thesis budget.

    The budget in `select` is per thesis, so a slice's on-topic works compete
    with everything else and mostly lose. When a slice must be screened in full
    text, its works are admitted here — still only if open access with a PDF,
    never if already in the corpus — and each row records why it was admitted,
    so the download set stays explainable after the fact.
    """
    if "selection_reason" not in frame:
        frame = frame.assign(selection_reason=frame["selected"].map(
            lambda s: "budget" if s else ""))
    eligible = (frame["doi"].isin(dois) & frame["drop_reason"].eq("")
                & frame["is_oa"] & frame["pdf_url"].ne("") & ~frame["selected"])
    frame = frame.copy()
    frame.loc[eligible, "selected"] = True
    frame.loc[eligible, "selection_reason"] = reason
    logger.info("Selection extended by %d papers (%s)", int(eligible.sum()), reason)
    return frame


# ── the step ──────────────────────────────────────────────────────────────────

def discover(
    theses: list[Thesis] | None = None,
    out_dir: Path | None = None,
    per_query: int = 50,
    max_pages: int = 2,
    top_n_per_thesis: int = TOP_N_PER_THESIS,
    session: requests.Session | None = None,
) -> pd.DataFrame:
    """Run the whole query set, dedupe, score and write harvest_candidates.parquet."""
    theses = theses if theses is not None else load_theses()
    target_dir = Path(out_dir) if out_dir else HARVEST_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    session = session or make_session()

    queries = build_query_set(theses)
    seeds = seed_dois(theses)

    by_doi: dict[str, dict] = {}
    worldwide: dict[str, int] = {}

    def absorb(record: dict, thesis_ids: list[str], is_seed: bool = False,
               label: str = "") -> None:
        labels = [part for part in label.split("+") if part]
        existing = by_doi.get(record["doi"])
        if existing is None:
            record = dict(record)
            record["matched_thesis_ids"] = sorted(set(thesis_ids))
            record["matched_labels"] = sorted(set(labels))
            record["n_query_hits"] = 1
            record["is_seed"] = is_seed
            by_doi[record["doi"]] = record
            return
        existing["matched_thesis_ids"] = sorted(
            set(existing["matched_thesis_ids"]) | set(thesis_ids))
        existing["matched_labels"] = sorted(
            set(existing["matched_labels"]) | set(labels))
        existing["n_query_hits"] += 1
        existing["is_seed"] = existing["is_seed"] or is_seed

    for i, q in enumerate(queries, 1):
        logger.info("[%d/%d] search %r", i, len(queries), q["query"])
        # A slice record asks for deeper pagination than the default; the
        # caller's `max_pages` remains the floor for everything else.
        pages = max(max_pages, int(q.get("max_pages", DEFAULT_MAX_PAGES))) \
            if q.get("label") else max_pages
        works, count = openalex_search(
            q["query"], session, per_page=per_query,
            max_pages=pages, filters=q["filters"])
        worldwide[q["query"]] = count
        for work in works:
            rec = to_record(work)
            if rec:
                absorb(rec, q["thesis_ids"], label=q.get("label", ""))
        time.sleep(_DELAY)

    for doi, thesis_ids in seeds.items():
        logger.info("seed doi:%s", doi)
        work = fetch_by_doi(doi, session)
        if work:
            rec = to_record(work)
            if rec:
                absorb(rec, thesis_ids, is_seed=True)
        else:
            logger.warning("seed DOI %s could not be resolved on OpenAlex", doi)
        time.sleep(_DELAY)

    frame = pd.DataFrame(list(by_doi.values()))
    if frame.empty:
        logger.error("discovery returned nothing — check network and query set")
        frame = pd.DataFrame(columns=[
            "doi", "slug", "openalex_id", "title", "abstract", "year", "journal",
            "cited_by_count", "type", "is_oa", "pdf_url", "referenced_works_count",
            "matched_thesis_ids", "matched_labels", "n_query_hits", "is_seed",
        ])

    frame = select(dedup(frame), top_n_per_thesis=top_n_per_thesis)
    frame = frame.sort_values(["selected", "score"], ascending=[False, False])

    path = target_dir / "harvest_candidates.parquet"
    frame.to_parquet(path, index=False)
    (target_dir / "worldwide_counts.json").write_text(
        json.dumps(worldwide, indent=2, ensure_ascii=False), encoding="utf-8")
    provenance = {
        **queryset_provenance(queries),
        "n_queries_run": len(queries),
        "n_seed_dois": len(seeds),
        "run_at": date.today().isoformat(),
    }
    (target_dir / PROVENANCE_FILE).write_text(
        json.dumps(provenance, indent=2, ensure_ascii=False), encoding="utf-8")
    write_report(frame, target_dir)

    logger.info("Discovery: %d unique works, %d new, %d selected for download",
                len(frame), int((frame["drop_reason"] == "").sum()),
                int(frame["selected"].sum()))
    return frame


def write_report(frame: pd.DataFrame, out_dir: Path) -> Path:
    """Human-readable harvest report, including the manual-drop list."""
    lines = [
        "# Paper 3 — harvest report",
        "",
        f"Generated {date.today().isoformat()}.",
        "",
        f"- unique works discovered: **{len(frame)}**",
    ]
    if not frame.empty:
        new = frame[frame["drop_reason"] == ""]
        paywalled = new[~new["is_oa"] | new["pdf_url"].eq("")]
        lines += [
            f"- already in corpus: **{int((frame['drop_reason'] != '').sum())}**",
            f"- new: **{len(new)}**",
            f"- selected for download: **{int(frame['selected'].sum())}**",
            f"- new but not open access: **{len(paywalled)}**",
            "",
            "## Not open access — add by hand if needed",
            "",
            "Save the PDF as `data/literature/pdf_missing/{slug}.pdf`, then re-run",
            "`--step grobid`. Nothing here is fetched automatically.",
            "",
        ]
        for row in paywalled.nlargest(min(60, len(paywalled)), "score").itertuples():
            lines.append(
                f"- [{row.doi}](https://doi.org/{row.doi}) — {row.title} "
                f"({row.year or '—'}) → `{row.slug}.pdf`"
            )
    path = out_dir / "harvest_report.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


if __name__ == "__main__":  # pragma: no cover
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s  %(levelname)-8s  %(message)s",
                        datefmt="%H:%M:%S")
    discover()
