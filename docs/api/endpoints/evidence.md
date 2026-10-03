# Evidence — quotes, claims, theses, novelty

This group turns "the paper says X" into a checked, stored and citable statement.

**Backing code**:
- `src/paper_3/evidence.verify_quote`: NFKC normalisation; exact match, then a sliding window accepted at ≥ 0.92 similarity.
- `classify_relation` / `screen` for LLM adjudication.
- The deterministic verdict and gate rules from `gap_matrix`, `acceptance` and `export`.

**Backing data**: `evidence.*` and `project.*` in Postgres (migration 0003).

**Regression fixtures**: the manual verification of 2026-10-01 (`citation_verification.md`). The endpoints must reproduce it.

| Source | Expected result |
|---|---|
| Johnson 2019 | `FOUND_EXACT` |
| Olofsson 2014 | `FOUND_EXACT` |
| Zheng 2018 | `FOUND_EXACT`; "by design" is `OVERSTATED` |
| Bates 2022 | `FOUND_EXACT` |
| Iqbal 2023 → Hawker 2022 | `FOUND_EXACT` with `attribution.cites_other_sources = true` and `resolved_dois = ["10.1088/1748-9326/ac4d4f"]` |
| Lehnigk 2026 (Discussion) | `CONTRADICTED` |
| Lefebvre 2019 "71 %" | `SOURCE_UNAVAILABLE`: the paper is not in the corpus. The 2026-10-01 check used its OpenAlex abstract. |

---

## `POST /quotes/verify`
- **Status**: implemented (2026-10-02; corpus TEI only, ≤ 50 quotations per request) · **Scope** `read` · S · **no LLM**
- **Purpose**: does a quotation, or a number, appear in the cited source? Where exactly, and does the source itself cite someone else for it?

**Request**: `{"items": [QuoteItem | {"open_citations_item": {...}}] (1–50), "project_id": string?, "acquire_missing": false}`.
- `source` is a DOI, a `paper_id` (or any historical file stem), or a cite key of `project_id` (`biblio.cite_key`).
- A quotation may contain `…` (or `...`) for an omission. The fragments must occur in order in one passage.
- An `open_citations_item` (one item of an `open_citations.json` file) is expanded into one `QuoteItem` per entry of `citations[].quotations`. Its `key` becomes `"<key>#<citation>.<quotation>"`. Expanded quotations that are too short are not checked. They are listed in `not_checked` and do not fail the request.
- `project_id` at the request level is the default for every item.
- `acquire_missing = true` answers `501 NOT_IMPLEMENTED`: acquisition jobs are phase 2.

**Response 200**: `{"items": [QuoteResult], "not_checked": [{key, quote, reason}], "summary": {"FOUND_EXACT": n, …}, "provenance"}`.

**Statuses**:

| Status | Meaning |
|---|---|
| `FOUND_EXACT` | every fragment occurs verbatim, in order, in one passage |
| `FOUND_NORMALIZED` | equal after normalisation: NFKC, ligatures, quote marks, dashes and minus, soft hyphens, whitespace |
| `FOUND_FUZZY` | best window similarity ≥ 0.92, or only the longest fragment was found. **Quote `span.text`, not your wording.** |
| `NOT_FOUND` | the full text was searched (see `searched`) and nothing matched |
| `SOURCE_UNAVAILABLE` | not in the corpus, or no GROBID full text. Nothing was checked. |

**Results are not stored.** The endpoint is read-only. To record a verdict, import it with the project data (phase 2: `POST /evidence/quote-checks`, scope `write`).

