#!/usr/bin/env python
"""Paper 1 -- explicit cross-sensor vertical-reference closure experiment.

Gauges (Baltic 1977), ICESat-2 ATL13, SWOT RiverSP/PIXC, one frame: EGG2015 quasigeoid heights for the
satellites, EVRF2019 normal heights for the gauges, and the empirical offset that closes the loop.

Surfaces and conventions (all numbers below are computed, not assumed)
    gauge      H_EVRF2019 = H_BS77 + D9902(x)                          EPSG:9902 grid ua_2019z.asc (zero-tide target)
    ICESat-2   h_MT       = ht_water_surf + free2mean(lat)             ATL13 v007 crust is tide-free; free2mean sign
                                                                       verified against the native field (P0 check 1)
               H_EGG      = h_MT - zeta_EGG2015(x)                      zeta: quasigeoid height, zero-tide, GRS80
    SWOT       h_MT       = wse + geoid_hght                            back to SWOT's own ellipsoidal height through the
                                                                       EGM2008 field SWOT subtracted (crust mean-tide:
                                                                       solid_tide excludes the permanent tide)
               H_EGG      = h_MT - zeta_EGG2015(x)
    Mean-tide and zero-tide crustal heights coincide, so h_MT - zeta is the zero-tide normal height EGG2015 defines.

Closure algebra (written once; D9902 enters exactly once)
    r = (H_BS77 + D9902) - (h_sat,MT - zeta_EGG2015)
    c = median r over the independent units at a control point  = "empirical local vertical closure offset".
    c is NOT a transformation EGG2015 -> EVRF2019: it can hold D(EVRF2007->EVRF2019) + eps_EGG2015 + satellite
    bias + gauge-zero bias + collocation error, and these data cannot separate them.

Reference-surface separation
    D_ref(x) = N_EGM2008(SWOT geoid_hght) - zeta_EGG2015(x)   (geoid vs quasigeoid, different model base and tide
    convention -- reported as a separation, never as a "misclosure"; it cancels in the chain above).
"""
from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import geopandas as gpd
import matplotlib
import numpy as np
import pandas as pd
import rasterio
from pyproj import Transformer
from scipy import stats
from scipy.interpolate import RegularGridInterpolator
from scipy.spatial import cKDTree

sys.path.insert(0, str(Path(__file__).resolve().parent))
import paper1_swot_icesat_zone as Z  # noqa: E402

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

KG = Path(__file__).resolve().parents[1]
VC = KG / "data/paper_1_audit/vertical_closure"
VIN = VC / "inputs"
FIG = KG / "outputs/paper_1/vertical_closure"
FIG.mkdir(parents=True, exist_ok=True)
CIN = KG / "data/paper_1_audit/correlation/inputs"
F_EGG = VIN / "data/1_data/egg_2015.tif"
F_UA = VIN / "data/external/datum/ua_2019z.asc"
F_GAUGE = CIN / "SWOT-DNIPRO/data/processed/gauges/gauge_levels_evrf2019.parquet"
F_MATCH53 = CIN / "icesat2-atl13-kakhovka/outputs/tables/gauge_icesat_egg2015_matchups.csv"
F_STATION = CIN / "icesat2-atl13-kakhovka/outputs/tables/egg2015_to_evrf2019_by_station.csv"
F_PASS_KH = CIN / "icesat2-atl13-kakhovka/data/processed/kherson_atl13_pass_levels.parquet"
F_PASS_RES = CIN / "icesat2-atl13-kakhovka/data/processed/kakhovka_atl13_pass_levels.parquet"
F_PIXC_KH = CIN / "SWOT-DNIPRO/outputs/tables/swot_validation_against_gauge_and_icesat.csv"
F_P59 = VC / "outputs/tables/p59_swot_flood_nodes.parquet"
F_P60 = VC / "outputs/tables/p60_swot_pool_nodes.parquet"
F_P60_OUT = VC / "outputs/tables/p60_swot_outlet_drawdown.csv"
F_FOOT = Z.OUT / "geo/SWOT-DNIPRO/outputs/figure_data/P20_reservoir_footprint.geojson"
F_ZONES = Z.OUT / "geo/SWOT-DNIPRO/data/processed/domains/analysis_zones_utm.geojson"
F_ANC = VIN / "atl13_v007_native_tide_geoid_fields_sample.parquet"   # SlideRule atl13x, anc_fields, 3 reservoir granules (2026-09-23)
TO_UTM = Transformer.from_crs("EPSG:4326", "EPSG:32636", always_xy=True)
GAUGE_R_KM = 3.0
EPSG9902_ACCURACY_M = 0.068
EGG2015_UNCERTAINTY_M = 0.019          # absolute uncertainty of EGG2015 as estimated by Denker et al. (2018)
EXPECTED_UA_SHA = "297440243e2ed8d67b1a61c6099db173b5fb13f85973854198b167c6c67b1a95"


