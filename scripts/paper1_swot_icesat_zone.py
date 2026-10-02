#!/usr/bin/env python
"""Paper 1 -- SWOT vs ICESat-2 across the whole study zone, with ICESat-2 carried into SWOT's own
geoid frame (EGM2008 mean-tide ``geoid_hght`` as shipped in every SWOT RiverSP node record).

Vertical chain (both sensors on WGS84; ITRF2014 vs ITRF2020 difference is mm-level and ignored)
    SWOT     wse                      = h_ell,mean-tide  - N_EGM2008,mean-tide          (product definition)
    ICESat-2 h_ell,tide-free          = ATL13 ht_water_surf                             (ATL03 convention)
             h_ell,mean-tide          = ht_water_surf + free2mean(lat)                  (ATL03 ATBD r007 p.125:
                                         free2mean = 0.06029 - 0.180873 sin^2 lat)
             H_IS2 (SWOT frame)       = h_ell,mean-tide - geoid_hght(SWOT node)         <- primary
    Sensitivities (same match set):
      naive    : SWOT wse vs ATL13 ht_ortho (EGM2008 tide-free geoid, tide-free crust)
      no-tide  : ht_water_surf - geoid_hght (geoid swapped, crust tide term omitted)
    Frame check: N_IS2 = ht_water_surf - ht_ortho (ATL13's EGM2008 realisation) is compared with SWOT
      geoid_hght at the same node.  Were ATL13's geoid tide-free, the difference would be
      N_mt - N_tf = 1.3*(0.099 - 0.296 sin^2 lat) ~ -7.7 cm (IERS, k=0.3).  Observed (2026-09-23): +2.4 cm,
      NMAD 1.3 cm -> ATL13 ht_ortho is NOT on a tide-free geoid; the primary chain uses SWOT's own
      geoid_hght at every node and therefore does not depend on ATL13's geoid at all.

Frozen rules
    S1  SWOT RiverSP nodes: valid wse, node_q <= 1 (primary; <= 2 sensitivity), dark_frac < 0.5.
    S2  An ICESat-2 ATL13 segment is attached to its nearest SWOT node within 200 m.
    S3  Unit of analysis = one crossing = (ICESat-2 date, RGT) x (SWOT cycle, pass); node-level
        differences are reduced by median inside a crossing.  Nodes are never replicates.
    S4  Primary time window |dt| <= 1 day; 1-3 d and 3-10 d reported as timing sensitivity.
        No interpolation in time.
    S5  No residual-based outlier removal; robust statistics (median, NMAD) are primary.
    S6  Zones along SWORD distance from the dam s (km, positive downstream):
        s < 0 former reservoir footprint; 0 <= s < 70 dam -> Kherson; s >= 70 delta / estuary.
"""
from __future__ import annotations

import glob
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import matplotlib
import numpy as np
import pandas as pd
import pyogrio
from scipy import stats
from scipy.spatial import cKDTree

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

KG = Path(__file__).resolve().parents[1]
IN = KG / "data/paper_1_audit/correlation/inputs"
OUT = KG / "data/paper_1_audit/swot_icesat_zone"
FIG = KG / "outputs/paper_1/swot_icesat_zone"
OUT.mkdir(parents=True, exist_ok=True); FIG.mkdir(parents=True, exist_ok=True)
SWOT_DIR = Path("/mnt/f/data_kakhovka_dem_swot/swot_ua/swot_l2_hr_riversp_2.0")
ATL13 = {n: IN / f"icesat2-atl13-kakhovka/data/raw/atl13/{n}_atl13x_raw.parquet" for n in ("kakhovka", "kherson", "dnipro_estuary")}
BBOX = (31.30, 46.08, 35.53, 48.00)  # analysis zones 1-4 (31.33-35.50 E, 46.11-47.96 N) + margin: nothing clipped
DAM = (33.3667, 46.7783)
MAX_NODE_DIST_M = 200.0
MAX_DT_D = 10.0
BREACH = pd.Timestamp("2023-06-06", tz="UTC")
SEED = 20260923; N_BOOT = 4000

