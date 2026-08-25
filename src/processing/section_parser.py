"""
DEPRECATED: This module is a redirect shim only.
All logic now lives in src.extraction.section_parser.

Kept solely so that any remaining call sites don't break.
DO NOT add new code here.
"""
from __future__ import annotations

from src.extraction.section_parser import (  # noqa: F401
    parse_document_sections,
    describe_parsed,
    SECTION_KEYS,
    SectionDict,
)

_SHIM_KEYS = (
    "abstract", "introduction", "methods", "data",
    "results", "discussion", "conclusion",
)


def split_sections(text: str) -> dict[str, str | None]:
    """Deprecated wrapper — use parse_document_sections() instead."""
    sections = parse_document_sections(text)
    result: dict[str, str | None] = {k: None for k in _SHIM_KEYS}
    for key in ("abstract", "introduction", "methods", "results", "discussion"):
        result[key] = sections.get(key)
    result["data"] = sections.get("methods")
    return result


def describe_sections(sections: dict) -> tuple[list[str], list[str]]:
    """Deprecated wrapper — use describe_parsed() instead."""
    found   = [k for k, v in sections.items() if v]
    missing = [k for k, v in sections.items() if not v]
    return found, missing
