"""Paper-folder import: parsing rules, labeller attribution, strict contracts (no database)."""

from __future__ import annotations

import json

import pytest

from src.contracts import research as C
from src.etl import paper_folders as pf


@pytest.mark.parametrize("text, kind, labeler", [
    ("Claude (Fable 5.1) for V. Nikoriak, 2026-09-28", "model", "claude-fable-5.1"),
    ("Claude (Opus 5.5) read the normalized text 2026-09-28", "model", "claude-opus-5.5"),
    ("gemini-3.1-flash-lite", "model", "gemini-3.1-flash-lite"),
    ("ollama:mistral-nemo:12b", "model", "mistral-nemo:12b"),
    ("ChatGPT", "model", "chatgpt"),
    ("", "unknown", "unattributed"),
])
def test_labeler_attribution(text, kind, labeler):
    lab = pf.labeler_from(text)
    assert (lab.labeler_kind, lab.labeler) == (kind, labeler)


def test_ref_status_vocabulary():
    assert pf.ref_status("verified") == "verified"
    assert pf.ref_status("VERIFY") == "to_verify"
    assert pf.ref_status("missing (search)") == "missing"
    assert pf.ref_status("in bib, no status note") == "unknown"


def _bundle(tmp_path, monkeypatch, theses):
    monkeypatch.setattr(pf, "ROOT", tmp_path)
    (tmp_path / "t.json").write_text(json.dumps(theses), encoding="utf-8")
    return pf.bundle_theses(pf.KT2, "t.json", C.Labeler(labeler_kind="import", labeler="test"))


def test_csv_dump_theses_are_parsed_by_the_documented_grammar(tmp_path, monkeypatch):
    b = _bundle(tmp_path, monkeypatch, [{
        "id": "TH-INT-01", "section": "1", "category": "background", "priority": "high", "tables": "T1, T2",
        "thesis": "The Kakhovka reservoir was destroyed on 6 June 2023.",
        "refs": "Vyshnevskyi_2023[SUPPORTED_BY:VERIFY]; Lehnigk_2026[COMPARATOR:verified]",
        "search_queries": "Kakhovka reservoir volume | dam breach flood wave"}])
    assert not b.rejects
    assert b.rows[0]["search_queries"] == ["Kakhovka reservoir volume", "dam breach flood wave"]
    assert b.rows[0]["tables"] == ["T1", "T2"]
    refs = b.children[0][1]
    assert {(r["key"], r["relation"], r["status"]) for r in refs} == {
        ("Vyshnevskyi_2023", "SUPPORTED_BY", "to_verify"), ("Lehnigk_2026", "COMPARATOR", "verified")}


def test_unparseable_refs_are_rejected_not_coerced(tmp_path, monkeypatch):
    b = _bundle(tmp_path, monkeypatch, [{
        "id": "TH-X", "thesis": "A statement long enough to pass the contract.", "refs": "Key without brackets"}])
    assert b.rows == [] and "cannot parse" in b.rejects[0]["errors"][0]


def test_unknown_relation_is_rejected(tmp_path, monkeypatch):
    b = _bundle(tmp_path, monkeypatch, [{
        "id": "TH-Y", "thesis": "A statement long enough to pass the contract.",
        "refs": [{"key": "K_2020", "relation": "INSPIRED_BY", "status": "verified"}]}])
    assert b.rows == [] and b.rejects


def test_legacy_python_list_strings():
    assert pf._legacy_list("['A', 'B']") == (["A", "B"], True)
    assert pf._legacy_list(["A"]) == (["A"], False)
    assert pf._legacy_list("[not python") == ("[not python", False)


def test_relevance_from_role():
    assert pf._relevance_from_role("NOT_RELEVANT") == "NOT_RELEVANT"
    assert pf._relevance_from_role("SUPPORTS") == "RELEVANT"
    assert pf._relevance_from_role(None) == "UNKNOWN"


def test_legacy_relation_names_map_to_current_roles():
    assert C.LEGACY_ROLES == {"CONTRADICTS": "CONTRASTS", "METHOD_RELEVANT": "METHOD_FROM", "ANALOGUE": "COMPARATOR"}
    assert all(v in C.EVIDENCE_ROLES for v in C.LEGACY_ROLES.values())


def test_screening_label_contract_rejects_human_claims_without_kind():
    with pytest.raises(Exception):
        C.ScreeningLabel(project_id=pf.FS3, subject_kind="thesis", subject_id="T1", relevance="RELEVANT",
                         labeler={"labeler_kind": "expert", "labeler": "someone"})
