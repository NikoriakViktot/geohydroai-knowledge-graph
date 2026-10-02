# Shared schemas

Notation: `field: type` · `?` = optional/nullable · `[]` = list · enums in `A | B`.

Each schema states its **contract status**:
- **implemented** — a pydantic model exists in `src/contracts/`; its JSON Schema is in `contracts/schemas/`; CI checks it.
- **planned** — this document is the spec until the model lands. The migration that adds the model is named.

Contracts are strict: unknown fields are rejected (`extra = "forbid"`) and enumerations are closed.

---

## Problem
Contract status: planned (API phase 1). Returned for every error ([README §5](README.md#5-errors)).

| Field | Type | Notes |
|---|---|---|
| `type` | string (URI) | stable per `code` |
| `title` | string | short human summary |
| `status` | int | HTTP status |
| `code` | string | machine code, e.g. `NOT_IN_CORPUS` |
| `detail` | string | what exactly went wrong |
| `instance` | string | request id |
| `errors` | `ValidationError[]` | for `422`: `{loc: (string\|int)[], msg: string, type: string}` |

## Provenance
Contract status: planned (phase 1). Present in every successful response.

| Field | Type | Notes |
|---|---|---|
| `api_version` | string | semver of the service |
| `git_commit` | string | commit of the running service |
| `corpus_manifest_id` | string | sha256 over (paper ids, retrieval rules, embedding model, collection) |
| `collection` | string? | Chroma collection answered from, e.g. `flood_papers_768d_v2` |
| `embedding_model` | string? | `allenai/specter2_base@3447645e` |
| `kb_version` | string? | ontology / knowledge-base version |
| `identity_run_id` | uuid? | `ops.run` id of the identity snapshot used |
| `llm` | `LLMUsage?` | `{provider, model, prompt_sha256, tokens_in, tokens_out, cached: bool, repair_count: int}` |
| `generated_at` | datetime | UTC |

## Coverage
Contract status: planned (phase 1). The denominator of any "found / not found" statement.

| Field | Type | Notes |
|---|---|---|
| `papers_in_slice` | int | papers that passed the filters and were searched |
| `chunks_in_slice` | int? | chunks searched (vector search) |
| `filters_applied` | object | the effective filters after normalisation |
| `excluded_cohorts` | string[] | e.g. `["paper_3"]` for prevalence endpoints |

## RetrievalValidity
`VALIDATED | UNVALIDATED | NOT_MEASURED`. Paired with `validity_detail: {recall: float?, controls_total: int?, controls_found: int?, gate_snapshot_id: string?}`.

- `VALIDATED`: positive-control recall ≥ 0.90 measured for this collection + manifest (+ project gate when a `project_id` is given).
- `UNVALIDATED`: measured and failed, e.g. the 2026-09 Paper 3 run had 7/23 = 30 %.
- `NOT_MEASURED`: no measurement exists. Treat it exactly like `UNVALIDATED` for absence claims.

---

## PaperIdentity
Contract status: **implemented**: `src/contracts/identity.py`, [`contracts/schemas/PaperIdentity.v1.json`](../../contracts/schemas/PaperIdentity.v1.json). Data: `core.paper`, `core.paper_alias`, `core.paper_file`, `core.cohort_member`.

| Field | Type | Notes |
|---|---|---|
| `paper_id` | string | corpus id (v1: file stem) |
| `doi` | string? | normalised; `null` when unknown **or** when the header DOI names another work |
| `title` | string? | |
| `year` | int? | 1500–2100 |
| `venue` | string? | |
| `openalex_id` | string? | short id, e.g. `W3084364076` |
| `identity_status` | `ok \| no_doi \| title_doi_mismatch \| duplicate \| not_a_paper \| truncated_json` | |
| `duplicate_of` | string? | canonical `paper_id` when `identity_status = duplicate` |
| `aliases` | `PaperAlias[]` | `{alias_type: doi\|doi_slug\|doi_slug_colon\|file_stem\|openalex_id\|pdf_sha256, alias, source}` |
| `files` | `PaperFile[]` | `{kind: pdf\|tei\|paper_json\|normalized\|enriched\|sodb\|nougat_regions, path, sha256?, size_bytes?, status: ok\|truncated\|duplicate_copy}` |
| `cohorts` | string[] | e.g. `["paper_3"]` (harvested for the Kakhovka report; excluded from prevalence counts) |

Rules:
- A paper with `identity_status = duplicate` is never returned as a search hit; its canonical copy is.
- A `not_a_paper` record is never returned by search, graph or evidence endpoints.

## PaperRef
Contract status: planned. Compact form used inside hits and lists: `{paper_id, doi?, title?, year?, venue?, identity_status}`.

## BBox
`{page: int, x: float, y: float, w: float, h: float}`. PDF points, from GROBID coordinates; Nougat never supplies coordinates.

## EvidenceSpan
Contract status: **implemented** (`src/contracts/api.py`, [`contracts/schemas/EvidenceSpan.v1.json`](../../contracts/schemas/EvidenceSpan.v1.json)). Returned by `/papers/{paper_id}/text` and inside `QuoteResult`.

| Field | Type | Notes |
|---|---|---|
| `span_id` | string | sha256(paper_id + passage_id + text)[:16] |
| `paper` | `PaperRef` | |
| `passage_id` | string | `abstract`, `s<i>[.s<j>].p<k>` (paragraph k of section s<i>), `fig_<k>`, `tab_<k>`; stable for one TEI file |
| `kind` | `abstract \| paragraph \| figure \| table` | |
| `section` / `section_n` | string? | TEI section title and printed number |
| `page` | int? | PDF page from GROBID coordinates; `null` when unknown (abstracts usually) |
| `char_start` / `char_end` | int? | offsets of `text` in the passage text |
| `chunk_id` | string? | Chroma chunk (planned: filled by search hits) |
| `text` | string | verbatim source text, never paraphrased |

Planned: `coords: BBox[]`.

## ChunkHit
Contract status: planned (phase 1): `{chunk_id, paper: PaperRef, chunk_type: abstract|sentence|paragraph|section|figure|table|formula, section?, page?, bbox?, text, score: float}`. `score` is 1 − cosine distance.

---

## Job
Contract status: planned (phase 2, `ops.job`).

| Field | Type | Notes |
|---|---|---|
| `job_id` | string | ULID |
| `type` | `ingest \| acquire \| discovery_search \| snowball \| screen \| theses_extract \| theses_evidence_run \| theses_novelty \| claims_check_batch \| bib_audit \| metrics_llm \| bundle_import \| rebuild_graph \| rebuild_vectors \| rebuild_analytics \| integrity_check \| discover_and_ingest \| generate_related_work` | |
| `owner` | string | consumer of the API key |
| `project_id` | string? | |
| `params` | object | the validated request |
| `idempotency_key` | string? | |
| `status` | `queued \| running \| succeeded \| partial \| failed \| cancelled` | |
| `steps` | `JobStep[]` | `{name, status: pending\|running\|succeeded\|skipped_up_to_date\|failed\|cancelled, attempts, started_at?, finished_at?, error?: Problem, outputs?: object}` |
| `progress` | float | 0–1 |
| `artifacts` | object | e.g. `{"neo4j": {"merged_nodes": 41}, "chroma": {"upserted": 312}, "report": "/v1/jobs/…/report"}` |
| `resource_class` | `cpu \| gpu \| grobid \| llm \| network` | |
| `created_at`, `started_at?`, `finished_at?` | datetime | |
| `provenance` | `Provenance` | |

`JobEvent` (SSE `data:` line): `{job_id, seq, at, step?, status?, progress?, message}`.

---

## QuoteItem / QuoteResult
Contract status: **implemented** (`contracts/schemas/QuoteVerifyRequest.v1.json`, `QuoteVerifyResponse.v1.json`; endpoint `POST /quotes/verify`). Results are not stored.

`QuoteItem`:

| Field | Type | Notes |
|---|---|---|
| `key` | string? | echoed back |
| `source` | string | DOI, `paper_id` (or a historical file stem), or a cite key of `project_id` |
| `quote` | string | the words claimed to be in the source; `…` (or `...`) marks an omission and the fragments are matched in order |
| `expected_numbers` | string[] | numbers to find near the quote, e.g. `["5.7", "-0.8"]`; the sign must agree; Unicode minus and decimal comma accepted |
| `manuscript_sentence` | string? | the citing sentence (echo only) |
| `project_id` | `ProjectId?` | needed for cite-key sources; defaults to the request's `project_id` |

`QuoteResult`:

| Field | Type | Notes |
|---|---|---|
| `key`, `source`, `quote` | | echoed |
| `status` | `FOUND_EXACT \| FOUND_NORMALIZED \| FOUND_FUZZY \| NOT_FOUND \| SOURCE_UNAVAILABLE` | normalisation = NFKC, ligatures, quotes, dashes, soft hyphens, whitespace |
| `score` | float? | 1.0 for exact and normalised matches; the similarity for `FOUND_FUZZY` (≥ 0.92 accepted) |
| `paper` | `PaperRef?` | the resolved source |
| `span` | `EvidenceSpan?` | the source's own sentences around the match |
| `numbers` | `NumberCheck[]` | `{value, found: bool, distance_chars?: int, context?: string, elsewhere: string[]}`. `found` refers to the matched passage; `elsewhere` lists other passage ids that contain the number |
| `attribution` | `Attribution?` | `{cites_other_sources: bool, in_text_refs: string[], resolved_dois: string[]}`. `true` means the matched sentences themselves cite others, i.e. possibly a **secondary citation** |
| `text_source` | `corpus_tei \| oa_fetch \| none` | `oa_fetch` is planned (phase 2) |
| `searched` | `{sections: int, passages: int, chars: int}?` | what was searched |
| `found_in` | string[] | for `NOT_FOUND`: keys of other items of the request that quote the same words for the same manuscript sentence and were found |
| `detail` | string? | why, for `NOT_FOUND`, `SOURCE_UNAVAILABLE` and `FOUND_FUZZY` |

## DoiMetadata / DoiVerifyResult
Contract status: **implemented** (`contracts/schemas/DoiMetadata.v1.json`, `DoiResponse.v1.json`, `DoiVerifyRequest.v1.json`, `DoiVerifyResponse.v1.json`; migration 0004 `biblio.http_cache`).

`DoiMetadata` fields:
- `doi`, `title`;
- `authors: [{family, given?, orcid?}]`;
- `year_issued?`, `year_online?`, `year_print?`, `date_online?`, `date_print?`;
- `venue?`, `volume?`, `issue?`, `pages?`, `article_number?`, `type?`, `publisher?`;
- `is_oa?`, `oa_status?`, `oa_url?`, `licence?`, `url?`;
- `sources: {field: crossref|openalex|datacite}`;
- `fetched: {registry: network|cache|stale_cache|not_found|unavailable}`.

`DoiVerifyResult`:

| Field | Type | Notes |
|---|---|---|
| `input_key` | string? | bib key from the request |
| `doi` | string? | normalised |
| `verdict` | `VERIFIED \| VERIFIED_WITH_NOTES \| MISMATCH \| UNRESOLVED \| NOT_A_DOI` | |
| `diffs` | `FieldDiff[]` | `{field, given, registry, source, severity: info\|minor\|major}` |
| `notes` | string[] | e.g. "online 2015-10-27, print 2016-03 (vol. 37): …" |
| `registry` | `DoiMetadata?` | |
| `in_corpus` | `PaperRef?` | canonical paper when the DOI is in the corpus |

## BibEntry
Contract status: planned (migration 0002): `{project_id, key, type: article|book|incollection|inproceedings|dataset|misc|techreport, doi?, fields: {title, author, year, journal?, volume?, number?, pages?, url?, publisher?, note?}, verification?: {status, method, verified_on, labeler_kind, labeler}}`. Key convention `Surname_YYYY` (`Wilson_Sader_2002`, `UNOSAT_3616_2023`); aliases in `biblio.cite_key`.

---

## Thesis / AtomicClaim
Contract status: **implemented** (`src/contracts/research.py`, migration 0003, `contracts/schemas/Thesis.v1.json`, `AtomicClaim.v1.json`); 233 theses and 139 atomic claims imported 2026-10-02. The implemented relation vocabulary is wider than the sketch below: `SUPPORTED_BY, SUPPORTS, COMPARATOR, NEEDS_SOURCE, METHOD_FROM, METHOD, DATASET_DOCUMENTATION, BACKGROUND, CONTRASTS, CONTRASTS_WITH, DEFINITION, LIMITATION`; ref status `verified | to_verify | missing | unknown`; `kind` adds `report`. These replace the three ad-hoc schemas (`theses.yaml`, `theses.json` + `atomic_claims.yaml`, `theses_v4.json`).

`Thesis`:

| Field | Type | Notes |
|---|---|---|
| `project_id` | string | namespace |
| `thesis_id` | string | e.g. `TH-INT-01` (unique within the project only) |
| `kind` | `literature \| article \| novelty` | |
| `section` | string | manuscript section |
| `category` | `background \| comparator \| method \| data \| result \| interpretation` | |
| `priority` | `high \| medium \| low` | |
| `statement` | string | |
| `quantitative` | string? | |
| `tables` | string[] | |
| `needs` | string? | |
| `refs` | `[{key, relation: SUPPORTED_BY\|METHOD_FROM\|COMPARATOR\|CONTRASTS_WITH\|NEEDS_SOURCE, status: verified\|VERIFY\|missing}]` | **objects, never a "Key[REL:status]" string** |
| `search_queries` | string[] | **a list, never a "q1 \| q2" string** |
| `authored_by` | `{labeler_kind: human\|model\|rule\|import, labeler}` | |

`AtomicClaim`:
- `{project_id, atomic_id, thesis_id, statement, required_roles: string[], manuscript_relevance: CORE|SUPPORTING|SUPPLEMENTARY|DROP, key_terms_primary: string[], key_terms_support: string[][], negative_terms: string[], extra_queries: string[], counterevidence_queries: string[], queries_version, authored_by}`.
- The first family of `key_terms_primary` must be the distinguishing concept, never a metric or a generic word.

## CitationOccurrence
Contract status: **implemented** (`contracts/schemas/CitationOccurrence.v1.json`): `{project_id, manuscript_version?, section, sentence, cite_key?, cite_text: "Lehnigk et al. (2026)", quoted: string[], work_id?, doi?}`.

## ClaimCheckResult
Contract status: planned (phase 4, `evidence.claim_check`).

| Field | Type | Notes |
|---|---|---|
| `verdict` | `SUPPORTED \| PARTIALLY_SUPPORTED \| OVERSTATED \| CONTRADICTED \| NOT_FOUND_IN_SOURCE \| SOURCE_UNAVAILABLE` | |
| `evidence` | `EvidenceSpan[]` | verbatim source passages the verdict rests on |
| `explanation` | string | must reference evidence by `span_id` |
| `suggested_rewrite` | string? | a sentence the source does support |
| `secondary_citation` | bool | the supporting passage itself cites another work |
| `labeler` | `{labeler_kind: model, labeler: <model id>}` | |
| `provenance` | `Provenance` | |

## ScreeningLabel
Contract status: **implemented** (`contracts/schemas/ScreeningLabel.v1.json`; 1,419 labels imported 2026-10-02). Implemented fields: `role` from `SUPPORTS | CONTRASTS | BACKGROUND | COMPARATOR | METHOD_FROM | DATASET_DOCUMENTATION | DEFINITION | LIMITATION | NOT_RELEVANT` (older names kept in `role_raw`), a separate `relevance` (`RELEVANT | PARTIALLY_RELEVANT | NOT_RELEVANT | UNKNOWN`) and `labeler {labeler_kind, labeler}`. Original sketch: `{project_id, subject: {thesis_id?|atomic_id?|query?}, paper: PaperRef, role: SUPPORTS|CONTRASTS|METHOD_RELEVANT|ANALOGUE|BACKGROUND|NOT_RELEVANT, confidence?, quote?, quote_verified: bool, rationale?, labeler_kind: human|model|model_assisted_external|rule, labeler, prompt_sha256?, run_id}`. **There are no human labels in the corpus as of 2026-10-02.** Every imported label is `model`.

## Candidate
Contract status: planned (phase 3): `{candidate_id, doi?, title, year?, venue?, abstract?, openalex_id?, is_oa?, oa_url?, licence?, cited_by_count?, source: openalex|crossref|arxiv|snowball|missing_reference, matched_queries: string[], score: float, embedding_similarity?: float, screening?: ScreeningLabel, corpus_status: in_corpus|stub_in_graph|new, review_status: new|screened|accepted|rejected|needs_manual|acquired|ingested|failed}`.

## Acquisition
Contract status: planned (migration 0002/0003, `core.acquisition`): `{doi?, paper_id?, route: unpaywall|europepmc|openalex|publisher|arxiv|wayback_oa|manual_upload|legacy_unknown, url?, oa_status?, licence?, version?: published|accepted|submitted, sha256?, fetched_at?, status: downloaded|duplicate|needs_manual|failed, failure_reason?}`. Paywalled works are never fetched by circumvention; they end as `needs_manual`.

## MetricFact
Contract status: planned (phase 1): `{metric: <canonical id, e.g. metric.nse>, value: float, raw_value: string, unit?, range_verdict: ok|suspect|unknown_metric, reason?, paper: PaperRef, evidence: EvidenceSpan, source: text|table, table_locator?: string}`. Valid ranges: NSE, KGE, R² ≤ 1 (no lower bound); kappa −1…1; OA, F1, IoU 0…1. Values outside the range are flagged `suspect`, never rescaled.

## GraphBundle
Contract status: planned (phase 4): `{producer, project_id, generated_at, nodes: [{id, type, label?, props?}], edges: [{source, target, relation, props?}]}`. Ids are prefixed with the node type (`TH:`, `REF:`, `CL:` …). Written to Neo4j under `:Bundle*` labels with `project_id`, MERGE only.
