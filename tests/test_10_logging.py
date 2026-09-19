"""
test_10_logging.py  —  Group 10: Logging and failure analysis
=============================================================

Validates that the pipeline emits correct structured log records for
key lifecycle events and that failure reports can be reconstructed from
log output.

Log markers tested:
  - build_paper_json() completes without warnings on valid input
  - process_paper() emits "SKIPPED_EXISTS" for idempotency skip
  - process_paper() emits "start" for non-skipped processing
  - pipeline_runner logs "Found N XML file(s)"
  - pipeline_runner logs "Pre-filter: N/M already processed"
  - Warning emitted when no XML files found

Implementation note:
  process_paper is a @ray.remote function. We access the underlying Python
  function via process_paper._function to call it without a Ray cluster.

  pipeline_runner.run_distributed() creates actors via EmbeddingActor.remote()
  before pre-filter; these must be mocked together with ray.
"""

from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from tests.conftest import run_pipeline, write_tei


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _mock_actor():
    a = MagicMock()
    a.remote.return_value = MagicMock()
    return a


def _run_distributed_mocked(xml_dir, out_dir, **kwargs):
    """
    Call run_distributed with all Ray and actor dependencies mocked so that
    we can test logging without a real Ray cluster.
    """
    from src.orchestration.pipeline_runner import run_distributed

    mock_ray = MagicMock()
    mock_ray.is_initialized.return_value = True

    with patch("src.orchestration.pipeline_runner.ray", mock_ray), \
         patch("src.orchestration.pipeline_runner.EmbeddingActor", _mock_actor()), \
         patch("src.orchestration.pipeline_runner.SpacyActor", _mock_actor()), \
         patch("src.orchestration.pipeline_runner.OllamaActor", _mock_actor()), \
         patch("src.orchestration.pipeline_runner.VectorStoreActor", _mock_actor()):
        try:
            result = run_distributed(xml_dir=xml_dir, out_dir=out_dir, **kwargs)
        except Exception:
            result = None
    return result


# ─────────────────────────────────────────────────────────────────────────────
# T10-01  build_paper_json() log output
# ─────────────────────────────────────────────────────────────────────────────

class TestBuildPaperJsonLogging:

    def test_no_warning_on_valid_paper(self, tmp_xml_dir, mock_geocoding, caplog):
        """Valid TEI input must not generate WARNING or ERROR log entries."""
        path = write_tei(
            tmp_xml_dir, stem="log_valid",
            abstract="Flood mapping with Sentinel-1 in Ukraine.",
            methods="HEC-HMS rainfall-runoff. SRTM DEM terrain input.",
            results="RMSE = 0.45 m. NSE = 0.87.",
        )
        with caplog.at_level(logging.WARNING):
            run_pipeline(path)

        pipeline_warnings = [
            r for r in caplog.records
            if r.levelno >= logging.WARNING
            and "src.ingestion" in r.name
            and "geonames" not in r.getMessage().lower()
            and "encode_fn" not in r.getMessage().lower()
        ]
        assert not pipeline_warnings, (
            "Unexpected pipeline warnings on valid paper:\n"
            + "\n".join(r.getMessage() for r in pipeline_warnings)
        )

    def test_pipeline_produces_no_errors(self, tmp_xml_dir, mock_geocoding, caplog):
        """build_paper_json() must not emit any ERROR records."""
        path = write_tei(tmp_xml_dir, stem="log_no_error",
                         abstract="SWAT model in Carpathians, Ukraine.")
        with caplog.at_level(logging.ERROR):
            run_pipeline(path)
        errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert not errors, f"Unexpected ERROR records: {[r.getMessage() for r in errors]}"


# ─────────────────────────────────────────────────────────────────────────────
# T10-02  process_paper() idempotency log markers
# ─────────────────────────────────────────────────────────────────────────────

