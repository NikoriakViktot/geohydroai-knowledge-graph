# Module Review 03 — `src/orchestration/` + `src/actors/` + `src/registry/`

**Version**: 1.0 | **Date**: 2026-06-11
**Scope**: `src/orchestration/` (10 files, 2,451 LOC), `src/actors/` (7 files, 1,259 LOC), `src/registry/` (6 files, 2,348 LOC).
**Verdict**: **GOOD patterns, missing resilience** — Score 6.5/10

---

## 1. What is done well

- **Two-level idempotency** (`pipeline_runner.py` + `process_paper.py`): filesystem pre-filter before Ray task creation, plus an in-task guard before XML parsing. Re-running the pipeline is safe and cheap.
- **Sliding-window concurrency**: bounded in-flight tasks (`RAY_MAX_CONCURRENT=4` default, configurable via settings), one-completion-one-submission. Prevents queue flooding on a 3 GB WSL2 host — the design respects its deployment reality.
- **Explicit memory lifecycle**: `del` + `gc.collect()` after TEIDocument/NER/embedding stages — unusual care, appropriate for the memory-constrained target.
- **`normalization_runner` and `enrichment_runner`** are fully idempotent, Ray-free, with per-file error isolation and an SQLite DOI cache (no duplicate OpenAlex calls).
- **Registry** (`src/registry/`): single-writer DuckDB with RLock, PyArrow ParquetFile reads — correct concurrency model for DuckDB.

---

## 2. Findings

### F-ORCH-1 — No retry logic anywhere in the pipeline (MEDIUM-HIGH)

A transient failure (Ollama timeout, OpenAlex 429/503, actor OOM) permanently marks the paper failed for that run. There is no exponential backoff, no retry budget. For a 3,875-paper corpus on flaky infrastructure, this converts transient noise into permanent data gaps that require manual `reset-stale` + re-run.

**Recommendation**: a small `retry(fn, attempts=3, backoff=2.0, retryable=(TimeoutError, ConnectionError, ...))` helper in `src/orchestration/`, applied at the actor-call boundary. Keep per-paper failure non-fatal as today.

### F-ORCH-2 — No Ray actor health checks; actor crash ⇒ indefinite hang (MEDIUM-HIGH)

Actors (`SpacyActor`, `EmbeddingActor`, `OllamaActor`) are created once per run with no heartbeat. If an actor dies mid-batch, pending `ray.get()` calls hang; there is no task-level timeout.

**Recommendation**: (1) `ray.get(ref, timeout=300)` with a per-stage timeout constant in settings; (2) a `health()` ping every N tasks; (3) `max_restarts=1` on actor decorators so Ray restarts crashed actors automatically.

### F-ORCH-3 — Judge failures invisible in run-level reporting (MEDIUM)

`process_paper.py` has 9 `except Exception` blocks; Ollama judge errors are logged per-paper but the run summary does not report "N papers processed without judge". Combined with F-EXT-3 (silent verdict repair), judge health is unobservable. **Recommendation**: aggregate `judge_used`, `judge_failed`, `verdict_repaired` counters into the registry per run.

### F-ORCH-4 — Legacy/SDOM duplication and a stale orphan module (MEDIUM)

- `src/tasks/process_paper.py` is an older duplicate of `src/orchestration/process_paper.py` (see stub audit in [07](07_REVIEW_dashboard_misc.md)). Anyone studying the codebase will not know which is canonical.
- `process_paper.py` re-implements NER/embedding/judge dispatch that conceptually belongs to `src/ingestion/pipeline.py` stages; the two paths can drift.

**Recommendation**: delete `src/tasks/`; extract the shared stage-dispatch into one function used by both CLI and Ray paths.

### F-ORCH-5 — `registry_db.py` monolith (1,258 LOC) (LOW-MEDIUM)

SQL string building, schema migration, state machine, and reporting in one file. Works, has an RLock discipline, but is the second-largest file in the repo. Split: `schema.py`, `state_machine.py`, `queries.py`. Low urgency — it is internally coherent.

### F-ORCH-6 — No cleanup on abnormal termination (LOW)

Killing the pipeline can leave Ray actors alive holding GPU/RAM. **Recommendation**: `atexit`/signal handler calling `ray.shutdown()`, and document `ray stop` in CLAUDE.md troubleshooting.

---

## 3. Tests

**Zero tests** for `pipeline_runner.py`, `process_paper.py`, `normalization_runner.py`, `enrichment_runner.py`, `reindex_chromadb.py`. The strongest patterns in the repo (idempotency, sliding window) are unverified by CI — a regression in the idempotency guard would silently cause GPU re-inference over 3,875 papers. See [08_TESTING_AUDIT.md](08_TESTING_AUDIT.md).

---

## 4. Recommendations summary

| # | Action | Effort |
|---|--------|--------|
| 1 | Retry helper + `ray.get` timeouts + actor `max_restarts` | 2 days |
| 2 | Judge health counters in registry | 1 day |
| 3 | Delete `src/tasks/`; unify stage dispatch | 1–2 days |
| 4 | Idempotency + sliding-window unit tests (mock Ray as in conftest) | 2 days |
| 5 | Registry split (optional) | 2 days |

**Module score: 6.5/10** — well-designed happy path, fragile failure path.