INK, INK2, MUTED, GRID, BASE = "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#c3c2b7"
C1, C2, C3 = "#2a78d6", "#eb6834", "#1baf7a"
DIV = matplotlib.colors.LinearSegmentedColormap.from_list("div", ["#104281", "#3987e5", "#f0efec", "#e34948", "#9e2b2a"])
plt.rcParams.update({
    "figure.facecolor": "white", "axes.facecolor": "white", "axes.edgecolor": BASE, "axes.labelcolor": INK2,
    "xtick.color": MUTED, "ytick.color": MUTED, "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.6,
    "axes.spines.top": False, "axes.spines.right": False, "font.size": 9, "axes.titlesize": 10,
    "axes.titleweight": "bold", "axes.titlecolor": INK, "legend.frameon": False, "savefig.dpi": 200,
})


def free2mean(lat):
    return 0.06029 - 0.180873 * np.sin(np.radians(lat)) ** 2


def geoid_mt_minus_tf(lat):
    return 1.3 * (0.099 - 0.296 * np.sin(np.radians(lat)) ** 2)


def nmad(e):
    e = np.asarray(e, float)
    return float(1.4826 * np.median(np.abs(e - np.median(e)))) if len(e) else np.nan


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def xy(lon, lat):
    """Local equirectangular metres (adequate for 200 m nearest-neighbour search)."""
    lat0 = np.radians(47.0)
    return np.column_stack([np.radians(lon) * 6371000 * np.cos(lat0), np.radians(lat) * 6371000])


# ----------------------------------------------------------------------------- SWOT
def load_swot() -> pd.DataFrame:
    cache = OUT / "swot_riversp_nodes_zone.parquet"
    files = sorted(glob.glob(str(SWOT_DIR / "*/*/*RiverSP_Node*.shp")))
    if cache.exists():                     # delete the cache to re-read the shapefiles
        return pd.read_parquet(cache)
    cols = ["reach_id", "node_id", "time_str", "lat", "lon", "river_name", "wse", "wse_u", "node_q", "dark_frac",
            "geoid_hght", "xovr_cal_q", "p_dist_out", "n_good_pix", "width"]
    parts = []
    for i, f in enumerate(files):
        d = pd.DataFrame(pyogrio.read_dataframe(f, bbox=BBOX, read_geometry=True, columns=cols).drop(columns="geometry"))
        d = d[(d.wse > -1e10) & (d.geoid_hght > -1e10) & (d.lat > -1e10)]
        if not len(d):
            continue
        nm = Path(f).name.split("_")
        d = d.assign(cycle=int(nm[5]), pass_id=int(nm[6]), source_file=Path(f).name)
        parts.append(d)
        if i % 100 == 0:
            print(f"  swot {i}/{len(files)}", flush=True)
    d = pd.concat(parts, ignore_index=True)
    d["t_swot"] = pd.to_datetime(d.time_str, utc=True, errors="coerce")
    d = d.dropna(subset=["t_swot"])
    d.to_parquet(cache, index=False)
    return d


# ----------------------------------------------------------------------------- ICESat-2
def load_icesat() -> pd.DataFrame:
    parts = []
    for body, f in ATL13.items():
        d = pd.read_parquet(f, columns=["time", "gt", "rgt", "cycle", "ht_water_surf", "ht_ortho", "stdev_water_surf", "geometry_wkt"])
        ll = d.geometry_wkt.str.extract(r"POINT \(([-\d.]+) ([-\d.]+)\)").astype(float)
        d["lon"], d["lat"] = ll[0].values, ll[1].values
        d = d.drop(columns="geometry_wkt")
        d = d[(d.lon.between(BBOX[0], BBOX[2])) & (d.lat.between(BBOX[1], BBOX[3]))]
        d = d[np.isfinite(d.ht_water_surf) & np.isfinite(d.ht_ortho) & (d.ht_water_surf < 1e5)]
        d["t_is2"] = pd.to_datetime(d.time, utc=True)
        d["body"] = body
        parts.append(d.drop(columns="time"))
    d = pd.concat(parts, ignore_index=True)
    # the three bodies overlap on shared granules: keep one copy of each segment
    d = d.drop_duplicates(subset=["t_is2", "gt", "lon", "lat"])
    return d


