#!/usr/bin/env python
"""Paper 1 -- SWOT and ICESat-2 at the posts below the dam and in the estuary, 2023 (before, during, after the breach).

Gauges: UkrHMI 2023 yearbook of the seas and marine estuaries (Inv. No. 230, daily levels, BS-77 zero + EPSG:9902 grid
-> EVRF2019). Kherson additionally from the river hydrological yearbook (80805U_2023.xls, Table 1.2) used elsewhere in
the project; the two published daily series for the same post are compared directly.
SWOT: RiverSP nodes within 3 km (node_q <= 1, dark_frac < 0.5), h = wse + geoid_hght, H = h - zeta_EGG2015; for posts on
the limans, LakeSP polygons (quality_f <= 1) that contain the post, same chain. The June fast-sampling orbit (pass 001)
is in the RiverSP archive and gives daily coverage of the dam-to-estuary reach through the breach fortnight.
ICESat-2: ATL13 pass levels of the Kherson and Dnipro-Buh liman bodies within 10 km, H = median_wse_evrs + free2mean.
Periods: pre-breach (<= 2023-06-05), breach fortnight (06-06..06-30), post-breach (>= 07-01).
Gauge rows the yearbook flags as uncertain ("?") are kept and marked; no residual-based removal.
"""
from __future__ import annotations

import sys
from pathlib import Path

import geopandas as gpd
import matplotlib
import numpy as np
import pandas as pd
from scipy import stats

sys.path.insert(0, str(Path(__file__).resolve().parent))
import paper1_vertical_closure as VCM  # noqa: E402
import paper1_swot_icesat_zone as Z  # noqa: E402

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

KG = Path(__file__).resolve().parents[1]
OUT = KG / "data/paper_1_audit/downstream_posts_2023"
FIG = KG / "outputs/paper_1/downstream_posts_2023"
OUT.mkdir(parents=True, exist_ok=True); FIG.mkdir(parents=True, exist_ok=True)
SEA = KG / "data/paper_3_audit/swot_dnipro_snapshot/swot/data/historical/sea_posts_2023"
CIN = KG / "data/paper_1_audit/correlation/inputs"
F_EST = KG / "data/paper_1_audit/station_panels/inputs/icesat2-atl13-kakhovka/data/processed/dnipro_estuary_atl13_pass_levels.parquet"
POSTS = [80805, 80807, 98032, 98025, 98027, 98022]
BREACH, POST0 = pd.Timestamp("2023-06-06"), pd.Timestamp("2023-07-01")


