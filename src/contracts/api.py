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


# ── bibliography: DOI metadata and verification (docs/api/endpoints/bibliography.md) ──

RegistryName = Literal["crossref", "openalex", "datacite"]


class DoiAuthor(_Contract):
    family: str
    given: str | None = None
    orcid: str | None = None


class DoiMetadata(_Contract):
    doi: str
    title: str | None = None
    authors: list[DoiAuthor] = []
    year_issued: int | None = Field(default=None, description="the registry's year: the earlier of online and print")
    year_online: int | None = None
    year_print: int | None = None
    date_online: str | None = Field(default=None, description="ISO date, possibly partial (2015-10-27, 2016-03)")
    date_print: str | None = None
    venue: str | None = None
    volume: str | None = None
    issue: str | None = None
    pages: str | None = None
    article_number: str | None = None
    type: str | None = None
    publisher: str | None = None
    is_oa: bool | None = None
    oa_status: str | None = None
    oa_url: str | None = None
    licence: str | None = None
    url: str | None = None
    sources: dict[str, RegistryName] = Field(default={}, description="which registry supplied each field")
    fetched: dict[str, Literal["network", "cache", "stale_cache", "not_found", "unavailable"]] = Field(
        default={}, description="per registry: how its answer was obtained, or why there is none")


class DoiResponse(DoiMetadata):
    in_corpus: PaperRef | None = None
    provenance: Provenance


class BibInput(_Contract):
    key: str | None = Field(default=None, description="echoed back; with project_id, a cite key to look up the DOI")
    doi: str | None = None
    title: str | None = None
    authors: str | list[str] | None = Field(default=None, description="BibTeX 'Family, G. and …', or a list")
    year: int | str | None = Field(default=None, description="'2024a' is read as 2024")
    journal: str | None = None
    volume: str | int | None = None
    issue: str | int | None = None
    pages: str | None = None
    bibtex: str | None = Field(default=None, description="one raw BibTeX entry instead of the fields")


class DoiVerifyRequest(_Contract):
    entries: list[BibInput] = Field(min_length=1, max_length=50)
    project_id: ProjectId | None = Field(default=None, description="lets entries without a DOI use their cite key")
    refresh: bool = Field(default=False, description="ignore cached registry answers")


class FieldDiff(_Contract):
    field: str
    given: str | None = None
    registry: str | None = None
    source: RegistryName | None = None
    severity: Literal["info", "minor", "major"]


DoiVerdict = Literal["VERIFIED", "VERIFIED_WITH_NOTES", "MISMATCH", "UNRESOLVED", "NOT_A_DOI"]


class DoiVerifyResult(_Contract):
    input_key: str | None = None
    doi: str | None = None
    verdict: DoiVerdict
    diffs: list[FieldDiff] = []
    notes: list[str] = []
    registry: DoiMetadata | None = None
    in_corpus: PaperRef | None = None


class DoiVerifyResponse(_Contract):
    results: list[DoiVerifyResult]
    summary: dict[str, int]
    provenance: Provenance


# ── graph (Neo4j projection, read-only; docs/api/endpoints/graph.md) ───────────

class GraphPaper(_Contract):
    paper_id: str | None = Field(default=None, description="null for a cited work outside the corpus")
    doi: str | None = None
    title: str | None = None
    year: int | None = None
    venue: str | None = None
    identity_status: str | None = Field(default=None, description="null for reference stubs")
    is_reference_stub: bool = False
    cited_by_count: int | None = None
    openalex_id: str | None = None


class GraphPaperHead(GraphPaper):
    study_type: str | None = None
    primary_country: str | None = None


class GraphEntityEdge(_Contract):
    canonical_id: str | None = None
    display_name: str | None = None
    family: str | None = None
    confidence: float | None = None
    role: Literal["used", "mentioned"] | None = Field(default=None, description="null when the extractor gave none")
    surface_form: str | None = None
    evidence: list[str] | None = None
    mention_in_evidence: bool | None = Field(default=None, description="the evidence contains the surface form; "
                                                                       "false means the snippet cannot support the edge")
    page: int | None = None
    section: str | None = None
    grounded: bool | None = Field(default=None, description="the surface form or display name occurs as a word in the "
                                                            "paper's TEI text; null = not checked")
    tei_mentions: int | None = None
    tei_evidence: list[str] | None = Field(default=None, description="up to 3 TEI sentences that mention it, verbatim")
    tei_page: int | None = None
    tei_section: str | None = None


class GraphAuthor(_Contract):
    name: str | None = None
    orcid: str | None = None
    position: str | None = None
    corresponding: bool | None = None
    institutions: list[str] = Field(default=[], description="all affiliations known for the author, not per paper")


