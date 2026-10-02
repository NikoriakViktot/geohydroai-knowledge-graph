#!/usr/bin/env python
"""Paper 1 -- satellite against gauge, station by station, over each gauge's full record.

Every gauge gets its own panel; Rozumivka, the one reservoir gauge still recording after the breach,
also gets a 2019-2025 time series with both sensors.

Heights: satellite in EGG2015 with the mean-tide crust (ICESat-2: median_wse_evrs + free2mean; SWOT RiverSP:
wse + geoid_hght - zeta), gauge in EVRF2019 (BS-77 + D9902). Same calendar date only.
Support: ICESat-2 nearest gauge within 20 km on the level pre-breach pool, within 10 km on the sloping
post-breach river (and always within 10 km at Kherson); SWOT RiverSP nodes within 3 km, node_q <= 1.
Unit: one overpass (ICESat-2: date x RGT; SWOT: date x cycle x pass).
"""
from __future__ import annotations

import sys
from pathlib import Path

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
OUT = KG / "data/paper_1_audit/station_panels"
FIG = KG / "outputs/paper_1/station_panels"
OUT.mkdir(parents=True, exist_ok=True); FIG.mkdir(parents=True, exist_ok=True)
CIN = KG / "data/paper_1_audit/correlation/inputs"
RES = ["Plavni", "Rozumivka", "Blahovishchenka", "Nikopol", "Velyka Lepetykha", "Nova Kakhovka"]
BREACH = pd.Timestamp("2023-06-06"); POST0 = pd.Timestamp("2023-07-01")


