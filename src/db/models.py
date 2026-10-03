"""SQLAlchemy models of the layer of truth — schemas ``core`` and ``ops`` (migration 0001).

Enumerated columns are TEXT with CHECK constraints built from the contract Literal
types in src/contracts/identity.py, so the database accepts exactly what the
contract allows.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import (
    BigInteger, Boolean, CheckConstraint, Date, DateTime, Float, ForeignKey, ForeignKeyConstraint, Index,
    Integer, MetaData, SmallInteger, Text, func, text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from src.contracts.identity import (
    ALIAS_TYPES, FILE_KINDS, FILE_STATUSES, IDENTITY_STATUSES, RUN_KINDS, RUN_STATUSES,
)
from src.contracts.research import (
    BIB_VERDICTS, CITATION_VERDICTS, EVIDENCE_ROLES, LABELER_KINDS, PROJECT_ID_PATTERN, REF_RELATIONS,
    REF_STATUSES, RELEVANCES, THESIS_KINDS,
)
from src.contracts.verify import VERIFY_PROBLEMS, VERIFY_TARGET_KINDS, VERIFY_VERDICTS

NAMING = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING)


# ── ops: provenance ─────────────────────────────────────────────────────────────

class Run(Base):
    """One execution that wrote to the layer of truth (ETL, ingest, rebuild …)."""

    __tablename__ = "run"
    __table_args__ = (
        CheckConstraint(_in("kind", RUN_KINDS), name="kind"),
        CheckConstraint(_in("status", RUN_STATUSES), name="status"),
        {"schema": "ops"},
    )

    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    kind: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, default="running")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    git_commit: Mapped[str | None] = mapped_column(Text)
    git_dirty: Mapped[bool | None] = mapped_column(Boolean)
    params: Mapped[dict] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))
    counts: Mapped[dict] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))
    notes: Mapped[str | None] = mapped_column(Text)


class SourceFile(Base):
    """A file read by a run — the provenance of everything imported from disk."""

    __tablename__ = "source_file"
    __table_args__ = ({"schema": "ops"},)

    sha256: Mapped[str] = mapped_column(Text, primary_key=True)
    path: Mapped[str] = mapped_column(Text, primary_key=True)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ops.run.run_id"))
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ApiKey(Base):
    """An API consumer key: only the sha256 of the key is stored."""

    __tablename__ = "api_key"
    __table_args__ = (
        CheckConstraint("scopes <@ ARRAY['read','llm','write','admin','verify']::text[]", name="scopes"),
        {"schema": "ops"},
    )

    key_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    consumer: Mapped[str] = mapped_column(Text)
    scopes: Mapped[list[str]] = mapped_column(ARRAY(Text))
    key_hash: Mapped[str] = mapped_column(Text, unique=True)
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# ── core: papers ────────────────────────────────────────────────────────────────

class Paper(Base):
    __tablename__ = "paper"
    __table_args__ = (
        CheckConstraint(_in("identity_status", IDENTITY_STATUSES), name="identity_status"),
        CheckConstraint("(identity_status = 'duplicate') = (duplicate_of IS NOT NULL)", name="duplicate_of"),
        # One canonical paper per DOI; duplicates point at it through duplicate_of.
        Index("uq_paper_doi_canonical", "doi", unique=True,
              postgresql_where=text("doi IS NOT NULL AND identity_status <> 'duplicate'")),
        {"schema": "core"},
    )

    paper_id: Mapped[str] = mapped_column(Text, primary_key=True)
    doi: Mapped[str | None] = mapped_column(Text)
    title: Mapped[str | None] = mapped_column(Text)
    year: Mapped[int | None] = mapped_column(SmallInteger)
    venue: Mapped[str | None] = mapped_column(Text)
    openalex_id: Mapped[str | None] = mapped_column(Text)
    identity_status: Mapped[str] = mapped_column(Text)
    duplicate_of: Mapped[str | None] = mapped_column(ForeignKey("core.paper.paper_id"))
    notes: Mapped[str | None] = mapped_column(Text)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ops.run.run_id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class PaperAlias(Base):
    __tablename__ = "paper_alias"
    __table_args__ = (
        CheckConstraint(_in("alias_type", ALIAS_TYPES), name="alias_type"),
        Index("ix_paper_alias_paper_id", "paper_id"),
        {"schema": "core"},
    )

    alias_type: Mapped[str] = mapped_column(Text, primary_key=True)
    alias: Mapped[str] = mapped_column(Text, primary_key=True)
    paper_id: Mapped[str] = mapped_column(ForeignKey("core.paper.paper_id", ondelete="CASCADE"))
    source: Mapped[str] = mapped_column(Text)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ops.run.run_id"))


class PaperFile(Base):
    __tablename__ = "paper_file"
    __table_args__ = (
        CheckConstraint(_in("kind", FILE_KINDS), name="kind"),
        CheckConstraint(_in("status", FILE_STATUSES), name="status"),
        Index("ix_paper_file_sha256", "sha256"),
        {"schema": "core"},
    )

    paper_id: Mapped[str] = mapped_column(ForeignKey("core.paper.paper_id", ondelete="CASCADE"), primary_key=True)
    kind: Mapped[str] = mapped_column(Text, primary_key=True)
    path: Mapped[str] = mapped_column(Text, primary_key=True)
    sha256: Mapped[str | None] = mapped_column(Text)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(Text, default="ok")
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ops.run.run_id"))


class CohortMember(Base):
    __tablename__ = "cohort_member"
    __table_args__ = ({"schema": "core"},)

    cohort: Mapped[str] = mapped_column(Text, primary_key=True)
    paper_id: Mapped[str] = mapped_column(ForeignKey("core.paper.paper_id", ondelete="CASCADE"), primary_key=True)
    source: Mapped[str] = mapped_column(Text)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ops.run.run_id"))


class ProjectionState(Base):
    """What each derived store (Neo4j, Chroma, parquet) was last built from."""

    __tablename__ = "projection_state"
    __table_args__ = ({"schema": "core"},)

    store: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text, primary_key=True)
    run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("ops.run.run_id"))
    item_count: Mapped[int | None] = mapped_column(BigInteger)
    built_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    details: Mapped[dict] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))
    notes: Mapped[str | None] = mapped_column(Text)


# ══ Research data from the per-paper folders (migration 0003) ═══════════════════

class _Provenance:
    """Where a row came from: the imported file (path + sha256) and the run that read it."""

    source_path: Mapped[str] = mapped_column(Text)
    source_sha256: Mapped[str] = mapped_column(Text)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ops.run.run_id"))


class _Labelled:
    labeler_kind: Mapped[str] = mapped_column(Text)
    labeler: Mapped[str] = mapped_column(Text)


def _labeler_check() -> CheckConstraint:
    return CheckConstraint(_in("labeler_kind", LABELER_KINDS), name="labeler_kind")


class Project(Base):
    """The registry of papers: where each one's repository is and what was last delivered (migration 0005)."""

    __tablename__ = "project"
    __table_args__ = (CheckConstraint(f"project_id ~ '{PROJECT_ID_PATTERN}'", name="project_id"),
                      {"schema": "project"})

    project_id: Mapped[str] = mapped_column(Text, primary_key=True)
    repo: Mapped[str | None] = mapped_column(Text)
    paper_label: Mapped[str | None] = mapped_column(Text)
    note: Mapped[str | None] = mapped_column(Text)
    distro: Mapped[str | None] = mapped_column(Text)                # WSL distribution holding the repository
    repo_path: Mapped[str | None] = mapped_column(Text)             # absolute path inside that distribution
    publication_dir: Mapped[str | None] = mapped_column(Text)       # delivery target, relative to repo_path
    public: Mapped[bool | None] = mapped_column(Boolean)            # public repository: text artefacts only
    manifest_path: Mapped[str | None] = mapped_column(Text)         # ghai.project.yaml, relative to repo_path
    manifest_sha256: Mapped[str | None] = mapped_column(Text)
    last_delivery_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_delivery_commit: Mapped[str | None] = mapped_column(Text)  # consumer HEAD when the delivery was made
    last_delivery: Mapped[dict | None] = mapped_column(JSONB)       # summary of DELIVERY_MANIFEST.json
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                        onupdate=func.now())


