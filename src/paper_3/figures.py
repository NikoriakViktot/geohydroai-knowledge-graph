"""Manuscript figures, drawn from the snapshot and from nothing else.

Every panel here reads a table that ``snapshot.py`` pulled and hashed, so a
figure cannot drift from the number the same table puts in the text. A figure
whose table is missing is skipped with a warning rather than drawn from a
default — a plot of absent data is worse than no plot.

The figure ids match the ``figure_id`` column of ``own_claims.yaml``.
"""
from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

from src.paper_3._utils import OUT_DIR
from src.paper_3.snapshot import SNAPSHOT_DIR

logger = logging.getLogger(__name__)

FIGURE_DIR = OUT_DIR / "figures"
TABLES = SNAPSHOT_DIR / "swot" / "outputs" / "tables"
ICESAT = SNAPSHOT_DIR / "icesat" / "outputs" / "tables"


def _read_at(base, name):
    p = base / name
    if not p.exists():
        logger.warning("figure input missing, skipping: %s", name)
        return None
    return pd.read_csv(p)

#: Muted, print-safe and distinguishable in greyscale.
INK = "#1b1b1b"
ACCENT = "#1f6f8b"
WARM = "#b5651d"
MUTED = "#8c8c8c"


def _style():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams.update({
        "figure.dpi": 200, "savefig.dpi": 300, "savefig.bbox": "tight",
        "font.size": 8.5, "axes.labelsize": 8.5, "axes.titlesize": 9,
        "axes.spines.top": False, "axes.spines.right": False,
        "axes.edgecolor": INK, "text.color": INK, "axes.labelcolor": INK,
        "xtick.color": INK, "ytick.color": INK, "grid.alpha": 0.25,
        "grid.linewidth": 0.5, "legend.frameon": False,
    })
    return plt


def _read(name: str) -> pd.DataFrame | None:
    path = TABLES / name
    if not path.exists():
        logger.warning("figure input missing, skipping: %s", name)
        return None
    return pd.read_csv(path)


# ── F13 · the bed against an independent observation ─────────────────────────

def fig_bed_validation(plt) -> Path | None:
    df = _read("p52_pool_dem_vs_icesat2.csv")
    if df is None:
        return None
    bands = [("dist to sounding 0-250 m", "0–250"), ("dist to sounding 250-500 m", "250–500"),
             ("dist to sounding 500-1000 m", "0.5–1 k"), ("dist to sounding 1000-2000 m", "1–2 k"),
             ("dist to sounding 2000-99999 m", "> 2 k")]
    d = df.set_index("set")
    rows = [(lab, d.loc[k]) for k, lab in bands if k in d.index]
    if not rows:
        return None

    fig, (ax, bx) = plt.subplots(1, 2, figsize=(7.2, 2.9), gridspec_kw={"width_ratios": [1.15, 1]})
    labels = [r[0] for r in rows]
    rmse = [float(r[1]["RMSE"]) for r in rows]
    bias = [float(r[1]["bias"]) for r in rows]
    n = [int(float(r[1]["N"])) for r in rows]

    ax.bar(labels, rmse, color=ACCENT, width=0.62)
    ax.plot(labels, bias, "o-", color=WARM, ms=3.5, lw=1.2, label="bias")
    ax.axhline(0, color=MUTED, lw=0.6)
    for i, (r, c) in enumerate(zip(rmse, n)):
        ax.text(i, r + 0.06, f"n={c:,}".replace(",", " "), ha="center", fontsize=6.4, color=MUTED)
    ax.set_ylabel("m")
    ax.set_xlabel("distance to the nearest sounding (m)")
    ax.set_title("a · flat within a kilometre of a sounding, then rising", loc="left")
    ax.legend(loc="upper left", fontsize=7.5)
    ax.grid(axis="y")

    strata = [("year 2023", "2023"), ("year 2024", "2024"), ("leaf-off", "leaf-off"),
              ("leaf-on", "leaf-on"), ("recession zone fast", "fast"),
              ("recession zone mid", "mid"), ("recession zone slow", "slow")]
    srows = [(lab, d.loc[k]) for k, lab in strata if k in d.index]
    bx.barh([r[0] for r in srows][::-1], [float(r[1]["RMSE"]) for r in srows][::-1],
            color=MUTED, height=0.6)
    overall = d.loc["ALL 2023-07..2024-12, dry bed"] if "ALL 2023-07..2024-12, dry bed" in d.index else None
    if overall is not None:
        bx.axvline(float(overall["RMSE"]), color=ACCENT, lw=1.3,
                   label=f"all dry bed, {float(overall['RMSE']):.2f} m")
        bx.legend(loc="upper right", fontsize=7.5)
    bx.set_xlabel("RMSE against ICESat-2 (m)")
    bx.set_title("b · by epoch, season and recession zone", loc="left")
    bx.grid(axis="x")

    out = FIGURE_DIR / "F13_bed_validation_icesat2.png"
    fig.savefig(out)
    plt.close(fig)
    return out


