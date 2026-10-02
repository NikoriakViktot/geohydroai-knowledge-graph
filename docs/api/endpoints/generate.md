# Generate — text written only from retrieved evidence

Every generated sentence must cite evidence ids from the pack it was given. A sentence without such support is marked `unsupported`; in `strict` mode it is removed.

Numbers, DOIs and citation keys are locked as tokens before the model sees the text and restored afterwards, the same technique as `src/paper_3/v2/translate.py`. The model cannot alter a value or invent a reference.

**Scope** `llm` for all. Each call consumes shared quota and records `provenance.llm`.

---

## `POST /generate/synthesis`
- **Status**: planned (phase 4, WP 4.4) · S (≤ 30 s) or **J**
- **Purpose**: answer a research question from the corpus, with a citation on every sentence. This is the dashboard's AI research synthesis, lifted out of Dash.

**Request**:

| Field | Type | Notes |
|---|---|---|
| `question` | string | |
| `filters` | `SearchFilters` | |
| `evidence_pack_id` | string? | reuse a pack from a previous call |
| `max_sources` | int | default 12 |
| `mode` | `strict \| annotated` | `strict` drops unsupported sentences; `annotated` keeps them flagged |
| `language` | `en \| uk` | output language; evidence stays in the source language |

**Response 200**:

| Field | Type | Notes |
|---|---|---|
| `answer` | `[{sentence, evidence_ids: string[], supported: bool}]` | |
| `evidence` | `EvidenceSpan[]` | the pack |
| `cited_dois` | string[] | DOIs that occur in the answer |
| `unverified_dois` | string[] | DOIs in the answer that are **not** in the pack. A non-empty list is a defect: report it |
| `limitations` | string | what the evidence does not cover |
| `coverage`, `retrieval_validity`, `provenance` | | |

**Errors**: `429 QUOTA_EXHAUSTED`; `424 SOURCE_UNAVAILABLE` when retrieval returned nothing usable (no generation happens then).

---

## `POST /generate/related-work`
- **Status**: planned (phase 4) · **J**
- **Purpose**: a related-work paragraph for a set of theses or a topic, with a table of the evidence used and the BibTeX for every cited work.
- **Request**: `{"project_id": string, "thesis_ids": string[] | null, "topic": string | null, "length_words": 250, "style": "author-year", "mode": "strict"}`.
- **Result artifacts**: `{"paragraph": [{sentence, evidence_ids, supported}], "evidence_table": [{span_id, doi, page, text}], "bibtex": string, "claims_to_check": [CitationOccurrence]}`.
- **Agent notes**:
  - The draft is input to a human author, not final text.
  - Run every resulting citation through `POST /claims/check` before submission.

---

## `POST /generate/rewrite-check`
- **Status**: planned (phase 4) · S
- **Purpose**: given an original and a rewritten paragraph and the evidence for it, flag claims that the rewrite strengthened, generalised or de-hedged beyond the evidence.
- **Request**: `{"original": string, "rewritten": string, "evidence_ids": string[] | null, "sources": string[] | null}`.
- **Response 200**: `{"issues": [{"sentence", "kind": "dehedged|scope_widened|number_changed|new_claim|citation_moved", "evidence_span_id"?, "explanation"}], "ok": bool, "provenance"}`.
