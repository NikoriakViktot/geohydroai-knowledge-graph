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
