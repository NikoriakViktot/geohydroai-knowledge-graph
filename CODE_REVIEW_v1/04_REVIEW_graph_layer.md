# Module Review 04 — `src/graph/` + `src/graphstore/` (Neo4j Layer)

**Version**: 1.0 | **Date**: 2026-06-11
**Scope**: `src/graph/` (11 files, 3,490 LOC: `neo4j_writer.py`, `build_graph.py`, `graph_loader.py`, `graph_constraints.py`, `graph_queries.py`, `graph_statistics.py`, `kg_synchronizer.py`, `table_kg_loader.py`, `region_kg_loader.py`), `src/graphstore/` (2 files, 540 LOC).
**Verdict**: **GOOD core, one rogue duplicate, credential hygiene problems** — Score 6/10

---

## 1. What is done well

- **MERGE-only discipline is real** in the primary writer: `src/graph/neo4j_writer.py` (`GraphWriter`) uses Cypher `MERGE` throughout; verified by grep — no un-gated `DELETE`/`CREATE` in the main write path.
- **Destructive operations are explicit and gated**: `graph_constraints.py:72-74` isolates `WIPE_STATEMENT = "MATCH (n) DETACH DELETE n"` behind the `--wipe` CLI flag (`build_graph.py:54, 94-97`). This is the correct pattern — the review confirms it does *not* violate the "no accidental DELETEs" invariant.
- Constraint creation is idempotent ("Run once on a fresh or wiped database. All statements are idempotent").
- Loaders are cleanly separated by concern: `table_kg_loader` (NumericFacts), `region_kg_loader` (Nougat regions), `kg_synchronizer`.

---

## 2. Findings

### F-GR-1 — `src/graphstore/neo4j_writer.py` is a duplicate writer that violates MERGE-only (HIGH)

**Evidence**:
- Two writers exist: `src/graph/neo4j_writer.py` (701 LOC, class `GraphWriter`, used by `build_graph`, `table_kg_loader`, `region_kg_loader`, `kg_synchronizer`, `graph_statistics`) and `src/graphstore/neo4j_writer.py` (540 LOC, class `Neo4jWriter`).
- The duplicate uses `CREATE` — `graphstore/neo4j_writer.py:431`:
  ```cypher
  CREATE (e:Evidence { ... })
  ```
  `CREATE` is not idempotent: re-running the RAG pipeline duplicates `Evidence` nodes.
- Sole consumer: `src/pipeline/rag_pipeline.py:310` (lazy import).

**Impact**: the architectural invariant "GraphWriter uses only Cypher MERGE" holds for `src/graph/` but is bypassed by a parallel package. Evidence nodes can multiply on reruns, corrupting evidence counts used for claim-confidence reporting — a direct scientific-integrity risk given the project's evidence-grounding policy.

**Recommendation**: merge the `Evidence`-writing capability into `src/graph/neo4j_writer.py` using `MERGE` keyed on a deterministic evidence ID (e.g., hash of `paper_id + chunk_id + claim_id`); delete `src/graphstore/`; update `rag_pipeline.py`.

### F-GR-2 — Hardcoded Neo4j password and personal identifiers as code defaults (HIGH — security)

Verified occurrences of `python2024` / personal credentials as in-code defaults:

| File:Line | Default |
|-----------|---------|
| `src/graph/neo4j_writer.py:40` | `os.getenv("NEO4J_PASSWORD", "python2024")` |
| `src/graph/graph_statistics.py:39` | same |
| `src/graph/build_graph.py:15, 53` | same (docstring + argparse default) |
| `src/graphstore/neo4j_writer.py:38` | same |
| `src/dashboard_dash/data_neo4j.py:27` | same |
| `src/config/settings.py:34` | `GEONAMES_USER` default `"viktornikoriak"` |
| `src/config/settings.py:53` | `OPEN_ALEX_EMAIL` default `"viktornikoriak@uhmi.org.ua"` |
| `src/ingestion/stages/geo_stage.py:329` | GeoNames username literal (bypasses settings) |
| `src/actors/geonames_actor.py:28` | `_USERNAME = "viktornikoriak"` |

**Impact**: for a local research stack this is a contained risk (the password also lives in `docker-compose.yml`), but as a codebase intended for publication/teaching it leaks personal identifiers and teaches a bad pattern. The DRY violation is also real: rotating the password requires touching 6 files.

**Recommendation**: single source `NEO4J_PASSWORD` in `src/config/settings.py` with **no default** (fail fast with a clear message); all 5 consumers import from settings. Replace personal GeoNames/OpenAlex identifiers with required env vars + `.env.example`. Since the literal lives in docs and git history anyway, also rotate the Neo4j password once centralization lands.

### F-GR-3 — Zero tests for the entire graph layer (HIGH)

`build_graph.py`, `neo4j_writer.py`, `graph_loader.py` have no tests — yet this layer materializes the final scientific product (57,603 nodes / 92,003 CITES edges). Node/edge cardinality regressions, constraint drift, or a MERGE-key typo would ship silently.

**Recommendation**: tests with a mocked driver session asserting (1) every write statement starts with `MERGE`, (2) MERGE keys for each node type, (3) wipe is never reachable without the flag. No live Neo4j needed — consistent with the existing conftest philosophy.

### F-GR-4 — `kg_synchronizer.py` swallows errors (4 × `except Exception`) (MEDIUM)

Sync discrepancies are logged-and-continued; there is no final divergence report (expected vs written counts). Add a summary delta so partial syncs are visible.

---

## 3. Recommendations summary

| # | Action | Effort |
|---|--------|--------|
| 1 | Fold `graphstore` Evidence writes into `GraphWriter` as MERGE; delete package | 1–2 days |
| 2 | Centralize credentials, remove defaults, `.env.example`, rotate password | 1 day |
| 3 | MERGE-only + cardinality test suite (mock driver) | 2 days |
| 4 | Synchronizer divergence report | 0.5 day |

**Module score: 6/10** — the primary writer is exemplary; the duplicate package and credential sprawl undermine it.
