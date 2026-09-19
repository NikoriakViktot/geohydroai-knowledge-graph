"""Prompt construction, response parsing, and the raw → finalize split.

No network: the LLM callers are never invoked here. What is tested is everything
around them — that a malformed answer cannot become evidence, that an unverifiable
quote is rejected, and that re-running costs nothing.
"""
from __future__ import annotations

import json

import pandas as pd
import pytest

from src.paper_3._utils import REFUSAL
from src.paper_3.classify_relation import (
    build_prompt,
    done_triples,
    load_raw,
    normalise_parsed,
    parse_response,
    prompt_sha256,
)
from src.paper_3.evidence import EvidencePassage
from src.paper_3.finalize_relations import (
    QUOTE_REQUIRED,
    apply_overrides,
    finalize_row,
    rejection_rate_by_model,
)
from src.paper_3.theses import NOT_RELEVANT, RELATIONS, SYSTEM_CLASSES, get_thesis, load_theses

QUOTE = "The median longitudinal water-surface slope increased to 3.314 cm per kilometre."


@pytest.fixture(scope="module")
def thesis():
    return get_thesis("T03", load_theses())


@pytest.fixture
def passages():
    return [
        EvidencePassage("P1", "p1", QUOTE, "results", 0, "p1.json"),
        EvidencePassage("P2", "p1", "We used ICESat-2 ATL13 over the reservoir pool.",
                        "data", 100, "p1.json"),
    ]


# ── prompt ────────────────────────────────────────────────────────────────────

def test_prompt_contains_thesis_passages_and_rules(thesis, passages):
    prompt = build_prompt(thesis, {"title": "T", "year": 2024, "doi": "10.1/x"}, passages)
    assert thesis.id in prompt
    assert "character-for-character" in prompt
    assert REFUSAL in prompt
    for p in passages:
        assert p.text in prompt
        assert f"[{p.passage_id}]" in prompt


def test_prompt_lists_contradicts_before_supports(thesis, passages):
    """Ordering is a deliberate counterweight to the pull towards agreement."""
    prompt = build_prompt(thesis, {}, passages)
    assert prompt.index("CONTRADICTS  ") < prompt.index("SUPPORTS  ")


def test_prompt_offers_only_legal_system_classes(thesis, passages):
    prompt = build_prompt(thesis, {}, passages)
    for cls in SYSTEM_CLASSES:
        assert cls in prompt


def test_prompt_handles_missing_metadata(thesis, passages):
    prompt = build_prompt(thesis, {}, passages)
    assert "(no DOI)" in prompt and "(title unavailable)" in prompt


def test_prompt_hash_is_stable_and_discriminating(thesis, passages):
    a = build_prompt(thesis, {"doi": "10.1/x"}, passages)
    b = build_prompt(thesis, {"doi": "10.1/y"}, passages)
    assert prompt_sha256(a) == prompt_sha256(a)
    assert prompt_sha256(a) != prompt_sha256(b)


# ── parsing ───────────────────────────────────────────────────────────────────

def test_parse_plain_json():
    assert parse_response('{"relation": "SUPPORTS"}')["relation"] == "SUPPORTS"


def test_parse_json_inside_a_code_fence():
    text = 'Here you go:\n```json\n{"relation": "ANALOGUE"}\n```\n'
    assert parse_response(text)["relation"] == "ANALOGUE"


@pytest.mark.parametrize("text", ["", "   ", "not json at all", None])
def test_parse_failures_return_none(text):
    assert parse_response(text) is None


def test_normalise_rejects_an_unknown_relation():
    """A malformed answer must not become evidence."""
    assert normalise_parsed({"relation": "PROBABLY_SUPPORTS"})["relation"] == NOT_RELEVANT


def test_normalise_clamps_confidence():
    assert normalise_parsed({"confidence": 5})["confidence"] == 1.0
    assert normalise_parsed({"confidence": -2})["confidence"] == 0.0
    assert normalise_parsed({"confidence": "nonsense"})["confidence"] == 0.0


def test_normalise_defaults_unknown_system_class_to_not_stated():
    assert normalise_parsed({"system_class": "estuary"})["system_class"] == "not_stated"
    assert normalise_parsed({"system_class": "RESERVOIR"})["system_class"] == "reservoir"


def test_normalise_requires_a_real_boolean_for_is_own_result():
    assert normalise_parsed({"is_own_result": "yes"})["is_own_result"] is False
    assert normalise_parsed({"is_own_result": True})["is_own_result"] is True


def test_normalise_on_an_empty_object_is_not_relevant():
    out = normalise_parsed({})
    assert out["relation"] == NOT_RELEVANT
    assert out["evidence_quote"] == ""


# ── finalize ──────────────────────────────────────────────────────────────────

def raw_record(relation="SUPPORTS", quote=QUOTE, model="gemini-2.5-flash", **over):
    record = {
        "thesis_id": "T03", "paper_id": "p1", "doi": "10.1/x", "title": "A paper",
        "year": 2024, "model": model, "prompt_sha256": "abc",
        "raw_response": json.dumps({
            "relation": relation, "confidence": 0.8, "passage_id": "P1",
            "evidence_quote": quote, "rationale": "because",
            "system_class": "reservoir", "is_own_result": True,
        }),
        "passages_offered": [
            {"passage_id": "P1", "text": QUOTE, "section": "results",
             "char_offset": 0, "source_file": "p1.json"},
        ],
    }
    record.update(over)
    return record


def test_verified_support_is_kept_with_its_provenance():
    row, rejection = finalize_row(raw_record())
    assert rejection is None
    assert row["relation"] == "SUPPORTS"
    assert row["quote_verified"] is True
    assert row["quote_section"] == "results"
    assert row["quote_source_file"] == "p1.json"