# ----------------------------------------------------------------------------- grids
class Grid:
    """Bilinear sampler over a regular lon/lat grid (EGG2015 GeoTIFF or the ESRI-ASCII EPSG:9902 grid)."""

    def __init__(self, lon, lat, values):
        self.f = RegularGridInterpolator((lat, lon), values, bounds_error=False, fill_value=np.nan)

    def __call__(self, lon, lat):
        return self.f(np.column_stack([np.asarray(lat, float), np.asarray(lon, float)]))


def egg2015(bbox=(31.0, 45.8, 36.0, 48.3)) -> Grid:
    with rasterio.open(F_EGG) as r:
        win = rasterio.windows.from_bounds(*bbox, transform=r.transform).round_offsets().round_lengths()
        a = r.read(1, window=win).astype(float)
        t = r.window_transform(win)
    lon = t.c + t.a * (np.arange(a.shape[1]) + 0.5)
    lat = t.f + t.e * (np.arange(a.shape[0]) + 0.5)
    return Grid(lon, lat[::-1], a[::-1])


def ua9902() -> Grid:
    hdr = {}
    with open(F_UA) as f:
        for _ in range(6):
            k, v = f.readline().split(); hdr[k.lower()] = float(v)
    a = np.loadtxt(F_UA, skiprows=6)
    a[a == hdr["nodata_value"]] = np.nan
    cs = hdr["cellsize"]
    lon = hdr["xllcorner"] + cs * (np.arange(int(hdr["ncols"])) + 0.5)
    lat = hdr["yllcorner"] + cs * (np.arange(int(hdr["nrows"])) + 0.5)
    return Grid(lon, lat, a[::-1])


# ----------------------------------------------------------------------------- closure algebra
def closure_residual(H_bs77, d9902, h_sat_mt, zeta):
    """r = (H_BS77 + D9902) - (h_sat,MT - zeta).  D9902 enters exactly once."""
    return (np.asarray(H_bs77, float) + np.asarray(d9902, float)) - (np.asarray(h_sat_mt, float) - np.asarray(zeta, float))


def nmad(e):
    return Z.nmad(e)


def boot_median(e, n=4000, seed=Z.SEED):
    e = np.asarray(e, float); e = e[np.isfinite(e)]
    if len(e) < 3:
        return np.nan, np.nan
    rng = np.random.default_rng(seed)
    b = [np.median(rng.choice(e, len(e))) for _ in range(n)]
    return float(np.percentile(b, 2.5)), float(np.percentile(b, 97.5))


def summary_row(label, e, **kw):
    lo, hi = boot_median(e)
    e = np.asarray(e, float); e = e[np.isfinite(e)]
    return dict(item=label, n=len(e), median_m=float(np.median(e)) if len(e) else np.nan, ci_lo_m=lo, ci_hi_m=hi,
                nmad_m=nmad(e) if len(e) else np.nan, **kw)


# ----------------------------------------------------------------------------- P0 checks
def p0_checks(ua: Grid, gauges: pd.DataFrame) -> pd.DataFrame:
    rows = []
    if F_ANC.exists():
        g = pd.read_parquet(F_ANC)
        d = g.segment_tide_earth_free2mean - Z.free2mean(g.lat)
        rows.append(dict(check="ATL13 v007 native segment_tide_earth_free2mean vs 0.06029-0.180873 sin^2(lat)",
                         value=f"max |diff| {abs(d).max() * 1000:.4f} mm over {len(g)} segments; native median {g.segment_tide_earth_free2mean.median():+.4f} m",
                         passed=bool(abs(d).max() < 0.001)))
        r = g.ht_ortho - (g.ht_water_surf - g.segment_geoid)
        rows.append(dict(check="ATL13 ht_ortho == ht_water_surf - segment_geoid", value=f"max |diff| {abs(r).max() * 1000:.4f} mm",
                         passed=bool(abs(r).max() < 0.001)))
        gm = g.segment_geoid_free2mean; th = 1.3 * (0.099 - 0.296 * np.sin(np.radians(g.lat)) ** 2)
        rows.append(dict(check="ATL13 segment_geoid_free2mean == (1+k)(0.099-0.296 sin^2), k=0.3",
                         value=f"median {gm.median():+.4f} m vs {th.median():+.4f} m; max |diff| {abs(gm - th).max() * 1000:.2f} mm",
                         passed=bool(abs(gm - th).max() < 0.002)))
    sha = hashlib.sha256(F_UA.read_bytes()).hexdigest()
    a = np.loadtxt(F_UA, skiprows=6)
    rows.append(dict(check="production grid ua_2019z.asc identity", value=f"sha256 {sha}; valid nodes {(a != -9999).sum()}",
                     passed=bool(sha == EXPECTED_UA_SHA and (a != -9999).sum() == 3776)))
    st = gauges.groupby("name_en")[["lon", "lat", "delta_epsg9902_m"]].first()
    dd = ua(st.lon.values, st.lat.values) - st.delta_epsg9902_m.values
    rows.append(dict(check="bilinear sampler reproduces the gauge-table D9902 at all 7 posts",
                     value=f"max |diff| {np.nanmax(np.abs(dd)) * 1000:.3f} mm", passed=bool(np.nanmax(np.abs(dd)) < 0.0005)))
    # closure algebra: D9902 counted once
    r1 = closure_residual(3.0, 0.2, 10.0, -6.0)
    rows.append(dict(check="closure algebra: r = (H_BS77 + D9902) - (h - zeta)", value=f"toy r = {r1:+.3f} (expected -12.800)",
                     passed=bool(abs(r1 + 12.8) < 1e-9)))
    with rasterio.open(F_EGG) as r:
        rows.append(dict(check="EGG2015 raster geometry", value=f"{r.width}x{r.height}, {r.res[0] * 60:.3f}' , bounds {tuple(round(v, 2) for v in r.bounds)}",
                         passed=bool(r.width == 7200 and r.height == 3600)))
    rows.append(dict(check="EGG2015 raster identity", value=f"sha256 {hashlib.sha256(F_EGG.read_bytes()).hexdigest()}", passed=True))
    return pd.DataFrame(rows)


