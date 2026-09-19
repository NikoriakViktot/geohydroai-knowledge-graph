"""Verdict rules and matrix assembly.

The verdict is the sentence the manuscript will eventually make about its own
novelty, so each rule is pinned here rather than left to whatever the aggregation
happens to do.
"""
from __future__ import annotations

import pandas as pd
import pytest

from src.paper_3.gap_matrix import (
    ABSENT,
    ADEQUATE,
    KNOWN,
    CANDIDATE_GAP,
    ControlRecall,
    RETRIEVAL_INCOMPLETE,
    CONTESTED,
    METHOD_ONLY,
    ANALOGUE_ONLY,
    MIXED,
    NOT_FOUND,
    RETRIEVAL_UNVALIDATED,
    SUPPORTED_BUT_SPARSE,
    THIN,
    ThesisCounts,
    assign_verdict,
    build,
    collapse_to_papers,
    count_relations,
    pick_closest_study,
    render_md,
)
from src.paper_3.theses import load_theses


def rel(thesis_id="T01", paper_id="p1", relation="SUPPORTS", verified=True,
        system="reservoir", own=True, confidence=0.9, doi="10.1/a",
        model="gemini-2.5-flash", **over):
    row = {
        "thesis_id": thesis_id, "paper_id": paper_id, "doi": doi,
        "title": f"Paper {paper_id}", "year": 2023, "relation": relation,
        "confidence": confidence, "evidence_quote": "a quote long enough to matter here",
        "quote_verified": verified, "quote_similarity": 1.0 if verified else 0.4,
        "system_class": system, "is_own_result": own,
        "model": model, "override_applied": False,
    }
    row.update(over)
    return row


def frame(rows):
    return pd.DataFrame(rows)


# ── counting ──────────────────────────────────────────────────────────────────

def test_counts_by_relation():
    c = count_relations(frame([
        rel(paper_id="a", relation="SUPPORTS"),
        rel(paper_id="b", relation="CONTRADICTS"),
        rel(paper_id="c", relation="METHOD_RELEVANT"),
        rel(paper_id="d", relation="ANALOGUE"),
    ]))
    assert (c.n_supporting_papers, c.n_contradicting_papers, c.n_method_relevant_papers, c.n_analogue_papers) == (1, 1, 1, 1)
    assert c.papers_found == 4


def test_unverified_support_is_rejected_not_counted():
    c = count_relations(frame([rel(relation="SUPPORTS", verified=False)]))
    assert c.n_supporting_papers == 0, "an unverifiable quote is not evidence"
    assert c.n_rejected_quotes == 1
    assert c.papers_found == 0


def test_unverified_contradiction_is_also_rejected():
    c = count_relations(frame([rel(relation="CONTRADICTS", verified=False)]))
    assert c.n_contradicting_papers == 0
    assert c.n_rejected_quotes == 1


def test_method_relevant_survives_without_a_verified_quote():
    """METHOD_RELEVANT describes what a paper is, not what it found."""
    c = count_relations(frame([rel(relation="METHOD_RELEVANT", verified=False)]))
    assert c.n_method_relevant_papers == 1
    assert c.n_rejected_quotes == 0


def test_not_relevant_rows_are_counted_as_prefiltered():
    c = count_relations(frame([rel(relation="NOT_RELEVANT", verified=False)]))
    assert c.n_prefiltered_out == 1
    assert c.papers_found == 0


def test_direct_support_requires_own_result_and_a_direct_system():
    cited = count_relations(frame([rel(own=False)]))
    assert cited.n_direct_support_papers == 0
    analogous = count_relations(frame([rel(system="dam_removal")]))
    assert analogous.n_direct_support_papers == 0
    direct = count_relations(frame([rel(system="reservoir", own=True)]))
    assert direct.n_direct_support_papers == 1


def test_counting_an_empty_frame_is_all_zeros():
    c = count_relations(pd.DataFrame(columns=["relation", "paper_id",
                                              "quote_verified", "is_own_result",
                                              "system_class"]))
    assert c == ThesisCounts()