class Thesis(_Provenance, _Labelled, Base):
    __tablename__ = "thesis"
    __table_args__ = (
        CheckConstraint(_in("kind", THESIS_KINDS), name="kind"),
        CheckConstraint("priority IS NULL OR priority IN ('high', 'medium', 'low')", name="priority"),
        _labeler_check(),
        {"schema": "project"},
    )

    project_id: Mapped[str] = mapped_column(ForeignKey("project.project.project_id"), primary_key=True)
    thesis_id: Mapped[str] = mapped_column(Text, primary_key=True)
    kind: Mapped[str] = mapped_column(Text)
    statement: Mapped[str] = mapped_column(Text)
    section: Mapped[str | None] = mapped_column(Text)
    category: Mapped[str | None] = mapped_column(Text)
    priority: Mapped[str | None] = mapped_column(Text)
    quantitative: Mapped[str | None] = mapped_column(Text)
    tables: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    needs: Mapped[str | None] = mapped_column(Text)
    search_queries: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    extra: Mapped[dict] = mapped_column(JSONB, default=dict)


class ThesisRef(Base):
    __tablename__ = "thesis_ref"
    __table_args__ = (
        ForeignKeyConstraint(["project_id", "thesis_id"], ["project.thesis.project_id", "project.thesis.thesis_id"],
                             ondelete="CASCADE"),
        CheckConstraint(_in("relation", REF_RELATIONS), name="relation"),
        CheckConstraint(_in("status", REF_STATUSES), name="status"),
        {"schema": "project"},
    )

    project_id: Mapped[str] = mapped_column(Text, primary_key=True)
    thesis_id: Mapped[str] = mapped_column(Text, primary_key=True)
    key: Mapped[str] = mapped_column(Text, primary_key=True)
    relation: Mapped[str] = mapped_column(Text, primary_key=True)
    status: Mapped[str] = mapped_column(Text)
    status_raw: Mapped[str | None] = mapped_column(Text)


