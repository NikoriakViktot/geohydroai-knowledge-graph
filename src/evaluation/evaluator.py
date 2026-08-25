"""
evaluator.py — Field-level precision/recall evaluation against a goldset.

Metrics per field:
  DOI              — exact match rate
  Sensor_Type      — exact match accuracy (SAR / Optical / Multi-sensor)
  Satellite_Names  — token-level F1 (comma-split, lowercased set overlap)
  Country          — token-level F1
  Methods          — token-level F1 (canonical names, case-insensitive)
  OA/F1/IoU/Kappa  — tolerance match |pred - true| ≤ 0.02  → TP
                     null pred + non-null true → FN
  Near_Real_Time   — 3-class accuracy: true / false / unclear

Usage
-----
python -m src.evaluation.evaluator \
    --goldset data/evaluation/goldset.csv \
    --pipeline-csv outputs/flood_papers_extracted.csv \
    --output data/evaluation/eval_report.txt
"""
from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

# ── Field evaluation result ───────────────────────────────────────────────────


@dataclass
class FieldEval:
    """Per-field evaluation metrics."""
    field:     str
    metric:    str        # "exact_match" | "token_f1" | "tolerance_match" | "3class_accuracy"
    precision: float | None = None   # TP / (TP + FP) — for token_f1
    recall:    float | None = None   # TP / (TP + FN) — for token_f1
    accuracy:  float | None = None   # exact match rate
    f1:        float | None = None   # 2 * P * R / (P + R) for token-level
    coverage:  float = 0.0           # % of predictions that are non-null
    n:         int   = 0             # goldset rows evaluated
    tp:        int   = 0
    fp:        int   = 0
    fn:        int   = 0
    notes:     str   = ""

    def summary_line(self) -> str:
        parts = [f"{self.field:<22} n={self.n:<4} cov={self.coverage:.0%}"]
        if self.accuracy is not None:
            parts.append(f"acc={self.accuracy:.3f}")
        if self.precision is not None:
            parts.append(f"P={self.precision:.3f}")
        if self.recall is not None:
            parts.append(f"R={self.recall:.3f}")
        if self.f1 is not None:
            parts.append(f"F1={self.f1:.3f}")
        if self.notes:
            parts.append(f"({self.notes})")
        return "  ".join(parts)


# ── Token helpers ─────────────────────────────────────────────────────────────


def _tokens(s: object) -> set[str]:
    """Split comma/semicolon-separated string into lowercase token set."""
    if not s or not isinstance(s, str) or str(s).strip().lower() in ("", "nan", "none"):
        return set()
    return {t.strip().lower() for t in str(s).replace(";", ",").split(",") if t.strip()}


def _token_f1(pred: object, true: object) -> tuple[float, float, float]:
    """Return (precision, recall, f1) for token-level set overlap."""
    p_set = _tokens(pred)
    t_set = _tokens(true)
    if not t_set and not p_set:
        return 1.0, 1.0, 1.0    # both empty → perfect match
    if not t_set:
        return 0.0, 1.0, 0.0    # nothing to match
    if not p_set:
        return 1.0, 0.0, 0.0    # missed everything
    tp = len(p_set & t_set)
    prec = tp / len(p_set)
    rec  = tp / len(t_set)
    f1   = 2 * prec * rec / (prec + rec) if (prec + rec) > 0 else 0.0
    return prec, rec, f1


def _is_null(v: object) -> bool:
    if v is None:
        return True
    if isinstance(v, float) and pd.isna(v):
        return True
    s = str(v).strip().lower()
    return s in ("", "nan", "none", "null")


# ── Field evaluators ──────────────────────────────────────────────────────────


def _eval_exact(field_name: str, goldset: pd.DataFrame,
                true_col: str, pred_col: str) -> FieldEval:
    rows = goldset[[true_col, pred_col]].dropna(subset=[true_col])
    n = len(rows)
    if n == 0:
        return FieldEval(field=field_name, metric="exact_match", n=0, notes="no ground truth")

    coverage = rows[pred_col].apply(lambda v: not _is_null(v)).mean()
    matches  = rows.apply(
        lambda r: str(r[pred_col]).strip().lower() == str(r[true_col]).strip().lower(),
        axis=1,
    )
    accuracy = matches.sum() / n
    return FieldEval(
        field=field_name, metric="exact_match",
        accuracy=round(accuracy, 4), coverage=round(coverage, 4), n=n,
    )


