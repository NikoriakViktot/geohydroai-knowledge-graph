"""Quantities computed here from snapshot tables, not taken from upstream.

Two things the manuscript needs that the upstream tables do not state directly:
the measurement uncertainty of a *single* overpass, and whether the pre/post
contrast survives a stricter minimum chainage span. Both are derivable from
``kakhovka_perdate_slopes_robust.csv``, which carries the per-pass Theil-Sen
interval and the span of every fit.

Everything written here records the sha256 of its input, so a derived number is
as traceable as a snapshot one: ``DERIVED_DIR/<name>.csv`` plus a manifest row
saying which snapshot file and which commit it came from. A derived table is
never edited by hand.
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from src.paper_3._utils import OUT_DIR
from src.paper_3.snapshot import SNAPSHOT_DIR

logger = logging.getLogger(__name__)

DERIVED_DIR = OUT_DIR / "derived"
MANIFEST = DERIVED_DIR / "DERIVED_MANIFEST.json"
PERDATE = SNAPSHOT_DIR / "swot" / "outputs" / "tables" / "kakhovka_perdate_slopes_robust.csv"

#: Minimum chainage spans the Methods section promises to test.
SPANS_KM = (10, 20, 30, 40)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _periods(frame: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    pre = frame[frame["period"].str.contains("PRE", na=False)]
    post = frame[frame["period"].str.contains("POST", na=False)]
    return pre, post


def per_pass_uncertainty(frame: pd.DataFrame) -> pd.DataFrame:
    """How uncertain is one overpass, as opposed to the pre/post contrast?

    The manuscript's confidence interval is on the *difference of medians* over
    overpasses. That is a different question from how well a single pass
    measures its own slope, and the second is the weaker of the two here: a fit
    through six beam-median points over ~25 km is not a precise slope estimate.
    Reporting only the first would overstate what one observation shows.
    """
    rows = []
    for label, sub in (("pre-breach", _periods(frame)[0]), ("post-breach", _periods(frame)[1])):
        half = (sub["ts_hi_cm_km"] - sub["ts_lo_cm_km"]) / 2.0
        rows.append({
            "period": label,
            "n_passes": len(sub),
            "half_width_median_cm_km": round(float(half.median()), 3),
            "half_width_p25_cm_km": round(float(half.quantile(0.25)), 3),
            "half_width_p75_cm_km": round(float(half.quantile(0.75)), 3),
            "n_interval_above_zero": int((sub["ts_lo_cm_km"] > 0).sum()),
            "n_points_median": int(sub["n_points"].median()),
        })
    return pd.DataFrame(rows)


def span_sensitivity(frame: pd.DataFrame, spans: tuple[int, ...] = SPANS_KM) -> pd.DataFrame:
    """Does the contrast survive a stricter minimum chainage span?

    A row whose ``binding`` is false means no pass was excluded at that
    threshold, so the row repeats the one above it and is not evidence of
    robustness; saying so is the point of the column.
    """
    rows = []
    shortest = float(frame["span_km"].min())
    for s in spans:
        sub = frame[frame["span_km"] >= s]
        pre, post = _periods(sub)
        if pre.empty or post.empty:
            rows.append({"min_span_km": s, "n_pre": len(pre), "n_post": len(post),
                         "median_pre_cm_km": "", "median_post_cm_km": "", "difference_cm_km": "",
                         "binding": s > shortest})
            continue
        a = float(pre["slope_theilsen_cm_km"].median())
        b = float(post["slope_theilsen_cm_km"].median())
        rows.append({
            "min_span_km": s, "n_pre": len(pre), "n_post": len(post),
            "median_pre_cm_km": round(a, 3), "median_post_cm_km": round(b, 3),
            "difference_cm_km": round(b - a, 3),
            "binding": s > shortest,
        })
    return pd.DataFrame(rows)


TABLES = {
    "slope_per_pass_uncertainty": per_pass_uncertainty,
    "slope_span_sensitivity": span_sensitivity,
}


def build() -> dict[str, Path]:
    if not PERDATE.exists():
        raise FileNotFoundError(f"snapshot input missing: {PERDATE} — run --step snapshot")
    frame = pd.read_csv(PERDATE)
    DERIVED_DIR.mkdir(parents=True, exist_ok=True)
    src_sha = _sha256(PERDATE)
    written: dict[str, Path] = {}
    entries = []
    for name, fn in TABLES.items():
        out = DERIVED_DIR / f"{name}.csv"
        table = fn(frame)
        table.to_csv(out, index=False)
        written[name] = out
        entries.append({"table": f"{name}.csv", "rows": len(table),
                        "source": str(PERDATE.relative_to(SNAPSHOT_DIR)), "source_sha256": src_sha,
                        "sha256": _sha256(out)})
        logger.info("derived %s (%d rows)", out.name, len(table))
    MANIFEST.write_text(json.dumps(
        {"built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "tables": entries},
        indent=1), encoding="utf-8")
    return written
