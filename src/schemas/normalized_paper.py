"""
normalized_paper.py  —  GeoHydroAI versioned schema contract for normalized paper JSON
=======================================================================================

Version: 1.0

This module is the single source of truth for the structure of every file
written to data/normalized/*.json.  All downstream layers (OpenAlex enrichment,
parquet builders, Neo4j graph builder, Gemini reasoning) must validate against
this contract before consuming normalized paper data.

Pydantic v2 is used for strict, self-documenting validation.

Key invariants enforced here:
    - canonical_id is required whenever match_type ∉ {unknown, error}
    - confidence is always in [0.0, 1.0]
    - match_type is a closed enum
    - schema_version is always present

CLI usage:
    python -m src.schemas.normalized_paper validate data/normalized
    python -m src.schemas.normalized_paper validate data/normalized --strict
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Optional

from typing import Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

SCHEMA_VERSION = "1.0"

_QA_DIR = Path(__file__).resolve().parents[2] / "data" / "ontology_qa"


# ─────────────────────────────────────────────────────────────────────────────
# Enums
# ─────────────────────────────────────────────────────────────────────────────

class MatchType(str, Enum):
    alias          = "alias"
    exact          = "exact"
    semantic       = "semantic"
    disambiguation = "disambiguation"
    unknown        = "unknown"
    error          = "error"


# ─────────────────────────────────────────────────────────────────────────────
# Sub-models
# ─────────────────────────────────────────────────────────────────────────────

class NormalizedEntity(BaseModel):
    """
    One normalization result for a single raw extracted mention.

    Produced by src.normalization.ontology_matcher.normalize_entity() and
    stored in paper["normalized_entities"][field_name][i].
    """
    model_config = ConfigDict(extra="forbid")

    raw_name:     str
    canonical_id: Optional[str]  = None
    display_name: Optional[str]  = None
    type:         Optional[str]  = None
    match_type:   MatchType
    confidence:   float          = Field(ge=0.0, le=1.0)
    source_field: Optional[str]  = None
    evidence:     Optional[Any]  = None
    category:     Optional[str]  = None
    subcategory:  Optional[str]  = None
    dimension:    Optional[str]  = None

    @model_validator(mode="after")
    def canonical_id_required_when_matched(self) -> "NormalizedEntity":
        if self.canonical_id is None and self.match_type not in (
            MatchType.unknown, MatchType.error
        ):
            raise ValueError(
                f"canonical_id must not be null when match_type='{self.match_type}' "
                f"(raw_name={self.raw_name!r})"
            )
        return self

    @field_validator("raw_name")
    @classmethod
    def raw_name_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("raw_name must be a non-empty string")
        return v


class MetricValue(BaseModel):
    """A single extracted metric with its numeric value and optional unit."""
    model_config = ConfigDict(extra="allow")

    type:  str
    value: float
    unit:  Optional[str] = None


class ReferenceItem(BaseModel):
    """
    One bibliography entry extracted by GROBID from a paper's reference list.
    Matches the output of src.ingestion.extract_references.extract_references().
    """
    model_config = ConfigDict(extra="allow")

    title:    Optional[str]       = None
    authors:  list[str]           = Field(default_factory=list)
    year:     Optional[int]       = None
    doi:      Optional[str]       = None
    journal:  Optional[str]       = None
    raw_text: Optional[str]       = None


class AuthorInfo(BaseModel):
    """
    Structured author record produced by src.ingestion.pipeline.parse_authors().

    The pipeline stores author dicts, not plain strings.  This model allows
    both shapes so that the schema validates real paper.json files.
    """
    model_config = ConfigDict(extra="allow")

    first_name:             Optional[str]       = None
    last_name:              Optional[str]       = None
    full_name:              Optional[str]       = None
    email:                  Optional[str]       = None
    orcid:                  Optional[str]       = None
    affiliation:            Optional[str]       = None
    affiliation_countries:  list[str]           = Field(default_factory=list)


class PaperMetadata(BaseModel):
    """
    Paper-level bibliographic metadata extracted from GROBID TEI XML.
    Matches the output of src.ingestion.pipeline.parse_metadata().

    Authors are stored as dicts (AuthorInfo) by the pipeline; plain strings
    are also accepted for forward-compatibility with hand-built records.
    """
    model_config = ConfigDict(extra="allow")

    paper_id:     str
    source_xml:   Optional[str]                         = None
    title:        Optional[str]                         = None
    doi:          Optional[str]                         = None
    url:          Optional[str]                         = None
    year:         Optional[str]                         = None
    journal:      Optional[str]                         = None
    publisher:    Optional[str]                         = None
    authors:      list[Union[str, AuthorInfo]]          = Field(default_factory=list)
    content_hash: Optional[str]                         = None


class NormalizationProvenance(BaseModel):
    """
    Pipeline provenance block — tracks which components ran and their outcomes.
    Extra fields are allowed so downstream layers can add their own.
    """
    model_config = ConfigDict(extra="allow")

    parser:                   Optional[str]       = None
    source_xml:               Optional[str]       = None
    has_geo:                  Optional[bool]      = None
    judge_used:               Optional[bool]      = None
    judge_model:              Optional[str]       = None
    unmatched_entities:       Optional[list[str]] = None
    normalized_schema_valid:  Optional[bool]      = None


# ─────────────────────────────────────────────────────────────────────────────
# Top-level model
# ─────────────────────────────────────────────────────────────────────────────

class NormalizedPaper(BaseModel):
    """
    Schema contract for data/normalized/{paper_id}.json.

    Version 1.0 — all downstream consumers must validate against this model
    before processing.

    Versioning:
        schema_version bumps on any backward-incompatible field change.
        Additive-only changes use minor version (handled by extra="allow"
        on sub-models).
    """
    model_config = ConfigDict(
        extra="allow",     # forward-compatible: new top-level fields don't break old readers
        str_strip_whitespace=True,
    )

    schema_version:      str                              = Field(default=SCHEMA_VERSION)
    metadata:            PaperMetadata
    sections:            dict[str, Any]                   = Field(default_factory=dict)
    entities:            dict[str, Any]                   = Field(default_factory=dict)
    normalized_entities: dict[str, list[NormalizedEntity]] = Field(default_factory=dict)
    references:          list[ReferenceItem]              = Field(default_factory=list)
    llm_judge:           Optional[dict[str, Any]]         = None
    provenance:          NormalizationProvenance

    @field_validator("schema_version")
    @classmethod
    def schema_version_must_be_known(cls, v: str) -> str:
        if v != SCHEMA_VERSION:
            raise ValueError(
                f"Unsupported schema_version '{v}'. Expected '{SCHEMA_VERSION}'."
            )
        return v


# ─────────────────────────────────────────────────────────────────────────────
# Validation helpers (used by process_paper.py and the CLI)
# ─────────────────────────────────────────────────────────────────────────────

def validate_paper_dict(data: dict) -> tuple[bool, Optional[str]]:
    """
    Validate a paper dict against the NormalizedPaper schema.

    Args:
        data: Raw dict loaded from a normalized JSON file.

    Returns:
        (is_valid, error_message_or_None)
    """
    try:
        NormalizedPaper.model_validate(data)
        return True, None
    except Exception as exc:
        return False, str(exc)


def validate_directory(
    directory: Path,
    write_errors: bool = True,
) -> dict:
    """
    Scan all *.json files in directory and validate each against NormalizedPaper.

    Args:
        directory:    Path to data/normalized/ or similar.
        write_errors: If True, write invalid examples to
                      data/ontology_qa/normalized_schema_errors.json.

    Returns:
        Summary dict with valid/invalid counts and error list.
    """
    files = sorted(directory.glob("*.json"))
    valid   = 0
    invalid = 0
    errors: list[dict] = []

    for path in files:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            invalid += 1
            errors.append({"file": path.name, "error": f"JSON parse error: {exc}"})
            continue

        ok, msg = validate_paper_dict(data)
        if ok:
            valid += 1
        else:
            invalid += 1
            errors.append({"file": path.name, "error": msg})

    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "directory":    str(directory),
        "total_files":  len(files),
        "valid":        valid,
        "invalid":      invalid,
        "errors":       errors,
    }

    if write_errors and errors:
        _QA_DIR.mkdir(parents=True, exist_ok=True)
        out = _QA_DIR / "normalized_schema_errors.json"
        out.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

    return summary


# ─────────────────────────────────────────────────────────────────────────────
# CLI  (python -m src.schemas.normalized_paper validate <dir>)
# ─────────────────────────────────────────────────────────────────────────────

def _main() -> None:
    import argparse
    import logging
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")

    parser = argparse.ArgumentParser(
        description="GeoHydroAI normalized paper schema validator",
        prog="python -m src.schemas.normalized_paper",
    )
    sub = parser.add_subparsers(dest="command")

    val_cmd = sub.add_parser("validate", help="Validate a directory of normalized JSONs")
    val_cmd.add_argument("directory", type=Path, help="Path to scan (e.g. data/normalized)")
    val_cmd.add_argument(
        "--strict", action="store_true",
        help="Exit code 1 even if only some files are invalid (default: only on all-fail)",
    )
    val_cmd.add_argument(
        "--no-write", action="store_true",
        help="Do not write normalized_schema_errors.json",
    )

    args = parser.parse_args()

    if args.command == "validate":
        directory = args.directory
        if not directory.exists():
            print(f"Directory not found: {directory}")
            sys.exit(1)

        summary = validate_directory(directory, write_errors=not args.no_write)

        print(f"\nSchema Validation: {directory}")
        print(f"  Total files : {summary['total_files']}")
        print(f"  Valid       : {summary['valid']}")
        print(f"  Invalid     : {summary['invalid']}")

        if summary["errors"]:
            print(f"\nInvalid files:")
            for e in summary["errors"][:20]:
                msg = e["error"]
                if len(msg) > 120:
                    msg = msg[:117] + "..."
                print(f"  {e['file']:40s}  {msg}")
            if len(summary["errors"]) > 20:
                print(f"  ... and {len(summary['errors']) - 20} more")
            if not args.no_write:
                print(f"\nError report: data/ontology_qa/normalized_schema_errors.json")

        if summary["invalid"] > 0:
            sys.exit(1)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    _main()