class GraphTopic(_Contract):
    topic_id: str | None = None
    name: str | None = None
    score: float | None = None


class GraphFact(_Contract):
    fact_id: str | None = None
    metric: str | None = None
    canonical_id: str | None = None
    value: float | None = None
    raw_cell: str | None = None
    table_label: str | None = None
    row_context: str | None = None
    col_header: str | None = None
    page: int | None = None
    confidence: float | None = None


class GraphPaperResponse(_Contract):
    paper: GraphPaperHead
    authors: list[GraphAuthor] | None = None
    methods: list[GraphEntityEdge] | None = None
    sensors: list[GraphEntityEdge] | None = None
    metrics: list[GraphEntityEdge] | None = None
    topics: list[GraphTopic] | None = None
    countries: list[str] | None = None
    flood_events: list[dict] | None = None
    facts: list[GraphFact] | None = None
    counts: dict[str, int]
    provenance: Provenance


class CitationItem(GraphPaper):
    direction: Literal["out", "in"]


class CitationsResponse(_Contract):
    items: list[CitationItem]
    next_cursor: str | None = None
    counts: dict[str, int]
    provenance: Provenance


class EntityPaperItem(GraphPaper):
    confidence: float | None = None
    role: Literal["used", "mentioned"] | None = None
    surface_form: str | None = None
    evidence: list[str] | None = None
    mention_in_evidence: bool | None = None
    page: int | None = None
    score: float | None = Field(default=None, description="topic score (HAS_TOPIC only)")
    grounded: bool | None = None
    tei_mentions: int | None = None
    tei_evidence: list[str] | None = None
    tei_page: int | None = None


class EntityPapersResponse(_Contract):
    items: list[EntityPaperItem]
    count: int = Field(description="all matching papers, not just this page")
    next_cursor: str | None = None
    coverage: dict[str, int]
    provenance: Provenance


class NamedQueryInfo(_Contract):
    name: str
    description: str
    params: dict[str, dict]
    returns: list[str]
    one_of: list[str] = []


class NamedQueriesResponse(_Contract):
    queries: list[NamedQueryInfo]
    provenance: Provenance


class GraphQueryRequest(_Contract):
    params: dict = {}
    limit: int = Field(default=100, ge=1, le=1000)


class CypherRequest(_Contract):
    query: str = Field(min_length=1, max_length=20_000)
    params: dict = {}
    limit: int = Field(default=1000, ge=1, le=1000)


class TabularResponse(_Contract):
    columns: list[str]
    rows: list[list]
    truncated: bool
    provenance: Provenance


# ── metrics and ontology (docs/api/endpoints/metrics.md) ──────────────────────

class FactEvidence(_Contract):
    text: str | None = Field(default=None, description="the sentence, verbatim (text facts)")
    passage_id: str | None = None
    section: str | None = None
    table_label: str | None = None
    col_header: str | None = None
    row_context: list[str] = []
    page: int | None = None


class MetricFact(_Contract):
    metric: str = Field(description="canonical id, e.g. metric.nse")
    label: str | None = None
    value: float = Field(description="as a ratio for bounded metrics read from a percentage (94.2 % → 0.942)")
    value_hi: float | None = Field(default=None, description="upper end when the paper gives a range")
    raw_value: str | None = None
    unit: str | None = Field(default=None, description="a recognised unit, else null")
    unit_raw: str | None = Field(default=None, description="unrecognised text from the table column")
    qualifier: Literal["range", ">", "<", "≥", "≤", "≈"] | None = Field(
        default=None, description="'>' etc.: an inequality, usually a criterion rather than a result")
    range_verdict: Literal["ok", "suspect", "unknown_metric"]
    source: Literal["text", "table"]
    fact_id: str | None = None
    paper: PaperRef | None = None
    evidence: FactEvidence


class RejectedValue(_Contract):
    raw: str
    metric: str
    reason: str
    evidence: FactEvidence


class MetricsExtractRequest(_Contract):
    text: str | None = Field(default=None, max_length=50_000)
    tei_xml: str | None = Field(default=None, max_length=5_000_000)
    paper_id: str | None = None
    metrics: list[str] = Field(default=[], description="canonical ids or names; empty = all")


class MetricsExtractResponse(_Contract):
    facts: list[MetricFact]
    rejected: list[RejectedValue]
    paper: PaperRef | None = None
    provenance: Provenance


class MetricFactsResponse(_Contract):
    items: list[MetricFact]
    next_cursor: str | None = None
    summary: dict
    coverage: dict
    provenance: Provenance


class MetricDefinition(_Contract):
    canonical_id: str
    name: str
    aliases: list[str]
    range: dict | None = Field(default=None, description="{lo, hi}; null bound = unbounded")
    percent_scale: bool
    group: str | None = None
    in_registry: bool
    extracted_from_text: bool = Field(description="POST /metrics/extract finds this metric in prose")


