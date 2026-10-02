"""Metric extraction, the fact table and ontology lookups, without live stores.

The fact table and the entity grounding are small parquet files written to tmp_path;
identity and the TEI location are stubbed.
"""

from __future__ import annotations

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from fastapi.testclient import TestClient

from src.api.app import create_app
from src.contracts.identity import PaperIdentity
from src.services import fulltext, identity_store, metrics

from tests.test_api import READ_KEY, StubKeys, StubManifest
from tests.test_api_evidence import TEI

H = {"X-API-Key": READ_KEY}
P1 = PaperIdentity(paper_id="p1", doi="10.1000/p1", title="SWAT in the Mekong", year=2024, identity_status="ok")


@pytest.fixture
def client(monkeypatch, tmp_path):
    facts = tmp_path / "numeric_facts.parquet"
    pq.write_table(pa.Table.from_pylist([
        {"fact_id": "f1", "paper_id": "p1", "table_id": "t1", "canonical_id": "metric.nse", "value": 0.87,
         "unit": "Eq. rainfall", "table_label": "Table 3", "col_header": "NSE", "row_context": '["Stung Treng"]', "page": 7,
         "confidence": 0.85},
        {"fact_id": "f2", "paper_id": "p1", "table_id": "t1", "canonical_id": "metric.nse", "value": 0.42,
         "unit": None, "table_label": "Table 3", "col_header": "NSE", "row_context": '["Kratie"]', "page": 7,
         "confidence": 0.85},
        {"fact_id": "f3", "paper_id": "p2", "table_id": "t9", "canonical_id": "metric.nse", "value": 0.91,
         "unit": "-", "table_label": "Table 1", "col_header": "NSE", "row_context": '["calibration"]', "page": 3,
         "confidence": 0.85},
        {"fact_id": "f4", "paper_id": "p2", "table_id": "t9", "canonical_id": "metric.overall_accuracy", "value": 1.4,
         "unit": None, "table_label": "Table 1", "col_header": "OA", "row_context": '["x"]', "page": 3,
         "confidence": 0.85},
    ]), facts)
    grounding = tmp_path / "entity_grounding.parquet"
    pq.write_table(pa.Table.from_pylist([
        {"rel": "USES_METHOD", "paper_id": "p1", "canonical_id": "method.swat", "grounded": True},
        {"rel": "USES_METHOD", "paper_id": "p2", "canonical_id": "method.swat", "grounded": False},
    ]), grounding)
    monkeypatch.setattr(metrics, "FACTS_FILE", facts)
    monkeypatch.setattr(metrics, "GROUNDING_FILE", grounding)
    tei = tmp_path / "p1.tei.xml"
    tei.write_text(TEI.replace("Floods are frequent on large floodplains such as the Jamuna.",
                               "The model reached NSE = −0.27 and KGE values between 0.61 and 0.74."), encoding="utf-8")
    monkeypatch.setattr(fulltext, "tei_path", lambda pid: tei if pid == "p1" else None)
    monkeypatch.setattr(identity_store, "resolve", lambda **kw: identity_store.Resolved("exact", 1.0, P1, None)
                        if "p1" in (kw.get("paper_id"), (kw.get("doi") or "").replace("10.1000/", "")) else None)
    monkeypatch.setattr(identity_store, "papers_by_id", lambda ids: {"p1": P1} if "p1" in ids else {})
    return TestClient(create_app(key_store=StubKeys(), manifest=StubManifest()))


def extract(client, **body):
    return client.post("/v1/metrics/extract", headers=H, json=body)


@pytest.mark.parametrize("text, expected", [
    ("The calibrated model reached NSE = −0.27 and an overall accuracy of 94.2 %.",
     [("metric.nse", -0.27, None, None, None), ("metric.overall_accuracy", 0.942, None, "%", None)]),
    ("KGE values between 0.61 and 0.74 were obtained.", [("metric.kge", 0.61, 0.74, None, "range")]),
    ("A model is satisfactory if NSE > 0.5.", [("metric.nse", 0.5, None, None, ">")]),
    ("the R 2 value was found to be 0.98", [("metric.r2", 0.98, None, None, None)]),
    ("an RMSE value of 170 mm/ year", [("metric.rmse", 170.0, None, "mm/year", None)]),
    ("RMSE from 12.62 m to 17.76 m", [("metric.rmse", 12.62, 17.76, "m", "range")]),
    ("Cohen's kappa coefficient of 85.3 % indicates agreement.", [("metric.kappa", 0.853, None, "%", None)]),
    ("Open access (OA) papers were 45 % of the total.", []),
    ("the precision agriculture sector grew by 12 %", []),
    ("On the other hand, NSE values are reported elsewhere.", []),
])
def test_text_extraction(client, text, expected):
    body = extract(client, text=text).json()
    got = [(f["metric"], f["value"], f["value_hi"], f["unit"], f["qualifier"]) for f in body["facts"]]
    assert sorted(got) == sorted(expected)
    assert all(f["range_verdict"] == "ok" and f["source"] == "text" for f in body["facts"])


def test_out_of_range_values_are_rejected_not_rescaled(client):
    body = extract(client, text="The NSE of 1.7 is implausible; the overall accuracy of 120 % too.").json()
    assert body["facts"] == []
    assert {(r["metric"], r["reason"]) for r in body["rejected"]} == {
        ("metric.nse", "outside valid range (Nash-Sutcliffe Efficiency ≤ 1)"),
        ("metric.overall_accuracy", "outside valid range (0 ≤ Overall Accuracy ≤ 1)")}


