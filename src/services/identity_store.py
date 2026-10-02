"""Paper identity queries on the PostgreSQL layer of truth (core.paper*).

Used by the API (GET /papers/resolve, POST /papers/resolve-batch, GET /papers/{id},
/stats, /manifest) and usable from CLIs. No FastAPI imports here.
"""

from __future__ import annotations

import difflib
import hashlib
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select

from src.contracts.identity import PaperAlias as AliasContract
from src.contracts.identity import PaperFile as FileContract
from src.contracts.identity import PaperIdentity
from src.db.engine import session_scope
from src.db.models import CohortMember, Paper, PaperAlias, PaperFile, ProjectionState, Run
from src.etl.identity import norm_title, openalex_short
from src.services.identity import normalize_doi

TITLE_MIN = 0.95
_FILE_SUFFIXES = (".tei.paper.json", ".tei.xml", ".pdf", ".json")


@dataclass(frozen=True)
class Resolved:
    match: str           # exact | alias | title_fuzzy
    score: float
    paper: PaperIdentity
    canonical: PaperIdentity | None


class InvalidSelector(ValueError):
    """Raised for an unusable selector (e.g. a value that is not a DOI)."""


def _identity(session, row: Paper, include: frozenset[str]) -> PaperIdentity:
    aliases = files = cohorts = []
    if "aliases" in include:
        aliases = [AliasContract(alias_type=a.alias_type, alias=a.alias, source=a.source)
                   for a in session.scalars(select(PaperAlias).where(PaperAlias.paper_id == row.paper_id)
                                            .order_by(PaperAlias.alias_type, PaperAlias.alias))]
    if "files" in include:
        files = [FileContract(kind=f.kind, path=f.path, sha256=f.sha256, size_bytes=f.size_bytes, status=f.status)
                 for f in session.scalars(select(PaperFile).where(PaperFile.paper_id == row.paper_id)
                                          .order_by(PaperFile.kind, PaperFile.path))]
    if "cohorts" in include:
        cohorts = list(session.scalars(select(CohortMember.cohort).where(CohortMember.paper_id == row.paper_id)))
    return PaperIdentity(paper_id=row.paper_id, doi=row.doi, title=row.title, year=row.year, venue=row.venue,
                         openalex_id=row.openalex_id, identity_status=row.identity_status,
                         duplicate_of=row.duplicate_of, aliases=aliases, files=files, cohorts=cohorts)


def _by_alias(session, alias_type: str, alias: str) -> Paper | None:
    pid = session.scalar(select(PaperAlias.paper_id).where(PaperAlias.alias_type == alias_type,
                                                           PaperAlias.alias == alias))
    return session.get(Paper, pid) if pid else None


def _strip_suffix(name: str) -> str:
    for suffix in _FILE_SUFFIXES:
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name


def resolve(*, doi: str | None = None, paper_id: str | None = None, file: str | None = None,
            openalex_id: str | None = None, title: str | None = None, year: int | None = None,
            include: frozenset[str] = frozenset()) -> Resolved | None:
    """Exactly one selector (title may come with year). Returns None when not in the corpus."""
    selectors = [s for s in (doi, paper_id, file, openalex_id, title) if s]
    if len(selectors) != 1:
        raise InvalidSelector("give exactly one of doi, paper_id, file, openalex_id, title")
    with session_scope() as s:
        row, match, score = None, "alias", 1.0
        if doi:
            d = normalize_doi(doi)
            if d is None:
                raise InvalidSelector(f"not a DOI: {doi!r}")
            row = _by_alias(s, "doi", d) or s.scalar(select(Paper).where(Paper.doi == d).limit(1))
        elif paper_id:
            row, match = s.get(Paper, paper_id), "exact"
        elif file:
            stem = _strip_suffix(file.rsplit("/", 1)[-1])
            for alias_type in ("file_stem", "doi_slug", "doi_slug_colon"):
                row = _by_alias(s, alias_type, stem)
                if row:
                    match = "exact" if alias_type == "file_stem" else "alias"
                    break
        elif openalex_id:
            row = _by_alias(s, "openalex_id", openalex_short(openalex_id) or "")
        else:
            row, score = _by_title(s, title or "", year)
            match = "title_fuzzy"
        if row is None:
            return None
        paper = _identity(s, row, include)
        canonical = None
        if row.duplicate_of:
            canonical = _identity(s, s.get(Paper, row.duplicate_of), frozenset())
        return Resolved(match=match, score=round(score, 4), paper=paper, canonical=canonical)


def _by_title(session, title: str, year: int | None) -> tuple[Paper | None, float]:
    wanted = norm_title(title)
    if len(wanted) < 10:
        raise InvalidSelector("title too short to match reliably")
    q = select(Paper).where(Paper.title.is_not(None), Paper.identity_status != "not_a_paper")
    if year:
        q = q.where(Paper.year.between(year - 1, year + 1))
    best, best_score = None, 0.0
    for row in session.scalars(q):
        score = difflib.SequenceMatcher(None, wanted, norm_title(row.title)).ratio()
        if score > best_score:
            best, best_score = row, score
    return (best, best_score) if best_score >= TITLE_MIN else (None, best_score)


