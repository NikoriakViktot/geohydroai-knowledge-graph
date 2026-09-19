"""Adjudicating from abstracts: the same rigour, weaker provenance, said out loud.

Two things this file is here to pin:

* the upgrade path — an abstract adjudication must not block the later full-text
  one, and full text must win when both exist;
* the demotions — abstract-level evidence can never produce a novelty claim,
  because a paper can test a thesis thoroughly in its results and never mention
  it in the abstract.
"""
from __future__ import annotations

import pandas as pd
import pytest

from src.paper_3 import abstract_mode as am
from src.paper_3 import gap_matrix as gm
from src.paper_3.evidence import verify_quote
from src.paper_3.theses import get_thesis, load_theses

ABSTRACT = (
    "We harmonised ICESat-2 ATL13 and SWOT water surface elevations to a common "
    "vertical datum using the EGM2008 geoid before comparison. "
    "The residual bias between the two sensors was 3.6 mm under close temporal "
    "collocation. "
    "We conclude that vertical datum harmonisation is a prerequisite for "
    "cross-mission water level comparison."
)
TITLE = "Vertical datum harmonisation for cross-mission water surface elevation"


@pytest.fixture(scope="module")
def theses():
    return load_theses()


@pytest.fixture(scope="module")
def thesis(theses):
    return get_thesis("T08", theses)


# ── passages from an abstract ─────────────────────────────────────────────────

def test_passages_come_out_of_the_abstract(thesis):
    passages = am.build_abstract_passages(
        TITLE, ABSTRACT, "p1", "10.1/x", thesis.key_terms, thesis.negative_terms)
    assert passages
    for p in passages:
        assert p.text in f"{TITLE} {ABSTRACT}" or p.text in ABSTRACT


def test_quote_verification_works_against_an_abstract(thesis):
    """The abstract *is* the source text, so the existing verifier applies."""
    passages = am.build_abstract_passages(
        TITLE, ABSTRACT, "p1", "10.1/x", thesis.key_terms, thesis.negative_terms)
    offered = {p.passage_id: p.text for p in passages}

    real = passages[0].text
    assert verify_quote(real, offered).verified

    invented = ("The authors report that vertical datums have no effect on "
                "cross-mission comparison whatsoever.")
    assert not verify_quote(invented, offered).verified


def test_source_file_records_that_this_came_from_an_inverted_index(thesis):
    passages = am.build_abstract_passages(
        TITLE, ABSTRACT, "p1", "10.1/x", thesis.key_terms, thesis.negative_terms)
    assert all(p.source_file.startswith(am.QUOTE_SOURCE_AUTHORITY) for p in passages)


def test_no_passages_without_text(thesis):
    assert am.build_abstract_passages(
        "", "", "p1", "10.1/x", thesis.key_terms, thesis.negative_terms) == []


def test_fewer_passages_than_the_full_text_path(thesis):
    """A long abstract must not be offered wholesale, or the anti-splicing
    guard stops discriminating between adjacent passages."""
    long_abstract = " ".join([ABSTRACT] * 6)
    passages = am.build_abstract_passages(
        TITLE, long_abstract, "p1", "10.1/x", thesis.key_terms, thesis.negative_terms)
    assert len(passages) <= am.MAX_PASSAGES


# ── the prefilter ─────────────────────────────────────────────────────────────

def test_an_on_topic_abstract_passes(thesis):
    ok, reason, score = am.prefilter_abstract(thesis, TITLE, ABSTRACT)
    assert ok and score > 0
    assert "key-term families" in reason


def test_a_missing_abstract_is_rejected_with_a_reason(thesis):
    ok, reason, _ = am.prefilter_abstract(thesis, TITLE, "")
    assert not ok
    assert "abstract missing" in reason


def test_a_stub_abstract_is_rejected(thesis):
    ok, reason, _ = am.prefilter_abstract(thesis, TITLE, "Short note.")
    assert not ok
    assert str(am.MIN_ABSTRACT_CHARS) in reason