class AtomicClaim(_Provenance, _Labelled, Base):
    __tablename__ = "atomic_claim"
    __table_args__ = (_labeler_check(), {"schema": "project"})

    project_id: Mapped[str] = mapped_column(ForeignKey("project.project.project_id"), primary_key=True)
    atomic_id: Mapped[str] = mapped_column(Text, primary_key=True)
    thesis_id: Mapped[str] = mapped_column(Text)
    statement: Mapped[str] = mapped_column(Text)
    required_roles: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    manuscript_relevance: Mapped[str | None] = mapped_column(Text)
    key_terms_primary: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    key_terms_support: Mapped[list] = mapped_column(JSONB, default=list)
    negative_terms: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    extra_queries: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    counterevidence_queries: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    queries_version: Mapped[str | None] = mapped_column(Text)


class PositiveControl(_Provenance, Base):
    __tablename__ = "positive_control"
    __table_args__ = ({"schema": "project"},)

    project_id: Mapped[str] = mapped_column(ForeignKey("project.project.project_id"), primary_key=True)
    thesis_id: Mapped[str] = mapped_column(Text, primary_key=True)
    doi: Mapped[str] = mapped_column(Text, primary_key=True)
    relevance: Mapped[str | None] = mapped_column(Text)
    expected_stage: Mapped[str | None] = mapped_column(Text)
    difficulty: Mapped[str | None] = mapped_column(Text)
    source_of_seed: Mapped[str | None] = mapped_column(Text)
    manually_checked: Mapped[bool | None] = mapped_column(Boolean)
    evidence: Mapped[dict] = mapped_column(JSONB, default=dict)


class CitationOccurrence(_Provenance, Base):
    __tablename__ = "citation_occurrence"
    __table_args__ = ({"schema": "project"},)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("project.project.project_id"))
    cite_key: Mapped[str] = mapped_column(Text)
    section: Mapped[str | None] = mapped_column(Text)
    sentence: Mapped[str] = mapped_column(Text)
    quoted: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    doi: Mapped[str | None] = mapped_column(Text)
    extra: Mapped[dict] = mapped_column(JSONB, default=dict)


