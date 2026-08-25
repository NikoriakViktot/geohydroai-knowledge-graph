---
name: Project: DuckDB Pipeline Registry Module
description: DuckDB-backed pipeline state registry implemented in src/registry/; single-writer pattern with threading.RLock; PyArrow ParquetFile API required for reading
type: project
---

New module `src/registry/` implements a production DuckDB pipeline state registry.

**Files:**
- `src/registry/__init__.py` — exports `PipelineRegistry`, `PaperStatus`
- `src/registry/constants.py` — `PaperStatus` enum, `SCHEMA_VERSION=1`, stage names, timeouts
- `src/registry/registry_db.py` — `PipelineRegistry` class (all core operations)
- `src/registry/analytics.py` — `RegistryAnalytics` class + 10 named SQL queries
- `src/registry/pipeline_integration.py` — `RegistryAwarePipelineRunner` bridge to Ray runner
- `src/registry/cli.py` — CLI: stats, pending, retries, failures, runtime, report, export, reset-stale, query, bootstrap

**Key design:**
- Single DuckDB connection + `threading.RLock` (driver-only writes; Ray workers return dicts)
- Optimistic locking via `version` INTEGER column (UPDATE WHERE version = ?)
- Three tables: `pipeline_registry` (main), `registry_events` (append-only audit), `worker_heartbeats` (high-write-rate, separate)
- Schema migrations tracked in `schema_version` table (SCHEMA_VERSION=1)
- Parquet export: `COPY TO` with explicit VARCHAR casts; status-partitioned Hive layout
- `REGISTRY_DIR` and `PIPELINE_VERSION` added to `src/config/settings.py`

**Critical PyArrow reading note:**
Use `pq.ParquetFile(path).read()` for single partition files, NOT `pq.read_table()`.
PyArrow 24.x routes `pq.read_table` through `ParquetDataset` which fails schema-merging.
For full dataset: `pyarrow.dataset.dataset(parquet_dir, format='parquet', partitioning='hive')`.

**Status values:** NEW → QUEUED → PROCESSING → SUCCESS/FAIL/INVALID_XML/SKIPPED/RETRY/EMPTY_OUTPUT/JSON_ERROR

**Why:** FAIL/EMPTY_OUTPUT/JSON_ERROR are retriable (→ RETRY with exponential backoff 60s/300s/900s). INVALID_XML is non-retriable. Stale PROCESSING papers reset to RETRY via `reset_stale_workers()` watchdog.
