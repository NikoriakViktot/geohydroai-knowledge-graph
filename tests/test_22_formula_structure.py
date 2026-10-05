"""Structural hash of equations (docs_v2/EQUATION_KG_PLAN.md, phase 2). No live stores."""
import json

import pandas as pd
import pytest

pytest.importorskip("antlr4")

from src.document import formula_structure as fs
from src.document.formula_structure import prepare, structure


def h(latex):
    s = structure(latex)
    assert s.status == "ok", (latex, s)
    return s.structural_hash


@pytest.mark.parametrize("a,b", [
    (r"\[Q=\frac{1}{n}AR^{2/3}S^{1/2}\] (3)", r"Q = {1 \over n} A R^{2/3} S^{1/2}"),
    (r"Q=\frac{1}{n}AR^{2/3}S^{1/2}", r"Q=n^{-1}AR^{2/3}S^{1/2},"),
    (r"Q_{t}=\frac{(P_{t}-0.2S)^{2}}{(P_{t}+0.8S)}", r"Q_t=\frac{(P_t-\frac{1}{5}S)^2}{P_t+0.8S}"),
    (r"\mathrm{NSE}=1-\frac{\sum_{i=1}^{n}(O_{i}-S_{i})^{2}}{\sum_{i=1}^{n}(O_{i}-\bar{O})^{2}}",
     r"NSE = 1-\frac{\sum_{i=1}^{n}(O_i-S_i)^2}{\sum_{i=1}^{n}(O_i-\overline{O})^2}"),
    (r"a = b + c", r"c + b = a"),                                       # sides and terms in a fixed order
    (r"NDWI = (Green - NIR)/(Green + NIR)", r"\text{NDWI}=\frac{GREEN-NIR}{GREEN+NIR}"),
    (r"a=\frac{b}{c}=d", r"d=b/c=a"),                                   # chains
    (r"\left( a \right) \cdot b = c", r"(a) \times b = c"),
])
def test_same_structure(a, b):
    assert h(a) == h(b)


@pytest.mark.parametrize("a,b", [
    (r"Q = AV", r"V = Q/A"),              # a rearrangement is ALGEBRAIC (phase 3), not the same structure
    (r"Q = AV", r"q = av"),               # symbol names are kept
    (r"a = \sum_{i=1}^{n} x_i", r"a = \sum_{i=1}^{N} x_i"),
])
def test_different_structure(a, b):
    assert h(a) != h(b)


@pytest.mark.parametrize("latex,status", [
    (r"\[\begin{array}{l} a=b \\ c=d\end{array}\]", "multiline"),
    (r"c_{\rm{t}}=K\quad{\rm{and}}\quad\nu=1", "multiple"),
    (r"p", "trivial"),
    (r"a = b ]", "parse_error"),                                        # no silent truncation
    (r"x = y - y", "degenerate"),                                       # cancelled to 0
    (None, "no_latex"),
])
def test_statuses(latex, status):
    assert structure(latex).status == status


def test_names_and_subscripts_survive():
    s = structure(r"E_{\rm NS}=1-\frac{\sum _{i=1}^{n} (Q_{{\rm Sim}i}-Q_{{\rm mea}i})^{2}}"
                  r"{\sum _{i=1}^{n}(Q_{{\rm mea}i}-\overline{Q}_{\rm mea})^{2}}")
    assert s.status == "ok"
    assert {"E_NS", "Q_Simi", "Q_meai", "Q_barmea"} <= set(s.symbols)
    assert structure(r"\mathit{SI}=\frac{\mathit{RMSE}}{C_{\mathit{dActual}}}").symbols == ["C_dActual", "RMSE", "SI"]
    assert structure(r"x^{o}_{i} = a^{\prime}").symbols == ["a_prime", "o", "x_i"]   # subscript kept
    assert structure(r"\frac{dS}{dt}=P-E-Q").canonical_expression == "Eq(-E + P - Q, Derivative(S, t))"


def test_prepare_keeps_raw_untouched():
    raw = r"\[Q=\frac{1}{n}AR^{2/3}S^{1/2}\] (3)"
    prepared, status = prepare(raw)
    assert status == "ok" and prepared == r"Q=\frac{1}{n}AR^{2/3}S^{1/2}"


def test_build_cache_is_incremental(tmp_path):
    d = tmp_path / "paperA"; d.mkdir()
    pd.DataFrame([{"formula_hash": "f1", "latex": r"Q = AV"}, {"formula_hash": "tei:x", "latex": None},
                  {"formula_hash": "f2", "latex": r"V = Q/A"}]).to_parquet(d / "equation_records.parquet")
    cache = tmp_path / "structure.parquet"
    s1 = fs.build(sodb=tmp_path, cache_path=cache)
    assert s1["new"] == 2 and s1["status"] == {"ok": 2}
    s2 = fs.build(sodb=tmp_path, cache_path=cache)
    assert s2["new"] == 0 and s2["cached"] == 2
    rows = fs.load_cache(cache)
    assert json.loads(rows["f1"]["symbols"]) == ["A", "Q", "V"] and rows["f1"]["error"] is None
