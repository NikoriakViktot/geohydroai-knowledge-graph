"""Anti-hallucination core: verify_quote must reject what the model did not copy.

Every count in the gap matrix depends on these tests. If verify_quote accepts a
paraphrase, a fabricated sentence enters the manuscript's related-work section
wearing quotation marks.
"""
from __future__ import annotations

import pytest

from src.paper_3.evidence import (
    FUZZY_THRESHOLD,
    MIN_QUOTE_CHARS,
    EvidencePassage,
    build_passages,
    normalize_text,
    split_sentences,
    verify_quote,
)

P1 = ("The median longitudinal water-surface slope increased from 0.090 cm/km "
      "before the breach to 3.314 cm/km afterwards.")
P2 = ("We harmonised all elevations to EVRF2019 using the EPSG:9902 coordinate "
      "operation, whose stated accuracy is 0.068 m.")
P3 = ("Wind setup and seiche activity explain the residual scatter observed in "
      "the pre-breach gauge record.")

PASSAGES = {"P1": P1, "P2": P2, "P3": P3}


# ── acceptance ────────────────────────────────────────────────────────────────

def test_exact_quote_is_verified():
    v = verify_quote(P1, PASSAGES)
    assert v.verified
    assert v.passage_id == "P1"
    assert v.similarity == 1.0
    assert not v.repaired


def test_substring_of_a_passage_is_verified():
    v = verify_quote("increased from 0.090 cm/km before the breach to 3.314 cm/km", PASSAGES)
    assert v.verified
    assert v.passage_id == "P1"


def test_collapsed_whitespace_still_matches():
    v = verify_quote("We  harmonised   all elevations to EVRF2019\nusing the "
                     "EPSG:9902 coordinate operation", PASSAGES)
    assert v.verified
    assert v.passage_id == "P2"
    assert v.similarity == 1.0


def test_smart_quotes_and_dashes_are_absorbed():
    passages = {"P1": 'The "tide-free" system is used by ATL03 products.'}
    v = verify_quote('The “tide–free” system is used by ATL03 products.', passages)
    assert v.verified


def test_soft_hyphen_and_ligature_are_absorbed():
    passages = {"P1": "The classification of inland water surfaces is difficult."}
    v = verify_quote("The classi­fication of inland water surfaces is diﬃcult.",
                     passages)
    assert v.verified


# ── rejection ─────────────────────────────────────────────────────────────────

def test_paraphrase_is_rejected():
    v = verify_quote("The slope went up a lot after the dam broke, from almost "
                     "nothing to over three centimetres per kilometre.", PASSAGES)
    assert not v.verified
    assert v.similarity < FUZZY_THRESHOLD


def test_invented_sentence_is_rejected():
    v = verify_quote("The authors conclude that satellite altimetry cannot detect "
                     "hydraulic regime transitions at any scale.", PASSAGES)
    assert not v.verified


def test_quote_spliced_from_two_passages_is_rejected():
    spliced = ("The median longitudinal water-surface slope increased from 0.090 cm/km "
               "whose stated accuracy is 0.068 m.")
    v = verify_quote(spliced, PASSAGES)
    assert not v.verified, "a quote spanning two passages must not verify"


def test_empty_and_whitespace_quotes_are_rejected_not_raised():
    for bad in ("", "   ", "\n"):
        v = verify_quote(bad, PASSAGES)
        assert not v.verified
        assert "empty" in v.reason


def test_quote_shorter_than_minimum_is_rejected():
    short = P1[:MIN_QUOTE_CHARS - 5]
    v = verify_quote(short, PASSAGES)
    assert not v.verified
    assert str(MIN_QUOTE_CHARS) in v.reason


def test_no_passages_offered_is_rejected_not_raised():
    v = verify_quote(P1, {})
    assert not v.verified
    assert "no passages" in v.reason


# ── the fuzzy boundary ────────────────────────────────────────────────────────

