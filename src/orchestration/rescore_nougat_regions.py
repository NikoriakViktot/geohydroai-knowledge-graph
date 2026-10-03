"""
rescore_nougat_regions.py — apply the Nougat acceptance gate to existing regions.parquet.

regions.parquet files written before 2026-10-03 carry Nougat output with no verdict.
This script runs src.document.nougat_gate.assess_region on every row (CPU only: the
PDF text layer of the page, no model) and rewrites the file with the gate columns
(nougat_status, nougat_flags, grounding_words, grounding_numbers, confidence,
crop_strategy). Nothing else in the row changes. Consumers use only rows with
nougat_status == "accepted"; ungated files are ignored by them.

The previous file is kept as regions.parquet.pregate (once) so the change can be
reverted.

Usage:
    python -m src.orchestration.rescore_nougat_regions [--dry-run] [--limit N] [--paper ID]
"""
from __future__ import annotations

import argparse
import collections
import json
import logging
import shutil
import sys
import time
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from src.analytics.parquet_schema import REGIONS_SCHEMA
from src.document import pdf_io
from src.document.nougat_gate import assess_region, region_text

log = logging.getLogger("geohydro.rescore_nougat")

ROOT     = Path(__file__).resolve().parents[2]
SODB     = ROOT / "data" / "sodb"
REGIONS  = ROOT / "data" / "nougat_regions"
PDF_DIRS = [ROOT / "data" / "literature" / d for d in ("pdf", "pdf_oa", "pdf_missing")]
FULL_PAGE_BBOX = (0.0, 0.0, 1e5, 1e5)


def pdf_for(paper_id: str) -> Path | None:
    for d in PDF_DIRS:
        p = d / f"{paper_id}.pdf"
        if p.exists():
            return p
    return None


def crop_strategies(paper_id: str) -> dict[str, str]:
    try:
        data = json.load(open(REGIONS / paper_id / "regions.json"))
        return {e["region"]["region_id"]: e["region"].get("crop_strategy")
                for e in data.get("regions", []) if e.get("region")}
    except Exception:
        return {}


def rescore_file(path: Path, dry_run: bool = False) -> collections.Counter:
    counts: collections.Counter = collections.Counter()
    df = pd.read_parquet(path)
    if df.empty:
        counts["empty_file"] += 1
        return counts
    paper_id = str(df["paper_id"].iloc[0])
    pdf = pdf_for(paper_id)
    strategies = crop_strategies(paper_id)
    words: dict[int, list | None] = {}

    def words_of(page: int):
        if pdf is None:
            return None
        if page not in words:
            try:
                words[page] = pdf_io.extract_page_words(pdf, page)
            except Exception:
                words[page] = None
        return words[page]

    out = []
    for row in df.to_dict("records"):
        text = region_text(row)
        strategy = strategies.get(row["region_id"]) or row.get("crop_strategy")
        bbox = FULL_PAGE_BBOX if strategy == "full_page" else \
            (row["bbox_x0"], row["bbox_y0"], row["bbox_x1"], row["bbox_y1"])
        v = assess_region(text, row["region_type"], bbox, words_of(int(row["page"])))
        row.update(v.as_row())
        row["crop_strategy"] = strategy
        counts[(row["region_type"], v.status)] += 1
        out.append(row)

    if not dry_run:
        backup = path.with_suffix(".parquet.pregate")
        if not backup.exists():
            shutil.copy2(path, backup)
        names = REGIONS_SCHEMA.names
        clean = [{k: (None if isinstance(r.get(k), float) and r.get(k) != r.get(k) else r.get(k))
                  for k in names} for r in out]                       # NaN → None
        table = pa.Table.from_pylist(clean, schema=REGIONS_SCHEMA)
        tmp = path.with_suffix(".parquet.tmp")
        pq.write_table(table, tmp, compression="snappy")
        tmp.replace(path)
    return counts


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--paper")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")

    files = sorted(SODB.glob(f"{args.paper or '*'}/regions.parquet"))
    if args.limit:
        files = files[: args.limit]
    total: collections.Counter = collections.Counter()
    t0 = time.time()
    for i, f in enumerate(files, 1):
        try:
            total.update(rescore_file(f, args.dry_run))
        except Exception as exc:
            total["error"] += 1
            log.warning("%s: %s", f.parent.name, exc)
        if i % 500 == 0:
            log.info("  %d/%d files (%.0f s)", i, len(files), time.time() - t0)
    log.info("done: %d files in %.0f s, dry_run=%s", len(files), time.time() - t0, args.dry_run)
    by_type: dict = collections.defaultdict(dict)
    for k, n in total.items():
        if isinstance(k, tuple):
            by_type[k[0]][k[1]] = n
    for t, d in sorted(by_type.items()):
        log.info("  %-20s %s", t, dict(sorted(d.items())))
    log.info("  empty files: %d, errors: %d", total["empty_file"], total["error"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