def hav(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = map(np.radians, (lat1, lon1, lat2, lon2))
    a = np.sin((lat2 - lat1) / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2
    return 6371 * 2 * np.arcsin(np.sqrt(a))


def period(d):
    return np.select([d < BREACH, d < POST0], ["pre-breach", "breach fortnight"], "post-breach")


def main():
    zeta = VCM.egg2015()
    meta = pd.read_csv(SEA / "post_metadata_2023.csv").set_index("post_id")
    yb = pd.read_csv(SEA / "daily_levels_cm.csv"); yb["date"] = pd.to_datetime(yb.date)
    yb = yb[yb.post_id.isin(POSTS) & (yb.variable == "water_level")].dropna(subset=["H_evrf2019_m"])
    gauge = yb[["post_id", "date", "H_evrf2019_m", "flag"]].rename(columns={"H_evrf2019_m": "gauge"})

    # Kherson: river yearbook (project series) vs sea yearbook
    g = pd.read_parquet(CIN / "SWOT-DNIPRO/data/processed/gauges/gauge_levels_evrf2019.parquet"); g["date"] = pd.to_datetime(g.date)
    riv = g[(g.name_en == "Kherson") & (g.date.dt.year == 2023)][["date", "H_evrf2019_m"]].rename(columns={"H_evrf2019_m": "river_yb"})
    kk = riv.merge(gauge[gauge.post_id == 80805][["date", "gauge", "flag"]].rename(columns={"gauge": "sea_yb"}), on="date", how="outer")
    kk["diff_m"] = kk.river_yb - kk.sea_yb
    both = kk.dropna(subset=["diff_m"])
    dual = dict(n_common_days=len(both), median_diff_cm=float(both.diff_m.median() * 100), sd_cm=float(both.diff_m.std() * 100),
                nmad_cm=float(Z.nmad(both.diff_m) * 100), max_abs_cm=float(both.diff_m.abs().max() * 100),
                n_days_river_only=int(kk.sea_yb.isna().sum()), river_only_first=str(kk[kk.sea_yb.isna()].date.min().date()),
                river_only_last=str(kk[kk.sea_yb.isna()].date.max().date()))
    pd.DataFrame([dual]).to_csv(OUT / "kherson_two_yearbooks.csv", index=False, float_format="%.3f")
    kk.to_csv(OUT / "kherson_two_yearbooks_daily.csv", index=False, float_format="%.4f")

    units = []
    # SWOT RiverSP
    sw = pd.read_parquet(Z.OUT / "swot_riversp_nodes_zone.parquet")
    sw = sw[(sw.node_q <= 1) & (sw.dark_frac < 0.5)].copy()
    sw["date"] = sw.t_swot.dt.tz_localize(None).dt.floor("D"); sw = sw[sw.date.dt.year == 2023]
    sw["sat"] = sw.wse + sw.geoid_hght - zeta(sw.lon, sw.lat)
    for pid in POSTS:
        la, lo = meta.loc[pid, "lat"], meta.loc[pid, "lon"]
        n = sw[hav(sw.lat.values, sw.lon.values, la, lo) <= 3.0]
        if len(n):
            units.append(n.groupby(["date", "cycle", "pass_id"]).agg(sat=("sat", "median"), n_nodes=("node_id", "nunique")).reset_index()
                         .assign(post_id=pid, sensor="SWOT RiverSP"))
    # SWOT LakeSP polygons containing the post (limans)
    lk = gpd.read_parquet(Z.OUT / "swot_lakesp_obs_zone.parquet")
    lk = lk[(lk.quality_f <= 1) & (lk.dark_frac < 0.5) & (lk.t_swot.dt.year == 2023)].copy()
    pts = gpd.GeoDataFrame({"post_id": POSTS}, geometry=gpd.points_from_xy(meta.loc[POSTS, "lon"], meta.loc[POSTS, "lat"]), crs="EPSG:4326")
    j = gpd.sjoin(pts, lk, predicate="within")
    j = j[j.area_total <= 20.0]          # a lake-averaged level is only meaningful for a small, level water body
    if len(j):
        j["date"] = j.t_swot.dt.tz_localize(None).dt.floor("D")
        j["sat"] = j.wse + j.geoid_hght - zeta(pts.set_index("post_id").loc[j.post_id].geometry.x.values,
                                                pts.set_index("post_id").loc[j.post_id].geometry.y.values)
        units.append(j.groupby(["post_id", "date", "cycle", "pass_id"]).agg(sat=("sat", "median"), area_km2=("area_total", "median")).reset_index()
                     .assign(sensor="SWOT LakeSP"))
    # ICESat-2 (Kherson body + Dnipro-Buh liman body)
    ic = []
    for f in (CIN / "icesat2-atl13-kakhovka/data/processed/kherson_atl13_pass_levels.parquet", F_EST):
        d = pd.read_parquet(f); d["date"] = pd.to_datetime(d.date); ic.append(d[d.date.dt.year == 2023])
    ic = pd.concat(ic, ignore_index=True)
    ic["sat"] = ic.median_wse_evrs_m + Z.free2mean(ic.lat_mean)
    for pid in POSTS:
        la, lo = meta.loc[pid, "lat"], meta.loc[pid, "lon"]
        n = ic[hav(ic.lat_mean.values, ic.lon_mean.values, la, lo) <= 10.0]
        if len(n):
            units.append(n.groupby(["date", "rgt"]).agg(sat=("sat", "median")).reset_index().assign(post_id=pid, sensor="ICESat-2"))
    U = pd.concat(units, ignore_index=True)
    U = U.merge(gauge, on=["post_id", "date"], how="left")
    U["period"] = period(U.date); U["resid"] = U.sat - U.gauge
    U["name"] = U.post_id.map(meta.name_en)
    U.to_csv(OUT / "downstream_units_2023.csv", index=False, float_format="%.5f")

    rows = []
    for (pid, sen, per), d in U.dropna(subset=["gauge"]).groupby(["post_id", "sensor", "period"]):
        r = dict(post=meta.loc[pid, "name_en"], post_id=pid, sensor=sen, period=per, n=len(d),
                 n_flagged=int(d.flag.astype(str).str.contains(r"\?", regex=True).sum()),
                 gauge_range_m=float(d.gauge.max() - d.gauge.min()),
                 c_m=float(-d.resid.median()), nmad_m=Z.nmad(d.resid))
        if len(d) >= 3 and d.gauge.std() > 0:
            r.update(pearson_r=float(stats.pearsonr(d.gauge, d.sat).statistic), spearman_rho=float(stats.spearmanr(d.gauge, d.sat).statistic))
        rows.append(r)
    S = pd.DataFrame(rows); S.to_csv(OUT / "downstream_summary_2023.csv", index=False, float_format="%.4f")
    # SWOT dates without a gauge value (gauge gaps), by post
    gaps = U[U.gauge.isna()].groupby(["name", "sensor", "period"]).size().rename("n_sat_without_gauge").reset_index()
    gaps.to_csv(OUT / "downstream_gauge_gaps_2023.csv", index=False)

    # ---- figure: 2023 time series per post
    posts_plot = [p for p in POSTS if (U.post_id == p).any() or (gauge.post_id == p).any()]
    fig, axes = plt.subplots(len(posts_plot), 1, figsize=(15, 3.1 * len(posts_plot)), sharex=True)
    col = {"SWOT RiverSP": Z.C3, "SWOT LakeSP": "#184f95", "ICESat-2": Z.C1}
    mk = {"SWOT RiverSP": "D", "SWOT LakeSP": "s", "ICESat-2": "o"}
    for ax, pid in zip(axes, posts_plot):
        gg = gauge[gauge.post_id == pid].sort_values("date")
        full = gg.set_index("date").gauge.asfreq("D")
        ax.plot(full.index, full.values, color=Z.INK2, lw=1, label="gauge, daily (sea yearbook 2023)")
        if pid == 80805:
            rr = riv.set_index("date").river_yb.asfreq("D")
            ax.plot(rr.index, rr.values, color=Z.MUTED, lw=1, ls="--", label="Kherson, river yearbook (project series)")
        d = U[U.post_id == pid]
        c = float(-np.median(d[d.period == "pre-breach"].resid.dropna())) if d[d.period == "pre-breach"].resid.notna().any() else 0.0
        txt = []
        for sen, q in d.groupby("sensor"):
            ax.plot(q.date, q.sat + c, mk[sen], color=col[sen], ms=4.5, mec="white", mew=0.5, label=f"{sen} + c (n={len(q)})")
        for _, r in S[S.post_id == pid].iterrows():
            rr_ = f"r {r.pearson_r:.2f}" if np.isfinite(r.get("pearson_r", np.nan)) else "r –"
            txt.append(f"{r.sensor} {r.period}: n {int(r.n)}, {rr_}, c {r.c_m * 100:+.1f} cm, NMAD {r.nmad_m * 100:.1f} cm")
        ax.axvspan(BREACH, POST0, color=Z.C2, alpha=0.08)
        lo_, hi_ = np.nanmin(full.values) - 0.3, np.nanmax(full.values) + 0.3
        if pid == 80805:
            hi_ = max(hi_, float(np.nanmax(riv.river_yb)) + 0.3)
        off = int(((d.sat + c) < lo_).sum() + ((d.sat + c) > hi_).sum())
        ax.set_ylim(lo_, hi_)
        if off:
            ax.text(0.99, 0.02, f"{off} satellite point(s) outside the axis", transform=ax.transAxes, ha="right", fontsize=7, color=Z.MUTED)
        ax.set_title(f"{meta.loc[pid, 'name_en']} ({pid}) — {meta.loc[pid, 'water_body']}; satellites shifted by pre-breach c = {c * 100:+.1f} cm", loc="left", fontsize=9)
        ax.text(1.005, 0.98, "\n".join(txt), transform=ax.transAxes, va="top", fontsize=6.8, color=Z.INK2)
        ax.set_ylabel("m"); ax.legend(fontsize=6.5, loc="upper left", ncol=2)
    axes[-1].set_xlim(pd.Timestamp("2023-01-01"), pd.Timestamp("2023-12-31"))
    fig.suptitle("Posts below the dam and on the limans, 2023: gauge, SWOT (fast-sampling June orbit + science orbit) and ICESat-2; shaded: breach fortnight",
                 x=0.01, ha="left", fontsize=10)
    fig.tight_layout(rect=(0, 0, 0.8, 0.98)); fig.savefig(FIG / "F_DP01_downstream_posts_2023.png"); plt.close(fig)

    # zoom on the breach fortnight at Kherson and Kasperivka
    fig, axes = plt.subplots(1, 3, figsize=(19, 4.8))
    for ax, pid in zip(axes, (80805, 98025, 98027)):
        gg = gauge[gauge.post_id == pid].set_index("date").gauge.asfreq("D")
        ax.plot(gg.loc["2023-05-20":"2023-07-20"].index, gg.loc["2023-05-20":"2023-07-20"].values, "-o", ms=2.5, color=Z.INK2, lw=1, label="gauge, sea yearbook")
        if pid == 80805:
            rr = riv.set_index("date").river_yb.loc["2023-05-20":"2023-07-20"]
            ax.plot(rr.index, rr.values, "--", color=Z.MUTED, lw=1.2, label="gauge, river yearbook")
        d = U[(U.post_id == pid) & U.date.between("2023-05-20", "2023-07-20")]
        c = float(-np.median(U[(U.post_id == pid) & (U.period == "pre-breach")].resid.dropna())) if len(U[(U.post_id == pid) & (U.period == "pre-breach")].resid.dropna()) else 0.0
        for sen, q in d.groupby("sensor"):
            ax.plot(q.date, q.sat + c, mk[sen], color=col[sen], ms=6, mec="white", mew=0.6, label=f"{sen} + c")
        ax.axvspan(BREACH, POST0, color=Z.C2, alpha=0.08)
        ax.set_title(f"{meta.loc[pid, 'name_en']}: the breach fortnight, daily SWOT", loc="left"); ax.set_ylabel("m EVRF2019"); ax.legend(fontsize=7.5)
        ax.tick_params(axis="x", rotation=30)
    fig.tight_layout(); fig.savefig(FIG / "F_DP02_breach_fortnight.png"); plt.close(fig)

    # Kherson: SWOT against the river-yearbook series on the days the sea yearbook leaves empty
    ks = U[(U.post_id == 80805) & (U.sensor == "SWOT RiverSP")].merge(riv, on="date", how="left")
    ks["resid_river"] = ks.sat - ks.river_yb
    c_pre = float(-ks[ks.period == "pre-breach"].resid_river.median())
    rows_k = []
    for lab, sel in (("pre-breach", ks.period == "pre-breach"), ("breach fortnight, both yearbooks", (ks.period == "breach fortnight") & ks.gauge.notna()),
                     ("13 Jun - 8 Jul, river yearbook only", ks.gauge.isna() & ks.date.between("2023-06-13", "2023-07-08")),
                     ("post-breach 2023", ks.period == "post-breach")):
        q = ks[sel & ks.river_yb.notna()]
        if len(q):
            e = q.resid_river + c_pre
            rows_k.append(dict(subset=lab, n=len(q), median_after_pre_offset_cm=float(e.median() * 100), nmad_cm=float(Z.nmad(e) * 100),
                               max_abs_cm=float(e.abs().max() * 100), gauge_range_m=float(q.river_yb.max() - q.river_yb.min()),
                               pearson_r=float(stats.pearsonr(q.river_yb, q.sat).statistic) if len(q) >= 3 else np.nan))
    pd.DataFrame(rows_k).to_csv(OUT / "kherson_swot_vs_river_yearbook.csv", index=False, float_format="%.3f")
    print(pd.DataFrame(rows_k).round(3).to_string(index=False))
    pd.set_option("display.width", 250); pd.set_option("display.max_columns", 20)
    print(pd.DataFrame([dual]).T); print(S.round(3).to_string(index=False)); print(gaps.to_string(index=False))


if __name__ == "__main__":
    main()
