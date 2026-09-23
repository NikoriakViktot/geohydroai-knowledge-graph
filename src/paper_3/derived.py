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
ICESAT_TABLES = SNAPSHOT_DIR / "icesat" / "outputs" / "tables"

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


def gauge_icesat_agreement(_frame: pd.DataFrame | None = None) -> pd.DataFrame:
    """Does the satellite confirm the official transformation, station by station?

    The manuscript states that the BS-77 to EVRF2019 offset varies along the
    reach. That is a property of the official grid. Whether ICESat-2 reproduces
    the same spatial pattern is a separate question, and the answer decides how
    much the satellite branch can be said to validate the frame rather than
    merely be tied to it.
    """
    from scipy import stats
    a = pd.read_csv(ICESAT_TABLES / "egg2015_to_evrf2019_by_station.csv")
    b = pd.read_csv(ICESAT_TABLES / "kakhovka_datum_comparison.csv")
    m = a.merge(b[["station_id", "delta_empirical_m", "delta_official_m",
                   "delta_unexplained_m"]], on="station_id")
    x, y, u = (m["delta_official_m"].values, m["delta_empirical_m"].values,
               m["delta_unexplained_m"].values)
    r, p_r = stats.pearsonr(x, y)
    rho, p_rho = stats.spearmanr(x, y)
    fit = stats.linregress(x, y)
    return pd.DataFrame([{
        "n_stations": len(m), "n_matchups": int(m["n_matchups"].sum()),
        "official_min_m": round(float(x.min()), 4), "official_max_m": round(float(x.max()), 4),
        "empirical_min_m": round(float(y.min()), 4), "empirical_max_m": round(float(y.max()), 4),
        "pearson_r": round(float(r), 3), "pearson_p": round(float(p_r), 3),
        "spearman_rho": round(float(rho), 3), "spearman_p": round(float(p_rho), 3),
        "slope": round(float(fit.slope), 3), "slope_stderr": round(float(fit.stderr), 3),
        "residual_mean_m": round(float(u.mean()), 4),
        "residual_sd_m": round(float(u.std(ddof=1)), 4),
        "residual_min_m": round(float(u.min()), 4), "residual_max_m": round(float(u.max()), 4),
        "residual_one_sign": bool((u > 0).all() or (u < 0).all()),
        "per_station_nmad_min_m": round(float(a["empirical_nmad_m"].min()), 3),
        "per_station_nmad_max_m": round(float(a["empirical_nmad_m"].max()), 3),
    }])


#: table name -> (builder, the snapshot files it actually reads). The manifest
#: records these per table; attributing all of them to one input would make the
#: provenance trail wrong in exactly the way it exists to prevent.
TABLES = {
    "slope_per_pass_uncertainty": (per_pass_uncertainty, (PERDATE,)),
    "slope_span_sensitivity": (span_sensitivity, (PERDATE,)),
    "gauge_icesat_agreement": (gauge_icesat_agreement,
                               (ICESAT_TABLES / "egg2015_to_evrf2019_by_station.csv",
                                ICESAT_TABLES / "kakhovka_datum_comparison.csv")),
}


def build() -> dict[str, Path]:
    if not PERDATE.exists():
        raise FileNotFoundError(f"snapshot input missing: {PERDATE} — run --step snapshot")
    frame = pd.read_csv(PERDATE)
    DERIVED_DIR.mkdir(parents=True, exist_ok=True)
    written: dict[str, Path] = {}
    entries = []
    for name, (fn, inputs) in TABLES.items():
        missing = [i for i in inputs if not i.exists()]
        if missing:
            logger.warning("%s skipped — missing %s", name, ", ".join(m.name for m in missing))
            continue
        out = DERIVED_DIR / f"{name}.csv"
        table = fn(frame)
        table.to_csv(out, index=False)
        written[name] = out
        entries.append({"table": f"{name}.csv", "rows": len(table),
                        "sources": [{"path": str(i.relative_to(SNAPSHOT_DIR)),
                                     "sha256": _sha256(i)} for i in inputs],
                        "sha256": _sha256(out)})
        logger.info("derived %s (%d rows)", out.name, len(table))
    MANIFEST.write_text(json.dumps(
        {"built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "tables": entries},
        indent=1), encoding="utf-8")
    return written