# ----------------------------------------------------------------------------- A. both sensors in EGG2015
def sensors_in_egg(zeta: Grid):
    m = pd.read_parquet(Z.OUT / "swot_icesat_node_matchups.parquet")
    m["zeta_node"] = zeta(m.lon, m.lat)
    m["zeta_is2"] = zeta(m.lon_is2, m.lat_is2)
    m["H_swot_egg"] = m.wse + m.geoid_hght - m.zeta_node
    m["H_is2_egg"] = m.h_mt - m.zeta_is2
    m["d_egg"] = m.H_swot_egg - m.H_is2_egg
    s = m[(m.node_q <= 1) & (m.dark_frac < 0.5)]
    c = s.groupby("crossing_id").agg(dt_days=("dt_days", "median"), d_egg=("d_egg", "median"), d_swotframe=("d_primary", "median"),
                                     s_km=("s_km", "median"), zone=("zone", lambda z: z.mode().iat[0]), period=("period", "first"),
                                     lon=("lon", "median"), lat=("lat", "median"), H_swot_egg=("H_swot_egg", "median"),
                                     H_is2_egg=("H_is2_egg", "median"), n_nodes=("node_id", "nunique")).reset_index()
    c["frame_difference_m"] = c.d_egg - c.d_swotframe
    rows = []
    for win, sel in (("≤1 d", c.dt_days.abs() <= 1), ("1–3 d", (c.dt_days.abs() > 1) & (c.dt_days.abs() <= 3)), ("3–10 d", c.dt_days.abs() > 3)):
        for sub, ss in [("all zones", c)] + [(z, c[c.zone == z]) for z in sorted(c.zone.unique())] + [(f"period {p}", c[c.period == p]) for p in sorted(c.period.unique())]:
            x = ss[sel.reindex(ss.index)]
            rows.append(summary_row(f"SWOT − ICESat-2 in EGG2015 | {win} | {sub}", x.d_egg, dt_window=win, subset=sub,
                                    share_abs_lt_10cm=float((x.d_egg.abs() < 0.10).mean()) if len(x) else np.nan,
                                    rmse_m=float(np.sqrt(np.mean(x.d_egg ** 2))) if len(x) else np.nan))
    return m, c, pd.DataFrame(rows)


