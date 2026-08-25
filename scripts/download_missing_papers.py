"""
Batch downloader for high-impact missing papers via Sci-Hub.

Reads data/analytics/missing_high_impact_references.csv (or a custom --csv)
and attempts to download PDFs for the top papers by a combined relevance score.

Usage:
    .venv/bin/python3 scripts/download_missing_papers.py \
        --top 300 \
        --min-refs 3 \
        --categories "Remote sensing" "DEM/topography" "Evaluation metrics" \
        --output-dir data/literature/pdf_missing \
        [--csv data/analytics/missing_rs_priority.csv] \
        [--delay 3.0] \
        [--skip-existing]

Note: Only download papers you are legally entitled to access in your jurisdiction.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import pandas as pd
import requests

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.analytics.missing_reference_recovery import scihub_download

CSV_PATH = PROJECT_ROOT / "data" / "analytics" / "missing_high_impact_references.csv"


def doi_to_slug(doi: str) -> str:
    return doi.lower().replace("/", "_").replace(":", "_")


def run(
    top: int,
    min_refs: int,
    output_dir: Path,
    categories: list[str] | None,
    delay: float,
    csv_path: Path,
    skip_existing: bool,
) -> None:
    df = pd.read_csv(csv_path)

    # filter by corpus frequency
    df = df[df["referenced_count"] >= min_refs].copy()

    # filter by categories (multiple allowed)
    if categories:
        cats_lower = [c.lower() for c in categories]
        df = df[df["category"].str.lower().isin(cats_lower)]

    # priority score: corpus frequency weighted higher
    if "impact_score" not in df.columns:
        df["impact_score"] = (
            df["referenced_count"] * 0.6
            + df["cited_by_count"].clip(upper=10000) / 200 * 0.4
        )
    df.sort_values("impact_score", ascending=False, inplace=True)
    df = df.head(top)

    # skip already downloaded
    if skip_existing:
        output_dir.mkdir(parents=True, exist_ok=True)
        existing_slugs = {p.stem.lower() for p in output_dir.glob("*.pdf")}
        before = len(df)
        df = df[~df["doi"].apply(doi_to_slug).isin(existing_slugs)]
        print(f"Skipping {before - len(df)} already downloaded; {len(df)} remaining")

    print(f"Papers to attempt: {len(df)}")
    print(f"Output dir: {output_dir}")
    if categories:
        print(f"Categories: {categories}")
    print()

    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0 (X11; Linux x86_64)"})

    ok = 0
    fail = 0
    for i, row in enumerate(df.itertuples(), 1):
        doi = row.doi
        title = str(row.title)[:65]
        refs = int(row.referenced_count)
        oa = int(row.cited_by_count) if hasattr(row, "cited_by_count") else 0
        print(f"[{i}/{len(df)}] corpus={refs} OA={oa:,}  {doi}  {title}")
        result = scihub_download(doi, output_dir, session=session)
        if result:
            ok += 1
        else:
            fail += 1
        time.sleep(delay)

    print(f"\nDone — {ok} downloaded, {fail} failed")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--top",          type=int,   default=1000,
                        help="top-N papers by impact score")
    parser.add_argument("--min-refs",     type=int,   default=3,
                        help="min referenced_count filter")
    parser.add_argument("--output-dir",   type=Path,
                        default=PROJECT_ROOT / "data" / "literature" / "pdf_missing",
                        help="download directory")
    parser.add_argument("--categories",   type=str,   nargs="+", default=None,
                        help="filter by one or more categories (space-separated)")
    parser.add_argument("--csv",          type=Path,  default=CSV_PATH,
                        help="input CSV (default: missing_high_impact_references.csv)")
    parser.add_argument("--delay",        type=float, default=3.0,
                        help="seconds between requests")
    parser.add_argument("--skip-existing", action="store_true", default=True,
                        help="skip DOIs already downloaded (default: True)")
    args = parser.parse_args()
    run(
        top=args.top,
        min_refs=args.min_refs,
        output_dir=args.output_dir,
        categories=args.categories,
        delay=args.delay,
        csv_path=args.csv,
        skip_existing=args.skip_existing,
    )
