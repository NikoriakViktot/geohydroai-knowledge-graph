"""Contract v1: shared API objects (docs/api/SCHEMAS.md)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from src.contracts.identity import IdentityStatus, PaperIdentity


class _Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ValidationErrorItem(_Contract):
    loc: list[str | int]
    msg: str
    type: str


class Problem(_Contract):
    """application/problem+json body of every error."""

    type: str
    title: str
    status: int
    code: str
    detail: str
    instance: str | None = None
    errors: list[ValidationErrorItem] = []


class LLMUsage(_Contract):
    provider: str
    model: str
    prompt_sha256: str
    tokens_in: int | None = None
    tokens_out: int | None = None
    cached: bool = False
    repair_count: int = 0


class Provenance(_Contract):
    """Present in every successful response; record it with any result you use."""

    api_version: str
    git_commit: str | None
    corpus_manifest_id: str | None
    collection: str | None = None
    embedding_model: str | None = None
    kb_version: str | None = None
    identity_run_id: str | None = None
    llm: LLMUsage | None = None
    generated_at: datetime


class PaperRef(_Contract):
    paper_id: str
    doi: str | None = None
    title: str | None = None
    year: int | None = None
    venue: str | None = None
    identity_status: IdentityStatus


MatchKind = Literal["exact", "alias", "title_fuzzy"]


class ResolveResponse(_Contract):
    match: MatchKind
    score: float = Field(ge=0, le=1)
    paper: PaperIdentity
    canonical: PaperRef | None = Field(default=None, description="the paper to use when `paper` is a duplicate")
    provenance: Provenance


class ResolveItem(_Contract):
    key: str | None = Field(default=None, description="echoed back, e.g. a bib key")
    doi: str | None = None
    paper_id: str | None = None
    title: str | None = None
    year: int | None = None
    file: str | None = None
    openalex_id: str | None = None


class ResolveBatchRequest(_Contract):
    items: list[ResolveItem] = Field(min_length=1, max_length=500)


class ResolveBatchResult(_Contract):
    key: str | None
    status: Literal["found", "not_in_corpus", "invalid", "ambiguous"]
    match: MatchKind | None = None
    paper: PaperRef | None = None
    canonical: PaperRef | None = None
    detail: str | None = None


class ResolveBatchResponse(_Contract):
    results: list[ResolveBatchResult]
    summary: dict[str, int]
    provenance: Provenance
