"""
goldset_builder.py — Generate a goldset template CSV for manual annotation.

Takes the top-N papers from a recent pipeline output CSV (or from live
retrieval) and emits a template where:
  - pred_*  columns are filled from the pipeline output
  - true_*  columns are left blank for manual annotation

Usage
-----
# From an existing pipeline CSV:
python -m src.evaluation.goldset_builder \
    --pipeline-csv outputs/flood_papers_extracted.csv \
    --output data/evaluation/goldset_template.csv \
    --n 20

# With a custom paper list:
python -m src.evaluation.goldset_builder \
    --pipeline-csv outputs/flood_papers_extracted.csv \
    --papers "paper_a.pdf,paper_b.pdf" \
    --output data/evaluation/goldset_template.csv
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

# ── Column schema ─────────────────────────────────────────────────────────────

GOLDSET_COLUMNS = [
    # Identity
    "paper_id",
    "title",

    # ── Ground truth (fill manually) ────────────────────────────────────────
    "true_sensor_type",        # SAR | Optical | Multi-sensor | Other
    "true_satellite_names",    # comma-separated
    "true_country",            # comma-separated for multi-country studies
    "true_methods",            # comma-separated canonical names
    "true_oa",                 # float 0–1 | empty
    "true_f1",                 # float 0–1 | empty
    "true_iou",                # float 0–1 | empty
    "true_kappa",              # float -1..1 | empty (Фаза 2.1: від'ємні валідні)
    "true_near_real_time",     # true | false | unclear

    # ── Pipeline predictions (auto-filled) ──────────────────────────────────
    "pred_sensor_type",
    "pred_satellite_names",
    "pred_country",
    "pred_methods",
    "pred_oa",
    "pred_f1",
    "pred_iou",
    "pred_kappa",
    "pred_near_real_time",
    "pred_near_real_time_label",
    "pred_confidence",
    "pred_extractor_mode",
    "pred_evidence_score",
    "pred_quality_score",
    "pred_provenance",         # compact: "OA@results[llm]; F1@table[sodb_parquet]"

    # ── Annotator notes ──────────────────────────────────────────────────────
    "annotation_notes",
]

# ── Mapping: pipeline CSV column → pred_* field ──────────────────────────────

_PRED_MAP = {
    "Sensor_Type":            "pred_sensor_type",
    "Satellite_Names":        "pred_satellite_names",
    "Country":                "pred_country",
    "Methods":                "pred_methods",
    "OA":                     "pred_oa",
    "F1":                     "pred_f1",
    "IoU":                    "pred_iou",
    "Kappa":                  "pred_kappa",
    "Near_Real_Time":         "pred_near_real_time",
    "Near_Real_Time_Label":   "pred_near_real_time_label",
    "Confidence":             "pred_confidence",
    "Extractor_Mode":         "pred_extractor_mode",
    "Evidence_Score":         "pred_evidence_score",
    "Quality_Score":          "pred_quality_score",
    "Provenance_JSON":        "pred_provenance",
}


def build_goldset_template(
    pipeline_df: pd.DataFrame,
    n: int = 20,
    paper_ids: list[str] | None = None,
) -> pd.DataFrame:
    """
    Build a goldset template from a pipeline DataFrame.

    Parameters
    ----------
    pipeline_df:
        Output of RAGPipeline.query() — must have Source_File, Title, and
        the standard pipeline columns.
    n:
        Number of papers to include (top-N by Confidence, then Extraction_Score).
    paper_ids:
        If provided, use these Source_File values instead of top-N selection.

    Returns
    -------
    DataFrame with GOLDSET_COLUMNS; true_* columns are blank.
    """
    df = pipeline_df.copy()

    if paper_ids:
        df = df[df["Source_File"].isin(paper_ids)]
        if df.empty:
            logger.warning("None of the requested paper_ids found in pipeline output.")
    else:
        sort_cols = [c for c in ("Confidence", "Extraction_Score") if c in df.columns]
        if sort_cols:
            df = df.sort_values(sort_cols, ascending=False)
        df = df.head(n)

    rows = []
    for _, row in df.iterrows():
        rec: dict[str, object] = {}

        # Identity
        rec["paper_id"] = row.get("Source_File", "")
        rec["title"]    = row.get("Title", "")

        # Blank ground truth
        for col in GOLDSET_COLUMNS:
            if col.startswith("true_") or col == "annotation_notes":
                rec[col] = ""

        # Auto-fill predictions
        for src_col, pred_col in _PRED_MAP.items():
            rec[pred_col] = row.get(src_col, "")

        rows.append(rec)

    out = pd.DataFrame(rows, columns=GOLDSET_COLUMNS)
    return out


# ── CLI ───────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Generate goldset template CSV from pipeline output",
    )
    parser.add_argument(
        "--pipeline-csv",
        required=True,
        help="Path to the pipeline output CSV (flood_papers_extracted.csv)",
    )
    parser.add_argument(
        "--output",
        default="data/evaluation/goldset_template.csv",
        help="Output path for the template CSV",
    )
    parser.add_argument(
        "--n",
        type=int,
        default=20,
        help="Number of papers to include (default: 20)",
    )
    parser.add_argument(
        "--papers",
        default="",
        help="Comma-separated list of Source_File values to include (overrides --n)",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    pipeline_csv = Path(args.pipeline_csv)
    if not pipeline_csv.exists():
        logger.error("Pipeline CSV not found: %s", pipeline_csv)
        sys.exit(1)

    logger.info("Loading pipeline CSV: %s", pipeline_csv)
    df = pd.read_csv(pipeline_csv)
    logger.info("  %d rows loaded", len(df))

    paper_ids = [p.strip() for p in args.papers.split(",") if p.strip()] or None

    goldset = build_goldset_template(df, n=args.n, paper_ids=paper_ids)

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    goldset.to_csv(out_path, index=False)

    logger.info("Goldset template saved → %s (%d rows)", out_path, len(goldset))
    print(f"\nNext step: open {out_path} in Excel/LibreOffice, fill the true_* columns,")
    print("and save as goldset.csv in the same directory.")


if __name__ == "__main__":
    main()
