"""Contract v1: research data moved out of the per-paper folders (API_PLAN_v1/11).

Vocabularies are the ones found in the data on 2026-10-02 (screening of
floodstate-eo Paper 3, the Kakhovka report audit, Article 1). Older names are
kept verbatim in `*_raw` fields and normalised here, never silently dropped.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal, get_args

from pydantic import BaseModel, ConfigDict, Field

ProjectId = Literal["floodstate-eo:paper3", "kakhovka-terrain:paper2", "swot-dnipro:paper1",
                    "kakhovka-report:v1", "article1"]
LabelerKind = Literal["human", "model", "model_assisted_external", "rule", "import", "unknown"]
ThesisKind = Literal["literature", "report", "article", "novelty"]
RefRelation = Literal["SUPPORTED_BY", "SUPPORTS", "COMPARATOR", "NEEDS_SOURCE", "METHOD_FROM", "METHOD",
                      "DATASET_DOCUMENTATION", "BACKGROUND", "CONTRASTS", "CONTRASTS_WITH", "DEFINITION",
                      "LIMITATION"]
RefStatus = Literal["verified", "to_verify", "missing", "unknown"]
EvidenceRole = Literal["SUPPORTS", "CONTRASTS", "BACKGROUND", "COMPARATOR", "METHOD_FROM",
                       "DATASET_DOCUMENTATION", "DEFINITION", "LIMITATION", "NOT_RELEVANT"]
Relevance = Literal["RELEVANT", "PARTIALLY_RELEVANT", "NOT_RELEVANT", "UNKNOWN"]
QuoteStatus = Literal["FOUND_EXACT", "FOUND_NORMALIZED", "FOUND_FUZZY", "NOT_FOUND", "SOURCE_UNAVAILABLE"]
CitationVerdict = Literal["VERIFIED", "FIX", "WORDING", "OPEN"]
BibVerdict = Literal["verified", "verified_with_notes", "mismatch", "unresolved"]

PROJECT_IDS: tuple[str, ...] = get_args(ProjectId)
LABELER_KINDS: tuple[str, ...] = get_args(LabelerKind)
THESIS_KINDS: tuple[str, ...] = get_args(ThesisKind)
REF_RELATIONS: tuple[str, ...] = get_args(RefRelation)
REF_STATUSES: tuple[str, ...] = get_args(RefStatus)
EVIDENCE_ROLES: tuple[str, ...] = get_args(EvidenceRole)
RELEVANCES: tuple[str, ...] = get_args(Relevance)
QUOTE_STATUSES: tuple[str, ...] = get_args(QuoteStatus)
CITATION_VERDICTS: tuple[str, ...] = get_args(CitationVerdict)
BIB_VERDICTS: tuple[str, ...] = get_args(BibVerdict)

#: Older relation names (4-relation scheme of the Kakhovka report) → current roles.
LEGACY_ROLES = {"CONTRADICTS": "CONTRASTS", "METHOD_RELEVANT": "METHOD_FROM", "ANALOGUE": "COMPARATOR"}


class _Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Labeler(_Contract):
    labeler_kind: LabelerKind
    labeler: str = Field(min_length=1, description="model id, tool name or the role of a person")


class ThesisRef(_Contract):
    key: str = Field(min_length=1)
    relation: RefRelation
    status: RefStatus
    status_raw: str | None = None


class Thesis(_Contract):
    project_id: ProjectId
    thesis_id: str = Field(min_length=1)
    kind: ThesisKind
    statement: str = Field(min_length=10)
    section: str | None = None
    category: str | None = None
    priority: Literal["high", "medium", "low"] | None = None
    quantitative: str | None = None
    tables: list[str] = []
    needs: str | None = None
    refs: list[ThesisRef] = []
    search_queries: list[str] = []
    extra: dict = {}
    authored_by: Labeler


class AtomicClaim(_Contract):
    project_id: ProjectId
    atomic_id: str = Field(min_length=1)
    thesis_id: str
    statement: str = Field(min_length=10)
    required_roles: list[EvidenceRole] = []
    manuscript_relevance: Literal["CORE", "SUPPORTING", "SUPPLEMENTARY", "DROP"] | None = None
    key_terms_primary: list[str] = []
    key_terms_support: list[list[str]] = []
    negative_terms: list[str] = []
    extra_queries: list[str] = []
    counterevidence_queries: list[str] = []
    queries_version: str | None = None
    authored_by: Labeler


class PositiveControl(_Contract):
    project_id: ProjectId
    thesis_id: str
    doi: str
    relevance: str | None = None
    expected_stage: str | None = None
    difficulty: str | None = None
    source_of_seed: str | None = None
    manually_checked: bool | None = None
    evidence: dict = {}


class CitationOccurrence(_Contract):
    project_id: ProjectId
    cite_key: str
    section: str | None = None
    sentence: str
    quoted: list[str] = []
    doi: str | None = None
    extra: dict = {}


class ScreeningLabel(_Contract):
    project_id: ProjectId
    subject_kind: Literal["thesis", "atomic_claim", "query"]
    subject_id: str
    paper_id: str | None = None
    doi: str | None = None
    role: EvidenceRole | None = None
    role_raw: str | None = None
    relevance: Relevance
    confidence: float | None = None
    quote: str | None = None
    quote_verified: bool | None = None
    rationale: str | None = None
    prompt_sha256: str | None = None
    extra: dict = {}
    labeler: Labeler


class QuoteCheck(_Contract):
    project_id: ProjectId
    cite_key: str
    doi: str | None = None
    claim_use: str | None = None
    verdict: CitationVerdict
    finding: str
    checked_on: date
    labeler: Labeler


class LiteratureNumber(_Contract):
    project_id: ProjectId
    number_id: str
    paper_id: str | None = None
    doi: str | None = None
    cite_key: str | None = None
    quantity_name: str | None = None
    value: float | None = None
    value_raw: str
    unit: str | None = None
    window_text: str | None = None
    page: str | None = None
    extra: dict = {}
    verified_by: Labeler | None = None


class TechnicalSource(_Contract):
    source_id: str
    cite_as: str
    source_type: str
    title: str | None = None
    identifier: str | None = None
    issuer: str | None = None
    url: str | None = None
    status: str | None = None
    extra: dict = {}


class BibVerification(_Contract):
    project_id: ProjectId | None = None
    cite_key: str | None = None
    doi: str | None = None
    verdict: BibVerdict
    checked_against: Literal["crossref", "openalex", "datacite", "registry_mix", "fulltext", "url"]
    details: dict = {}
    checked_at: datetime | date | None = None
    labeler: Labeler
