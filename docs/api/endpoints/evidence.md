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
| Iqbal 2023 → Hawker 2022 | secondary citation |
| Lehnigk 2026 (Discussion) | `CONTRADICTED` |
| Lefebvre 2019 "71 %" | `NOT_FOUND` |

---

## `POST /quotes/verify`
- **Status**: planned (phase 1, WP 1.9) · **Scope** `read` · S for ≤ 50 items, **J** above · **no LLM**
- **Purpose**: does a quotation, or a number, appear in the cited source? Where exactly?

**Request**: `{"items": [QuoteItem] (≤ 50 sync), "acquire_missing": false}`.
- Items may also be given as `open_citations.json` items: `{"open_citations_item": {...}}`; each quotation is expanded into one `QuoteItem`.
- `acquire_missing = true` starts an acquisition job for sources that are not in the corpus. Their results stay `SOURCE_UNAVAILABLE` until that job completes and you call again.

**Response 200**: `{"items": [QuoteResult], "summary": {"FOUND_EXACT": n, "FOUND_NORMALIZED": n, "FOUND_FUZZY": n, "NOT_FOUND": n, "SOURCE_UNAVAILABLE": n}, "provenance"}`. Every result is stored in `evidence.quote_check`.

```json
{"items": [
  {"source": "10.5194/nhess-19-2405-2019", "quote": "does not accurately capture inundated cells"},
  {"source": "10.1029/2024WR038314", "quote": "initial volumetric flow rate", "expected_numbers": ["5.7", "0.8"]},
  {"source": "Iqbal_2023", "quote": "mean absolute vertical error of 1.12-1.61 m"}]}
```
```json
{"items": [
  {"status": "FOUND_EXACT", "span": {"paper": {"doi": "10.5194/nhess-19-2405-2019"}, "section": "Abstract", "page": 1,
   "text": "Overall, the NWM-HAND method does not accurately capture inundated cells but is quite capable of highlighting regions likely to be at risk in 4th-order streams and higher."},
   "numbers": [], "attribution": {"cites_other_sources": false, "in_text_refs": [], "resolved_dois": []}, "text_source": "corpus_tei"},
  {"status": "FOUND_EXACT", "numbers": [{"value": "5.7", "found": true, "distance_chars": 12}, {"value": "0.8", "found": true, "distance_chars": 18}], "…": "…"},
  {"status": "FOUND_NORMALIZED", "attribution": {"cites_other_sources": true, "in_text_refs": ["Hawker et al., 2022"],
   "resolved_dois": ["10.1088/1748-9326/ac4d4f"]}, "…": "…"}],
 "summary": {"FOUND_EXACT": 2, "FOUND_NORMALIZED": 1}, "provenance": {"…": "…"}}
```

**Agent notes**:
- `FOUND_*` with `attribution.cites_other_sources = true` means the source is itself quoting someone else. Check the original (third item above: Iqbal reports Hawker's numbers, and reports them wrongly).
- `NOT_FOUND` lists what was searched (`searched.sections`, `searched.chars`). Do not upgrade it to "the paper does not say this" unless the full text was available (`text_source = corpus_tei`).
- Quotes shorter than 25 characters are rejected (`422`): they match too easily.

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
- **Status**: planned (phase 1) · **Scope** `read` · S
- **Purpose**: validate a `theses.json` or `atomic_claims.yaml` against the contracts **before** any import or audit.
- **Request**: `{"kind": "theses" | "atomic_claims", "project_id": string, "document": object | string (yaml)}`.
- **Response 200**: `{"valid": true, "counts": {"theses": 51, "refs": 175, "search_queries": 112}}`.
- **Response 422**: `VALIDATION_FAILED` with `errors[]`. Example: a CSV dump with `refs` as a `"Key[REL:status]"` string fails with `loc: ["theses", 0, "refs"], msg: "expected a list of objects"`. Nothing is coerced.

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
- **Status**: planned (phase 2) · **Scope** `read` · S
- **Purpose**: the stored theses of a project with their current statuses and verdicts.
- **Response 200**: `{"project_id", "theses": [Thesis + {"status": "VERIFIED_SUPPORTED|PARTIAL|CONTRADICTED|UNRESOLVED", "atomic_claims": [...]}], "gate": {"passed": bool, "recall": float?, "snapshot_id"}, "provenance"}`.