def test_an_off_topic_abstract_is_rejected(thesis):
    off = ("We forecast urban traffic congestion from mobile phone data across "
           "twelve metropolitan areas using a recurrent neural network trained "
           "on three years of anonymised trajectories, achieving good accuracy.")
    ok, reason, _ = am.prefilter_abstract(thesis, "Traffic forecasting", off)
    assert not ok
    assert "not on topic" in reason


def test_the_prefilter_uses_the_same_on_topic_rule_as_full_text(thesis):
    """Only the text differs between the two paths, never the topical test."""
    generic = ("We compare satellite water level estimates against gauges and "
               "report the bias between them over a long study period, with "
               "careful attention to the statistical treatment of the residuals.")
    assert thesis.families_hit(generic) >= 2, "generic text does hit two families"
    assert not thesis.is_on_topic(generic)
    assert not am.prefilter_abstract(thesis, "", generic)[0]


# ── candidate building ────────────────────────────────────────────────────────

def _harvest(**over):
    row = {
        "doi": "10.1/x", "title": TITLE, "abstract": ABSTRACT, "year": 2024,
        "journal": "J", "cited_by_count": 5, "n_query_hits": 2, "is_seed": False,
        "matched_thesis_ids": ["T08"],
    }
    row.update(over)
    return pd.DataFrame([row])


def test_candidates_use_the_slug_the_harvest_will_use_later(tmp_path, theses):
    """The one choice that makes the full-text pass an upgrade, not a duplicate."""
    from src.paper_3._utils import doi_to_slug

    out = am.build_candidates(_harvest(), theses, tmp_path, corpus_dois=set())
    assert out.loc[0, "paper_id"] == doi_to_slug("10.1/x")


def test_candidates_record_the_evidence_level(tmp_path, theses):
    out = am.build_candidates(_harvest(), theses, tmp_path, corpus_dois=set())
    assert set(out["evidence_level"]) == {"abstract"}


def test_candidates_flag_whether_the_paper_was_already_in_the_graph(tmp_path, theses):
    """Without this, a 3 680-paper knowledge graph could be quietly replaced by
    a fresh OpenAlex search and nobody would see it happen."""
    inside = am.build_candidates(_harvest(), theses, tmp_path,
                                 corpus_dois={"10.1/x"})
    assert bool(inside.loc[0, "in_existing_corpus"])

    outside = am.build_candidates(_harvest(), theses, tmp_path, corpus_dois=set())
    assert not bool(outside.loc[0, "in_existing_corpus"])


def test_rejected_candidates_are_kept_with_their_reason(tmp_path, theses):
    out = am.build_candidates(_harvest(abstract=""), theses, tmp_path,
                              corpus_dois=set())
    assert len(out) == 1
    assert not out.loc[0, "prefilter_pass"]
    assert out.loc[0, "prefilter_reason"]


def test_a_row_with_no_doi_is_skipped(tmp_path, theses):
    assert am.build_candidates(_harvest(doi=""), theses, tmp_path,
                               corpus_dois=set()).empty


def test_top_n_caps_the_passing_candidates(tmp_path, theses):
    rows = pd.concat([_harvest(doi=f"10.1/{i}") for i in range(20)],
                     ignore_index=True)
    out = am.build_candidates(rows, theses, tmp_path, top_n_per_thesis=5,
                              corpus_dois=set())
    assert int(out["prefilter_pass"].sum()) == 5


def test_missing_abstracts_are_measured_per_thesis(tmp_path, theses):
    """A paywall must not read as a literature gap."""
    rows = pd.concat([_harvest(doi="10.1/a"),
                      _harvest(doi="10.1/b", abstract="")], ignore_index=True)
    out = am.build_candidates(rows, theses, tmp_path, corpus_dois=set())
    assert am.abstracts_missing_by_thesis(out)["T08"] == 0.5


def test_abstracts_missing_on_an_empty_frame():
    assert am.abstracts_missing_by_thesis(pd.DataFrame()) == {}


# ── the demotions ─────────────────────────────────────────────────────────────