class TechnicalSource(_Provenance, Base):
    __tablename__ = "technical_source"
    __table_args__ = ({"schema": "biblio"},)

    source_id: Mapped[str] = mapped_column(Text, primary_key=True)
    cite_as: Mapped[str] = mapped_column(Text)
    source_type: Mapped[str] = mapped_column(Text)
    title: Mapped[str | None] = mapped_column(Text)
    identifier: Mapped[str | None] = mapped_column(Text)
    issuer: Mapped[str | None] = mapped_column(Text)
    url: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str | None] = mapped_column(Text)
    extra: Mapped[dict] = mapped_column(JSONB, default=dict)


#: Upstream services whose responses biblio.http_cache keeps.
HTTP_SERVICES = ("crossref", "openalex", "datacite", "unpaywall", "url")


class HttpCache(Base):
    """A registry's answer: a 200 body, or a definitive 404/410. Timeouts, 429 and 5xx are never
    stored, so a failed lookup is not remembered as "not found". `url` never carries credentials."""

    __tablename__ = "http_cache"
    __table_args__ = (
        CheckConstraint(_in("service", HTTP_SERVICES), name="service"),
        CheckConstraint("status IN (200, 404, 410)", name="status"),
        Index("ix_http_cache_expires_at", "expires_at"),
        {"schema": "biblio"},
    )

    service: Mapped[str] = mapped_column(Text, primary_key=True)
    key: Mapped[str] = mapped_column(Text, primary_key=True)
    url: Mapped[str] = mapped_column(Text)
    status: Mapped[int] = mapped_column(SmallInteger)
    response: Mapped[dict | None] = mapped_column(JSONB)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class CiteKey(_Provenance, Base):
    __tablename__ = "cite_key"
    __table_args__ = ({"schema": "biblio"},)

    project_id: Mapped[str] = mapped_column(ForeignKey("project.project.project_id"), primary_key=True)
    key: Mapped[str] = mapped_column(Text, primary_key=True)
    doi: Mapped[str | None] = mapped_column(Text)
    paper_id: Mapped[str | None] = mapped_column(Text)
    extra: Mapped[dict] = mapped_column(JSONB, default=dict)


