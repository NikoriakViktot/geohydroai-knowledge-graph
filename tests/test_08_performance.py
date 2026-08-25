"""
test_08_performance.py  —  Group 8: Performance benchmarks
===========================================================

Measures wall-clock time for critical pipeline stages.
All tests use encode_fn=None (no GPU/embedding) and ner_entities=None (no NER)
to isolate the deterministic parsing stages.

Thresholds are conservative and designed for CI:
  - parse_sections():       < 200 ms per paper
  - build_paper_json():     < 500 ms per paper (no embeddings, no NER)
  - pre-filter (N=100):     < 100 ms total (stat() calls only)
  - batch throughput:       ≥ 2 papers/sec on non-embedded path

Tests are SKIPPED if running in a slow environment (e.g. emulated ARM on x86).
Mark with @pytest.mark.slow to exclude from normal CI if needed.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from tests.conftest import build_tei_xml, run_pipeline, write_tei


# ─────────────────────────────────────────────────────────────────────────────
# Constants — all times in seconds
# ─────────────────────────────────────────────────────────────────────────────

MAX_PARSE_SECTIONS_S  = 0.200   # 200 ms per paper
MAX_BUILD_PAPER_JSON_S = 0.500  # 500 ms per paper (no embeddings)
MAX_PREFILTER_100_S   = 0.100   # 100 ms for 100 stat() calls
MIN_THROUGHPUT_PPS    = 2.0     # papers per second


# ─────────────────────────────────────────────────────────────────────────────
# T08-01  parse_sections() latency
# ─────────────────────────────────────────────────────────────────────────────

class TestParseSectionsLatency:

    def test_parse_sections_under_200ms(self, tmp_xml_dir, mock_geocoding):
        from src.ingestion.pipeline import parse_sections
        from lxml import etree

        path = write_tei(
            tmp_xml_dir, stem="bench_parse",
            abstract="Flood mapping using Sentinel-1 SAR data in Ukraine.",
            methods="HEC-RAS and HEC-HMS were applied. SRTM DEM terrain input.",
            results="RMSE = 0.45 m. NSE = 0.87.",
        )
        tree = etree.parse(str(path))
        root = tree.getroot()

        t0 = time.perf_counter()
        sections = parse_sections(root)
        elapsed = time.perf_counter() - t0

        assert isinstance(sections, dict)
        assert elapsed < MAX_PARSE_SECTIONS_S, (
            f"parse_sections() took {elapsed*1000:.1f} ms — limit is "
            f"{MAX_PARSE_SECTIONS_S*1000:.0f} ms"
        )

    def test_parse_sections_stable_over_10_runs(self, tmp_xml_dir, mock_geocoding):
        from src.ingestion.pipeline import parse_sections
        from lxml import etree

        path = write_tei(tmp_xml_dir, stem="bench_stable",
                         abstract="SWAT watershed model applied in Ukraine.")
        tree = etree.parse(str(path))
        root = tree.getroot()

        times = []
        for _ in range(10):
            t0 = time.perf_counter()
            parse_sections(root)
            times.append(time.perf_counter() - t0)

        avg = sum(times) / len(times)
        assert avg < MAX_PARSE_SECTIONS_S, (
            f"Average parse_sections() time {avg*1000:.1f} ms exceeds limit"
        )


# ─────────────────────────────────────────────────────────────────────────────
# T08-02  build_paper_json() latency
# ─────────────────────────────────────────────────────────────────────────────

class TestBuildPaperJsonLatency:

    def test_build_paper_json_under_500ms(self, tmp_xml_dir, mock_geocoding):
        path = write_tei(
            tmp_xml_dir, stem="bench_build",
            abstract="Sentinel-1 SAR flood mapping in the Dnipro basin, Ukraine.",
            study_area="Dnipro River basin, Kherson Oblast, Ukraine.",
            methods="HEC-HMS rainfall-runoff. SRTM DEM terrain input. SWAT calibration.",
            results="RMSE = 0.45. NSE = 0.87.",
        )

        t0 = time.perf_counter()
        paper = run_pipeline(path)
        elapsed = time.perf_counter() - t0

        assert isinstance(paper, dict)
        assert elapsed < MAX_BUILD_PAPER_JSON_S, (
            f"build_paper_json() took {elapsed*1000:.1f} ms — limit is "
            f"{MAX_BUILD_PAPER_JSON_S*1000:.0f} ms"
        )

    def test_build_paper_json_median_under_500ms(self, tmp_xml_dir, mock_geocoding):
        """Median over 5 runs must stay under limit."""
        paths = [
            write_tei(tmp_xml_dir, stem=f"bench_med_{i}",
                      abstract=f"Flood study {i} using Sentinel-1 in Ukraine.")
            for i in range(5)
        ]
        times = []
        for p in paths:
            t0 = time.perf_counter()
            run_pipeline(p)
            times.append(time.perf_counter() - t0)

        times.sort()
        median = times[len(times) // 2]
        assert median < MAX_BUILD_PAPER_JSON_S, (
            f"Median build_paper_json() time {median*1000:.1f} ms exceeds limit"
        )


# ─────────────────────────────────────────────────────────────────────────────
# T08-03  Pre-filter throughput
# ─────────────────────────────────────────────────────────────────────────────

class TestPreFilterThroughput:

    def test_prefilter_100_files_under_100ms(self, tmp_xml_dir, tmp_path, mock_geocoding):
        """
        The Level-1 pre-filter is a list comprehension with Path.exists() calls.
        For 100 files it must complete in < 100 ms (stat() is O(1) per call).
        """
        out_dir = tmp_path / "pf_bench"
        out_dir.mkdir()

        # Create 100 dummy XML files (minimal)
        xml_files = []
        for i in range(100):
            f = tmp_xml_dir / f"pf_{i:03d}.tei.xml"
            f.write_text(build_tei_xml(abstract=f"Paper {i}."), encoding="utf-8")
            xml_files.append(f)

        # Mark half as already processed
        for f in xml_files[:50]:
            (out_dir / f"{f.stem}.paper.json").write_text("{}", encoding="utf-8")

        t0 = time.perf_counter()
        to_process = [
            f for f in xml_files
            if not (out_dir / f"{f.stem}.paper.json").exists()
        ]
        elapsed = time.perf_counter() - t0

        assert len(to_process) == 50, f"Expected 50 unprocessed, got {len(to_process)}"
        assert elapsed < MAX_PREFILTER_100_S, (
            f"Pre-filter for 100 files took {elapsed*1000:.1f} ms — limit is "
            f"{MAX_PREFILTER_100_S*1000:.0f} ms"
        )


# ─────────────────────────────────────────────────────────────────────────────
# T08-04  Batch throughput
# ─────────────────────────────────────────────────────────────────────────────

class TestBatchThroughput:

    def test_batch_throughput_at_least_2_pps(self, tmp_xml_dir, mock_geocoding):
        """
        Process 10 papers sequentially (no Ray) and verify throughput >= 2/sec.
        This confirms the pipeline is not pathologically slow in unit test mode.
        """
        N = 10
        paths = [
            write_tei(
                tmp_xml_dir, stem=f"batch_{i:02d}",
                abstract=f"Flood study {i} with Sentinel-1 in Ukraine.",
                methods=f"HEC-HMS was used. SRTM DEM terrain input. Paper {i}.",
            )
            for i in range(N)
        ]

        t0 = time.perf_counter()
        for p in paths:
            run_pipeline(p)
        elapsed = time.perf_counter() - t0

        pps = N / elapsed
        assert pps >= MIN_THROUGHPUT_PPS, (
            f"Throughput {pps:.2f} papers/sec is below minimum {MIN_THROUGHPUT_PPS} pps. "
            f"Total: {elapsed:.2f}s for {N} papers."
        )

    def test_per_paper_time_printed(self, tmp_xml_dir, mock_geocoding, capsys):
        """Informational: print per-paper timing for CI logs."""
        N = 5
        paths = [
            write_tei(tmp_xml_dir, stem=f"timing_{i}", abstract=f"Study {i}.")
            for i in range(N)
        ]
        times = []
        for p in paths:
            t0 = time.perf_counter()
            run_pipeline(p)
            times.append(time.perf_counter() - t0)

        avg_ms = sum(times) / len(times) * 1000
        print(f"\n[T08] avg build_paper_json(): {avg_ms:.1f} ms over {N} papers")
        # This test always passes — it's informational only
        assert True