# ── verdict rules ─────────────────────────────────────────────────────────────

def test_known_needs_three_supports_and_a_direct_one():
    v, _ = assign_verdict(ThesisCounts(papers_found=3, n_supporting_papers=3,
                                       n_direct_support_papers=1), ADEQUATE)
    assert v == KNOWN


def test_three_supports_without_a_direct_one_is_sparse():
    v, _ = assign_verdict(ThesisCounts(papers_found=3, n_supporting_papers=3,
                                       n_direct_support_papers=0), ADEQUATE)
    assert v == SUPPORTED_BUT_SPARSE


def test_a_single_support_is_sparse_not_known():
    v, why = assign_verdict(ThesisCounts(papers_found=1, n_supporting_papers=1),
                            ADEQUATE)
    assert v == SUPPORTED_BUT_SPARSE
    assert "too thin" in why


def test_support_and_contradiction_is_contested():
    v, why = assign_verdict(ThesisCounts(papers_found=4, n_supporting_papers=2,
                                         n_contradicting_papers=1), ADEQUATE)
    assert v == CONTESTED
    assert "read them" in why


def test_contradiction_alone_is_also_contested():
    v, why = assign_verdict(ThesisCounts(papers_found=2, n_contradicting_papers=2),
                            ADEQUATE)
    assert v == CONTESTED
    assert "may be wrong as stated" in why


def test_analogues_only():
    v, _ = assign_verdict(ThesisCounts(papers_found=3, n_analogue_papers=2), ADEQUATE)
    assert v == ANALOGUE_ONLY


def test_methods_only():
    v, why = assign_verdict(
        ThesisCounts(papers_found=6, n_method_relevant_papers=3), ADEQUATE)
    assert v == METHOD_ONLY
    assert "none tests the hypothesis" in why


def test_candidate_gap_requires_related_papers_and_nothing_bearing():
    v, why = assign_verdict(ThesisCounts(papers_found=6), ADEQUATE)
    assert v == CANDIDATE_GAP
    assert "Retrieval validated" in why


def test_candidate_gap_is_the_only_novelty_eligible_verdict():
    from src.paper_3.gap_matrix import NOVELTY_ELIGIBLE
    assert NOVELTY_ELIGIBLE == (CANDIDATE_GAP,)


def test_a_contradiction_never_yields_candidate_gap():
    v, _ = assign_verdict(
        ThesisCounts(papers_found=9, n_method_relevant_papers=5,
                     n_contradicting_papers=1), ADEQUATE)
    assert v == CONTESTED


def test_too_little_evidence_is_not_found():
    v, why = assign_verdict(ThesisCounts(papers_found=2), THIN)
    assert v == NOT_FOUND
    assert "too little evidence" in why


# ── retrieval validation gates everything ─────────────────────────────────────

def test_unvalidated_retrieval_overrides_every_other_rule():
    """A null result from a search nobody has shown to work is not a finding."""
    strong = ThesisCounts(papers_found=9, n_supporting_papers=9,
                          n_direct_support_papers=5)
    v, why = assign_verdict(strong, ADEQUATE, retrieval_validated=False)
    assert v == RETRIEVAL_UNVALIDATED
    assert "cannot be distinguished from a retrieval failure" in why


def test_absent_coverage_is_also_unvalidated():
    v, why = assign_verdict(ThesisCounts(papers_found=9, n_supporting_papers=9),
                            ABSENT,
                            {"openalex_worldwide_hits": 37,
                             "openalex_hits_in_corpus": 2,
                             "coverage_ratio": 0.054})
    assert v == RETRIEVAL_UNVALIDATED
    assert "reflects corpus coverage, not the state of the literature" in why
    assert "37" in why and "2" in why


def test_absent_coverage_rationale_survives_missing_numbers():
    v, why = assign_verdict(ThesisCounts(), ABSENT, {})
    assert v == RETRIEVAL_UNVALIDATED
    assert "corpus coverage" in why