def _eval_token_f1(field_name: str, goldset: pd.DataFrame,
                   true_col: str, pred_col: str) -> FieldEval:
    rows = goldset[[true_col, pred_col]].dropna(subset=[true_col])
    rows = rows[rows[true_col].apply(lambda v: not _is_null(v))]
    n = len(rows)
    if n == 0:
        return FieldEval(field=field_name, metric="token_f1", n=0, notes="no ground truth")

    coverage = rows[pred_col].apply(lambda v: not _is_null(v)).mean()
    precs, recs, f1s = [], [], []
    tp_total = fp_total = fn_total = 0
    for _, row in rows.iterrows():
        p_set = _tokens(row[pred_col])
        t_set = _tokens(row[true_col])
        tp = len(p_set & t_set)
        fp = len(p_set - t_set)
        fn = len(t_set - p_set)
        tp_total += tp; fp_total += fp; fn_total += fn
        prec = tp / len(p_set) if p_set else 0.0
        rec  = tp / len(t_set) if t_set else 0.0
        f1   = 2 * prec * rec / (prec + rec) if (prec + rec) > 0 else 0.0
        precs.append(prec); recs.append(rec); f1s.append(f1)

    # Macro averages
    macro_p  = sum(precs) / n
    macro_r  = sum(recs)  / n
    macro_f1 = sum(f1s)   / n
    return FieldEval(
        field=field_name, metric="token_f1",
        precision=round(macro_p,  4),
        recall   =round(macro_r,  4),
        f1       =round(macro_f1, 4),
        coverage =round(coverage, 4),
        n=n, tp=tp_total, fp=fp_total, fn=fn_total,
    )


def _eval_tolerance(field_name: str, goldset: pd.DataFrame,
                    true_col: str, pred_col: str,
                    tol: float = 0.02) -> FieldEval:
    rows = goldset[[true_col, pred_col]].dropna(subset=[true_col])
    rows = rows[rows[true_col].apply(lambda v: not _is_null(v))]
    n = len(rows)
    if n == 0:
        return FieldEval(field=field_name, metric="tolerance_match", n=0, notes="no ground truth")

    tp = fn = 0
    n_pred = 0
    for _, row in rows.iterrows():
        true_val = row[true_col]
        pred_val = row[pred_col]
        try:
            true_f = float(true_val)
        except (TypeError, ValueError):
            continue
        if _is_null(pred_val):
            fn += 1
        else:
            n_pred += 1
            try:
                pred_f = float(pred_val)
                if abs(pred_f - true_f) <= tol:
                    tp += 1
                else:
                    fn += 1
            except (TypeError, ValueError):
                fn += 1

    coverage = n_pred / n if n > 0 else 0.0
    accuracy = tp / n if n > 0 else 0.0
    recall   = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    return FieldEval(
        field=field_name, metric="tolerance_match",
        accuracy=round(accuracy, 4), recall=round(recall, 4),
        coverage=round(coverage, 4), n=n, tp=tp, fn=fn,
        notes=f"tol=±{tol}",
    )


def _eval_nrt(goldset: pd.DataFrame, true_col: str, pred_col: str) -> FieldEval:
    """3-class accuracy for Near_Real_Time: true / false / unclear."""
    rows = goldset[[true_col, pred_col]].dropna(subset=[true_col])
    rows = rows[rows[true_col].apply(lambda v: not _is_null(v))]
    n = len(rows)
    if n == 0:
        return FieldEval(field="Near_Real_Time", metric="3class_accuracy", n=0,
                         notes="no ground truth")

    coverage = rows[pred_col].apply(lambda v: not _is_null(v)).mean()
    matches = rows.apply(
        lambda r: str(r[pred_col]).strip().lower() == str(r[true_col]).strip().lower(),
        axis=1,
    )
    accuracy = matches.sum() / n
    return FieldEval(
        field="Near_Real_Time", metric="3class_accuracy",
        accuracy=round(accuracy, 4), coverage=round(coverage, 4), n=n,
        notes="true/false/unclear",
    )


# ── Main evaluate function ────────────────────────────────────────────────────