# ── F15 · the drawdown and the wave it produced ──────────────────────────────

def fig_drawdown_and_wave(plt) -> Path | None:
    dd = _read("p60_swot_outlet_drawdown.csv")
    pr = _read("p59_swot_peak_wse_profile.csv")
    if dd is None or pr is None:
        return None
    dd = dd.copy()
    dd["date"] = pd.to_datetime(dd["date"])
    dd = dd.sort_values("date")

    fig, (ax, bx) = plt.subplots(1, 2, figsize=(7.2, 2.9))
    ax.plot(dd["date"], dd["H_outlet"], "o-", color=ACCENT, ms=4, lw=1.4, label="SWOT, outlet")
    if "H_outlet_min" in dd and dd["H_outlet_min"].notna().any():
        ax.fill_between(dd["date"], dd["H_outlet_min"], dd["H_outlet_max"],
                        color=ACCENT, alpha=0.18, lw=0)
    if "H_rozumivka" in dd and dd["H_rozumivka"].notna().any():
        ax.plot(dd["date"], dd["H_rozumivka"], "s--", color=WARM, ms=3, lw=1.1,
                label="Rozumivka gauge")
    ax.set_xlim(pd.Timestamp("2023-05-28"), pd.Timestamp("2023-06-30"))
    ax.set_ylabel("water surface (m, EVRF2019)")
    ax.set_title("a · the pool emptying, outlet vs upstream gauge", loc="left")
    ax.legend(fontsize=7.5)
    ax.grid(axis="y")
    for lab in ax.get_xticklabels():
        lab.set_rotation(30)
        lab.set_ha("right")

    pr = pr.sort_values("bin_km")
    bx.plot(pr["bin_km"], pr["rise_m"], "o-", color=ACCENT, ms=3.5, lw=1.4, label="rise above pre-breach")
    bx.plot(pr["bin_km"], pr["H_pre"], "-", color=MUTED, lw=1.0, label="pre-breach surface")
    bx.set_xlabel("distance below the dam (km)")
    bx.set_ylabel("m")
    bx.set_title("b · the wave, decaying downstream", loc="left")
    bx.legend(fontsize=7.5)
    bx.grid(axis="y")

    out = FIGURE_DIR / "F15_swot_drawdown_and_wave.png"
    fig.savefig(out)
    plt.close(fig)
    return out


# ── F14 · what the delivered terrain is worth, per land cover ────────────────

def fig_terrain_accuracy(plt) -> Path | None:
    df = _read("p57_dem_accuracy_night.csv")
    if df is None:
        return None
    wc = df[df["set"].str.contains("WorldCover", na=False)].copy()
    if wc.empty:
        return None
    wc["cover"] = wc["set"].str.extract(r"WorldCover (\w+)")[0]
    wc = wc.dropna(subset=["cover"]).sort_values("RMSE")

    fig, ax = plt.subplots(figsize=(4.6, 2.9))
    colours = [WARM if float(r) > 1.5 else ACCENT for r in wc["RMSE"]]
    ax.barh(wc["cover"], wc["RMSE"].astype(float), color=colours, height=0.62)
    for i, (r, n) in enumerate(zip(wc["RMSE"].astype(float), wc["N"].astype(float))):
        ax.text(r + 0.05, i, f"{r:.2f}  (n={int(n):,})".replace(",", " "),
                va="center", fontsize=6.4, color=MUTED)
    ax.set_xlabel("RMSE against night-time ICESat-2 (m)")
    ax.set_title("Terrain error is a land-cover statement:\nunder canopy the residual is vegetation, not relief",
                 loc="left")
    ax.set_xlim(0, float(wc["RMSE"].astype(float).max()) * 1.45)
    ax.grid(axis="x")

    out = FIGURE_DIR / "F14_terrain_accuracy_by_cover.png"
    fig.savefig(out)
    plt.close(fig)
    return out


