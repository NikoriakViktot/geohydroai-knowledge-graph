"""
test_16_table_extractor.py — NumericFact екстракція з TEI-таблиць (Фаза 3.1).

Закриває прогалину 08_TESTING_AUDIT: table_extractor живив 16,308 NumericFact
у Neo4j без жодного тесту. Перевіряє і наукову коректність _VALUE_BOUNDS
(від'ємні NSE/Kappa в таблицях зберігаються — табличний шлях, на відміну від
regex-шляху, НЕ мав бага F-EXT-1).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from src.extraction.table_extractor import (
    extract_numeric_facts,
    _split_unit,
    _value_plausible,
    _VALUE_BOUNDS,
)

_TEI_TABLE = """<?xml version="1.0" encoding="UTF-8"?>
<TEI xmlns="http://www.tei-c.org/ns/1.0">
 <text><body>
  <figure type="table" coords="5,50,50,200,100">
   <head>Model performance</head><label>2</label>
   <table>
    <row><cell>Station</cell><cell>NSE</cell><cell>RMSE (m3/s)</cell><cell>Kappa</cell></row>
    <row><cell>Desna</cell><cell>0.78</cell><cell>145.2</cell><cell>0.81</cell></row>
    <row><cell>Boonville</cell><cell>-0.27</cell><cell>512.0</cell><cell>-0.12</cell></row>
    <row><cell>Broken</cell><cell>5.0</cell><cell>-3.1</cell><cell>2.0</cell></row>
   </table>
  </figure>
 </body></text>
</TEI>"""


@pytest.fixture()
def tei_table_file(tmp_path: Path) -> Path:
    p = tmp_path / "paper.tei.xml"
    p.write_text(_TEI_TABLE, encoding="utf-8")
    return p


def test_extracts_facts_with_units_and_provenance(tei_table_file):
    facts = extract_numeric_facts(tei_table_file, "test-paper")
    by = {(f.canonical_id, f.value): f for f in facts}

    assert ("metric.nse", 0.78) in by
    rmse = by[("metric.rmse", 145.2)]
    assert rmse.unit == "m3/s"                      # одиниця з заголовка колонки
    assert rmse.table_id == "test-paper_tbl_001"    # стабільний provenance-ID
    assert rmse.page == 5                           # з coords


def test_negative_values_preserved_in_tables(tei_table_file):
    """Від'ємні NSE/Kappa — валідні наукові результати (рядок Boonville)."""
    facts = extract_numeric_facts(tei_table_file, "test-paper")
    values = {(f.canonical_id, f.value) for f in facts}
    assert ("metric.nse", -0.27) in values
    assert ("metric.kappa", -0.12) in values


def test_implausible_values_rejected(tei_table_file):
    """NSE=5, RMSE=−3.1, Kappa=2.0 — артефакти парсингу, відкидаються."""
    facts = extract_numeric_facts(tei_table_file, "test-paper")
    values = {(f.canonical_id, f.value) for f in facts}
    assert ("metric.nse", 5.0) not in values
    assert ("metric.rmse", -3.1) not in values
    assert ("metric.kappa", 2.0) not in values


def test_fact_ids_deterministic(tei_table_file):
    """Повторна екстракція дає ті самі fact_id (ідемпотентність у Neo4j MERGE)."""
    ids_a = [f.fact_id for f in extract_numeric_facts(tei_table_file, "test-paper")]
    ids_b = [f.fact_id for f in extract_numeric_facts(tei_table_file, "test-paper")]
    assert ids_a == ids_b and len(ids_a) == len(set(ids_a))


def test_missing_and_invalid_tei(tmp_path):
    assert extract_numeric_facts(tmp_path / "nope.tei.xml", "x") == []
    bad = tmp_path / "bad.tei.xml"
    bad.write_text("<TEI><unclosed", encoding="utf-8")
    assert extract_numeric_facts(bad, "x") == []    # graceful, без винятку


# ── Наукова коректність bounds (регресія F-EXT-1 для табличного шляху) ───────

def test_value_bounds_allow_negative_efficiency():
    assert _VALUE_BOUNDS["metric.nse"][0] < 0
    assert _VALUE_BOUNDS["metric.kge"][0] < 0
    assert _VALUE_BOUNDS["metric.kappa"] == (-1.0, 1.0)
    assert _value_plausible("metric.nse", -0.27)
    assert not _value_plausible("metric.nse", 5.0)
    assert _value_plausible("metric.pbias", -12.5)


@pytest.mark.parametrize("header,name,unit", [
    ("RMSE (m³/s)", "RMSE", "m³/s"),
    ("NSE", "NSE", None),
    ("Kappa", "Kappa", None),
])
def test_split_unit(header, name, unit):
    assert _split_unit(header) == (name, unit)
