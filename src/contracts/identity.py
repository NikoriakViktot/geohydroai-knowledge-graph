"""Contract v1: paper identity, aliases, files, cohorts, runs."""

from __future__ import annotations

from typing import Literal, get_args

from pydantic import BaseModel, ConfigDict, Field

IdentityStatus = Literal[
    "ok",                  # canonical paper, DOI known and consistent
    "no_doi",              # canonical paper without a DOI
    "title_doi_mismatch",  # the header DOI resolves to a work with a different title
    "duplicate",           # another paper_id is the canonical copy (see duplicate_of)
    "not_a_paper",         # not a scholarly work (e.g. an administrative document)
    "truncated_json",      # paper.json is cut off and must be regenerated
]
AliasType = Literal[
    "doi",             # normalised DOI (lower case, no resolver prefix)
    "doi_slug",        # DOI with "/" → "_"      (src/paper_3/_utils.doi_to_slug)
    "doi_slug_colon",  # DOI with "/" and ":" → "_" (recover_missing / download scripts)
    "file_stem",       # file name stem used across data/ (the v1 paper_id itself)
    "openalex_id",     # short OpenAlex work id, e.g. W3084364076
    "pdf_sha256",      # content address of the PDF
]
FileKind = Literal["pdf", "tei", "paper_json", "normalized", "enriched", "sodb", "nougat_regions"]
FileStatus = Literal["ok", "truncated", "duplicate_copy"]
RunKind = Literal["etl", "ingest", "reindex", "graph_build", "audit", "api_job"]
RunStatus = Literal["running", "succeeded", "failed", "partial"]

IDENTITY_STATUSES: tuple[str, ...] = get_args(IdentityStatus)
ALIAS_TYPES: tuple[str, ...] = get_args(AliasType)
FILE_KINDS: tuple[str, ...] = get_args(FileKind)
FILE_STATUSES: tuple[str, ...] = get_args(FileStatus)
RUN_KINDS: tuple[str, ...] = get_args(RunKind)
RUN_STATUSES: tuple[str, ...] = get_args(RunStatus)


class _Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PaperAlias(_Contract):
    alias_type: AliasType
    alias: str = Field(min_length=1)
    source: str = Field(description="where the alias was observed, e.g. 'paper_json.metadata.doi'")


class PaperFile(_Contract):
    kind: FileKind
    path: str = Field(description="path relative to the repository root")
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    size_bytes: int | None = Field(default=None, ge=0)
    status: FileStatus = "ok"


class PaperIdentity(_Contract):
    """One paper as the layer of truth knows it."""

    paper_id: str = Field(min_length=1, description="v1: the file stem used across data/")
    doi: str | None = Field(default=None, pattern=r"^10\.\d{4,9}/\S+$")
    title: str | None = None
    year: int | None = Field(default=None, ge=1500, le=2100)
    venue: str | None = None
    openalex_id: str | None = None
    identity_status: IdentityStatus
    duplicate_of: str | None = None
    aliases: list[PaperAlias] = []
    files: list[PaperFile] = []
    cohorts: list[str] = []


class CohortMember(_Contract):
    cohort: str = Field(min_length=1)
    paper_id: str = Field(min_length=1)
    source: str