# ----------------------------------------------------------------------------- B. reference-surface separation
def reference_separation(zeta: Grid, ua: Grid):
    sw = pd.read_parquet(Z.OUT / "swot_riversp_nodes_zone.parquet")
    n = sw.groupby("node_id").agg(lon=("lon", "median"), lat=("lat", "median"), geoid_hght=("geoid_hght", "median"),
                                  p_dist_out=("p_dist_out", "first")).reset_index()
    n["zeta"] = zeta(n.lon, n.lat)
    n["d9902"] = ua(n.lon, n.lat)
    n["D_ref"] = n.geoid_hght - n.zeta
    try:
        import pyproj
        pyproj.network.set_network_enabled(True)
        t = Transformer.from_crs("EPSG:4979", "EPSG:4326+3855", always_xy=True)
        _, _, H = t.transform(n.lon.values, n.lat.values, np.zeros(len(n)))
        n["N_nga"] = -np.asarray(H)
    except Exception:
        n["N_nga"] = np.nan
    x, y = TO_UTM.transform(n.lon.values, n.lat.values)
    n["x_km"], n["y_km"] = np.asarray(x) / 1e3, np.asarray(y) / 1e3
    A = np.column_stack([np.ones(len(n)), n.x_km - n.x_km.mean(), n.y_km - n.y_km.mean()])
    coef, *_ = np.linalg.lstsq(A, n.D_ref.values, rcond=None)
    resid = n.D_ref.values - A @ coef
    m = pd.read_parquet(Z.OUT / "swot_icesat_node_matchups.parquet").drop_duplicates("node_id")
    rows = [summary_row("D_ref = N_EGM2008 (SWOT geoid_hght) − zeta_EGG2015 at SWOT nodes", n.D_ref,
                        min_m=n.D_ref.min(), max_m=n.D_ref.max(),
                        plane_gradient_cm_per_km=float(np.hypot(coef[1], coef[2]) * 100),
                        plane_residual_nmad_m=nmad(resid)),
            summary_row("NGA EGM2008 2.5′ grid (PROJ us_nga_egm08_25) − zeta_EGG2015", n.N_nga - n.zeta),
            summary_row("SWOT geoid_hght − NGA EGM2008 grid", n.geoid_hght - n.N_nga),
            summary_row("ATL13 segment_geoid − SWOT geoid_hght (same nodes)", -m.geoid_swot_minus_is2),
            summary_row("D9902 (BS-77 → EVRF2019 grid) at SWOT nodes", n.d9902, min_m=n.d9902.min(), max_m=n.d9902.max())]
    lat = np.array([46.3, 47.9])
    tide = pd.DataFrame([dict(term="crust tide-free → mean-tide (ATL03/ATL13 free2mean)", at_46_3_m=Z.free2mean(lat[0]), at_47_9_m=Z.free2mean(lat[1])),
                         dict(term="geoid tide-free → mean-tide, (1+k)(0.099−0.296 sin²φ), k=0.3", at_46_3_m=Z.geoid_mt_minus_tf(lat[0]), at_47_9_m=Z.geoid_mt_minus_tf(lat[1])),
                         dict(term="geoid zero-tide → mean-tide, (0.099−0.296 sin²φ)", at_46_3_m=0.099 - 0.296 * np.sin(np.radians(lat[0])) ** 2,
                              at_47_9_m=0.099 - 0.296 * np.sin(np.radians(lat[1])) ** 2)])
    return n, pd.DataFrame(rows), tide


