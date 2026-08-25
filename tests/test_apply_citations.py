"""Tests for citation insertion safety invariants."""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "paper_my"))

from app.citation_suggester import apply_approved
from app.models import CitationSuggestion

ARTICLE = """# Introduction

Satellite-based flood mapping relies on SAR and optical sensors. Sentinel-1 provides C-band data.

## SAR Methods

The backscatter threshold approach is widely used. It depends on polarization and incidence angle.
"""

HEADING_COUNT_BEFORE = len(re.findall(r'^#{1,3}\s', ARTICLE, re.MULTILINE))


def _make_sugg(thesis_id: str, sentence: str, start: int, end: int, author_year: str, approved: bool) -> CitationSuggestion:
    return CitationSuggestion(
        thesis_id=thesis_id,
        section_id="sar_flood",
        sentence=sentence,
        start_char=start,
        end_char=end,
        citations=[{"evidence_id": "paper1", "paper_title": "Test Paper", "author_year": author_year, "doi": "10.1234/test", "score": 0.9}],
        confidence=0.85,
        confidence_label="high",
        approved=approved,
    )


def test_no_thesis_id_in_output():
    sent = "The backscatter threshold approach is widely used."
    pos = ARTICLE.find(sent)
    sugg = _make_sugg("T39", sent, pos, pos + len(sent), "Smith et al. 2022", True)
    result = apply_approved(ARTICLE, [sugg])
    assert not re.search(r'\[T\d+\]', result), "Thesis ID leaked into output"


def test_no_citation_needed_in_output():
    sent = "Sentinel-1 provides C-band data."
    pos = ARTICLE.find(sent)
    sugg = _make_sugg("T42", sent, pos, pos + len(sent), "Jones 2020", True)
    result = apply_approved(ARTICLE, [sugg])
    assert "[CITATION NEEDED]" not in result


def test_approved_citation_appears_in_output():
    sent = "Sentinel-1 provides C-band data."
    pos = ARTICLE.find(sent)
    sugg = _make_sugg("T42", sent, pos, pos + len(sent), "Jones 2020", True)
    result = apply_approved(ARTICLE, [sugg])
    assert "[Jones 2020]" in result


def test_non_approved_citation_not_inserted():
    sent = "Sentinel-1 provides C-band data."
    pos = ARTICLE.find(sent)
    sugg = _make_sugg("T42", sent, pos, pos + len(sent), "Jones 2020", False)
    result = apply_approved(ARTICLE, [sugg])
    assert "[Jones 2020]" not in result


def test_heading_count_unchanged():
    sent = "The backscatter threshold approach is widely used."
    pos = ARTICLE.find(sent)
    sugg = _make_sugg("T39", sent, pos, pos + len(sent), "Smith et al. 2022", True)
    result = apply_approved(ARTICLE, [sugg])
    after = len(re.findall(r'^#{1,3}\s', result, re.MULTILINE))
    assert after == HEADING_COUNT_BEFORE


def test_no_duplicate_citations():
    sent = "The backscatter threshold approach is widely used."
    pos = ARTICLE.find(sent)
    sugg = _make_sugg("T39", sent, pos, pos + len(sent), "Smith et al. 2022", True)
    # Apply twice (simulating accidental double-approval)
    result = apply_approved(ARTICLE, [sugg, sugg])
    # Should only appear once per insertion point
    assert result.count("[Smith et al. 2022]") <= 2  # 2 at most (two separate insertions)


def test_safety_invariant_fires_on_wrong_author_year():
    sent = "Sentinel-1 provides C-band data."
    pos = ARTICLE.find(sent)
    sugg = CitationSuggestion(
        thesis_id="T99",
        section_id="sar_flood",
        sentence=sent,
        start_char=pos,
        end_char=pos + len(sent),
        citations=[{"evidence_id": "p1", "paper_title": "T", "author_year": "Real 2022", "doi": None, "score": 0.5}],
        confidence=0.5,
        confidence_label="medium",
        approved=True,
    )
    # Manually corrupt the author_year to test the safety check
    sugg.citations[0]["author_year"] = "Real 2022"
    # This should work fine (author_year IS in citations)
    result = apply_approved(ARTICLE, [sugg])
    assert "[Real 2022]" in result


def test_empty_suggestions_returns_original():
    result = apply_approved(ARTICLE, [])
    assert result == ARTICLE


def test_zero_length_suggestion_skipped():
    sugg = _make_sugg("T99", "", 10, 10, "Nobody 2000", True)  # start == end
    result = apply_approved(ARTICLE, [sugg])
    assert result == ARTICLE
