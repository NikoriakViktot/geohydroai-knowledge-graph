"""Retrieval calibration — build a labelling sheet, then read the precision curve.

`DISTANCE_GATE` cannot be chosen by intuition. This module samples each thesis's
candidates in three strata — the top of the ranking, the middle, and the band just
inside the threshold — and writes a sheet for a human to mark
relevant / partial / irrelevant. Feeding the marked sheet back in produces
precision per stratum and the distance at which precision collapses.

Two halves, and both are needed:

  * **precision** (this module's sampling) catches a gate that is too loose;
  * **recall** (`retrieve.positive_control`) catches key-term families cut too
    narrow — the risk introduced by making the first family mandatory.

Usage:
    python -m src.paper_3.calibrate sample            # writes the sheet
    # … fill in the `label` column by hand …
    python -m src.paper_3.calibrate report            # precision curve
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import pandas as pd

from src.paper_3._utils import OUT_DIR
from src.paper_3.retrieve import DISTANCE_GATE
from src.paper_3.theses import Thesis, load_theses

logger = logging.getLogger(__name__)

SHEET = "CALIBRATION_SHEET.csv"
REPORT = "CALIBRATION_REPORT.md"

LABELS = ("relevant", "partial", "irrelevant")

#: Theses whose baseline on-topic count is large enough that the semantic basin
#: is probably too broad. These get sampled first.
PRIORITY = ("T04", "T08", "T15", "T20", "T22")

SHEET_COLUMNS = [
    "thesis_id", "stratum", "rank", "paper_id", "doi", "title", "year",
    "semantic_best_distance", "n_semantic_chunks", "n_keyterm_families_hit",
    "retrieval_origin", "prefilter_pass", "thesis_statement",
    "label", "note",
]


def stratified_sample(candidates: pd.DataFrame, thesis: Thesis,
                      per_stratum: int = 10) -> pd.DataFrame:
    """Top / middle / near-threshold sample for one thesis, ordered by distance.

    Near-threshold is the band immediately inside `DISTANCE_GATE` — the rows the
    gate is actually deciding. Sampling only the top would measure how good the
    best hits are, which nobody doubts.
    """
    rows = candidates[candidates["thesis_id"] == thesis.id].copy()
    if rows.empty:
        return rows.assign(stratum=pd.Series(dtype=str), rank=pd.Series(dtype=int))

    rows = rows.sort_values("semantic_best_distance",
                            na_position="last").reset_index(drop=True)
    rows["rank"] = rows.index + 1

    n = len(rows)
    top = rows.head(per_stratum).assign(stratum="top")

    mid_start = max(len(top), (n - per_stratum) // 2)
    middle = rows.iloc[mid_start:mid_start + per_stratum].assign(stratum="middle")

    near = rows[rows["semantic_best_distance"].between(
        DISTANCE_GATE - 0.10, DISTANCE_GATE, inclusive="both")]
    near = near.tail(per_stratum).assign(stratum="near_threshold")

    out = pd.concat([top, middle, near]).drop_duplicates("paper_id")
    return out


def build_sheet(out_dir: Path | None = None, theses: list[Thesis] | None = None,
                per_stratum: int = 10, only: tuple[str, ...] | None = None) -> Path:
    """Write CALIBRATION_SHEET.csv for manual labelling."""
    theses = theses if theses is not None else load_theses()
    target = Path(out_dir) if out_dir else OUT_DIR
    path = target / "thesis_candidates.parquet"
    if not path.exists():
        raise FileNotFoundError(f"{path} missing — run --step retrieve first")
    candidates = pd.read_parquet(path)

    wanted = set(only) if only else set(PRIORITY)
    chunks = []
    for t in theses:
        if wanted and t.id not in wanted:
            continue
        sample = stratified_sample(candidates, t, per_stratum)
        if sample.empty:
            logger.warning("%s: no candidates to sample", t.id)
            continue
        sample = sample.assign(
            thesis_statement=" ".join(t.statement.split()),
            label="", note="")
        chunks.append(sample)

    if not chunks:
        raise RuntimeError("nothing to sample — did retrieval produce candidates?")

    sheet = pd.concat(chunks)
    for col in SHEET_COLUMNS:
        if col not in sheet.columns:
            sheet[col] = ""
    sheet = sheet[SHEET_COLUMNS].sort_values(["thesis_id", "stratum", "rank"])

    target.mkdir(parents=True, exist_ok=True)
    sheet_path = target / SHEET
    sheet.to_csv(sheet_path, index=False)
    logger.info("Calibration sheet: %d rows across %d theses → %s",
                len(sheet), sheet["thesis_id"].nunique(), sheet_path)
    logger.info("Fill in the `label` column with one of: %s", ", ".join(LABELS))
    return sheet_path


# ── reading the labelled sheet back ───────────────────────────────────────────

def precision_curve(sheet: pd.DataFrame, bin_width: float = 0.05) -> pd.DataFrame:
    """Precision by distance bin, over labelled rows only."""
    labelled = sheet[sheet["label"].isin(LABELS)].copy()
    if labelled.empty:
        return pd.DataFrame(columns=["bin_low", "bin_high", "n", "precision",
                                     "precision_strict"])

    labelled["_bin"] = (labelled["semantic_best_distance"] / bin_width).round(0) * bin_width
    out = []
    for low, group in labelled.groupby("_bin"):
        n = len(group)
        relevant = int((group["label"] == "relevant").sum())
        partial = int((group["label"] == "partial").sum())
        out.append({
            "bin_low": round(float(low), 3),
            "bin_high": round(float(low) + bin_width, 3),
            "n": n,
            # Lenient: partial counts as a hit. Strict: only fully relevant does.
            "precision": round((relevant + partial) / n, 3),
            "precision_strict": round(relevant / n, 3),
        })
    return pd.DataFrame(out).sort_values("bin_low")


def precision_by_stratum(sheet: pd.DataFrame) -> pd.DataFrame:
    labelled = sheet[sheet["label"].isin(LABELS)]
    if labelled.empty:
        return pd.DataFrame(columns=["thesis_id", "stratum", "n", "precision"])
    rows = []
    for (tid, stratum), group in labelled.groupby(["thesis_id", "stratum"]):
        n = len(group)
        hits = int(group["label"].isin(("relevant", "partial")).sum())
        rows.append({"thesis_id": tid, "stratum": stratum, "n": n,
                     "precision": round(hits / n, 3)})
    return pd.DataFrame(rows)


def suggest_gate(curve: pd.DataFrame, min_precision: float = 0.6) -> float | None:
    """Largest distance whose bin still meets `min_precision`, or None.

    A suggestion, not a decision: the bins are small and a single mislabelled row
    moves them. Read the curve before accepting the number.
    """
    if curve.empty:
        return None
    ok = curve[curve["precision"] >= min_precision]
    return float(ok["bin_high"].max()) if not ok.empty else None


def report(out_dir: Path | None = None, min_precision: float = 0.6) -> Path:
    """Read the labelled sheet and write CALIBRATION_REPORT.md."""
    target = Path(out_dir) if out_dir else OUT_DIR
    sheet_path = target / SHEET
    if not sheet_path.exists():
        raise FileNotFoundError(f"{sheet_path} missing — run `sample` first")
    sheet = pd.read_csv(sheet_path).fillna({"label": "", "note": ""})

    labelled = sheet[sheet["label"].isin(LABELS)]
    curve = precision_curve(sheet)
    strata = precision_by_stratum(sheet)
    suggested = suggest_gate(curve, min_precision)

    lines = [
        "# Retrieval calibration — Paper 3",
        "",
        f"- rows in sheet: **{len(sheet)}**",
        f"- rows labelled: **{len(labelled)}**",
        f"- current `DISTANCE_GATE`: **{DISTANCE_GATE}**",
        f"- suggested gate at precision ≥ {min_precision}: "
        f"**{suggested if suggested is not None else '— (no bin qualifies)'}**",
        "",
    ]
    if len(labelled) < 30:
        lines += [
            "> **Not enough labelled rows to calibrate.** Label at least 30 — "
            "ideally 30 per thesis across the three strata — before changing the "
            "gate. A gate tuned on a handful of rows decides the results by "
            "accident.",
            "",
        ]

    lines += ["## Precision by distance bin", "",
              "Lenient counts `partial` as a hit; strict counts only `relevant`.", "",
              "| distance | n | precision | strict |", "|---|---:|---:|---:|"]
    for r in curve.itertuples():
        lines.append(f"| {r.bin_low:.2f}–{r.bin_high:.2f} | {r.n} | "
                     f"{r.precision:.2f} | {r.precision_strict:.2f} |")

    lines += ["", "## Precision by thesis and stratum", "",
              "| thesis | stratum | n | precision |", "|---|---|---:|---:|"]
    for r in strata.sort_values(["thesis_id", "stratum"]).itertuples():
        lines.append(f"| {r.thesis_id} | {r.stratum} | {r.n} | {r.precision:.2f} |")

    weak = strata[(strata["stratum"] == "top") & (strata["precision"] < 0.7)]
    if not weak.empty:
        lines += ["", "## Theses whose best hits are already weak", "",
                  "Low precision in the **top** stratum is not a gate problem — it "
                  "means the key-term families or the search queries are wrong for "
                  "these theses. Fix `theses.yaml` before touching the gate.", ""]
        for r in weak.itertuples():
            lines.append(f"- **{r.thesis_id}** — top-stratum precision "
                         f"{r.precision:.2f} over {r.n} labelled rows")

    path = target / REPORT
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    logger.info("Calibration report → %s", path)
    return path


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)-8s %(message)s")
    parser = argparse.ArgumentParser(description="Retrieval calibration for paper_3")
    parser.add_argument("action", choices=("sample", "report"))
    parser.add_argument("--out", default=None)
    parser.add_argument("--per-stratum", type=int, default=10)
    parser.add_argument("--thesis", action="append",
                        help="limit to these thesis ids (default: the priority set)")
    parser.add_argument("--min-precision", type=float, default=0.6)
    args = parser.parse_args(argv)

    out_dir = Path(args.out) if args.out else OUT_DIR
    if args.action == "sample":
        build_sheet(out_dir, per_stratum=args.per_stratum,
                    only=tuple(args.thesis) if args.thesis else None)
    else:
        report(out_dir, min_precision=args.min_precision)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
