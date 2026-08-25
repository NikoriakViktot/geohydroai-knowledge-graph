"""
analytics.py  —  DuckDB analytics queries for the pipeline registry
====================================================================

All queries run directly against DuckDB — no pandas, no conversion overhead.

Usage
-----
    from src.registry.analytics import RegistryAnalytics
    from src.registry import PipelineRegistry

    reg = PipelineRegistry(registry_dir)
    reg.init_registry()

    ana = RegistryAnalytics(reg)
    print(ana.success_rate())
    print(ana.most_common_errors())
    df  = ana.to_dataframe("SELECT * FROM pipeline_registry LIMIT 100")
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Named SQL queries — importable for use in Airflow / Dagster operators
# ─────────────────────────────────────────────────────────────────────────────

SQL_SUCCESS_RATE = """
SELECT
    COUNT(*)                                            AS total,
    COUNT(*) FILTER (WHERE status = 'SUCCESS')          AS success_n,
    COUNT(*) FILTER (WHERE status IN
        ('FAIL','INVALID_XML','EMPTY_OUTPUT','JSON_ERROR')) AS fail_n,
    COUNT(*) FILTER (WHERE status = 'SKIPPED')          AS skipped_n,
    ROUND(
        100.0 * COUNT(*) FILTER (WHERE status = 'SUCCESS')
        / NULLIF(COUNT(*), 0), 2
    )                                                   AS success_rate_pct,
    ROUND(
        100.0 * COUNT(*) FILTER (WHERE status IN
            ('FAIL','INVALID_XML','EMPTY_OUTPUT','JSON_ERROR'))
        / NULLIF(COUNT(*), 0), 2
    )                                                   AS failure_rate_pct
FROM pipeline_registry;
"""

SQL_AVERAGE_RUNTIME = """
SELECT
    ROUND(AVG(runtime_sec), 3)                                      AS avg_sec,
    ROUND(MEDIAN(runtime_sec), 3)                                   AS median_sec,
    ROUND(PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY runtime_sec), 3) AS p95_sec,
    ROUND(PERCENTILE_CONT(0.99) WITHIN GROUP (ORDER BY runtime_sec), 3) AS p99_sec,
    ROUND(MIN(runtime_sec), 3)                                      AS min_sec,
    ROUND(MAX(runtime_sec), 3)                                      AS max_sec,
    COUNT(*)                                                        AS sample_n
FROM pipeline_registry
WHERE status = 'SUCCESS' AND runtime_sec IS NOT NULL;
"""

SQL_SLOWEST_PAPERS = """
SELECT
    paper_id,
    ROUND(runtime_sec, 2)   AS runtime_sec,
    worker_id,
    pipeline_version,
    finished_at::DATE       AS date
FROM pipeline_registry
WHERE status = 'SUCCESS' AND runtime_sec IS NOT NULL
ORDER BY runtime_sec DESC
LIMIT {limit};
"""

SQL_MOST_COMMON_ERRORS = """
SELECT
    error_type,
    COUNT(*)                                        AS occurrences,
    ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 1) AS pct_of_failures,
    AVG(retry_count)                                AS avg_retries_before_fail,
    MIN(updated_at)::DATE                           AS first_seen,
    MAX(updated_at)::DATE                           AS last_seen
FROM pipeline_registry
WHERE status IN ('FAIL','INVALID_XML','EMPTY_OUTPUT','JSON_ERROR','RETRY')
  AND error_type IS NOT NULL
GROUP BY error_type
ORDER BY occurrences DESC
LIMIT {limit};
"""

SQL_RETRY_STATISTICS = """
SELECT
    retry_count,
    COUNT(*)        AS papers,
    ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 1) AS pct_of_total,
    COUNT(*) FILTER (WHERE status = 'SUCCESS') AS eventually_succeeded
FROM pipeline_registry
WHERE retry_count > 0
GROUP BY retry_count
ORDER BY retry_count;
"""

SQL_DAILY_INGESTION_VOLUME = """
SELECT
    finished_at::DATE                               AS day,
    COUNT(*)                                        AS total_processed,
    COUNT(*) FILTER (WHERE status = 'SUCCESS')      AS success,
    COUNT(*) FILTER (WHERE status IN
        ('FAIL','INVALID_XML','EMPTY_OUTPUT','JSON_ERROR')) AS fail,
    COUNT(*) FILTER (WHERE status = 'SKIPPED')      AS skipped,
    ROUND(AVG(runtime_sec), 2)                      AS avg_rt_sec,
    COUNT(DISTINCT worker_id)                        AS active_workers
