#!/usr/bin/env python
"""Paper 1 -- geodetic sensitivity audit of the headline slope result.

Question: can the vertical reference (EGG2015, the ATL13 tide term, or the empirical local vertical closure
offset c tied to the EVRF2019 gauges) create or change the pre -> post longitudinal water-surface-slope contrast?

A. Frame invariance on the SAME points, SAME passes, SAME QC, SAME chainage x, SAME fit (the production
   `fit()` of SWOT-DNIPRO scripts/make_transition_stats.py, copied verbatim):
     production        wse_m as used for the manuscript (verified below = H_EGG2015 + free2mean(lat))
     egg2015_no_tide   ht_water_surf - zeta_EGG2015 (the ATL13 mean-tide term removed)
     prod_plus_const_c production + one constant c                      -> Delta S must be 0 to rounding
     prod_plus_c_nearest production + c of the nearest reservoir gauge  (piecewise-constant c(x): the
                       Phase 2d "nearest-station corrector surface" -- the variant that CAN change slope)
     prod_plus_c_linear production + c fitted linearly in chainage     -> Delta S = dc/dx exactly
   Per overpass Delta S_i = S_frame,i - S_production,i; max and median |Delta S|; headline table per frame.
B. Residual-frame gradient: c_station vs station chainage (cm/km, 95 % CI) against the +3.2 cm/km contrast.
C. Leave-one-station-out prediction of c (mean of the other five; nearest other station).
D. Common spatial support: points restricted to 10 km chainage bins observed in both periods; and passes
   whose RGT is observed in both periods.
E. Heterogeneity detrended: per date, p95-p05 of residuals from a robust planar fit (soft-L1) in UTM, on the
   same segments as the production metric (pre: all pool segments; post/drawdown: main + connected channel).
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import matplotlib
import numpy as np
import pandas as pd
from pyproj import Transformer
from scipy import stats
from scipy.optimize import least_squares

sys.path.insert(0, str(Path(__file__).resolve().parent))
import paper1_vertical_closure as VCM  # noqa: E402
import paper1_swot_icesat_zone as Z  # noqa: E402

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

KG = Path(__file__).resolve().parents[1]
A = KG / "data/paper_1_audit/slope_frame_audit"
IN = A / "inputs"
FIG = KG / "outputs/paper_1/slope_frame_audit"
FIG.mkdir(parents=True, exist_ok=True)
F_PTS = IN / "outputs/figure_data/FigD_kakhovka_profile_points.csv"
F_ROBUST = IN / "outputs/tables/kakhovka_perdate_slopes_robust.csv"
F_K5 = IN / "outputs/tables/k5_gauge_levels_evrf2019.csv"
F_SEG = IN / "icesat2-atl13-kakhovka/data/processed/kakhovka_atl13_segments.parquet"
F_CLS = IN / "SWOT-DNIPRO/outputs/tables/atl13_water_classification.parquet"
F_F4 = IN / "SWOT-DNIPRO/outputs/tables/pre_post_fragmentation_statistics.csv"
F_C = KG / "data/paper_1_audit/vertical_closure/vc_closure_offsets.csv"
SEED = 20260923
RNG = np.random.default_rng(SEED)
MIN_PTS, CHAIN_SEP, MIN_SPAN = 3, 5.0, 20.0
BREACH = pd.Timestamp("2023-06-06")
TO_UTM = Transformer.from_crs("EPSG:4326", "EPSG:32636", always_xy=True)


# ---- verbatim from SWOT-DNIPRO scripts/make_transition_stats.py (fit / perm_test / boot_diff logic) ----
def n_distinct(v, sep=CHAIN_SEP):
    v = np.sort(np.asarray(v)); keep = [v[0]]
    for x in v[1:]:
        if x - keep[-1] > sep:
            keep.append(x)
    return len(keep)


def fit(df, min_span, col="wse_m"):
    out = []
    for (date, period), g in df.groupby(["date", "period"]):
        span = g.chain_km.max() - g.chain_km.min()
        if len(g) < MIN_PTS or n_distinct(g.chain_km.values) < MIN_PTS or span < min_span:
            continue
        x, y = g.chain_km.to_numpy(float), g[col].to_numpy(float)
        ts = stats.theilslopes(y, x, 0.95)
        out.append({"date": date, "period": period, "n_points": len(g), "span_km": span,
                    "slope_ols_cm_km": float(np.polyfit(x, y, 1)[0]) * 100, "slope_theilsen_cm_km": float(ts[0]) * 100,
                    "wse_p95_p05_m": float(np.percentile(y, 95) - np.percentile(y, 5))})
    return pd.DataFrame(out)


def perm_test(a, b, n=20000):
    a, b = np.asarray(a, float), np.asarray(b, float); obs = np.median(b) - np.median(a)
    pool = np.concatenate([a, b]); na = len(a); cnt = 0
    for _ in range(n):
        RNG.shuffle(pool)
        if abs(np.median(pool[na:]) - np.median(pool[:na])) >= abs(obs):
            cnt += 1
    return obs, (cnt + 1) / (n + 1)


def boot_diff(a, b, n=20000):
    a, b = np.asarray(a, float), np.asarray(b, float)
    d = [np.median(RNG.choice(b, len(b), True)) - np.median(RNG.choice(a, len(a), True)) for _ in range(n)]
    return float(np.percentile(d, 2.5)), float(np.percentile(d, 97.5))


def headline(prof, est="slope_theilsen_cm_km", label=""):
    pre, post, draw = (prof[prof.period == p] for p in ("PRE_BREACH", "POST_BREACH", "BREACH_DRAWDOWN"))
    if len(pre) < 2 or len(post) < 2:
        return dict(frame=label, n_pre=len(pre), n_post=len(post))
    obs, p = perm_test(pre[est], post[est]); lo, hi = boot_diff(pre[est], post[est])
    return dict(frame=label, estimator="Theil-Sen" if "theil" in est else "OLS", n_pre=len(pre), n_draw=len(draw), n_post=len(post),
                median_pre_cm_km=float(np.median(pre[est])), median_post_cm_km=float(np.median(post[est])),
                diff_cm_km=obs, diff_ci_lo=lo, diff_ci_hi=hi, permutation_p=p,
                mannwhitney_p=float(stats.mannwhitneyu(pre[est], post[est], alternative="two-sided").pvalue),
                pre_positive=f"{int((pre[est] > 0).sum())}/{len(pre)}", post_positive=f"{int((post[est] > 0).sum())}/{len(post)}")


def stations() -> pd.DataFrame:
    c = pd.read_csv(F_C)
    c = c[(c.sensor == "ICESat-2 ATL13") & c.period.str.startswith("PRE_BREACH 2020")][["control_point", "c_m", "lon", "lat"]]
    k5 = pd.read_csv(F_K5, usecols=["station_name", "chain_km"]).drop_duplicates("station_name")
    return c.merge(k5, left_on="control_point", right_on="station_name").drop(columns="station_name")


def main():
    out = {}
    pts = pd.read_csv(F_PTS)
    pts = pts.dropna(subset=["date", "period", "chain_km", "wse_m"]).copy()
    # A0: what the production frame is
    pts["recon_egg_tide"] = pts.median_wse_evrs_m + Z.free2mean(pts.lat_mean)
    d0 = (pts.wse_m - pts.recon_egg_tide).abs()
    out["production_frame_check"] = dict(max_abs_diff_mm=float(d0.max() * 1000),
                                         statement="wse_m == median_wse_evrs_m + free2mean(lat_mean): production slopes are in the EGG2015 frame (mean-tide crust); no gauge-derived offset enters")
    st = stations()
    c_const = float(st.c_m.mean())
    # nearest reservoir gauge per point (the Phase 2d Voronoi surface), and a linear c(chainage)
    dd = np.column_stack([np.hypot((pts.lon_mean - lo) * np.cos(np.radians(47)), pts.lat_mean - la) for lo, la in zip(st.lon, st.lat)])
    pts["c_nearest"] = st.c_m.values[dd.argmin(1)]
    lin = stats.linregress(st.chain_km, st.c_m)
    pts["c_linear"] = lin.intercept + lin.slope * pts.chain_km
    frames = {"production": pts.wse_m, "egg2015_no_tide": pts.median_wse_evrs_m, "prod_plus_const_c": pts.wse_m + c_const,
              "prod_plus_c_nearest": pts.wse_m + pts.c_nearest, "prod_plus_c_linear": pts.wse_m + pts.c_linear}
    per, heads = {}, []
    for k, v in frames.items():
        q = pts.assign(**{"H": v})
        per[k] = fit(q, MIN_SPAN, col="H").set_index(["date", "period"])
        heads.append(headline(per[k].reset_index(), label=k))
        heads.append(headline(per[k].reset_index(), est="slope_ols_cm_km", label=k))
    H = pd.DataFrame(heads)
    base = per["production"]
    rob = pd.read_csv(F_ROBUST).set_index(["date", "period"])
    repro = (base.slope_theilsen_cm_km - rob.slope_theilsen_cm_km.reindex(base.index)).abs()
    out["reproduction_of_published_per_pass_slopes"] = dict(n=int(repro.notna().sum()), max_abs_diff_cm_km=float(repro.max()))
    rows = []
    for k, t in per.items():
        for est in ("slope_theilsen_cm_km", "slope_ols_cm_km"):
            dS = (t[est] - base[est]).dropna()
            rows.append(dict(frame=k, estimator=est, n_passes=len(dS), same_pass_set=bool(t.index.equals(base.index)),
                             max_abs_dS_cm_km=float(dS.abs().max()), median_abs_dS_cm_km=float(dS.abs().median()),
                             n_passes_dS_gt_0p01=int((dS.abs() > 0.01).sum())))
    inv = pd.DataFrame(rows)
    perpass = pd.concat({k: t[["slope_theilsen_cm_km", "slope_ols_cm_km", "span_km", "n_points"]] for k, t in per.items()}, axis=1)
    A.mkdir(exist_ok=True)
    H.to_csv(A / "sfa_headline_by_frame.csv", index=False, float_format="%.6g")
    inv.to_csv(A / "sfa_invariance.csv", index=False, float_format="%.6g")
    perpass.to_csv(A / "sfa_per_pass_slopes.csv", float_format="%.6g")

    # B. residual-frame gradient in cm/km
    n = len(st); tcrit = stats.t.ppf(0.975, n - 2)
    grad = dict(n_stations=n, slope_cm_per_km=lin.slope * 100, ci_lo=(lin.slope - tcrit * lin.stderr) * 100,
                ci_hi=(lin.slope + tcrit * lin.stderr) * 100, p=lin.pvalue, chain_min_km=float(st.chain_km.min()),
                chain_max_km=float(st.chain_km.max()), c_range_m=float(st.c_m.max() - st.c_m.min()))
    hl = H[(H.frame == "production") & (H.estimator == "Theil-Sen")].iloc[0]
    grad["max_plausible_abs_gradient_cm_per_km"] = max(abs(grad["ci_lo"]), abs(grad["ci_hi"]))
    grad["headline_contrast_cm_per_km"] = float(hl.diff_cm_km)
    grad["ratio_contrast_to_max_gradient"] = grad["headline_contrast_cm_per_km"] / grad["max_plausible_abs_gradient_cm_per_km"]
    # steepest possible jump-induced slope from the nearest-station surface over a 20 km fit
    s_sorted = st.sort_values("chain_km")
    jumps = np.abs(np.diff(s_sorted.c_m.values))
    grad["max_adjacent_station_jump_m"] = float(jumps.max())
    grad["jump_over_20km_worst_case_cm_per_km"] = float(jumps.max() / 20 * 100)
    pd.DataFrame([grad]).to_csv(A / "sfa_residual_gradient.csv", index=False, float_format="%.6g")
    out["residual_gradient"] = grad

    # C. leave-one-station-out
    lo_rows = []
    for i, r in st.iterrows():
        oth = st.drop(i)
        near = oth.iloc[np.argmin(np.abs(oth.chain_km - r.chain_km))]
        lo_rows.append(dict(station=r.control_point, c_m=r.c_m, pred_mean_others=oth.c_m.mean(), err_mean=r.c_m - oth.c_m.mean(),
                            pred_nearest_other=near.c_m, nearest_other=near.control_point, err_nearest=r.c_m - near.c_m))
    L = pd.DataFrame(lo_rows)
    L.to_csv(A / "sfa_loso_closure_offset.csv", index=False, float_format="%.6g")
    out["loso"] = dict(rmse_mean_m=float(np.sqrt((L.err_mean ** 2).mean())), max_abs_mean_m=float(L.err_mean.abs().max()),
                       rmse_nearest_m=float(np.sqrt((L.err_nearest ** 2).mean())), max_abs_nearest_m=float(L.err_nearest.abs().max()))

    # D. common spatial support
    prof = per["production"].reset_index()
    elig = pts.merge(prof[["date", "period"]], on=["date", "period"])
    elig["bin"] = (elig.chain_km // 10).astype(int)
    bpre = set(elig[elig.period == "PRE_BREACH"].bin); bpost = set(elig[elig.period == "POST_BREACH"].bin)
    common = bpre & bpost
    cs = pts[((pts.chain_km // 10).astype(int)).isin(common)].copy()
    cs_fit = fit(cs, MIN_SPAN)
    rg = elig.groupby("period").rgt.apply(set)
    rgt_common = rg.get("PRE_BREACH", set()) & rg.get("POST_BREACH", set())
    rp = pts[pts.rgt.isin(rgt_common)]
    rp_fit = fit(rp, MIN_SPAN)
    sup = pd.DataFrame([dict(test="all passes (production)", **{k: v for k, v in headline(prof).items() if k != "frame"}),
                        dict(test=f"common 10 km chainage bins ({len(common)} bins, {min(common) * 10}-{(max(common) + 1) * 10} km)", **{k: v for k, v in headline(cs_fit).items() if k != "frame"}),
                        dict(test=f"RGTs observed in both periods ({len(rgt_common)} RGTs)", **{k: v for k, v in headline(rp_fit).items() if k != "frame"})])
    sup.to_csv(A / "sfa_common_support.csv", index=False, float_format="%.6g")

    # E. heterogeneity, raw vs detrended (same segments as the production F4 metric)
    zeta = VCM.egg2015()
    seg = pd.read_parquet(F_SEG)
    seg["dt"] = pd.to_datetime(seg.time, utc=True, errors="coerce").dt.tz_localize(None)
    seg = seg[seg.dt < BREACH].dropna(subset=["lat", "lon", "h_wgs84_m", "dt"])
    seg["wse_m"] = seg.h_wgs84_m + Z.free2mean(seg.lat) - zeta(seg.lon, seg.lat)
    seg["date"] = seg.dt.dt.normalize(); seg["period"] = "PRE_BREACH"
    cl = pd.read_parquet(F_CLS); cl["date"] = pd.to_datetime(cl.date)
    cls_map = {"MAIN_CHANNEL": "main_channel", "CONNECTED_SIDE_CHANNEL": "connected_side", "TRIBUTARY": "connected_side"}
    cl = cl[cl.water_class.map(cls_map).isin(["main_channel", "connected_side"])]
    allseg = pd.concat([seg[["date", "period", "lon", "lat", "wse_m"]], cl[["date", "period", "lon", "lat", "wse_m"]]], ignore_index=True)
    x, y = TO_UTM.transform(allseg.lon.values, allseg.lat.values)
    allseg["xk"], allseg["yk"] = np.asarray(x) / 1e3, np.asarray(y) / 1e3
    het = []
    for (d, p), g in allseg.groupby(["date", "period"]):
        if len(g) < 30:
            continue
        v = g.wse_m.to_numpy(); X = np.column_stack([np.ones(len(g)), g.xk - g.xk.mean(), g.yk - g.yk.mean()])
        if np.ptp(g.xk) < 1 and np.ptp(g.yk) < 1:
            res = v - np.median(v); b = np.array([np.median(v), 0, 0])
        else:
            b = least_squares(lambda bb: X @ bb - v, x0=np.linalg.lstsq(X, v, rcond=None)[0], loss="soft_l1", f_scale=0.05).x
            res = v - X @ b
        het.append(dict(date=d, period=p, n=len(g), raw_p95_p05_m=float(np.percentile(v, 95) - np.percentile(v, 5)),
                        detrended_p95_p05_m=float(np.percentile(res, 95) - np.percentile(res, 5)),
                        detrended_nmad_m=Z.nmad(res), plane_gradient_cm_per_km=float(np.hypot(b[1], b[2]) * 100)))
    het = pd.DataFrame(het)
    het.to_csv(A / "sfa_heterogeneity_by_date.csv", index=False, float_format="%.6g")
    hrows = []
    for col in ("raw_p95_p05_m", "detrended_p95_p05_m", "detrended_nmad_m"):
        a, b = het[het.period == "PRE_BREACH"][col], het[het.period == "POST_BREACH"][col]
        obs, p = perm_test(a, b); lo, hi = boot_diff(a, b)
        hrows.append(dict(metric=col, n_pre=len(a), n_post=len(b), median_pre_m=float(a.median()), median_post_m=float(b.median()),
                          diff_m=obs, ci_lo=lo, ci_hi=hi, permutation_p=p,
                          mannwhitney_p=float(stats.mannwhitneyu(a, b, alternative="two-sided").pvalue)))
    HT = pd.DataFrame(hrows); HT.to_csv(A / "sfa_heterogeneity_detrended.csv", index=False, float_format="%.6g")
    f4 = pd.read_csv(F_F4).iloc[0]
    out["heterogeneity_reproduction"] = dict(published_pre=float(f4.median_pre), published_post=float(f4.median_post),
                                             reproduced_pre=float(HT.iloc[0].median_pre_m), reproduced_post=float(HT.iloc[0].median_post_m),
                                             published_n=f"{int(f4.n_pre)}/{int(f4.n_post)}", reproduced_n=f"{int(HT.iloc[0].n_pre)}/{int(HT.iloc[0].n_post)}")

    # figure: per-pass S_frame vs S_production
    fig, axes = plt.subplots(1, 2, figsize=(14, 5))
    ax = axes[0]
    b0 = per["production"].slope_theilsen_cm_km
    for k, col, mk in (("prod_plus_c_nearest", Z.C2, "D"), ("egg2015_no_tide", Z.C3, "o"), ("prod_plus_const_c", Z.C1, "s")):
        dS = per[k].slope_theilsen_cm_km.reindex(b0.index) - b0
        ax.plot(b0.values, dS.values, mk, color=col, ms=6, mec="white", label=f"{k}: max |ΔS| {dS.abs().max():.3g} cm/km")
    ax.axhline(0, color=Z.MUTED, lw=0.8); ax.set_xlabel("production per-pass Theil–Sen slope, cm/km"); ax.set_ylabel("S_frame − S_production, cm/km")
    ax.set_title(f"Per-pass slope change by vertical frame ({len(b0)} passes)", loc="left"); ax.legend(fontsize=7.5)
    ax = axes[1]
    hh = H[H.estimator == "Theil-Sen"].set_index("frame")
    yv = np.arange(len(hh))
    ax.errorbar(hh.diff_cm_km, yv, xerr=[hh.diff_cm_km - hh.diff_ci_lo, hh.diff_ci_hi - hh.diff_cm_km], fmt="o", color=Z.C1, capsize=4)
    ax.set_yticks(yv); ax.set_yticklabels(hh.index); ax.invert_yaxis(); ax.axvline(0, color=Z.MUTED, lw=0.8)
    ax.axvspan(grad["ci_lo"], grad["ci_hi"], color=Z.C2, alpha=0.2, label="95 % CI of the closure-offset gradient along the reach")
    ax.set_xlabel("post − pre median slope, cm/km (95 % bootstrap CI)"); ax.legend(fontsize=7.5, loc="lower right")
    ax.set_title("The headline contrast in every vertical frame", loc="left")
    fig.tight_layout(); fig.savefig(FIG / "F_S01_slope_frame_invariance.png"); plt.close(fig)

    man = dict(script=str(Path(__file__).relative_to(KG)), script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
               inputs={p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (F_PTS, F_ROBUST, F_SEG, F_CLS, F_C)}, **out)
    (A / "sfa_manifest.json").write_text(json.dumps(man, indent=2, default=str))
    pd.set_option("display.width", 250); pd.set_option("display.max_columns", 20)
    print(json.dumps(out, indent=1, default=str)); print(inv.round(6).to_string(index=False)); print(H.round(4).to_string(index=False))
    print(sup.round(4).to_string(index=False)); print(L.round(4).to_string(index=False)); print(HT.round(4).to_string(index=False))


if __name__ == "__main__":
    main()