# ----------------------------------------------------------------------------- C. closure offsets
def closure_offsets(zeta: Grid, ua: Grid, g: pd.DataFrame):
    rows, units = [], []
    # ICESat-2, six reservoir posts: matchup rows at the reported radius, gauge variant A (timed term readings)
    mm = pd.read_csv(F_MATCH53); st = pd.read_csv(F_STATION); rep = dict(zip(st.name_en, st.reported_radius_km))
    mm = mm[mm.apply(lambda r: r.radius_km == rep[r.name_en], axis=1)].copy()
    mm["h_mt_minus_zeta_old"] = mm.h_icesat_egg2015_m                      # upstream: ht_water_surf − zeta (no free2mean)
    mm["H_is2_egg"] = mm.h_icesat_egg2015_m + Z.free2mean(mm.station_lat)
    mm["H_bs77"] = mm.gauge_zero_bs77_m + mm.stage_A_m
    mm["r"] = closure_residual(mm.H_bs77, mm.delta_epsg9902_m, mm.H_is2_egg + 0.0, 0.0)
    for name, d in mm.groupby("name_en"):
        u = d.groupby(["date", "rgt", "beam"]).r.median()
        lo, hi = boot_median(u.values)
        rows.append(dict(control_point=name, sensor="ICESat-2 ATL13", period="PRE_BREACH 2020–2021", n_units=len(u),
                         n_dates=d.date.nunique(), c_m=float(np.median(u)), ci_lo_m=lo, ci_hi_m=hi, nmad_m=nmad(u.values),
                         c_upstream_no_free2mean_m=float(st.loc[st.name_en == name, "c_station_m"].iloc[0]),
                         d9902_m=float(d.delta_epsg9902_m.iloc[0]), lon=float(d.station_lon.iloc[0]), lat=float(d.station_lat.iloc[0])))
    # Kherson and Rozumivka: pass levels (ICESat) and RiverSP nodes (SWOT) within GAUGE_R_KM / 10 km
    gd = g[["date", "name_en", "H_bs77_m", "delta_epsg9902_m", "lat", "lon"]]
    kh = pd.read_parquet(F_PASS_KH); kh["date"] = pd.to_datetime(kh.date)
    kh["zeta"] = zeta(kh.lon_mean, kh.lat_mean)
    kh["h_mt"] = kh.median_wse_evrs_m + kh.zeta + Z.free2mean(kh.lat_mean)   # back to ellipsoid, then crust to mean-tide
    rs = pd.read_parquet(F_PASS_RES); rs = rs[rs.qc_pass].copy(); rs["date"] = pd.to_datetime(rs.date)
    rs["zeta"] = zeta(rs.lon_mean, rs.lat_mean); rs["h_mt"] = rs.median_wse_evrs_m + rs.zeta + Z.free2mean(rs.lat_mean)
    sw = pd.read_parquet(Z.OUT / "swot_riversp_nodes_zone.parquet")
    sw = sw[(sw.node_q <= 1) & (sw.dark_frac < 0.5)].copy()
    sw["date"] = sw.t_swot.dt.tz_localize(None).dt.floor("D")
    sw["zeta"] = zeta(sw.lon, sw.lat); sw["h_mt"] = sw.wse + sw.geoid_hght
    periods = {"PRE_BREACH": (None, "2023-06-05"), "POST_BREACH (≥2023-07-01)": ("2023-07-01", None)}
    for name, passes, rad in (("Kherson", kh, 10.0), ("Rozumivka", rs, 10.0)):
        gg = gd[gd.name_en == name]; la, lo = gg.lat.iloc[0], gg.lon.iloc[0]
        for sensor, src, rkm in (("ICESat-2 ATL13", passes, rad), ("SWOT RiverSP", sw, GAUGE_R_KM)):
            latc = "lat_mean" if "lat_mean" in src else "lat"; lonc = "lon_mean" if "lon_mean" in src else "lon"
            near = src[Z.haversine_km(src[latc].values, src[lonc].values, la, lo) <= rkm] if hasattr(Z, "haversine_km") else None
            if near is None:
                dist = 6371 * 2 * np.arcsin(np.sqrt(np.sin(np.radians(src[latc] - la) / 2) ** 2 + np.cos(np.radians(la)) *
                                                    np.cos(np.radians(src[latc])) * np.sin(np.radians(src[lonc] - lo) / 2) ** 2))
                near = src[dist <= rkm]
            key = ["date", "rgt"] if sensor.startswith("ICESat") else ["date", "cycle", "pass_id"]
            u = near.groupby(key).agg(h_mt=("h_mt", "median"), zeta=("zeta", "median")).reset_index().merge(gg, on="date", how="inner")
            u["r"] = closure_residual(u.H_bs77_m, u.delta_epsg9902_m, u.h_mt, u.zeta)
            for plab, (t0, t1) in periods.items():
                s = u
                if t0: s = s[s.date >= t0]
                if t1: s = s[s.date <= t1]
                if len(s) < 3:
                    continue
                lo_, hi_ = boot_median(s.r.values)
                rows.append(dict(control_point=name, sensor=sensor, period=plab, n_units=len(s), n_dates=s.date.nunique(),
                                 c_m=float(s.r.median()), ci_lo_m=lo_, ci_hi_m=hi_, nmad_m=nmad(s.r.values),
                                 d9902_m=float(gg.delta_epsg9902_m.iloc[0]), lon=lo, lat=la,
                                 support=f"≤{rkm:g} km, same calendar date"))
                units.append(s.assign(control_point=name, sensor=sensor, period=plab))
    # SWOT PIXC at Kherson, 1 km (part3 chain: height − tides − zeta, crust mean-tide)
    v = pd.read_csv(F_PIXC_KH); v = v[v.radius_km == 1.0]
    r = v.H_gauge_evrf2019_m - v.H_SWOT_common_m
    lo_, hi_ = boot_median(r.values)
    kg = gd[gd.name_en == "Kherson"].iloc[0]
    rows.append(dict(control_point="Kherson", sensor="SWOT PIXC (1 km)", period="PRE_BREACH Apr–Jun 2023", n_units=len(v),
                     n_dates=v.date.nunique(), c_m=float(r.median()), ci_lo_m=lo_, ci_hi_m=hi_, nmad_m=nmad(r.values),
                     d9902_m=float(kg.delta_epsg9902_m), lon=float(kg.lon), lat=float(kg.lat), support="1 km, same calendar date"))
    C = pd.DataFrame(rows)
    return C, (pd.concat(units, ignore_index=True) if units else pd.DataFrame()), mm


