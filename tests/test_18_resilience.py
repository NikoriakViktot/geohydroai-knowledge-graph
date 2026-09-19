"""
test_18_resilience.py — retry, ідемпотентність pre-filter, зіпсовані входи
(Фази 3.2, 3.3, 3.5).

Зіпсовані входи — реальні failure-modes корпусу: 12 обрізаних Elsevier
paper.json (KNOWN ISSUE #4), биті TEI, не-PDF байти.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.orchestration.retry import retry_call
from src.orchestration.pipeline_runner import pending_xml_files


# ── retry_call (Фаза 3.2) ─────────────────────────────────────────────────────

def test_retry_recovers_from_transient():
    calls = {"n": 0}
    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise ConnectionError("transient")
        return "ok"
    assert retry_call(flaky, backoff_s=0.01) == "ok"
    assert calls["n"] == 3


def test_retry_does_not_hide_programming_errors():
    """TypeError/KeyError — баги, не transient; пропливають одразу."""
    calls = {"n": 0}
    def buggy():
        calls["n"] += 1
        raise TypeError("bug")
    with pytest.raises(TypeError):
        retry_call(buggy, backoff_s=0.01)
    assert calls["n"] == 1


def test_retry_raises_after_exhaustion():
    def always(): raise TimeoutError("permanent")
    with pytest.raises(TimeoutError):
        retry_call(always, attempts=2, backoff_s=0.01)


# ── Level-1 ідемпотентність (Фаза 3.3) ────────────────────────────────────────
# Регресія тут = масова GPU-переекстракція 4.8K статей.

def test_prefilter_skips_done_papers(tmp_path: Path):
    xml_dir, out_dir = tmp_path / "xml", tmp_path / "out"
    xml_dir.mkdir(); out_dir.mkdir()
    xmls = []
    for stem in ("a", "b", "c"):
        p = xml_dir / f"{stem}.tei.xml"
        p.write_text("<TEI/>")
        xmls.append(p)
    (out_dir / "a.tei.paper.json").write_text("{}")   # 'a' вже оброблена

    pending = pending_xml_files(xmls, out_dir)
    assert [p.stem for p in pending] == ["b.tei", "c.tei"]


def test_prefilter_second_run_submits_zero(tmp_path: Path):
    """Повторний прогін після успіху не створює ЖОДНОЇ задачі."""
    xml_dir, out_dir = tmp_path / "xml", tmp_path / "out"
    xml_dir.mkdir(); out_dir.mkdir()
    xmls = []
    for stem in ("x", "y"):
        p = xml_dir / f"{stem}.tei.xml"
        p.write_text("<TEI/>")
        (out_dir / f"{stem}.tei.paper.json").write_text("{}")
        xmls.append(p)
    assert pending_xml_files(xmls, out_dir) == []


def test_prefilter_overwrite_resubmits_all(tmp_path: Path):
    xml_dir, out_dir = tmp_path / "xml", tmp_path / "out"
    xml_dir.mkdir(); out_dir.mkdir()
    p = xml_dir / "x.tei.xml"; p.write_text("<TEI/>")
    (out_dir / "x.tei.paper.json").write_text("{}")
    assert pending_xml_files([p], out_dir, overwrite=True) == [p]


# ── Зіпсовані входи (Фаза 3.5) ────────────────────────────────────────────────

def test_truncated_paper_json_is_graceful(tmp_path: Path):
    """Обрізаний mid-write JSON (реальний кейс 12 Elsevier-файлів)."""
    full = json.dumps({"metadata": {"title": "T"}, "sections": {"abstract": "A" * 500}})
    truncated = tmp_path / "1-s2.0-TRUNC-main.tei.paper.json"
    truncated.write_text(full[: len(full) // 2])    # обрив посередині

    with pytest.raises(json.JSONDecodeError):
        json.loads(truncated.read_text())           # сирий read падає…

    # …але корпусні читачі мають пропускати такий файл без краху:
    from src.evaluation.goldset_sampler import _load_papers
    import src.evaluation.goldset_sampler as gs
    orig = gs.PAPER_JSON_DIR
    try:
        gs.PAPER_JSON_DIR = tmp_path
        papers = _load_papers()
        assert papers == []                          # пропущено, не впало
    finally:
        gs.PAPER_JSON_DIR = orig


def test_empty_and_invalid_tei_graceful():
    from src.ingestion.tei_validator import validate_tei
    from src.ingestion.failure_types import FailureType

    q, ft = validate_tei("")
    assert q is None and ft == FailureType.EMPTY_TEI

    q, ft = validate_tei("<TEI><unclosed")
    assert q is None and ft == FailureType.INVALID_XML


def test_non_pdf_bytes_raise_pdf_io_error(tmp_path: Path):
    from src.document.pdf_io import PdfIOError, probe_pdf, extract_pages_text
    fake = tmp_path / "fake.pdf"
    fake.write_bytes(b"this is not a pdf at all" * 10)
    with pytest.raises(PdfIOError):
        probe_pdf(fake)
    with pytest.raises(PdfIOError):
        extract_pages_text(fake)


def test_page_geometry_helpers_raise_pdf_io_error_on_bad_bytes(tmp_path: Path):
    """Facade additions for the yearbook extractor fail the same named way."""
    from src.document.pdf_io import (PdfIOError, extract_page_tables,
                                     extract_page_text, extract_page_words)
    fake = tmp_path / "fake.pdf"
    fake.write_bytes(b"%PDF-1.4 but nothing else")
    for fn in (extract_page_text, extract_page_words, extract_page_tables):
        with pytest.raises(PdfIOError):
            fn(fake, 1)


def test_corrupt_parquet_detected(tmp_path: Path):
    import pyarrow.parquet as pq
    bad = tmp_path / "regions.parquet"
    bad.write_bytes(b"PAR1 garbage not a real parquet file")
    with pytest.raises(Exception):                   # pyarrow.ArrowInvalid
        pq.read_table(bad)
