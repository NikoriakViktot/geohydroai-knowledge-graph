"""Paper 1 vertical-reference closure: algebra, slope frame invariance, derived evidence tables."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(SCRIPTS))
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def vc():
    pytest.importorskip("rasterio"); pytest.importorskip("geopandas"); pytest.importorskip("pyogrio")
    return _load("paper1_vertical_closure")


@pytest.fixture(scope="module")
def sfa():
    pytest.importorskip("rasterio"); pytest.importorskip("geopandas"); pytest.importorskip("pyogrio")
    return _load("paper1_slope_frame_audit")


# ---- closure algebra -----------------------------------------------------------------------------------
def test_closure_counts_d9902_once(vc):
    # r = (H_BS77 + D9902) - (h - zeta); a gauge already in EVRF2019 must not receive D9902 again
    H_bs77, d9902, h, zeta = 15.8, 0.19, 38.0, 22.3
    r = vc.closure_residual(H_bs77, d9902, h, zeta)
    assert r == pytest.approx((H_bs77 + d9902) - (h - zeta))
    assert r != pytest.approx((H_bs77 + 2 * d9902) - (h - zeta))


def test_free2mean_matches_atl03_formula(vc):
    lat = np.array([46.5, 47.0, 47.9])
    assert np.allclose(vc.Z.free2mean(lat), 0.06029 - 0.180873 * np.sin(np.radians(lat)) ** 2)
    assert (vc.Z.free2mean(lat) < 0).all()          # at these latitudes the term lowers a tide-free height


# ---- slope frame invariance (the production fit, copied into the audit) ---------------------------------
def _profile(slope_cm_km=3.0, seed=0):
    rng = np.random.default_rng(seed)
    x = np.linspace(0, 40, 8)
    return pd.DataFrame({"date": "2024-01-01", "period": "POST_BREACH", "chain_km": x,
                         "wse_m": 5 + slope_cm_km / 100 * x + rng.normal(0, 0.01, x.size)})


def test_constant_offset_leaves_slope_unchanged(sfa):
    p = _profile()
    a = sfa.fit(p, 20.0).slope_theilsen_cm_km.iloc[0]
    b = sfa.fit(p.assign(wse_m=p.wse_m - 0.135), 20.0).slope_theilsen_cm_km.iloc[0]
    assert abs(a - b) < 1e-9


def test_linear_offset_shifts_slope_by_its_gradient(sfa):
    p = _profile()
    g = 0.0005                                         # m per km = 0.05 cm/km
    a = sfa.fit(p, 20.0).slope_ols_cm_km.iloc[0]
    b = sfa.fit(p.assign(wse_m=p.wse_m + g * p.chain_km), 20.0).slope_ols_cm_km.iloc[0]
    assert b - a == pytest.approx(g * 100, abs=1e-9)


def test_step_offset_changes_slope(sfa):
    p = _profile()
    step = np.where(p.chain_km > 20, 0.07, 0.0)       # a Voronoi-cell boundary crossing mid-profile
    a = sfa.fit(p, 20.0).slope_theilsen_cm_km.iloc[0]
    b = sfa.fit(p.assign(wse_m=p.wse_m + step), 20.0).slope_theilsen_cm_km.iloc[0]
    assert abs(b - a) > 0.05


# ---- derived evidence tables (skip where the local audit outputs are absent) -----------------------------
DER = ROOT / "data" / "paper_3_audit" / "derived"


def _derived(name):
    f = DER / f"{name}.csv"
    if not f.exists():
        pytest.skip(f"{f} not built")
    return pd.read_csv(f, dtype=str, keep_default_na=False)


def test_gauge_agreement_applies_mean_tide_term():
    r = _derived("gauge_icesat_agreement").iloc[0]
    assert float(r.corrector_mean_m) == pytest.approx(float(r.upstream_mean_m) - float(r.free2mean_applied_m), abs=2e-3)
    assert float(r.corrector_mean_m) > float(r.upstream_mean_m)


def test_slope_audit_constant_offset_is_exactly_invariant():
    t = _derived("slope_frame_audit").set_index("key")
    assert float(t.loc["const", "max_dS"]) < 1e-9
    assert float(t.loc["frame", "repro_cm_km"]) < 1e-9


def test_closure_offsets_have_expected_keys():
    t = _derived("vertical_closure_offsets")
    for k in ("Kherson|SWOT||PRE_BREACH|≤3 km", "Kherson|ICESat-2||PRE_BREACH|≤10 km", "Rozumivka|SWOT||POST_BREACH|≤3 km"):
        assert k in set(t.key), k
