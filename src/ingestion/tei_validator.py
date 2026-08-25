"""
tei_validator.py — TEI XML semantic validation layer.

Parses the GROBID TEI XML output and extracts structured quality flags.
Does NOT alter the XML — pure read-only analysis.

All XPath queries use the TEI namespace to be robust across GROBID versions.
"""

from __future__ import annotations

import logging
from typing import Optional

from src.document.tei_io import Element, TEIXMLError, parse_string, TEI_NS

from src.ingestion.failure_types import FailureType
from src.ingestion.models import TEIQuality

log = logging.getLogger(__name__)

NS = TEI_NS


def validate_tei(xml_text: str) -> tuple[Optional[TEIQuality], FailureType]:
    """
    Parse and structurally validate GROBID TEI XML.

    Returns:
        (TEIQuality, FailureType.SUCCESS) on valid XML, or
        (None, FailureType.INVALID_XML | FailureType.EMPTY_TEI) on failure.
    """
    if not xml_text or not xml_text.strip():
        return None, FailureType.EMPTY_TEI

    try:
        root = parse_string(xml_text)
    except TEIXMLError as exc:
        log.warning("INVALID_XML: %s", exc)
        return None, FailureType.INVALID_XML

    q = _extract_quality(root)

    if not q.has_title and not q.has_body:
        return None, FailureType.EMPTY_TEI

    return q, FailureType.SUCCESS


def _extract_quality(root: Element) -> TEIQuality:
    def has(xpath: str) -> bool:
        return bool(root.xpath(xpath, namespaces=NS))

    def count(xpath: str) -> int:
        return len(root.xpath(xpath, namespaces=NS))

    has_title       = has("//tei:titleStmt/tei:title[@level='a' or @level='m']")
    has_abstract    = has("//tei:abstract")
    has_body        = has("//tei:body//tei:div")
    has_references  = has("//tei:listBibl/tei:biblStruct")
    has_figures     = has("//tei:figure[not(@type='table')]")
    has_tables      = has("//tei:figure[@type='table']")
    has_formulas    = has("//tei:formula")
    has_coordinates = has("//*[@coords]")

    ref_count      = count("//tei:listBibl/tei:biblStruct")
    figure_count   = count("//tei:figure[not(@type='table')]")
    table_count    = count("//tei:figure[@type='table']")
    sentence_count = count("//tei:s")

    return TEIQuality(
        has_title=has_title,
        has_abstract=has_abstract,
        has_body=has_body,
        has_references=has_references,
        has_figures=has_figures,
        has_tables=has_tables,
        has_formulas=has_formulas,
        has_coordinates=has_coordinates,
        ref_count=ref_count,
        figure_count=figure_count,
        table_count=table_count,
        sentence_count=sentence_count,
    )