def test_fabricated_quote_is_rejected_and_the_quote_dropped():
    row, rejection = finalize_row(raw_record(
        quote="The authors state that satellites cannot measure slope at all."))
    assert rejection is not None
    assert not row["quote_verified"]
    assert row["evidence_quote"] == "", "an unverified quote must not be stored"
    assert rejection["reason"]


def test_unparseable_response_is_recorded_not_raised():
    row, rejection = finalize_row(raw_record(raw_response="I cannot answer that."))
    assert row["parse_ok"] is False
    assert row["relation"] == NOT_RELEVANT
    assert rejection["reason"] == "unparseable response"


def test_not_relevant_needs_no_quote():
    row, rejection = finalize_row(raw_record(relation="NOT_RELEVANT", quote=""))
    assert rejection is None
    assert row["relation"] == NOT_RELEVANT


@pytest.mark.parametrize("relation", ["METHOD_RELEVANT", "ANALOGUE"])
def test_method_and_analogue_survive_an_unverified_quote(relation):
    """These describe what a paper is, which the offered passages already show."""
    row, rejection = finalize_row(raw_record(relation=relation, quote="invented text here"))
    assert rejection is None
    assert row["relation"] == relation
    assert not row["quote_verified"]


@pytest.mark.parametrize("relation", QUOTE_REQUIRED)
def test_claims_about_findings_always_need_a_verified_quote(relation):
    _row, rejection = finalize_row(raw_record(relation=relation,
                                              quote="entirely fabricated sentence here"))
    assert rejection is not None


def test_quote_spanning_no_offered_passage_is_rejected():
    record = raw_record(quote="text from a passage that was never offered at all")
    _row, rejection = finalize_row(record)
    assert rejection is not None


# ── overrides ─────────────────────────────────────────────────────────────────

def _relations_frame():
    rows = [finalize_row(raw_record())[0],
            finalize_row(raw_record(relation="ANALOGUE"))[0]]
    frame = pd.DataFrame(rows)
    frame.loc[1, "doi"] = "10.1/y"
    return frame


def test_override_replaces_the_relation_and_records_itself():
    frame = apply_overrides(_relations_frame(), {
        ("T03", "10.1/y"): ("CONTRADICTS", 1.0, "read in full: the authors reject this"),
    })
    row = frame[frame["doi"] == "10.1/y"].iloc[0]
    assert row["relation"] == "CONTRADICTS"
    assert row["override_applied"]
    assert "read in full" in row["override_note"]


def test_override_of_a_finding_claim_is_trusted_over_the_quote_check():
    frame = apply_overrides(_relations_frame(), {
        ("T03", "10.1/y"): ("SUPPORTS", 1.0, "human verified against the PDF"),
    })
    assert frame[frame["doi"] == "10.1/y"].iloc[0]["quote_verified"]


def test_an_override_that_matches_nothing_is_a_warning_not_a_crash():
    frame = apply_overrides(_relations_frame(), {("T99", "10.9/z"): ("SUPPORTS", 1.0, "x")})
    assert len(frame) == 2
    assert not frame["override_applied"].any()


def test_no_overrides_leaves_the_frame_untouched():
    frame = _relations_frame()
    assert apply_overrides(frame, {}).equals(frame)


# ── resume behaviour ──────────────────────────────────────────────────────────

def test_done_triples_identify_completed_work():
    rows = [{"thesis_id": "T01", "paper_id": "p1", "model": "gemini-2.5-flash"}]
    done = done_triples(rows)
    assert ("T01", "p1", "gemini-2.5-flash", "full_text") in done
    assert ("T01", "p1", "ollama:mistral-nemo:12b", "full_text") not in done


def test_an_abstract_adjudication_does_not_block_the_full_text_pass():
    """The upgrade path: the same paper must be re-asked once its PDF arrives."""
    rows = [{"thesis_id": "T01", "paper_id": "p1", "model": "gemini-2.5-flash",
             "evidence_level": "abstract"}]
    done = done_triples(rows)
    assert ("T01", "p1", "gemini-2.5-flash", "abstract") in done
    assert ("T01", "p1", "gemini-2.5-flash", "full_text") not in done


def test_rows_written_before_evidence_level_existed_read_as_full_text():
    rows = [{"thesis_id": "T01", "paper_id": "p1", "model": "m"}]
    assert ("T01", "p1", "m", "full_text") in done_triples(rows)


def test_load_raw_skips_malformed_lines(tmp_path):
    path = tmp_path / "relations_raw.jsonl"
    path.write_text('{"thesis_id": "T01"}\nnot json\n\n{"thesis_id": "T02"}\n',
                    encoding="utf-8")
    rows = load_raw(path)
    assert [r["thesis_id"] for r in rows] == ["T01", "T02"]


def test_load_raw_of_a_missing_file_is_empty(tmp_path):
    assert load_raw(tmp_path / "nothing.jsonl") == []


# ── quality signal ────────────────────────────────────────────────────────────

def test_rejection_rate_is_reported_per_model():
    relations = pd.DataFrame([
        {"model": "m1", "relation": "SUPPORTS"},
        {"model": "m1", "relation": "SUPPORTS"},
        {"model": "m2", "relation": "ANALOGUE"},
    ])
    rejected = pd.DataFrame([{"model": "m1"}, {"model": "m1"}])
    rates = rejection_rate_by_model(rejected, relations)
    assert rates["m1"] == 0.5
    assert "m2" not in rates, "a model that attempted no quote has no rate"


def test_rejection_rate_on_empty_input():
    assert rejection_rate_by_model(pd.DataFrame(), pd.DataFrame()) == {}