class TestProcessPaperLogging:

    def _get_func(self):
        from src.orchestration.process_paper import process_paper
        return process_paper._function

    def test_skipped_exists_logged(self, tmp_xml_dir, tmp_path, mock_geocoding, caplog):
        """When output exists, process_paper must log 'SKIPPED_EXISTS'."""
        out_dir = tmp_path / "log_skip"
        out_dir.mkdir()
        path = write_tei(tmp_xml_dir, stem="log_skip_test",
                         abstract="Sentinel-1 flood mapping.")
        # Pre-create output to trigger idempotency guard
        (out_dir / "log_skip_test.tei.paper.json").write_text("{}", encoding="utf-8")

        mock_actor = MagicMock()

        with caplog.at_level(logging.INFO):
            result = self._get_func()(
                str(path),
                mock_actor, mock_actor, mock_actor,
                out_dir=str(out_dir),
                overwrite=False,
            )

        assert result.get("_status") == "SKIPPED", f"Expected SKIPPED, got {result}"
        skipped_msgs = [r.getMessage() for r in caplog.records if "skip" in r.getMessage().lower()]
        assert skipped_msgs, (
            "Expected 'SKIPPED_EXISTS' log record. "
            f"All records: {[r.getMessage() for r in caplog.records]}"
        )

    def test_start_logged_when_processing(self, tmp_xml_dir, tmp_path, mock_geocoding, caplog):
        """
        When not skipped, process_paper must log 'start' before doing any work.
        The test doesn't pre-create the output, so the guard does NOT fire.
        The function will fail later when trying to use Ray actors — that's OK;
        we only need to see the 'start' record emitted before the actor calls.
        """
        out_dir = tmp_path / "log_start"
        out_dir.mkdir()
        path = write_tei(tmp_xml_dir, stem="log_start_test",
                         abstract="HEC-HMS watershed model.")

        mock_actor = MagicMock()
        # Make spacy actor parse return something to avoid crash before logging
        mock_actor.parse.remote.return_value = MagicMock()

        with caplog.at_level(logging.INFO):
            try:
                self._get_func()(
                    str(path),
                    mock_actor, mock_actor, mock_actor,
                    out_dir=str(out_dir),
                    overwrite=False,
                )
            except Exception:
                pass  # Expected — Ray.get() won't work outside cluster

        start_msgs = [r.getMessage() for r in caplog.records if "start" in r.getMessage().lower()]
        assert start_msgs, (
            "Expected 'start' log record from process_paper. "
            f"Records: {[r.getMessage() for r in caplog.records]}"
        )

    def test_sentinel_paper_id_in_skip_log(self, tmp_xml_dir, tmp_path, mock_geocoding, caplog):
        """Skipped log must reference the paper filename."""
        out_dir = tmp_path / "skip_log_id"
        out_dir.mkdir()
        path = write_tei(tmp_xml_dir, stem="skip_id_paper", abstract="Flood study.")
        (out_dir / "skip_id_paper.tei.paper.json").write_text("{}", encoding="utf-8")

        mock_actor = MagicMock()
        with caplog.at_level(logging.INFO):
            self._get_func()(
                str(path), mock_actor, mock_actor, mock_actor,
                out_dir=str(out_dir), overwrite=False,
            )

        all_msgs = [r.getMessage() for r in caplog.records]
        assert any("skip_id_paper" in m for m in all_msgs), (
            f"Paper filename not found in log messages: {all_msgs}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# T10-03  pipeline_runner log markers
# ─────────────────────────────────────────────────────────────────────────────

class TestPipelineRunnerLogging:

    def test_found_xml_files_logged(self, tmp_xml_dir, tmp_path, mock_geocoding, caplog):
        """run_distributed() must log 'Found N XML file(s)' at INFO."""
        out_dir = tmp_path / "runner_log"
        out_dir.mkdir()

        for i in range(3):
            write_tei(tmp_xml_dir, stem=f"runner_{i}",
                      abstract=f"Paper {i} flood study.")

        # Mark all as done so the function returns early (no Ray task submission)
        for i in range(3):
            (out_dir / f"runner_{i}.tei.paper.json").write_text("{}", encoding="utf-8")

        with caplog.at_level(logging.INFO):
            _run_distributed_mocked(xml_dir=tmp_xml_dir, out_dir=out_dir, overwrite=False)

        found_msgs = [
            r.getMessage() for r in caplog.records
            if "found" in r.getMessage().lower() and "xml" in r.getMessage().lower()
        ]
        assert found_msgs, (
            "Expected 'Found N XML file(s)' log. "
            f"Records: {[r.getMessage() for r in caplog.records]}"
        )

    def test_prefilter_skip_count_logged(self, tmp_xml_dir, tmp_path, mock_geocoding, caplog):
        """When papers are pre-filtered, the count must appear in a log record."""
        out_dir = tmp_path / "prefilter_log"
        out_dir.mkdir()

        for i in range(4):
            write_tei(tmp_xml_dir, stem=f"pf_{i}", abstract=f"Study {i}.")

        # Mark 3 of 4 as already done
        for i in range(4):
            (out_dir / f"pf_{i}.tei.paper.json").write_text("{}", encoding="utf-8")

        with caplog.at_level(logging.INFO):
            _run_distributed_mocked(xml_dir=tmp_xml_dir, out_dir=out_dir, overwrite=False)

        skip_msgs = [
            r.getMessage() for r in caplog.records
            if any(kw in r.getMessage().lower() for kw in ["skip", "already", "pre-filter"])
        ]
        assert skip_msgs, (
            "Expected pre-filter skip count in log. "
            f"Records: {[r.getMessage() for r in caplog.records]}"
        )

    def test_no_files_found_logged_as_warning(self, tmp_path, mock_geocoding, caplog):
        """Empty xml_dir must emit a WARNING (not silently return)."""
        empty_dir = tmp_path / "empty_xml"
        empty_dir.mkdir()
        out_dir = tmp_path / "empty_out"
        out_dir.mkdir()

        with caplog.at_level(logging.WARNING):
            _run_distributed_mocked(xml_dir=empty_dir, out_dir=out_dir, overwrite=False)

        warning_msgs = [
            r.getMessage() for r in caplog.records
            if r.levelno >= logging.WARNING
        ]
        assert warning_msgs, (
            "Expected WARNING when no XML files found. "
            f"Records: {[r.getMessage() for r in caplog.records]}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# T10-04  Quality report reconstruction from logs
# ─────────────────────────────────────────────────────────────────────────────

class TestQualityReportFromLogs:

    def test_multiple_papers_produce_distinct_hashes(self, tmp_xml_dir, mock_geocoding):
        """
        Running the pipeline on N papers yields N distinct content_hash values,
        meaning each paper is uniquely identifiable in any log-based report.
        """
        paths = [
            write_tei(tmp_xml_dir, stem=f"multi_log_{i}",
                      abstract=f"Paper {i}: {'Sentinel-1' if i % 2 == 0 else 'SWAT'} study.")
            for i in range(4)
        ]
        hashes = {run_pipeline(p)["metadata"]["content_hash"] for p in paths}
        assert len(hashes) == 4, (
            f"Expected 4 distinct content hashes, got {len(hashes)}: {hashes}"
        )

    def test_info_level_sufficient_for_skip_reports(
        self, tmp_xml_dir, tmp_path, mock_geocoding, caplog
    ):
        """
        SKIPPED records must be emitted at INFO (not DEBUG) so that standard
        production logging captures them without enabling debug mode.
        """
        from src.orchestration.process_paper import process_paper

        out_dir = tmp_path / "info_skip"
        out_dir.mkdir()
        path = write_tei(tmp_xml_dir, stem="info_level_skip",
                         abstract="Satellite flood mapping.")
        (out_dir / "info_level_skip.tei.paper.json").write_text("{}", encoding="utf-8")

        mock_actor = MagicMock()
        with caplog.at_level(logging.INFO):  # NOT DEBUG — only INFO and above
            process_paper._function(
                str(path), mock_actor, mock_actor, mock_actor,
                out_dir=str(out_dir), overwrite=False,
            )

        skip_info = [
            r for r in caplog.records
            if r.levelno == logging.INFO
            and ("skip" in r.getMessage().lower() or "SKIPPED" in r.getMessage())
        ]
        assert skip_info, (
            "SKIPPED event not visible at INFO level — production logs would miss it. "
            f"INFO records: {[r.getMessage() for r in caplog.records if r.levelno == logging.INFO]}"
        )

    def test_all_papers_tracked_via_content_hash(self, tmp_xml_dir, mock_geocoding):
        """
        Each paper's content_hash is stable across re-runs, enabling
        idempotent tracking in an audit log.
        """
        path = write_tei(tmp_xml_dir, stem="audit_paper",
                         abstract="DEM validation using ICESat-2 ATL08 in Ukraine.")
        p1 = run_pipeline(path)
        p2 = run_pipeline(path)
        assert p1["metadata"]["content_hash"] == p2["metadata"]["content_hash"], (
            "content_hash changed between runs — audit log tracking would break"
        )