# ── closest study ─────────────────────────────────────────────────────────────

def test_contradiction_outranks_support_as_closest_study():
    out = pick_closest_study(frame([
        rel(paper_id="a", relation="SUPPORTS", doi="10.1/support", confidence=1.0),
        rel(paper_id="b", relation="CONTRADICTS", doi="10.1/contra", confidence=0.5),
    ]))
    assert out["closest_existing_study_doi"] == "10.1/contra"


def test_closest_study_is_explicit_when_nothing_was_found():
    out = pick_closest_study(pd.DataFrame(columns=["relation"]))
    assert "none found" in out["closest_existing_study_doi"]


def test_closest_study_ignores_not_relevant_rows():
    out = pick_closest_study(frame([rel(relation="NOT_RELEVANT")]))
    assert "none found" in out["closest_existing_study_doi"]


# ── assembly ──────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def theses():
    return load_theses()


def test_all_theses_appear_even_with_no_relations(theses):
    empty = pd.DataFrame(columns=["thesis_id", "paper_id", "relation",
                                  "quote_verified", "is_own_result", "system_class",
                                  "confidence", "doi", "title", "year",
                                  "evidence_quote", "quote_similarity"])
    matrix = build(empty, theses=theses)
    from src.paper_3.theses import expected_thesis_ids
    assert len(matrix) == len(expected_thesis_ids())
    assert list(matrix["thesis_id"]) == expected_thesis_ids()
    assert set(matrix["verdict"]) == {RETRIEVAL_UNVALIDATED}


def test_build_tolerates_missing_optional_columns(theses):
    minimal = pd.DataFrame([{"thesis_id": "T01", "paper_id": "p1",
                             "relation": "METHOD_RELEVANT"}])
    matrix = build(minimal, theses=theses)
    assert matrix.loc[matrix["thesis_id"] == "T01", "n_method_relevant_papers"].item() == 1


def test_coverage_is_joined_onto_the_matrix(theses):
    relations = frame([rel(thesis_id="T09", relation="SUPPORTS")])
    coverage = pd.DataFrame([{
        "thesis_id": "T09", "corpus_adequacy": ABSENT,
        "openalex_worldwide_hits": 882, "openalex_hits_in_corpus": 0,
        "coverage_ratio": 0.0,
    }])
    # Give T09 a recovered control so the coverage rule is what is being tested,
    # not the missing-control rule that would otherwise fire first.
    matrix = build(relations, coverage, theses=theses,
                   control_recall={"T09": ControlRecall(n_controls=1, n_recovered=1)})
    row = matrix[matrix["thesis_id"] == "T09"].iloc[0]
    assert row["verdict"] == RETRIEVAL_UNVALIDATED
    assert row["openalex_worldwide_hits"] == 882
    assert "corpus coverage" in row["verdict_rationale"]
    assert not row["novelty_eligible"]


def test_matrix_counts_match_recounting_the_relations(theses):
    relations = frame([
        rel(thesis_id="T01", paper_id="a", relation="SUPPORTS"),
        rel(thesis_id="T01", paper_id="b", relation="ANALOGUE"),
        rel(thesis_id="T02", paper_id="c", relation="CONTRADICTS"),
    ])
    matrix = build(relations, theses=theses)
    for tid in ("T01", "T02"):
        expected = count_relations(relations[relations["thesis_id"] == tid])
        row = matrix[matrix["thesis_id"] == tid].iloc[0]
        assert row["n_supporting_papers"] == expected.n_supporting_papers
        assert row["n_contradicting_papers"] == expected.n_contradicting_papers
        assert row["n_analogue_papers"] == expected.n_analogue_papers
        assert row["papers_found"] == expected.papers_found


def test_rejected_quotes_are_excluded_from_counts_but_reported(theses):
    relations = frame([
        rel(thesis_id="T01", paper_id="a", relation="SUPPORTS", verified=True),
        rel(thesis_id="T01", paper_id="b", relation="SUPPORTS", verified=False),
    ])
    row = build(relations, theses=theses).query("thesis_id == 'T01'").iloc[0]
    assert row["n_supporting_papers"] == 1
    assert row["n_rejected_quotes"] == 1