# ----------------------------------------------------------------------------- D. p59 / p60 recomputation
def recompute_p59_p60(zeta: Grid, c_new: float, c_old: float):
    out = []
    P = pd.read_parquet(F_P60)
    P["H_egg"] = P.wse + P.geoid_hght - P.zeta
    P["H_new"] = P.H_egg + c_new
    o = P[P["where"] == "outlet"].groupby("date").agg(n=("H_new", "size"), H_old=("H_evrf", "median"), H_new=("H_new", "median"),
                                                      H_egg=("H_egg", "median")).reset_index()
    o["date"] = pd.to_datetime(o.date)
    for d in ("2023-05-31", "2023-06-13"):
        r = o[o.date == d]
        if len(r):
            r = r.iloc[0]
            out.append(dict(item=f"SWOT outlet water surface {d}", old_m=r.H_old, new_m=r.H_new, H_egg2015_m=r.H_egg, n_nodes=int(r.n)))
    a, b = o[o.date == "2023-05-31"], o[o.date == "2023-06-13"]
    if len(a) and len(b):
        out.append(dict(item="outlet fall 31 May → 13 June", old_m=float(a.H_old.iloc[0] - b.H_old.iloc[0]),
                        new_m=float(a.H_new.iloc[0] - b.H_new.iloc[0]), H_egg2015_m=np.nan, n_nodes=np.nan))
    N = pd.read_parquet(F_P59); N = N[N.river_name == "Dnipro"].copy()
    N["H_egg"] = N.wse + N.geoid_hght - N.zeta; N["H_new"] = N.H_egg + c_new
    N["bin_km"] = (np.floor(N.s_km / 5.0) * 5.0).astype(int); N["date"] = pd.to_datetime(N.date)
    for col in ("H_evrf", "H_new"):
        prof = N.groupby(["date", "bin_km"])[col].agg(n="size", p50="median").reset_index(); prof = prof[prof.n >= 3]
        pre = prof[(prof.date >= "2023-05-25") & (prof.date <= "2023-06-05")].groupby("bin_km").p50.median()
        pk = prof[prof.date >= "2023-06-06"].sort_values("p50", ascending=False).drop_duplicates("bin_km").set_index("bin_km")
        for bk in (15,):
            if bk in pk.index:
                out.append(dict(item=f"{col}: peak at bin {bk}–{bk + 5} km", old_m=np.nan, new_m=float(pk.loc[bk, "p50"]),
                                H_egg2015_m=np.nan, n_nodes=int(pk.loc[bk, "n"]), date=str(pk.loc[bk, "date"].date())))
                out.append(dict(item=f"{col}: rise at bin {bk}–{bk + 5} km", old_m=np.nan, new_m=float(pk.loc[bk, "p50"] - pre.get(bk, np.nan)),
                                H_egg2015_m=np.nan, n_nodes=np.nan))
    t = pd.DataFrame(out)
    t["c_applied_old_m"], t["c_applied_new_m"] = c_old, c_new
    t["note"] = "old = p59/p60 chain (wse + geoid_hght + free2mean − zeta + c_old); new = wse + geoid_hght − zeta + c_new"
    return t