def evaluate(
    goldset_csv: Path,
    pipeline_df: pd.DataFrame,
) -> dict[str, FieldEval]:
    """
    Evaluate pipeline predictions against a manual goldset.

    The goldset must have columns: paper_id, true_*, pred_* or pipeline columns.
    If pred_* columns are missing, they are joined from pipeline_df on Source_File.

    Returns
    -------
    dict[field_name → FieldEval]
    """
    goldset = pd.read_csv(goldset_csv)

    # If goldset has pred_* columns, use them directly.
    # Otherwise, join from pipeline_df on paper_id == Source_File.
    has_pred = any(c.startswith("pred_") for c in goldset.columns)
    if not has_pred:
        logger.info("Joining pipeline predictions to goldset on paper_id/Source_File")
        pipeline_df = pipeline_df.rename(columns={"Source_File": "paper_id"})
        goldset = goldset.merge(pipeline_df, on="paper_id", how="left", suffixes=("", "_pipe"))
        # Rename pipeline columns to pred_*
        for src_col, pred_col in [
            ("Sensor_Type", "pred_sensor_type"),
            ("Satellite_Names", "pred_satellite_names"),
            ("Country", "pred_country"),
            ("Methods", "pred_methods"),
            ("OA", "pred_oa"), ("F1", "pred_f1"),
            ("IoU", "pred_iou"), ("Kappa", "pred_kappa"),
            ("Near_Real_Time_Label", "pred_near_real_time_label"),
            ("Confidence", "pred_confidence"),
        ]:
            if src_col in goldset.columns and pred_col not in goldset.columns:
                goldset[pred_col] = goldset[src_col]

    results: dict[str, FieldEval] = {}

    # ── Exact match fields ────────────────────────────────────────────────────
    results["Sensor_Type"] = _eval_exact(
        "Sensor_Type", goldset, "true_sensor_type", "pred_sensor_type",
    )

    # ── Token-F1 fields ───────────────────────────────────────────────────────
    for field_name, true_col, pred_col in [
        ("Satellite_Names", "true_satellite_names", "pred_satellite_names"),
        ("Country",         "true_country",         "pred_country"),
        ("Methods",         "true_methods",         "pred_methods"),
    ]:
        results[field_name] = _eval_token_f1(field_name, goldset, true_col, pred_col)

    # ── Tolerance match (numeric metrics) ────────────────────────────────────
    for field_name, true_col, pred_col in [
        ("OA",    "true_oa",    "pred_oa"),
        ("F1",    "true_f1",    "pred_f1"),
        ("IoU",   "true_iou",   "pred_iou"),
        ("Kappa", "true_kappa", "pred_kappa"),
    ]:
        results[field_name] = _eval_tolerance(field_name, goldset, true_col, pred_col)

    # ── NRT 3-class ───────────────────────────────────────────────────────────
    true_col  = "true_near_real_time"
    pred_col  = "pred_near_real_time_label" if "pred_near_real_time_label" in goldset.columns \
                else "pred_near_real_time"
    results["Near_Real_Time"] = _eval_nrt(goldset, true_col, pred_col)

    return results


# ── Report writer ─────────────────────────────────────────────────────────────


def write_report(
    evals: dict[str, FieldEval],
    output_path: Path | None = None,
) -> str:
    """Format evaluation results as a plain-text report."""
    lines = [
        "=" * 65,
        "  RAG Pipeline — Field-Level Evaluation Report",
        "=" * 65,
        "",
    ]

    # Group by metric type
    groups = {
        "Exact Match":         ["Sensor_Type"],
        "Token F1 (set)":      ["Satellite_Names", "Country", "Methods"],
        "Tolerance (±0.02)":   ["OA", "F1", "IoU", "Kappa"],
        "3-Class Accuracy":    ["Near_Real_Time"],
    }

    for group_name, fields in groups.items():
        lines.append(f"── {group_name} " + "─" * (50 - len(group_name)))
        for f in fields:
            ev = evals.get(f)
            if ev:
                lines.append("  " + ev.summary_line())
        lines.append("")

    # Summary table
    lines.append("── Summary " + "─" * 54)
    header = f"  {'Field':<22}  {'n':>4}  {'Coverage':>8}  {'Score':>8}"
    lines.append(header)
    lines.append("  " + "-" * 50)
    for f, ev in evals.items():
        score = ev.f1 if ev.f1 is not None else ev.accuracy
        score_str = f"{score:.3f}" if score is not None else "  n/a"
        cov_str   = f"{ev.coverage:.0%}" if ev.n > 0 else "  n/a"
        lines.append(f"  {ev.field:<22}  {ev.n:>4}  {cov_str:>8}  {score_str:>8}")
    lines.append("=" * 65)

    report = "\n".join(lines)
    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(report, encoding="utf-8")
        logger.info("Evaluation report saved → %s", output_path)

    return report


# ── CLI ───────────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Evaluate pipeline output against a manual goldset",
    )
    parser.add_argument(
        "--goldset",
        required=True,
        help="Path to the manually annotated goldset CSV",
    )
    parser.add_argument(
        "--pipeline-csv",
        default="",
        help="Path to the pipeline output CSV (used only if goldset lacks pred_* columns)",
    )
    parser.add_argument(
        "--output",
        default="data/evaluation/eval_report.txt",
        help="Output path for the evaluation report",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    goldset_path = Path(args.goldset)
    if not goldset_path.exists():
        logger.error("Goldset CSV not found: %s", goldset_path)
        sys.exit(1)

    pipeline_df: pd.DataFrame | None = None
    if args.pipeline_csv:
        p = Path(args.pipeline_csv)
        if p.exists():
            pipeline_df = pd.read_csv(p)
        else:
            logger.warning("Pipeline CSV not found: %s — will use goldset pred_* columns", p)

    evals = evaluate(goldset_path, pipeline_df if pipeline_df is not None else pd.DataFrame())
    report = write_report(evals, output_path=Path(args.output))
    print(report)


if __name__ == "__main__":
    main()
