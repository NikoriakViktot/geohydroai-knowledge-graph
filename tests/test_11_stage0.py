"""
test_11_stage0.py — Unit + integration tests for Stage 0 raw ingestion.

Coverage:
  - RawStore: paths, is_complete, store_pdf, write_metadata/source, append_log,
              verify_pdf_integrity, atomic writes, idempotency
  - Stage0Ingestor: ok, skipped, force, error paths, metadata persistence
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.ingestion.models import ContentHash, TriageResult
from src.ingestion.stage0.ingestor import Stage0Ingestor, Stage0Result
from src.ingestion.stage0.raw_store import RawStore


# ── Helpers ───────────────────────────────────────────────────────────────────

def _fake_pdf(tmp_path: Path, name: str = "test.pdf", content: bytes = b"fake-pdf-content") -> Path:
    p = tmp_path / name
    p.write_bytes(content)
    return p


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _ok_triage() -> TriageResult:
    return TriageResult(
        is_scanned=False, is_encrypted=False, has_text=True,
        page_count=10, estimated_tokens=5000, file_size_mb=1.2,
        triage_status="OK",
    )


def _skip_triage(reason: str = "PDF_SCANNED") -> TriageResult:
    return TriageResult(
        is_scanned=True, is_encrypted=False, has_text=False,
        page_count=5, estimated_tokens=0, file_size_mb=0.8,
        triage_status="SKIP", skip_reason=reason,
    )


# ─────────────────────────────────────────────────────────────────────────────
# RawStore
# ─────────────────────────────────────────────────────────────────────────────

class TestRawStorePaths:
    def test_root_is_raw_root_slash_paper_id(self, tmp_path):
        store = RawStore("abc123", tmp_path)
        assert store.root == tmp_path / "abc123"

    def test_pdf_path(self, tmp_path):
        store = RawStore("abc123", tmp_path)
        assert store.pdf_path == store.root / "paper.pdf"

    def test_checksum_path(self, tmp_path):
        store = RawStore("abc123", tmp_path)
        assert store.checksum_path == store.root / "checksum.sha256"

    def test_metadata_path(self, tmp_path):
        store = RawStore("abc123", tmp_path)
        assert store.metadata_path == store.root / "metadata.json"

    def test_source_path(self, tmp_path):
        store = RawStore("abc123", tmp_path)
        assert store.source_path == store.root / "source.json"

    def test_log_path(self, tmp_path):
        store = RawStore("abc123", tmp_path)
        assert store.log_path == store.root / "ingestion_log.json"

    def test_root_directory_is_created(self, tmp_path):
        store = RawStore("newpaper", tmp_path)
        assert store.root.is_dir()


class TestRawStoreIsComplete:
    def test_incomplete_when_empty(self, tmp_path):
        store = RawStore("p1", tmp_path)
        assert not store.is_complete

    def test_incomplete_when_only_pdf(self, tmp_path):
        store = RawStore("p1", tmp_path)
        store.pdf_path.write_bytes(b"pdf")
        assert not store.is_complete

    def test_complete_when_all_mandatory_files_present(self, tmp_path):
        store = RawStore("p1", tmp_path)
        store.pdf_path.write_bytes(b"pdf")
        store.checksum_path.write_text("abc", encoding="utf-8")
        store.metadata_path.write_text("{}", encoding="utf-8")
        store.source_path.write_text("{}", encoding="utf-8")
        assert store.is_complete


class TestRawStoreStorePdf:
    def test_copies_pdf_to_paper_pdf(self, tmp_path):
        src = _fake_pdf(tmp_path, "src.pdf", b"hello pdf")
        store = RawStore("p1", tmp_path / "raw")
        store.store_pdf(src)
        assert store.pdf_path.read_bytes() == b"hello pdf"

    def test_returns_sha256_hex(self, tmp_path):
        content = b"hello pdf"
        src = _fake_pdf(tmp_path, "src.pdf", content)
        store = RawStore("p1", tmp_path / "raw")
        digest = store.store_pdf(src)
        assert digest == _sha256_bytes(content)

    def test_writes_checksum_file(self, tmp_path):
        content = b"hello pdf"
        src = _fake_pdf(tmp_path, "src.pdf", content)
        store = RawStore("p1", tmp_path / "raw")
        digest = store.store_pdf(src)
        assert store.checksum_path.read_text().strip() == digest

    def test_idempotent_second_call_skips_copy(self, tmp_path):
        content = b"hello pdf"
        src = _fake_pdf(tmp_path, "src.pdf", content)
        store = RawStore("p1", tmp_path / "raw")
        d1 = store.store_pdf(src)
        # Overwrite src so any re-copy would change bytes
        src.write_bytes(b"different")
        d2 = store.store_pdf(src)
        assert d1 == d2
        assert store.pdf_path.read_bytes() == content  # original kept

    def test_no_tmp_file_remains_after_write(self, tmp_path):
        src = _fake_pdf(tmp_path, "src.pdf", b"data")
        store = RawStore("p1", tmp_path / "raw")
        store.store_pdf(src)
        tmp_file = store.pdf_path.with_suffix(".pdf.tmp")
        assert not tmp_file.exists()


class TestRawStoreVerifyIntegrity:
    def test_returns_true_when_ok(self, tmp_path):
        content = b"integrity test"
        src = _fake_pdf(tmp_path, "src.pdf", content)
        store = RawStore("p1", tmp_path / "raw")
        store.store_pdf(src)
        assert store.verify_pdf_integrity() is True

    def test_returns_false_when_no_files(self, tmp_path):
        store = RawStore("p1", tmp_path)
        assert store.verify_pdf_integrity() is False

    def test_returns_false_when_pdf_tampered(self, tmp_path):
        src = _fake_pdf(tmp_path, "src.pdf", b"original")
        store = RawStore("p1", tmp_path / "raw")
        store.store_pdf(src)
        store.pdf_path.write_bytes(b"tampered")
        assert store.verify_pdf_integrity() is False


class TestRawStoreMetadataSource:
    def test_write_metadata_roundtrip(self, tmp_path):
        store = RawStore("p1", tmp_path)
        store.write_metadata({"doi": "10.1234/test", "year": 2023})
        assert store.read_metadata() == {"doi": "10.1234/test", "year": 2023}

    def test_write_source_injects_acquired_at(self, tmp_path):
        store = RawStore("p1", tmp_path)
        store.write_source({"acquisition_method": "local_copy"})
        data = store.read_source()
        assert "acquired_at" in data
        assert data["acquisition_method"] == "local_copy"

    def test_write_source_does_not_overwrite_acquired_at(self, tmp_path):
        store = RawStore("p1", tmp_path)
        store.write_source({"acquired_at": "2024-01-01T00:00:00+00:00"})
        assert store.read_source()["acquired_at"] == "2024-01-01T00:00:00+00:00"

    def test_read_metadata_returns_empty_dict_when_missing(self, tmp_path):
        store = RawStore("p1", tmp_path)
        assert store.read_metadata() == {}

    def test_read_source_returns_empty_dict_when_missing(self, tmp_path):
        store = RawStore("p1", tmp_path)
        assert store.read_source() == {}


class TestRawStoreAppendLog:
    def test_creates_log_file_on_first_call(self, tmp_path):
        store = RawStore("p1", tmp_path)
        store.append_log({"event": "start"})
        assert store.log_path.exists()

    def test_accumulates_multiple_events(self, tmp_path):
        store = RawStore("p1", tmp_path)
        store.append_log({"event": "start"})
        store.append_log({"event": "done"})
        events = json.loads(store.log_path.read_text())
        assert len(events) == 2
        assert events[0]["event"] == "start"
        assert events[1]["event"] == "done"

    def test_injects_timestamp_if_missing(self, tmp_path):
        store = RawStore("p1", tmp_path)
        store.append_log({"event": "x"})
        events = json.loads(store.log_path.read_text())
        assert "timestamp" in events[0]

    def test_preserves_existing_timestamp(self, tmp_path):
        store = RawStore("p1", tmp_path)
        store.append_log({"event": "x", "timestamp": "2024-01-01T00:00:00"})
        events = json.loads(store.log_path.read_text())
        assert events[0]["timestamp"] == "2024-01-01T00:00:00"

    def test_log_is_valid_json_list(self, tmp_path):
        store = RawStore("p1", tmp_path)
        for i in range(5):
            store.append_log({"event": f"step_{i}"})
        data = json.loads(store.log_path.read_text())
        assert isinstance(data, list)
        assert len(data) == 5

    def test_handles_corrupted_log_gracefully(self, tmp_path):
        store = RawStore("p1", tmp_path)
        store.log_path.write_text("not-valid-json", encoding="utf-8")
        store.append_log({"event": "recovery"})  # must not raise
        data = json.loads(store.log_path.read_text())
        assert data[0]["event"] == "recovery"


# ─────────────────────────────────────────────────────────────────────────────
# Stage0Ingestor — with mocked triage
# ─────────────────────────────────────────────────────────────────────────────

class TestStage0IngestorMocked:
    """Unit tests using mocked triage_pdf + sha256_pdf."""

    @pytest.fixture
    def raw_root(self, tmp_path):
        return tmp_path / "raw"

    @pytest.fixture
    def pdf(self, tmp_path):
        return _fake_pdf(tmp_path, "test.pdf", b"fake-pdf-bytes")

    def _run(self, pdf, raw_root, force=False, metadata=None, source=None):
        content = pdf.read_bytes()
        sha = _sha256_bytes(content)
        fake_hash = ContentHash(sha256=sha, pdf_path=str(pdf))

        with (
            patch("src.ingestion.stage0.ingestor.sha256_pdf", return_value=fake_hash),
            patch("src.ingestion.stage0.ingestor.triage_pdf", return_value=_ok_triage()),
        ):
            ingestor = Stage0Ingestor(raw_root=raw_root, force=force)
            return ingestor.run(pdf, metadata=metadata, source=source)

    def test_status_ok_on_first_run(self, pdf, raw_root):
        result = self._run(pdf, raw_root)
        assert result.status == "ok"

    def test_should_continue_true_on_ok(self, pdf, raw_root):
        result = self._run(pdf, raw_root)
        assert result.should_continue is True

    def test_paper_id_is_sha256(self, pdf, raw_root):
        result = self._run(pdf, raw_root)
        expected = _sha256_bytes(pdf.read_bytes())
        assert result.paper_id == expected

    def test_raw_root_is_paper_dir(self, pdf, raw_root):
        result = self._run(pdf, raw_root)
        assert result.raw_root == raw_root / result.paper_id

    def test_status_skipped_on_second_run(self, pdf, raw_root):
        self._run(pdf, raw_root)
        result = self._run(pdf, raw_root)
        assert result.status == "skipped"
        assert result.should_continue is False

    def test_force_re_ingests(self, pdf, raw_root):
        self._run(pdf, raw_root)
        result = self._run(pdf, raw_root, force=True)
        assert result.status == "ok"

    def test_metadata_written_to_json(self, pdf, raw_root):
        meta = {"doi": "10.1234/test", "title": "Test Paper", "year": 2023}
        result = self._run(pdf, raw_root, metadata=meta)
        store = RawStore(result.paper_id, raw_root)
        saved = store.read_metadata()
        assert saved["doi"] == "10.1234/test"
        assert saved["title"] == "Test Paper"

    def test_source_written_to_json(self, pdf, raw_root):
        src = {"acquisition_method": "local_copy", "source_url": None}
        result = self._run(pdf, raw_root, source=src)
        store = RawStore(result.paper_id, raw_root)
        saved = store.read_source()
        assert saved["acquisition_method"] == "local_copy"

    def test_pdf_copy_verified_in_store(self, pdf, raw_root):
        result = self._run(pdf, raw_root)
        store = RawStore(result.paper_id, raw_root)
        assert store.verify_pdf_integrity() is True

    def test_events_contain_triage_ok(self, pdf, raw_root):
        result = self._run(pdf, raw_root)
        assert any("triage_ok" in e for e in result.events)

    def test_events_contain_done(self, pdf, raw_root):
        result = self._run(pdf, raw_root)
        assert any("done:" in e for e in result.events)

    def test_ingestion_log_appended(self, pdf, raw_root):
        result = self._run(pdf, raw_root)
        store = RawStore(result.paper_id, raw_root)
        log = json.loads(store.log_path.read_text())
        assert any(e.get("event") == "stage0_complete" for e in log)


class TestStage0IngestorErrorPaths:
    @pytest.fixture
    def raw_root(self, tmp_path):
        return tmp_path / "raw"

    def test_missing_pdf_returns_error(self, tmp_path, raw_root):
        missing = tmp_path / "nonexistent.pdf"
        # sha256_pdf will raise FileNotFoundError — not mocked
        ingestor = Stage0Ingestor(raw_root=raw_root)
        result = ingestor.run(missing)
        assert result.status == "error"
        assert result.should_continue is False

    def test_triage_skip_returns_skip_triage(self, tmp_path, raw_root):
        pdf = _fake_pdf(tmp_path, "scanned.pdf", b"fake")
        sha = _sha256_bytes(b"fake")
        fake_hash = ContentHash(sha256=sha, pdf_path=str(pdf))

        with (
            patch("src.ingestion.stage0.ingestor.sha256_pdf", return_value=fake_hash),
            patch("src.ingestion.stage0.ingestor.triage_pdf", return_value=_skip_triage()),
        ):
            ingestor = Stage0Ingestor(raw_root=raw_root)
            result = ingestor.run(pdf)

        assert result.status == "skip_triage"
        assert result.should_continue is False

    def test_triage_exception_returns_error(self, tmp_path, raw_root):
        pdf = _fake_pdf(tmp_path, "bad.pdf", b"fake")
        sha = _sha256_bytes(b"fake")
        fake_hash = ContentHash(sha256=sha, pdf_path=str(pdf))

        with (
            patch("src.ingestion.stage0.ingestor.sha256_pdf", return_value=fake_hash),
            patch("src.ingestion.stage0.ingestor.triage_pdf", side_effect=RuntimeError("fitz fail")),
        ):
            ingestor = Stage0Ingestor(raw_root=raw_root)
            result = ingestor.run(pdf)

        assert result.status == "error"

    def test_result_paper_id_empty_on_sha256_failure(self, tmp_path, raw_root):
        pdf = _fake_pdf(tmp_path, "bad.pdf", b"fake")
        with patch("src.ingestion.stage0.ingestor.sha256_pdf", side_effect=OSError("disk error")):
            ingestor = Stage0Ingestor(raw_root=raw_root)
            result = ingestor.run(pdf)
        assert result.status == "error"
        assert result.paper_id == ""


class TestStage0Result:
    def test_should_continue_true_only_for_ok(self):
        for status in ("skipped", "skip_triage", "error"):
            r = Stage0Result(
                paper_id="x", status=status, checksum="c",
                raw_root=Path("/tmp"), triage=None,
            )
            assert r.should_continue is False

    def test_should_continue_true_for_ok(self):
        r = Stage0Result(
            paper_id="x", status="ok", checksum="c",
            raw_root=Path("/tmp"), triage=None,
        )
        assert r.should_continue is True
