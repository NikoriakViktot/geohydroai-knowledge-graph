"""Contract v1: shared API objects (docs/api/SCHEMAS.md)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from src.contracts.identity import IdentityStatus, PaperIdentity
from src.contracts.research import Labeler, ProjectId, QuoteStatus


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


# ── evidence: verbatim spans, quotations, theses (docs/api/endpoints/evidence.md) ──

PassageKind = Literal["abstract", "paragraph", "figure", "table"]


class EvidenceSpan(_Contract):
    """Verbatim text of a corpus paper with where it is. Quote `text` exactly; never paraphrase it."""

    span_id: str = Field(description="sha256(paper_id + passage_id + text)[:16]")
    paper: PaperRef
    passage_id: str = Field(description="'abstract', 's<i>[.s<j>].p<k>' (paragraph), 'fig_<k>' or 'tab_<k>'; "
                                        "stable for one TEI file")
    kind: PassageKind
    section: str | None = None
    section_n: str | None = Field(default=None, description="section number as printed, e.g. '3.2'")
    page: int | None = Field(default=None, description="PDF page from GROBID coordinates; null when unknown")
    char_start: int | None = Field(default=None, description="offset of `text` in the passage text")
    char_end: int | None = None
    chunk_id: str | None = None
    text: str


class NumberCheck(_Contract):
    value: str
    found: bool = Field(description="found in the passage that holds the quotation")
    distance_chars: int | None = Field(default=None, description="characters from the quotation; 0 = inside it")
    context: str | None = None
    elsewhere: list[str] = Field(default=[], description="other passage_ids that contain the number (max 5)")


class Attribution(_Contract):
    cites_other_sources: bool = Field(description="the matched sentences cite other works: a possible secondary citation")
    in_text_refs: list[str] = []
    resolved_dois: list[str] = []


class Searched(_Contract):
    sections: int
    passages: int
    chars: int


class QuoteItem(_Contract):
    key: str | None = Field(default=None, description="echoed back, e.g. the cite key")
    source: str = Field(min_length=1, description="DOI, paper_id, or a cite key of `project_id`")
    quote: str = Field(min_length=1, max_length=5000,
                       description="the words claimed to be in the source; '…' or '...' marks an omission")
    expected_numbers: list[str] = Field(default=[], max_length=20)
    manuscript_sentence: str | None = None
    project_id: ProjectId | None = None


class OpenCitationsQuote(_Contract):
    open_citations_item: dict = Field(description="one item of an open_citations.json file; each of its "
                                                  "citations[].quotations becomes one QuoteItem")


class QuoteVerifyRequest(_Contract):
    items: list[QuoteItem | OpenCitationsQuote] = Field(min_length=1, max_length=50)
    project_id: ProjectId | None = Field(default=None, description="default project for cite-key sources")
    acquire_missing: bool = False


class QuoteResult(_Contract):
    key: str | None = None
    source: str
    quote: str
    status: QuoteStatus
    score: float | None = Field(default=None, description="1.0 for exact matches; similarity for FOUND_FUZZY")
    paper: PaperRef | None = None
    span: EvidenceSpan | None = None
    numbers: list[NumberCheck] = []
    attribution: Attribution | None = None
    text_source: Literal["corpus_tei", "oa_fetch", "none"]
    searched: Searched | None = None
    found_in: list[str] = Field(default=[], description="keys of other items of this request that quote the same "
                                                        "words for the same manuscript sentence and were found")
    detail: str | None = None


class NotChecked(_Contract):
    key: str | None = None
    quote: str
    reason: str


class QuoteVerifyResponse(_Contract):
    items: list[QuoteResult]
    not_checked: list[NotChecked] = []
    summary: dict[str, int]
    provenance: Provenance


class SectionInfo(_Contract):
    id: str = Field(description="section handle used in passage_ids, e.g. 's3' or 's3.s1'")
    n: str | None = None
    title: str
    level: int | None = None
    pages: list[int] = []
    paragraphs: int
    chars: int


class SectionsResponse(_Contract):
    paper: PaperRef
    sections: list[SectionInfo]
    has_abstract: bool
    figures: int
    tables: int
    formulas: int
    provenance: Provenance


class TextResponse(_Contract):
    paper: PaperRef
    spans: list[EvidenceSpan]
    truncated: bool
    matched_passages: int
    provenance: Provenance


class ThesesValidateRequest(_Contract):
    kind: Literal["theses", "atomic_claims"]
    project_id: ProjectId
    document: list | dict | str = Field(description="the parsed document, or its JSON/YAML text")
    authored_by: Labeler | None = Field(default=None, description="who wrote the document, when it does not say")


class ThesesValidateResponse(_Contract):
    valid: bool
    counts: dict[str, int]
    warnings: list[str] = []
    provenance: Provenance


class ReferenceEntry(_Contract):
    n: int = Field(description="position in the paper's reference list, from 1")
    xml_id: str = Field(description="TEI id; the target of in-text citation markers")
    raw: str | None = Field(default=None, description="the reference as printed, when GROBID kept it")
    title: str | None = None
    authors: list[str] = []
    year: int | None = None
    venue: str | None = None
    doi: str | None = Field(default=None, description="normalised; as parsed by GROBID, not verified")
    cited_in_text: int = Field(description="number of in-text citation markers that point at this entry")
    in_corpus: PaperRef | None = None
    match: Literal["doi", "title"] | None = Field(default=None, description="how `in_corpus` was found")


class ReferencesResponse(_Contract):
    paper: PaperRef
    references: list[ReferenceEntry]
    counts: dict[str, int]
    provenance: Provenance


class TableEntry(_Contract):
    table_id: str = Field(description="'tab_<k>', the passage id used by /text and /quotes/verify")
    xml_id: str | None = None
    label: str | None = None
    caption: str | None = None
    page: int | None = None
    rows: list[list[str]] = Field(description="cell text, row-major, as parsed by GROBID")
    facts: list[dict] | None = Field(default=None, description="numeric facts of the table; null = not computed "
                                                               "(planned, work package 1.8)")


class TablesResponse(_Contract):
    paper: PaperRef
    tables: list[TableEntry]
    provenance: Provenance
