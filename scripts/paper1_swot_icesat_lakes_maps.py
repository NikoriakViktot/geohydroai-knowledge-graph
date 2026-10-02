#!/usr/bin/env python
"""Paper 1 -- SWOT LakeSP vs ICESat-2 over the whole water surface (not only the SWORD centreline),
plus the maps for the whole-zone SWOT/ICESat-2 comparison.

Runs after paper1_swot_icesat_zone.py (reuses its loaders, frame functions and RiverSP crossings).

Vertical frame -- identical target to the RiverSP analysis: SWOT's EGM2008 mean-tide geoid.
    LakeSP wse is the median of pixel wse, each pixel referenced to the geoid at its own location, so the
    ICESat-2 point is carried to the same frame with a point-wise SWOT geoid:
        N_ATL13(point) = ht_water_surf - ht_ortho                      (ATL13's own EGM2008 realisation)
        tie(point)     = IDW (k=8) of [geoid_hght_SWOT - N_ATL13] measured at the RiverSP nodes
                         (paper1_swot_icesat_zone.py: median +2.4 cm, NMAD 1.3 cm, +2.6 cm W -> +1.0 cm E;
                          NOT the -7.7 cm a tide-free ATL13 geoid would give, so no theoretical term is used)
        H_IS2 = ht_water_surf + free2mean(lat) - (N_ATL13 + tie)

Frozen rules
    L1  LakeSP Obs polygons: valid wse, quality_f <= 1, dark_frac < 0.5 (primary); all quality as sensitivity.
    L2  ICESat-2 segments are first reduced to 500 m cells per (date, RGT, beam) (median), then kept only
        if the cell centre lies inside the lake polygon.
    L3  Unit = (ICESat-2 date, RGT) x (LakeSP obs polygon); >= 3 ICESat-2 cells required.
    L4  |dt| <= 1 d primary; 1-3 d, 3-10 d sensitivity.  No outlier removal on the difference.
    L5  Lake size class by SWOT area_total: remnant/small <= 20 km2, large > 20 km2.  A lake-averaged
        wse is only meaningful for a level surface: ICESat-2's own p95-p05 inside the polygon is reported
        for every unit so the reader can see where that assumption fails (never used to drop units).
"""
from __future__ import annotations

import glob
import json
import sys
from pathlib import Path

import geopandas as gpd
import matplotlib
import numpy as np
import pandas as pd
import pyogrio
import rasterio
from pyproj import Transformer
from rasterio.merge import merge

sys.path.insert(0, str(Path(__file__).resolve().parent))
import paper1_swot_icesat_zone as Z  # noqa: E402

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.colors import ListedColormap  # noqa: E402

LAKE_DIR = Path("/mnt/f/data_kakhovka_dem_swot/swot_ua/swot_l2_hr_lakesp_2.0")
MASK_DIR = Path("/mnt/f/data_kakhovka_dem_swot/data_swot/processed/water_masks")
GEO = Z.OUT / "geo"
F_FOOT = GEO / "SWOT-DNIPRO/outputs/figure_data/P20_reservoir_footprint.geojson"
F_ZONES = GEO / "SWOT-DNIPRO/data/processed/domains/analysis_zones_utm.geojson"
F_ESTUARY = GEO / "icesat2-atl13-kakhovka/data/aoi/dnipro_estuary.geojson"
MASK_PRE, MASK_POST = "20230605", "20230908"  # 20230908: best post-breach coverage of all three reservoir tiles (0.99/0.93/1.00 valid)
SMALL_KM2 = 20.0
TO_UTM = Transformer.from_crs("EPSG:4326", "EPSG:32636", always_xy=True)


# ----------------------------------------------------------------------------- LakeSP
def load_lakes() -> gpd.GeoDataFrame:
    cache = Z.OUT / "swot_lakesp_obs_zone.parquet"
    if cache.exists():
        return gpd.read_parquet(cache)
    cols = ["obs_id", "lake_id", "time_str", "wse", "wse_u", "wse_std", "area_total", "quality_f", "dark_frac", "partial_f",
            "xovr_cal_q", "geoid_hght", "lake_name"]
    parts = []
    files = sorted(glob.glob(str(LAKE_DIR / "*/*/*LakeSP_Obs_*.shp")))
    for i, f in enumerate(files):
        d = pyogrio.read_dataframe(f, bbox=Z.BBOX, columns=cols)
        d = d[(d.wse > -1e10) & (d.area_total > 0)]
        if len(d):
            nm = Path(f).name.split("_")
            parts.append(d.assign(cycle=int(nm[5]), pass_id=int(nm[6]), source_file=Path(f).name))
        if i % 100 == 0:
            print(f"  lakesp {i}/{len(files)}", flush=True)
    g = gpd.GeoDataFrame(pd.concat(parts, ignore_index=True), geometry="geometry", crs="EPSG:4326")
    g["t_swot"] = pd.to_datetime(g.time_str, utc=True, errors="coerce")
    g = g.dropna(subset=["t_swot"]).reset_index(drop=True)
    g.to_parquet(cache)
    return g