class MetricOntologyResponse(_Contract):
    metrics: list[MetricDefinition]
    provenance: Provenance


class NormalizeTerm(_Contract):
    text: str = Field(min_length=1, max_length=200)
    expected_type: str | None = None
    context: str | None = Field(default=None, max_length=2000)


class NormalizeRequest(_Contract):
    terms: list[NormalizeTerm] = Field(min_length=1, max_length=200)
    allow_semantic: bool = False


class NormalizeResult(_Contract):
    text: str
    canonical_id: str | None = None
    display_name: str | None = None
    type: str | None = None
    match_type: str
    confidence: float


class NormalizeResponse(_Contract):
    results: list[NormalizeResult]
    provenance: Provenance


class OntologyEntity(_Contract):
    canonical_id: str
    display_name: str | None = None
    type: str | None = None
    aliases: list[str] = []
    definition: str | None = None


class OntologyEntitiesResponse(_Contract):
    items: list[OntologyEntity]
    count: int
    next_cursor: str | None = None
    provenance: Provenance


# ── search (Chroma projection; docs/api/endpoints/search.md) ──────────────────

ChunkType = Literal["abstract", "sentence", "paragraph", "section", "figure", "table", "formula"]
SearchableStatus = Literal["ok", "no_doi", "title_doi_mismatch", "truncated_json"]
RetrievalValidity = Literal["NOT_MEASURED", "MEASURED_BELOW_GATE", "VALIDATED"]


class SearchFilters(_Contract):
    year_from: int | None = None
    year_to: int | None = None
    paper_ids: list[str] | None = None
    dois: list[str] | None = None
    chunk_types: list[ChunkType] | None = None
    sections: list[str] | None = Field(default=None, description="prefix match on the section title")
    exclude_cohorts: list[str] = []
    identity_status: list[SearchableStatus] = Field(
        default=["ok", "no_doi", "title_doi_mismatch"],
        description="duplicates and non-papers are never searched")


class ChunkSearchRequest(_Contract):
    query: str = Field(min_length=3, max_length=1000)
    k: int = Field(default=20, ge=1, le=200)
    filters: SearchFilters = SearchFilters()
    min_score: float | None = Field(default=None, ge=-1, le=1)
    project_id: ProjectId | None = None


class ChunkHit(_Contract):
    chunk_id: str
    score: float = Field(description="1 − cosine distance")
    chunk_type: str | None = None
    paper: PaperRef | None = None
    section: str | None = None
    page: int | None = None
    text: str


class SearchCoverage(_Contract):
    papers_in_slice: int
    chunks_in_slice: int
    papers_without_chunks: int = Field(description="papers allowed by the filters that have no chunks in the index")
    filters_applied: dict
    excluded_cohorts: list[str] = []
    note: str | None = None


class ChunkSearchResponse(_Contract):
    hits: list[ChunkHit]
    coverage: SearchCoverage
    retrieval_validity: RetrievalValidity
    validity_detail: dict = {}
    provenance: Provenance


class PaperSearchRequest(_Contract):
    queries: list[str] = Field(min_length=1, max_length=20)
    k: int = Field(default=20, ge=1, le=200)
    max_candidates: int = Field(default=500, ge=10, le=5000)
    filters: SearchFilters = SearchFilters()
    aggregate: Literal["max", "mean", "count"] = "max"
    project_id: ProjectId | None = None


class PaperHit(_Contract):
    paper_id: str
    paper: PaperRef | None = None
    score: float
    hits: int
    queries_matched: int
    best_chunks: list[ChunkHit]


class PaperSearchResponse(_Contract):
    papers: list[PaperHit]
    coverage: SearchCoverage
    retrieval_validity: RetrievalValidity
    validity_detail: dict = {}
    provenance: Provenance


class SimilarRequest(_Contract):
    paper_id: str | None = None
    doi: str | None = None
    k: int = Field(default=20, ge=1, le=200)
    filters: SearchFilters = SearchFilters()


# ── locate: where can I read this paper? (docs/api/endpoints/acquisition.md) ───

class FileLocation(_Contract):
    paper_id: str
    kind: Literal["pdf", "tei"]
    path: str = Field(description="absolute path on the API host")
    windows_path: str | None = Field(default=None, description="\\\\wsl.localhost\\<distro>\\… when the API runs in WSL")
    exists: bool
    status: str = Field(description="ok, or e.g. duplicate_copy")


