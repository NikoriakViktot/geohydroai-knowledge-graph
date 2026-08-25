"""
registry_db.py  —  PipelineRegistry: DuckDB-backed pipeline state store
========================================================================

Architecture
------------
Single-writer pattern:
    The orchestrator process (pipeline_runner.py driver) owns the DuckDB
    connection.  Ray workers return result dicts; the driver calls
    mark_success() / mark_failure() from the result-processing loop.
    This matches how DuckDB works: multiple readers, single writer.

Thread safety:
    A threading.RLock guards every write operation.  Multiple reader
    threads (analytics queries) run concurrently; writers serialize.

Optimistic locking:
    Every row has a `version` INTEGER column.  Update statements include
    `WHERE paper_id = ? AND version = ?` and increment version.  If the
    UPDATE affects 0 rows, a concurrent modification is detected and the
    caller can retry.

Heartbeats:
    Workers call heartbeat() every N seconds while processing.  The
    orchestrator calls detect_stale_workers() to find papers whose worker
    has gone silent.  Stale papers are reset to RETRY status.

Audit log:
    Every status transition is appended to registry_events (append-only).
    Never delete from this table — it is the lineage record.

Schema migrations:
    schema_version table tracks applied migrations.  init_registry()
    runs pending migrations in order before returning.

Parquet persistence:
    export_parquet() uses DuckDB COPY TO for zero-pandas bulk export.
    Output is Hive-partitioned by status for efficient downstream queries.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import platform
import socket
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import re

import duckdb

from src.registry.constants import (
    PaperStatus,
    SCHEMA_VERSION,
    HEARTBEAT_TIMEOUT_SEC,
    DEFAULT_MAX_RETRIES,
)

log = logging.getLogger(__name__)

# ─── DDL ─────────────────────────────────────────────────────────────────────

_DDL_SCHEMA_VERSION = """
CREATE TABLE IF NOT EXISTS schema_version (
    version     INTEGER  PRIMARY KEY,
    applied_at  TIMESTAMPTZ NOT NULL,
    description VARCHAR  NOT NULL
);
"""

_DDL_PIPELINE_REGISTRY = """
CREATE TABLE IF NOT EXISTS pipeline_registry (
    -- Identity
    paper_id          VARCHAR  NOT NULL PRIMARY KEY,
    source_pdf        VARCHAR,
    source_xml        VARCHAR,

    -- State machine
    status            VARCHAR  NOT NULL DEFAULT 'NEW',
    version           INTEGER  NOT NULL DEFAULT 0,       -- optimistic lock

    -- Timing
    created_at        TIMESTAMPTZ NOT NULL,
    updated_at        TIMESTAMPTZ NOT NULL,
    started_at        TIMESTAMPTZ,
    finished_at       TIMESTAMPTZ,
    runtime_sec       DOUBLE,
    heartbeat_at      TIMESTAMPTZ,                       -- last worker ping

    -- Current pipeline position
    stage             VARCHAR,                            -- current/last stage
    stage_timings     JSON,                              -- {stage: seconds}

    -- Retry control
    retry_count       INTEGER  NOT NULL DEFAULT 0,
    max_retries       INTEGER  NOT NULL DEFAULT 3,
    next_retry_at     TIMESTAMPTZ,

    -- Content hashes (idempotency + change detection)
    input_hash        VARCHAR(64),
    output_hash       VARCHAR(64),

    -- Output location
    output_json       VARCHAR,

    -- Error tracking
    error_type        VARCHAR,
    error_message     VARCHAR,
    error_traceback   VARCHAR,

    -- Worker / execution context
    worker_id         VARCHAR,
    worker_host       VARCHAR,
    pipeline_version  VARCHAR,

    -- Lineage & provenance
    lineage_meta      JSON,   -- {grobid_version, tei_schema, kb_version, ...}
    tags              VARCHAR[],

    CONSTRAINT status_check CHECK (
        status IN (
            'NEW','QUEUED','PROCESSING','SUCCESS','FAIL',
            'SKIPPED','RETRY','INVALID_XML','EMPTY_OUTPUT','JSON_ERROR'
        )
    )
);
"""

_DDL_REGISTRY_EVENTS = """
CREATE TABLE IF NOT EXISTS registry_events (
    -- Append-only audit / lineage log.  Never UPDATE or DELETE from here.
    event_seq     BIGINT,                               -- inserted by trigger equivalent
    paper_id      VARCHAR  NOT NULL,
    event_type    VARCHAR  NOT NULL,                   -- REGISTER / START / SUCCESS / FAIL / SKIP / HEARTBEAT / RETRY_RESET / STALE_RESET
    status_from   VARCHAR,
    status_to     VARCHAR,
    stage         VARCHAR,
    worker_id     VARCHAR,
    runtime_sec   DOUBLE,
    message       VARCHAR,
    metadata      JSON,
    created_at    TIMESTAMPTZ NOT NULL
);
"""

_DDL_HEARTBEATS = """
CREATE TABLE IF NOT EXISTS worker_heartbeats (
    -- High-write-rate table; one row per (paper_id, worker_id) pair,
    -- upserted on every heartbeat.  Kept separate from pipeline_registry
    -- so heartbeat writes never block analytics queries on the main table.
    paper_id       VARCHAR  NOT NULL,
    worker_id      VARCHAR  NOT NULL,
    stage          VARCHAR,
    last_seen_at   TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (paper_id, worker_id)
);
"""

_DDL_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_registry_status    ON pipeline_registry (status);
CREATE INDEX IF NOT EXISTS idx_registry_worker    ON pipeline_registry (worker_id);
CREATE INDEX IF NOT EXISTS idx_registry_created   ON pipeline_registry (created_at);
CREATE INDEX IF NOT EXISTS idx_events_paper       ON registry_events   (paper_id);
CREATE INDEX IF NOT EXISTS idx_events_type        ON registry_events   (event_type);
CREATE INDEX IF NOT EXISTS idx_events_created     ON registry_events   (created_at);
"""

