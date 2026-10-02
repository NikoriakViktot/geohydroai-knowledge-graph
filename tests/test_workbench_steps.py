"""Workbench checking steps (theses, citations, bibliography, review) with a stubbed Knowledge API.

The paper repository is a local directory (LocalRunner); reports are staged under a temporary
WORKBENCH_DIR. No live API, no database.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from src.workbench import client, steps
from src.workbench.remote import LocalRunner, RemoteRepo
from src.workbench.steps import bibliography, citations, review, theses

PID = "test-repo:paper1"
LONG_A = "water under dense canopy is invisible to C-band backscatter"
MANIFEST = f"""schema: ghai.project/v1
project_id: {PID}
repo: {{path: /x/test-repo}}
publication_dir: pub
paths:
  manuscript_out: pub/ms.md
  bib: pub/refs.bib
  theses: pub/theses.json
  atomic_claims: pub/claims.yaml
  reviews: pub/reviews/
  review_rules: pub/review_rules.yaml
citation: {{style: apa}}
"""
RULES = """schema: ghai.review_rules/v1
files:
  ms: {path: pub/ms.md, label: ms.md}
rules:
  - {id: C, type: forbidden, in: [ms], major_in: [ms], phrases: [physical reconstruction], observed: "forbidden '{phrase}'"}