# ----------------------------------------------------------------------------- matching
def match(sw: pd.DataFrame, is2: pd.DataFrame) -> pd.DataFrame:
    nodes = sw.groupby("node_id").agg(lon=("lon", "median"), lat=("lat", "median"), geoid_hght=("geoid_hght", "median"),
                                      p_dist_out=("p_dist_out", "first"), reach_id=("reach_id", "first"),
                                      river_name=("river_name", "first")).reset_index()
    tree = cKDTree(xy(nodes.lon.values, nodes.lat.values))
    dist, idx = tree.query(xy(is2.lon.values, is2.lat.values), distance_upper_bound=MAX_NODE_DIST_M)
    ok = np.isfinite(dist)
    s = is2[ok].copy(); s["node_id"] = nodes.node_id.values[idx[ok]]; s["node_dist_m"] = dist[ok]
    s["is2_date"] = s.t_is2.dt.floor("D")
    s["N_is2_tf"] = s.ht_water_surf - s.ht_ortho
    s["h_mt"] = s.ht_water_surf + free2mean(s.lat)
    a = s.groupby(["is2_date", "rgt", "gt", "node_id"]).agg(
        t_is2=("t_is2", "median"), h_mt=("h_mt", "median"), h_tf=("ht_water_surf", "median"), ht_ortho=("ht_ortho", "median"),
        N_is2_tf=("N_is2_tf", "median"), lat_is2=("lat", "median"), lon_is2=("lon", "median"), n_seg=("h_mt", "size"),
        seg_spread_m=("h_mt", nmad)).reset_index()
    # beams hitting the same node in one overpass -> one value per (overpass, node)
    a = a.groupby(["is2_date", "rgt", "node_id"]).agg(
        t_is2=("t_is2", "median"), h_mt=("h_mt", "median"), h_tf=("h_tf", "median"), ht_ortho=("ht_ortho", "median"),
        N_is2_tf=("N_is2_tf", "median"), lat_is2=("lat_is2", "median"), lon_is2=("lon_is2", "median"), n_seg=("n_seg", "sum"),
        n_beams=("gt", "nunique")).reset_index()
    m = a.merge(sw[["node_id", "t_swot", "cycle", "pass_id", "wse", "wse_u", "node_q", "dark_frac", "geoid_hght", "xovr_cal_q",
                    "n_good_pix", "width", "source_file"]], on="node_id")
    m["dt_days"] = (m.t_swot - m.t_is2).dt.total_seconds() / 86400
    m = m[m.dt_days.abs() <= MAX_DT_D].copy()
    m = m.merge(nodes[["node_id", "p_dist_out", "reach_id", "river_name", "lon", "lat"]], on="node_id")
    dam_node = nodes.iloc[np.argmin(np.hypot(*(xy(nodes.lon.values, nodes.lat.values) - xy(np.array([DAM[0]]), np.array([DAM[1]]))).T))]
    m["s_km"] = (dam_node.p_dist_out - m.p_dist_out) / 1e3
    m["zone"] = np.select([m.s_km < 0, m.s_km < 70], ["reservoir footprint (upstream of dam)", "dam -> Kherson"], "delta / estuary")
    m["period"] = np.select([m.t_is2 < BREACH, m.t_is2 < pd.Timestamp("2023-07-01", tz="UTC")],
                            ["PRE_BREACH", "BREACH_DRAWDOWN"], "POST_BREACH")
    # the three frames
    m["H_is2_swotframe"] = m.h_mt - m.geoid_hght                 # primary
    m["d_primary"] = m.wse - m.H_is2_swotframe
    m["d_naive_ht_ortho"] = m.wse - m.ht_ortho
    m["d_no_tide"] = m.wse - (m.h_tf - m.geoid_hght)
    m["geoid_swot_minus_is2"] = m.geoid_hght - m.N_is2_tf
    m["geoid_theory_mt_minus_tf"] = geoid_mt_minus_tf(m.lat_is2)
    m["crossing_id"] = m.is2_date.dt.strftime("%Y-%m-%d") + "_rgt" + m.rgt.astype(str) + "__c" + m.cycle.astype(str) + "p" + m.pass_id.astype(str)
    return m, nodes, dam_node


