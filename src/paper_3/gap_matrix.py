"""Aggregate relations into the per-thesis Gap Evidence Matrix.

The verdict is assigned by the rules in `assign_verdict`, not by a language model.
A model writes the prose in `what_is_established` / `what_remains_missing`; the
label itself is arithmetic on verified evidence, so it can be recomputed and
disputed without re-running anything.

The rule that matters most is the last one: when corpus coverage for a thesis is
`absent`, the verdict is UNKNOWN *and says why*. Silence in a corpus that never
held the relevant literature is not evidence of a gap in the literature, and the
matrix must never let those two be read as the same thing.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from src.paper_3._utils import OUT_DIR
from src.paper_3.theses import NOT_RELEVANT, RELATIONS, Thesis, load_theses

logger = logging.getLogger(__name__)

#: Several independent papers directly confirm the thesis.
KNOWN = "KNOWN"
#: One or two direct papers — real, but too thin to call settled.
SUPPORTED_BUT_SPARSE = "SUPPORTED_BUT_SPARSE"
#: Substantive support and substantive contradiction both exist.
CONTESTED = "CONTESTED"
#: The method the thesis depends on exists, but nobody has tested the hypothesis.
METHOD_ONLY = "METHOD_ONLY"
#: The mechanism is established in other systems, never tested in this one.
ANALOGUE_ONLY = "ANALOGUE_ONLY"
#: Retrieval was validated and returned nothing bearing on the thesis.
NOT_FOUND = "NOT_FOUND"
#: No hold-out positive control passed, so a null result is uninterpretable.
RETRIEVAL_UNVALIDATED = "RETRIEVAL_UNVALIDATED"
#: Retrieval works in general for this thesis, but it missed a *verified direct*
#: control — so it cannot be trusted to have found direct evidence, which is
#: precisely what a gap claim asserts does not exist.
RETRIEVAL_INCOMPLETE = "RETRIEVAL_INCOMPLETE"
#: Retrieval validated, literature searched, no direct evidence found.
#: **The only verdict that may become a novelty claim in the manuscript.**
CANDIDATE_GAP = "CANDIDATE_GAP"

VERDICTS = (KNOWN, SUPPORTED_BUT_SPARSE, CONTESTED, METHOD_ONLY, ANALOGUE_ONLY,
            NOT_FOUND, RETRIEVAL_UNVALIDATED, RETRIEVAL_INCOMPLETE, CANDIDATE_GAP)

#: Verdicts that may be cited as novelty in §7.8. Exactly one.
NOVELTY_ELIGIBLE = (CANDIDATE_GAP,)

VERDICT_MEANING = {
    KNOWN: "several independent papers directly confirm this",
    SUPPORTED_BUT_SPARSE: "one or two direct papers — real but thin",
    CONTESTED: "substantive support and substantive contradiction both exist",
    METHOD_ONLY: "the method exists; the hypothesis is untested",
    ANALOGUE_ONLY: "established in other systems, not tested in this one",
    NOT_FOUND: "retrieval validated; nothing bearing on the thesis returned",
    RETRIEVAL_UNVALIDATED: "no positive control passed — a null result here "
                           "cannot be distinguished from a retrieval failure",
    RETRIEVAL_INCOMPLETE: "retrieval missed a verified direct control for this "
                          "thesis, so it cannot be trusted to have found direct "
                          "evidence — no gap may be claimed here",
    CANDIDATE_GAP: "retrieval validated and no direct evidence found — "
                   "eligible for a novelty claim after reading the precedents",
}

ADEQUATE, THIN, ABSENT = "adequate", "thin", "absent"

#: How deep the evidence behind a thesis goes.
ABSTRACT_ONLY, MIXED_LEVEL, FULL_TEXT = "abstract_only", "mixed", "full_text"

#: Demotions applied after the nine verdict rules, when every piece of evidence
#: for a thesis came from an abstract. An abstract states a headline; it does not
#: let you check that three papers measured the same thing the same way, and it
#: certainly does not show that no paper tests a thesis — a paper can test one
#: thoroughly in its results and never mention it in the abstract.
_ABSTRACT_DEMOTION = {
    KNOWN: SUPPORTED_BUT_SPARSE,
    CANDIDATE_GAP: RETRIEVAL_INCOMPLETE,
}

#: Columns of relations.parquet that gap_matrix depends on.
RELATION_COLUMNS = (
    "thesis_id", "paper_id", "doi", "title", "year", "relation", "confidence",
    "evidence_quote", "quote_verified", "quote_similarity", "system_class",
    "is_own_result",
)

MATRIX_COLUMNS = [
    "thesis_id", "block", "block_title", "statement",
    "papers_found",
    # Paper-level counts — the only ones a verdict rule may read.
    "n_supporting_papers", "n_contradicting_papers",
    "n_method_relevant_papers", "n_analogue_papers", "n_direct_support_papers",
    "n_mixed_papers",
    # Passage-level counts — evidence provenance, never a verdict input.
    "n_supporting_passages", "n_contradicting_passages",
    "n_verified_quotes", "n_rejected_quotes", "n_model_disagreements",
    "closest_existing_study_doi", "closest_existing_study_title",
    "closest_existing_study_year", "closest_existing_study_relation",
    "closest_existing_study_quote", "closest_existing_study_score",
    "what_is_established", "what_remains_missing",
    "verdict", "verdict_rationale",
    "corpus_adequacy", "openalex_worldwide_hits", "openalex_hits_in_corpus",
    "coverage_ratio", "kg_expansion_available", "adjudication_tier",
    # Retrieval validation — the answer to "how do you know nothing was missed?"
    "retrieval_validated", "n_controls", "n_controls_recovered",
    "control_recall", "holdout_recall", "expected_stage_recall", "semantic_recall",
    "n_direct_controls", "n_direct_controls_recovered", "novelty_eligible",
    "evidence_level_mix", "verdict_qualifier", "n_papers_without_abstract",
    "n_candidates_prefiltered_out", "manuscript_anchor", "numeric_anchor",
    "run_id", "generated_at",
]

#: System classes that count as direct evidence for this manuscript's claims.
#: A dam-removal study on a small river supports the mechanism but is an analogue,
#: not a same-system observation.
DIRECT_SYSTEMS = frozenset({"reservoir", "breach", "lake", "river", "multiple"})

NOT_ESTABLISHED = (
    "Not established as a literature gap: OpenAlex reports {worldwide} candidate "
    "works for this thesis, of which {in_corpus} are present in this corpus "
    "(coverage {ratio:.2f}). The absence of supporting evidence here reflects "
    "corpus coverage, not the state of the literature."
)


@dataclass(frozen=True)
class ControlRecall:
    """Hold-out recall for one thesis, at the three granularities that differ.

    `overall` answers "did it end up in the candidate set"; `expected_stage`
    answers "did the stage we predicted deliver it"; `semantic` answers "did
    embedding search find it unaided". A control that arrived only through
    citation expansion passes the first and fails the other two, and reporting
    only the first would overstate how well semantic retrieval works.
    """

    n_controls: int = 0
    n_recovered: int = 0
    n_direct_verified: int = 0
    n_direct_recovered: int = 0
    n_holdout: int = 0
    n_holdout_recovered: int = 0
    n_expected_stage_recovered: int = 0
    n_semantic_recovered: int = 0

    @property
    def overall(self) -> float:
        return self.n_recovered / self.n_controls if self.n_controls else 0.0

    @property
    def expected_stage(self) -> float:
        return (self.n_expected_stage_recovered / self.n_controls
                if self.n_controls else 0.0)

    @property
    def semantic(self) -> float:
        return (self.n_semantic_recovered / self.n_controls
                if self.n_controls else 0.0)

    @property
    def holdout(self) -> float:
        return self.n_holdout_recovered / self.n_holdout if self.n_holdout else 0.0

    @property
    def validated(self) -> bool:
        """At least one control came back — retrieval demonstrably does something."""
        return self.n_recovered > 0

    @property
    def direct_complete(self) -> bool:
        """Every verified direct control was recovered.

        False here forbids CANDIDATE_GAP: a search that missed a paper we know
        is directly relevant cannot be cited as evidence that no such paper exists.
        """
        return self.n_direct_verified == self.n_direct_recovered


@dataclass(frozen=True)
class ThesisCounts:
    """Verified-evidence counts for one thesis.

    **The unit of evidence is the paper, not the passage.** Eight supporting
    sentences in one article are one supporting paper with eight supporting
    passages, and only the first number may enter a verdict rule — otherwise a
    single verbose article could carry a thesis to KNOWN on its own.
    """

    papers_found: int = 0
    #: Papers whose verified evidence both supports and contradicts the thesis.
    #: Counted towards neither side — a paper that says both says neither cleanly.
    n_mixed_papers: int = 0
    n_supporting_papers: int = 0
    n_contradicting_papers: int = 0
    n_method_relevant_papers: int = 0
    n_analogue_papers: int = 0
    #: Evidence provenance — how many adjudicated passages back those papers.
    n_supporting_passages: int = 0
    n_contradicting_passages: int = 0
    n_verified_quotes: int = 0
    n_rejected_quotes: int = 0
    #: Supporting PAPERS reporting their own result in a directly comparable system.
    n_direct_support_papers: int = 0
    n_prefiltered_out: int = 0
    #: Papers where two adjudications disagreed on the relation. Never silent.
    n_model_disagreements: int = 0


#: A paper whose adjudications both support and contradict a thesis is genuinely
#: mixed evidence, not a contradiction. MIXED exists at paper level only — it is
#: never a relation a model may return — and it counts towards neither support
#: nor contradiction, because a paper that says both says neither cleanly.
MIXED = "MIXED"

#: Which relation wins when a paper was adjudicated more than once (two models,
#: or a re-run) and the answers are not contradictory. SUPPORTS+CONTRADICTS is
#: handled before this table is consulted, and becomes MIXED.
_RELATION_PRIORITY = {"CONTRADICTS": 4, "SUPPORTS": 3, "METHOD_RELEVANT": 2,
                      "ANALOGUE": 1, NOT_RELEVANT: 0}

#: Full text outranks an abstract for the same paper — but only after
#: verification, so a verified abstract quote still beats an unverified
#: full-text one, for the same reason verification outranks relation priority.
_LEVEL_PRIORITY = {"full_text": 1, "abstract": 0}


def collapse_to_papers(rows: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """One row per paper. Returns (collapsed, n_disagreements).

    `classify_relation` makes one call per (thesis, paper, model), so running
    twice — or once with Gemini and once with Ollama — yields two rows for the
    same paper. Counting those as two papers would let a re-run manufacture
    evidence, so they are collapsed here before anything is counted.
    """
    if rows.empty:
        return rows, 0

    rows = rows.copy()
    rows["_priority"] = rows["relation"].map(_RELATION_PRIORITY).fillna(0)
    rows["_override"] = rows.get("override_applied",
                                 pd.Series(False, index=rows.index)).fillna(False)
    rows["_verified"] = rows["quote_verified"].fillna(False).astype(bool)
    rows["_level"] = (rows["evidence_level"] if "evidence_level" in rows
                      else pd.Series("full_text", index=rows.index)
                      ).fillna("full_text").map(_LEVEL_PRIORITY).fillna(0)
    rows["_confidence"] = rows["confidence"].fillna(0.0).astype(float)

    disagreements = 0
    mixed_pids: set[str] = set()
    for pid, group in rows.groupby("paper_id"):
        judged = group[group["relation"].isin(RELATIONS)]
        if judged["relation"].nunique() > 1:
            disagreements += 1
        # Both directions asserted on verified evidence: genuinely mixed.
        verified_rel = set(judged.loc[
            judged["quote_verified"].fillna(False).astype(bool), "relation"])
        if {"SUPPORTS", "CONTRADICTS"} <= verified_rel:
            mixed_pids.add(pid)

    # A human override outranks everything; then a VERIFIED quote; then the
    # relation priority; then confidence.
    #
    # Verification must outrank relation priority. With the order reversed, an
    # unverifiable CONTRADICTS would beat a verified SUPPORTS on priority alone,
    # then be discarded for lacking a quote — silently erasing real evidence.
    collapsed = (rows.sort_values(
        ["_override", "_verified", "_level", "_priority", "_confidence"],
        ascending=False)
        .groupby("paper_id", as_index=False)
        .first())

    if mixed_pids:
        # An explicit human override is the one thing that outranks MIXED: a
        # person who has read the paper may legitimately say it is not mixed.
        overridden = set(collapsed.loc[collapsed["_override"], "paper_id"])
        promote = collapsed["paper_id"].isin(mixed_pids - overridden)
        collapsed.loc[promote, "relation"] = MIXED

    return collapsed.drop(columns=["_priority", "_override", "_verified",
                                   "_level", "_confidence"]), disagreements


def count_relations(rows: pd.DataFrame) -> ThesisCounts:
    """Count one thesis's evidence at paper level, excluding failed quotes.

    A relation asserted on an unverifiable quote is not evidence. It is counted
    as a rejection so the loss is visible, never as support.
    """
    if rows.empty:
        return ThesisCounts()

    collapsed, disagreements = collapse_to_papers(rows)

    prefiltered = int((collapsed["relation"] == NOT_RELEVANT).sum())
    judged = collapsed[collapsed["relation"].isin(tuple(RELATIONS) + (MIXED,))]

    # METHOD_RELEVANT and ANALOGUE describe what a paper *is*, and remain valid
    # even when the model failed to copy a sentence cleanly. SUPPORTS and
    # CONTRADICTS assert what a paper *found*, so they require a verified quote.
    needs_quote = judged["relation"].isin(("SUPPORTS", "CONTRADICTS"))
    verified = judged["quote_verified"].fillna(False).astype(bool)
    kept = judged[~needs_quote | verified]
    rejected = judged[needs_quote & ~verified]

    def papers(relation: str) -> int:
        return int(kept.loc[kept["relation"] == relation, "paper_id"].nunique())

    # Passages are counted from the UNCOLLAPSED rows: they are provenance, and
    # several adjudications of one paper are several pieces of provenance.
    all_verified = rows["quote_verified"].fillna(False).astype(bool)
    passages = rows[all_verified]

    direct = kept[
        (kept["relation"] == "SUPPORTS")
        & kept["is_own_result"].fillna(False).astype(bool)
        & kept["system_class"].fillna("not_stated").isin(DIRECT_SYSTEMS)
    ]

    return ThesisCounts(
        papers_found=int(kept["paper_id"].nunique()),
        n_mixed_papers=papers(MIXED),
        n_supporting_papers=papers("SUPPORTS"),
        n_contradicting_papers=papers("CONTRADICTS"),
        n_method_relevant_papers=papers("METHOD_RELEVANT"),
        n_analogue_papers=papers("ANALOGUE"),
        n_supporting_passages=int((passages["relation"] == "SUPPORTS").sum()),
        n_contradicting_passages=int((passages["relation"] == "CONTRADICTS").sum()),
        n_verified_quotes=int(verified.sum()),
        n_rejected_quotes=int(rejected["paper_id"].nunique()),
        n_direct_support_papers=int(direct["paper_id"].nunique()),
        n_prefiltered_out=prefiltered,
        n_model_disagreements=disagreements,
    )


def assign_verdict(counts: ThesisCounts, corpus_adequacy: str,
                   coverage: dict | None = None,
                   retrieval_validated: bool = True,
                   recall: ControlRecall | None = None,
                   evidence_level_mix: str = FULL_TEXT,
                   ) -> tuple[str, str]:
    """Verdict and rationale, after the evidence-level demotion.

    Thin wrapper over `_assign_verdict_core` so the nine rules stay in one
    readable block and the demotion is visibly a separate, later step.
    Use `assign_verdict_full` when the qualifier is needed too.
    """
    verdict, rationale, _qualifier = assign_verdict_full(
        counts, corpus_adequacy, coverage, retrieval_validated, recall,
        evidence_level_mix)
    return verdict, rationale


def assign_verdict_full(counts: ThesisCounts, corpus_adequacy: str,
                        coverage: dict | None = None,
                        retrieval_validated: bool = True,
                        recall: ControlRecall | None = None,
                        evidence_level_mix: str = FULL_TEXT,
                        ) -> tuple[str, str, str]:
    """(verdict, rationale, qualifier)."""
    verdict, rationale = _assign_verdict_core(
        counts, corpus_adequacy, coverage, retrieval_validated, recall)
    return _demote_for_evidence_level(verdict, rationale, evidence_level_mix)


def _assign_verdict_core(counts: ThesisCounts, corpus_adequacy: str,
                         coverage: dict | None = None,
                         retrieval_validated: bool = True,
                         recall: ControlRecall | None = None) -> tuple[str, str]:
    """Return (verdict, rationale). Deterministic — no model involved.

    Precedence, and the reasoning behind the order:

      1. No positive control passed → RETRIEVAL_UNVALIDATED. A null result from a
         search nobody has shown to work is not a finding about the literature.
      2. Coverage absent → RETRIEVAL_UNVALIDATED, naming the shortfall. Same
         reasoning: we could not see the literature.
      3. Support and contradiction both present → CONTESTED.
      4. >=3 supports including a direct same-system result → KNOWN.
      5. Any support → SUPPORTED_BUT_SPARSE.
      6. >=2 analogues, no support → ANALOGUE_ONLY.
      7. >=2 method-relevant, no support → METHOD_ONLY.
      8. Enough related papers, nothing bearing → CANDIDATE_GAP.
      9. Too little of anything → NOT_FOUND.

    Only CANDIDATE_GAP is eligible to become a novelty claim, and even then only
    after a human has read the closest precedents. A gap is not a contribution.
    """
    coverage = coverage or {}

    if not retrieval_validated:
        return RETRIEVAL_UNVALIDATED, (
            "No hold-out positive control passed for this thesis, so a null "
            "result cannot be distinguished from a retrieval failure. Supply a "
            "verified positive control before reading anything into this row."
        )

    if corpus_adequacy == ABSENT:
        return RETRIEVAL_UNVALIDATED, NOT_ESTABLISHED.format(
            worldwide=coverage.get("openalex_worldwide_hits", 0),
            in_corpus=coverage.get("openalex_hits_in_corpus", 0),
            ratio=float(coverage.get("coverage_ratio", 0.0) or 0.0),
        )

    if counts.n_contradicting_papers >= 1 and (
            counts.n_supporting_papers >= 1 or counts.n_mixed_papers >= 1):
        return CONTESTED, (
            f"{counts.n_supporting_papers} supporting and "
            f"{counts.n_contradicting_papers} contradicting paper(s)"
            + (f", plus {counts.n_mixed_papers} reporting both"
               if counts.n_mixed_papers else "")
            + " — read them before making any claim here."
        )

    if counts.n_contradicting_papers >= 1:
        return CONTESTED, (
            f"{counts.n_contradicting_papers} paper(s) contradict this thesis and "
            f"none supports it — the thesis may be wrong as stated."
        )

    if counts.n_supporting_papers >= 3 and counts.n_direct_support_papers >= 1:
        return KNOWN, (
            f"{counts.n_supporting_papers} supporting papers "
            f"({counts.n_supporting_passages} passages), "
            f"{counts.n_direct_support_papers} of them reporting their own result "
            f"in a directly comparable system."
        )

    if counts.n_supporting_papers >= 1:
        return SUPPORTED_BUT_SPARSE, (
            f"{counts.n_supporting_papers} supporting paper(s), "
            f"{counts.n_direct_support_papers} of them a same-system own result — "
            f"real support, too thin to call settled."
        )

    if counts.n_analogue_papers >= 2:
        return ANALOGUE_ONLY, (
            f"{counts.n_analogue_papers} analogue(s) establish the mechanism in "
            f"other systems; no paper tests it in this one."
        )

    if counts.n_method_relevant_papers >= 2:
        return METHOD_ONLY, (
            f"{counts.n_method_relevant_papers} paper(s) supply methods this "
            f"thesis depends on, but none tests the hypothesis."
        )

    # From here down every remaining verdict is a *null* result, and a null
    # result is only interpretable if the search was shown to find what it should.
    # A missed verified-direct control forbids exactly these, and nothing above:
    # papers that WERE found are still found, whatever else the search missed.
    if recall is not None and not recall.direct_complete:
        missed = recall.n_direct_verified - recall.n_direct_recovered
        return RETRIEVAL_INCOMPLETE, (
            f"Retrieval missed {missed} of {recall.n_direct_verified} verified "
            f"direct positive control(s) for this thesis. A search that cannot "
            f"find a paper known to be directly relevant cannot be cited as "
            f"evidence that no such paper exists. No gap may be claimed here "
            f"until the control is recovered."
        )

    if counts.papers_found >= 5:
        return CANDIDATE_GAP, (
            f"Retrieval validated"
            + (f" (controls {recall.n_direct_recovered}/{recall.n_direct_verified} "
               f"direct, holdout {recall.n_holdout_recovered}/{recall.n_holdout})"
               if recall and recall.n_direct_verified else "")
            + f"; {counts.papers_found} related papers examined and none bears on "
            f"the thesis either way. Eligible for a novelty claim after the "
            f"closest precedents have been read."
        )

    return NOT_FOUND, (
        f"Only {counts.papers_found} related paper(s) found — too little evidence "
        f"to place this thesis; corpus coverage is '{corpus_adequacy}'."
    )


def _demote_for_evidence_level(verdict: str, rationale: str,
                               evidence_level_mix: str) -> tuple[str, str, str]:
    """Apply the abstract-only demotion. Returns (verdict, rationale, qualifier).

    A qualifier column rather than new verdict values: adding an ABSTRACT_KNOWN
    and an ABSTRACT_CANDIDATE_GAP would double a nine-value scale and break
    VERDICT_MEANING, NOVELTY_ELIGIBLE, render_md and every test that reads them.
    """
    if evidence_level_mix != ABSTRACT_ONLY:
        return verdict, rationale, ""
    demoted = _ABSTRACT_DEMOTION.get(verdict)
    if demoted is None:
        return verdict, rationale, "ABSTRACT_ONLY"
    return demoted, (
        f"{rationale} Demoted from {verdict}: every piece of evidence for this "
        f"thesis was adjudicated from an abstract, which cannot establish how a "
        f"finding was obtained, nor that no paper tests the thesis."
    ), "ABSTRACT_ONLY"


def pick_closest_study(rows: pd.DataFrame) -> dict:
    """The single most informative existing study for this thesis.

    A contradiction outranks a support: the paper most likely to undermine a
    novelty claim is the one the author most needs to read.
    """
    blank = {
        "closest_existing_study_doi": "— (none found)",
        "closest_existing_study_title": "— (none found)",
        "closest_existing_study_year": None,
        "closest_existing_study_relation": "",
        "closest_existing_study_quote": "",
        "closest_existing_study_score": 0.0,
    }
    if rows.empty:
        return blank

    weight = {"CONTRADICTS": 4.0, "SUPPORTS": 3.0, "METHOD_RELEVANT": 2.0, "ANALOGUE": 1.0}
    kept = rows[rows["relation"].isin(RELATIONS)].copy()
    if kept.empty:
        return blank

    verified = kept["quote_verified"].fillna(False).astype(bool)
    kept["_score"] = (
        kept["relation"].map(weight).fillna(0.0)
        + kept["confidence"].fillna(0.0).astype(float)
        + verified.astype(float)
    )
    best = kept.nlargest(1, "_score").iloc[0]
    return {
        "closest_existing_study_doi": best.get("doi") or "— (no DOI)",
        "closest_existing_study_title": best.get("title") or "— (no title)",
        "closest_existing_study_year": best.get("year"),
        "closest_existing_study_relation": best.get("relation"),
        "closest_existing_study_quote": best.get("evidence_quote") or "",
        "closest_existing_study_score": round(float(best["_score"]), 3),
    }


def build(
    relations: pd.DataFrame,
    coverage: pd.DataFrame | None = None,
    theses: list[Thesis] | None = None,
    prose: dict[str, dict[str, str]] | None = None,
    control_recall: dict[str, ControlRecall] | None = None,
    evidence_levels: dict[str, str] | None = None,
    missing_abstracts: dict[str, float] | None = None,
    kg_expansion_available: bool = True,
    adjudication_tier: str = "gemini",
    run_id: str = "",
) -> pd.DataFrame:
    """Build the 24-row matrix.

    Every thesis gets a row even when nothing was found for it — a thesis that
    silently vanished from the output would read as though it had never been asked.
    """
    theses = theses if theses is not None else load_theses()
    prose = prose or {}
    control_recall = control_recall or {}
    evidence_levels = evidence_levels or {}
    missing_abstracts = missing_abstracts or {}
    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    cov_by_thesis: dict[str, dict] = {}
    if coverage is not None and not coverage.empty:
        cov_by_thesis = {r["thesis_id"]: r for r in coverage.to_dict("records")}

    for col in RELATION_COLUMNS:
        if col not in relations.columns:
            relations = relations.assign(**{col: None})

    rows = []
    for t in theses:
        subset = relations[relations["thesis_id"] == t.id]
        counts = count_relations(subset)
        cov = cov_by_thesis.get(t.id, {})
        adequacy = cov.get("corpus_adequacy", THIN)
        recall = control_recall.get(t.id) or ControlRecall()
        # A thesis with no recovered control has an uninterpretable null result.
        validated = recall.validated
        level_mix = evidence_levels.get(t.id, FULL_TEXT)
        verdict, rationale, qualifier = assign_verdict_full(
            counts, adequacy, cov, retrieval_validated=validated,
            recall=recall, evidence_level_mix=level_mix)
        text = prose.get(t.id, {})

        row = {
            "thesis_id": t.id,
            "block": t.block,
            "block_title": t.block_title,
            "statement": t.statement,
            "papers_found": counts.papers_found,
            "n_supporting_papers": counts.n_supporting_papers,
            "n_contradicting_papers": counts.n_contradicting_papers,
            "n_method_relevant_papers": counts.n_method_relevant_papers,
            "n_analogue_papers": counts.n_analogue_papers,
            "n_direct_support_papers": counts.n_direct_support_papers,
            "n_mixed_papers": counts.n_mixed_papers,
            "n_supporting_passages": counts.n_supporting_passages,
            "n_contradicting_passages": counts.n_contradicting_passages,
            "n_verified_quotes": counts.n_verified_quotes,
            "n_rejected_quotes": counts.n_rejected_quotes,
            "n_model_disagreements": counts.n_model_disagreements,
            "what_is_established": text.get("what_is_established", ""),
            "what_remains_missing": text.get("what_remains_missing", ""),
            "verdict": verdict,
            "verdict_rationale": rationale,
            "corpus_adequacy": adequacy,
            "openalex_worldwide_hits": cov.get("openalex_worldwide_hits", 0),
            "openalex_hits_in_corpus": cov.get("openalex_hits_in_corpus", 0),
            "coverage_ratio": cov.get("coverage_ratio", 0.0),
            "kg_expansion_available": kg_expansion_available,
            "adjudication_tier": adjudication_tier,
            "retrieval_validated": validated,
            "n_controls": recall.n_controls,
            "n_controls_recovered": recall.n_recovered,
            "control_recall": round(recall.overall, 3),
            "holdout_recall": round(recall.holdout, 3),
            "expected_stage_recall": round(recall.expected_stage, 3),
            "semantic_recall": round(recall.semantic, 3),
            "n_direct_controls": recall.n_direct_verified,
            "n_direct_controls_recovered": recall.n_direct_recovered,
            "novelty_eligible": verdict in NOVELTY_ELIGIBLE,
            "evidence_level_mix": level_mix,
            "verdict_qualifier": qualifier,
            "n_papers_without_abstract": round(
                float(missing_abstracts.get(t.id, 0.0)), 4),
            "n_candidates_prefiltered_out": counts.n_prefiltered_out,
            "manuscript_anchor": t.manuscript_anchor,
            "numeric_anchor": t.numeric_anchor,
            "run_id": run_id,
            "generated_at": generated_at,
        }
        row.update(pick_closest_study(subset))
        rows.append(row)

    return pd.DataFrame(rows, columns=MATRIX_COLUMNS)


# ── rendering ─────────────────────────────────────────────────────────────────

#: Verdicts under which "still missing" would be a claim about the literature
#: that the row cannot support: retrieval either was never shown to work or is
#: known to have missed a verified control.
UNASSESSABLE_VERDICTS = frozenset({RETRIEVAL_UNVALIDATED, RETRIEVAL_INCOMPLETE})


def _gap_prose_assessable(verdict: str, publication_eligible: bool) -> bool:
    """May a row's prose be labelled *Established* / *Still missing*?

    Only when the matrix as a whole is publication-eligible AND this row's
    verdict rests on validated retrieval. An exploratory matrix withholds gap
    language on every row, whatever the individual verdicts say.
    """
    return bool(publication_eligible) and verdict not in UNASSESSABLE_VERDICTS


def render_md(matrix: pd.DataFrame, thresholds_note: str = "",
              publication_eligible: bool = True) -> str:
    """Render the matrix as the reviewable deliverable."""
    if matrix.empty:
        return "# Gap Evidence Matrix\n\nNo theses were evaluated.\n"

    head = matrix.iloc[0]
    lines = [
        "# Gap Evidence Matrix — Paper 3",
        "",
        f"Generated {head['generated_at']} · run `{head['run_id'] or 'unset'}` · "
        f"adjudication: {head['adjudication_tier']}",
        "",
        "Verdicts are assigned by fixed rules over verified evidence, not by a "
        "language model. `corpus_adequacy` states whether this corpus was in a "
        "position to answer the thesis at all: a thesis marked **absent** is "
        "reported as UNKNOWN because we could not see the literature, which is "
        "not the same as the literature being silent.",
        "",
    ]
    if not publication_eligible:
        levels = set(matrix.get("evidence_level_mix", []))
        lines += [
            "> **EXPLORATORY — NOT A FINISHED GAP ANALYSIS.** This matrix was "
            "produced before the acceptance gate passed"
            + (", from abstract-level evidence only"
               if levels == {ABSTRACT_ONLY} else "")
            + ". No thesis here is eligible to become a novelty claim, and no "
            "row may be cited as evidence that a gap exists in the literature.",
            "",
        ]
    if not bool(head["kg_expansion_available"]):
        lines += [
            "> **Neo4j was unavailable for this run.** Knowledge-graph expansion "
            "contributed no candidates, so counts below are a lower bound.",
            "",
        ]
    if thresholds_note:
        lines += [thresholds_note, ""]

    lines += [
        "Counts below are **papers**, not passages. S / C / M / A are supporting, "
        "contradicting, method-relevant and analogue papers; the passage counts "
        "sit in the per-thesis sections as provenance and never enter a verdict.",
        "",
        "`Controls` is direct-control recall / holdout recall — the answer to "
        "*how do you know nothing was missed?* A CANDIDATE_GAP with 0/n direct "
        "controls recovered is impossible by construction.",
        "",
        "| Thesis | Verdict | Coverage | Controls | Papers | S | C | M | A | Closest existing study |",
        "|---|---|---|---|---:|---:|---:|---:|---:|---|",
    ]
    for r in matrix.itertuples():
        doi = r.closest_existing_study_doi
        closest = (f"[{doi}](https://doi.org/{doi})"
                   if isinstance(doi, str) and doi.startswith("10.") else doi)
        lines.append(
            f"| {r.thesis_id} | {r.verdict.replace('_', ' ')} | {r.corpus_adequacy} | "
            f"{r.n_direct_controls_recovered}/{r.n_direct_controls}"
            f" · {int(r.holdout_recall * 100)}% | "
            f"{r.papers_found} | {r.n_supporting_papers} | {r.n_contradicting_papers} | "
            f"{r.n_method_relevant_papers} | {r.n_analogue_papers} | {closest} |"
        )

    def _opt(value) -> str:
        """Render an optional cell. Parquet turns None into NaN, and a literal
        'nan' in the deliverable reads as a value rather than an absence."""
        if value is None:
            return ""
        try:
            if pd.isna(value):
                return ""
        except (TypeError, ValueError):
            pass
        if isinstance(value, float) and value.is_integer():
            return str(int(value))
        return str(value)

    lines += ["", "---", ""]
    for block in sorted(matrix["block"].unique()):
        rows = matrix[matrix["block"] == block]
        lines += [f"## Block {block} — {rows.iloc[0]['block_title']}", ""]
        for r in rows.itertuples():
            lines += [
                f"### {r.thesis_id} — {r.verdict.replace('_', ' ')}",
                "",
                f"> {r.statement}",
                "",
                f"- **Manuscript anchor**: {r.manuscript_anchor}"
                + (f" · number under test: `{_opt(r.numeric_anchor)}`"
                   if _opt(r.numeric_anchor) else ""),
                f"- **Evidence (papers)**: {r.papers_found} — "
                f"{r.n_supporting_papers} supporting "
                f"({r.n_direct_support_papers} same-system own results), "
                f"{r.n_contradicting_papers} contradicting, "
                f"{r.n_method_relevant_papers} method-relevant, "
                f"{r.n_analogue_papers} analogues",
                f"- **Evidence (passages)**: {r.n_supporting_passages} supporting, "
                f"{r.n_contradicting_passages} contradicting, "
                f"{r.n_verified_quotes} verified, {r.n_rejected_quotes} papers lost "
                f"to unverifiable quotes, "
                f"{r.n_candidates_prefiltered_out} filtered out before adjudication"
                + (f", {r.n_model_disagreements} paper(s) with disagreeing "
                   f"adjudications" if r.n_model_disagreements else ""),
                f"- **Corpus coverage**: {r.corpus_adequacy} — "
                f"{r.openalex_hits_in_corpus} of {r.openalex_worldwide_hits} "
                f"candidate works present (ratio {float(r.coverage_ratio or 0):.2f})",
                f"- **Retrieval validation**: "
                f"{r.n_direct_controls_recovered}/{r.n_direct_controls} verified "
                f"direct controls recovered · overall {float(r.control_recall or 0):.0%} "
                f"· at expected stage {float(r.expected_stage_recall or 0):.0%} "
                f"· semantic alone {float(r.semantic_recall or 0):.0%} "
                f"· holdout {float(r.holdout_recall or 0):.0%}",
                f"- **Verdict rationale**: {r.verdict_rationale}",
            ]
            # The model's prose is written before the verdict is known, so it
            # says "established" and "still missing" regardless. Those words
            # are gap language, and a row whose verdict forbids a gap reading
            # must not carry them: the banner at the top is not enough when
            # the sentence a reviewer quotes sits 40 lines below it. The text
            # stays in the parquet as provenance; only the label changes.
            assessable = _gap_prose_assessable(r.verdict, publication_eligible)
            if r.what_is_established:
                label = ("Established" if assessable
                         else "Observed in exploratory retrieval")
                lines.append(f"- **{label}**: {r.what_is_established}")
            if assessable and r.what_remains_missing:
                lines.append(f"- **Still missing**: {r.what_remains_missing}")
            elif not assessable:
                lines.append(
                    "- **Gap status**: NOT ASSESSABLE — retrieval unvalidated; "
                    "the model's gap prose is withheld until a positive control "
                    "passes for this thesis")
            if isinstance(r.closest_existing_study_doi, str) and \
                    r.closest_existing_study_doi.startswith("10."):
                lines += [
                    f"- **Closest study** ({r.closest_existing_study_relation}): "
                    f"{r.closest_existing_study_title} "
                    f"([{r.closest_existing_study_doi}]"
                    f"(https://doi.org/{r.closest_existing_study_doi}), "
                    f"{_opt(r.closest_existing_study_year) or '—'})",
                ]
                if r.closest_existing_study_quote:
                    lines.append(f"  > {r.closest_existing_study_quote}")
            else:
                lines.append("- **Closest study**: — (none found in corpus)")
            lines.append("")
    return "\n".join(lines) + "\n"


#: A matrix produced before the acceptance gate passes, or from abstracts only,
#: is never written under the canonical name. Downstream code asks for
#: GAP_MATRIX.parquet and gets a FileNotFoundError rather than an exploratory
#: table it might mistake for a finished gap analysis.
CANONICAL_MATRIX = "gap_matrix.parquet"
EXPLORATORY_MATRIX = "GAP_MATRIX_EXPLORATORY_UNVALIDATED.parquet"


def matrix_filename(publication_eligible: bool) -> str:
    return CANONICAL_MATRIX if publication_eligible else EXPLORATORY_MATRIX


def run(out_dir: Path | None = None, publication_eligible: bool | None = None,
        **kwargs) -> pd.DataFrame:
    """Read relations + coverage from disk, write the matrix and its deliverables.

    `publication_eligible` decides the output filename. Left as None it is
    derived: a matrix is publication-eligible only when at least one thesis is
    novelty-eligible and no thesis rests on abstract-only evidence.
    """
    target = Path(out_dir) if out_dir else OUT_DIR
    relations_path = target / "relations.parquet"
    if not relations_path.exists():
        raise FileNotFoundError(
            f"{relations_path} missing — run --step finalize first")
    relations = pd.read_parquet(relations_path)

    coverage_path = target / "corpus_coverage.parquet"
    coverage = pd.read_parquet(coverage_path) if coverage_path.exists() else None
    if coverage is None:
        logger.warning("corpus_coverage.parquet missing — every thesis will be "
                       "treated as 'thin' and no absent-coverage disclosure made")

    matrix = build(relations, coverage, **kwargs)

    if publication_eligible is None:
        publication_eligible = bool(
            matrix["novelty_eligible"].any()
            and not (matrix["evidence_level_mix"] == ABSTRACT_ONLY).any())

    name = matrix_filename(publication_eligible)
    matrix.to_parquet(target / name, index=False)
    stem = "GAP_MATRIX" if publication_eligible else "GAP_MATRIX_EXPLORATORY_UNVALIDATED"
    matrix.to_csv(target / f"{stem}.csv", index=False)
    (target / f"{stem}.md").write_text(
        render_md(matrix, publication_eligible=publication_eligible),
        encoding="utf-8")

    logger.info("Gap matrix → %s", name)
    logger.info("  verdicts: %s", matrix["verdict"].value_counts().to_dict())
    if not publication_eligible:
        logger.warning("  NOT publication-eligible: written under the exploratory "
                       "name so it cannot be mistaken for a finished gap analysis")
    return matrix