class BibVerification(_Provenance, _Labelled, Base):
    __tablename__ = "verification"
    __table_args__ = (
        CheckConstraint(_in("verdict", BIB_VERDICTS), name="verdict"),
        _labeler_check(),
        Index("ix_verification_doi", "doi"),
        {"schema": "biblio"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    project_id: Mapped[str | None] = mapped_column(Text)
    cite_key: Mapped[str | None] = mapped_column(Text)
    doi: Mapped[str | None] = mapped_column(Text)
    verdict: Mapped[str] = mapped_column(Text)
    checked_against: Mapped[str] = mapped_column(Text)
    details: Mapped[dict] = mapped_column(JSONB, default=dict)
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MethodReference(_Provenance, Base):
    __tablename__ = "method_reference"
    __table_args__ = ({"schema": "biblio"},)

    project_id: Mapped[str] = mapped_column(ForeignKey("project.project.project_id"), primary_key=True)
    doi_or_key: Mapped[str] = mapped_column(Text, primary_key=True)
    used_for: Mapped[str] = mapped_column(Text, primary_key=True)
    note: Mapped[str | None] = mapped_column(Text)
    extra: Mapped[dict] = mapped_column(JSONB, default=dict)


class ScreeningLabel(_Provenance, _Labelled, Base):
    __tablename__ = "screening_label"
    __table_args__ = (
        CheckConstraint(f"role IS NULL OR {_in('role', EVIDENCE_ROLES)}", name="role"),
        CheckConstraint(_in("relevance", RELEVANCES), name="relevance"),
        CheckConstraint("subject_kind IN ('thesis', 'atomic_claim', 'query')", name="subject_kind"),
        _labeler_check(),
        Index("ix_screening_label_subject", "project_id", "subject_id"),
        {"schema": "evidence"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("project.project.project_id"))
    subject_kind: Mapped[str] = mapped_column(Text)
    subject_id: Mapped[str] = mapped_column(Text)
    paper_id: Mapped[str | None] = mapped_column(Text)
    doi: Mapped[str | None] = mapped_column(Text)
    role: Mapped[str | None] = mapped_column(Text)
    role_raw: Mapped[str | None] = mapped_column(Text)
    relevance: Mapped[str] = mapped_column(Text)
    confidence: Mapped[float | None] = mapped_column(Float)
    quote: Mapped[str | None] = mapped_column(Text)
    quote_verified: Mapped[bool | None] = mapped_column(Boolean)
    rationale: Mapped[str | None] = mapped_column(Text)
    prompt_sha256: Mapped[str | None] = mapped_column(Text)
    extra: Mapped[dict] = mapped_column(JSONB, default=dict)
    source_row: Mapped[int | None] = mapped_column(Integer)


class ClaimEvidence(_Provenance, Base):
    """One row of a literature-audit evidence table (e.g. 02_thesis_evidence.csv)."""

    __tablename__ = "claim_evidence"
    __table_args__ = (CheckConstraint(f"role IS NULL OR {_in('role', EVIDENCE_ROLES)}", name="role"),
                      {"schema": "evidence"})

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("project.project.project_id"))
    thesis_id: Mapped[str | None] = mapped_column(Text)
    atomic_id: Mapped[str | None] = mapped_column(Text)
    role: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    paper_id: Mapped[str | None] = mapped_column(Text)
    cite_key: Mapped[str | None] = mapped_column(Text)
    doi: Mapped[str | None] = mapped_column(Text)
    section: Mapped[str | None] = mapped_column(Text)
    page: Mapped[str | None] = mapped_column(Text)
    chunk_id: Mapped[str | None] = mapped_column(Text)
    quote_verified: Mapped[bool | None] = mapped_column(Boolean)
    evidence_quote: Mapped[str | None] = mapped_column(Text)
    status_rule: Mapped[str | None] = mapped_column(Text)
    extra: Mapped[dict] = mapped_column(JSONB, default=dict)
    source_row: Mapped[int | None] = mapped_column(Integer)


class QuoteCheck(_Provenance, _Labelled, Base):
    __tablename__ = "quote_check"
    __table_args__ = (CheckConstraint(_in("verdict", CITATION_VERDICTS), name="verdict"), _labeler_check(),
                      {"schema": "evidence"})

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("project.project.project_id"))
    cite_key: Mapped[str] = mapped_column(Text)
    doi: Mapped[str | None] = mapped_column(Text)
    claim_use: Mapped[str | None] = mapped_column(Text)
    verdict: Mapped[str] = mapped_column(Text)
    finding: Mapped[str] = mapped_column(Text)
    checked_on: Mapped[date] = mapped_column(Date)


class LiteratureNumber(_Provenance, Base):
    __tablename__ = "literature_number"
    __table_args__ = (CheckConstraint(f"verified_kind IS NULL OR {_in('verified_kind', LABELER_KINDS)}",
                                      name="verified_kind"), {"schema": "evidence"})

    project_id: Mapped[str] = mapped_column(ForeignKey("project.project.project_id"), primary_key=True)
    number_id: Mapped[str] = mapped_column(Text, primary_key=True)
    paper_id: Mapped[str | None] = mapped_column(Text)
    doi: Mapped[str | None] = mapped_column(Text)
    cite_key: Mapped[str | None] = mapped_column(Text)
    quantity_name: Mapped[str | None] = mapped_column(Text)
    value: Mapped[float | None] = mapped_column(Float)
    value_raw: Mapped[str] = mapped_column(Text)
    unit: Mapped[str | None] = mapped_column(Text)
    window_text: Mapped[str | None] = mapped_column(Text)
    page: Mapped[str | None] = mapped_column(Text)
    extra: Mapped[dict] = mapped_column(JSONB, default=dict)
    verified_kind: Mapped[str | None] = mapped_column(Text)
    verified_by: Mapped[str | None] = mapped_column(Text)


class NoveltyVerdict(_Provenance, _Labelled, Base):
    __tablename__ = "novelty_verdict"
    __table_args__ = (_labeler_check(), {"schema": "evidence"})

    project_id: Mapped[str] = mapped_column(ForeignKey("project.project.project_id"), primary_key=True)
    question_id: Mapped[str] = mapped_column(Text, primary_key=True)
    verdict: Mapped[str] = mapped_column(Text)
    denominator: Mapped[dict] = mapped_column(JSONB, default=dict)
    closest: Mapped[list] = mapped_column(JSONB, default=list)
    statement: Mapped[str | None] = mapped_column(Text)


class Override(_Provenance, _Labelled, Base):
    __tablename__ = "override"
    __table_args__ = (CheckConstraint(f"role IS NULL OR {_in('role', EVIDENCE_ROLES)}", name="role"),
                      _labeler_check(), {"schema": "evidence"})

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("project.project.project_id"))
    atomic_id: Mapped[str] = mapped_column(Text)
    paper_id: Mapped[str | None] = mapped_column(Text)   # None: a claim-level override
    role: Mapped[str | None] = mapped_column(Text)
    justification: Mapped[str] = mapped_column(Text)
    decided_on: Mapped[date | None] = mapped_column(Date)
    extra: Mapped[dict] = mapped_column(JSONB, default=dict)


