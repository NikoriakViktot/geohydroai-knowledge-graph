"""
observability.py — Structured ingestion observability.

Writes two parallel logs per pipeline run:
  1. JSONL  — one JSON object per PDF, machine-readable, queryable with jq/DuckDB
  2. CSV    — one row per PDF, spreadsheet-compatible summary

Also tracks in-memory counters for live tqdm postfix and end-of-run summary.
"""

from __future__ import annotations

import csv
import json
import logging
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from src.ingestion.models import IngestionRecord

log = logging.getLogger(__name__)

_CSV_HEADER = [
    "sha256", "pdf_name", "status", "triage_status",
    "pages", "tokens", "size_mb", "is_scanned",
    "has_title", "has_abstract", "has_body", "has_refs",
    "has_coords", "ref_count", "sentence_count",
    "elapsed_sec", "attempts", "failure_detail", "processed_at",
]


@dataclass
class RunMetrics:
    """Live counters updated after every PDF."""
    success:  int = 0
    partial:  int = 0
    fail:     int = 0
    skipped:  int = 0
    total:    int = 0
    total_elapsed: float = 0.0
    start_time: float = 0.0

    def throughput_per_min(self) -> float:
        elapsed = time.perf_counter() - self.start_time
        processed = self.success + self.partial + self.fail
        return processed / elapsed * 60 if elapsed > 0 and processed > 0 else 0.0

    def postfix(self) -> dict:
        return {
            "ok":   self.success,
            "part": self.partial,
            "fail": self.fail,
            "skip": self.skipped,
        }


class IngestionObserver:
    """
    Thread-safe (single-threaded use) structured logger for ingestion events.

    Appends to JSONL and CSV on every record; counters are in-memory only.
    """

    def __init__(self, run_dir: Path) -> None:
        run_dir.mkdir(parents=True, exist_ok=True)
        self._jsonl_path = run_dir / "ingestion.jsonl"
        self._csv_path   = run_dir / "ingestion.csv"
        self._metrics    = RunMetrics(start_time=time.perf_counter())
        self._failure_counter: Counter[str] = Counter()

        # Write CSV header if file is new
        if not self._csv_path.exists():
            with open(self._csv_path, "w", newline="", encoding="utf-8") as fh:
                csv.writer(fh).writerow(_CSV_HEADER)

    # ── Record one PDF outcome ────────────────────────────────────────────────

    def record(self, rec: IngestionRecord) -> None:
        self._write_jsonl(rec)
        self._write_csv(rec)
        self._update_metrics(rec)
        log.info(
            "%s | %s | %.1fs | attempts=%d | refs=%d | sents=%d",
            rec.ingestion_status,
            Path(rec.pdf_path).name,
            rec.elapsed_sec,
            rec.attempts,
            rec.tei_quality.ref_count    if rec.tei_quality else 0,
            rec.tei_quality.sentence_count if rec.tei_quality else 0,
        )

    @property
    def metrics(self) -> RunMetrics:
        return self._metrics

    def failure_summary(self) -> dict[str, int]:
        return dict(self._failure_counter.most_common())

    # ── JSONL write ───────────────────────────────────────────────────────────

    def _write_jsonl(self, rec: IngestionRecord) -> None:
        line = json.dumps(rec.to_dict(), ensure_ascii=False)
        with open(self._jsonl_path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    # ── CSV write ─────────────────────────────────────────────────────────────

    def _write_csv(self, rec: IngestionRecord) -> None:
        q = rec.tei_quality
        row = [
            rec.sha256[:16],                       # prefix for readability
            Path(rec.pdf_path).name,
            rec.ingestion_status,
            rec.triage_status,
            rec.page_count,
            rec.estimated_tokens,
            f"{rec.file_size_mb:.1f}",
            rec.is_scanned,
            q.has_title       if q else "",
            q.has_abstract    if q else "",
            q.has_body        if q else "",
            q.has_references  if q else "",
            q.has_coordinates if q else "",
            q.ref_count       if q else "",
            q.sentence_count  if q else "",
            f"{rec.elapsed_sec:.2f}",
            rec.attempts,
            rec.failure_detail[:120],
            rec.processed_at,
        ]
        with open(self._csv_path, "a", newline="", encoding="utf-8") as fh:
            csv.writer(fh).writerow(row)

    # ── Metrics ───────────────────────────────────────────────────────────────

    def _update_metrics(self, rec: IngestionRecord) -> None:
        m = self._metrics
        m.total += 1
        m.total_elapsed += rec.elapsed_sec

        status = rec.ingestion_status
        if status == "SUCCESS":
            m.success += 1
        elif status == "PARTIAL":
            m.partial += 1
        elif status in ("SKIPPED", "PDF_ENCRYPTED", "PDF_SCANNED",
                        "PDF_NO_TEXT", "PDF_TOO_MANY_PAGES", "HTTP_204",
                        "NO_BLOCKS", "TOO_MANY_BLOCKS", "TOO_MANY_TOKENS"):
            m.skipped += 1
        else:
            m.fail += 1
            self._failure_counter[status] += 1

    # ── End-of-run summary ────────────────────────────────────────────────────

    def print_summary(self) -> None:
        m = self._metrics
        processed = m.success + m.partial + m.fail
        wall      = time.perf_counter() - m.start_time
        avg_rt    = m.total_elapsed / processed if processed else 0.0
        bar       = "─" * 56

        print(f"\n{bar}")
        print(f"  GROBID INGESTION COMPLETE")
        print(bar)
        print(f"  {'Total':<22}: {m.total}")
        print(f"  {'SUCCESS':<22}: {m.success}")
        print(f"  {'PARTIAL':<22}: {m.partial}")
        print(f"  {'FAIL':<22}: {m.fail}")
        print(f"  {'SKIPPED':<22}: {m.skipped}")
        print(f"  {bar[2:]}")
        print(f"  {'Wall time':<22}: {wall:.1f}s")
        print(f"  {'Avg time/PDF':<22}: {avg_rt:.1f}s")
        print(f"  {'Throughput':<22}: {m.throughput_per_min():.1f} PDFs/min")
        if self._failure_counter:
            print(f"  {bar[2:]}")
            print(f"  Failure breakdown:")
            for ft, n in self._failure_counter.most_common():
                print(f"    {ft:<30}: {n}")
        print(f"{bar}\n")