"""


class FakeAPI:
    def __init__(self, found_in: str | None = "A_2020"):
        self.found_in = found_in
        self.theses = SimpleNamespace(validate=self.validate)
        self.bib = SimpleNamespace(citations=self.citations, audit=self.audit, render=self.render)
        self.quotes = SimpleNamespace(verify=self.verify)
        self.verified: list[dict] = []

    def validate(self, kind, project_id, document, authored_by=None):
        if kind == "atomic_claims":
            raise client.GHAIError(422, "VALIDATION_FAILED", "1 problem(s)", {
                "errors": [{"loc": ["claims", 0, "statement"], "msg": "too short", "type": "string_too_short"}]})
        return {"valid": True, "counts": {"theses": len(document)}, "warnings": [], "provenance": {"corpus_manifest_id": "m1"}}

    def citations(self, manuscript, bibtex, project_id=None):
        sentence = f'SAR misses water: "{LONG_A}" (A 2020; B 2021).'
        occ = [
            {"cite_text": "A 2020", "authors": "A", "year": "2020", "status": "resolved", "cite_key": "A_2020",
             "doi": "10.1/a", "section": "Intro", "sentence": sentence, "line": 3, "quoted": [LONG_A]},
            {"cite_text": "B 2021", "authors": "B", "year": "2021", "status": "resolved", "cite_key": "B_2021",
             "doi": "10.1/b", "section": "Intro", "sentence": sentence, "line": 3, "quoted": [LONG_A]},
            {"cite_text": "C 2019", "authors": "C", "year": "2019", "status": "resolved", "cite_key": "C_2019",
             "doi": "10.1/c", "section": "Methods", "sentence": "As in C 2019.", "line": 5, "quoted": []},
            {"cite_text": "D 2018", "authors": "D", "year": "2018", "status": "missing", "cite_key": None,
             "doi": None, "section": "Methods", "sentence": "And D 2018.", "line": 6, "quoted": []},
        ]
        return {"occurrences": occ, "missing_keys": [{"cite_text": "D 2018"}], "uncited_entries": ["E_2017"],
                "summary": {"resolved": 3, "missing": 1}, "provenance": {"corpus_manifest_id": "m1"}}

    def verify(self, items, project_id=None):
        self.verified += items
        out = []
        for it in items:
            hit = self.found_in and it["source"] == {"A_2020": "10.1/a", "B_2021": "10.1/b"}[self.found_in]
            out.append({"key": it["key"], "source": it["source"], "quote": it["quote"],
                        "status": "FOUND_EXACT" if hit else "NOT_FOUND", "numbers": [], "attribution": None,
                        "span": {"text": "something else entirely"} if not hit else {"text": it["quote"]}})
        return {"items": out, "not_checked": [], "summary": {}, "provenance": {"corpus_manifest_id": "m1"}}

    def audit(self, bibtex, project_id=None, search_missing=True):
        return {"entries": [{"key": "A_2020", "type": "article", "status": "ok", "problems": [], "warnings": []},
                            {"key": "B_2021", "type": "article", "status": "fix", "doi": "10.1/b",
                             "problems": ["title differs from the registry"], "warnings": [],
                             "suggested_bibtex": "@article{B_2021, title={Right title}}"}],
                "summary": {"entries": 2, "ok": 1, "fix": 1, "unresolved": 0}, "provenance": {"corpus_manifest_id": "m1"}}

    def render(self, bibtex, keys=None, manuscript=None, style="apa"):
        return {"references": [{"key": "A_2020", "text": "A. (2020). Title A."}], "unresolved_keys": [],
                "uncited_entries": ["E_2017"]}


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    repo = tmp_path / "test-repo"
    (repo / "pub").mkdir(parents=True)
    (repo / "pub" / "ghai.project.yaml").write_text(MANIFEST, encoding="utf-8")
    (repo / "pub" / "ms.md").write_text(f'# Intro\nText.\nSAR misses water: "{LONG_A}" (A 2020; B 2021).\n'
                                        "A {{claim:P1.2}} value and a physical reconstruction.\n", encoding="utf-8")
    (repo / "pub" / "refs.bib").write_text("@article{A_2020, title={A}}\n", encoding="utf-8")
    (repo / "pub" / "theses.json").write_text(json.dumps([{"id": "T1", "thesis": "A thesis of length."}]), encoding="utf-8")
    (repo / "pub" / "claims.yaml").write_text("claims: []\n", encoding="utf-8")
    (repo / "pub" / "review_rules.yaml").write_text(RULES, encoding="utf-8")
    monkeypatch.setattr(steps, "WORKBENCH_DIR", tmp_path / "workbench")
    target = {"project_id": PID, "repo_path": str(repo), "distro": "local", "publication_dir": "pub",
              "manifest_path": "pub/ghai.project.yaml", "public": True, "registered": True}
    return steps.Context(PID, target, RemoteRepo("local", str(repo), runner=LocalRunner()), local_target=True)


def staged(ctx, name):
    return json.loads((steps.project_dir(PID) / "out" / "pub/reviews/workbench" / name).read_text(encoding="utf-8"))


def test_theses_step_reports_located_errors_and_never_coerces(ctx, monkeypatch):
    monkeypatch.setattr(client, "api", lambda: FakeAPI())
    assert theses.run(ctx) == 2
    doc = staged(ctx, "theses_validation.json")
    assert [r["status"] for r in doc["results"]] == ["VALID", "VALIDATION_FAILED"]
    md = (steps.project_dir(PID) / "out/pub/reviews/workbench/theses_validation.md").read_text(encoding="utf-8")
    assert "`claims → 0 → statement`" in md and "**Provenance.**" in md


def test_a_quotation_is_judged_over_every_source_its_sentence_cites(ctx, monkeypatch):
    api = FakeAPI(found_in="A_2020")
    monkeypatch.setattr(client, "api", lambda: api)
    assert citations.run(ctx) == 0
    v = {c["key"]: (c["verdict"], c["reason"]) for c in staged(ctx, "citation_verification.json")["citations"]}
    assert v["A_2020"][0] == "VERIFIED"
    assert v["B_2021"] == ("UNCHECKED", "its sentence's quotations come from another cited source")
    assert v["C_2019"] == ("UNCHECKED", "cited without quoted words")
    assert v["D 2018"][0] == "OPEN"
    q = staged(ctx, "citation_verification.json")["quotations"]
    assert len(q) == 1 and q[0]["found_in"] == ["A_2020"] and set(q[0]["keys"]) == {"A_2020", "B_2021"}


def test_a_quotation_found_in_none_of_its_sources_is_fix_for_each(ctx, monkeypatch):
    monkeypatch.setattr(client, "api", lambda: FakeAPI(found_in=None))
    assert citations.run(ctx) == 2
    v = {c["key"]: c["verdict"] for c in staged(ctx, "citation_verification.json")["citations"]}
    assert v["A_2020"] == "FIX" and v["B_2021"] == "FIX"


def test_bibliography_and_review_merge_everything(ctx, monkeypatch):
    api = FakeAPI()
    monkeypatch.setattr(client, "api", lambda: api)
    assert bibliography.run(ctx) == 2
    out = steps.project_dir(PID) / "out/pub/reviews/workbench"
    assert "Right title" in (out / "bibliography_suggestions.bib").read_text(encoding="utf-8")
    assert "A. (2020). Title A." in (out / "REFERENCES.md").read_text(encoding="utf-8")
    theses.run(ctx)
    citations.run(ctx)
    assert review.run(ctx) == 2
    found = staged(ctx, "review_findings.json")
    kinds = {(f["check_id"], f["severity"]) for f in found["findings"]}
    assert ("MARKER", "MAJOR") in kinds and ("C", "MAJOR") in kinds
    assert ("BIB", "MAJOR") in kinds and ("THESES", "MAJOR") in kinds
    assert set(found["steps_included"]) == {"theses", "citations", "bibliography"}
    report = (out / "REVIEW_REPORT.md").read_text(encoding="utf-8")
    assert "{{claim:P1.2}}" in report and "physical reconstruction" in report
