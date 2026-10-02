"""The Python client against the real app (FastAPI TestClient as transport), stores stubbed."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.api.app import create_app
from src.services import identity_store

from tests import test_api_evidence as ev
from tests.test_api import READ_KEY, StubKeys, StubManifest

CLIENT = Path(__file__).resolve().parents[1] / "clients" / "python" / "ghai_client" / "__init__.py"


def load_client():
    spec = importlib.util.spec_from_file_location("ghai_client", CLIENT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["ghai_client"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def api(monkeypatch, tmp_path):
    tei = tmp_path / "iqbal_2023.tei.xml"
    tei.write_text(ev.TEI, encoding="utf-8")
    fixture_client = ev.make_client(monkeypatch, tei)   # the evidence tests' stubbed app
    ghai = load_client()
    return ghai, ghai.GHAI("http://testserver/v1", READ_KEY, client=fixture_client, retries=0)


def test_client_is_served_by_the_api():
    app = TestClient(create_app(key_store=StubKeys(), manifest=StubManifest()))
    r = app.get("/v1/client.py")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/x-python")
    assert "class GHAI" in r.text and r.text == CLIENT.read_text(encoding="utf-8")


def test_client_calls_and_errors(api):
    ghai, client = api
    sections = client.papers.sections("iqbal_2023")
    assert [s["id"] for s in sections["sections"]] == ["s0", "s1"]
    res = client.quotes.verify([{"source": "iqbal_2023", "quote": "does not accurately capture inundated cells"}])
    assert res["summary"] == {"FOUND_EXACT": 1}
    with pytest.raises(ghai.GHAIError) as exc:
        client.quotes.verify([{"source": "iqbal_2023", "quote": "by design"}])
    assert exc.value.status == 422 and exc.value.code == "VALIDATION_FAILED" and exc.value.errors
    with pytest.raises(ghai.GHAIError) as exc:
        client.papers.sections("unknown")
    assert exc.value.code == "NOT_FOUND"


def test_open_citations_are_packed_into_requests_of_at_most_50_quotations(api, monkeypatch):
    ghai, client = api
    item = {"key": "Iqbal_2023", "doi": "10.1111/jfr3.12937",
            "citations": [{"sentence": "s", "quotations": ["does not accurately capture inundated cells"] * 20}]}
    sent = []
    real_post = client.post
    monkeypatch.setattr(client, "post", lambda path, json=None, **p: sent.append(len(json["items"])) or real_post(path, json, **p))
    merged = client.quotes.verify_open_citations({"items": [item, item, item]})
    assert sent == [2, 1] and len(merged["items"]) == 60 and merged["summary"] == {"FOUND_EXACT": 60}