def crossings(m: pd.DataFrame, qmax: int) -> pd.DataFrame:
    s = m[(m.node_q <= qmax) & (m.dark_frac < 0.5)]
    return s.groupby("crossing_id").agg(
        is2_date=("is2_date", "first"), rgt=("rgt", "first"), cycle=("cycle", "first"), pass_id=("pass_id", "first"),
        t_is2=("t_is2", "median"), t_swot=("t_swot", "median"), dt_days=("dt_days", "median"),
        n_nodes=("node_id", "nunique"), s_km=("s_km", "median"), s_min=("s_km", "min"), s_max=("s_km", "max"),
        lon=("lon", "median"), lat=("lat", "median"), zone=("zone", lambda z: z.mode().iat[0]), period=("period", "first"),
        wse_swot=("wse", "median"), H_is2=("H_is2_swotframe", "median"),
        d_primary=("d_primary", "median"), d_naive_ht_ortho=("d_naive_ht_ortho", "median"), d_no_tide=("d_no_tide", "median"),
        within_crossing_nmad_m=("d_primary", nmad), geoid_swot_minus_is2=("geoid_swot_minus_is2", "median"),
        geoid_theory=("geoid_theory_mt_minus_tf", "median"), wse_u_med=("wse_u", "median")).reset_index().assign(node_q_max=qmax)


def summarise(c: pd.DataFrame, col="d_primary", n_boot=N_BOOT) -> dict:
    e = c[col].to_numpy(float)
    out = dict(n_crossings=len(c), n_nodes=int(c.n_nodes.sum()) if "n_nodes" in c else np.nan)
    if len(e) == 0:
        return out
    rng = np.random.default_rng(SEED)
    bm = [np.median(rng.choice(e, len(e))) for _ in range(n_boot)] if len(e) >= 3 else [np.nan]
    out.update(median_m=float(np.median(e)), median_ci_lo=float(np.nanpercentile(bm, 2.5)), median_ci_hi=float(np.nanpercentile(bm, 97.5)),
               nmad_m=nmad(e), mean_m=float(e.mean()), rmse_m=float(np.sqrt(np.mean(e ** 2))), mae_m=float(np.mean(np.abs(e))),
               p05_m=float(np.percentile(e, 5)), p95_m=float(np.percentile(e, 95)), share_abs_lt_10cm=float(np.mean(np.abs(e) < 0.10)))
    if len(c) >= 3 and c.wse_swot.std() > 0:
        out.update(pearson_r=float(stats.pearsonr(c.H_is2, c.wse_swot).statistic),
                   spearman_rho=float(stats.spearmanr(c.H_is2, c.wse_swot).statistic),
                   theilsen_slope=float(stats.theilslopes(c.wse_swot, c.H_is2).slope),
                   level_range_m=float(c.wse_swot.max() - c.wse_swot.min()))
    return out