```json
{"items": [
  {"source": "10.5194/nhess-19-2405-2019", "quote": "does not accurately capture inundated cells"},
  {"source": "10.1029/2024WR038314", "quote": "the initial volumetric flow rate is", "expected_numbers": ["5.7", "0.8", "-5.7"]},
  {"source": "AshikIqbal_EffectivenessofDEMsforFloodModeling_jfr3.12937",
   "quote": "A mean absolute vertical error of 1.12-1.61 m was found for the FABDEM in built-up areas"}]}
```
```json
{"items": [
  {"status": "FOUND_EXACT", "score": 1.0, "text_source": "corpus_tei",
   "span": {"passage_id": "abstract", "kind": "abstract", "section": "Abstract", "page": null,
            "text": "Overall, the NWM-HAND method does not accurately capture inundated cells but is quite capable of highlighting regions likely to be at risk in 4th-order streams and higher.", "…": "…"},
   "attribution": {"cites_other_sources": false, "in_text_refs": [], "resolved_dois": []}, "…": "…"},
  {"status": "FOUND_EXACT", "numbers": [
     {"value": "5.7", "found": true, "distance_chars": 2, "context": "…"},
     {"value": "0.8", "found": true, "distance_chars": 8, "context": "…"},
     {"value": "-5.7", "found": false, "elsewhere": []}], "…": "…"},
  {"status": "FOUND_EXACT", "span": {"section": "| DISCUSSION", "page": 15,
     "text": "A mean absolute vertical error of 1.12-1.61 m was found for the FABDEM in built-up areas (Hawker et al., 2022), which conforms to the findings for the Ranigram area.", "…": "…"},
   "attribution": {"cites_other_sources": true, "in_text_refs": ["(Hawker et al., 2022)"],
                   "resolved_dois": ["10.1088/1748-9326/ac4d4f"]}, "…": "…"}],
 "not_checked": [], "summary": {"FOUND_EXACT": 3}, "provenance": {"…": "…"}}
```

**Errors**:
- `422 VALIDATION_FAILED`: a direct quotation, or its longest `…` fragment, is shorter than 25 characters (`errors[].loc = ["body", "items", i, "quote"]`). Nothing is checked.
- `413 PAYLOAD_TOO_LARGE`: more than 50 quotations after expansion.
- `501 NOT_IMPLEMENTED`: `acquire_missing = true`.
- `503 STORE_UNAVAILABLE`: Postgres or the TEI store is unavailable.

**Agent notes**:
- `attribution.cites_other_sources = true` means the source's own sentence cites other work. You are holding a **secondary citation**: read the original. In the third item above, Iqbal reports Hawker's before/after pair, 1.61 → 1.12 m, as a "range".
- `span.text` is the source's own sentences around the match. Quote it, with `paper.doi` and `page`. `page` is `null` when GROBID gave no coordinates; abstracts usually have none.
- Numbers are matched with their sign: `-5.7` does not match `5.7`. A range dash (`1.12-1.61`) is not a minus sign. `numbers[].found` refers to the matched passage. `elsewhere` lists other passages that contain the number.
- `NOT_FOUND` with `text_source = corpus_tei` means the GROBID text was searched; GROBID can lose text in tables and formulas. It is evidence of absence only for that text.
- `SOURCE_UNAVAILABLE` says nothing about the paper. Report it as "not checked", never as "not supported" (R-SCI-3).
- `open_citations.json` attaches every quotation of a sentence to **each** work cited in that sentence. A `NOT_FOUND` for one of those works is expected when the words belong to a co-cited work. Send all the file's items in one request: `found_in` then names the item whose source does contain the quotation. On the floodstate-eo file of 2026-10-01:
  - `Zheng_2018#0.0` → `found_in: ["Johnson_2019#0.0"]`;
  - `Zheng_2018#2.0` stays `NOT_FOUND`: those words are Bates 2022's, which has no open item.

---

## `POST /claims/check`
- **Status**: planned (phase 4, WP 4.2) · **Scope** `llm` · S for 1 claim, **J** for batches
- **Purpose**: does the cited source support the manuscript sentence as written? It catches overstatement, contradiction and secondary citation.

**Request**:

| Field | Type | Notes |
|---|---|---|
| `claim` | string | the manuscript sentence (or `occurrence` from `/manuscripts/citations`) |
| `sources` | string[] | DOIs or paper ids cited for it (1–5) |
| `project_id` | string? | stores the result under this namespace |
| `quoted` | string[] | quoted words, checked first by `/quotes/verify` |
| `strictness` | `normal \| strict` | `strict` treats hedge removal and scope widening as `OVERSTATED` |