def test_metric_filter_and_errors(client):
    body = extract(client, text="NSE = 0.8 and KGE = 0.7", metrics=["KGE"]).json()
    assert [f["metric"] for f in body["facts"]] == ["metric.kge"]
    assert extract(client, text="x", metrics=["bogus"]).status_code == 422
    assert extract(client, text="x", paper_id="p1").status_code == 422
    assert extract(client, tei_xml='<!DOCTYPE TEI [<!ENTITY x SYSTEM "file:///etc/passwd">]><TEI/>').status_code == 422
    r = client.post("/v1/metrics/extract", headers=H, params={"mode": "llm"}, json={"paper_id": "p1"})
    assert r.status_code == 501


def test_paper_extraction_reads_sentences_and_tables(client):
    body = extract(client, paper_id="p1").json()
    text = [(f["metric"], f["value"], f["evidence"]["passage_id"], f["evidence"]["page"]) for f in body["facts"]
            if f["source"] == "text"]
    assert ("metric.nse", -0.27, "s0.p0", 2) in text and body["paper"]["paper_id"] == "p1"
    assert all(f["paper"]["paper_id"] == "p1" for f in body["facts"])


def test_tei_upload(client):
    body = extract(client, tei_xml=TEI.replace("Floods are frequent on large floodplains such as the Jamuna.",
                                               "Overall accuracy of 91 % was reached.")).json()
    assert [(f["metric"], f["value"]) for f in body["facts"] if f["source"] == "text"] == [("metric.overall_accuracy", 0.91)]


def test_fact_table_queries(client):
    body = client.get("/v1/metrics/facts", headers=H, params={"metric": "NSE", "min": 0.8}).json()
    assert [(i["fact_id"], i["value"]) for i in body["items"]] == [("f1", 0.87), ("f3", 0.91)]
    first = body["items"][0]
    assert first["unit"] is None and first["unit_raw"] == "Eq. rainfall"
    assert first["evidence"]["row_context"] == ["Stung Treng"] and first["paper"]["doi"] == "10.1000/p1"
    assert body["summary"]["n"] == 2 and body["summary"]["papers"] == 2
    # the method join uses grounded edges only: p2's SWAT edge is not grounded
    swat = client.get("/v1/metrics/facts", headers=H, params={"metric": "metric.nse", "method": "method.swat"}).json()
    assert [i["fact_id"] for i in swat["items"]] == ["f1", "f2"]
    # an OA of 1.4 is out of range: hidden by default, visible as suspect
    assert client.get("/v1/metrics/facts", headers=H, params={"metric": "OA"}).json()["items"] == []
    sus = client.get("/v1/metrics/facts", headers=H, params={"range_verdict": "suspect"}).json()
    assert [(i["metric"], i["range_verdict"]) for i in sus["items"]] == [("metric.overall_accuracy", "suspect")]
    page = client.get("/v1/metrics/facts", headers=H, params={"metric": "NSE", "limit": 1}).json()
    nxt = client.get("/v1/metrics/facts", headers=H, params={"metric": "NSE", "limit": 1, "cursor": page["next_cursor"]}).json()
    assert page["items"][0]["fact_id"] != nxt["items"][0]["fact_id"]
    by_doi = client.get("/v1/metrics/facts", headers=H, params={"doi": "10.1000/p1"}).json()
    assert {i["fact_id"] for i in by_doi["items"]} == {"f1", "f2"}
    assert client.get("/v1/metrics/facts", headers=H, params={"metric": "nonsense"}).status_code == 422
    assert client.get("/v1/metrics/facts", headers=H, params={"source": "text"}).json()["items"] == []


def test_ontology(client):
    m = {x["canonical_id"]: x for x in client.get("/v1/metrics/ontology", headers=H).json()["metrics"]}
    assert m["metric.nse"]["range"] == {"lo": None, "hi": 1.0} and "NSE" in m["metric.nse"]["aliases"]
    assert m["metric.kappa"]["range"] == {"lo": -1.0, "hi": 1.0}
    r = client.post("/v1/ontology/normalize", headers=H, json={"terms": [{"text": "Sentinel-1"}, {"text": "zzqq"}]})
    assert [(x["canonical_id"], x["match_type"]) for x in r.json()["results"]] == [("sensor.sentinel_1", "alias"),
                                                                                (None, "unknown")]
    e = client.get("/v1/ontology/entities", headers=H, params={"type": "metric", "q": "nash"}).json()
    assert [i["canonical_id"] for i in e["items"]] == ["metric.nse"]
    assert client.get("/v1/ontology/entities", headers=H, params={"type": "weather"}).status_code == 422


def test_tei_parser_does_not_expand_external_entities(tmp_path):
    from src.document.parser import TEIParser
    secret = tmp_path / "secret.txt"
    secret.write_text("TOP-SECRET", encoding="utf-8")
    xml = (f'<?xml version="1.0"?><!DOCTYPE TEI [<!ENTITY x SYSTEM "file://{secret}">]>'
           '<TEI xmlns="http://www.tei-c.org/ns/1.0"><teiHeader><fileDesc><titleStmt><title>&x;</title>'
           '</titleStmt></fileDesc></teiHeader></TEI>')
    assert "TOP-SECRET" not in (TEIParser().parse_text(xml, "x").title or "")
