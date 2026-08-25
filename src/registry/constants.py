"""
constants.py  —  Registry status values and schema version
"""

from __future__ import annotations

from enum import Enum


class PaperStatus(str, Enum):
    NEW         = "NEW"
    QUEUED      = "QUEUED"
    PROCESSING  = "PROCESSING"
    SUCCESS     = "SUCCESS"
    FAIL        = "FAIL"
    SKIPPED     = "SKIPPED"
    RETRY       = "RETRY"
    INVALID_XML = "INVALID_XML"
    EMPTY_OUTPUT= "EMPTY_OUTPUT"
    JSON_ERROR  = "JSON_ERROR"

    def is_terminal(self) -> bool:
        return self in {
            PaperStatus.SUCCESS,
            PaperStatus.SKIPPED,
            PaperStatus.INVALID_XML,
        }

    def is_retriable(self) -> bool:
        return self in {
            PaperStatus.FAIL,
            PaperStatus.EMPTY_OUTPUT,
            PaperStatus.JSON_ERROR,
        }


# Bump this when the schema changes; migration logic lives in registry_db.py
SCHEMA_VERSION = 1

# Pipeline stages for granular telemetry
PIPELINE_STAGES = [
    "xml_parse",
    "ner",
    "embedding",
    "entity_extraction",
    "geo_extraction",
    "task_classification",
    "llm_judge",
    "json_write",
    "normalization",
]

# Heartbeat timeout: workers silent for longer than this are considered stale
HEARTBEAT_TIMEOUT_SEC = 300   # 5 minutes

# Maximum retry attempts before a paper is permanently failed
DEFAULT_MAX_RETRIES = 3
