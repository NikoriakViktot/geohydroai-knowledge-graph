"""The human verification layer and the paper inspection endpoints (no live stores)."""

from __future__ import annotations

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from src.api.app import create_app
from src.api.deps import Principal, hash_key
from src.contracts.research import SEED_PROJECT_IDS
from src.services import filelinks, identity_store, projects, regions, verify

READ, HUMAN = "read-key", "human-key"


class Keys:
    def lookup(self, h):
        return {hash_key(READ): Principal("claude-mcp", frozenset({"read", "llm"}), "k1"),
                hash_key(HUMAN): Principal("niko-personal", frozenset({"read", "verify"}), "k2")}.get(h)


class Manifest:
    def get(self):
        return {"corpus_manifest_id": "f" * 64, "frozen": False, "papers": 1}


@pytest.fixture
def client(monkeypatch, tmp_path):
    written: list = []

    def record(checks, labeler, key_id):
        written.extend((c, labeler) for c in checks)
        return list(range(1, len(checks) + 1))

    monkeypatch.setattr(verify, "record", record)
    monkeypatch.setattr(verify, "listing", lambda **kw: [{
        "check_id": 7, "target_kind": "formula", "target_id": "p_for_1", "field": "latex", "paper_id": "p",
        "project_id": None, "verdict": "incorrect", "problem": "formula_broken", "corrected": None, "note": None,
        "shown": None, "labeler": "niko-personal", "supersedes": None, "created_at": None}])
    monkeypatch.setattr(verify, "summary", lambda project_id=None: {
        "total": 1, "by_verdict": {"incorrect": 1}, "by_problem": {"formula_broken": 1},
        "by_target_kind": {"formula": {"incorrect": 1}},
        "parse_problem_papers": [{"paper_id": "p", "problems": {"formula_broken": 1}, "total": 1}]})
    # regions of one paper, with a crop file inside the repository's data/ tree
    sodb = tmp_path / "sodb"
    (sodb / "p").mkdir(parents=True)
    crop = filelinks.ROOT / "data" / "test_crop_verify.png"
    crop.write_bytes(b"\x89PNG\r\n\x1a\n")
    pd.DataFrame([{"region_id": "p_for_1", "paper_id": "p", "page": 3, "bbox_x0": 1.0, "bbox_y0": 2.0, "bbox_x1": 3.0,
                   "bbox_y1": 4.0, "region_type": "FORMULA_REGION", "source_parser": "nougat", "nougat_text": "",
                   "nougat_latex": "\\[a=b\\]", "crop_path": "data/test_crop_verify.png"}]).to_parquet(sodb / "p" / "regions.parquet")
    monkeypatch.setattr(regions, "SODB", sodb)
    monkeypatch.setattr(regions, "pdf_paths", lambda ids: {})
    monkeypatch.setattr(identity_store, "papers_by_id", lambda ids: {})
    monkeypatch.setattr(projects, "registered", lambda pid: pid in SEED_PROJECT_IDS)
    monkeypatch.setattr(projects, "theses_set", lambda pid: {"project_id": pid, "theses": [], "unattached_evidence": [],
                                                             "counts": {}, "status_source": "model-assessed"})
    c = TestClient(create_app(key_store=Keys(), manifest=Manifest()))
    c.written = written
    yield c
    crop.unlink(missing_ok=True)


CHECK = {"target_kind": "formula", "target_id": "p_for_1", "field": "latex", "paper_id": "p",
         "verdict": "incorrect", "problem": "formula_broken", "corrected": {"latex": "a = b"}}


def test_only_a_verify_key_can_write_and_rows_are_human(client):
    r = client.post("/v1/verifications", json={"checks": [CHECK]}, headers={"X-API-Key": READ})
    assert r.status_code == 403 and r.json()["code"] == "FORBIDDEN_SCOPE"
    r = client.post("/v1/verifications", json={"checks": [CHECK]}, headers={"X-API-Key": HUMAN})
    assert r.status_code == 201
    item = r.json()["items"][0]
    assert item["labeler_kind"] == "human" and item["labeler"] == "niko-personal" and item["check_id"] == 1
    assert client.written[0][1] == "niko-personal"


def test_unknown_verdict_or_problem_is_refused(client):
    bad = {**CHECK, "verdict": "looks fine"}
    assert client.post("/v1/verifications", json={"checks": [bad]}, headers={"X-API-Key": HUMAN}).status_code == 422
    bad = {**CHECK, "problem": "llm_says_wrong"}
    assert client.post("/v1/verifications", json={"checks": [bad]}, headers={"X-API-Key": HUMAN}).status_code == 422


def test_read_checks_and_summary(client):
    r = client.get("/v1/verifications", params={"target_kind": "formula", "target_id": ["p_for_1"]},
                   headers={"X-API-Key": READ})
    assert r.status_code == 200 and r.json()["items"][0]["verdict"] == "incorrect"
    s = client.get("/v1/verifications/summary", headers={"X-API-Key": READ}).json()
    assert s["parse_problem_papers"][0]["paper_id"] == "p"


def test_regions_carry_latex_and_a_signed_crop_link(client):
    r = client.get("/v1/papers/p/regions", headers={"X-API-Key": READ})
    assert r.status_code == 200
    reg = r.json()["regions"][0]
    assert reg["latex"] == "\\[a=b\\]" and reg["text"] is None and reg["bbox"] == [1.0, 2.0, 3.0, 4.0]
    png = client.get(reg["crop_url"].replace("http://testserver", ""))
    assert png.status_code == 200 and png.headers["content-type"] == "image/png"
    assert client.get("/v1/papers/none/regions", headers={"X-API-Key": READ}).json()["code"] == "SOURCE_UNAVAILABLE"


def test_links_and_theses_set(client):
    r = client.post("/v1/papers/links", json={"paper_ids": ["p"]}, headers={"X-API-Key": READ})
    assert r.json()["links"] == [{"paper_id": "p", "pdf_url": None, "doi_url": None}]
    r = client.get("/v1/theses/sets/floodstate-eo:paper3", headers={"X-API-Key": READ})
    assert r.status_code == 200 and r.json()["status_source"] == "model-assessed"
    assert client.get("/v1/theses/sets/nope:x", headers={"X-API-Key": READ}).json()["code"] == "UNKNOWN_PROJECT"
