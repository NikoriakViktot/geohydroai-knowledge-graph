# Module Review 06 — `src/semantic_objects/` + `src/analytics/` + `src/evaluation/` + `src/processing/`

**Version**: 1.0 | **Date**: 2026-06-11
**Scope**: `semantic_objects/` (8 files, 1,948 LOC), `analytics/` (8 files, 2,544 LOC), `evaluation/` (3 files, 601 LOC), `processing/` (5 files, 934 LOC).
**Verdict**: **GOOD design, observability gaps in analytics** — Score 7/10

---

## 1. What is done well

- **`semantic_objects/`** follows the repo's best practices: frozen dataclasses, immutable design, 15 explicit IF-THEN validation rules in `semantic_validator.py`, typed semantic edges. It is exercised by the largest test group (84 tests, `test_14_stage25.py`).
- **`evaluation/`** (MetricFact evidence layer, NRT 3-state, goldset builder, field-level evaluator) is the right scaffolding for the scientific-validity work this review calls for — it exists, it just needs to be *fed* with labeled data.
- **`analytics/parquet_builder.py`** uses streaming I/O and canonical-ID validation; the 11-table analytics schema is a clean star-ish layout for scientometrics.

---

## 2. Findings

### F-SEM-1 — Semantic validators only integration-tested (MEDIUM)

`TableSemanticValidator`, `FigureSemanticValidator`, `EquationSemanticValidator` are exercised only through `test_14_stage25.py` end-to-end paths. Edge cases — empty tables, caption-less figures, malformed LaTeX — have no direct unit tests. A rule regression would surface as a diffuse drop in annotation counts, not a failing test.

**Recommendation**: one unit test per IF-THEN rule (15 rules → ~15 small tests), each with a minimal positive and negative fixture.

### F-ANA-1 — Analytics swallows corrupted-input errors without an audit trail (MEDIUM)

- `analytics/sodb_aggregator.py`: 4 × `except Exception: ... continue` — corrupted parquet directories are skipped silently.
- `analytics/parquet_validators.py`: 4 × `except Exception` — *the validators themselves* ignore validation errors.

For an analytics layer feeding a publication, "N inputs skipped" must be a first-class output. **Recommendation**: every skip appends `(paper_id, file, reason)` to a `skipped_inputs.parquet`; builders end with a summary line; validators never swallow.

### F-ANA-2 — `print`-based reporting in analytics (~15 call sites in `parquet_builder.py`) (LOW)

Mixed `print` + `logging` means batch runs under nohup/Ray lose half the narrative. Standardize on `logging`, keep `print` only in `__main__` blocks.

### F-PROC-1 — `processing/` overlaps `analytics/` and `ingestion/` (LOW-MEDIUM)

`processing/analytics_pipeline.py`, `chunking.py`, `metadata cleaning` duplicate concerns owned elsewhere (LayoutAwareChunker lives in `document/`; analytics in `analytics/`). Candidate for consolidation or explicit deprecation note — currently a reader cannot tell which chunker is canonical.

---

## 3. Recommendations summary

| # | Action | Effort |
|---|--------|--------|
| 1 | Per-rule unit tests for semantic validators | 1.5 days |
| 2 | `skipped_inputs` audit trail; validators never swallow | 1 day |
| 3 | Logging consistency in analytics | 0.5 day |
| 4 | Deprecate or merge `processing/` duplicates | 1 day |

**Module score: 7/10** — among the healthiest packages; main risk is silent data loss in aggregation.
