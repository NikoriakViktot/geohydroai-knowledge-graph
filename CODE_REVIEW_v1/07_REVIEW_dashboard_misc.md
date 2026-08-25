# Module Review 07 — `src/dashboard_dash/` + `src/config/` + stubs & misc packages

**Version**: 1.0 | **Date**: 2026-06-11
**Scope**: `dashboard_dash/` (13 files, 4,934 LOC), `config/` (2 files, ~100 LOC), `retrieval/`, `pipeline/`, `discovery/`, and 5 stub packages.
**Verdict**: **POOR (dashboard) / FAIR (config) — main cleanup target** — Score 4/10

---

## 1. Findings — Dashboard

### F-DASH-1 — `callbacks.py`: 1,963 LOC, 55 `except Exception` returning silent defaults (HIGH)

Verified by grep: 55 occurrences of `except Exception` in one file (plus 16 more in `research_query_service.py`, 918 LOC). The dominant pattern:

```python
except Exception:
    return {}
```

**Impact**: any backend failure (Neo4j down, missing parquet, schema drift) renders as an *empty chart with no error*. For a research dashboard this is scientifically dangerous: an analyst cannot distinguish "no data exists" from "query failed" — the two have opposite interpretations.

**Recommendation**:
1. Split callbacks by page into `callbacks/` package (`overview.py`, `geo.py`, `metrics.py`, …) — Dash supports multi-module registration.
2. One `@safe_callback` decorator: logs the exception with callback name, returns a visible error figure (`fig.add_annotation("data unavailable: <reason>")`) instead of `{}`.
3. Add a global error-count badge so degraded mode is visible.

### F-DASH-2 — Dashboard reads Neo4j with its own connection code and hardcoded password (MEDIUM)

`data_neo4j.py:27` duplicates connection logic + the `python2024` default (see [04](04_REVIEW_graph_layer.md) F-GR-2). Route through `src/graph` query API.

---

## 2. Findings — Config

### F-CFG-1 — `settings.py` has no validation and inconsistent env names (MEDIUM)

Verified issues in `src/config/settings.py` (97 lines):
- `GEMINI_API_KEY = os.getenv("Gemini_API_Key", "")` — mixed-case env var name (line 97); every other var is UPPER_SNAKE. Anyone setting `GEMINI_API_KEY` gets a silent empty string.
- Personal defaults: `GEONAMES_USER` (line 34), `OPEN_ALEX_EMAIL` (lines 51-54) — see F-GR-2.
- No range validation on thresholds (`ENTITY_SCORE_THRESHOLD` etc., lines 38-41) — `=1.5` would be accepted silently.
- Two config surfaces exist ("default in both config files" per CLAUDE.md re: COLLECTION_NAME) — drift risk.

**Recommendation**: migrate to a single `pydantic-settings` `Settings` class: typed fields, `Field(ge=0, le=1)` for thresholds, correct env aliases, no personal defaults. ~1 day, large payoff for a teaching codebase.

---

## 3. Findings — Stub & orphan packages

Verified contents:

| Package | Contents | Status | Action |
|---------|----------|--------|--------|
| `src/embedding/` | `embedder.py` (69 LOC) | stub, unused | delete or implement |
| `src/analysis/` | `method_clustering.py` (113 LOC) | orphan single file | move into `analytics/` or delete |
| `src/assembly/` | `paper_assembler.py` (152 LOC) | stub | delete or absorb into stage2 |
| `src/tasks/` | `process_paper.py` (44 LOC) | **stale duplicate** of `orchestration/process_paper.py` | delete (confusion hazard) |
| `src/utils/` | `logging_config.py` (28 LOC) | underused — most modules configure logging ad hoc | make it the *only* logging setup, used everywhere |
| `src/pipeline/` | `rag_pipeline.py` (454 LOC) | sole consumer of rogue `graphstore` writer | re-point to `src/graph` (see F-GR-1) |

**Impact**: 34 top-level packages for what is conceptually ~12 responsibilities. For the stated goal — *a library clean enough to learn from / train a model on* — every orphan package doubles the apparent architecture and will contaminate any code-derived training corpus with dead patterns.

---

## 4. Recommendations summary

| # | Action | Effort |
|---|--------|--------|
| 1 | Split callbacks + `@safe_callback` with visible errors | 3 days |
| 2 | `pydantic-settings` migration, fix `Gemini_API_Key` | 1 day |
| 3 | Delete/absorb 5 stub packages; adopt `utils/logging_config.py` repo-wide | 1–2 days |
| 4 | Dashboard Neo4j access via `src/graph` API | 1 day |

**Module score: 4/10** — not because it doesn't work, but because it concentrates the repo's worst observability and dead-code debt.