class OALocation(_Contract):
    url: str
    kind: Literal["pdf", "landing"]
    version: str | None = Field(default=None, description="publishedVersion | acceptedVersion | submittedVersion")
    license: str | None = None
    host: str | None = Field(default=None, description="publisher | repository")
    source: str = Field(description="openalex, unpaywall, arxiv, or several joined with '+'")


class LocateResponse(_Contract):
    query: str
    doi: str | None = None
    resolved_from: Literal["doi", "url", "pii", "arxiv", "paper_id", "landing_page"] | None = None
    title: str | None = None
    year: int | None = None
    venue: str | None = None
    in_corpus: PaperRef | None = None
    files: list[FileLocation] = []
    is_oa: bool | None = None
    oa_status: str | None = None
    best_pdf_url: str | None = None
    open_access: list[OALocation] = []
    doi_url: str | None = None
    notes: list[str] = []
    provenance: Provenance


# ── manuscripts (docs/api/endpoints/bibliography.md) ───────────────────────────

class ManuscriptCitationsRequest(_Contract):
    manuscript: str = Field(min_length=1, max_length=2_000_000, description="markdown")
    bibtex: str = Field(max_length=5_000_000)
    project_id: ProjectId | None = None


class ManuscriptCitation(_Contract):
    cite_text: str = Field(description="as written, e.g. 'Johnson et al. (2019)' or '(Bates 2022)'")
    authors: str
    year: str = Field(description="with its suffix, e.g. 2024a")
    status: Literal["resolved", "ambiguous", "missing"]
    cite_key: str | None = None
    candidates: list[str] = []
    doi: str | None = None
    section: str | None = None
    sentence: str
    line: int
    quoted: list[str] = Field(default=[], description="words in quotation marks in the sentence")


class ManuscriptCitationsResponse(_Contract):
    occurrences: list[ManuscriptCitation]
    missing_keys: list[dict]
    uncited_entries: list[str]
    summary: dict[str, int]
    provenance: Provenance


class BibFormatRequest(_Contract):
    dois: list[str] = Field(min_length=1, max_length=100)
    project_id: ProjectId | None = Field(default=None, description="avoid keys already used in this project")
    key_style: Literal["Surname_YYYY"] = "Surname_YYYY"


class BibFormatEntry(_Contract):
    doi: str
    key: str | None = None
    bibtex: str | None = None
    collision: bool = False
    notes: list[str] = []
    in_corpus: PaperRef | None = None


class BibFormatResponse(_Contract):
    entries: list[BibFormatEntry]
    provenance: Provenance


class BibRenderRequest(_Contract):
    bibtex: str = Field(min_length=1, max_length=5_000_000)
    keys: list[str] | None = None
    manuscript: str | None = Field(default=None, max_length=2_000_000)
    style: Literal["apa", "agu", "copernicus", "elsevier-harvard"] = "apa"


class RenderedReference(_Contract):
    key: str
    text: str


class BibRenderResponse(_Contract):
    references: list[RenderedReference]
    unresolved_keys: list[str] = []
    uncited_entries: list[str] = []
    provenance: Provenance


class BibAuditRequest(_Contract):
    bibtex: str = Field(min_length=1, max_length=5_000_000)
    project_id: ProjectId | None = None
    search_missing: bool = Field(default=True, description="look up DOIs for entries that have none (Crossref)")


class BibAuditEntry(_Contract):
    key: str
    type: str
    status: Literal["ok", "fix", "unresolved"]
    doi: str | None = None
    verdict: DoiVerdict | None = None
    suggested_doi: str | None = None
    problems: list[str] = []
    warnings: list[str] = []
    suggested_bibtex: str | None = None


class BibAuditResponse(_Contract):
    entries: list[BibAuditEntry]
    summary: dict[str, int]
    mixed_field_names: list[str] = []
    provenance: Provenance


class EntityMention(_Contract):
    canonical_id: str
    display_name: str | None = None
    surface_form: str | None = None
    role: Literal["used", "mentioned"] | None = None
    confidence: float | None = None
    grounded: bool | None = Field(default=None, description="the term occurs as a word in the paper's TEI text; "
                                                            "null = not checked")
    tei_mentions: int | None = None
    tei_evidence: list[str] = []
    evidence: list[str] = Field(default=[], description="the extractor's own snippets (often miss the mention)")
    page: int | None = None


class ExtractorLabel(_Contract):
    label: str
    confidence: float | None = None
    source: str | None = None


class PaperEntitiesResponse(_Contract):
    paper: PaperRef
    methods: list[EntityMention]
    sensors: list[EntityMention]
    metrics: list[EntityMention] = Field(description="metric mentions; values are in /metrics/facts and /metrics/extract")
    task: ExtractorLabel | None = None
    study_type: ExtractorLabel | None = None
    study_area: dict
    source_file: str
    provenance: Provenance