FROM pipeline_registry
WHERE finished_at IS NOT NULL
GROUP BY 1
ORDER BY 1 DESC
LIMIT 90;
"""

SQL_PIPELINE_THROUGHPUT = """
-- Papers per hour in 1-hour buckets (last 7 days)
SELECT
    DATE_TRUNC('hour', finished_at)             AS hour_bucket,
    COUNT(*)                                    AS papers_finished,
    COUNT(*) FILTER (WHERE status='SUCCESS')    AS succeeded,
    ROUND(AVG(runtime_sec), 2)                  AS avg_rt_sec
FROM pipeline_registry
WHERE finished_at >= now() - INTERVAL '7 days'
  AND finished_at IS NOT NULL
GROUP BY 1
ORDER BY 1 DESC;
"""

SQL_WORKER_UTILIZATION = """
SELECT
    worker_id,
    COUNT(*)                                        AS papers_total,
    COUNT(*) FILTER (WHERE status = 'SUCCESS')      AS success,
    COUNT(*) FILTER (WHERE status IN
        ('FAIL','INVALID_XML','EMPTY_OUTPUT','JSON_ERROR')) AS fail,
    ROUND(
        100.0 * COUNT(*) FILTER (WHERE status = 'SUCCESS')
        / NULLIF(COUNT(*), 0), 1
    )                                               AS success_rate_pct,
    ROUND(AVG(runtime_sec), 2)                      AS avg_rt_sec,
    MIN(started_at)::DATE                           AS first_task_date,
    MAX(finished_at)::DATE                          AS last_task_date
FROM pipeline_registry
WHERE worker_id IS NOT NULL
GROUP BY worker_id
ORDER BY papers_total DESC;
"""

SQL_RESUME_CANDIDATES = """
-- Papers that should be resubmitted on pipeline restart
SELECT
    paper_id,
    source_xml,
    status,
    retry_count,
    max_retries,
    next_retry_at,
    error_type,
    updated_at
FROM pipeline_registry
WHERE
    -- Explicitly queued for retry
    (status = 'RETRY' AND (next_retry_at IS NULL OR next_retry_at <= now()))
    -- Never started
    OR status IN ('NEW', 'QUEUED')
    -- Was processing when pipeline crashed (no terminal status)
    OR (
        status = 'PROCESSING'
        AND (
            heartbeat_at IS NULL
            OR epoch_ms(now()) - epoch_ms(heartbeat_at) > 300 * 1000
        )
    )
ORDER BY
    CASE status
        WHEN 'PROCESSING' THEN 0   -- highest priority: was running
        WHEN 'RETRY'      THEN 1
        WHEN 'QUEUED'     THEN 2
        WHEN 'NEW'        THEN 3
    END,
    retry_count ASC,
    created_at  ASC;
"""

SQL_LINEAGE_SUMMARY = """
-- Per pipeline_version statistics for lineage tracking
SELECT
    pipeline_version,
    COUNT(*)                                        AS papers,
    COUNT(*) FILTER (WHERE status = 'SUCCESS')      AS success,
    COUNT(*) FILTER (WHERE status IN
        ('FAIL','INVALID_XML','EMPTY_OUTPUT','JSON_ERROR')) AS fail,
    ROUND(AVG(runtime_sec), 2)                      AS avg_rt_sec,
    MIN(created_at)::DATE                           AS first_processed,
    MAX(created_at)::DATE                           AS last_processed
FROM pipeline_registry
GROUP BY pipeline_version
ORDER BY last_processed DESC;
"""

SQL_STAGE_TIMING_BREAKDOWN = """
-- Average time per pipeline stage (requires stage_timings JSON column)
WITH stages AS (
    SELECT
        paper_id,
        json_extract(stage_timings, '$.xml_parse')      ::DOUBLE AS xml_parse,
        json_extract(stage_timings, '$.ner')            ::DOUBLE AS ner,
        json_extract(stage_timings, '$.embedding')      ::DOUBLE AS embedding,
        json_extract(stage_timings, '$.entity_extraction')::DOUBLE AS entity_extraction,
        json_extract(stage_timings, '$.geo_extraction') ::DOUBLE AS geo_extraction,
        json_extract(stage_timings, '$.task_classification')::DOUBLE AS task_classification,
        json_extract(stage_timings, '$.llm_judge')      ::DOUBLE AS llm_judge,
        json_extract(stage_timings, '$.json_write')     ::DOUBLE AS json_write
    FROM pipeline_registry
    WHERE status = 'SUCCESS' AND stage_timings IS NOT NULL AND stage_timings != '{}'
)
SELECT
    ROUND(AVG(xml_parse), 3)           AS avg_xml_parse_sec,
    ROUND(AVG(ner), 3)                 AS avg_ner_sec,
    ROUND(AVG(embedding), 3)           AS avg_embedding_sec,
    ROUND(AVG(entity_extraction), 3)   AS avg_entity_extraction_sec,
    ROUND(AVG(geo_extraction), 3)      AS avg_geo_extraction_sec,
    ROUND(AVG(task_classification), 3) AS avg_task_classification_sec,
    ROUND(AVG(llm_judge), 3)           AS avg_llm_judge_sec,
    ROUND(AVG(json_write), 3)          AS avg_json_write_sec,
    COUNT(*)                           AS sample_n