# ── P2-F4 · roughness of the mapped surface, state by state ──────────────────

def fig_roughness_states(plt) -> Path | None:
    df = _read("p58_manning_mosaic_summary.csv")
    if df is None:
        return None
    d = df[df["cell_m"] == 20].copy()
    if d.empty:
        return None
    order = ["BREACH_2023", "FIRST_EXPOSURE_2023", "STATE_2024", "STATE_2025", "CURRENT_2026"]
    d["k"] = d["state"].apply(lambda s: order.index(s) if s in order else 99)
    d = d.sort_values("k")
    labels = [s.replace("_", "\n") for s in d["state"]]

    fig, ax = plt.subplots(figsize=(5.0, 2.9))
    ax.fill_between(range(len(d)), d["n_base_p10"].astype(float), d["n_base_p90"].astype(float),
                    color=MUTED, alpha=0.22, lw=0, label="class spread (p10–p90)")
    ax.plot(range(len(d)), d["n_base_area_weighted"].astype(float), "o-",
            color=ACCENT, ms=5, lw=1.6, label="area-weighted base n")
    for i, v in enumerate(d["n_base_area_weighted"].astype(float)):
        ax.text(i, v + 0.004, f"{v:.4f}", ha="center", fontsize=7, color=ACCENT)
    ax.set_xticks(range(len(d)))
    ax.set_xticklabels(labels, fontsize=7)
    ax.set_ylabel("Manning n (literature prior)")
    ax.set_title("Roughness implied by the mapped surface\n(a prior per class, not a calibrated coefficient)",
                 loc="left")
    ax.legend(fontsize=7.5)
    ax.grid(axis="y")

    out = FIGURE_DIR / "P2-F4_roughness_by_state.png"
    fig.savefig(out)
    plt.close(fig)
    return out


# ── P2-F7 · canopy by class, on a matched season window ──────────────────────

def fig_canopy_by_class(plt) -> Path | None:
    df = _read("p62_gedi_canopy_matched_window.csv")
    if df is None:
        return None
    wide = df.pivot_table(index="manning_class", columns="year", values="rh98_p50")
    wide = wide.dropna().sort_values(wide.columns[-1])
    if wide.empty:
        return None
    import numpy as np
    y = np.arange(len(wide))
    fig, ax = plt.subplots(figsize=(5.4, 3.1))
    ax.barh(y - 0.19, wide.iloc[:, 0], height=0.36, color=MUTED, label=str(wide.columns[0]))
    ax.barh(y + 0.19, wide.iloc[:, 1], height=0.36, color=ACCENT, label=str(wide.columns[1]))
    ax.axvline(2.7, color=WARM, lw=1.1, ls="--")
    ax.text(2.74, len(wide) - 0.5, "GEDI noise floor", color=WARM, fontsize=6.8, va="top")
    ax.set_yticks(y)
    ax.set_yticklabels([c.replace("_", " ") for c in wide.index], fontsize=7.5)
    ax.set_xlabel("median rh98 (m), 1 June - 9 July")
    ax.set_title("Canopy rises in the woody classes only;\nbare and water classes sit on the sensor floor", loc="left")
    ax.legend(fontsize=7.5, loc="lower right")
    ax.grid(axis="x")
    out = FIGURE_DIR / "P2-F7_canopy_by_class.png"
    fig.savefig(out)
    plt.close(fig)
    return out



# ── Paper 1 ──────────────────────────────────────────────────────────────────