# ── rendering ─────────────────────────────────────────────────────────────────

def test_render_md_contains_every_thesis_and_its_verdict(theses):
    relations = frame([rel(thesis_id="T01", relation="SUPPORTS")])
    md = render_md(build(relations, theses=theses))
    for t in theses:
        assert t.id in md
    assert "Gap Evidence Matrix" in md


def test_render_md_discloses_a_missing_knowledge_graph(theses):
    empty = pd.DataFrame(columns=["thesis_id", "relation", "paper_id"])
    md = render_md(build(empty, theses=theses, kg_expansion_available=False))
    assert "Neo4j was unavailable" in md
    assert "lower bound" in md


def test_render_md_on_an_empty_matrix_does_not_raise():
    assert "No theses" in render_md(pd.DataFrame())


# ── rendering optional values ─────────────────────────────────────────────────

def test_render_md_does_not_print_nan_for_a_missing_numeric_anchor(theses):
    """Parquet turns None into NaN; a literal 'nan' would read as a value."""
    relations = frame([rel(thesis_id="T09", relation="SUPPORTS")])
    matrix = build(relations, theses=theses)
    matrix["numeric_anchor"] = pd.NA
    md = render_md(matrix)
    assert "number under test: `nan`" not in md
    assert "number under test" not in md


def test_render_md_prints_a_numeric_anchor_when_present(theses):
    matrix = build(frame([rel(thesis_id="T03")]), theses=theses)
    md = render_md(matrix)
    assert "number under test" in md
    assert "cm/km" in md


def test_render_md_formats_years_as_integers(theses):
    matrix = build(frame([rel(thesis_id="T01", relation="SUPPORTS")]), theses=theses)
    md = render_md(matrix)
    assert "2023)" in md
    assert "2023.0" not in md


# ── the unit of evidence is the paper, not the passage ────────────────────────

def test_one_paper_adjudicated_twice_counts_once():
    """Re-running with a second model must not manufacture a supporting paper."""
    rows = frame([
        rel(paper_id="p1", relation="SUPPORTS", model="gemini-2.5-flash"),
        rel(paper_id="p1", relation="SUPPORTS", model="ollama:mistral-nemo:12b"),
    ])
    c = count_relations(rows)
    assert c.n_supporting_papers == 1, "two adjudications of one paper is one paper"
    assert c.papers_found == 1
    assert c.n_supporting_passages == 2, "but two pieces of provenance"


def test_a_rerun_cannot_push_a_thesis_to_known():
    """Two papers adjudicated twice each is still two supporting papers."""
    rows = frame([
        rel(paper_id=pid, relation="SUPPORTS", doi=f"10.1/{pid}", model=m)
        for pid in ("p1", "p2")
        for m in ("gemini-2.5-flash", "ollama:mistral-nemo:12b")
    ])
    c = count_relations(rows)
    assert c.n_supporting_papers == 2
    verdict, _ = assign_verdict(c, ADEQUATE)
    assert verdict != KNOWN, "four rows must not satisfy the three-paper rule"


def test_direct_support_papers_are_deduplicated_too():
    rows = frame([
        rel(paper_id="p1", relation="SUPPORTS", model="m1"),
        rel(paper_id="p1", relation="SUPPORTS", model="m2"),
    ])
    assert count_relations(rows).n_direct_support_papers == 1


def test_disagreeing_adjudications_are_reported_not_hidden():
    rows = frame([
        rel(paper_id="p1", relation="SUPPORTS", model="m1"),
        rel(paper_id="p1", relation="CONTRADICTS", model="m2"),
    ])
    c = count_relations(rows)
    assert c.n_model_disagreements == 1