def test_abstract_only_can_never_produce_a_gap_claim():
    """The load-bearing rule of this whole pass."""
    counts = gm.ThesisCounts(papers_found=9)
    assert gm.assign_verdict_full(counts, gm.ADEQUATE)[0] == gm.CANDIDATE_GAP

    verdict, rationale, qualifier = gm.assign_verdict_full(
        counts, gm.ADEQUATE, evidence_level_mix=gm.ABSTRACT_ONLY)
    assert verdict == gm.RETRIEVAL_INCOMPLETE
    assert qualifier == "ABSTRACT_ONLY"
    assert "nor that no paper tests the thesis" in rationale


def test_abstract_only_demotes_known_to_sparse():
    counts = gm.ThesisCounts(papers_found=3, n_supporting_papers=3,
                             n_direct_support_papers=1)
    assert gm.assign_verdict_full(counts, gm.ADEQUATE)[0] == gm.KNOWN
    assert gm.assign_verdict_full(
        counts, gm.ADEQUATE,
        evidence_level_mix=gm.ABSTRACT_ONLY)[0] == gm.SUPPORTED_BUT_SPARSE


def test_abstract_only_leaves_contested_alone():
    """A contradiction found in an abstract is still a contradiction."""
    counts = gm.ThesisCounts(papers_found=3, n_supporting_papers=1,
                             n_contradicting_papers=1)
    verdict, _r, qualifier = gm.assign_verdict_full(
        counts, gm.ADEQUATE, evidence_level_mix=gm.ABSTRACT_ONLY)
    assert verdict == gm.CONTESTED
    assert qualifier == "ABSTRACT_ONLY"


def test_mixed_evidence_is_not_demoted():
    counts = gm.ThesisCounts(papers_found=9)
    verdict, _r, qualifier = gm.assign_verdict_full(
        counts, gm.ADEQUATE, evidence_level_mix=gm.MIXED_LEVEL)
    assert verdict == gm.CANDIDATE_GAP
    assert qualifier == ""


# ── full text wins when both exist ────────────────────────────────────────────

def _rel(level, relation="SUPPORTS", verified=True, confidence=0.9):
    return {
        "thesis_id": "T08", "paper_id": "p1", "doi": "10.1/x", "title": "T",
        "year": 2024, "relation": relation, "confidence": confidence,
        "evidence_quote": "a quote long enough to be checked properly",
        "quote_verified": verified, "quote_similarity": 1.0,
        "system_class": "reservoir", "is_own_result": True,
        "model": "m", "override_applied": False, "evidence_level": level,
    }


def test_full_text_outranks_an_abstract_for_the_same_paper():
    rows = pd.DataFrame([_rel("abstract", "ANALOGUE"),
                         _rel("full_text", "SUPPORTS")])
    collapsed, _ = gm.collapse_to_papers(rows)
    assert collapsed.loc[0, "relation"] == "SUPPORTS"


def test_a_verified_abstract_beats_an_unverified_full_text():
    """Verification outranks depth, for the same reason it outranks priority."""
    rows = pd.DataFrame([_rel("abstract", "SUPPORTS", verified=True),
                         _rel("full_text", "CONTRADICTS", verified=False)])
    collapsed, _ = gm.collapse_to_papers(rows)
    assert collapsed.loc[0, "relation"] == "SUPPORTS"


def test_rows_without_an_evidence_level_still_collapse():
    rows = pd.DataFrame([{k: v for k, v in _rel("abstract").items()
                          if k != "evidence_level"}])
    collapsed, _ = gm.collapse_to_papers(rows)
    assert len(collapsed) == 1


# ── exploratory naming ────────────────────────────────────────────────────────

def test_an_ineligible_matrix_never_gets_the_canonical_name():
    assert gm.matrix_filename(False) == gm.EXPLORATORY_MATRIX
    assert gm.matrix_filename(True) == gm.CANONICAL_MATRIX
    assert "EXPLORATORY" in gm.EXPLORATORY_MATRIX


def test_the_exploratory_header_forbids_citing_it_as_a_gap(theses):
    empty = pd.DataFrame(columns=["thesis_id", "paper_id", "relation"])
    matrix = gm.build(empty, theses=theses,
                      evidence_levels={t.id: gm.ABSTRACT_ONLY for t in theses})
    text = gm.render_md(matrix, publication_eligible=False)
    assert "EXPLORATORY" in text
    assert "may be cited as evidence that a gap exists" in text
    assert "abstract-level evidence only" in text