# ----------------------------------------------------------------------------- figures
def figures(nodes, C, cross, ua: Grid):
    foot = gpd.read_file(F_FOOT).set_crs("EPSG:32636", allow_override=True)
    zones = gpd.read_file(F_ZONES).to_crs("EPSG:32636")
    xmin, ymin, xmax, ymax = zones.total_bounds
    fig, axes = plt.subplots(1, 2, figsize=(20, 7.8))
    x, y = TO_UTM.transform(nodes.lon.values, nodes.lat.values)
    seq = matplotlib.colors.LinearSegmentedColormap.from_list("seq", Z_SEQ)
    for ax, col, lab, title in ((axes[0], "D_ref", "N_EGM2008 (SWOT geoid_hght) − ζ_EGG2015, m", "(a) EGM2008–EGG2015 reference-surface separation at SWOT nodes"),
                                (axes[1], None, "Δ9902 (BS-77 → EVRF2019), m", "(b) EPSG:9902 grid (ua_2019z.asc) over the study zone")):
        zones.boundary.plot(ax=ax, color=Z.MUTED, lw=0.8, ls="--"); foot.boundary.plot(ax=ax, color=Z.INK2, lw=1.0)
        if col:
            sc = ax.scatter(x, y, c=nodes[col], cmap=seq, s=6)
        else:
            gx, gy = np.meshgrid(np.linspace(xmin, xmax, 220), np.linspace(ymin, ymax, 160))
            inv = Transformer.from_crs("EPSG:32636", "EPSG:4326", always_xy=True)
            glon, glat = inv.transform(gx, gy)
            gv = ua(glon.ravel(), glat.ravel()).reshape(gx.shape)
            sc = ax.pcolormesh(gx, gy, gv, cmap=seq, shading="auto", alpha=0.9)
            cp = C.drop_duplicates("control_point")
            px, py = TO_UTM.transform(cp.lon.values, cp.lat.values)
            ax.plot(px, py, "s", color=Z.INK, ms=6)
            for (_, r), xx, yy in zip(cp.iterrows(), px, py):
                ax.annotate(f"{r.control_point} {r.d9902_m:.3f}", (xx, yy), xytext=(4, 4), textcoords="offset points", fontsize=7)
        fig.colorbar(sc, ax=ax, shrink=0.8, label=lab)
        ax.set_xlim(xmin, xmax); ax.set_ylim(ymin, ymax); ax.set_aspect("equal"); ax.grid(False); ax.ticklabel_format(style="plain")
        ax.set_title(title, loc="left"); ax.set_xlabel("UTM 36N easting, m"); ax.set_ylabel("northing, m")
    fig.tight_layout(); fig.savefig(FIG / "F_V01_reference_surfaces_map.png", bbox_inches="tight"); plt.close(fig)

    fig, ax = plt.subplots(figsize=(11, 6))
    order = ["Plavni", "Rozumivka", "Blahovishchenka", "Nikopol", "Velyka Lepetykha", "Nova Kakhovka", "Kherson"]
    sens_col = {"ICESat-2 ATL13": Z.C1, "SWOT RiverSP": Z.C2, "SWOT PIXC (1 km)": Z.C3}
    ylab, yi = [], 0
    for cp in order:
        for _, r in C[C.control_point == cp].iterrows():
            col = sens_col[r.sensor]
            ax.plot([r.ci_lo_m, r.ci_hi_m], [yi, yi], color=col, lw=2)
            ax.plot(r.c_m, yi, "o", color=col, ms=7, mec="white")
            if np.isfinite(r.get("c_upstream_no_free2mean_m", np.nan)):
                ax.plot(r.c_upstream_no_free2mean_m, yi, "x", color=Z.MUTED, ms=7)
            ax.text(0.29, yi, f"n={int(r.n_units)}", va="center", fontsize=7, color=Z.INK2)
            sup = "reported-radius matchups" if pd.isna(r.get("support")) else r.support.split(",")[0]
            ylab.append(f"{cp} · {r.sensor.replace(' ATL13', '')} · {r.period.split(' ')[0].replace('_', '-').lower()} · {sup}"); yi += 1
    for s, col in sens_col.items():
        ax.plot([], [], "o-", color=col, label=s)
    ax.plot([], [], "x", color=Z.MUTED, label="upstream value without free2mean (superseded)")
    ax.axvline(0, color=Z.MUTED, lw=0.8); ax.set_yticks(range(len(ylab))); ax.set_yticklabels(ylab, fontsize=7.5); ax.invert_yaxis()
    ax.set_xlim(-0.35, 0.32); ax.set_xlabel("empirical local vertical closure offset c = (H_BS77 + Δ9902) − (h_MT − ζ_EGG2015), m (95 % CI)")
    ax.set_title("Closure offsets at the control points, both sensors", loc="left"); ax.legend(fontsize=7.5, loc="lower left")
    fig.tight_layout(); fig.savefig(FIG / "F_V02_closure_offsets.png"); plt.close(fig)

    fig, ax = plt.subplots(figsize=(11, 4.6))
    c1 = cross[cross.dt_days.abs() <= 1]
    for per, col in (("PRE_BREACH", Z.C3), ("BREACH_DRAWDOWN", Z.C2), ("POST_BREACH", Z.C1)):
        d = c1[c1.period == per]; ax.plot(d.s_km, d.d_egg, "o", color=col, ms=6, mec="white", label=f"{per} (n={len(d)})")
    ax.axhline(0, color=Z.MUTED, lw=0.8); ax.set_ylim(-0.5, 0.5); ax.axvline(0, color=Z.MUTED, ls=":")
    off = int((c1.d_egg.abs() > 0.5).sum())
    ax.set_title(f"SWOT − ICESat-2, both reduced to EGG2015 (|Δt| ≤ 1 d); {off} point(s) beyond ±0.5 m not shown", loc="left")
    ax.set_xlabel("SWORD distance from dam s, km (negative = former reservoir)"); ax.set_ylabel("m"); ax.legend(fontsize=7.5)
    fig.tight_layout(); fig.savefig(FIG / "F_V03_swot_icesat_egg2015_along_system.png"); plt.close(fig)


Z_SEQ = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"]


