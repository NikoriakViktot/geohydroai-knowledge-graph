# Module Review 05 — `src/normalization/` + `src/ontology/` + `src/enrichment/` + `src/schemas/` + `src/validation/`

**Version**: 1.0 | **Date**: 2026-06-11
**Scope**: `normalization/` (6 files, 1,450 LOC), `ontology/` (3 files, 1,482 LOC), `enrichment/` (6 files, 1,391 LOC), `schemas/` (2 files, 358 LOC), `validation/` (2 files, 418 LOC).
**Verdict**: **GOOD contracts, untested algorithms** — Score 6.5/10

---

## 1. What is done well

- **`src/schemas/normalized_paper.py`** is the strongest data contract in the repo: Pydantic v2, explicit `schema_version = "1.0"`, closed `MatchType` enum (`alias|exact|semantic|disambiguation|unknown|error`), and the invariant *`canonical_id` required when `match_type ∉ {unknown, error}`* — exactly how a normalization contract should look. It is covered by `tests/test_normalized_paper_schema.py`.
- **`validation/judge_normalizer.py`** is careful defensive code with typed recovery paths (good per-field handling of `ImportError`/`AttributeError` vs generic exceptions).
- The ontology (1,118 entities, 3,161 aliases) is data-driven and versioned (v2 migration completed across 8 files / 46 models).
- Enrichment uses a sliding window + SQLite cache for OpenAlex — no duplicate API calls, polite to the API.

---

## 2. Findings

### F-NORM-1 — `merge_ontology.py` (1,166 LOC) — complex algorithm, zero unit tests (HIGH)

Entity deduplication and graph-merge logic decide what counts as "the same method/sensor" across 3,680 papers. A merge bug here rewrites scientific identity silently (e.g., merging "HEC-RAS 1D" into "HEC-RAS 2D"). No test exercises it.

**Recommendation**: property-style unit tests on small synthetic ontologies: idempotency (merge(merge(x)) == merge(x)), alias-collision behavior, no-merge-across-type guarantee. 

### F-NORM-2 — Embedding fallback paths swallow errors (MEDIUM)

`normalization/embedding_matcher.py` carries 5 `except Exception` handlers; failures fall back to weaker matching (alias/exact) without recording that the semantic tier was unavailable. Result: a run with a broken embedding model produces systematically different `match_type` distributions with no alarm.

**Recommendation**: count tier-downgrade events per run and write them to the normalization report; fail the run if the semantic tier was unavailable for >X% of papers.

### F-NORM-3 — Enrichment has no tests and no retry (MEDIUM)

`enrichment/openalex_enrichment.py` + `actors/openalex_actor.py` (762 LOC): a 429/5xx marks the paper failed (97 papers failed in the last run). No mock-based test verifies cache hits, DOI normalization, or error classification. Combine with F-ORCH-1 retry helper.

### F-NORM-4 — `canonical_id` vs `ontology_id` naming drift — **RETRACTED 2026-06-11**

**Correction**: verification during Phase 1.5 remediation found **zero occurrences** of `ontology_id` (or `canon_id`/`kb_id` variants) in the codebase; `canonical_id` is used uniformly across 40 files. The original finding came from an unverified exploration report. `study_geo` vs `study_area` are distinct concepts (extracted geo-entity block vs TEI section/fact type), not drift. A glossary entry for `canonical_id` was still added to docs_v2/DATA_MODEL.md.

---

## 3. Scientific note

Normalization is the layer that turns noisy mentions into graph identities — its quality ceiling bounds the whole knowledge graph. The contract (schema) is excellent; what is missing is **measurement**: there is no precision/recall report of `match_type=semantic` decisions against a labeled sample. The judge gold set proposed in [02](02_REVIEW_ingestion_extraction.md) F-EXT-3 should include 200–300 mention→canonical_id pairs to cover this layer too (they double as training data, see [10_TRAINING_DATASET_PLAN.md](10_TRAINING_DATASET_PLAN.md)).

---

## 4. Recommendations summary

| # | Action | Effort |
|---|--------|--------|
| 1 | Unit tests for `merge_ontology.py` (idempotency, type safety) | 2 days |
| 2 | Tier-downgrade telemetry in embedding matcher | 0.5 day |
| 3 | Enrichment tests (mocked OpenAlex) + retry | 1.5 days |
| 4 | Naming unification `canonical_id` | 0.5 day |
| 5 | Labeled sample for semantic-match precision | folded into gold-set task |

**Module score: 6.5/10** — best contracts in the repo, weakest verification of the algorithms behind them.
