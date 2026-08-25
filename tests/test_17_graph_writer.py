"""
test_17_graph_writer.py — Neo4j writers без живої БД (Фаза 3.3).

Закриває F-GR-3: графовий шар матеріалізує фінальний науковий продукт
(57K вузлів) без тестів. Mock-драйвер перевіряє:
  - кожен write_* використовує MERGE з правильним ключем вузла;
  - wipe() недосяжний без явного виклику;
  - fact_writer: Evidence отримує детермінований evidence_id (дедуплікація
    при повторних запусках — Фаза 1.2).
"""
from __future__ import annotations

import re
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


# ── Інфраструктура: фейковий драйвер, що записує всі (cypher, params) ─────────

class _RecordingSession:
    def __init__(self, calls: list):
        self._calls = calls

    def run(self, cypher, *args, **kwargs):
        self._calls.append((cypher, kwargs or (args[0] if args else {})))
        return MagicMock()

    def execute_write(self, fn, *args, **kwargs):
        tx = MagicMock()
        tx.run = lambda cypher, **kw: self._calls.append((cypher, kw))
        return fn(tx, *args, **kwargs)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


def _make_writer(calls: list):
    from src.graph.neo4j_writer import GraphWriter
    with patch("src.graph.neo4j_writer.GraphDatabase") as gd:
        driver = MagicMock()
        driver.session.side_effect = lambda **kw: _RecordingSession(calls)
        gd.driver.return_value = driver
        return GraphWriter(uri="bolt://test", user="u", password="test-pass")


# ── MERGE-ключі вузлів ────────────────────────────────────────────────────────

@pytest.mark.parametrize("method,rows,merge_key", [
    ("write_papers",       [{"paper_id": "p1"}],       "MERGE (p:Paper {paper_id: r.paper_id})"),
    ("write_authors",      [{"author_id": "a1"}],      "MERGE (a:Author {author_id: r.author_id})"),
    ("write_methods",      [{"canonical_id": "m1"}],   "MERGE (m:Method {canonical_id: r.canonical_id})"),
    ("write_sensors",      [{"canonical_id": "s1"}],   "MERGE (s:Sensor {canonical_id: r.canonical_id})"),
    ("write_metrics",      [{"canonical_id": "x1"}],   "MERGE (m:Metric {canonical_id: r.canonical_id})"),
    ("write_countries",    [{"name": "Ukraine"}],      "MERGE (c:Country {name: r.name})"),
])
def test_node_writers_use_merge_with_stable_key(method, rows, merge_key):
    calls: list = []
    gw = _make_writer(calls)
    getattr(gw, method)(rows)
    cyphers = " ".join(c for c, _ in calls)
    assert merge_key in cyphers, f"{method} має MERGE-ключ {merge_key!r}"
    # Жодного CREATE-вузла в data-write шляху
    assert not re.search(r"\bCREATE\s*\(", cyphers)


def test_wipe_not_reachable_from_writers():
    """Деструктивний DETACH DELETE виконується ЛИШЕ явним wipe()."""
    calls: list = []
    gw = _make_writer(calls)
    gw.write_papers([{"paper_id": "p1"}])
    gw.write_countries([{"name": "Ukraine"}])
    assert not any("DETACH DELETE" in c for c, _ in calls)

    gw.wipe()
    assert any("DETACH DELETE" in c for c, _ in calls)


# ── fact_writer: детермінований Evidence-ключ (Фаза 1.2) ──────────────────────

def _fake_fact_result():
    ev = SimpleNamespace(text="NSE = 0.78 for calibration", section="results",
                         field="nse", source="regex", chunk_id="c42")
    fact = SimpleNamespace(
        id="fact-001", fact_type="task", task="flood_mapping",
        evidence=[ev], related_fact_ids=[],
        satellite=None, method=None, metric=None, study_area=None, value=None,
    )
    return SimpleNamespace(paper_id="paper-X", title="T", facts=[fact],
                           fact_count=1)


def test_evidence_id_deterministic_across_runs():
    from src.graph.fact_writer import _upsert_fact_paper

    def run_once() -> list:
        calls: list = []
        tx = MagicMock()
        tx.run = lambda cypher, **kw: calls.append((cypher, kw))
        _upsert_fact_paper(tx, _fake_fact_result())
        return calls

    eids_a = [kw["eid"] for c, kw in run_once() if "Evidence" in c and "eid" in kw]
    eids_b = [kw["eid"] for c, kw in run_once() if "Evidence" in c and "eid" in kw]

    assert eids_a, "Evidence MERGE має виконуватись"
    assert eids_a == eids_b, "evidence_id має бути детермінованим (дедуплікація)"


def test_evidence_uses_merge_not_create():
    from src.graph.fact_writer import _upsert_fact_paper
    calls: list = []
    tx = MagicMock()
    tx.run = lambda cypher, **kw: calls.append(cypher)
    _upsert_fact_paper(tx, _fake_fact_result())
    ev_cyphers = [c for c in calls if "Evidence" in c]
    assert ev_cyphers and all("MERGE (e:Evidence" in c for c in ev_cyphers)
    assert not any(re.search(r"\bCREATE\s*\(e:Evidence", c) for c in ev_cyphers)