def latest_identity_run() -> tuple[str | None, datetime | None]:
    with session_scope() as s:
        row = s.execute(select(Run.run_id, Run.finished_at)
                        .where(Run.kind == "etl", Run.name == "identity", Run.status == "succeeded")
                        .order_by(Run.finished_at.desc()).limit(1)).first()
    return (str(row.run_id), row.finished_at) if row else (None, None)


def stats() -> dict:
    with session_scope() as s:
        by_status = dict(s.execute(select(Paper.identity_status, func.count()).group_by(Paper.identity_status)).all())
        files = dict(s.execute(select(PaperFile.kind, func.count()).group_by(PaperFile.kind)).all())
        projections = [dict(store=p.store, name=p.name, item_count=p.item_count,
                            built_at=p.built_at, run_id=str(p.run_id) if p.run_id else None, details=p.details)
                       for p in s.scalars(select(ProjectionState).order_by(ProjectionState.store, ProjectionState.name))]
    return {"papers": {"total": sum(by_status.values()), "by_identity_status": by_status},
            "files": files, "projections": projections}


def corpus_manifest(collection: str, embedding_model: str, rules_version: str) -> dict:
    """Identity of the corpus snapshot: hash over canonical paper ids, the identity run,
    the vector collection, the embedding model and the retrieval-rules version."""
    run_id, finished = latest_identity_run()
    with session_scope() as s:
        ids = list(s.scalars(select(Paper.paper_id)
                             .where(Paper.identity_status.not_in(("duplicate", "not_a_paper")))
                             .order_by(Paper.paper_id)))
    h = hashlib.sha256()
    for part in (*ids, run_id or "", collection, embedding_model, rules_version):
        h.update(part.encode("utf-8"))
        h.update(b"\x00")
    return {"corpus_manifest_id": h.hexdigest(), "frozen": False, "papers": len(ids),
            "collection": collection, "embedding_model": embedding_model,
            "retrieval_rules_version": rules_version, "identity_run_id": run_id, "created_at": finished}


# ── references of a paper → corpus papers (GET /papers/{id}/references) ─────────

_TITLE_INDEX: tuple[float, dict[str, list[tuple[str, int | None]]]] | None = None
_TITLE_INDEX_TTL = 600.0
_TITLE_MIN_CHARS = 20


def _title_index(session) -> dict[str, list[tuple[str, int | None]]]:
    """norm_title → [(canonical paper_id, year)], rebuilt every 10 minutes."""
    import time
    global _TITLE_INDEX
    if _TITLE_INDEX and time.monotonic() - _TITLE_INDEX[0] < _TITLE_INDEX_TTL:
        return _TITLE_INDEX[1]
    index: dict[str, list[tuple[str, int | None]]] = {}
    rows = session.execute(select(Paper.paper_id, Paper.title, Paper.year, Paper.duplicate_of)
                           .where(Paper.title.is_not(None), Paper.identity_status != "not_a_paper"))
    for pid, title, year, dup in rows:
        key = norm_title(title)
        if len(key) >= _TITLE_MIN_CHARS:
            entry = (dup or pid, year)
            if entry[0] not in {e[0] for e in index.get(key, [])}:
                index.setdefault(key, []).append(entry)
    _TITLE_INDEX = (time.monotonic(), index)
    return index


def match_references(refs: list[tuple[str | None, str | None, int | None]]) -> list[tuple[PaperIdentity | None, str | None]]:
    """For each (doi, title, year) of a bibliography: the corpus paper it designates and how.

    DOIs are looked up in one query. Without a DOI match, the title must equal a corpus
    title after normalisation (≥ 20 characters, year ± 1 when both are known) and designate
    exactly one paper. Duplicates resolve to their canonical paper. No fuzzy matching: a
    wrong "in corpus" is worse than a missed one.
    """
    dois = {d for d in (normalize_doi(r[0]) for r in refs if r[0]) if d}
    with session_scope() as s:
        by_doi: dict[str, str] = {}
        if dois:
            for alias, pid in s.execute(select(PaperAlias.alias, PaperAlias.paper_id)
                                        .where(PaperAlias.alias_type == "doi", PaperAlias.alias.in_(dois))):
                by_doi[alias] = pid
        titles = _title_index(s)
        chosen: list[tuple[str | None, str | None]] = []
        for doi, title, year in refs:
            d = normalize_doi(doi) if doi else None
            if d and d in by_doi:
                chosen.append((by_doi[d], "doi"))
                continue
            cands = [pid for pid, y in titles.get(norm_title(title), []) if not (year and y and abs(y - year) > 1)] \
                if title else []
            chosen.append((cands[0], "title") if len(cands) == 1 else (None, None))
        ids = {pid for pid, _ in chosen if pid}
        rows = {p.paper_id: p for p in s.scalars(select(Paper).where(Paper.paper_id.in_(ids)))} if ids else {}
        canon_ids = {p.duplicate_of for p in rows.values() if p.duplicate_of} - set(rows)
        if canon_ids:
            rows.update({p.paper_id: p for p in s.scalars(select(Paper).where(Paper.paper_id.in_(canon_ids)))})
        out = []
        for pid, how in chosen:
            row = rows.get(pid) if pid else None
            if row is not None and row.duplicate_of and row.duplicate_of in rows:
                row = rows[row.duplicate_of]
            out.append((_identity(s, row, frozenset()) if row is not None else None, how if row is not None else None))
        return out
