"""
SODB Diagnostics — classify per-paper parquet health across data/sodb/.

Output: data/analytics/sodb_diagnostics.parquet + printed summary.

For each paper and each artifact type (tables, formulas, regions, numeric_facts):
  - exists, row_count, schema_valid, status, error

Status values:
  OK            — exists, row_count > 0
  EMPTY         — exists, row_count == 0
  MISSING       — file does not exist
  CORRUPT       — file exists but cannot be read

Usage:
    python -m src.analytics.sodb_diagnostics
    python -m src.analytics.sodb_diagnostics --sodb-root data/sodb --output data/analytics/sodb_diagnostics.parquet
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq

SODB_ROOT   = Path("data/sodb")
NOUGAT_DIR  = Path("data/nougat_regions")
OUTPUT      = Path("data/analytics/sodb_diagnostics.parquet")
ARTIFACTS   = ["tables.parquet", "formulas.parquet", "regions.parquet", "numeric_facts.parquet"]


def classify(path: Path) -> dict:
    if not path.exists():
        return {"exists": False, "row_count": 0, "schema_valid": True, "status": "MISSING", "error": None}
    try:
        pf = pq.read_table(path)
        n  = pf.num_rows
        status = "OK" if n > 0 else "EMPTY"
        return {"exists": True, "row_count": n, "schema_valid": True, "status": status, "error": None}
    except Exception as e:
        return {"exists": True, "row_count": 0, "schema_valid": False, "status": "CORRUPT", "error": str(e)[:200]}


def main(sodb_root: Path = SODB_ROOT, output: Path = OUTPUT) -> None:
    rows = []
    paper_dirs = sorted(d for d in sodb_root.iterdir() if d.is_dir())
    print(f"Scanning {len(paper_dirs)} paper dirs in {sodb_root} …")

    for paper_dir in paper_dirs:
        paper_id = paper_dir.name
        for artifact in ARTIFACTS:
            c = classify(paper_dir / artifact)
            rows.append({"paper_id": paper_id, "artifact": artifact, **c})

    df = pd.DataFrame(rows)
    output.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(output, index=False)
    print(f"Written: {output}  ({len(df):,} rows)\n")

    # ── Summary by artifact ───────────────────────────────────────────────────
    print("=== SUMMARY BY ARTIFACT ===")
    summary = df.groupby(["artifact", "status"]).size().unstack(fill_value=0)
    print(summary.to_string())

    # ── Papers with ≥1 OK artifact ────────────────────────────────────────────
    ok_df      = df[df["status"] == "OK"]
    ok_counts  = ok_df.groupby("paper_id")["artifact"].count()
    print(f"\n=== PAPER-LEVEL OK COVERAGE ===")
    print(f"Papers with ≥1 artifact OK : {(ok_counts >= 1).sum():,}")
    print(f"Papers with ≥2 artifacts OK: {(ok_counts >= 2).sum():,}")
    print(f"Papers with all 4 OK        : {(ok_counts == 4).sum():,}")

    # Per-artifact OK counts
    for art in ARTIFACTS:
        n_ok = (df[df["artifact"] == art]["status"] == "OK").sum()
        total = len(paper_dirs)
        print(f"  {art:<30s}: {n_ok:>5,} OK  /  {total:,}  ({n_ok/total*100:.1f}%)")

    # ── Nougat cross-check ────────────────────────────────────────────────────
    print(f"\n=== NOUGAT REGION CROSS-CHECK ===")
    if NOUGAT_DIR.exists():
        nougat_papers = {d.name for d in NOUGAT_DIR.iterdir() if d.is_dir()}
        print(f"data/nougat_regions/ dirs : {len(nougat_papers):,}")
        regions_ok = set(ok_df[ok_df["artifact"] == "regions.parquet"]["paper_id"])
        print(f"regions.parquet OK in sodb: {len(regions_ok):,}")
        overlap = nougat_papers & regions_ok
        print(f"Overlap (both OK)          : {len(overlap):,}")
        nougat_missing_in_sodb = nougat_papers - regions_ok
        print(f"In nougat_regions but sodb EMPTY/MISSING: {len(nougat_missing_in_sodb):,}")
        if nougat_missing_in_sodb:
            print("  Sample:", list(nougat_missing_in_sodb)[:5])
    else:
        print(f"  {NOUGAT_DIR} not found — skipping")

    # ── Papers needing re-extraction ──────────────────────────────────────────
    print("\n=== PAPERS NEEDING WORK (tables.parquet MISSING or EMPTY) ===")
    needs = df[(df["artifact"] == "tables.parquet") & (df["status"].isin(["MISSING", "EMPTY"]))]
    print(f"Count: {len(needs):,}")
    needs_path = output.parent / "papers_needing_extraction.txt"
    needs["paper_id"].to_csv(needs_path, index=False, header=False)
    print(f"Paper IDs written to: {needs_path}")

    print("\n=== CORRUPT FILES ===")
    corrupt = df[df["status"] == "CORRUPT"]
    if corrupt.empty:
        print("None found.")
    else:
        print(corrupt[["paper_id", "artifact", "error"]].to_string(index=False))


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="SODB parquet health diagnostics")
    ap.add_argument("--sodb-root", type=Path, default=SODB_ROOT)
    ap.add_argument("--output",    type=Path, default=OUTPUT)
    args = ap.parse_args()
    main(args.sodb_root, args.output)
