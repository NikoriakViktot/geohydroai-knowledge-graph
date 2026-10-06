"""/v1/equations, /v1/quantities, /v1/laws with a fake Neo4j dispatched on the '// name' line.
No live stores; user values must reach Cypher only as parameters."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from src.api.app import create_app
from src.services import graph_read

from tests.test_api import READ_KEY, StubManifest
from tests.test_api_graph import Keys

H = {"X-API-Key": READ_KEY}
EQ = "10.1007_s11069-015-1926-0:formula_5"
SH = "abc123"
NSE_PY = '''import numpy


def compute_NSE(O, S, O_bar=None):
    if O_bar is None:
        O_bar = numpy.mean(O)
    n = len(O)
    return 1 - (sum((O[i_ - 1] - S[i_ - 1])**2 for i_ in range(1, n + 1)))/(sum((-O_bar + O[i_ - 1])**2 for i_ in range(1, n + 1)))
'''
EQUATION = {"eq_id": EQ, "paper_id": "10.1007_s11069-015-1926-0", "xml_id": "formula_5", "equation_number": "6",
            "page": 12, "latex_raw": r"\[NSE=1-\frac{\sum(O_i-S_i)^2}{\sum(O_i-\bar{O})^2}\]",
            "formula_structural_hash": SH, "canonical_expression": "Eq(1 - …, NSE)", "structure_status": "ok",
            "formula_text_hash": "t1", "image_path": "data/x.png", "image_sha256": "f" * 64}
LINK = {"law_id": "law.nse", "name": "Nash–Sutcliffe efficiency", "status": "accepted", "score": 0.62, "variant": None,
        "s_quantity": 0.33, "s_math": 0.8, "s_text": 1.0, "s_concept": 1.0, "math_method": "renamed",
        "mapping": json.dumps({"Q_obs": "O_i"}), "capped": None}


class Fake:
    def __init__(self):
        self.calls = []

    def answer(self, name, p):
        if name == "equation":
            return [{"e": EQUATION, "paper": {"paper_id": EQUATION["paper_id"], "doi": "10.1007/s11069-015-1926-0",
                                              "title": "T", "year": 2015}, "computes": []}] if p["eq_id"] == EQ else []
        if name == "equation_parameters":
            return [{"symbol": r"\bar{O}", "description": "the mean of the observed data", "unit": None, "symbol_tex": None,
                     "value": None, "source": "after", "quantity": "mean of the observed data",
                     "quantity_id": "quantity.mean", "dimension_check": None}]
        if name == "equation_laws":
            return [LINK]
        if name == "equation_exact":
            return [{"eq_id": "other:formula_1", "paper_id": "other"}]
        if name == "equation_algebraic":
            return []
        if name == "equation_code":
            return [{"status": "ok", "form": "explicit", "target": "NSE", "check": "passed",
                     "args": json.dumps([{"name": "O", "symbol": "O", "kind": "array"},
                                         {"name": "S", "symbol": "S", "kind": "array"},
                                         {"name": "O_bar", "symbol": "O_bar", "kind": "mean", "default": "O"}]),
                     "python": NSE_PY, "julia": "function compute_NSE(O, S, O_bar=nothing)\nend\n",
                     "canonical_expression": "Eq(1 - …, NSE)"}]
        if name == "chain_concepts":
            return [{"concepts": [{"label": "Metric", "canonical_id": "metric.nse", "via": "DEFINES_METRIC"}]}]
        if name == "chain_facts":
            return [{"fact_id": "f1", "metric": "metric.nse", "value": 0.81, "unit": None, "table_label": "Table 3",
                     "page": 9, "raw_cell": "0.81", "row_context": "calibration", "col_header": "NSE"}]
        if name == "equation_search":
            item = {"total": 1, "eq_id": EQ, "paper_id": EQUATION["paper_id"], "title": "T", "year": 2015,
                    "equation_number": "6", "page": 12, "latex_raw": "x", "canonical_expression": "e", "purpose": None,
                    "structure_status": "ok", "laws": [{"law_id": "law.nse", "status": "accepted", "score": 0.62}],
                    "matched_parameters": []}
            return [item]
        if name in ("quantity_counts",):
            return [{"quantity_id": "quantity.discharge", "parameters": 28, "equations": 20}]
        if name == "quantity_names":
            return [{"name": "river discharge", "method": "exact", "score": 1.0, "parameters": 4}]
        if name == "quantity_units":
            return [{"unit": "m3 s-1", "dimension_check": "ok", "parameters": 3}]
        if name == "quantity_laws":
            return [{"law_id": "law.manning", "name": "Manning equation", "symbol": "Q"}]
        if name == "quantity_totals":
            return [{"parameters": 28, "equations": 20, "papers": 9}]
        if name == "law_counts":
            return [{"law_id": "law.nse", "accepted_equations": 22, "accepted_papers": 21, "candidate_equations": 162}]
        if name == "law_instances":
            return [{"total": 1, "eq_id": EQ, "paper_id": EQUATION["paper_id"], "title": "T", "year": 2015, "page": 12,
                     "latex_raw": "x", **{k: v for k, v in LINK.items() if k not in ("law_id", "name")}}]
        return []

    def run_read(self, query, params=None, limit=1000):
        name = query.split("\n", 1)[0].removeprefix("// ").strip()
        self.calls.append((name, query, dict(params or {})))
        rows = self.answer(name, params or {})
        cols = list(rows[0]) if rows else []
        return cols, [[graph_read._plain(v) for v in r.values()] for r in rows][:limit], len(rows) > limit


@pytest.fixture
def fake(monkeypatch):
    f = Fake()
    monkeypatch.setattr(graph_read, "run_read", f.run_read)
    return f


@pytest.fixture
def client(fake):
    return TestClient(create_app(key_store=Keys(), manifest=StubManifest()))


def test_equation_with_annotated_code(client, fake):
    body = client.get(f"/v1/equations/{EQ}", headers=H).json()
    assert body["equation"]["latex_raw"].startswith(r"\[NSE") and body["paper"]["year"] == 2015
    assert body["laws"][0]["mapping"] == {"Q_obs": "O_i"}
    assert body["equivalents"]["exact"] == [{"eq_id": "other:formula_1", "paper_id": "other"}]
    code = body["code"]
    assert code["check"] == "passed" and "def compute_NSE" in code["python"]
    assert "Equation 6 of 10.1007_s11069-015-1926-0 (page 12)" in code["python_annotated"]
    # the accented parameter documents O_bar, not the array O
    assert "O_bar (default: mean of O): the mean of the observed data" in code["python_annotated"]
    assert "O (array): (no definition found in the paper)" in code["python_annotated"]
    assert all(EQ not in q for _, q, _ in fake.calls)                     # bound, never interpolated


def test_equation_errors_and_include(client):
    assert client.get("/v1/equations/nope:formula_0", headers=H).status_code == 404
    assert client.get(f"/v1/equations/{EQ}", headers=H, params={"include": "bogus"}).status_code == 400
    body = client.get(f"/v1/equations/{EQ}", headers=H, params={"include": "laws"}).json()
    assert body["parameters"] is None and body["code"] is None and body["laws"]


def test_chain_reaches_reported_values(client, fake):
    body = client.get(f"/v1/equations/{EQ}/chain", headers=H).json()
    assert body["quantities"] == [{"quantity_id": "quantity.mean", "label": "mean", "dimension": "1",
                                   "symbols": [r"\bar{O}"]}] or body["quantities"][0]["quantity_id"] == "quantity.mean"
    assert body["concepts"][0]["canonical_id"] == "metric.nse"
    assert body["reported_values"][0]["value"] == 0.81
    facts = next(p for n, _, p in fake.calls if n == "chain_facts")
    assert facts["metrics"] == ["metric.nse"]


def test_search_resolves_names_and_validates(client, fake):
    body = client.get("/v1/equations/search", headers=H, params={"quantity": "river discharge"}).json()
    assert body["resolved"]["quantity_id"] == "quantity.discharge" and body["count"] == 1
    params = next(p for n, _, p in fake.calls if n == "equation_search")
    assert params["quantity"] == "quantity.discharge" and params["law"] is None
    assert client.get("/v1/equations/search", headers=H).status_code == 422                 # no filter
    assert client.get("/v1/equations/search", headers=H, params={"law": "law.nope"}).status_code == 422
    assert client.get("/v1/equations/search", headers=H, params={"quantity": "zzqx"}).status_code == 422


def test_quantities(client):
    body = client.get("/v1/quantities", headers=H, params={"q": "discharge"}).json()
    top = body["items"][0]
    assert top["quantity_id"] == "quantity.discharge" and top["parameters"] == 28 and top["dimension"] == "L3 T-1"
    one = client.get("/v1/quantities/quantity.discharge", headers=H).json()
    assert one["counts"]["papers"] == 9 and one["laws"][0]["law_id"] == "law.manning"
    assert client.get("/v1/quantities/quantity.nope", headers=H).status_code == 404


def test_normalize_needs_no_database(client, fake):
    r = client.post("/v1/quantities/normalize", headers=H,
                    json={"items": [{"name": "observed discharge", "unit": "m s 21"}, {"name": "the"}]})
    items = r.json()["items"]
    assert (items[0]["quantity_id"], items[0]["dimension_check"]) == ("quantity.discharge", "ok_convention")
    assert items[1]["method"] == "not_a_quantity"
    assert not fake.calls
    assert client.post("/v1/quantities/normalize", headers=H, json={"items": []}).status_code == 422


def test_laws(client):
    body = client.get("/v1/laws", headers=H).json()
    nse = next(i for i in body["items"] if i["law_id"] == "law.nse")
    assert nse["accepted_papers"] == 21 and body["weights"]["math"] == 0.4
    one = client.get("/v1/laws/law.nse", headers=H).json()
    assert one["forms"] and one["forms"][0]["code_check"] == "passed" and "def compute_NSE" in one["forms"][0]["code_python"]
    assert one["instances"][0]["s_math"] == 0.8 and one["count"] == 1
    assert client.get("/v1/laws/law.nope", headers=H).status_code == 404


def test_scope_required(client):
    assert client.get("/v1/laws").status_code == 401