def fig_slope_per_date(plt) -> Path | None:
    """The headline: every overpass, with the interval its own fit carries.

    Plotting only the two group medians would hide what Section 6.3.1 says —
    a single pass does not resolve the gradient. Showing each pass with its
    Theil-Sen interval makes the argument visible: the evidence is that the
    whole post-breach ensemble sits above zero, not that any one pass does.
    """
    df = _read("kakhovka_perdate_slopes_robust.csv")
    if df is None:
        return None
    import numpy as np
    d = df.copy()
    d["date"] = pd.to_datetime(d["date"])
    d = d.sort_values("date")
    colour = {"PRE_BREACH": ACCENT, "BREACH_DRAWDOWN": WARM, "POST_BREACH": "#2e7d32"}

    fig, (ax, bx) = plt.subplots(1, 2, figsize=(7.4, 3.1),
                                 gridspec_kw={"width_ratios": [2.1, 1]})
    for period, g in d.groupby("period"):
        c = colour.get(period, MUTED)
        ax.errorbar(g["date"], g["slope_theilsen_cm_km"],
                    yerr=[g["slope_theilsen_cm_km"] - g["ts_lo_cm_km"],
                          g["ts_hi_cm_km"] - g["slope_theilsen_cm_km"]],
                    fmt="o", ms=3.6, lw=0.9, capsize=1.8, color=c,
                    ecolor=c, elinewidth=0.7, alpha=0.9,
                    label=period.replace("_", " ").lower())
    ax.axhline(0, color=INK, lw=0.8)
    ax.axvline(pd.Timestamp("2023-06-06"), color=MUTED, ls="--", lw=0.9)
    # A handful of post-breach fits carry intervals a hundred times the signal.
    # Left unclipped they flatten the very contrast the panel exists to show, so
    # the axis is bounded and the clipping is stated rather than hidden.
    lo, hi = -20.0, 30.0
    n_clipped = int(((d["ts_lo_cm_km"] < lo) | (d["ts_hi_cm_km"] > hi)).sum())
    ax.set_ylim(lo, hi)
    ax.text(0.015, 0.03,
            f"{n_clipped} of {len(d)} intervals extend beyond the axis "
            f"(widest {d['ts_hi_cm_km'].max():.0f} cm/km); see Section 6.3.1",
            transform=ax.transAxes, fontsize=6.3, color=MUTED)
    ax.text(pd.Timestamp("2023-06-20"), hi * 0.9, "breach", fontsize=7, color=MUTED)
    ax.set_ylabel("longitudinal slope (cm/km)")
    ax.set_title("a · every overpass, with its own Theil–Sen interval", loc="left")
    ax.legend(fontsize=7, ncol=3, loc="upper left")
    ax.grid(axis="y")
    for lab in ax.get_xticklabels():
        lab.set_rotation(25); lab.set_ha("right")

    order = ["PRE_BREACH", "POST_BREACH"]
    data = [d.loc[d["period"] == p, "slope_theilsen_cm_km"].values for p in order]
    parts = bx.boxplot(data, widths=0.55, patch_artist=True,
                       tick_labels=["pre", "post"])
    for patch, p_ in zip(parts["boxes"], order):
        patch.set_facecolor(colour[p_]); patch.set_alpha(0.35)
        patch.set_edgecolor(colour[p_])
    for i, (p_, vals) in enumerate(zip(order, data), start=1):
        bx.scatter(np.random.normal(i, 0.055, len(vals)), vals, s=9,
                   color=colour[p_], zorder=3, alpha=0.85)
        bx.text(i, np.median(vals) + 0.35, f"{np.median(vals):+.2f}",
                ha="center", fontsize=7.5, color=colour[p_])
    bx.axhline(0, color=INK, lw=0.8)
    bx.set_ylabel("cm/km")
    bx.set_title("b · the distributions compared", loc="left")
    bx.grid(axis="y")

    out = FIGURE_DIR / "F02_slope_per_overpass.png"
    fig.savefig(out); plt.close(fig)
    return out


def fig_heterogeneity(plt) -> Path | None:
    """The second, independently defined geometric observable."""
    df = _read("kakhovka_perdate_slopes_robust.csv")
    if df is None or "wse_p95_p05_m" not in df.columns:
        return None
    import numpy as np
    d = df.copy()
    d["date"] = pd.to_datetime(d["date"])
    colour = {"PRE_BREACH": ACCENT, "BREACH_DRAWDOWN": WARM, "POST_BREACH": "#2e7d32"}
    fig, ax = plt.subplots(figsize=(4.9, 2.9))
    for period, g in d.groupby("period"):
        ax.scatter(g["date"], g["wse_p95_p05_m"], s=18, alpha=0.85,
                   color=colour.get(period, MUTED),
                   label=period.replace("_", " ").lower())
    for period in ("PRE_BREACH", "POST_BREACH"):
        g = d[d["period"] == period]
        if len(g):
            ax.hlines(np.median(g["wse_p95_p05_m"]), g["date"].min(), g["date"].max(),
                      color=colour[period], lw=1.6)
    ax.axvline(pd.Timestamp("2023-06-06"), color=MUTED, ls="--", lw=0.9)
    ax.set_ylabel("within-overpass WSE range, p95 − p05 (m)")
    ax.set_title("Water-surface heterogeneity, by overpass", loc="left")
    ax.legend(fontsize=7)
    ax.grid(axis="y")
    for lab in ax.get_xticklabels():
        lab.set_rotation(25); lab.set_ha("right")
    out = FIGURE_DIR / "F04_heterogeneity.png"
    fig.savefig(out); plt.close(fig)
    return out



