"""Tests for paper_my/app/article_parser."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "paper_my"))

from app.article_parser import parse_article, split_sentences

SAMPLE_ARTICLE = """# Introduction

Satellite-based flood mapping is crucial for disaster management. It supports early warning systems.

## SAR-Based Flood Mapping

Sentinel-1 SAR enables all-weather flood detection. The backscatter threshold approach is widely used.

### Optical Methods

MNDWI achieves high accuracy under clear-sky conditions. NDWI is used for binary water masking.
"""


def test_section_count():
    sections = parse_article(SAMPLE_ARTICLE)
    assert len(sections) == 3  # #, ##, ### = 3 headings


def test_headings_extracted():
    sections = parse_article(SAMPLE_ARTICLE)
    headings = [s.heading for s in sections]
    assert "Introduction" in headings
    assert "SAR-Based Flood Mapping" in headings


def test_level_detection():
    sections = parse_article(SAMPLE_ARTICLE)
    h1 = next(s for s in sections if s.heading == "Introduction")
    assert h1.level == 1
    h2 = next(s for s in sections if s.heading == "SAR-Based Flood Mapping")
    assert h2.level == 2
    h3 = next(s for s in sections if s.heading == "Optical Methods")
    assert h3.level == 3


def test_section_id_guess_sar():
    sections = parse_article(SAMPLE_ARTICLE)
    sar = next(s for s in sections if "SAR" in s.heading)
    assert sar.section_id_guess == "sar_flood"


def test_section_id_guess_optical():
    sections = parse_article(SAMPLE_ARTICLE)
    opt = next(s for s in sections if "Optical" in s.heading)
    assert opt.section_id_guess == "optical_flood"


def test_char_offsets_non_overlapping():
    sections = parse_article(SAMPLE_ARTICLE)
    for i in range(len(sections) - 1):
        assert sections[i].end_char <= sections[i + 1].start_char


def test_full_text_covered():
    sections = parse_article(SAMPLE_ARTICLE)
    total = sum(s.end_char - s.start_char for s in sections)
    assert total == len(SAMPLE_ARTICLE)


def test_split_sentences_basic():
    block = "Sentinel-1 provides SAR data. MNDWI is used for optical mapping. U-Net achieves high IoU."
    sents = split_sentences(block)
    assert len(sents) >= 2
    texts = [s[0] for s in sents]
    assert any("Sentinel-1" in t for t in texts)


def test_split_sentences_offsets_within_block():
    block = "First sentence. Second sentence. Third one here."
    sents = split_sentences(block)
    for text, start, end in sents:
        assert 0 <= start < end <= len(block)
        assert block[start:end].strip() == text.strip()


def test_empty_article():
    sections = parse_article("")
    assert len(sections) == 1
    assert sections[0].section_id_guess is None


def test_no_headings_article():
    text = "This is plain text without any heading. Just some content."
    sections = parse_article(text)
    assert len(sections) == 1
    assert sections[0].text == text