# ─── Migrations ───────────────────────────────────────────────────────────────

_MIGRATIONS: dict[int, tuple[str, str]] = {
    # version: (description, sql)
    1: (
        "Initial schema",
        _DDL_SCHEMA_VERSION
        + _DDL_PIPELINE_REGISTRY
        + _DDL_REGISTRY_EVENTS
        + _DDL_HEARTBEATS
        + _DDL_INDEXES,
    ),
}


# ─────────────────────────────────────────────────────────────────────────────
# PipelineRegistry
# ─────────────────────────────────────────────────────────────────────────────

class PipelineRegistry:
    """
    DuckDB-backed pipeline state registry.

    Parameters
    ----------
    registry_dir:
        Directory for all registry artefacts.  Layout::

            registry_dir/
                pipeline_registry.duckdb
                parquet/
                    status=SUCCESS/...
                    status=FAIL/...
                audit/
                    events_YYYYMM.parquet
    pipeline_version:
        Semantic version of the pipeline code (e.g. "2.4.1").  Written
        into every row so you can filter by version in analytics.
    max_retries:
        Default maximum retry attempts per paper.
    """

    def __init__(
        self,
        registry_dir: Path,
        pipeline_version: str = "unknown",
        max_retries: int = DEFAULT_MAX_RETRIES,
    ) -> None:
        self._dir             = Path(registry_dir)
        self._db_path         = self._dir / "pipeline_registry.duckdb"
        self._parquet_dir     = self._dir / "parquet"
        self._audit_dir       = self._dir / "audit"
        self._pipeline_ver    = pipeline_version
        self._max_retries     = max_retries
        self._lock            = threading.RLock()
        self._conn: Optional[duckdb.DuckDBPyConnection] = None
        self._worker_id       = self._default_worker_id()

        self._dir.mkdir(parents=True, exist_ok=True)
        self._parquet_dir.mkdir(parents=True, exist_ok=True)
        self._audit_dir.mkdir(parents=True, exist_ok=True)

    # ── Connection management ─────────────────────────────────────────────────

    def _default_worker_id(self) -> str:
        return f"{socket.gethostname()}-{os.getpid()}"

    def _connect(self) -> duckdb.DuckDBPyConnection:
        if self._conn is None:
            self._conn = duckdb.connect(str(self._db_path))
            # DuckDB WAL is built-in; tune checkpoint size to reduce write latency
            self._conn.execute("SET checkpoint_threshold = '16MB'")
        return self._conn

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    @contextmanager
    def _transaction(self):
        """Context manager: BEGIN / COMMIT / ROLLBACK around a block."""
        conn = self._connect()
        conn.execute("BEGIN")
        try:
            yield conn
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

    # ── Schema init & migrations ──────────────────────────────────────────────

    def init_registry(self) -> None:
        """
        Initialise (or migrate) the DuckDB schema.

        Safe to call multiple times — migrations are idempotent.
        Creates schema_version, pipeline_registry, registry_events,
        worker_heartbeats, and all indexes.
        """
        with self._lock:
            conn = self._connect()
            # Create schema_version first so we can track applied migrations
            conn.execute(_DDL_SCHEMA_VERSION)
            applied = {
                r[0]
                for r in conn.execute("SELECT version FROM schema_version").fetchall()
            }
            for ver in sorted(_MIGRATIONS):
                if ver not in applied:
                    desc, sql = _MIGRATIONS[ver]
                    log.info("[registry] applying migration v%d: %s", ver, desc)
                    # DuckDB has no executescript — split on ';\n' to preserve
                    # semi-colons inside string literals (e.g. CHECK constraints)
                    for stmt in re.split(r";\s*\n", sql):
                        stmt = stmt.strip()
                        if stmt:
                            conn.execute(stmt)
                    conn.execute(
                        "INSERT INTO schema_version VALUES (?, ?, ?)",
                        [ver, _now(), desc],
                    )
            log.info("[registry] schema up-to-date (v%d)", SCHEMA_VERSION)

    # ── Core write operations ─────────────────────────────────────────────────

    def register_paper(
        self,
        paper_id:   str,
        source_xml: str  = "",
        source_pdf: str  = "",
        input_hash: Optional[str] = None,
        tags:       Optional[list[str]] = None,
        lineage_meta: Optional[dict]    = None,
    ) -> bool:
        """
        Register a new paper in the registry.

        Returns True if inserted, False if paper_id already exists
        (idempotent — safe to call on restart).
        """
        now = _now()
        tags_val    = tags or []
        lineage_val = json.dumps(lineage_meta or {})

        with self._lock:
            conn = self._connect()
            existing = conn.execute(
                "SELECT status FROM pipeline_registry WHERE paper_id = ?",
                [paper_id],
            ).fetchone()

            if existing is not None:
                log.debug("[registry] register_paper: %s already exists (%s)", paper_id, existing[0])
                return False

            conn.execute(
                """
                INSERT INTO pipeline_registry
                    (paper_id, source_xml, source_pdf, status, version,
                     created_at, updated_at, input_hash, max_retries,
                     pipeline_version, tags, lineage_meta)
                VALUES (?,?,?,?,?, ?,?,?,?, ?,?,?)
                """,
                [
                    paper_id, source_xml, source_pdf, PaperStatus.NEW, 0,
                    now, now, input_hash, self._max_retries,
                    self._pipeline_ver, tags_val, lineage_val,
                ],
            )
            self._append_event(
                conn, paper_id, "REGISTER",
                status_from=None, status_to=PaperStatus.NEW,
                message=f"xml={source_xml}",
            )
            log.debug("[registry] registered %s", paper_id)
            return True

    def start_processing(
        self,
        paper_id:  str,
        worker_id: Optional[str] = None,
        stage:     str = "xml_parse",
    ) -> bool:
        """
        Transition paper from NEW/QUEUED/RETRY → PROCESSING.

        Optimistic lock: if another worker has already claimed this paper,
        returns False.  The caller should skip the paper.
        """
        wid = worker_id or self._worker_id
        now = _now()

        with self._lock:
            conn = self._connect()
            row = conn.execute(
                "SELECT status, version FROM pipeline_registry WHERE paper_id = ?",
                [paper_id],
            ).fetchone()

            if row is None:
                log.warning("[registry] start_processing: unknown paper %s", paper_id)
                return False

            current_status, current_version = row

            # Only claim if in a startable state
            if current_status not in {
                PaperStatus.NEW, PaperStatus.QUEUED, PaperStatus.RETRY
            }:
                log.debug(
                    "[registry] start_processing: %s is %s — skipping",
                    paper_id, current_status,
                )
                return False

            # Optimistic lock: UPDATE only if version hasn't changed
            updated = conn.execute(
                """
                UPDATE pipeline_registry SET
                    status      = 'PROCESSING',
                    version     = version + 1,
                    started_at  = ?,
                    updated_at  = ?,
                    worker_id   = ?,
                    worker_host = ?,
                    stage       = ?,
                    error_type  = NULL,
                    error_message = NULL
                WHERE paper_id = ? AND version = ?
                """,
                [now, now, wid, socket.gethostname(), stage, paper_id, current_version],
            ).rowcount

            if updated == 0:
                log.warning("[registry] start_processing: optimistic lock conflict on %s", paper_id)
                return False

            # Upsert heartbeat
            conn.execute(
                """
                INSERT INTO worker_heartbeats (paper_id, worker_id, stage, last_seen_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT (paper_id, worker_id) DO UPDATE SET
                    stage        = excluded.stage,
                    last_seen_at = excluded.last_seen_at
                """,
                [paper_id, wid, stage, now],
            )
            self._append_event(
                conn, paper_id, "START",
                status_from=current_status, status_to=PaperStatus.PROCESSING,
                worker_id=wid, stage=stage,
            )
            return True

    def mark_success(
        self,
        paper_id:    str,
        output_json: str  = "",
        output_hash: Optional[str] = None,
        runtime_sec: Optional[float] = None,
        stage_timings: Optional[dict[str, float]] = None,
        worker_id:   Optional[str] = None,
    ) -> None:
        """Record successful completion."""
        wid = worker_id or self._worker_id
        now = _now()

        with self._lock:
            conn = self._connect()
            row = conn.execute(
                "SELECT started_at, version FROM pipeline_registry WHERE paper_id = ?",
                [paper_id],
            ).fetchone()
            if row is None:
                log.warning("[registry] mark_success: unknown paper %s", paper_id)
                return

            started_at, ver = row
            rt = runtime_sec
            if rt is None and started_at is not None:
                rt = (now - _parse_ts(started_at)).total_seconds()

            conn.execute(
                """
                UPDATE pipeline_registry SET
                    status        = 'SUCCESS',
                    version       = version + 1,
                    finished_at   = ?,
                    updated_at    = ?,
                    runtime_sec   = ?,
                    output_json   = ?,
                    output_hash   = ?,
                    stage         = 'done',
                    stage_timings = ?,
                    worker_id     = ?,
                    error_type    = NULL,
                    error_message = NULL
                WHERE paper_id = ?
                """,
                [
                    now, now, rt, output_json, output_hash,
                    json.dumps(stage_timings or {}), wid, paper_id,
                ],
            )
            self._append_event(
                conn, paper_id, "SUCCESS",
                status_from=PaperStatus.PROCESSING, status_to=PaperStatus.SUCCESS,
                worker_id=wid, runtime_sec=rt,
            )
            self._cleanup_heartbeat(conn, paper_id, wid)

    def mark_failure(
        self,
        paper_id:       str,
        error_type:     str  = "UNKNOWN_ERROR",
        error_message:  str  = "",
        error_traceback: str = "",
        runtime_sec:    Optional[float] = None,
        worker_id:      Optional[str] = None,
        status:         str = PaperStatus.FAIL,
    ) -> None:
        """
        Record failure and schedule retry if retry_count < max_retries.

        For invalid XML (non-retriable), pass status=PaperStatus.INVALID_XML.
        """
        wid = worker_id or self._worker_id
        now = _now()

        with self._lock:
            conn = self._connect()
            row = conn.execute(
                """
                SELECT started_at, retry_count, max_retries, version
                FROM pipeline_registry WHERE paper_id = ?
                """,
                [paper_id],
            ).fetchone()
            if row is None:
                log.warning("[registry] mark_failure: unknown paper %s", paper_id)
                return

            started_at, retry_count, max_retries, ver = row
            rt = runtime_sec
            if rt is None and started_at is not None:
                rt = (now - _parse_ts(started_at)).total_seconds()

            # Decide next status
            is_retriable = PaperStatus(status).is_retriable() if status in PaperStatus._value2member_map_ else False
            final_status = status
            next_retry_at = None
            new_retry_count = retry_count

            if is_retriable and retry_count < max_retries:
                final_status   = PaperStatus.RETRY
                new_retry_count = retry_count + 1
                # Exponential back-off: 60s, 300s, 900s
                backoff_sec    = 60 * (5 ** retry_count)
                next_retry_at  = _now_plus(backoff_sec)
                log.info(
                    "[registry] %s → RETRY %d/%d (backoff %ds)",
                    paper_id, new_retry_count, max_retries, backoff_sec,
                )
            else:
                log.info("[registry] %s → %s (retries exhausted or non-retriable)", paper_id, final_status)

            conn.execute(
                """
                UPDATE pipeline_registry SET
                    status          = ?,
                    version         = version + 1,
                    finished_at     = ?,
                    updated_at      = ?,
                    runtime_sec     = ?,
                    error_type      = ?,
                    error_message   = ?,
                    error_traceback = ?,
                    retry_count     = ?,
                    next_retry_at   = ?,
                    worker_id       = ?
                WHERE paper_id = ?
                """,
                [
                    final_status, now, now, rt,
                    error_type, error_message[:2000], error_traceback[:4000],
                    new_retry_count, next_retry_at, wid, paper_id,
                ],
            )
            self._append_event(
                conn, paper_id, "FAIL",
                status_from=PaperStatus.PROCESSING, status_to=final_status,
                worker_id=wid, runtime_sec=rt,
                message=f"{error_type}: {error_message[:200]}",
            )
            self._cleanup_heartbeat(conn, paper_id, wid)

    def mark_skipped(
        self,
        paper_id:  str,
        reason:    str = "already_processed",
        worker_id: Optional[str] = None,
    ) -> None:
        """Record that a paper was skipped (output already exists)."""
        wid = worker_id or self._worker_id
        now = _now()

        with self._lock:
            conn = self._connect()
            conn.execute(
                """
                UPDATE pipeline_registry SET
                    status      = 'SKIPPED',
                    version     = version + 1,
                    finished_at = ?,
                    updated_at  = ?,
                    worker_id   = ?,
                    error_message = ?
                WHERE paper_id = ? AND status NOT IN ('SUCCESS', 'SKIPPED')
                """,
                [now, now, wid, reason, paper_id],
            )
            self._append_event(
                conn, paper_id, "SKIP",
                status_from=None, status_to=PaperStatus.SKIPPED,
                worker_id=wid, message=reason,
            )

    def heartbeat(
        self,
        paper_id:  str,
        worker_id: Optional[str] = None,
        stage:     Optional[str] = None,
    ) -> None:
        """
        Update worker heartbeat.  Call every ~60s from within a processing task
        so the orchestrator can detect stale workers.
        """
        wid = worker_id or self._worker_id
        now = _now()

        with self._lock:
            conn = self._connect()
            conn.execute(
                """
                INSERT INTO worker_heartbeats (paper_id, worker_id, stage, last_seen_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT (paper_id, worker_id) DO UPDATE SET
                    stage        = excluded.stage,
                    last_seen_at = excluded.last_seen_at
                """,
                [paper_id, wid, stage, now],
            )
            # Also update heartbeat_at on the main row for quick dashboard queries
            conn.execute(
                "UPDATE pipeline_registry SET heartbeat_at = ?, stage = COALESCE(?, stage) WHERE paper_id = ?",
                [now, stage, paper_id],
            )

    # ── Query: resume / retry ─────────────────────────────────────────────────

    def get_unprocessed_papers(
        self,
        limit: Optional[int] = None,
    ) -> list[dict]:
        """
        Return papers that have not yet been successfully processed.
        Includes NEW, QUEUED, and papers in RETRY state whose next_retry_at
        has passed.

        Use this to populate the processing queue on (re)start.
        """
        limit_clause = f"LIMIT {limit}" if limit else ""
        sql = f"""
        SELECT
            paper_id, source_xml, source_pdf, status,
            retry_count, input_hash, pipeline_version, created_at
        FROM pipeline_registry
        WHERE status IN ('NEW', 'QUEUED')
           OR (status = 'RETRY' AND (next_retry_at IS NULL OR next_retry_at <= now()))
        ORDER BY retry_count ASC, created_at ASC
        {limit_clause}
        """
        with self._lock:
            rows = self._connect().execute(sql).fetchall()
        cols = ["paper_id", "source_xml", "source_pdf", "status",
                "retry_count", "input_hash", "pipeline_version", "created_at"]
        return [dict(zip(cols, r)) for r in rows]

    def get_retry_candidates(
        self,
        max_retries: Optional[int] = None,
    ) -> list[dict]:
        """
        Papers in RETRY state ready for resubmission.
        """
        max_r = max_retries or self._max_retries
        sql = """
        SELECT paper_id, source_xml, retry_count, next_retry_at, error_type
        FROM pipeline_registry
        WHERE status = 'RETRY'
          AND retry_count < ?
          AND (next_retry_at IS NULL OR next_retry_at <= now())
        ORDER BY retry_count ASC, next_retry_at ASC
        """
        with self._lock:
            rows = self._connect().execute(sql, [max_r]).fetchall()
        cols = ["paper_id", "source_xml", "retry_count", "next_retry_at", "error_type"]
        return [dict(zip(cols, r)) for r in rows]

    def get_processing_papers(self) -> list[dict]:
        """Papers currently marked PROCESSING (may include stale workers)."""
        sql = """
        SELECT
            paper_id, worker_id, started_at, heartbeat_at, stage,
            (epoch_ms(now()) - epoch_ms(started_at)) / 1000.0 AS elapsed_sec
        FROM pipeline_registry
        WHERE status = 'PROCESSING'
        ORDER BY started_at ASC
        """
        with self._lock:
            rows = self._connect().execute(sql).fetchall()
        cols = ["paper_id", "worker_id", "started_at", "heartbeat_at", "stage", "elapsed_sec"]
        return [dict(zip(cols, r)) for r in rows]

    # ── Stale worker detection ────────────────────────────────────────────────

    def detect_stale_workers(
        self,
        timeout_sec: int = HEARTBEAT_TIMEOUT_SEC,
    ) -> list[dict]:
        """
        Find papers in PROCESSING state whose last heartbeat is older than
        timeout_sec.  Returns list of stale paper dicts.

        Call this from the orchestrator's watchdog thread.
        """
        sql = """
        SELECT
            r.paper_id, r.worker_id, r.started_at,
            r.heartbeat_at,
            (epoch_ms(now()) - epoch_ms(COALESCE(r.heartbeat_at, r.started_at))) / 1000.0
                AS silence_sec
        FROM pipeline_registry r
        WHERE r.status = 'PROCESSING'
          AND (
              r.heartbeat_at IS NULL
              OR epoch_ms(now()) - epoch_ms(r.heartbeat_at) > ? * 1000
          )
        ORDER BY silence_sec DESC
        """
        with self._lock:
            rows = self._connect().execute(sql, [timeout_sec]).fetchall()
        cols = ["paper_id", "worker_id", "started_at", "heartbeat_at", "silence_sec"]
        return [dict(zip(cols, r)) for r in rows]

    def reset_stale_workers(
        self,
        timeout_sec: int = HEARTBEAT_TIMEOUT_SEC,
    ) -> int:
        """
        Reset stale PROCESSING papers back to RETRY.
        Returns count of reset papers.
        """
        stale = self.detect_stale_workers(timeout_sec)
        if not stale:
            return 0

        now = _now()
        reset_count = 0
        for row in stale:
            pid = row["paper_id"]
            wid = row["worker_id"]
            log.warning(
                "[registry] resetting stale worker: paper=%s worker=%s silence=%.0fs",
                pid, wid, row["silence_sec"],
            )
            with self._lock:
                conn = self._connect()
                conn.execute(
                    """
                    UPDATE pipeline_registry SET
                        status      = 'RETRY',
                        version     = version + 1,
                        updated_at  = ?,
                        retry_count = retry_count + 1,
                        error_type  = 'STALE_WORKER',
                        error_message = ?
                    WHERE paper_id = ? AND status = 'PROCESSING'
                    """,
                    [
                        now,
                        f"Worker {wid} silent for {row['silence_sec']:.0f}s",
                        pid,
                    ],
                )
                self._append_event(
                    conn, pid, "STALE_RESET",
                    status_from=PaperStatus.PROCESSING, status_to=PaperStatus.RETRY,
                    worker_id=wid,
                    message=f"silence={row['silence_sec']:.0f}s",
                )
                reset_count += 1

        return reset_count

    # ── Analytics queries ─────────────────────────────────────────────────────

    def build_runtime_report(self) -> dict:
        """
        Aggregate runtime statistics across all terminal papers.

        Returns dict with:
            total_papers, success, fail, skipped, success_rate,
            avg_runtime_sec, median_runtime_sec, p95_runtime_sec,
            slowest_papers (top 10)
        """
        with self._lock:
            conn = self._connect()

            # Top-level counts
            counts = conn.execute("""
                SELECT
                    COUNT(*)                                       AS total,
                    COUNT(*) FILTER (WHERE status = 'SUCCESS')    AS success,
                    COUNT(*) FILTER (WHERE status IN ('FAIL','INVALID_XML','EMPTY_OUTPUT','JSON_ERROR'))
                                                                  AS fail,
                    COUNT(*) FILTER (WHERE status = 'SKIPPED')    AS skipped,
                    COUNT(*) FILTER (WHERE status = 'PROCESSING') AS processing,
                    COUNT(*) FILTER (WHERE status IN ('NEW','QUEUED','RETRY'))
                                                                  AS pending
                FROM pipeline_registry
            """).fetchone()

            total, success, fail, skipped, processing, pending = counts
            success_rate = (success / total * 100) if total > 0 else 0.0

            # Runtime stats (only successful papers with runtime recorded)
            rt = conn.execute("""
                SELECT
                    AVG(runtime_sec)                        AS avg,
                    MEDIAN(runtime_sec)                     AS median,
                    PERCENTILE_CONT(0.95) WITHIN GROUP
                        (ORDER BY runtime_sec)              AS p95,
                    MIN(runtime_sec)                        AS min,
                    MAX(runtime_sec)                        AS max
                FROM pipeline_registry
                WHERE status = 'SUCCESS' AND runtime_sec IS NOT NULL
            """).fetchone()

            # Slowest 10
            slowest = conn.execute("""
                SELECT paper_id, runtime_sec, worker_id, finished_at
                FROM pipeline_registry
                WHERE status = 'SUCCESS' AND runtime_sec IS NOT NULL
                ORDER BY runtime_sec DESC
                LIMIT 10
            """).fetchall()

        return {
            "total_papers":    total,
            "success":         success,
            "fail":            fail,
            "skipped":         skipped,
            "processing":      processing,
            "pending":         pending,
            "success_rate_pct": round(success_rate, 2),
            "runtime": {
                "avg_sec":    round(rt[0] or 0, 3),
                "median_sec": round(rt[1] or 0, 3),
                "p95_sec":    round(rt[2] or 0, 3),
                "min_sec":    round(rt[3] or 0, 3),
                "max_sec":    round(rt[4] or 0, 3),
            },
            "slowest_papers": [
                {"paper_id": r[0], "runtime_sec": r[1], "worker_id": r[2], "finished_at": str(r[3])}
                for r in slowest
            ],
        }

    def build_failure_report(self) -> dict:
        """
        Aggregate failure statistics.

        Returns dict with:
            total_failures, by_status, by_error_type,
            most_common_errors, retry_statistics, permanently_failed
        """
        with self._lock:
            conn = self._connect()

            # Failures by status
            by_status = conn.execute("""
                SELECT status, COUNT(*) AS n
                FROM pipeline_registry
                WHERE status IN ('FAIL','INVALID_XML','EMPTY_OUTPUT','JSON_ERROR')
                GROUP BY status
                ORDER BY n DESC
            """).fetchall()

            # Failures by error type
            by_error = conn.execute("""
                SELECT error_type, COUNT(*) AS n,
                       AVG(retry_count) AS avg_retries
                FROM pipeline_registry
                WHERE status IN ('FAIL','INVALID_XML','EMPTY_OUTPUT','JSON_ERROR','RETRY')
                  AND error_type IS NOT NULL
                GROUP BY error_type
                ORDER BY n DESC
                LIMIT 20
            """).fetchall()

            # Retry statistics
            retry_stats = conn.execute("""
                SELECT
                    AVG(retry_count)        AS avg_retries,
                    MAX(retry_count)        AS max_retries,
                    COUNT(*) FILTER (WHERE retry_count > 0) AS papers_retried,
                    COUNT(*) FILTER (WHERE retry_count >= max_retries
                                      AND status NOT IN ('SUCCESS','SKIPPED'))
                                          AS permanently_failed
                FROM pipeline_registry
            """).fetchone()

            # Papers that failed all retries
            perm_failed = conn.execute("""
                SELECT paper_id, error_type, error_message, retry_count, updated_at
                FROM pipeline_registry
                WHERE status IN ('FAIL','INVALID_XML','EMPTY_OUTPUT','JSON_ERROR')
                  AND retry_count >= max_retries
                ORDER BY updated_at DESC
                LIMIT 50
            """).fetchall()

        total_fail = sum(r[1] for r in by_status)
        return {
            "total_failures": total_fail,
            "by_status": {r[0]: r[1] for r in by_status},
            "by_error_type": [
                {"error_type": r[0], "count": r[1], "avg_retries": round(r[2] or 0, 1)}
                for r in by_error
            ],
            "retry_statistics": {
                "avg_retries_per_paper":  round(retry_stats[0] or 0, 2),
                "max_retries_seen":       retry_stats[1] or 0,
                "papers_retried":         retry_stats[2] or 0,
                "permanently_failed":     retry_stats[3] or 0,
            },
            "permanently_failed_papers": [
                {
                    "paper_id":    r[0],
                    "error_type":  r[1],
                    "error_msg":   (r[2] or "")[:120],
                    "retry_count": r[3],
                    "updated_at":  str(r[4]),
                }
                for r in perm_failed
            ],
        }

    def build_throughput_report(self) -> dict:
        """Daily ingestion volume and worker utilisation."""
        with self._lock:
            conn = self._connect()

            daily = conn.execute("""
                SELECT
                    DATE_TRUNC('day', finished_at)::DATE  AS day,
                    COUNT(*)                              AS total,
                    COUNT(*) FILTER (WHERE status = 'SUCCESS') AS success,
                    AVG(runtime_sec)                      AS avg_rt_sec,
                    COUNT(DISTINCT worker_id)             AS workers
                FROM pipeline_registry
                WHERE finished_at IS NOT NULL
                GROUP BY 1
                ORDER BY 1 DESC
                LIMIT 30
            """).fetchall()

            worker_util = conn.execute("""
                SELECT
                    worker_id,
                    COUNT(*)                                    AS papers_handled,
                    COUNT(*) FILTER (WHERE status='SUCCESS')    AS success,
                    AVG(runtime_sec)                            AS avg_rt_sec,
                    MIN(started_at)                             AS first_task,
                    MAX(finished_at)                            AS last_task
                FROM pipeline_registry
                WHERE worker_id IS NOT NULL
                GROUP BY worker_id
                ORDER BY papers_handled DESC
            """).fetchall()

        return {
            "daily_volume": [
                {
                    "day": str(r[0]), "total": r[1], "success": r[2],
                    "avg_rt_sec": round(r[3] or 0, 2), "workers": r[4],
                }
                for r in daily
            ],
            "worker_utilisation": [
                {
                    "worker_id": r[0], "papers_handled": r[1], "success": r[2],
                    "avg_rt_sec": round(r[3] or 0, 2),
                    "first_task": str(r[4]), "last_task": str(r[5]),
                }
                for r in worker_util
            ],
        }

    # ── Parquet export ────────────────────────────────────────────────────────

    def export_parquet(
        self,
        statuses:  Optional[list[str]] = None,
        overwrite: bool = True,
    ) -> dict[str, Path]:
        """
        Export registry data to Hive-partitioned Parquet files.

        Structure::

            parquet/
                status=SUCCESS/data.parquet
                status=FAIL/data.parquet
                status=SKIPPED/data.parquet
                ...

        Uses DuckDB's native COPY TO — no pandas overhead.

        Returns mapping of status → output path.

        Reading the output
        ------------------
        Use ``pq.ParquetFile(path).read()`` for individual partition files
        or ``pyarrow.dataset.dataset(parquet_dir, partitioning='hive')``
        for the full multi-partition dataset.  Avoid ``pq.read_table()``
        on single files — a PyArrow 24.x regression routes it through
        ``ParquetDataset`` which may fail schema-merging across partitions.
        """
        export_statuses = statuses or [s.value for s in PaperStatus]
        outputs: dict[str, Path] = {}

        with self._lock:
            conn = self._connect()
            for status in export_statuses:
                out_dir = self._parquet_dir / f"status={status}"
                out_dir.mkdir(parents=True, exist_ok=True)
                out_path = out_dir / "data.parquet"

                if out_path.exists() and not overwrite:
                    outputs[status] = out_path
                    continue

                # Explicit VARCHAR casts prevent DuckDB from writing
                # dictionary-encoded columns that PyArrow can't merge
                # across partition files.
                conn.execute(f"""
                    COPY (
                        SELECT
                            paper_id::VARCHAR          AS paper_id,
                            source_pdf::VARCHAR        AS source_pdf,
                            source_xml::VARCHAR        AS source_xml,
                            status::VARCHAR            AS status,
                            started_at, finished_at,
                            CAST(runtime_sec AS DOUBLE) AS runtime_sec,
                            input_hash::VARCHAR        AS input_hash,
                            output_hash::VARCHAR       AS output_hash,
                            output_json::VARCHAR       AS output_json,
                            CAST(retry_count AS INTEGER) AS retry_count,
                            error_type::VARCHAR        AS error_type,
                            error_message::VARCHAR     AS error_message,
                            worker_id::VARCHAR         AS worker_id,
                            pipeline_version::VARCHAR  AS pipeline_version,
                            created_at, updated_at,
                            stage::VARCHAR             AS stage,
                            stage_timings::VARCHAR     AS stage_timings,
                            lineage_meta::VARCHAR      AS lineage_meta
                        FROM pipeline_registry
                        WHERE status = '{status}'
                    ) TO '{out_path}' (FORMAT PARQUET, COMPRESSION ZSTD)
                """)
                outputs[status] = out_path
                log.info("[registry] exported %s → %s", status, out_path)

        return outputs

    def export_audit_log_parquet(self) -> Path:
        """
        Export the registry_events audit log to a monthly-partitioned Parquet
        file for long-term archival.

        Returns path to the written file.
        """
        month_str = datetime.now(timezone.utc).strftime("%Y%m")
        out_path  = self._audit_dir / f"events_{month_str}.parquet"

        with self._lock:
            conn = self._connect()
            conn.execute(f"""
                COPY (
                    SELECT * FROM registry_events
                    WHERE STRFTIME(created_at, '%Y%m') = '{month_str}'
                    ORDER BY created_at
                ) TO '{out_path}' (FORMAT PARQUET, COMPRESSION ZSTD)
            """)
        log.info("[registry] audit log → %s", out_path)
        return out_path

    # ── Bulk registration (for large corpus bootstrapping) ───────────────────

    def bulk_register(
        self,
        papers: list[dict],
        skip_existing: bool = True,
    ) -> tuple[int, int]:
        """
        Register many papers in a single transaction.

        Each dict must have 'paper_id'; optionally source_xml, source_pdf,
        input_hash, tags, lineage_meta.

        Returns (inserted, skipped) counts.
        """
        now = _now()
        inserted = 0
        skipped  = 0

        with self._lock, self._transaction() as conn:
            for p in papers:
                pid = p["paper_id"]
                if skip_existing:
                    exists = conn.execute(
                        "SELECT 1 FROM pipeline_registry WHERE paper_id = ?", [pid]
                    ).fetchone()
                    if exists:
                        skipped += 1
                        continue

                conn.execute(
                    """
                    INSERT OR REPLACE INTO pipeline_registry
                        (paper_id, source_xml, source_pdf, status, version,
                         created_at, updated_at, input_hash, max_retries,
                         pipeline_version, tags, lineage_meta)
                    VALUES (?,?,?,?,?, ?,?,?,?, ?,?,?)
                    """,
                    [
                        pid,
                        p.get("source_xml", ""),
                        p.get("source_pdf", ""),
                        PaperStatus.NEW, 0,
                        now, now,
                        p.get("input_hash"),
                        self._max_retries,
                        self._pipeline_ver,
                        p.get("tags", []),
                        json.dumps(p.get("lineage_meta", {})),
                    ],
                )
                inserted += 1

        log.info("[registry] bulk_register: %d inserted, %d skipped", inserted, skipped)
        return inserted, skipped

    # ── Convenience queries ───────────────────────────────────────────────────

    def get_status(self, paper_id: str) -> Optional[str]:
        """Return current status of a paper, or None if not registered."""
        with self._lock:
            row = self._connect().execute(
                "SELECT status FROM pipeline_registry WHERE paper_id = ?", [paper_id]
            ).fetchone()
        return row[0] if row else None

    def paper_exists(self, paper_id: str) -> bool:
        return self.get_status(paper_id) is not None

    def query(self, sql: str, params: Optional[list] = None) -> list[dict]:
        """
        Run an arbitrary SELECT query and return list of dicts.

        For analytics, exploration, and custom dashboards.
        Use with care — SQL injection risk if params come from user input.
        """
        with self._lock:
            conn = self._connect()
            cursor = conn.execute(sql, params or [])
            cols   = [d[0] for d in cursor.description]
            return [dict(zip(cols, row)) for row in cursor.fetchall()]

    def stats_summary(self) -> dict:
        """Quick one-liner summary for CLI / tqdm postfix."""
        sql = """
        SELECT
            COUNT(*) FILTER (WHERE status = 'SUCCESS')   AS ok,
            COUNT(*) FILTER (WHERE status IN ('FAIL','INVALID_XML','EMPTY_OUTPUT','JSON_ERROR')) AS fail,
            COUNT(*) FILTER (WHERE status = 'SKIPPED')   AS skip,
            COUNT(*) FILTER (WHERE status = 'PROCESSING')AS proc,
            COUNT(*) FILTER (WHERE status IN ('NEW','QUEUED','RETRY')) AS pend
        FROM pipeline_registry
        """
        with self._lock:
            r = self._connect().execute(sql).fetchone()
        return {"ok": r[0], "fail": r[1], "skip": r[2], "proc": r[3], "pend": r[4]}

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _append_event(
        self,
        conn: duckdb.DuckDBPyConnection,
        paper_id: str,
        event_type: str,
        *,
        status_from: Optional[str] = None,
        status_to:   Optional[str] = None,
        stage:       Optional[str] = None,
        worker_id:   Optional[str] = None,
        runtime_sec: Optional[float] = None,
        message:     Optional[str] = None,
        metadata:    Optional[dict] = None,
    ) -> None:
        """Insert one record into the append-only audit log."""
        seq = int(time.time_ns() // 1000)  # microsecond-resolution sequence
        conn.execute(
            """
            INSERT INTO registry_events
                (event_seq, paper_id, event_type, status_from, status_to,
                 stage, worker_id, runtime_sec, message, metadata, created_at)
            VALUES (?,?,?,?,?, ?,?,?,?,?, ?)
            """,
            [
                seq, paper_id, event_type,
                str(status_from) if status_from else None,
                str(status_to)   if status_to   else None,
                stage, worker_id or self._worker_id,
                runtime_sec, message,
                json.dumps(metadata or {}),
                _now(),
            ],
        )

    def _cleanup_heartbeat(
        self,
        conn: duckdb.DuckDBPyConnection,
        paper_id: str,
        worker_id: str,
    ) -> None:
        conn.execute(
            "DELETE FROM worker_heartbeats WHERE paper_id = ? AND worker_id = ?",
            [paper_id, worker_id],
        )


# ─── Utility ─────────────────────────────────────────────────────────────────

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _now_plus(seconds: float) -> datetime:
    from datetime import timedelta
    return datetime.now(timezone.utc) + timedelta(seconds=seconds)


def _parse_ts(ts) -> datetime:
    """Parse DuckDB timestamp value to datetime."""
    if isinstance(ts, datetime):
        return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
    if isinstance(ts, str):
        return datetime.fromisoformat(ts).replace(tzinfo=timezone.utc)
    return _now()