def test_a_paper_saying_both_becomes_mixed_not_contradicting():
    """Genuinely mixed evidence is its own category, counted towards neither side."""
    rows = frame([
        rel(paper_id="p1", relation="SUPPORTS", confidence=0.99, model="m1"),
        rel(paper_id="p1", relation="CONTRADICTS", confidence=0.10, model="m2"),
    ])
    c = count_relations(rows)
    assert c.n_mixed_papers == 1
    assert c.n_supporting_papers == 0
    assert c.n_contradicting_papers == 0
    assert c.papers_found == 1


def test_mixed_requires_both_sides_to_be_verified():
    """An unverifiable contradiction does not make a supporting paper mixed."""
    rows = frame([
        rel(paper_id="p1", relation="SUPPORTS", verified=True, model="m1"),
        rel(paper_id="p1", relation="CONTRADICTS", verified=False, model="m2"),
    ])
    c = count_relations(rows)
    assert c.n_mixed_papers == 0
    assert c.n_supporting_papers == 1


def test_mixed_does_not_count_towards_known():
    counts = ThesisCounts(papers_found=3, n_mixed_papers=3, n_supporting_papers=0)
    verdict, _ = assign_verdict(counts, ADEQUATE)
    assert verdict != KNOWN


def test_a_human_override_can_resolve_a_mixed_paper():
    rows = frame([
        rel(paper_id="p1", relation="SUPPORTS", model="m1"),
        rel(paper_id="p1", relation="CONTRADICTS", model="m2"),
        rel(paper_id="p1", relation="SUPPORTS", model="human",
            override_applied=True),
    ])
    c = count_relations(rows)
    assert c.n_mixed_papers == 0
    assert c.n_supporting_papers == 1


def test_a_human_override_outranks_every_model():
    rows = frame([
        rel(paper_id="p1", relation="CONTRADICTS", confidence=0.99, model="m1"),
        rel(paper_id="p1", relation="ANALOGUE", confidence=0.10, model="m2",
            override_applied=True),
    ])
    c = count_relations(rows)
    assert c.n_analogue_papers == 1
    assert c.n_contradicting_papers == 0


def test_collapse_returns_one_row_per_paper():
    rows = frame([
        rel(paper_id="p1", model="m1"), rel(paper_id="p1", model="m2"),
        rel(paper_id="p2", model="m1"),
    ])
    collapsed, disagreements = collapse_to_papers(rows)
    assert len(collapsed) == 2
    assert disagreements == 0


def test_collapse_of_an_empty_frame():
    empty = pd.DataFrame(columns=["paper_id", "relation", "quote_verified",
                                  "confidence"])
    collapsed, disagreements = collapse_to_papers(empty)
    assert collapsed.empty and disagreements == 0


def test_matrix_separates_paper_and_passage_counts(theses):
    rows = frame([
        rel(thesis_id="T01", paper_id="p1", relation="SUPPORTS", model="m1"),
        rel(thesis_id="T01", paper_id="p1", relation="SUPPORTS", model="m2"),
    ])
    row = build(rows, theses=theses).query("thesis_id == 'T01'").iloc[0]
    assert row["n_supporting_papers"] == 1
    assert row["n_supporting_passages"] == 2


def test_render_md_labels_the_counts_as_papers(theses):
    md = render_md(build(frame([rel(thesis_id="T01")]), theses=theses))
    assert "are **papers**, not passages" in md
    assert "Evidence (papers)" in md
    assert "Evidence (passages)" in md


# ── retrieval completeness gates the gap claim ────────────────────────────────

def _recall(**over):
    base = dict(n_controls=2, n_recovered=2, n_direct_verified=2,
                n_direct_recovered=2, n_holdout=1, n_holdout_recovered=1,
                n_expected_stage_recovered=2, n_semantic_recovered=2)
    base.update(over)
    return ControlRecall(**base)