# ----------------------------------------------------------------------------- figures
def figures(c1: pd.DataFrame, call: pd.DataFrame, m1: pd.DataFrame, nodes: pd.DataFrame, bytab: pd.DataFrame):
    # F_Z01 map
    fig, ax = plt.subplots(figsize=(11, 6))
    ax.plot(nodes.lon, nodes.lat, ",", color=BASE)
    lim = 0.3
    sc = ax.scatter(c1.lon, c1.lat, c=c1.d_primary.clip(-lim, lim), cmap=DIV, vmin=-lim, vmax=lim, s=40, edgecolor="white", lw=0.6, zorder=3)
    ax.plot(*DAM, marker="^", color=INK, ms=8, zorder=4); ax.annotate("Kakhovka dam", DAM, xytext=(5, 5), textcoords="offset points", fontsize=8)
    ax.plot(32.612, 46.624, marker="s", color=INK, ms=6, zorder=4); ax.annotate("Kherson 80805", (32.612, 46.624), xytext=(5, -12), textcoords="offset points", fontsize=8)
    fig.colorbar(sc, ax=ax, label="SWOT − ICESat-2 (m), clipped ±0.3", shrink=0.8)
    ax.set_xlabel("lon"); ax.set_ylabel("lat"); ax.set_aspect(1 / np.cos(np.radians(47)))
    ax.set_title(f"SWOT vs ICESat-2 in SWOT's EGM2008 frame, whole study zone — {len(c1)} crossings with |Δt| ≤ 1 d (grey = SWORD nodes)", loc="left")
    fig.tight_layout(); fig.savefig(FIG / "F_Z01_map_crossings.png"); plt.close(fig)

    # F_Z02 difference vs distance from dam + vs |dt|
    fig, axes = plt.subplots(1, 2, figsize=(15, 4.8))
    ax = axes[0]
    for (per, col) in (("PRE_BREACH", C3), ("BREACH_DRAWDOWN", C2), ("POST_BREACH", C1)):
        d = c1[c1.period == per]
        ax.plot(d.s_km, d.d_primary, "o", ms=6, color=col, mec="white", mew=0.7, label=f"{per} (n={len(d)})")
    ax.axhline(0, color=MUTED, lw=0.8); ax.axvline(0, color=MUTED, ls=":", lw=1); ax.text(0.5, ax.get_ylim()[1], " dam", va="top", color=MUTED, fontsize=8)
    ax.axvline(70, color=MUTED, ls=":", lw=1); ax.text(70.5, ax.get_ylim()[1], " delta/estuary", va="top", color=MUTED, fontsize=8)
    ax.set_xlabel("SWORD distance from dam s, km (negative = former reservoir)"); ax.set_ylabel("SWOT − ICESat-2, m")
    off = int((c1.d_primary.abs() > 0.5).sum()); ax.set_ylim(-0.5, 0.5)
    ax.set_title(f"Crossing differences along the whole system (|Δt| ≤ 1 d); {off} point(s) beyond ±0.5 m not shown", loc="left")
    ax.legend(fontsize=7, loc="lower left")
    ax = axes[1]
    bins = [(0, 1, "≤1 d"), (1, 3, "1–3 d"), (3, 10, "3–10 d")]
    data = [call[(call.dt_days.abs() > lo) & (call.dt_days.abs() <= hi)].d_primary.values if lo > 0 else call[call.dt_days.abs() <= hi].d_primary.values for lo, hi, _ in bins]
    bp = ax.boxplot(data, tick_labels=[f"{b[2]}\nn={len(d)}" for b, d in zip(bins, data)], showfliers=True, widths=0.5, patch_artist=True,
                    medianprops=dict(color=INK, lw=1.5), flierprops=dict(marker="o", ms=3, mfc=MUTED, mec="none"))
    for p in bp["boxes"]:
        p.set_facecolor("#cde2fb"); p.set_edgecolor(C1)
    ax.axhline(0, color=MUTED, lw=0.8); ax.set_ylabel("SWOT − ICESat-2, m"); ax.set_ylim(-1, 1)
    ax.set_title(f"Timing sensitivity by |Δt|; {sum(int((np.abs(d) > 1).sum()) for d in data)} point(s) beyond ±1 m not shown", loc="left")
    fig.tight_layout(); fig.savefig(FIG / "F_Z02_difference_along_system_and_dt.png"); plt.close(fig)

    # F_Z03 scatter
    fig, ax = plt.subplots(figsize=(6.2, 6))
    ax.plot(c1.H_is2, c1.wse_swot, "o", ms=5, color=C1, mec="white", mew=0.7)
    lo, hi = min(c1.H_is2.min(), c1.wse_swot.min()) - 0.3, max(c1.H_is2.max(), c1.wse_swot.max()) + 0.3
    ax.plot([lo, hi], [lo, hi], "--", color=MUTED, lw=1, label="1:1")
    r = bytab[(bytab.subset == "all zones") & (bytab.frame == "primary") & (bytab.dt_window == "≤1 d")].iloc[0]
    ax.text(0.03, 0.97, f"n = {int(r.n_crossings)} crossings\nr = {r.pearson_r:.3f}, ρ = {r.spearman_rho:.3f}\nTheil–Sen slope {r.theilsen_slope:.3f}\n"
            f"median {r.median_m:+.3f} m, NMAD {r.nmad_m:.3f} m\n(r mostly reflects the along-system level range)",
            transform=ax.transAxes, va="top", fontsize=8, bbox=dict(boxstyle="round,pad=0.3", fc="white", ec=GRID))
    ax.set_xlabel("ICESat-2 in SWOT frame (ht_water_surf + free2mean − geoid_hght), m"); ax.set_ylabel("SWOT RiverSP wse, m")
    ax.set_title("SWOT vs ICESat-2, |Δt| ≤ 1 d", loc="left"); ax.legend(loc="lower right", fontsize=8)
    fig.tight_layout(); fig.savefig(FIG / "F_Z03_scatter.png"); plt.close(fig)

    # F_Z04 frame proof: geoid difference + three frames
    fig, axes = plt.subplots(1, 2, figsize=(14, 4.6))
    ax = axes[0]
    g = m1.drop_duplicates("node_id")
    ax.hist(g.geoid_swot_minus_is2 * 100, bins=60, color=C1, alpha=0.9)
    th = g.geoid_theory_mt_minus_tf.median() * 100
    ax.axvline(th, color=C2, lw=2, label=f"expected if ATL13 geoid were tide-free: {th:.1f} cm")
    ax.axvline(g.geoid_swot_minus_is2.median() * 100, color=INK, lw=1.5, ls="--", label=f"observed median {g.geoid_swot_minus_is2.median() * 100:.1f} cm")
    ax.set_xlabel("SWOT geoid_hght − ATL13 geoid (ht_water_surf − ht_ortho), cm"); ax.set_ylabel("nodes")
    ax.set_title(f"SWOT vs ATL13 geoid at the same nodes (n = {len(g)}): not the tide-free offset", loc="left"); ax.legend(fontsize=8)
    ax = axes[1]
    fr = bytab[(bytab.subset == "all zones") & (bytab.dt_window == "≤1 d")].set_index("frame").loc[["naive_ht_ortho", "no_tide", "primary"]]
    y = np.arange(3)
    ax.errorbar(fr.median_m * 100, y, xerr=[(fr.median_m - fr.median_ci_lo) * 100, (fr.median_ci_hi - fr.median_m) * 100], fmt="o", color=C1, ms=8, capsize=4)
    for i, (k, r) in enumerate(fr.iterrows()):
        ax.text(r.median_m * 100, i - 0.28, f"{r.median_m * 100:+.1f} cm (NMAD {r.nmad_m * 100:.1f})", ha="center", fontsize=8, color=INK2)
    ax.set_yticks(y); ax.set_yticklabels(["naive: wse − ht_ortho", "geoid swapped, no crust tide term", "primary: full SWOT frame"])
    ax.axvline(0, color=MUTED, lw=0.8); ax.set_xlabel("median SWOT − ICESat-2, cm (95 % bootstrap CI over crossings)")
    ax.set_title("Which vertical chain closes the gap (|Δt| ≤ 1 d)", loc="left"); ax.set_ylim(-0.6, 2.4)
    fig.tight_layout(); fig.savefig(FIG / "F_Z04_frame_proof.png"); plt.close(fig)


