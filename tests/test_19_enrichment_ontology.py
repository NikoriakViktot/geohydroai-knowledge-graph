"""
test_19_enrichment_ontology.py — OpenAlex enrichment + merge_ontology (Фаза 3.4).

Закриває F-NORM-1 (merge_ontology: 1,166 LOC вирішують наукову ідентичність
сутностей без тестів) і F-NORM-3 (enrichment: кеш/DOI/помилки без тестів).
OpenAlexActor тестується як plain-клас через __ray_metadata__.modified_class —
без Ray-кластера і мережі (HTTP мокається).
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

# Ray-декоратор ховає клас; modified_class — оригінал без remote-обгортки
from src.actors.openalex_actor import OpenAlexActor
_PlainActor = OpenAlexActor.__ray_metadata__.modified_class

from src.ontology.merge_ontology import (
    Registry, _merge_into, _make_id, _norm_type, _merge_lists,
)


# ── OpenAlex DOI нормалізація ─────────────────────────────────────────────────

@pytest.mark.parametrize("raw,clean", [
    ("10.3390/RS6065067",               "10.3390/rs6065067"),
    ("https://doi.org/10.1002/x.123",   "10.1002/x.123"),
    ("doi:10.1088/1748-9326/ac4d4f",    "10.1088/1748-9326/ac4d4f"),
    ("  10.1111/jfr3.12303  ",          "10.1111/jfr3.12303"),
])
def test_clean_doi(raw, clean):
    assert _PlainActor._clean_doi(raw) == clean


# ── OpenAlex кеш: один HTTP-запит на DOI ──────────────────────────────────────

def _actor_with_mock_session(tmp_path, response_json=None, status=200):
    actor = _PlainActor(cache_path=tmp_path / "cache.json")
    session = MagicMock()
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = response_json or {"id": "W1", "title": "T"}
    if status >= 400:
        import requests
        resp.raise_for_status.side_effect = requests.HTTPError(f"{status}")
    else:
        resp.raise_for_status.return_value = None
    session.get.return_value = resp
    actor.session = session
    return actor, session


def test_doi_cache_prevents_duplicate_http(tmp_path):
    actor, session = _actor_with_mock_session(tmp_path)
    w1 = actor.get_work_by_doi("10.3390/rs6065067")
    w2 = actor.get_work_by_doi("https://doi.org/10.3390/RS6065067")  # той самий DOI
    assert w1 == w2
    assert session.get.call_count == 1, "другий виклик має йти з кешу"
    # кеш персистентний на диску
    cached = json.loads((tmp_path / "cache.json").read_text())
    assert "10.3390/rs6065067" in cached


def test_http_429_propagates_for_retry(tmp_path):
    """429 НЕ ковтається — пропливає, щоб retry_call міг повторити."""
    import requests
    actor, _ = _actor_with_mock_session(tmp_path, status=429)
    with pytest.raises(requests.HTTPError):
        actor.get_work_by_doi("10.1234/throttled")
    # невдалий результат НЕ закешований
    assert not (tmp_path / "cache.json").exists() or \
        "10.1234/throttled" not in json.loads((tmp_path / "cache.json").read_text())


# ── merge_ontology: ідемпотентність і типова безпека ──────────────────────────

def _entity(eid="method.test", name="Test", **extra):
    base = {
        "id": eid, "display_name": name, "type": eid.split(".")[0],
        "definition": "", "aliases": [], "used_for": [], "inputs": [],
        "outputs": [], "limitations": [], "related": [], "contexts": [],
        "domain": "", "source_files": [],
    }
    base.update(extra)
    return base


def test_merge_into_idempotent():
    """merge(merge(x, p), p) == merge(x, p) — повторний merge нічого не змінює."""
    target = _entity(definition="short", aliases=["a"])
    patch_e = _entity(definition="a much longer definition", aliases=["b", "a"])
    _merge_into(target, patch_e)
    snapshot = json.loads(json.dumps(target))
    _merge_into(target, patch_e)
    assert target == snapshot


def test_merge_prefers_longer_definition_and_dedups_aliases():
    target = _entity(definition="short", aliases=["NSE"])
    _merge_into(target, _entity(definition="longer definition wins", aliases=["nse", "Nash"]))
    assert target["definition"] == "longer definition wins"
    # дедуп нечутливий до регістру
    assert [a.lower() for a in target["aliases"]].count("nse") == 1


def test_registry_no_cross_type_merge():
    """method.ndwi і metric.ndwi — РІЗНІ сутності: merge між типами заборонений."""
    reg = Registry()
    reg.upsert(_entity("method.ndwi", "NDWI (method)"))
    reg.upsert(_entity("metric.ndwi", "NDWI (metric)"))
    ids = {e["id"] for e in reg.all_entities()}
    assert ids == {"method.ndwi", "metric.ndwi"}

    groups = reg.by_file_key()
    assert any(e["id"] == "method.ndwi" for e in groups.get("methods", []))
    assert any(e["id"] == "metric.ndwi" for e in groups.get("metrics", []))


def test_registry_upsert_same_id_merges_not_duplicates():
    reg = Registry()
    reg.upsert(_entity("sensor.sar", aliases=["sar"]))
    reg.upsert(_entity("sensor.sar", aliases=["synthetic aperture radar"]))
    ents = [e for e in reg.all_entities() if e["id"] == "sensor.sar"]
    assert len(ents) == 1
    assert set(a.lower() for a in ents[0]["aliases"]) == {"sar", "synthetic aperture radar"}


def test_make_id_stable_and_typed():
    assert _make_id("HEC-RAS", "method") == _make_id("hec-ras", "Method")
    assert _make_id("X", "method") != _make_id("X", "metric")


def test_merge_lists_preserves_order_first_wins():
    assert _merge_lists(["A", "b"], ["B", "c", "a"]) == ["A", "b", "c"]
