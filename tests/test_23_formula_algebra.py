"""ALGEBRAIC equivalence by candidate generation (docs_v2/EQUATION_KG_PLAN.md, phase 3). No live stores."""
import json
import warnings

import pandas as pd
import pytest

pytest.importorskip("antlr4")

from src.document import formula_algebra as fa
from src.document.formula_structure import structure

warnings.simplefilter("ignore")


def sides(latex):
    e = fa.to_expr(structure(latex).srepr)
    return (e.lhs, e.rhs)


@pytest.mark.parametrize("a,b", [
    (r"Q=AV", r"V=\frac{Q}{A}"),
    (r"S=\frac{25400}{CN}-254", r"S=\frac{25400-254CN}{CN}"),
    (r"Q=\frac{1}{n}AR^{2/3}S^{1/2}", r"n=\frac{A R^{2/3} S^{1/2}}{Q}"),
    (r"\frac{dS}{dt}=P-E-Q", r"P=\frac{dS}{dt}+E+Q"),            # derivatives are shared opaque terms
    (r"Q = a \cdot (H - z)^{b}", r"\log Q = \log a + b \log(H - z)"),   # rating curve; "a (H - z)" would parse as a function
])
def test_algebraic(a, b):
    assert fa.equivalent(sides(a), sides(b))[0] == "ALGEBRAIC"


@pytest.mark.parametrize("a,b", [
    (r"Q=AV", r"Q=A+V"),
    (r"Q=\frac{(P-0.2S)^2}{P+0.8S}", r"Q=\frac{(P-0.05S)^2}{P+0.95S}"),   # SCS-CN, λ = 0.2 vs 0.05
    (r"y=x^{2}", r"y=x"),
])
def test_different(a, b):
    assert fa.equivalent(sides(a), sides(b))[0] == "DIFFERENT"


def test_symbol_key():
    assert fa.symbol_key("N_{h,max}") == "N_hmax"
    assert fa.symbol_key(r"l(\theta)") == "l"
    assert fa.param_symbols([{"symbol": "a"}], {"a_i", "b"}) == {"a_i": {"symbol": "a"}}
    assert fa.param_symbols([{"symbol": "a"}], {"a_i", "a_j"}) == {}          # ambiguous base


def _eq(eq_id, latex, tokens=None):
    st = structure(latex)
    return fa.Eq(eq_id, st.structural_hash, st.srepr, tokens or {})


def test_quantity_blocks_pair_different_notations():
    a = _eq("p1:f0", r"v_{i}=\frac{1}{n_{i}}R_{i}^{2/3}S_{i}^{1/2}",
            {"v_i": "quantity.velocity", "n_i": "quantity.manning_n", "R_i": "quantity.hydraulic_radius",
             "S_i": "quantity.slope"})
    b = _eq("p2:f3", r"n=\frac{1}{\bar{v}}R_{h}^{2/3}S^{1/2}",
            {"v_bar": "quantity.velocity", "n": "quantity.manning_n", "R_h": "quantity.hydraulic_radius",
             "S": "quantity.slope"})
    c = _eq("p3:f1", r"P=IV", {"P": "quantity.power", "I": "quantity.current", "V": "quantity.voltage"})
    bl, skipped = fa.blocks([a, b, c], max_block=40)
    assert skipped == 0
    assert [k[0] for k in bl] == ["quantity"]                                     # P = IV meets nobody
    r = fa.check_pair(a, b, "quantity")
    assert r["verdict"] == "ALGEBRAIC"
    assert r["mapping"] == {"v_bar": "v_i", "n": "n_i", "R_h": "R_i", "S": "S_i"}


def test_pairings_try_every_order_within_a_concept():
    a = _eq("p1:f0", r"\frac{dS}{dt}=I-O", {"I": "quantity.discharge", "O": "quantity.discharge"})
    b = _eq("p2:f0", r"\frac{dS}{dt}=Q_{in}-Q_{out}", {"Q_in": "quantity.discharge", "Q_out": "quantity.discharge"})
    assert len(fa.pairings(a, b, "mixed")) == 2


def test_run_writes_one_row_per_structure_pair(tmp_path):
    d = tmp_path / "paperA"; d.mkdir()
    latex = {"f1": r"Q=AV", "f2": r"V=\frac{Q}{A}", "f3": r"Q=A+V"}
    pd.DataFrame([{"equation_id": f"paperA:{k}", "formula_hash": k, "parameters": "[]"} for k in latex]
                 ).to_parquet(d / "equation_records.parquet")
    cache = {}
    for k, v in latex.items():
        st = structure(v)
        cache[k] = {"status": st.status, "kind": st.kind, "srepr": st.srepr,
                    "structural_hash": st.structural_hash, "symbols": json.dumps(st.symbols)}
    df, stats = fa.run(sodb=tmp_path, cache=cache)
    assert stats["pairs"] == 3
    assert sorted(df["verdict"]) == ["ALGEBRAIC", "DIFFERENT", "DIFFERENT"]
