"""SQLAlchemy models of the layer of truth — schemas ``core`` and ``ops`` (migration 0001).

Enumerated columns are TEXT with CHECK constraints built from the contract Literal
types in src/contracts/identity.py, so the database accepts exactly what the
contract allows.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger, Boolean, CheckConstraint, DateTime, ForeignKey, Index, MetaData,
    SmallInteger, Text, func, text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from src.contracts.identity import (
    ALIAS_TYPES, FILE_KINDS, FILE_STATUSES, IDENTITY_STATUSES, RUN_KINDS, RUN_STATUSES,
)

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
        CheckConstraint("scopes <@ ARRAY['read','llm','write','admin']::text[]", name="scopes"),
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