def test_a_missed_direct_control_forbids_candidate_gap():
    """The answer to 'how do you know the literature is absent?'"""
    counts = ThesisCounts(papers_found=9)
    assert assign_verdict(counts, ADEQUATE, recall=_recall())[0] == CANDIDATE_GAP

    verdict, why = assign_verdict(
        counts, ADEQUATE, recall=_recall(n_direct_recovered=1))
    assert verdict == RETRIEVAL_INCOMPLETE
    assert "cannot be cited as evidence that no such paper exists" in why


def test_a_missed_direct_control_also_forbids_not_found():
    """A thin null result is just as uninterpretable as a broad one."""
    verdict, _ = assign_verdict(ThesisCounts(papers_found=2), THIN,
                                recall=_recall(n_direct_recovered=0))
    assert verdict == RETRIEVAL_INCOMPLETE


def test_a_missed_direct_control_does_not_erase_evidence_that_was_found():
    """Papers that WERE found stay found, whatever else the search missed."""
    counts = ThesisCounts(papers_found=4, n_supporting_papers=3,
                          n_direct_support_papers=1)
    verdict, _ = assign_verdict(counts, ADEQUATE, recall=_recall(n_direct_recovered=0))
    assert verdict == KNOWN


def test_a_missed_contradiction_control_still_reports_contested():
    counts = ThesisCounts(papers_found=3, n_supporting_papers=1,
                          n_contradicting_papers=1)
    verdict, _ = assign_verdict(counts, ADEQUATE, recall=_recall(n_direct_recovered=0))
    assert verdict == CONTESTED


def test_unverified_controls_do_not_block_a_gap_claim():
    """A guess must not block a verdict any more than it may license one."""
    verdict, _ = assign_verdict(
        ThesisCounts(papers_found=9), ADEQUATE,
        recall=_recall(n_direct_verified=0, n_direct_recovered=0))
    assert verdict == CANDIDATE_GAP


def test_candidate_gap_rationale_cites_its_control_recall():
    _v, why = assign_verdict(ThesisCounts(papers_found=9), ADEQUATE,
                             recall=_recall())
    assert "2/2 direct" in why
    assert "holdout 1/1" in why


def test_no_recall_object_keeps_the_old_permissive_behaviour():
    """Callers that cannot supply recall are not silently blocked."""
    verdict, _ = assign_verdict(ThesisCounts(papers_found=9), ADEQUATE, recall=None)
    assert verdict == CANDIDATE_GAP


# ── the three recall metrics ──────────────────────────────────────────────────

def test_recall_metrics_are_independent():
    r = ControlRecall(n_controls=4, n_recovered=4,
                      n_expected_stage_recovered=2, n_semantic_recovered=1)
    assert r.overall == 1.0
    assert r.expected_stage == 0.5
    assert r.semantic == 0.25


def test_a_citation_only_recovery_passes_overall_and_fails_semantic():
    """Recovered through the citation graph is weaker than 'recall passed'."""
    r = ControlRecall(n_controls=1, n_recovered=1,
                      n_expected_stage_recovered=0, n_semantic_recovered=0)
    assert r.overall == 1.0 and r.validated
    assert r.semantic == 0.0 and r.expected_stage == 0.0


def test_recall_of_nothing_is_zero_not_a_crash():
    r = ControlRecall()
    assert (r.overall, r.semantic, r.expected_stage, r.holdout) == (0.0, 0.0, 0.0, 0.0)
    assert not r.validated
    assert r.direct_complete, "no direct controls means nothing was missed"


def test_matrix_carries_the_recall_columns(theses):
    matrix = build(frame([rel(thesis_id="T01")]), theses=theses,
                   control_recall={"T01": _recall()})
    row = matrix.query("thesis_id == 'T01'").iloc[0]
    assert row["control_recall"] == 1.0
    assert row["holdout_recall"] == 1.0
    assert row["n_direct_controls"] == 2
    assert row["n_direct_controls_recovered"] == 2


def test_render_md_shows_control_recall(theses):
    md = render_md(build(frame([rel(thesis_id="T01")]), theses=theses,
                         control_recall={"T01": _recall()}))
    assert "Controls" in md
    assert "Retrieval validation" in md
    assert "semantic alone" in md