def main():
    t0 = datetime.now(timezone.utc)
    print("SWOT nodes ...", flush=True); sw = load_swot()
    print(f"  {len(sw):,} valid node observations, {sw.source_file.nunique()} files, {sw.node_id.nunique()} nodes", flush=True)
    print("ICESat-2 ...", flush=True); is2 = load_icesat(); print(f"  {len(is2):,} segments", flush=True)
    m, nodes, dam_node = match(sw, is2)
    m.to_parquet(OUT / "swot_icesat_node_matchups.parquet", index=False)
    print(f"  node matchups |dt|<=10 d: {len(m):,}; crossings {m.crossing_id.nunique()}", flush=True)

    rows = []; cr = {}
    for q in (1, 2):
        c = crossings(m, q); cr[q] = c
        for win, sel in (("≤1 d", c.dt_days.abs() <= 1), ("1–3 d", (c.dt_days.abs() > 1) & (c.dt_days.abs() <= 3)),
                         ("3–10 d", c.dt_days.abs() > 3), ("≤3 d", c.dt_days.abs() <= 3)):
            for subset, ss in [("all zones", c)] + [(z, c[c.zone == z]) for z in sorted(c.zone.unique())] + \
                              [(f"period {p}", c[c.period == p]) for p in sorted(c.period.unique())]:
                s = ss[sel.reindex(ss.index)]
                for frame, col in (("primary", "d_primary"), ("naive_ht_ortho", "d_naive_ht_ortho"), ("no_tide", "d_no_tide")):
                    if frame != "primary" and subset != "all zones":
                        continue
                    rows.append(dict(node_q_max=q, dt_window=win, subset=subset, frame=frame, **summarise(s, col)))
    tab = pd.DataFrame(rows)
    tab.to_csv(OUT / "swot_icesat_zone_summary.csv", index=False, float_format="%.6g")
    for q, c in cr.items():
        c.to_csv(OUT / f"swot_icesat_crossings_nodeq{q}.csv", index=False, float_format="%.6g")
    g = m.drop_duplicates("node_id")
    proof = dict(n_nodes=len(g), geoid_diff_median_m=float(g.geoid_swot_minus_is2.median()), geoid_diff_nmad_m=nmad(g.geoid_swot_minus_is2),
                 geoid_theory_median_m=float(g.geoid_theory_mt_minus_tf.median()),
                 free2mean_median_m=float(free2mean(g.lat_is2).median()),
                 expected_naive_offset_m=float((free2mean(g.lat_is2) - g.geoid_theory_mt_minus_tf).median()))
    pd.DataFrame([proof]).to_csv(OUT / "swot_icesat_geoid_frame_proof.csv", index=False, float_format="%.6g")

    primary = tab[(tab.node_q_max == 1)]
    c1 = cr[1][cr[1].dt_days.abs() <= 1]; call = cr[1]
    figures(c1, call, m[(m.node_q <= 1)], nodes, primary)
    manifest = dict(run_utc=t0.isoformat(), script=str(Path(__file__).relative_to(KG)), script_sha256=sha256(Path(__file__)),
                    swot_dir=str(SWOT_DIR), swot_files=int(sw.source_file.nunique()),
                    atl13_inputs={k: dict(path=str(v.relative_to(IN)), sha256=sha256(v)) for k, v in ATL13.items()},
                    dam_node=dict(node_id=str(dam_node.node_id), p_dist_out=float(dam_node.p_dist_out)),
                    rules=dict(max_node_dist_m=MAX_NODE_DIST_M, max_dt_days=MAX_DT_D, primary_node_q_max=1),
                    frame_proof=proof)
    (OUT / "swot_icesat_zone_manifest.json").write_text(json.dumps(manifest, indent=2, default=str))
    pd.set_option("display.width", 250); pd.set_option("display.max_columns", 30)
    print(pd.DataFrame([proof]).T)
    print(primary[primary.frame == "primary"][["dt_window", "subset", "n_crossings", "n_nodes", "median_m", "median_ci_lo", "median_ci_hi",
                                                "nmad_m", "rmse_m", "share_abs_lt_10cm", "pearson_r", "level_range_m"]].round(3).to_string(index=False))
    print(primary[(primary.subset == "all zones")][["dt_window", "frame", "n_crossings", "median_m", "nmad_m"]].round(4).to_string(index=False))


if __name__ == "__main__":
    main()
