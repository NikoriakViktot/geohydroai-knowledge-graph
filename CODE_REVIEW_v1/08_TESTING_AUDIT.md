# Review 08 — Testing Audit

**Version**: 1.0 | **Date**: 2026-06-11
**Scope**: `tests/` (22 files, 606 collected tests) vs `src/` (34 packages, 226 files).
**Verdict**: **Excellent infrastructure, dangerously uneven coverage** — Score 3/10 (coverage), 9/10 (infrastructure quality)

---

## 1. What is done well

- **conftest.py mocking strategy is exemplary**: TEI XML builder mirroring GROBID output, mocked geocoding, 6 domain-realistic paper fixtures (SAR flood, HEC-RAS, SWAT, NDVI, review, DEM). The full suite runs without Ollama, Ray, GPU, or network — fast, deterministic CI.
- The SDOM pipeline stages are properly covered: Stage 0 (48), Stage 1 (51), Stage 2 (51), Stage 2.5 (84), parser routing, plus schema, idempotency, logging, and edge-case suites.
- `test_normalized_paper_schema.py` pins the Pydantic contract — exactly the kind of test that prevents silent schema drift.

## 2. Coverage map (verified against tests/ contents)

| src package | Tests | Risk if it regresses |
|-------------|-------|----------------------|
| `ingestion/stage0–2` | ✅ heavy (234 tests) | — |
| `document/` | ✅ good (routing, chunker, models) | — |
| `semantic_objects/` | ⚠️ integration-only via stage 2.5 | rule regressions diffuse |
| `schemas/` | ✅ | — |
| `normalization/` | ⚠️ partial | match-type drift |
| **`orchestration/`** | ❌ none | idempotency break ⇒ mass GPU re-inference |
| **`graph/`** (+ `graphstore/`) | ❌ none | corrupt final scientific product |
| **`extraction/`** (incl. `regex_extractor`, `table_extractor`) | ❌ none | wrong metrics enter NumericFacts — *and the NSE bug (F-EXT-1) proves this is not hypothetical* |
| **`enrichment/` + `actors/`** | ❌ none (only inline src test files) | silent API contract breaks |
| `dashboard_dash/`, `analytics/`, `evaluation/`, `retrieval/`, `validation/`, `ontology/` | ❌ none | silent data loss / wrong reports |

Roughly **10% of files** have direct coverage; the covered 10% is the new SDOM path, while the *operational* legacy path (which produced the 3,546-paper corpus) is nearly untested.

## 3. Key gaps and the order to close them

1. **`extraction/regex_extractor.py` + `table_extractor.py`** — pure functions, trivially testable, highest scientific stakes. The NSE/Kappa range bug would have been caught by a 5-line parametrized test ("NSE = -0.27" must extract as −0.27). **Do this first.**
2. **Architecture-invariant tests** — a tiny `test_import_invariants.py`: grep imports for lxml/fitz outside allowed files; assert all `src/graph` write statements start with MERGE. Cheap, permanent enforcement of CLAUDE.md's invariant table.
3. **Orchestration idempotency** — mock Ray (pattern already exists in conftest), assert a second run submits zero tasks.
4. **Graph writer** — mock driver session; assert MERGE keys per node type; assert wipe unreachable without `--wipe`.
5. **Judge normalizer + repair-rate** — feed malformed LLM outputs, assert repaired shape *and* that a repair counter increments.
6. **Malformed-input fixtures** — truncated JSON (the real 12-file Elsevier failure mode!), corrupt parquet, empty TEI. The corpus already exhibits these; tests should too.

## 4. Tooling recommendations

- Add `pytest-cov` with an initial gate of ~35% line coverage, ratchet upward per phase; per-package thresholds for `extraction/` (90%) and `graph/` (80%).
- Add `mypy` (strict on `src/document/`, `src/schemas/`, `src/extraction/`) to CI.
- Keep the no-GPU/no-network property as a hard rule for the default suite; mark anything else `@pytest.mark.integration`.

**Estimated effort for items 1–5: ~6–8 days**, parallelizable with remediation phases (see [09_REMEDIATION_PLAN.md](09_REMEDIATION_PLAN.md)).