def geoid_tie(lon, lat, k=8):
    """SWOT geoid_hght - ATL13 geoid, measured at RiverSP nodes, interpolated by inverse distance."""
    m = pd.read_parquet(Z.OUT / "swot_icesat_node_matchups.parquet").drop_duplicates("node_id")
    tree = Z.cKDTree(Z.xy(m.lon.values, m.lat.values))
    d, i = tree.query(Z.xy(np.asarray(lon), np.asarray(lat)), k=k)
    w = 1.0 / np.maximum(d, 100.0) ** 2
    return (w * m.geoid_swot_minus_is2.values[i]).sum(1) / w.sum(1), d[:, 0]


def icesat_cells(is2: pd.DataFrame) -> gpd.GeoDataFrame:
    s = is2.copy()
    tie, dnear = geoid_tie(s.lon.values, s.lat.values)
    s["geoid_tie_m"], s["dist_tie_node_km"] = tie, dnear / 1e3
    s["H_is2"] = s.ht_water_surf + Z.free2mean(s.lat) - ((s.ht_water_surf - s.ht_ortho) + s.geoid_tie_m)
    x, y = TO_UTM.transform(s.lon.values, s.lat.values)
    s["cx"], s["cy"] = (np.asarray(x) // 500).astype(int), (np.asarray(y) // 500).astype(int)
    s["is2_date"] = s.t_is2.dt.floor("D")
    c = s.groupby(["is2_date", "rgt", "gt", "cx", "cy"]).agg(t_is2=("t_is2", "median"), H_is2=("H_is2", "median"),
                                                           lon=("lon", "median"), lat=("lat", "median"), n_seg=("H_is2", "size")).reset_index()
    return gpd.GeoDataFrame(c, geometry=gpd.points_from_xy(c.lon, c.lat), crs="EPSG:4326")


def lake_matchups(lakes: gpd.GeoDataFrame, cells: gpd.GeoDataFrame) -> pd.DataFrame:
    # restrict candidates by time first: an ICESat date only meets lakes observed within 10 d
    j = gpd.sjoin(cells, lakes[["obs_id", "lake_id", "t_swot", "wse", "wse_u", "wse_std", "area_total", "quality_f", "dark_frac",
                                "partial_f", "cycle", "pass_id", "geometry"]], predicate="within", how="inner")
    j["dt_days"] = (j.t_swot - j.t_is2).dt.total_seconds() / 86400
    j = j[j.dt_days.abs() <= Z.MAX_DT_D]
    u = j.groupby(["is2_date", "rgt", "obs_id"]).agg(
        lake_id=("lake_id", "first"), t_is2=("t_is2", "median"), t_swot=("t_swot", "first"), dt_days=("dt_days", "median"),
        wse=("wse", "first"), wse_u=("wse_u", "first"), wse_std=("wse_std", "first"), area_km2=("area_total", "first"),
        quality_f=("quality_f", "first"), dark_frac=("dark_frac", "first"), partial_f=("partial_f", "first"),
        cycle=("cycle", "first"), pass_id=("pass_id", "first"),
        H_is2=("H_is2", "median"), is2_p05=("H_is2", lambda v: np.percentile(v, 5)), is2_p95=("H_is2", lambda v: np.percentile(v, 95)),
        n_cells=("H_is2", "size"), lon=("lon", "median"), lat=("lat", "median")).reset_index()
    u = u[u.n_cells >= 3].copy()
    u["is2_range_p95_p05_m"] = u.is2_p95 - u.is2_p05
    u["d_primary"] = u.wse - u.H_is2
    u["size_class"] = np.where(u.area_km2 <= SMALL_KM2, f"small (<= {SMALL_KM2:g} km2)", f"large (> {SMALL_KM2:g} km2)")
    u["period"] = np.select([u.t_is2 < Z.BREACH, u.t_is2 < pd.Timestamp("2023-07-01", tz="UTC")], ["PRE_BREACH", "BREACH_DRAWDOWN"], "POST_BREACH")
    u["wse_swot"] = u.wse
    return u


# ----------------------------------------------------------------------------- map layers
def mask_mosaic(date: str, step: int = 5):
    fs = sorted(glob.glob(str(MASK_DIR / f"*MSIL2A_{date}T*_water.tif")))
    srcs = [rasterio.open(f) for f in fs]
    arr, tr = merge(srcs, nodata=255, method="max" if False else "first")
    for s in srcs:
        s.close()
    a = arr[0][::step, ::step]
    ext = (tr.c, tr.c + tr.a * arr.shape[2], tr.f + tr.e * arr.shape[1], tr.f)
    return a, ext


def base_map(ax, mask, ext, foot, zones, title):
    ax.set_facecolor("white")
    cm = ListedColormap(["#f7f6f2", "#9ec5f4"])
    m = np.ma.masked_where(mask == 255, mask)
    ax.imshow(m, extent=ext, cmap=cm, vmin=0, vmax=1, interpolation="nearest", zorder=0)
    zones.boundary.plot(ax=ax, color=Z.MUTED, lw=0.8, ls="--", zorder=1)
    foot.boundary.plot(ax=ax, color=Z.INK2, lw=1.0, zorder=2)
    for _, z in zones.iterrows():
        p = z.geometry.representative_point()
        ax.annotate(z.analysis_zone.split("_")[0] + "_" + z.analysis_zone.split("_")[1], (p.x, p.y), fontsize=7, color=Z.MUTED, ha="center")
    ax.set_title(title, loc="left"); ax.set_aspect("equal")
    ax.set_xlabel("UTM 36N easting, m"); ax.set_ylabel("northing, m")
    ax.ticklabel_format(style="plain"); ax.grid(False)


def maps(riv: pd.DataFrame, lak: pd.DataFrame, nodes: pd.DataFrame, riv3: pd.DataFrame, lak3: pd.DataFrame):
    foot = gpd.read_file(F_FOOT).set_crs("EPSG:32636", allow_override=True)  # file header says 4326; coordinates and its crs field are 32636
    zones = gpd.read_file(F_ZONES).to_crs("EPSG:32636")
    mpre, epre = mask_mosaic(MASK_PRE); mpost, epost = mask_mosaic(MASK_POST)
    xmin, ymin, xmax, ymax = zones.total_bounds
    lim = 0.3
    norm = matplotlib.colors.Normalize(-lim, lim)
    nx, ny = TO_UTM.transform(nodes.lon.values, nodes.lat.values)
    dstr = lambda d: f"{d[:4]}-{d[4:6]}-{d[6:]}"  # noqa: E731

    def layers(ax, r, l, small=True):
        out = None
        if len(r):
            x, y = TO_UTM.transform(r.lon.values, r.lat.values)
            out = ax.scatter(x, y, c=r.d_primary.clip(-lim, lim), cmap=Z.DIV, norm=norm, s=48, marker="o", edgecolor=Z.INK, lw=0.5,
                             zorder=5, label=f"RiverSP node crossings (n={len(r)})")
        ls = l[l.size_class.str.startswith("small")]; ll = l[l.size_class.str.startswith("large")]
        if len(ls):
            x, y = TO_UTM.transform(ls.lon.values, ls.lat.values)
            out = ax.scatter(x, y, c=ls.d_primary.clip(-lim, lim), cmap=Z.DIV, norm=norm, s=62, marker="D", edgecolor=Z.INK, lw=0.5,
                             zorder=5, label=f"LakeSP water body ≤{SMALL_KM2:g} km² (n={len(ls)})")
        if len(ll):
            x, y = TO_UTM.transform(ll.lon.values, ll.lat.values)
            ax.scatter(x, y, facecolors="none", edgecolors=Z.DIV(norm(ll.d_primary.clip(-lim, lim))), s=70, marker="s", lw=1.6,
                       zorder=4, label=f"LakeSP >{SMALL_KM2:g} km², not level: not a valid test (n={len(ll)})")
        return out

    def furniture(ax):
        ax.plot(nx, ny, ",", color=Z.BASE, zorder=1)
        dx, dy = TO_UTM.transform(*Z.DAM); ax.plot(dx, dy, "^", color=Z.INK, ms=9, zorder=6)
        ax.annotate("dam", (dx, dy), xytext=(6, 4), textcoords="offset points", fontsize=8)
        ax.plot([], [], color=Z.INK2, label="pre-breach pool 2023-06-05 (2 299 km²)")
        ax.plot([], [], color=Z.MUTED, ls="--", label="analysis zones 1–4")
        ax.legend(loc="lower right", fontsize=7.5, frameon=True)

    fig, axes = plt.subplots(1, 2, figsize=(20, 8.6))
    for ax, per, mask, ext, lab in ((axes[0], ("PRE_BREACH", "BREACH_DRAWDOWN"), mpre, epre, f"(a) Pre-breach + drawdown, |Δt| ≤ 1 d\nS2 water {dstr(MASK_PRE)}"),
                                    (axes[1], ("POST_BREACH",), mpost, epost, f"(b) Post-breach, |Δt| ≤ 1 d\nS2 water {dstr(MASK_POST)}")):
        base_map(ax, mask, ext, foot, zones, lab)
        sc = layers(ax, riv[riv.period.isin(per)], lak[lak.period.isin(per)])
        ax.set_xlim(xmin, xmax); ax.set_ylim(ymin, ymax); furniture(ax)
    fig.colorbar(matplotlib.cm.ScalarMappable(norm, Z.DIV), ax=axes, shrink=0.75, label="SWOT − ICESat-2 in SWOT EGM2008 frame, m (clipped ±0.3)")
    fig.suptitle("SWOT vs ICESat-2 over the whole study zone (zones 1–4, nothing clipped). S2 masks cover the reservoir tiles only.",
                 x=0.01, y=0.86, ha="left", fontsize=10)
    fig.savefig(Z.FIG / "F_Z05_map_whole_zone_pre_post.png", bbox_inches="tight"); plt.close(fig)

    fig, ax = plt.subplots(figsize=(14, 8.6))
    base_map(ax, mpost, epost, foot, zones, f"(c) All periods, |Δt| ≤ 3 d — S2 water {dstr(MASK_POST)}")
    layers(ax, riv3, lak3); ax.set_xlim(xmin, xmax); ax.set_ylim(ymin, ymax); furniture(ax)
    fig.colorbar(matplotlib.cm.ScalarMappable(norm, Z.DIV), ax=ax, shrink=0.7, label="SWOT − ICESat-2, m (clipped ±0.3)")
    fig.savefig(Z.FIG / "F_Z08_map_whole_zone_3d.png", bbox_inches="tight"); plt.close(fig)

    # reservoir zoom, all periods, no clipping of the pool: extent = footprint bounds + 5 km
    fig, ax = plt.subplots(figsize=(14, 9.5))
    base_map(ax, mpost, epost, foot, zones, f"(d) Former reservoir, all periods, |Δt| ≤ 3 d\nS2 water {dstr(MASK_POST)}, outline = pre-breach pool")
    fx0, fy0, fx1, fy1 = foot.total_bounds
    layers(ax, riv3, lak3); furniture(ax)
    ax.set_xlim(fx0 - 5000, fx1 + 5000); ax.set_ylim(fy0 - 5000, fy1 + 5000)
    fig.colorbar(matplotlib.cm.ScalarMappable(norm, Z.DIV), ax=ax, shrink=0.7, label="SWOT − ICESat-2, m (clipped ±0.3)")
    fig.savefig(Z.FIG / "F_Z06_map_reservoir_zoom.png", bbox_inches="tight"); plt.close(fig)


def lake_figures(u1: pd.DataFrame, uall: pd.DataFrame):
    fig, axes = plt.subplots(1, 3, figsize=(18, 5))
    ax = axes[0]
    for (cls, col) in ((f"small (<= {SMALL_KM2:g} km2)", Z.C1), (f"large (> {SMALL_KM2:g} km2)", Z.C2)):
        d = u1[u1.size_class == cls]
        ax.plot(d.H_is2, d.wse_swot, "o" if col == Z.C1 else "D", ms=6, color=col, mec="white", mew=0.7, label=f"{cls}: n={len(d)}")
    lo, hi = min(u1.H_is2.min(), u1.wse_swot.min()) - 0.3, max(u1.H_is2.max(), u1.wse_swot.max()) + 0.3
    ax.plot([lo, hi], [lo, hi], "--", color=Z.MUTED, lw=1, label="1:1")
    ax.set_xlabel("ICESat-2 inside polygon (SWOT frame), m"); ax.set_ylabel("SWOT LakeSP wse, m"); ax.legend(fontsize=8)
    ax.set_title("LakeSP vs ICESat-2, |Δt| ≤ 1 d", loc="left")
    ax = axes[1]
    ax.plot(u1.is2_range_p95_p05_m, u1.d_primary, "o", ms=6, color=Z.C1, mec="white", mew=0.7)
    ax.axhline(0, color=Z.MUTED, lw=0.8); ax.set_xscale("symlog", linthresh=0.1)
    ax.set_xlabel("ICESat-2 p95 − p05 inside the lake polygon, m (is the surface level?)"); ax.set_ylabel("SWOT − ICESat-2, m")
    ax.set_title("Lake-averaged wse only works for a level surface", loc="left")
    ax = axes[2]
    bins = [(0, 1, "≤1 d"), (1, 3, "1–3 d"), (3, 10, "3–10 d")]
    data = [uall[(uall.dt_days.abs() > lo) & (uall.dt_days.abs() <= hi)].d_primary.values if lo > 0 else uall[uall.dt_days.abs() <= hi].d_primary.values for lo, hi, _ in bins]
    bp = ax.boxplot(data, tick_labels=[f"{b[2]}\nn={len(d)}" for b, d in zip(bins, data)], widths=0.5, patch_artist=True,
                    medianprops=dict(color=Z.INK, lw=1.5), flierprops=dict(marker="o", ms=3, mfc=Z.MUTED, mec="none"))
    for p in bp["boxes"]:
        p.set_facecolor("#cde2fb"); p.set_edgecolor(Z.C1)
    ax.axhline(0, color=Z.MUTED, lw=0.8); ax.set_ylabel("SWOT − ICESat-2, m"); ax.set_title("LakeSP timing sensitivity", loc="left")
    fig.tight_layout(); fig.savefig(Z.FIG / "F_Z07_lakesp_vs_icesat.png"); plt.close(fig)


def main():
    print("LakeSP ...", flush=True); lakes = load_lakes(); print(f"  {len(lakes):,} lake observations", flush=True)
    lakes_q = lakes[(lakes.quality_f <= 1) & (lakes.dark_frac < 0.5)]
    print("ICESat-2 cells ...", flush=True); cells = icesat_cells(Z.load_icesat()); print(f"  {len(cells):,} 500 m cells", flush=True)
    u = lake_matchups(lakes_q, cells); u.to_csv(Z.OUT / "swot_lakesp_icesat_matchups.csv", index=False, float_format="%.6g")
    ua = lake_matchups(lakes, cells); ua.to_csv(Z.OUT / "swot_lakesp_icesat_matchups_allquality.csv", index=False, float_format="%.6g")
    rows = []
    for qlab, d in (("quality_f<=1", u), ("all quality", ua)):
        for win, sel in (("≤1 d", d.dt_days.abs() <= 1), ("1–3 d", (d.dt_days.abs() > 1) & (d.dt_days.abs() <= 3)), ("3–10 d", d.dt_days.abs() > 3)):
            for sub, ss in [("all lakes", d)] + [(c, d[d.size_class == c]) for c in sorted(d.size_class.unique())] + \
                           [(f"period {p}", d[d.period == p]) for p in sorted(d.period.unique())]:
                s = ss[sel.reindex(ss.index)]
                r = Z.summarise(s.assign(n_nodes=s.n_cells))
                r.pop("n_nodes", None)
                rows.append(dict(product="LakeSP", quality=qlab, dt_window=win, subset=sub, median_is2_range_m=float(s.is2_range_p95_p05_m.median()) if len(s) else np.nan, **r))
    tab = pd.DataFrame(rows); tab.to_csv(Z.OUT / "swot_lakesp_icesat_summary.csv", index=False, float_format="%.6g")

    riv = pd.read_csv(Z.OUT / "swot_icesat_crossings_nodeq1.csv")
    riv1 = riv[riv.dt_days.abs() <= 1]
    nodes = pd.read_parquet(Z.OUT / "swot_riversp_nodes_zone.parquet").groupby("node_id")[["lon", "lat"]].median().reset_index()
    u1 = u[u.dt_days.abs() <= 1]
    print("maps ...", flush=True)
    maps(riv1, u1, nodes, riv[riv.dt_days.abs() <= 3], u[u.dt_days.abs() <= 3])
    lake_figures(u1, u)
    pd.set_option("display.width", 250)
    print(tab[tab.quality == "quality_f<=1"][["dt_window", "subset", "n_crossings", "median_m", "median_ci_lo", "median_ci_hi", "nmad_m",
                                             "rmse_m", "share_abs_lt_10cm", "pearson_r", "median_is2_range_m"]].round(3).to_string(index=False))
    (Z.OUT / "swot_lakesp_manifest.json").write_text(json.dumps(dict(lake_dir=str(LAKE_DIR), n_lake_obs=len(lakes), mask_pre=MASK_PRE,
                                                                   mask_post=MASK_POST, small_km2=SMALL_KM2), indent=2))


if __name__ == "__main__":
    main()
