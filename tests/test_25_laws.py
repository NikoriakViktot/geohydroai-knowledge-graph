"""Law registry and evidence-scored EQUATION_INSTANCE_OF (docs_v2/EQUATION_KG_PLAN.md, phase 4b). No live stores."""
import json
import warnings

import pytest

pytest.importorskip("antlr4")

from src.document.formula_structure import structure
from src.ontology import laws

warnings.simplefilter("ignore")


def test_registry_forms_parse_and_code_passes():
    fms = laws.forms()
    reg = laws.registry()
    assert 15 <= len(reg["laws"]) <= 25
    assert {f.law_id for f in fms} == {l["id"] for l in reg["laws"]}
    assert all(f.code["check"] == "passed" for f in fms), [(f.law_id, f.variant) for f in fms if f.code["check"] != "passed"]
    ids = [l["id"] for l in reg["laws"]]
    assert len(ids) == len(set(ids))


def _link(cases):
    cache, rows = {}, []
    for k, (latex, lead, params) in enumerate(cases):
        s = structure(latex) if latex else None
        fh = f"h{k}"
        r = {"equation_id": f"p{k}:formula_0", "paper_id": f"p{k}", "formula_hash": fh, "lead_in": lead,
             "parameters": json.dumps(params)}
        if s is not None:
            cache[fh] = {"status": s.status, "kind": s.kind, "srepr": s.srepr, "structural_hash": s.structural_hash,
                         "symbols": json.dumps(s.symbols), "lhs": s.lhs}
        rows.append(r)
    df, _ = laws.link(cache=cache, rows=rows)
    return df


def best(df, eq):
    x = df[df.eq_id == eq].sort_values("score", ascending=False)
    return x.iloc[0] if len(x) else None


def test_quantity_pairing_accepts_other_notation():
    df = _link([(r"q = a v", "", [{"symbol": "q", "description": "discharge"},
                                  {"symbol": "a", "description": "cross-sectional area"},
                                  {"symbol": "v", "description": "flow velocity"}])])
    b = best(df, "p0:formula_0")
    assert (b.law_id, b.status, b.math_method) == ("law.continuity_q_av", "accepted", "algebraic_by_quantity")


def test_shape_alone_is_not_meaning():
    df = _link([(r"P=IV", "", [])])
    assert df[(df.law_id == "law.continuity_q_av") & (df.status == "accepted")].empty


def test_variant_is_found_and_hard_negative_rejected():
    df = _link([(r"Q=\frac{(P-0.05S)^{2}}{P+0.95S}", "the SCS curve number method", []),
                (r"E=1-\frac{\sum_{t=1}^{T}(Q_{o,t}-Q_{m,t})^{2}}{\sum_{t=1}^{T}(Q_{o,t}-\bar{Q_{o}})^{2}}", "Nash–Sutcliffe efficiency", []),
                (r"E=1-\frac{\sum_{t=1}^{T}(Q_{o,t}-Q_{m,t})}{\sum_{t=1}^{T}(Q_{o,t}-\bar{Q_{o}})^{2}}", "Nash–Sutcliffe efficiency", [])])
    b = best(df, "p0:formula_0")
    assert (b.law_id, b.variant, b.status) == ("law.scs_cn_runoff", "lambda_0.05", "accepted")
    b = best(df, "p1:formula_0")
    assert (b.law_id, b.status, b.math_method) == ("law.nse", "accepted", "renamed")
    nse2 = df[(df.eq_id == "p2:formula_0") & (df.law_id == "law.nse")]   # no square: text cannot outvote the formula
    assert nse2.empty or (nse2.status == "candidate").all()


def test_foreign_acronym_is_capped():
    assert laws._foreign_name("law.rmse", "MAE") and laws._foreign_name("law.ndvi", "NDWI")
    assert not laws._foreign_name("law.ndvi", "NDVI") and not laws._foreign_name("law.manning", "V")


def test_text_channel_weights():
    assert laws.s_text("law.manning", {"lead_in": "using Manning's equation"}) == 1.0
    assert laws.s_text("law.manning", {"section": "Manning roughness"}) == 0.6
    assert laws.s_text("law.manning", {"lead_in": "the discharge is"}) == 0.0