def hav(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = map(np.radians, (lat1, lon1, lat2, lon2))
    a = np.sin((lat2 - lat1) / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2
    return 6371 * 2 * np.arcsin(np.sqrt(a))


def period(d):
    return np.select([d < BREACH, d < POST0], ["pre-breach", "drawdown"], "post-breach")


def main():
    zeta = VCM.egg2015()
    g = pd.read_parquet(CIN / "SWOT-DNIPRO/data/processed/gauges/gauge_levels_evrf2019.parquet")
    g = g[(g.qc == "ok") & (~g.fill_suspect)].copy(); g["date"] = pd.to_datetime(g.date)
    st = g.groupby("name_en")[["lat", "lon"]].first()
    gd = g[["date", "name_en", "H_evrf2019_m"]].rename(columns={"name_en": "station", "H_evrf2019_m": "gauge"})
    units = []
    # ICESat-2 over the reservoir footprint (all periods)
    p = pd.read_parquet(CIN / "icesat2-atl13-kakhovka/data/processed/kakhovka_atl13_pass_levels.parquet")
    p = p[p.qc_pass].copy(); p["date"] = pd.to_datetime(p.date)
    p["sat"] = p.median_wse_evrs_m + Z.free2mean(p.lat_mean)
    D = np.column_stack([hav(p.lat_mean.values, p.lon_mean.values, st.loc[s].lat, st.loc[s].lon) for s in RES])
    p["station"] = np.array(RES)[D.argmin(1)]; p["dist_km"] = D.min(1)
    p["period"] = period(p.date)
    p = p[((p.period == "pre-breach") & (p.dist_km <= 20)) | ((p.period != "pre-breach") & (p.dist_km <= 10))]
    u = p.groupby(["station", "date", "rgt", "period"]).agg(sat=("sat", "median"), dist_km=("dist_km", "median")).reset_index()
    units.append(u.assign(sensor="ICESat-2"))
    # ICESat-2 over the lower Dnipro, Kherson
    k = pd.read_parquet(CIN / "icesat2-atl13-kakhovka/data/processed/kherson_atl13_pass_levels.parquet"); k["date"] = pd.to_datetime(k.date)
    k["sat"] = k.median_wse_evrs_m + Z.free2mean(k.lat_mean)
    k["dist_km"] = hav(k.lat_mean.values, k.lon_mean.values, st.loc["Kherson"].lat, st.loc["Kherson"].lon)
    k = k[k.dist_km <= 10]; k["period"] = period(k.date)
    units.append(k.groupby(["date", "rgt", "period"]).agg(sat=("sat", "median"), dist_km=("dist_km", "median")).reset_index().assign(station="Kherson", sensor="ICESat-2"))
    # SWOT RiverSP nodes within 3 km of a gauge
    sw = pd.read_parquet(Z.OUT / "swot_riversp_nodes_zone.parquet")
    sw = sw[(sw.node_q <= 1) & (sw.dark_frac < 0.5)].copy()
    sw["date"] = sw.t_swot.dt.tz_localize(None).dt.floor("D")
    sw["sat"] = sw.wse + sw.geoid_hght - zeta(sw.lon, sw.lat)
    for s in RES + ["Kherson"]:
        d = hav(sw.lat.values, sw.lon.values, st.loc[s].lat, st.loc[s].lon)
        n = sw[d <= 3.0]
        if len(n):
            units.append(n.groupby(["date", "cycle", "pass_id"]).agg(sat=("sat", "median")).reset_index()
                         .assign(station=s, sensor="SWOT RiverSP", period=lambda x: period(x.date)))
    U = pd.concat(units, ignore_index=True).merge(gd, on=["date", "station"], how="inner")
    U["resid"] = U.sat - U.gauge
    U.to_csv(OUT / "station_units.csv", index=False, float_format="%.5f")

    rows = []
    for (s, sen, per), d in U.groupby(["station", "sensor", "period"]):
        r = dict(station=s, sensor=sen, period=per, n=len(d), first=str(d.date.min().date()), last=str(d.date.max().date()),
                 gauge_range_m=float(d.gauge.max() - d.gauge.min()), median_sat_minus_gauge_m=float(d.resid.median()), nmad_m=Z.nmad(d.resid))
        if len(d) >= 3 and d.gauge.std() > 0:
            r.update(pearson_r=float(stats.pearsonr(d.gauge, d.sat).statistic), spearman_rho=float(stats.spearmanr(d.gauge, d.sat).statistic),
                     theilsen_slope=float(stats.theilslopes(d.sat, d.gauge).slope))
        r["tier"] = "primary-eligible" if len(d) >= 20 else ("exploratory" if len(d) >= 10 else "descriptive-only")
        rows.append(r)
    S = pd.DataFrame(rows)
    S.to_csv(OUT / "station_summary.csv", index=False, float_format="%.4f")

    # ---- figure: one panel per gauge + Rozumivka time series
    col = {("ICESat-2", "pre-breach"): Z.C1, ("ICESat-2", "post-breach"): Z.C2, ("SWOT RiverSP", "post-breach"): Z.C3,
           ("SWOT RiverSP", "pre-breach"): "#184f95", ("ICESat-2", "drawdown"): Z.MUTED, ("SWOT RiverSP", "drawdown"): Z.MUTED}
    mk = {"ICESat-2": "o", "SWOT RiverSP": "D"}
    fig = plt.figure(figsize=(17, 13))
    gs = fig.add_gridspec(3, 4, height_ratios=[1, 1, 1.05])
    axes = [fig.add_subplot(gs[i // 4, i % 4]) for i in range(7)]
    for ax, s in zip(axes, ["Plavni", "Rozumivka", "Blahovishchenka", "Nikopol", "Velyka Lepetykha", "Nova Kakhovka", "Kherson"]):
        d = U[(U.station == s) & (U.period != "drawdown")]
        txt = []
        for (sen, per), q in d.groupby(["sensor", "period"]):
            ax.plot(q.gauge, q.sat, mk[sen], color=col[(sen, per)], ms=5.5, mec="white", mew=0.6, label=f"{sen}, {per} (n={len(q)})")
            r = S[(S.station == s) & (S.sensor == sen) & (S.period == per)].iloc[0]
            rr = f"r {r.pearson_r:.2f}" if np.isfinite(r.get("pearson_r", np.nan)) else "r –"
            txt.append(f"{sen.split()[0]} {per.split('-')[0]}: {rr}, n {int(r.n)}, c {-r.median_sat_minus_gauge_m * 100:+.1f} cm")
        if len(d):
            lo, hi = d.gauge.min(), d.gauge.max(); pad = 0.05 * (hi - lo + 0.1)
            cc = -np.median(d[d.period == "pre-breach"].resid) if (d.period == "pre-breach").any() else -np.median(d.resid)
            ax.plot([lo - pad, hi + pad], [lo - pad - cc, hi + pad - cc], "--", color=Z.MUTED, lw=1)
        ax.text(0.02, 0.98, "\n".join(txt), transform=ax.transAxes, va="top", fontsize=6.8,
                bbox=dict(boxstyle="round,pad=0.25", fc="white", ec=Z.GRID))
        ax.set_title(s, loc="left"); ax.set_xlabel("gauge, m EVRF2019", fontsize=8); ax.set_ylabel("satellite, m EGG2015", fontsize=8)
        ax.legend(fontsize=6.5, loc="lower right")
    ax = fig.add_subplot(gs[1, 3])
    ax.axis("off")
    ax.text(0.0, 0.95, "Each panel: satellite (EGG2015, mean-tide crust) against the\nsame-date gauge (EVRF2019). Dashed line: 1:1 shifted by the\nstation's median offset c. c is printed per series.\nICESat-2 ≤20 km on the pre-breach pool, ≤10 km on the\npost-breach river; SWOT RiverSP ≤3 km, node_q ≤1.\nn < 10: descriptive only.", va="top", fontsize=8.5, color=Z.INK2)
    ax = fig.add_subplot(gs[2, :])
    gr = g[g.name_en == "Rozumivka"].sort_values("date")
    ax.plot(gr.date, gr.H_evrf2019_m, color=Z.INK2, lw=0.9, label="Rozumivka gauge 80959, daily (EVRF2019)")
    d = U[U.station == "Rozumivka"]
    cpre = -np.median(d[(d.sensor == "ICESat-2") & (d.period == "pre-breach")].resid)
    for (sen, per), q in d.groupby(["sensor", "period"]):
        ax.plot(q.date, q.sat + cpre, mk[sen], color=col[(sen, per)], ms=5, mec="white", mew=0.5, label=f"{sen}, {per} + c_pre (n={len(q)})")
    ax.axvline(BREACH, color=Z.MUTED, ls=":", lw=1); ax.text(BREACH, ax.get_ylim()[1], " breach", va="top", fontsize=8, color=Z.MUTED)
    ax.set_title(f"Rozumivka 2019–2025: gauge and both satellites; satellites shifted by the pre-breach ICESat-2 offset c = {cpre * 100:+.1f} cm", loc="left")
    ax.set_ylabel("m"); ax.legend(fontsize=7.5, ncol=3, loc="lower left")
    fig.tight_layout(); fig.savefig(FIG / "F_ST01_station_panels.png"); plt.close(fig)
    pd.set_option("display.width", 250); pd.set_option("display.max_columns", 20)
    print(S.round(3).to_string(index=False))


if __name__ == "__main__":
    main()