FROM stages;
"""


# ─────────────────────────────────────────────────────────────────────────────
# RegistryAnalytics
# ─────────────────────────────────────────────────────────────────────────────

class RegistryAnalytics:
    """
    Analytics layer over PipelineRegistry.

    Wraps the registry's DuckDB connection to run the named queries above
    and return clean Python dicts or optionally pandas DataFrames.
    """

    def __init__(self, registry) -> None:
        # registry is a PipelineRegistry instance
        self._reg = registry

    # ── Individual analytics ──────────────────────────────────────────────────

    def success_rate(self) -> dict:
        rows = self._reg.query(SQL_SUCCESS_RATE)
        return rows[0] if rows else {}

    def average_runtime(self) -> dict:
        rows = self._reg.query(SQL_AVERAGE_RUNTIME)
        return rows[0] if rows else {}

    def slowest_papers(self, limit: int = 10) -> list[dict]:
        return self._reg.query(SQL_SLOWEST_PAPERS.format(limit=limit))

    def most_common_errors(self, limit: int = 10) -> list[dict]:
        return self._reg.query(SQL_MOST_COMMON_ERRORS.format(limit=limit))

    def retry_statistics(self) -> list[dict]:
        return self._reg.query(SQL_RETRY_STATISTICS)

    def daily_ingestion_volume(self) -> list[dict]:
        return self._reg.query(SQL_DAILY_INGESTION_VOLUME)

    def pipeline_throughput(self) -> list[dict]:
        return self._reg.query(SQL_PIPELINE_THROUGHPUT)

    def worker_utilization(self) -> list[dict]:
        return self._reg.query(SQL_WORKER_UTILIZATION)

    def resume_candidates(self) -> list[dict]:
        return self._reg.query(SQL_RESUME_CANDIDATES)

    def lineage_summary(self) -> list[dict]:
        return self._reg.query(SQL_LINEAGE_SUMMARY)

    def stage_timing_breakdown(self) -> dict:
        rows = self._reg.query(SQL_STAGE_TIMING_BREAKDOWN)
        return rows[0] if rows else {}

    # ── Composite report ──────────────────────────────────────────────────────

    def full_report(self) -> dict:
        """
        Composite analytics report — all metrics in one call.

        Suitable for: daily summary emails, Slack notifications,
        Grafana data sources, Airflow task callbacks.
        """
        return {
            "generated_at":        datetime.now(timezone.utc).isoformat(),
            "success_rate":        self.success_rate(),
            "average_runtime":     self.average_runtime(),
            "slowest_papers":      self.slowest_papers(5),
            "most_common_errors":  self.most_common_errors(5),
            "retry_statistics":    self.retry_statistics(),
            "daily_volume":        self.daily_ingestion_volume()[:7],
            "worker_utilization":  self.worker_utilization(),
            "stage_timings":       self.stage_timing_breakdown(),
            "lineage_summary":     self.lineage_summary(),
        }

    # ── Optional pandas export ────────────────────────────────────────────────

    def to_dataframe(self, sql: str, params: Optional[list] = None):
        """
        Execute SQL and return a pandas DataFrame.

        Requires pandas to be installed.  Use for Jupyter / data science
        workflows only — production code should use the dict-returning methods.
        """
        try:
            import pandas as pd
        except ImportError:
            raise ImportError("pandas required: pip install pandas")
        rows = self._reg.query(sql, params)
        return pd.DataFrame(rows)

    def export_full_report_json(self, out_path: Path) -> Path:
        """Write the full analytics report as JSON to disk."""
        report = self.full_report()
        out_path = Path(out_path)
        out_path.write_text(
            json.dumps(report, indent=2, default=str),
            encoding="utf-8",
        )
        log.info("[analytics] report → %s", out_path)
        return out_path
