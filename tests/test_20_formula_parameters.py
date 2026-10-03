"""Formula parameters from the definition clause (src/document/formula_parameters.py).
Texts are Nougat page markdown from 10.5194/gmd-14-1037-2021 (WaterGAP 2.2d)."""
from src.document.formula_parameters import formula_parameters, is_unit, norm_symbol, render_parameters

PAGE = r"""The canopy storage \(S_{\text{c}}\) (mm) is calculated as

\[\frac{\text{d}S_{\text{c}}}{\text{d}t}=P-P_{\text{t}}-E_{\text{c}},\] (2)

where \(P\) is precipitation (mm d\({}^{-1}\)); \(P_{\text{t}}\) is throughfall, the fraction of \(P\) that reaches the soil (mm d\({}^{-1}\)); and \(E_{\text{c}}\) is evaporation from the canopy (mm d\({}^{-1}\)).

\[S_{\text{c,max}}=m_{\text{c}}\cdot L\] (4)

where \(m_{\text{c}}\) is 0.3 mm (Deardorff, 1978), and \(L\) (\(-\)) is the one-side leaf area index. \(L\) is a function of daily temperature.

\[P_{\text{eff}}=P_{\text{f}}-P_{\text{sn}}+M,\] (16)

where \(P_{\text{f}}\) is throughfall (mm d\({}^{-1}\); see Eq. 3), \(P_{\text{sn}}\) is snowfall (mm d\({}^{-1}\); see Eq. 12) and \(M\) is snowmelt (mm d\({}^{-1}\); see Eq. 13).

\[P_{\text{sn}}=P_{\text{t}}\ \text{if}\ T<T_{\text{f}}\] (12)

where \(T\) is daily air temperature (\({}^{\circ}\)C) and \(T_{\text{f}}\) is snow freeze temperature, set to 0 \({}^{\circ}\)C.
"""


def params_of(number: str) -> dict:
    import re
    m = re.search(r"\\\[[^\]]*?\\\]\s*\(" + number + r"\)", PAGE, re.S)
    eq = m.group(0)
    return {p["symbol"]: p for p in formula_parameters(PAGE, m.start(), m.end(), eq)}


def test_where_clause_symbols_units_and_preceding_definition():
    p = params_of("2")
    assert set(p) == {"P", "P_t", "E_c", "S_c"}
    assert p["P"]["description"] == "precipitation" and p["P"]["unit"] == "mm d^-1"
    assert p["P_t"]["description"].startswith("throughfall")
    assert p["S_c"]["source"] == "before" and p["S_c"]["unit"] == "mm"


def test_constant_value_unit_and_citation_kept_out():
    p = params_of("4")
    assert p["m_c"]["value"] == "0.3" and p["m_c"]["unit"] == "mm"
    assert p["L"]["unit"] == "-" and "leaf area index" in p["L"]["description"]
    assert "function of daily temperature" not in p["L"]["description"]


def test_semicolon_inside_unit_parentheses_does_not_split():
    p = params_of("16")
    assert {k: v["unit"] for k, v in p.items()} == {"P_f": "mm d^-1", "P_sn": "mm d^-1", "M": "mm d^-1"}
    assert p["M"]["description"] == "snowmelt"


def test_degree_unit_and_set_to_value():
    p = params_of("12")
    assert p["T"]["unit"] == "°C"
    assert p["T_f"]["value"] == "0" and p["T_f"]["unit"] == "°C"


def test_symbols_not_in_the_equation_are_not_its_parameters():
    p = params_of("16")
    assert "T" not in p and "L" not in p


def test_unit_whitelist():
    assert all(is_unit(u) for u in ["mm d^-1", "m3 s^-1", "kPa °C^-1", "-", "%", "W m^-2"])
    assert not any(is_unit(u) for u in ["semi", "or local", "in the case", "50", "e.g., monthly streamflow"])


def test_norm_symbol_and_render():
    assert norm_symbol(r"S_{\text{c,max}}") == "S_c,max"
    assert norm_symbol(r"\left\langle \overline{u}\right\rangle") == "u"
    txt = render_parameters([{"symbol": "m_c", "description": "", "unit": "mm", "value": "0.3"}])
    assert txt == "where\nm_c = 0.3 [mm]"


def test_glued_degree_unit():
    assert is_unit(r"kPa\({}^{\circ}\)C\({}^{-1}\)") and is_unit("kPa°C^-1")