def fig_gauge_icesat(plt) -> Path | None:
    """The tie between the satellite branch and the gauges, station by station.

    Only pre-breach data can appear here: the reservoir gauge series end on
    31 December 2021, so every matchup precedes the breach by construction.
    """
    import numpy as np
    a = _read_at(ICESAT, "egg2015_to_evrf2019_by_station.csv")
    b = _read_at(ICESAT, "kakhovka_datum_comparison.csv")
    if a is None or b is None:
        return None
    m = a.merge(b[["station_id", "delta_empirical_m", "delta_official_m",
                   "delta_unexplained_m"]], on="station_id").sort_values("delta_official_m")
    from scipy import stats
    x, y = m["delta_official_m"].values, m["delta_empirical_m"].values
    r, p_r = stats.pearsonr(x, y)

    fig, (ax, bx) = plt.subplots(1, 2, figsize=(7.4, 3.2),
                                 gridspec_kw={"width_ratios": [1, 1.25]})
    ax.scatter(x * 100, y * 100, s=46, color=ACCENT, zorder=3)
    for xi, yi, nm, nn in zip(x, y, m["name_en"], m["n_matchups"]):
        ax.annotate(f"{nm} (n={nn})", (xi * 100, yi * 100), textcoords="offset points",
                    xytext=(7, 4), fontsize=6.2, color=INK)
    lim = [min(x.min(), y.min()) * 100 - 3, max(x.max(), y.max()) * 100 + 3]
    ax.plot(lim, lim, color=MUTED, lw=1.0, ls="--", label="1:1")
    ax.set_xlim(lim); ax.set_ylim(lim)
    ax.set_xlabel("official transformation (cm)")
    ax.set_ylabel("empirical offset from ICESat-2 (cm)")
    ax.set_title(f"a · the two do not track each other\n"
                 f"Pearson r = {r:+.2f} (p = {p_r:.2f}, n = {len(m)})", loc="left")
    ax.legend(fontsize=7, loc="upper left")
    ax.grid(alpha=0.2)

    yy = np.arange(len(m))
    lo = (m["c_station_m"] - m["bootstrap_ci95_low_m"]).abs().values * 100
    hi = (m["bootstrap_ci95_high_m"] - m["c_station_m"]).abs().values * 100
    bx.errorbar(m["c_station_m"].values * 100, yy, xerr=[lo, hi], fmt="o",
                ms=5, color=ACCENT, ecolor=ACCENT, elinewidth=1.1, capsize=2.5)
    bx.axvline(float(m["c_station_m"].mean()) * 100, color=WARM, lw=1.2,
               label=f"mean {m['c_station_m'].mean()*100:+.1f} cm")
    bx.set_yticks(yy)
    bx.set_yticklabels([f"{n}  (n={k})" for n, k in zip(m["name_en"], m["n_matchups"])],
                       fontsize=7.2)
    bx.set_xlabel("alignment constant (cm), 95 % bootstrap interval")
    bx.set_title("b · well determined at each station,\nand of one sign throughout", loc="left")
    bx.legend(fontsize=7.5, loc="lower right")
    bx.grid(axis="x", alpha=0.25)

    out = FIGURE_DIR / "F01_gauge_icesat_tie.png"
    fig.savefig(out); plt.close(fig)
    return out


FIGURES = {
    "F01": fig_gauge_icesat,
    "F02": fig_slope_per_date,
    "F04": fig_heterogeneity,
    "P2-F7": fig_canopy_by_class,
    "F13": fig_bed_validation,
    "F14": fig_terrain_accuracy,
    "F15": fig_drawdown_and_wave,
    "P2-F4": fig_roughness_states,
}


def build(only: str | None = None) -> list[Path]:
    plt = _style()
    FIGURE_DIR.mkdir(parents=True, exist_ok=True)
    made: list[Path] = []
    for fid, fn in FIGURES.items():
        if only and fid != only:
            continue
        path = fn(plt)
        if path is None:
            logger.warning("%s not drawn (input missing)", fid)
            continue
        logger.info("%s -> %s", fid, path.name)
        made.append(path)
    return made