def main():
    t0 = datetime.now(timezone.utc)
    zeta, ua = egg2015(), ua9902()
    g = pd.read_parquet(F_GAUGE); g = g[(g.qc == "ok") & (~g.fill_suspect)].copy(); g["date"] = pd.to_datetime(g.date)
    p0 = p0_checks(ua, g); p0.to_csv(VC / "vc_p0_checks.csv", index=False); print(p0.to_string())
    if not p0.passed.all():
        raise SystemExit("P0 check failed -- see vc_p0_checks.csv")
    m, cross, egg_sum = sensors_in_egg(zeta)
    cross.to_csv(VC / "vc_swot_icesat_egg2015_crossings.csv", index=False, float_format="%.6g")
    egg_sum.to_csv(VC / "vc_swot_icesat_egg2015_summary.csv", index=False, float_format="%.6g")
    fd = cross.frame_difference_m
    print(f"frame invariance: |d_egg − d_swotframe| median {fd.abs().median() * 1000:.2f} mm, max {fd.abs().max() * 1000:.2f} mm")
    nodes, sep, tide = reference_separation(zeta, ua)
    sep.to_csv(VC / "vc_reference_surface_separation.csv", index=False, float_format="%.6g")
    tide.to_csv(VC / "vc_permanent_tide_terms.csv", index=False, float_format="%.6g")
    nodes.to_parquet(VC / "vc_reference_surfaces_at_nodes.parquet", index=False)
    C, U, mm = closure_offsets(zeta, ua, g)
    C.to_csv(VC / "vc_closure_offsets.csv", index=False, float_format="%.6g")
    U.to_csv(VC / "vc_closure_units.csv", index=False, float_format="%.6g")
    res = C[(C.sensor == "ICESat-2 ATL13") & C.period.str.startswith("PRE_BREACH 2020")]
    c_new, c_old = float(res.c_m.mean()), float(res.c_upstream_no_free2mean_m.mean())
    agg = pd.DataFrame([dict(item="reservoir, six posts, ICESat-2 (free2mean applied)", mean_m=c_new, median_m=float(res.c_m.median()),
                             sd_m=float(res.c_m.std(ddof=1)), min_m=float(res.c_m.min()), max_m=float(res.c_m.max()),
                             drift_per_deg_lon_m=float(stats.linregress(res.lon, res.c_m).slope),
                             drift_p=float(stats.linregress(res.lon, res.c_m).pvalue), n_stations=len(res),
                             n_units=int(res.n_units.sum())),
                        dict(item="reservoir, six posts, upstream values (no free2mean, superseded)", mean_m=c_old,
                             median_m=float(res.c_upstream_no_free2mean_m.median()), sd_m=float(res.c_upstream_no_free2mean_m.std(ddof=1)),
                             min_m=float(res.c_upstream_no_free2mean_m.min()), max_m=float(res.c_upstream_no_free2mean_m.max()),
                             n_stations=len(res))])
    agg.to_csv(VC / "vc_closure_offset_reservoir_summary.csv", index=False, float_format="%.6g")
    budget = pd.DataFrame([
        dict(line="EPSG:9902 operation accuracy (registry field; not a sigma)", value_m=EPSG9902_ACCURACY_M, kind="published"),
        dict(line="EGG2015 absolute uncertainty (Denker et al. 2018)", value_m=EGG2015_UNCERTAINTY_M, kind="published"),
        dict(line="EVRF2007 → EVRF2019 in Ukraine", value_m=np.nan, kind="not assumed; contained in c"),
        dict(line="D_ref spread across the zone (NMAD)", value_m=float(sep.iloc[0].nmad_m), kind="measured; cancels in the chain"),
        dict(line="c between-station SD, reservoir (ICESat-2)", value_m=float(res.c_m.std(ddof=1)), kind="measured"),
        dict(line="SWOT − ICESat-2 in EGG2015, |Δt| ≤ 1 d, median", value_m=float(egg_sum.iloc[0].median_m), kind="measured"),
        dict(line="SWOT − ICESat-2 in EGG2015, |Δt| ≤ 1 d, NMAD", value_m=float(egg_sum.iloc[0].nmad_m), kind="measured"),
    ])
    budget.to_csv(VC / "vc_uncertainty_budget_lines.csv", index=False, float_format="%.6g")
    pp = recompute_p59_p60(zeta, c_new, float(pd.read_csv(F_STATION).c_station_m.mean()))
    pp.to_csv(VC / "vc_p59_p60_recomputed.csv", index=False, float_format="%.6g")
    figures(nodes, C, cross, ua)
    man = dict(run_utc=t0.isoformat(), script=str(Path(__file__).relative_to(KG)),
               script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
               grids={"ua_2019z.asc": dict(sha256=hashlib.sha256(F_UA.read_bytes()).hexdigest(),
                                           source="CRS-EU HTRANS2019 directory, https://www.crs-geo.eu/crs/descrtrans/HTRANS2019/ua_2019z.asc (downloaded 2026-09-02); file name as in EPSG:9902; not the CRS-EU IDW description file idw_ua_2019_z.asc and not the Stopkhai et al. (2026) UKG2017/EVRF2019zero_AMST raster"),
                      "egg_2015.tif": dict(sha256=hashlib.sha256(F_EGG.read_bytes()).hexdigest(), source="restricted full-resolution EGG2015 1′×1′ copy; provenance per manuscript §7.9")},
               c_reservoir_new_m=c_new, c_reservoir_upstream_m=c_old)
    (VC / "vc_manifest.json").write_text(json.dumps(man, indent=2, default=str))
    pd.set_option("display.width", 250); pd.set_option("display.max_columns", 20)
    print(egg_sum.head(8).round(4).to_string(index=False)); print(sep.round(4).to_string(index=False)); print(tide.round(4).to_string(index=False))
    print(C.round(4).to_string(index=False)); print(agg.round(4).to_string(index=False)); print(pp.round(3).to_string(index=False))


if __name__ == "__main__":
    main()