class Acquisition(_Provenance, Base):
    __tablename__ = "acquisition"
    __table_args__ = (
        CheckConstraint("route IN ('unpaywall', 'europepmc', 'openalex', 'publisher', 'arxiv', 'wayback_oa', "
                        "'manual_upload', 'legacy_unknown')", name="route"),
        CheckConstraint("status IN ('downloaded', 'duplicate', 'needs_manual', 'failed')", name="status"),
        Index("ix_acquisition_doi", "doi"),
        {"schema": "core"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    doi: Mapped[str | None] = mapped_column(Text)
    paper_id: Mapped[str | None] = mapped_column(Text)
    project_id: Mapped[str | None] = mapped_column(Text)
    route: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    url: Mapped[str | None] = mapped_column(Text)
    failure_reason: Mapped[str | None] = mapped_column(Text)
    priority: Mapped[int | None] = mapped_column(Integer)
    extra: Mapped[dict] = mapped_column(JSONB, default=dict)


# ══ The human verification layer (migration 0006) ══════════════════════════════

class HumanCheck(Base):
    """One person's judgement of one piece of parsed or extracted data. Append-only (a trigger
    refuses UPDATE and DELETE); a correction is a new row with ``supersedes``."""

    __tablename__ = "human_check"
    __table_args__ = (
        CheckConstraint(_in("target_kind", VERIFY_TARGET_KINDS), name="target_kind"),
        CheckConstraint(_in("verdict", VERIFY_VERDICTS), name="verdict"),
        CheckConstraint(f"problem IS NULL OR {_in('problem', VERIFY_PROBLEMS)}", name="problem"),
        CheckConstraint("labeler_kind = 'human'", name="labeler_kind"),
        Index("ix_human_check_target", "target_kind", "target_id"),
        Index("ix_human_check_paper", "paper_id"),
        {"schema": "verify"},
    )

    check_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    target_kind: Mapped[str] = mapped_column(Text)
    target_id: Mapped[str] = mapped_column(Text)
    field: Mapped[str | None] = mapped_column(Text)
    paper_id: Mapped[str | None] = mapped_column(Text)
    project_id: Mapped[str | None] = mapped_column(Text)
    verdict: Mapped[str] = mapped_column(Text)
    problem: Mapped[str | None] = mapped_column(Text)
    corrected: Mapped[dict | None] = mapped_column(JSONB)
    note: Mapped[str | None] = mapped_column(Text)
    shown: Mapped[dict | None] = mapped_column(JSONB)
    labeler_kind: Mapped[str] = mapped_column(Text, server_default="human")
    labeler: Mapped[str] = mapped_column(Text)
    key_id: Mapped[str | None] = mapped_column(Text)
    supersedes: Mapped[int | None] = mapped_column(ForeignKey("verify.human_check.check_id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
