"""Quantity ontology: name normalisation, unit dimensions, the dimension check and the
graph rows (docs_v2/EQUATION_KG_PLAN.md, phase 1). No model, no live stores."""
from fractions import Fraction

import pandas as pd
import pytest

from src.ontology import quantity_map
from src.ontology.quantities import (check_dimension, is_junk, normalise, ontology, quantity_dimension,
                                     unit_dimension)


def test_ontology_aliases_unique_and_dimensions_parse():
    o = ontology()
    assert o["version"]
    seen = {}
    for q in o["by_id"].values():
        assert q["id"].startswith("quantity.")
        for v in q["dimension"].values():
            Fraction(v)
        for a in q["aliases"]:
            assert seen.setdefault(a.lower(), q["id"]) == q["id"], a


@pytest.mark.parametrize("name,qid,method", [
    ("water depth", "quantity.water_depth", "exact"),
    ("Manning's roughness coefficient", "quantity.manning_n", "exact"),
    ("observed discharge", "quantity.discharge", "stripped"),
    ("water depth at node i", "quantity.water_depth", "stripped"),
    ("routing reach length", "quantity.length", "head"),
    ("von karman constant", "quantity.von_karman_constant", "exact"),
    ("coefficient of discharge", "quantity.discharge_coefficient", "exact"),
    ("slope angle", "quantity.angle", "exact"),
    ("dynamic viscosity", "quantity.dynamic_viscosity", "exact"),
])
def test_normalise(name, qid, method):
    m = normalise(name)
    assert (m.quantity_id, m.method) == (qid, method)


@pytest.mark.parametrize("name", ["the", "number of", "of order one", "order one", "observed and", "explained in section 2.2.1"])
def test_junk_is_not_a_quantity(name):
    assert is_junk(name)
    assert normalise(name).method == "not_a_quantity"


@pytest.mark.parametrize("name", [
    "rain detected but not observed",         # a clause, not a noun phrase
    "satellite estimates and their average",
    "von karman something constant",          # a generic head never matches
])
def test_no_head_match_for_clauses_or_generic_heads(name):
    assert normalise(name).quantity_id is None


@pytest.mark.parametrize("unit,dim", [
    ("m3 s-1", {"L": 3, "T": -1}),
    ("m^3/s", {"L": 3, "T": -1}),
    ("m/s 2", {"L": 1, "T": -2}),            # detached exponent
    ("m s 21", {"L": 1, "T": -1}),           # PDF minus glyph read as "2"
    ("kg m 23", {"M": 1, "L": -3}),
    ("m^-1/3 s", {"L": Fraction(-1, 3), "T": 1}),
    ("mm/day", {"L": 1, "T": -1}),
    ("[L T-1]", {"L": 1, "T": -1}),
    ("L3/T", {"L": 3, "T": -1}),
    ("L 2", {"L": 2}),
    ("%", {}),
])
def test_unit_dimension(unit, dim):
    assert unit_dimension(unit) == {k: Fraction(v) for k, v in dim.items()}


def test_unreadable_unit_is_none():
    assert unit_dimension("bananas per fortnight") is None


@pytest.mark.parametrize("qid,unit,expected", [
    ("quantity.discharge", "m3 s-1", "ok"),
    ("quantity.manning_n", "s m^-1/3", "ok"),
    ("quantity.velocity", "m s 21", "ok"),
    ("quantity.volume", "L", "ok"),                       # litre
    ("quantity.volume", "mm", "ok_convention"),           # depth-equivalent storage
    ("quantity.precipitation", "mm d^-1", "ok_convention"),
    ("quantity.rainfall_intensity", "days", "mismatch"),
    ("quantity.discharge", "t", "unknown"),               # Q(t): the argument, not tonnes
    ("quantity.mean", "m", "unknown"),                    # statistics take the variable's dimension
    (None, "m", "unknown"),
])
def test_check_dimension(qid, unit, expected):
    assert check_dimension(qid, unit) == expected


def test_variable_dimension_statistics_are_not_checked():
    assert quantity_dimension("quantity.standard_deviation") is None


def test_map_resolve_prefers_map_and_falls_back_to_rules(tmp_path, monkeypatch):
    p = tmp_path / "quantity_map_test.parquet"
    pd.DataFrame([{"name": "kinematic viscosity of water", "quantity_id": "quantity.kinematic_viscosity",
                   "method": "embedding", "score": 0.92, "qualifiers": "[]", "frequency": 13}]).to_parquet(p)
    quantity_map.load_map.cache_clear()
    monkeypatch.setattr(quantity_map, "map_path", lambda version=None: p)
    try:
        assert quantity_map.resolve("kinematic viscosity of water").method == "embedding"
        assert quantity_map.resolve("water depth").quantity_id == "quantity.water_depth"
        assert quantity_map.resolve(None).quantity_id is None
    finally:
        quantity_map.load_map.cache_clear()


def test_build_from_equation_records(tmp_path):
    d = tmp_path / "paperA"; d.mkdir()
    params = [{"symbol": "Q", "description": "observed discharge", "unit": "m3 s-1"},
              {"symbol": "n", "description": "Manning roughness coefficient", "unit": "s m^-1/3"},
              {"symbol": "P", "description": "rainfall intensity", "unit": "days"},
              {"symbol": "x", "description": "number of", "unit": None}]
    import json
    pd.DataFrame([{"equation_id": "paperA:formula_0", "parameters": json.dumps(params)}]).to_parquet(
        d / "equation_records.parquet")
    qmap, dims = quantity_map.build(embed=False, sodb=tmp_path)
    by = qmap.set_index("name")
    assert by.loc["observed discharge", "quantity_id"] == "quantity.discharge"
    assert by.loc["number of", "method"] == "not_a_quantity"
    assert dict(zip(dims["symbol"], dims["dimension_check"])) == {"Q": "ok", "n": "ok", "P": "mismatch"}
    s = quantity_map.summary(qmap, dims)
    assert s["not_a_quantity_occurrences"] == 1 and s["dimension_check"]["mismatch"] == 1


def test_loader_quantity_rows():
    from src.graph.equation_kg_loader import quantity_rows
    concepts, links = quantity_rows({"observed discharge", "rain detected but not observed"})
    assert len(concepts) == len(ontology()["by_id"])
    assert all(c["canonical_id"].startswith("quantity.") for c in concepts)
    assert [(l["name"], l["canonical_id"], l["method"]) for l in links] == [
        ("observed discharge", "quantity.discharge", "stripped")]