**Response 200**: `ClaimCheckResult` ([SCHEMAS](../SCHEMAS.md#claimcheckresult)).

```json
{"verdict": "CONTRADICTED",
 "evidence": [{"paper": {"doi": "10.1029/2025gl120832"}, "section": "Abstract",
   "text": "Modifying reservoir and channel bathymetry to reflect geomorphology produces better agreement with SWOT, … yet still fails to reproduce both flood stage and timing."}],
 "explanation": "The manuscript says the models reproduce stage and timing once bathymetry is corrected; the source (span 1) says that even with corrected bathymetry they do not.",
 "suggested_rewrite": "Such models improve with corrected reservoir and channel bathymetry but still do not reproduce the peak stage and its timing together (Lehnigk et al. 2026).",
 "secondary_citation": false,
 "labeler": {"labeler_kind": "model", "labeler": "gemini-3.1-flash-lite"}, "provenance": {"…": "…"}}
```

**Errors**: `424 SOURCE_UNAVAILABLE` (no full text: the verdict would be a guess); `429 QUOTA_EXHAUSTED`.

**Agent notes**:
- The verdict is a model judgement grounded in quoted spans. Show the spans to the human; do not paste only the verdict.
- `suggested_rewrite` stays within what the evidence supports. Do not strengthen it.

---

## `POST /theses/validate`
- **Status**: implemented (2026-10-02) · **Scope** `read` · S · **no LLM**
- **Purpose**: validate a `theses.json` or `atomic_claims.yaml` against contract v1 **before** any import or audit. Nothing is stored.
- **Request**: `{"kind": "theses" | "atomic_claims", "project_id": ProjectId, "document": object | list | string, "authored_by": Labeler?}`.
  - `document` is the parsed document, or its JSON/YAML text.
  - `authored_by` is used when the document does not say who wrote it; an `atomic_claims.yaml` usually does (`authored_by: "Claude (Fable 5.1) …"`).

**Accepted document shapes**:
- **theses**: a list of theses, or `{"theses": [...], "authored_by"?}`.
  - Fields: `id`, `thesis` (or `statement`, ≥ 10 characters), `kind?`, `section?`, `category?`, `priority?` (`high|medium|low`), `quantitative?`, `tables` (list), `needs?`, `refs` (list of `{key, relation, status}`), `search_queries` (list of strings), `extra` (object).
  - `status` accepts the bundle vocabulary `verified | VERIFY | to_verify | missing | missing (search) | in bib, no status note | unknown`; it is mapped to `RefStatus` and kept in `status_raw`.
  - `tables` given as a string `"T16; T21"` is accepted and reported in `warnings`; the contract form is a list.
- **atomic_claims**: `{"claims": [...], "authored_by"?, "queries_version"?, "novelty_questions"?}`.
  - Claims follow the `AtomicClaim` fields.
  - A claim with `not_needed: true` is skipped and counted.
  - `novelty_questions` are counted but not validated.
  - `thesis_id` values are checked against the project's theses in Postgres; when that is not possible, `warnings` says so.

**Response 200**: `{"valid": true, "counts": {"theses": 51, "refs": 175, "search_queries": 112, "tables_as_string": 51}, "warnings": [...], "provenance"}`. These are the actual counts of the floodstate-eo Paper 3 bundle.

**Response 422** `VALIDATION_FAILED`: `errors[]` locate each problem with the document's own field names, plus `counts` and `warnings`. Example: the Paper 2 CSV dump, with `refs` as a `"Key[REL:status]"` string, fails with `loc: ["theses", 0, "refs"], msg: "expected a list of objects {key, relation, status}"`. Nothing is coerced. Other failures:
- lists written as Python-literal strings (`"['A', 'B']"`);
- unknown fields (producer-specific data belongs under `extra`);
- duplicate ids;
- unknown relations or roles.

**Response 422** `UNKNOWN_PROJECT`: `project_id` has the right shape but is not in the registry (`project.project`). A paper is registered once, by the workbench `init` step; never invent a namespace.

---

## `POST /theses/extract`
- **Status**: planned (phase 4, WP 4.3) · **Scope** `llm` · **J**
- **Purpose**: decompose manuscript paragraphs (or a paper's abstract and conclusions) into atomic claims in the `AtomicClaim` contract.
- **Request**: `{"project_id": string, "text": string | null, "paper_id": string | null, "thesis_id": string?}`.
- **Result artifact**: `{"claims": [AtomicClaim], "dropped": [{"text", "reason"}]}`. Each claim carries `authored_by = {labeler_kind: model, labeler: <model>}` and quotes the sentence it came from.
- **Agent notes**:
  - The first `key_terms_primary` family must name the distinguishing concept, never a metric or a generic word.
  - Review the claims before you use them as search specifications.

---

## `POST /theses/evidence`
- **Status**: planned (phase 2) · **Scope** `read` · S
- **Purpose**: for one atomic claim, gather candidate evidence: semantic, lexical and citation routes, with key-term density ranking. No LLM.
- **Request**: `{"project_id": string, "atomic_id": string}`, or an inline `AtomicClaim`; plus `"k": 12`.
- **Response 200**: `{"candidates": [{"paper": PaperRef, "route": "semantic|exact|citation|graph", "density": float, "best_spans": [EvidenceSpan]}], "coverage", "retrieval_validity", "provenance"}`.

---

## `POST /theses/evidence-run`
- **Status**: planned (phase 4) · **Scope** `llm` · **J**
- **Purpose**: the complete literature audit of a thesis bundle: retrieve → select → screen (LLM roles with verified quotes) → derive statuses → export. It replaces `tools/paper3_literature_audit.py prepare…export`.
- **Request**: `{"project_id": string, "theses": [Thesis] | null, "atomic_claims": [AtomicClaim] | null, "lanes": ["gemini-3.1-flash-lite", "gemini-3.5-flash-lite"], "max_llm_calls": 400}`. When `theses` and `atomic_claims` are null, the stored ones are used.
- **Result artifacts**: `thesis_evidence` (rows like `02_thesis_evidence.csv`), `source_ledger` (03), `claim_citation_matrix` (05), `status_by_claim`, `unresolved`. All rows are stored in `evidence.*` with run provenance.

---

## `POST /theses/novelty`
- **Status**: planned (phase 4) · **Scope** `llm` · **J**
- **Purpose**: answer a novelty question ("has anyone done X for Y?"). Gate semantics are enforced.
- **Request**: `{"project_id": string, "question_id": string, "question": string, "slice": {"key_terms": [[…]], "years": […]}}`.
- **Result**: `{"verdict": "CANDIDATE_GAP" | "PRIOR_WORK_FOUND" | "RETRIEVAL_UNVALIDATED", "denominator": {"papers_in_slice": n}, "closest_papers": [PaperRef], "statement": string}`.
- **Rule**: `CANDIDATE_GAP` is returned **only** when the project's acceptance gate has passed. Otherwise the best possible verdict is `RETRIEVAL_UNVALIDATED`, and asking for a gap explicitly yields `409 GATE_NOT_PASSED`.

---

## `GET /theses/sets/{project_id}`
- **Status**: implemented (2026-10-03: theses, references, atomic claims and evidence rows from `project.*` / `evidence.claim_evidence`; the gate summary is planned) · **Scope** `read` · S
- **Purpose**: the stored theses of a project with the literature evidence of each.
- **Response 200**: `{"project_id", "theses": [{"thesis_id", "kind", "statement", "section", "category", "priority", "quantitative", "tables", "needs", "labeler_kind", "labeler", "refs": [{"key", "relation", "status"}], "atomic_claims": [{"atomic_id", "statement", "required_roles", "manuscript_relevance", "labeler_kind"}], "evidence": [{"evidence_id": "ce:<id>", "thesis_id", "atomic_id", "role", "status", "paper_id", "cite_key", "doi", "section", "page", "chunk_id", "quote_verified", "evidence_quote", "status_rule"}]}], "unattached_evidence": [...], "counts", "status_source", "provenance"}`.
- **Errors**: `422 UNKNOWN_PROJECT`; `503`.

**Agent notes**:
- Evidence `status` values (`VERIFIED_SUPPORTED`, `VERIFIED_PARTIAL`, …) were assigned by the literature run's models. They are model-assessed (R-SCI-6); a person's judgement of a row is in `GET /verifications?target_kind=claim_evidence&target_id=ce:<id>`.