def _corrupt(text: str, fraction: float) -> str:
    """Replace `fraction` of characters with 'x', spread evenly through the text."""
    chars = list(text)
    n = max(1, int(len(chars) * fraction))
    step = max(1, len(chars) // n)
    for i in range(0, len(chars), step):
        chars[i] = "x"
    return "".join(chars)


def test_high_similarity_quote_is_repaired_not_rejected():
    slightly_off = P1.replace("afterwards", "afterward")  # one character
    v = verify_quote(slightly_off, PASSAGES)
    assert v.verified
    assert v.repaired
    assert v.similarity >= FUZZY_THRESHOLD


def test_repaired_quote_stores_the_passage_text_not_the_model_text():
    slightly_off = P2.replace("harmonised", "harmonized")  # British vs US spelling
    v = verify_quote(slightly_off, PASSAGES)
    assert v.verified and v.repaired
    assert "harmonised" in v.text, "must store the source's wording"
    assert "harmonized" not in v.text, "must not launder the model's wording"


def test_heavily_corrupted_quote_falls_below_threshold():
    v = verify_quote(_corrupt(P1, 0.20), PASSAGES)
    assert not v.verified
    assert v.similarity < FUZZY_THRESHOLD


def test_verdict_reports_the_best_candidate_even_when_rejected():
    v = verify_quote(_corrupt(P3, 0.30), PASSAGES)
    assert not v.verified
    assert v.passage_id == "P3", "rejection should still name the nearest passage"


# ── normalisation ─────────────────────────────────────────────────────────────

def test_normalize_text_is_idempotent():
    once = normalize_text(P1)
    assert normalize_text(once) == once


def test_normalize_text_preserves_case():
    assert "EVRF2019" in normalize_text(P2)


def test_normalize_text_handles_none_and_empty():
    assert normalize_text("") == ""
    assert normalize_text(None) == ""


# ── passage construction ──────────────────────────────────────────────────────

KEY_TERMS = (
    ("water surface slope", "water-surface slope"),
    ("reservoir", "breach"),
)

SECTIONS = {
    "results": (
        "The median longitudinal water-surface slope rose after the reservoir breach. "
        "Unrelated filler about instrument calibration procedures follows here. "
        "A second sentence mentions the water-surface slope of the reservoir again."
    ),
    "methods": "We describe the study area and the general approach taken.",
}


def test_build_passages_returns_only_matching_sentences():
    out = build_passages(SECTIONS, "paper_x", "paper_x.json", KEY_TERMS)
    assert out, "expected at least one passage"
    assert all(isinstance(p, EvidencePassage) for p in out)
    assert all(p.n_families_hit >= 1 for p in out)


def test_build_passages_returns_empty_when_no_key_terms_hit():
    sections = {"body": "This paper is about urban traffic congestion forecasting."}
    assert build_passages(sections, "paper_y", "paper_y.json", KEY_TERMS) == []


def test_build_passages_ids_are_sequential_and_unique():
    out = build_passages(SECTIONS, "paper_x", "paper_x.json", KEY_TERMS)
    ids = [p.passage_id for p in out]
    assert ids == [f"P{i + 1}" for i in range(len(out))]
    assert len(set(ids)) == len(ids)


def test_build_passages_respects_max():
    long_section = {"body": " ".join(
        [f"The water-surface slope of the reservoir changed in year {i}." for i in range(50)]
    )}
    out = build_passages(long_section, "p", "p.json", KEY_TERMS, max_passages=5)
    assert len(out) == 5


def test_build_passages_negative_term_suppresses_single_family_hit():
    sections = {"body": "We performed a SWOT analysis of the reservoir programme."}
    key_terms = (("SWOT",), ("reservoir",))
    kept = build_passages(sections, "p", "p.json", key_terms)
    dropped = build_passages(sections, "p", "p.json", key_terms,
                             negative_terms=("SWOT analysis",))
    # Two families hit, so the negative term alone should not remove it; the rule
    # only suppresses weak single-family hits.
    assert kept and dropped, "two-family hits survive a negative term"

    weak = {"body": "We performed a SWOT analysis of the programme."}
    assert build_passages(weak, "p", "p.json", key_terms) != []
    assert build_passages(weak, "p", "p.json", key_terms,
                          negative_terms=("SWOT analysis",)) == []


def test_build_passages_output_is_verifiable_against_itself():
    """A passage built from a paper must verify as a quote of that passage."""
    out = build_passages(SECTIONS, "paper_x", "paper_x.json", KEY_TERMS)
    offered = {p.passage_id: p.text for p in out}
    for p in out:
        v = verify_quote(p.text, offered)
        assert v.verified and v.passage_id == p.passage_id


# ── sentence splitting ────────────────────────────────────────────────────────

@pytest.mark.parametrize("text,expected", [
    ("One sentence. Two sentences! Three?", 3),
    ("No terminal punctuation", 1),
    ("", 0),
])
def test_split_sentences_counts(text, expected):
    assert len(split_sentences(text)) == expected
