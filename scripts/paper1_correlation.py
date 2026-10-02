#!/usr/bin/env python
"""Paper 1 -- temporal co-variability across the harmonised gauge / ICESat-2 / SWOT network.

Inputs are read-only copies staged from the Ubuntu-24.04 repos (see inputs/SHA256SUMS and
inputs/SOURCE_COMMITS.txt).  Every number written by this script is recomputed from those
files; nothing is copied from the manuscript.

Frozen rules (fixed before any coefficient was looked at)
--------------------------------------------------------
R1  The daily gauge parquet (gauge_levels_evrf2019) carries NO observation time (k5 audit:
    obs_time_known = False), so every match against it is an exact calendar-date join, no
    interpolation.  The upstream 53-row manuscript matchup set was built from the timed
    08:00/20:00 local TERM readings: variant A = linear between the two bracketing term
    readings (<=12 h; upstream primary, kept primary here), B = nearest term reading,
    C = daily mean interpolated (<=18 h).  All three are reported.
R2  Gauge rows with qc != "ok" or fill_suspect are dropped.  No residual-based outlier removal
    anywhere (the 5 April 2023 SWOT/ICESat mismatch stays in).
R3  Independent unit: one satellite overpass = calendar date (all beams and all stations hit on
    that date share one cluster).  Beams are aggregated by median before any statistic.
R4  Primary satellite<->gauge statistic = WITHIN-STATION centred Pearson/Spearman (station
    medians of the matched sample removed, recomputed inside every bootstrap resample), with a
    cluster bootstrap over overpass dates.  Pooled absolute levels are reported alongside.
R5  Daily gauge<->gauge inference = moving-block bootstrap (primary block 14 d; 7 and 30 d
    sensitivity) + an effective-n p-value (Bretherton et al. 1999 AR(1) form) with
    Benjamini-Hochberg FDR across the exploratory matrix.  Naive p is kept for transparency.
R6  Evidence tier by independent events: >=20 primary-eligible, 10-19 exploratory, <10
    descriptive only (agreement statistics, no correlation claim).
R7  SWOT<->ICESat-2 (3 collocations): no correlation coefficient is computed.
R8  PIXC<->RiverSP is a processing-chain test (same observation) and is not correlated here.
R9  Lagged correlation only for regularly sampled daily series (gauges; SWOT RiverSP fast
    sampling vs Kherson gauge).  Never for sparse ATL13.
R10 ATL13 extended sample: QC-pass PRE_BREACH beam-passes inside gauge coverage
    (2019-01-01..2021-12-31), each assigned to its nearest reservoir gauge; primary support
    radius 20 km (10 km and unrestricted as sensitivity).
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import matplotlib
import numpy as np
import pandas as pd
from scipy import signal, stats

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

KG = Path(__file__).resolve().parents[1]
OUT = KG / "data/paper_1_audit/correlation"
IN = OUT / "inputs"
FIG = KG / "outputs/paper_1/correlation"
OUT.mkdir(parents=True, exist_ok=True)
FIG.mkdir(parents=True, exist_ok=True)

SW = IN / "SWOT-DNIPRO"
IS = IN / "icesat2-atl13-kakhovka"
F_GAUGE = SW / "data/processed/gauges/gauge_levels_evrf2019.parquet"
F_MATCH53 = IS / "outputs/tables/gauge_icesat_egg2015_matchups.csv"
F_STATION = IS / "outputs/tables/egg2015_to_evrf2019_by_station.csv"
F_PASS_RES = IS / "data/processed/kakhovka_atl13_pass_levels.parquet"
F_PASS_KH = IS / "data/processed/kherson_atl13_pass_levels.parquet"
F_PIXC_KH = SW / "outputs/tables/swot_validation_against_gauge_and_icesat.csv"
F_RIVERSP_KH = SW / "outputs/tables/p59_swot_vs_kherson.csv"
F_SWOT_IS2 = SW / "outputs/tables/part4_colocated_swot_icesat.csv"

SEED = 20260923
N_BOOT = 4000
RES_ORDER = ["Plavni", "Rozumivka", "Blahovishchenka", "Nikopol", "Velyka Lepetykha", "Nova Kakhovka"]
ALL_ORDER = RES_ORDER + ["Kherson"]
GAUGE_WINDOW = ("2019-01-01", "2021-12-31")
BREACH = pd.Timestamp("2023-06-06")

FRAME_GAUGE = "EVRF2019 normal height (EPSG:9389 zero-tide) = BS-77 zero + stage + EPSG:9902 grid"
FRAME_ATL13 = "EGG2015 normal height (ATL13 ht_water_surf - zeta_EGG2015, upstream pipeline); no station corrector c"
FRAME_SWOT = "EGG2015 normal height (SWOT common frame, part3/p59 chain); no reservoir corrector c"

# chart tokens (dataviz reference palette, light mode -- print figures)
INK, INK2, MUTED, GRID, BASE = "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#c3c2b7"
C1, C2, C3 = "#2a78d6", "#eb6834", "#1baf7a"
DIV_STOPS = ["#104281", "#3987e5", "#f0efec", "#e34948", "#9e2b2a"]  # diverging blue<->red, gray midpoint
SEQ = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"]
plt.rcParams.update({
    "figure.facecolor": "white", "axes.facecolor": "white", "axes.edgecolor": BASE, "axes.labelcolor": INK2,
    "xtick.color": MUTED, "ytick.color": MUTED, "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.6,
    "axes.spines.top": False, "axes.spines.right": False, "font.size": 9, "axes.titlesize": 10,
    "axes.titleweight": "bold", "axes.titlecolor": INK, "legend.frameon": False, "savefig.dpi": 200,
})

rng_global = np.random.default_rng(SEED)
DIV = matplotlib.colors.LinearSegmentedColormap.from_list("div", DIV_STOPS)


# ----------------------------------------------------------------------------- helpers
def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def nmad(e) -> float:
    e = np.asarray(e, float)
    return float(1.4826 * np.median(np.abs(e - np.median(e))))


def tier(n_ind: int) -> str:
    return "primary-eligible" if n_ind >= 20 else ("exploratory" if n_ind >= 10 else "descriptive-only")


def haversine_km(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = map(np.radians, (lat1, lon1, lat2, lon2))
    a = np.sin((lat2 - lat1) / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2
    return 6371.0 * 2 * np.arcsin(np.sqrt(a))


def assoc(x, y) -> dict:
    """Association + agreement for paired x (reference/gauge) and y (satellite or second gauge)."""
    x = np.asarray(x, float); y = np.asarray(y, float)
    ok = np.isfinite(x) & np.isfinite(y); x, y = x[ok], y[ok]
    out = {"n_rows": int(len(x))}
    if len(x) < 3 or np.std(x) == 0 or np.std(y) == 0:
        return out
    pr, sp, kt = stats.pearsonr(x, y), stats.spearmanr(x, y), stats.kendalltau(x, y, variant="b")
    e = y - x
    out.update(pearson_r=pr.statistic, pearson_p_naive=pr.pvalue, spearman_rho=sp.statistic, spearman_p_naive=sp.pvalue,
               kendall_tau_b=kt.statistic, kendall_p_naive=kt.pvalue, median_bias_m=float(np.median(e)),
               mean_bias_m=float(np.mean(e)), nmad_m=nmad(e), mae_m=float(np.mean(np.abs(e))),
               rmse_m=float(np.sqrt(np.mean(e ** 2))), sd_x_m=float(np.std(x, ddof=1)), sd_y_m=float(np.std(y, ddof=1)),
               theilsen_slope=float(stats.theilslopes(y, x).slope))
    return out


def centre(df: pd.DataFrame, by: str, cols=("x", "y")) -> pd.DataFrame:
    d = df.copy()
    for c in cols:
        d[c + "_anom"] = d[c] - d.groupby(by)[c].transform("median")
    return d


def cluster_boot(df: pd.DataFrame, cluster: str, centred_by: str | None, n_boot=N_BOOT, seed=SEED) -> dict:
    """Cluster bootstrap of Pearson/Spearman; centring recomputed inside each resample."""
    rng = np.random.default_rng(seed)
    X, Y = df.x.to_numpy(float), df.y.to_numpy(float)
    ccodes = pd.factorize(df[cluster])[0]
    members = [np.flatnonzero(ccodes == k) for k in range(ccodes.max() + 1)]
    scodes = pd.factorize(df[centred_by])[0] if centred_by else None
    pr, sr, bias = [], [], []
    for _ in range(n_boot):
        idx = np.concatenate([members[k] for k in rng.integers(0, len(members), len(members))])
        x, y = X[idx], Y[idx]
        bias.append(np.median(y - x))
        if centred_by:
            sc = scodes[idx]; x = x.copy(); y = y.copy()
            for s in np.unique(sc):
                m = sc == s
                x[m] -= np.median(x[m]); y[m] -= np.median(y[m])
        if np.std(x) == 0 or np.std(y) == 0:
            continue
        pr.append(np.corrcoef(x, y)[0, 1])
        sr.append(np.corrcoef(stats.rankdata(x), stats.rankdata(y))[0, 1])
    q = lambda a: np.nanpercentile(a, [2.5, 97.5]) if len(a) else (np.nan, np.nan)  # noqa: E731
    (plo, phi), (slo, shi), (blo, bhi) = q(pr), q(sr), q(bias)
    return dict(pearson_ci_lo=plo, pearson_ci_hi=phi, spearman_ci_lo=slo, spearman_ci_hi=shi,
                bias_ci_lo_m=blo, bias_ci_hi_m=bhi, n_boot_valid=len(pr), boot_scheme=f"cluster:{cluster}")


def block_boot(x, y, block: int, n_boot=N_BOOT, seed=SEED) -> dict:
    x = np.asarray(x, float); y = np.asarray(y, float)
    n = len(x); rng = np.random.default_rng(seed)
    starts = np.arange(0, n - block + 1); nb = int(np.ceil(n / block))
    pr = np.empty(n_boot); sr = np.empty(n_boot)
    rx, ry = stats.rankdata(x), stats.rankdata(y)
    for b in range(n_boot):
        idx = (rng.choice(starts, nb)[:, None] + np.arange(block)).ravel()[:n]
        pr[b] = np.corrcoef(x[idx], y[idx])[0, 1]
        sr[b] = np.corrcoef(rx[idx], ry[idx])[0, 1]   # rank corr on resampled ranks (ties from resampling ignored)
    return dict(pearson_ci_lo=np.nanpercentile(pr, 2.5), pearson_ci_hi=np.nanpercentile(pr, 97.5),
                spearman_ci_lo=np.nanpercentile(sr, 2.5), spearman_ci_hi=np.nanpercentile(sr, 97.5),
                boot_scheme=f"moving-block:{block}d")


def lag1(a) -> float:
    a = np.asarray(a, float) - np.mean(a)
    return float(np.sum(a[1:] * a[:-1]) / np.sum(a * a))


def neff_p(x, y, r) -> tuple[float, float]:
    r1, r2 = lag1(x), lag1(y)
    n_eff = len(x) * (1 - r1 * r2) / (1 + r1 * r2)
    n_eff = float(np.clip(n_eff, 3, len(x)))
    t = r * np.sqrt((n_eff - 2) / max(1e-12, 1 - r * r))
    return n_eff, float(2 * stats.t.sf(abs(t), n_eff - 2))


def bh(p: pd.Series) -> pd.Series:
    p = p.astype(float); ok = p.notna(); q = pd.Series(np.nan, index=p.index)
    v = p[ok].sort_values(); m = len(v)
    adj = (v * m / np.arange(1, m + 1))[::-1].cummin()[::-1].clip(upper=1)
    q[adj.index] = adj
    return q


# ----------------------------------------------------------------------------- loaders
def load_gauges() -> pd.DataFrame:
    g = pd.read_parquet(F_GAUGE)
    g = g[(g.qc == "ok") & (~g.fill_suspect)].copy()
    g["date"] = pd.to_datetime(g.date)
    return g


def gauge_wide(g: pd.DataFrame) -> pd.DataFrame:
    w = g.pivot_table(index="date", columns="name_en", values="H_evrf2019_m", aggfunc="first")
    return w.reindex(columns=[c for c in ALL_ORDER if c in w.columns]).asfreq("D")


# ----------------------------------------------------------------------------- A. gauge <-> gauge
def transforms(a: pd.Series, b: pd.Series) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    d = pd.concat([a.rename("a"), b.rename("b")], axis=1)
    both = d.dropna()
    t = np.arange(len(both))
    raw = (both.a.values, both.b.values)
    det = tuple(v - np.polyval(np.polyfit((both.index - both.index[0]).days, v, 1), (both.index - both.index[0]).days) for v in raw)
    dd = d.diff()  # consecutive calendar days only (asfreq D keeps gaps as NaN)
    dd = dd.dropna()
    del t
    return {"raw": raw, "detrended": det, "first_difference": (dd.a.values, dd.b.values)}


def gauge_gauge(w: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    rows, boots = [], []
    periods = {"2019-2021 (pre-breach, all gauges)": GAUGE_WINDOW,
               "2023-07-01..2025-12-31 (post-breach)": ("2023-07-01", "2025-12-31")}
    for plabel, (t0, t1) in periods.items():
        ww = w.loc[t0:t1]
        cols = [c for c in ww.columns if ww[c].notna().sum() > 60]
        for i, a in enumerate(cols):
            for b in cols[i + 1:]:
                for tname, (x, y) in transforms(ww[a], ww[b]).items():
                    if len(x) < 60:
                        continue
                    s = assoc(x, y)
                    n_eff, p_eff = neff_p(x, y, s["pearson_r"])
                    row = dict(analysis="gauge_gauge", period=plabel, series_a=a, series_b=b, transform=tname,
                               support="pairwise-complete common daily dates", n_independent=round(n_eff),
                               n_eff=n_eff, pearson_p_neff=p_eff, **s)
                    for blk in (7, 14, 30):
                        bb = block_boot(x, y, blk, n_boot=2000)
                        boots.append(dict(analysis="gauge_gauge", period=plabel, series_a=a, series_b=b, transform=tname, **bb))
                        if blk == 14:
                            row.update(bb)
                    if tname != "raw":   # agreement on transformed series is meaningless
                        for k in ("median_bias_m", "mean_bias_m", "nmad_m", "mae_m", "rmse_m"):
                            row[k] = np.nan
                    rows.append(row)
    df = pd.DataFrame(rows)
    df["pearson_p_neff_fdr"] = np.nan
    for (per, tr), idx in df.groupby(["period", "transform"]).groups.items():
        df.loc[idx, "pearson_p_neff_fdr"] = bh(df.loc[idx, "pearson_p_neff"])
    return df, pd.DataFrame(boots)


def lag_analysis(w: pd.DataFrame, max_lag=14, n_boot=500):
    ww = w.loc[GAUGE_WINDOW[0]:GAUGE_WINDOW[1]]
    ref_list = ["Nova Kakhovka", "Kherson"]
    curves, summ = [], []
    lags = np.arange(-max_lag, max_lag + 1)
    rng = np.random.default_rng(SEED)
    for ref in ref_list:
        for st in [c for c in ALL_ORDER if c != ref and c in ww.columns]:
            for tname in ("raw", "detrended", "first_difference"):
                a, b = ww[st], ww[ref]
                if tname == "first_difference":
                    a, b = a.diff(), b.diff()
                elif tname == "detrended":
                    def dtr(s):
                        v = s.dropna(); tt = (v.index - v.index[0]).days
                        return (v - np.polyval(np.polyfit(tt, v.values, 1), tt)).reindex(s.index)
                    a, b = dtr(a), dtr(b)
                A = a.values
                Bs = np.vstack([b.shift(-k).values for k in lags])  # corr(a_t, b_{t+k}); k>0: ref lags station
                def corr_all(idx):
                    out = np.full(len(lags), np.nan)
                    xa = A[idx]
                    for j in range(len(lags)):
                        yb = Bs[j, idx]; m = np.isfinite(xa) & np.isfinite(yb)
                        if m.sum() > 30:
                            out[j] = np.corrcoef(xa[m], yb[m])[0, 1]
                    return out
                full = corr_all(np.arange(len(A)))
                n = len(A); blk = 14; starts = np.arange(n - blk + 1); nb = int(np.ceil(n / blk))
                peaks = []
                for _ in range(n_boot):
                    idx = (rng.choice(starts, nb)[:, None] + np.arange(blk)).ravel()[:n]
                    c = corr_all(idx)
                    if np.isfinite(c).any():
                        peaks.append(lags[np.nanargmax(c)])
                peaks = np.array(peaks)
                k0 = int(lags[np.nanargmax(full)])
                half = full[np.isfinite(full)]
                width = int(np.sum(full >= np.nanmax(full) - 0.02))  # plateau width within 0.02 of peak
                for L, r in zip(lags, full):
                    curves.append(dict(reference=ref, station=st, transform=tname, lag_days=int(L), pearson_r=r))
                summ.append(dict(reference=ref, station=st, transform=tname, lag_search="±14 d", peak_lag_days=k0,
                                 r_peak=float(np.nanmax(full)), r_zero_lag=float(full[lags == 0][0]),
                                 plateau_width_days_within_0p02=width,
                                 boot_peak_lag_p2p5=float(np.percentile(peaks, 2.5)), boot_peak_lag_p97p5=float(np.percentile(peaks, 97.5)),
                                 boot_share_peak_at_zero=float(np.mean(peaks == 0)), n_boot=len(peaks),
                                 interpretation=("synchronous (localised zero-lag peak)" if (k0 == 0 and width <= 3 and np.mean(peaks == 0) >= 0.8)
                                                 else "persistence plateau - not a travel time" if width > 3
                                                 else f"peak at {k0} d - inspect bootstrap stability")))
                del half
    return pd.DataFrame(curves), pd.DataFrame(summ)


def coherence_table(w: pd.DataFrame) -> pd.DataFrame:
    ww = w.loc[GAUGE_WINDOW[0]:GAUGE_WINDOW[1]]
    rows = []
    bands = {"period > 60 d": (0, 1 / 60), "14-60 d": (1 / 60, 1 / 14), "3-14 d": (1 / 14, 1 / 3)}
    ref = "Nova Kakhovka"
    for st in [c for c in ALL_ORDER if c != ref]:
        d = ww[[st, ref]].interpolate(limit=3, limit_area="inside").dropna()  # only gaps <=3 d bridged
        # longest contiguous run
        runs = (d.index.to_series().diff() != pd.Timedelta("1D")).cumsum()
        seg = d[runs == runs.value_counts().idxmax()]
        x, y = signal.detrend(seg[st].values), signal.detrend(seg[ref].values)
        f, C = signal.coherence(x, y, fs=1.0, nperseg=min(256, len(x) // 4))
        row = dict(station=st, reference=ref, n_days_contiguous=len(seg), segment=f"{seg.index[0].date()}..{seg.index[-1].date()}",
                   nperseg=min(256, len(x) // 4))
        for bname, (lo, hi) in bands.items():
            m = (f > lo) & (f <= hi)
            row[f"mean_coherence_{bname}"] = float(C[m].mean()) if m.any() else np.nan
        rows.append(row)
    return pd.DataFrame(rows)


# ----------------------------------------------------------------------------- B. ATL13 <-> gauge
def atl13_manuscript_set(g: pd.DataFrame):
    m = pd.read_csv(F_MATCH53)
    st = pd.read_csv(F_STATION)
    rep = dict(zip(st.name_en, st.reported_radius_km))
    checks = []
    out_rows, units_all = [], {}
    for variant, col, note in (("A_term_interpolated", "h_gauge_evrf2019_A_m", "primary: linear between bracketing 08/20 term readings"),
                               ("B_term_nearest", "h_gauge_evrf2019_B_m", "sensitivity: nearest term reading <=12 h"),
                               ("C_daily_mean", "h_gauge_evrf2019_C_m", "sensitivity: daily mean interpolated <=18 h")):
        for radius_rule in ("reported", 1.0, 2.0, 3.0, 5.0):
            if radius_rule == "reported":
                s = m[m.apply(lambda r: r.radius_km == rep[r.name_en], axis=1)].copy()
            else:
                s = m[m.radius_km == radius_rule].copy()
            if variant == "A_term_interpolated" and radius_rule == "reported":
                checks.append(dict(check="ATL13-gauge matchup rows at reported radius", expected="53", reproduced=str(len(s)),
                                   ok=len(s) == 53))
                for name, gg in s.groupby("name_en"):
                    c_rep = float(st.loc[st.name_en == name, "c_station_m"].iloc[0])
                    c_now = float(np.median(gg.c_station_A_m))
                    checks.append(dict(check=f"c_station {name}", expected=f"{c_rep:+.4f}", reproduced=f"{c_now:+.4f}",
                                       ok=abs(c_rep - c_now) < 0.005))
            # aggregate beams -> station x overpass (R3)
            u = s.groupby(["name_en", "date", "rgt"]).agg(y=("h_icesat_egg2015_m", "median"), x=(col, "median"),
                                                          n_beams=("beam", "nunique")).reset_index()
            u["overpass_id"] = u.date
            key = f"{variant}|{radius_rule}"
            units_all[key] = u
            for mode, cb in (("pooled_absolute", None), ("within_station_centred", "name_en")):
                if mode == "within_station_centred":
                    uc = centre(u, "name_en"); a = assoc(uc.x_anom, uc.y_anom)
                    a.update({k: assoc(u.x, u.y).get(k) for k in ("median_bias_m", "mean_bias_m", "nmad_m", "mae_m", "rmse_m")})
                else:
                    a = assoc(u.x, u.y)
                bb = cluster_boot(u, "overpass_id", cb)
                out_rows.append(dict(analysis="atl13_gauge_reservoir_manuscript_set", gauge_variant=variant, variant_note=note,
                                     support=f"radius={radius_rule} km", mode=mode, n_beam_rows=len(s),
                                     n_station_overpasses=len(u), n_independent=u.overpass_id.nunique(),
                                     n_stations=u.name_en.nunique(), **a, **bb))
    # per-station (reported radius, A)
    u = units_all["A_term_interpolated|reported"]
    per = []
    for name, gg in u.groupby("name_en"):
        a = assoc(gg.x, gg.y)
        bb = cluster_boot(gg, "overpass_id", None, n_boot=2000) if len(gg) >= 4 else {}
        per.append(dict(analysis="atl13_gauge_per_station_manuscript_set", station=name, n_independent=len(gg), **a, **bb))
    return pd.DataFrame(out_rows), pd.DataFrame(per), units_all, checks


def atl13_extended(g: pd.DataFrame):
    p = pd.read_parquet(F_PASS_RES)
    p = p[p.qc_pass & (p.period == "PRE_BREACH")].copy()
    p["date"] = pd.to_datetime(p.date)
    p = p[(p.date >= GAUGE_WINDOW[0]) & (p.date <= GAUGE_WINDOW[1])]
    st = g[g.name_en.isin(RES_ORDER)].groupby("name_en")[["lat", "lon"]].first()
    D = np.column_stack([haversine_km(p.lat_mean.values, p.lon_mean.values, la, lo) for la, lo in zip(st.lat, st.lon)])
    p["station"] = st.index.values[D.argmin(1)]; p["dist_km"] = D.min(1)
    u = p.groupby(["date", "rgt", "station"]).agg(y=("median_wse_evrs_m", "median"), n_beams=("beam", "nunique"),
                                                  dist_km=("dist_km", "median"), within_pass_nmad_m=("nmad_m", "median")).reset_index()
    gd = g[g.name_en.isin(RES_ORDER)][["date", "name_en", "H_evrf2019_m"]]
    u = u.merge(gd.rename(columns={"name_en": "station", "H_evrf2019_m": "x"}), on=["date", "station"], how="inner")
    net = gd.groupby("date").H_evrf2019_m.agg(x_network="median", n_gauges="count").reset_index()
    u = u.merge(net, on="date", how="left")
    u["overpass_id"] = u.date
    rows = []
    for lab, dmax in (("dist<=10 km", 10), ("dist<=20 km (primary)", 20), ("all (nearest gauge)", 1e9)):
        s = u[u.dist_km <= dmax].copy()
        for ref in ("nearest_station", "network_median"):
            ss = s.copy()
            if ref == "network_median":
                ss["x"] = ss.x_network
            for mode, cb in (("pooled_absolute", None), ("within_station_centred", "station")):
                if mode == "within_station_centred":
                    sc = centre(ss, "station"); a = assoc(sc.x_anom, sc.y_anom)
                    a.update({k: assoc(ss.x, ss.y).get(k) for k in ("median_bias_m", "mean_bias_m", "nmad_m", "mae_m", "rmse_m")})
                else:
                    a = assoc(ss.x, ss.y)
                bb = cluster_boot(ss, "overpass_id", cb)
                rows.append(dict(analysis="atl13_gauge_reservoir_extended", support=lab, gauge_reference=ref, mode=mode,
                                 n_station_overpasses=len(ss), n_independent=ss.overpass_id.nunique(),
                                 n_stations=ss.station.nunique(), **a, **bb))
    # first differences between successive overpasses at the same station (same RGT not required)
    s = u[u.dist_km <= 20].sort_values(["station", "date"]).copy()
    s["dx"] = s.groupby("station").x.diff(); s["dy"] = s.groupby("station").y.diff()
    s["gap_d"] = s.groupby("station").date.diff().dt.days
    fd = s[(s.gap_d > 0) & (s.gap_d <= 31)].dropna(subset=["dx", "dy"])
    a = assoc(fd.dx, fd.dy)
    for k in ("median_bias_m", "mean_bias_m", "nmad_m", "mae_m", "rmse_m"):
        a.pop(k, None)
    bb = cluster_boot(fd.assign(x=fd.dx, y=fd.dy), "overpass_id", None)
    rows.append(dict(analysis="atl13_gauge_reservoir_extended", support="dist<=20 km, successive visits <=31 d apart",
                     gauge_reference="nearest_station", mode="first_difference", n_station_overpasses=len(fd),
                     n_independent=fd.overpass_id.nunique(), n_stations=fd.station.nunique(), **a, **bb))
    per = []
    s = u[u.dist_km <= 20]
    for name, gg in s.groupby("station"):
        a = assoc(gg.x, gg.y)
        bb = cluster_boot(gg, "overpass_id", None, n_boot=2000)
        per.append(dict(analysis="atl13_gauge_per_station_extended", station=name, n_independent=gg.overpass_id.nunique(), **a, **bb))
    return pd.DataFrame(rows), pd.DataFrame(per), u, p


def atl13_kherson(g: pd.DataFrame):
    k = pd.read_parquet(F_PASS_KH)
    k["date"] = pd.to_datetime(k.date)
    kg = g[g.name_en == "Kherson"]
    la, lo = kg.lat.iloc[0], kg.lon.iloc[0]
    k["dist_km"] = haversine_km(k.lat_mean.values, k.lon_mean.values, la, lo)
    u = k.groupby(["date", "rgt"]).agg(y=("median_wse_evrs_m", "median"), n_beams=("beam", "nunique"),
                                       dist_km=("dist_km", "median"), period=("period", "first")).reset_index()
    u = u.merge(kg[["date", "H_evrf2019_m"]].rename(columns={"H_evrf2019_m": "x"}), on="date", how="inner")
    u["overpass_id"] = u.date; u["station"] = "Kherson"
    rows = []
    for plab, sel in (("PRE_BREACH", u.date < BREACH), ("POST_BREACH (>=2023-07-01)", u.date >= "2023-07-01")):
        for lab, dmax in (("dist<=5 km", 5), ("dist<=10 km (primary)", 10), ("dist<=20 km", 20)):
            s = u[sel & (u.dist_km <= dmax)]
            if len(s) < 3:
                continue
            a = assoc(s.x, s.y); bb = cluster_boot(s, "overpass_id", None)
            rows.append(dict(analysis="atl13_gauge_kherson", period=plab, support=lab, mode="single_station",
                             n_station_overpasses=len(s), n_independent=s.overpass_id.nunique(), **a, **bb))
    return pd.DataFrame(rows), u


# ----------------------------------------------------------------------------- C. SWOT <-> Kherson
def swot_kherson(g: pd.DataFrame, checks: list):
    v = pd.read_csv(F_PIXC_KH); v["date"] = pd.to_datetime(v.date)
    rows = []
    for r, s in v.groupby("radius_km"):
        s = s.assign(x=s.H_gauge_evrf2019_m, y=s.H_SWOT_common_m, overpass_id=s.date)
        a = assoc(s.x, s.y); bb = cluster_boot(s, "overpass_id", None)
        rows.append(dict(analysis="swot_pixc_gauge_kherson", period="2023-04-05..2023-06-05 (pre-breach)",
                         support=f"radius={r} km" + (" (manuscript)" if r == 1.0 else ""), mode="single_station",
                         n_independent=len(s), **a, **bb))
        if r == 1.0:
            checks.append(dict(check="SWOT-Kherson median bias (1 km)", expected="+0.0264", reproduced=f"{a['median_bias_m']:+.4f}",
                               ok=abs(a["median_bias_m"] - 0.0264) < 5e-4))
            checks.append(dict(check="SWOT-Kherson NMAD (1 km)", expected="0.0413", reproduced=f"{a['nmad_m']:.4f}",
                               ok=abs(a["nmad_m"] - 0.0413) < 5e-4))
    pixc = v[v.radius_km == 1.0].assign(x=lambda d: d.H_gauge_evrf2019_m, y=lambda d: d.H_SWOT_common_m)

    # RiverSP fast-sampling series: remove the reservoir corrector that p59 added (it does not transfer
    # to Kherson -- manuscript §5.6) and use the project's own Kherson EVRF2019 gauge series.
    c_res = float(pd.read_csv(F_STATION).c_station_m.mean())
    q = pd.read_csv(F_RIVERSP_KH); q["date"] = pd.to_datetime(q.date)
    kg = g[g.name_en == "Kherson"][["date", "H_evrf2019_m"]]
    q = q.merge(kg, on="date", how="left")
    q["y"] = q.swot_p50 - c_res; q["x"] = q.H_evrf2019_m
    q = q.dropna(subset=["y", "x"]).set_index("date")
    segs = {"pre-breach 05-26..06-05": ("2023-05-26", "2023-06-05"), "breach+recession 06-06..07-10": ("2023-06-06", "2023-07-10"),
            "recession 06-17..07-10 (after 06-17)": ("2023-06-17", "2023-07-10"), "full 05-26..07-10": ("2023-05-26", "2023-07-10")}
    for lab, (t0, t1) in segs.items():
        s = q.loc[t0:t1]
        a = assoc(s.x, s.y); bb = cluster_boot(s.reset_index().assign(overpass_id=lambda d: d.date), "overpass_id", None)
        rows.append(dict(analysis="swot_riversp_gauge_kherson", period=lab, support="RiverSP nodes <3 km, node_q<=1",
                         mode="levels (event-dominated where breach included)", n_independent=len(s), **a, **bb))
        full = q.loc[t0:t1].asfreq("D")
        d = full[["x", "y"]].diff().dropna()
        if len(d) >= 3:
            a = assoc(d.x, d.y)
            for k in ("median_bias_m", "mean_bias_m", "nmad_m", "mae_m", "rmse_m"):
                a.pop(k, None)
            bb = cluster_boot(d.reset_index().assign(overpass_id=lambda z: z.date), "overpass_id", None)
            rows.append(dict(analysis="swot_riversp_gauge_kherson", period=lab, support="consecutive-day pairs only",
                             mode="first_difference", n_independent=len(d), **a, **bb))
    # lag diagnostic (R9): SWOT_t vs gauge_{t+k}, k in -3..3, on levels and first differences
    lagrows = []
    full = q.loc["2023-05-26":"2023-07-10"].asfreq("D")
    for tname in ("levels", "first_difference"):
        X = full.x if tname == "levels" else full.x.diff(); Y = full.y if tname == "levels" else full.y.diff()
        for k in range(-3, 4):
            d = pd.concat([Y.rename("y"), X.shift(-k).rename("x")], axis=1).dropna()
            lagrows.append(dict(reference="Kherson gauge 80805", station="SWOT RiverSP <3 km", transform=tname, lag_days=k,
                                n=len(d), pearson_r=d.x.corr(d.y), spearman_rho=d.x.corr(d.y, method="spearman")))
    return pd.DataFrame(rows), pixc, q.reset_index(), pd.DataFrame(lagrows), c_res


def swot_icesat(checks: list) -> pd.DataFrame:
    t = pd.read_csv(F_SWOT_IS2)
    exp = {"2023-04-05": -0.1810, "2023-05-04": 0.0597, "2023-05-14": 0.0036}
    for _, r in t.iterrows():
        checks.append(dict(check=f"SWOT-ICESat {r.date}", expected=f"{exp[r.date]:+.4f}", reproduced=f"{r.d_swot_minus_icesat_m:+.4f}",
                           ok=abs(exp[r.date] - r.d_swot_minus_icesat_m) < 5e-4))
    t["abs_dt_hours"] = t.dt_hours.abs()
    t["note"] = "n=3: no correlation computed (R7); residual vs |dt| reported"
    return t


# ----------------------------------------------------------------------------- canonical tables
def canonical(g, pres, u_ext, u_kh, pixc, rsp, swis) -> tuple[pd.DataFrame, pd.DataFrame]:
    obs = []
    obs.append(pd.DataFrame(dict(sensor="gauge", product="UkrHMI yearbook daily", station_id=g.name_en, overpass_id=pd.NaT,
                                 obs_date=g.date, obs_time_utc=pd.NaT, height_m=g.H_evrf2019_m, vertical_frame=FRAME_GAUGE,
                                 n_native=1, spread_m=np.nan, qc_pass=True, source_file=F_GAUGE.name)))
    obs.append(pd.DataFrame(dict(sensor="ICESat-2", product="ATL13 v7 beam-pass (reservoir)", station_id=pres.get("station"),
                                 overpass_id=pres.date, obs_date=pres.date, obs_time_utc=pres.datetime,
                                 height_m=pres.median_wse_evrs_m, vertical_frame=FRAME_ATL13, n_native=pres.n_points,
                                 spread_m=pres.nmad_m, qc_pass=pres.qc_pass, source_file=F_PASS_RES.name,
                                 beam=pres.beam, rgt=pres.rgt, dist_to_gauge_km=pres.get("dist_km"))))
    kh = pd.read_parquet(F_PASS_KH); kh["date"] = pd.to_datetime(kh.date)
    obs.append(pd.DataFrame(dict(sensor="ICESat-2", product="ATL13 v7 beam-pass (lower Dnipro/Kherson)", station_id="Kherson",
                                 overpass_id=kh.date, obs_date=kh.date, obs_time_utc=kh.datetime, height_m=kh.median_wse_evrs_m,
                                 vertical_frame=FRAME_ATL13, n_native=kh.n_points, spread_m=kh.nmad_m, qc_pass=True,
                                 source_file=F_PASS_KH.name, beam=kh.beam, rgt=kh.rgt)))
    v = pd.read_csv(F_PIXC_KH); v["date"] = pd.to_datetime(v.date)
    obs.append(pd.DataFrame(dict(sensor="SWOT", product="L2_HR_PIXC (Kherson, radius " + v.radius_km.astype(str) + " km)",
                                 station_id="Kherson", overpass_id=v.date, obs_date=v.date, obs_time_utc=pd.to_datetime(v.swot_utc, utc=True),
                                 height_m=v.H_SWOT_common_m, vertical_frame=FRAME_SWOT, n_native=v.n_px, spread_m=v.within_scene_nmad_m,
                                 qc_pass=True, source_file=F_PIXC_KH.name)))
    obs.append(pd.DataFrame(dict(sensor="SWOT", product="L2_HR_RiverSP node median <3 km (Kherson)", station_id="Kherson",
                                 overpass_id=rsp.date, obs_date=rsp.date, obs_time_utc=pd.NaT, height_m=rsp.y, vertical_frame=FRAME_SWOT,
                                 n_native=rsp.swot_n, spread_m=(rsp.swot_p75 - rsp.swot_p25) / 1.349, qc_pass=True,
                                 source_file=F_RIVERSP_KH.name)))
    O = pd.concat(obs, ignore_index=True)
    shas = {p.name: sha256(p) for p in (F_GAUGE, F_PASS_RES, F_PASS_KH, F_PIXC_KH, F_RIVERSP_KH)}
    O["source_file_sha256"] = O.source_file.map(shas)
    for c in ("obs_time_utc",):
        O[c] = pd.to_datetime(O[c], utc=True, errors="coerce")
    M = []
    M.append(u_ext.assign(pair="ATL13-gauge reservoir (extended)", x_frame=FRAME_GAUGE, y_frame=FRAME_ATL13, match_rule="same calendar date; nearest gauge"))
    M.append(u_kh.assign(pair="ATL13-gauge Kherson", x_frame=FRAME_GAUGE, y_frame=FRAME_ATL13, match_rule="same calendar date"))
    M.append(pixc[["date", "x", "y", "n_px"]].assign(station="Kherson", overpass_id=pixc.date, pair="SWOT PIXC-gauge Kherson (1 km)",
                                                    x_frame=FRAME_GAUGE, y_frame=FRAME_SWOT, match_rule="same calendar date"))
    M.append(rsp[["date", "x", "y", "swot_n"]].assign(station="Kherson", overpass_id=rsp.date, pair="SWOT RiverSP-gauge Kherson",
                                                     x_frame=FRAME_GAUGE, y_frame=FRAME_SWOT, match_rule="same calendar date"))
    M.append(swis.rename(columns={"date": "date_str"}).assign(pair="SWOT-ICESat-2 collocation", date=lambda d: pd.to_datetime(d.date_str),
                                                               match_rule="|dt| as observed; no interpolation"))
    Mx = pd.concat(M, ignore_index=True)
    Mx = Mx.rename(columns={"x": "height_reference_m", "y": "height_satellite_m"})
    Mx["residual_sat_minus_ref_m"] = Mx.height_satellite_m - Mx.height_reference_m
    for c in Mx.columns:
        if Mx[c].dtype == object:
            Mx[c] = Mx[c].astype(str)
    return O, Mx


# ----------------------------------------------------------------------------- figures
def stat_text(a: dict, ci: dict | None = None, n_ind=None) -> str:
    s = f"r = {a['pearson_r']:.2f}"
    if ci is not None and np.isfinite(ci.get("pearson_ci_lo", np.nan)):
        s += f" [{ci['pearson_ci_lo']:.2f}, {ci['pearson_ci_hi']:.2f}]"
    s += f"\nρ = {a['spearman_rho']:.2f}"
    if n_ind is not None:
        s += f"\nn = {n_ind} overpasses"
    return s


def fig_timeseries(w, u_ext, u_kh):
    fig, axes = plt.subplots(7, 1, figsize=(10, 14), sharex=False)
    for ax, st in zip(axes, ALL_ORDER):
        s = w[st].loc["2019":"2021"] if st != "Kherson" else w[st].loc["2019":"2025"]
        ax.plot(s.index, s.values, color=INK2, lw=1.0, label="gauge, daily (EVRF2019)")
        sat = u_ext[(u_ext.station == st) & (u_ext.dist_km <= 20)] if st != "Kherson" else u_kh[u_kh.dist_km <= 10]
        ax.plot(sat.date, sat.y, "o", ms=4, color=C1, mec="white", mew=0.8, label="ATL13 overpass (EGG2015, no c)")
        ax.set_title(f"{st} — n = {sat.overpass_id.nunique()} overpasses", loc="left")
        ax.set_ylabel("m")
        if st == "Kherson":
            ax.axvline(BREACH, color=MUTED, ls=":", lw=1)
            ax.text(BREACH, ax.get_ylim()[1], " breach", color=MUTED, va="top", fontsize=8)
    axes[0].legend(loc="lower left", ncol=2, fontsize=8)
    fig.suptitle("Gauge series and matched ATL13 overpasses (points are never joined across gaps)\n"
                 "Vertical offset between the two = the un-applied EGG2015→EVRF2019 corrector c (≈ −0.17 m)", x=0.01, ha="left", fontsize=10)
    fig.tight_layout(); fig.savefig(FIG / "F_C01_timeseries_overlay.png"); plt.close(fig)


def fig_heatmap(gg: pd.DataFrame):
    sub = gg[gg.period.str.startswith("2019")]
    fig, axes = plt.subplots(1, 3, figsize=(15, 5.2))
    cmap = matplotlib.colors.LinearSegmentedColormap.from_list("seq", SEQ)
    for ax, tr in zip(axes, ("raw", "detrended", "first_difference")):
        M = pd.DataFrame(np.nan, index=ALL_ORDER, columns=ALL_ORDER); N = M.copy()
        for _, r in sub[sub["transform"] == tr].iterrows():
            M.loc[r.series_a, r.series_b] = M.loc[r.series_b, r.series_a] = r.pearson_r
            N.loc[r.series_a, r.series_b] = N.loc[r.series_b, r.series_a] = r.n_rows
        signed = tr == "first_difference"
        cm, lo = (DIV, -1.0) if signed else (cmap, 0.0)
        im = ax.imshow(M.values, cmap=cm, vmin=lo, vmax=1)
        for i in range(7):
            for j in range(7):
                if i != j and np.isfinite(M.iat[i, j]):
                    v = M.iat[i, j]
                    dark = abs(v) > 0.6 if signed else v > 0.6
                    ax.text(j, i, f"{v:.2f}\nn{int(N.iat[i, j])}", ha="center", va="center", fontsize=6.5,
                            color="white" if dark else INK)
        ax.set_xticks(range(7)); ax.set_yticks(range(7))
        ax.set_xticklabels([s.replace("Velyka ", "V. ").replace("Blahovishchenka", "Blahovish.") for s in ALL_ORDER], rotation=45, ha="right")
        ax.set_yticklabels([s.replace("Velyka ", "V. ").replace("Blahovishchenka", "Blahovish.") for s in ALL_ORDER])
        ax.grid(False); ax.set_title({"raw": "raw daily levels", "detrended": "linearly detrended", "first_difference": "first differences (ΔH/day)"}[tr])
        fig.colorbar(im, ax=ax, shrink=0.75, label="Pearson r")
    fig.suptitle("Gauge–gauge correlation, 2019–2021, pairwise-complete common dates (n shown per cell); upstream → downstream order",
                 x=0.01, ha="left", fontsize=10)
    fig.tight_layout(); fig.savefig(FIG / "F_C02_gauge_correlation_heatmap.png"); plt.close(fig)


def scatter_panel(ax, x, y, title, txt, one_to_one=True, color=C1, xl="gauge (m)", yl="satellite (m)"):
    ax.plot(x, y, "o", ms=5, color=color, mec="white", mew=0.8, alpha=0.9)
    lo = np.nanmin([np.nanmin(x), np.nanmin(y)]); hi = np.nanmax([np.nanmax(x), np.nanmax(y)]); pad = 0.05 * (hi - lo + 1e-9)
    if one_to_one:
        ax.plot([lo - pad, hi + pad], [lo - pad, hi + pad], color=MUTED, lw=1, ls="--", label="1:1")
    ts = stats.theilslopes(y, x)
    xx = np.array([np.nanmin(x), np.nanmax(x)])
    ax.plot(xx, ts.intercept + ts.slope * xx, color=INK2, lw=1.4, label=f"Theil–Sen slope {ts.slope:.2f}")
    ax.set_title(title, loc="left"); ax.set_xlabel(xl); ax.set_ylabel(yl)
    ax.text(0.02, 0.98, txt, transform=ax.transAxes, va="top", fontsize=8, color=INK,
            bbox=dict(boxstyle="round,pad=0.3", fc="white", ec=GRID))
    ax.legend(loc="lower right", fontsize=7)


def fig_scatter(ms_units, ext_u, kh_u, pixc, res_ms, res_ext, res_kh, res_sw):
    fig, axes = plt.subplots(2, 3, figsize=(15, 9.5))
    u = ms_units["A_term_interpolated|reported"]
    r = res_ms[(res_ms.gauge_variant == "A_term_interpolated") & (res_ms.support == "radius=reported km")]
    ra, rc = r[r["mode"] == "pooled_absolute"].iloc[0], r[r["mode"] == "within_station_centred"].iloc[0]
    scatter_panel(axes[0, 0], u.x, u.y, "(a) ATL13 vs gauge, manuscript set — absolute",
                  stat_text(ra, ra, u.overpass_id.nunique()) + f"\nmedian bias {ra.median_bias_m:+.3f} m", one_to_one=False,
                  xl="gauge, m EVRF2019", yl="ATL13, m EGG2015")
    uc = centre(u, "name_en")
    scatter_panel(axes[0, 1], uc.x_anom, uc.y_anom, "(b) same, within-station anomalies",
                  stat_text(rc, rc, u.overpass_id.nunique()), xl="gauge anomaly, m", yl="ATL13 anomaly, m")
    e = ext_u[ext_u.dist_km <= 20]; ec = centre(e, "station")
    r = res_ext[(res_ext.support == "dist<=20 km (primary)") & (res_ext.gauge_reference == "nearest_station") & (res_ext["mode"] == "within_station_centred")].iloc[0]
    scatter_panel(axes[0, 2], ec.x_anom, ec.y_anom, "(c) ATL13 vs gauge, extended ≤20 km — anomalies",
                  stat_text(r, r, e.overpass_id.nunique()), xl="gauge anomaly, m", yl="ATL13 anomaly, m")
    k = kh_u[(kh_u.date < BREACH) & (kh_u.dist_km <= 10)]
    r = res_kh[(res_kh.period == "PRE_BREACH") & (res_kh.support == "dist<=10 km (primary)")].iloc[0]
    scatter_panel(axes[1, 0], k.x, k.y, "(d) ATL13 vs Kherson gauge, pre-breach ≤10 km",
                  stat_text(r, r, k.overpass_id.nunique()) + f"\nmedian bias {r.median_bias_m:+.3f} m", one_to_one=False,
                  xl="gauge, m EVRF2019", yl="ATL13, m EGG2015")
    k = kh_u[(kh_u.date >= "2023-07-01") & (kh_u.dist_km <= 10)]
    r = res_kh[(res_kh.period.str.startswith("POST")) & (res_kh.support == "dist<=10 km (primary)")]
    if len(r):
        r = r.iloc[0]
        scatter_panel(axes[1, 1], k.x, k.y, "(e) ATL13 vs Kherson gauge, post-breach ≤10 km",
                      stat_text(r, r, k.overpass_id.nunique()) + f"\nmedian bias {r.median_bias_m:+.3f} m", one_to_one=False,
                      xl="gauge, m EVRF2019", yl="ATL13, m EGG2015")
    r = res_sw[(res_sw.analysis == "swot_pixc_gauge_kherson") & (res_sw.support.str.contains("manuscript"))].iloc[0]
    scatter_panel(axes[1, 2], pixc.x, pixc.y, "(f) SWOT PIXC vs Kherson, 1 km (n = 9)",
                  stat_text(r, r) + f"\nn = 9 passes\nmedian bias {r.median_bias_m:+.3f} m", color=C2,
                  xl="gauge, m EVRF2019", yl="SWOT, m EGG2015")
    fig.suptitle("Satellite vs gauge. r with 95 % cluster-bootstrap CI (unit = overpass date). Absolute panels are offset by the un-applied corrector, so no 1:1 line.",
                 x=0.01, ha="left", fontsize=10)
    fig.tight_layout(); fig.savefig(FIG / "F_C03_satellite_gauge_scatter.png"); plt.close(fig)


def fig_bland_altman(ext_u, kh_u, pixc, rsp):
    sets = [("ATL13 − gauge, reservoir extended ≤20 km\n(residual incl. station corrector)", ext_u[ext_u.dist_km <= 20], C1),
            ("ATL13 − gauge, Kherson ≤10 km (pre + post)", kh_u[(kh_u.dist_km <= 10) & ((kh_u.date < BREACH) | (kh_u.date >= "2023-07-01"))], C1),
            ("SWOT PIXC − gauge, Kherson 1 km", pixc, C2),
            ("SWOT RiverSP − gauge, Kherson (c removed)", rsp, C2)]
    fig, axes = plt.subplots(1, 4, figsize=(18, 4.6))
    for ax, (t, d, col) in zip(axes, sets):
        m = (d.x + d.y) / 2; e = d.y - d.x
        ax.plot(m, e, "o", ms=5, color=col, mec="white", mew=0.8)
        med, nm = np.median(e), nmad(e)
        ax.axhline(med, color=INK2, lw=1.4); ax.axhline(med + 1.96 * nm, color=MUTED, ls="--", lw=1); ax.axhline(med - 1.96 * nm, color=MUTED, ls="--", lw=1)
        rho = stats.spearmanr(m, np.abs(e - med)).statistic
        ax.text(0.02, 0.98, f"median {med:+.3f} m\nNMAD {nm:.3f} m\nlimits = median ± 1.96·NMAD\nρ(|e|, level) = {rho:+.2f}\nn = {len(d)}",
                transform=ax.transAxes, va="top", fontsize=8, bbox=dict(boxstyle="round,pad=0.3", fc="white", ec=GRID))
        ax.set_title(t, loc="left", fontsize=9); ax.set_xlabel("mean of the two, m"); ax.set_ylabel("satellite − gauge, m")
    fig.tight_layout(); fig.savefig(FIG / "F_C04_bland_altman.png"); plt.close(fig)


def fig_lag(curves: pd.DataFrame, swlag: pd.DataFrame):
    fig, axes = plt.subplots(1, 4, figsize=(18, 4.6), sharey=False)
    ref = "Nova Kakhovka"
    order = [s for s in RES_ORDER if s != ref] + ["Kherson"]
    cols = dict(zip(order, SEQ[1:]))  # ordinal: distance from the dam (light = far upstream)
    for ax, tr in zip(axes[:3], ("raw", "detrended", "first_difference")):
        for st in order:
            c = curves[(curves.reference == ref) & (curves.station == st) & (curves["transform"] == tr)]
            ax.plot(c.lag_days, c.pearson_r, color=cols[st], lw=2 if st == "Kherson" else 1.6, ls="--" if st == "Kherson" else "-")
            k = c.loc[c.pearson_r.idxmax()]
            ax.plot(k.lag_days, k.pearson_r, "o", ms=5, color=cols[st], mec="white")
        ax.axvline(0, color=MUTED, lw=0.8)
        ax.set_title({"raw": "raw levels", "detrended": "detrended", "first_difference": "first differences"}[tr] + f" vs {ref}", loc="left")
        ax.set_xlabel("lag k, days  (corr(station_t, NK_t+k))"); ax.set_ylabel("Pearson r")
    for st in order:
        axes[2].plot([], [], color=cols[st], ls="--" if st == "Kherson" else "-", label=st)
    axes[2].legend(fontsize=7, loc="lower left", title="station (upstream → Kherson)", title_fontsize=7)
    ax = axes[3]
    for tr, col in (("levels", C2), ("first_difference", C1)):
        c = swlag[swlag["transform"] == tr]
        ax.plot(c.lag_days, c.pearson_r, "-o", color=col, ms=5, mec="white", label=f"{tr.replace('_', ' ')} (n≈{int(c.n.median())})")
    ax.axvline(0, color=MUTED, lw=0.8); ax.legend(fontsize=7, loc="lower center")
    ax.set_title("SWOT RiverSP vs Kherson, 26 May–10 Jul", loc="left"); ax.set_xlabel("lag k, days"); ax.set_ylabel("Pearson r")
    fig.suptitle("Lagged correlation, 2019–2021 daily gauges (±14 d) and SWOT fast-sampling (±3 d). A broad plateau = persistence, not travel time.",
                 x=0.01, ha="left", fontsize=10)
    fig.tight_layout(); fig.savefig(FIG / "F_C05_lagged_correlation.png"); plt.close(fig)


def fig_station(per_ms, per_ext):
    fig, ax = plt.subplots(figsize=(8.5, 4.8))
    for off, (d, col, lab) in ((-0.15, (per_ms, C1, "manuscript set (reported radius)")), (0.15, (per_ext, C2, "extended ≤20 km"))):
        for i, st in enumerate(RES_ORDER):
            r = d[d.station == st]
            if not len(r) or not np.isfinite(r.iloc[0].get("pearson_r", np.nan)):
                continue
            r = r.iloc[0]
            lo, hi = r.get("pearson_ci_lo", np.nan), r.get("pearson_ci_hi", np.nan)
            if np.isfinite(lo):
                ax.plot([lo, hi], [i + off] * 2, color=col, lw=2, solid_capstyle="round")
            ax.plot(r.pearson_r, i + off, "o", ms=7, color=col, mec="white", mew=1)
            ax.text(1.02, i + off, f"n={int(r.n_independent)}", va="center", fontsize=7, color=INK2, transform=ax.get_yaxis_transform())
        ax.plot([], [], "o-", color=col, label=lab)
    ax.set_yticks(range(len(RES_ORDER))); ax.set_yticklabels(RES_ORDER); ax.invert_yaxis()
    ax.axvline(0, color=MUTED, lw=0.8); ax.set_xlim(-1, 1); ax.set_xlabel("Pearson r (ATL13 vs same-date gauge), 95 % bootstrap CI over overpasses")
    ax.legend(fontsize=8, loc="lower left")
    ax.set_title("Station-specific ATL13–gauge correlation (pre-breach)", loc="left")
    fig.tight_layout(); fig.savefig(FIG / "F_C06_station_specific_correlations.png"); plt.close(fig)


def fig_support(sens: pd.DataFrame, swis: pd.DataFrame):
    fig, axes = plt.subplots(1, 3, figsize=(16, 4.6))
    groups = [("ATL13 reservoir: manuscript set (gauge A), within-station", "atl13_ms"),
              ("ATL13 reservoir: extended, within-station", "atl13_ext"),
              ("SWOT PIXC − Kherson: radius", "swot")]
    for ax, (title, key) in zip(axes[:2], groups[:2]):
        d = sens[sens.group == key].reset_index(drop=True)
        for i, r in d.iterrows():
            ax.plot([r.pearson_ci_lo, r.pearson_ci_hi], [i, i], color=C1, lw=2)
            ax.plot(r.pearson_r, i, "o", ms=7, color=C1, mec="white")
            ax.text(1.02, i, f"n={int(r.n_independent)}", va="center", fontsize=7, color=INK2, transform=ax.get_yaxis_transform())
        ax.set_yticks(range(len(d))); ax.set_yticklabels(d.label); ax.invert_yaxis(); ax.axvline(0, color=MUTED, lw=0.8)
        ax.set_xlim(-1, 1); ax.set_xlabel("Pearson r (anomalies), 95 % CI"); ax.set_title(title, loc="left", fontsize=9)
    ax = axes[2]
    d = sens[sens.group == "swot"].reset_index(drop=True)
    ax.errorbar(d.radius, d.median_bias_m, yerr=[d.median_bias_m - d.bias_ci_lo_m, d.bias_ci_hi_m - d.median_bias_m], fmt="o-", color=C2,
                ms=6, capsize=3, label="median SWOT − gauge (95 % CI)")
    ax.axhline(0, color=MUTED, lw=0.8); ax.set_xscale("log"); ax.set_xticks(d.radius); ax.set_xticklabels([f"{v:g}" for v in d.radius])
    ax.set_xlabel("aggregation radius around Kherson gauge, km"); ax.set_ylabel("m")
    for _, r in d.iterrows():
        ax.annotate(f"r={r.pearson_r:.2f}", (r.radius, r.median_bias_m), textcoords="offset points", xytext=(6, 6), fontsize=7, color=INK2)
    ax.set_title("SWOT PIXC − Kherson vs radius (n = 9)", loc="left", fontsize=9); ax.legend(fontsize=7)
    fig.tight_layout(); fig.savefig(FIG / "F_C07_support_sensitivity.png"); plt.close(fig)

    fig, ax = plt.subplots(figsize=(5.5, 4))
    ax.axhline(0, color=MUTED, lw=0.8)
    ax.plot(swis.abs_dt_hours, swis.d_swot_minus_icesat_m, "o", ms=8, color=C2, mec="white")
    for _, r in swis.iterrows():
        ax.annotate(f"{r.date}\nΔt {r.dt_hours:+.1f} h", (r.abs_dt_hours, r.d_swot_minus_icesat_m), textcoords="offset points",
                    xytext=(6, -4), fontsize=7, color=INK2)
    ax.set_xlabel("|Δt| between SWOT and ICESat-2, hours"); ax.set_ylabel("SWOT − ICESat-2, m")
    ax.set_title("SWOT–ICESat-2 collocations (n = 3; no r computed)", loc="left", fontsize=9)
    fig.tight_layout(); fig.savefig(FIG / "F_C08_swot_icesat_timing.png"); plt.close(fig)


# ----------------------------------------------------------------------------- main
def main():
    t_start = datetime.now(timezone.utc)
    checks: list = []
    g = load_gauges(); w = gauge_wide(g)
    checks.append(dict(check="gauge daily values in source (all QC)", expected="10194",
                       reproduced=str(len(pd.read_parquet(F_GAUGE))), ok=len(pd.read_parquet(F_GAUGE)) == 10194))

    print("A. gauge-gauge ...", flush=True)
    gg, gg_boot = gauge_gauge(w)
    curves, lagsum = lag_analysis(w)
    coh = coherence_table(w)

    print("B. ATL13-gauge ...", flush=True)
    res_ms, per_ms, ms_units, ch = atl13_manuscript_set(g); checks += ch
    res_ext, per_ext, u_ext, pres = atl13_extended(g)
    res_kh, u_kh = atl13_kherson(g)

    print("C. SWOT ...", flush=True)
    res_sw, pixc, rsp, swlag, c_res = swot_kherson(g, checks)
    swis = swot_icesat(checks)

    # ---- pairwise table
    for d in (res_ms, res_ext, res_kh, res_sw, per_ms, per_ext):
        d["evidence_tier"] = d.n_independent.astype(int).map(tier)
    gg["evidence_tier"] = "primary-eligible (long daily series; see n_eff)"
    pairwise = pd.concat([gg, res_ms, per_ms, res_ext, per_ext, res_kh, res_sw], ignore_index=True)
    lead = ["analysis", "period", "series_a", "series_b", "station", "transform", "gauge_variant", "gauge_reference", "support", "mode",
            "evidence_tier", "n_rows", "n_independent", "n_eff", "pearson_r", "pearson_ci_lo", "pearson_ci_hi", "spearman_rho",
            "spearman_ci_lo", "spearman_ci_hi", "kendall_tau_b", "pearson_p_naive", "pearson_p_neff", "pearson_p_neff_fdr",
            "spearman_p_naive", "kendall_p_naive", "median_bias_m", "bias_ci_lo_m", "bias_ci_hi_m", "nmad_m", "rmse_m", "mae_m", "mean_bias_m"]
    pairwise = pairwise[[c for c in lead if c in pairwise] + [c for c in pairwise if c not in lead]]
    pairwise.to_csv(OUT / "correlation_pairwise.csv", index=False, float_format="%.6g")
    gg_boot.to_csv(OUT / "correlation_pairwise_bootstrap.csv", index=False, float_format="%.6g")
    lagsum.to_csv(OUT / "correlation_lag_summary.csv", index=False, float_format="%.6g")
    pd.concat([curves, swlag.drop(columns=["n", "spearman_rho"]).assign(lag_days=swlag.lag_days)], ignore_index=True) \
        .to_csv(OUT / "correlation_lag_curves.csv", index=False, float_format="%.6g")
    swlag.to_csv(OUT / "correlation_lag_swot_kherson.csv", index=False, float_format="%.6g")
    coh.to_csv(OUT / "correlation_coherence.csv", index=False, float_format="%.6g")
    swis.to_csv(OUT / "correlation_swot_icesat_collocations.csv", index=False, float_format="%.6g")

    # ---- support sensitivity
    sens = []
    for rr in ("reported", 1.0, 2.0, 3.0, 5.0):
        r = res_ms[(res_ms.gauge_variant == "A_term_interpolated") & (res_ms.support == f"radius={rr} km") & (res_ms["mode"] == "within_station_centred")].iloc[0]
        sens.append(dict(group="atl13_ms", label=f"radius {rr} km", **r[["pearson_r", "pearson_ci_lo", "pearson_ci_hi", "n_independent", "median_bias_m", "nmad_m"]]))
    for gv in ("B_term_nearest", "C_daily_mean"):
        r = res_ms[(res_ms.gauge_variant == gv) & (res_ms.support == "radius=reported km") & (res_ms["mode"] == "within_station_centred")].iloc[0]
        sens.append(dict(group="atl13_ms", label=f"reported, gauge variant {gv.split('_')[0]}", **r[["pearson_r", "pearson_ci_lo", "pearson_ci_hi", "n_independent", "median_bias_m", "nmad_m"]]))
    for sup in ("dist<=10 km", "dist<=20 km (primary)", "all (nearest gauge)"):
        for ref in ("nearest_station", "network_median"):
            r = res_ext[(res_ext.support == sup) & (res_ext.gauge_reference == ref) & (res_ext["mode"] == "within_station_centred")].iloc[0]
            sens.append(dict(group="atl13_ext", label=f"{sup.replace(' (primary)', '*')}, {ref.replace('_', ' ')}",
                             **r[["pearson_r", "pearson_ci_lo", "pearson_ci_hi", "n_independent", "median_bias_m", "nmad_m"]]))
    for _, r in res_sw[res_sw.analysis == "swot_pixc_gauge_kherson"].iterrows():
        sens.append(dict(group="swot", label=r.support, radius=float(r.support.split("=")[1].split(" ")[0]),
                         **r[["pearson_r", "pearson_ci_lo", "pearson_ci_hi", "n_independent", "median_bias_m", "nmad_m", "bias_ci_lo_m", "bias_ci_hi_m"]]))
    sens = pd.DataFrame(sens)
    sens.to_csv(OUT / "correlation_support_sensitivity.csv", index=False, float_format="%.6g")

    # ---- canonical tables + inventory
    O, Mx = canonical(g, pres, u_ext, u_kh, pixc, rsp, swis)
    O.to_parquet(OUT / "canonical_observations.parquet", index=False)
    Mx.to_parquet(OUT / "canonical_matchups.parquet", index=False)
    inv = []
    for f, role, frame in ((F_GAUGE, "7 gauges, daily, EVRF2019", FRAME_GAUGE), (F_MATCH53, "ATL13-gauge beam matchups (manuscript 53)", FRAME_ATL13),
                           (F_STATION, "per-station corrector c", "-"), (F_PASS_RES, "ATL13 reservoir beam-pass levels", FRAME_ATL13),
                           (F_PASS_KH, "ATL13 Kherson beam-pass levels", FRAME_ATL13), (F_PIXC_KH, "SWOT PIXC vs Kherson (9 passes x 4 radii)", FRAME_SWOT),
                           (F_RIVERSP_KH, "SWOT RiverSP <3 km vs Kherson daily (p59)", FRAME_SWOT + "; p59 added reservoir c, removed here"),
                           (F_SWOT_IS2, "SWOT-ICESat-2 collocations", "-")):
        d = pd.read_parquet(f) if f.suffix == ".parquet" else pd.read_csv(f)
        dc = next((c for c in ("date", "datetime") if c in d.columns), None)
        dd = pd.to_datetime(d[dc].astype(str).str[:10], errors="coerce") if dc else None
        inv.append(dict(dataset=role, file=str(f.relative_to(IN)), rows=len(d), start=str(dd.min().date()) if dc else "",
                        end=str(dd.max().date()) if dc else "", vertical_frame=frame, sha256=sha256(f)))
    pd.DataFrame(inv).to_csv(OUT / "correlation_data_inventory.csv", index=False)
    pd.DataFrame(checks).to_csv(OUT / "correlation_reproduction_checks.csv", index=False)

    print("figures ...", flush=True)
    fig_timeseries(w, u_ext, u_kh)
    fig_heatmap(gg)
    fig_scatter(ms_units, u_ext, u_kh, pixc, res_ms, res_ext, res_kh, res_sw)
    fig_bland_altman(u_ext, u_kh, pixc, rsp)
    fig_lag(curves, swlag)
    fig_station(per_ms, per_ext)
    fig_support(sens, swis)

    try:
        kg_commit = subprocess.run(["git", "-C", str(KG), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    except Exception:
        kg_commit = ""
    manifest = dict(run_utc=t_start.isoformat(), script=str(Path(__file__).relative_to(KG)), script_sha256=sha256(Path(__file__)),
                    knoweledg_graf_commit=kg_commit, source_commits=(IN / "SOURCE_COMMITS.txt").read_text().strip().splitlines(),
                    seed=SEED, n_boot=N_BOOT, reservoir_corrector_removed_from_riversp_m=c_res,
                    reproduction_checks_all_ok=bool(all(c["ok"] for c in checks)),
                    outputs=sorted(p.name for p in OUT.glob("correlation_*")) + sorted(p.name for p in FIG.glob("*.png")))
    (OUT / "correlation_run_manifest.json").write_text(json.dumps(manifest, indent=2, default=str))
    print(pd.DataFrame(checks).to_string())
    print("done")


if __name__ == "__main__":
    main()
