"""Python/Julia code generated from equations (docs_v2/EQUATION_KG_PLAN.md, phase 4a). No live stores."""
import json
import warnings

import numpy as np
import pytest

pytest.importorskip("antlr4")

from src.document.formula_code import annotate, generate, ident
from src.document.formula_structure import structure

warnings.simplefilter("ignore")


def gen(latex):
    s = structure(latex)
    assert s.status == "ok", s
    return generate(s.srepr, s.lhs), s


def run(code, name, *a, **kw):
    ns = {}
    exec(code.python, ns)
    return ns[name](*a, **kw)


def test_manning_explicit():
    c, _ = gen(r"Q=\frac{1}{n}AR^{2/3}S^{1/2}")
    assert (c.status, c.form, c.target, c.check) == ("ok", "explicit", "Q", "passed")
    assert run(c, "compute_Q", A=10.0, R=1.0, S=0.0004, n=0.02) == pytest.approx(10.0)
    assert "function compute_Q(A, R, S, n)" in c.julia


def test_nse_arrays_and_mean_default():
    c, _ = gen(r"\mathrm{NSE}=1-\frac{\sum_{i=1}^{n}(O_{i}-S_{i})^{2}}{\sum_{i=1}^{n}(O_{i}-\bar{O})^{2}}")
    kinds = {a["name"]: a["kind"] for a in c.args}
    assert kinds == {"O": "array", "S": "array", "NSE": "scalar", "O_bar": "mean"}
    o = np.array([1.0, 2, 3, 4])
    assert run(c, "compute_NSE", o, o) == pytest.approx(1.0)
    assert run(c, "compute_NSE", o, o + 0.5) == pytest.approx(0.8)
    assert "sum((O[i_] - S[i_]) .^ 2 for i_ in 1:n)" in c.julia


def test_pearson_product_of_sums():
    c, s = gen(r"r=\frac{\sum_{i=1}^{n}(x_i-\bar{x})(y_i-\bar{y})}{\sqrt{\sum_{i=1}^{n}(x_i-\bar{x})^2\sum_{i=1}^{n}(y_i-\bar{y})^2}}")
    x, y = np.array([1.0, 2, 3, 4, 5]), np.array([2.0, 4.1, 5.9, 8.2, 9.8])
    assert run(c, "compute_r", x, y) == pytest.approx(np.corrcoef(x, y)[0, 1])


def test_tendency_and_derivative_inputs():
    c, _ = gen(r"\frac{dS}{dt}=P-E-Q")
    assert (c.form, c.target) == ("tendency", "dS_dt")
    assert run(c, "compute_dS_dt", E=1.0, P=5.0, Q=2.0) == pytest.approx(2.0)


def test_solved_keeps_sums():
    c, _ = gen(r"1-\mathrm{NSE}=\frac{\sum_{i=1}^{n}(Q_{obs,i}-Q_{sim,i})^{2}}{\sum_{i=1}^{n}(Q_{obs,i}-\bar{Q})^{2}}")
    assert (c.form, c.target, c.check) == ("solved", "NSE", "passed")
    assert "sum(" in c.python.split("def residual")[0]


def test_scalar_and_array_with_one_base():
    c, _ = gen(r"P = \sum_{i=1}^{12} P_i")
    assert [a["name"] for a in c.args if a["kind"] == "array"] == ["P_i"]
    assert run(c, "compute_P", np.ones(12)) == pytest.approx(12.0)


def test_identifiers_are_safe():
    assert ident("lambda") == "lambda_" and ident("end") == "end_" and ident("Q_t-1") == "Q_t_1"
    assert ident("2x") == "x2x"


def test_annotate_documents_arguments():
    c, s = gen(r"Q=\frac{1}{n}AR^{2/3}S^{1/2}")
    from dataclasses import asdict
    row = {**asdict(c), "canonical_expression": s.canonical_expression}
    eq = {"equation_id": "p1:formula_3", "paper_id": "p1", "equation_number": "4", "page": 7,
          "parameters": json.dumps([{"symbol": "n", "description": "Manning roughness coefficient", "unit": "s m^-1/3"},
                                    {"symbol": "Q", "description": "discharge", "unit": "m3 s-1"}])}
    text = annotate(row, eq, "python")
    assert "Equation 4 of p1 (page 7)" in text
    assert "n: Manning roughness coefficient [s m^-1/3]  — quantity.manning_n" in text
    assert "A: (no definition found in the paper)" in text
    assert text.count('"""') == 2 and "def compute_Q" in text
    assert annotate(row, eq, "julia").startswith("# Equation 4 of p1")
