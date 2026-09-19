"""Calibration, provenance and the frozen manifest — the five pre-harvest gates.

These cover the recall half of calibration (`positive_control`), the precision
half (`calibrate`), the retrieval-origin vocabulary, the one-hop citation limit,
and the freeze that makes a run answerable a year later.
"""
from __future__ import annotations

import json

import pandas as pd
import pytest

from src.paper_3 import calibrate, freeze
from src.paper_3.corpus_index import expand_by_citation
from src.paper_3.retrieve import (
    CITATION_DEPTH,
    RETRIEVAL_ORIGINS,
    positive_control,
    resolve_seeds,
)
from src.paper_3.theses import get_thesis, load_theses


@pytest.fixture(autouse=True)
def _no_live_chroma(monkeypatch):
    """Never let a unit test open the real vector store.

    `freeze.build()` records ChromaDB's collection size, and doing that for
    real means loading a 1.3-million-chunk HNSW index. On a cold cache after
    heavy disk I/O that took 1 770 s in one test run and pushed the whole suite
    past its timeout twice. The manifest's *shape* is what these tests check;
    the live count belongs to an integration check, not here.
    """
    monkeypatch.setattr(freeze, "_chroma_state", lambda: {
        "collection": "stub_collection", "persist_dir": "",
        "embedding_model": "stub", "n_chunks": 0,
    })


@pytest.fixture(scope="module")
def theses():
    return load_theses()


# ── retrieval provenance ──────────────────────────────────────────────────────

def test_citation_depth_is_one_and_not_a_parameter():
    """Two hops through a highly cited method paper reaches most of the corpus."""
    assert CITATION_DEPTH == 1


def test_retrieval_origin_vocabulary_is_closed():
    assert set(RETRIEVAL_ORIGINS) == {
        "semantic", "references", "cited_by", "graph_neighbor"}


def test_there_is_no_seed_origin():
    """A control that entered because we named it proves nothing about retrieval."""
    assert "seed" not in RETRIEVAL_ORIGINS


def _index(rows):
    return pd.DataFrame(rows, columns=["paper_id", "doi", "title"])


def _edges(rows):
    return pd.DataFrame(rows, columns=["source_paper_id", "referenced_doi"])


def test_backward_expansion_is_labelled_references():
    """A paper the seed cites is in the seed's reference list — ancestry."""
    index = _index([("seed", "10.1/seed", "S"), ("old", "10.1/old", "O")])
    edges = _edges([("seed", "10.1/old")])
    out = expand_by_citation(["seed"], index, edges)
    assert out.loc[out["paper_id"] == "old", "relation"].item() == "references"


def test_forward_expansion_is_labelled_cited_by():
    """A paper that cites the seed is descent."""
    index = _index([("seed", "10.1/seed", "S"), ("new", "10.1/new", "N")])
    edges = _edges([("new", "10.1/seed")])
    out = expand_by_citation(["seed"], index, edges)
    assert out.loc[out["paper_id"] == "new", "relation"].item() == "cited_by"


def test_expansion_never_returns_the_seed_itself():
    index = _index([("seed", "10.1/seed", "S"), ("old", "10.1/old", "O")])
    edges = _edges([("seed", "10.1/old"), ("seed", "10.1/seed")])
    out = expand_by_citation(["seed"], index, edges)
    assert "seed" not in set(out["paper_id"])


def test_expansion_does_not_reach_two_hops():
    """A cites B, B cites C. From A, only B — never C."""
    index = _index([("a", "10.1/a", "A"), ("b", "10.1/b", "B"), ("c", "10.1/c", "C")])
    edges = _edges([("a", "10.1/b"), ("b", "10.1/c")])
    out = expand_by_citation(["a"], index, edges)
    assert set(out["paper_id"]) == {"b"}, "depth must stop at one hop"


def test_expansion_counts_links_per_paper():
    index = _index([("s1", "10.1/s1", ""), ("s2", "10.1/s2", ""),
                    ("t", "10.1/t", "")])
    edges = _edges([("s1", "10.1/t"), ("s2", "10.1/t")])
    out = expand_by_citation(["s1", "s2"], index, edges)
    assert out.loc[out["paper_id"] == "t", "n_seed_links"].item() == 2


# ── positive control (recall) ─────────────────────────────────────────────────

def test_resolve_seeds_finds_and_reports_missing(theses):
    index = _index([("p_huang", "10.1029/2025gl119771", "Uneven lake WSE")])
    found, missing = resolve_seeds(theses, index)
    assert "p_huang" in found["T07"]
    assert "10.1029/2025gl120832" in missing["T07"], "the absent seed must be named"


def test_resolve_seeds_on_an_empty_corpus(theses):
    found, missing = resolve_seeds(theses, _index([]))
    assert all(not v for v in found.values())
    assert "10.1029/2025gl119771" in missing["T04"]


def test_positive_control_flags_a_seed_not_in_the_corpus(theses):
    control = positive_control(theses, _index([]),
                               pd.DataFrame(columns=["thesis_id", "paper_id",
                                                     "prefilter_pass"]))
    assert not control.empty
    assert set(control["status"]) == {"not in corpus"}
    assert not control["in_corpus"].any()


def test_positive_control_distinguishes_its_failure_modes(theses, monkeypatch):
    """Each failure has a different fix, so they must not read the same."""
    index = _index([("p1", "10.1029/2025gl119771", "T")])
    candidates = pd.DataFrame(columns=["thesis_id", "paper_id", "prefilter_pass"])

    import src.paper_3.retrieve as retrieve_mod

    # Primary family absent → the family is worded too narrowly.
    monkeypatch.setattr(retrieve_mod, "load_paper_json",
                        lambda pid: {"sections": {"body": "unrelated text"}})
    control = positive_control(theses, index, candidates)
    row = control[control["thesis_id"] == "T07"].iloc[0]
    assert "primary family too narrow" in row["status"]

    # On topic but retrieval never returned it.
    monkeypatch.setattr(
        retrieve_mod, "load_paper_json",
        lambda pid: {"sections": {"body": "SWOT KaRIn LakeSP water surface elevation"}})
    control = positive_control(theses, index, candidates)
    row = control[control["thesis_id"] == "T07"].iloc[0]
    assert "retrieval missed it" in row["status"]

    # Retrieved but prefiltered out.
    got = pd.DataFrame([{"thesis_id": "T07", "paper_id": "p1", "prefilter_pass": False}])
    control = positive_control(theses, index, got)
    row = control[control["thesis_id"] == "T07"].iloc[0]
    assert "prefiltered out" in row["status"]

    # All the way through.
    got = pd.DataFrame([{"thesis_id": "T07", "paper_id": "p1", "prefilter_pass": True}])
    control = positive_control(theses, index, got)
    row = control[control["thesis_id"] == "T07"].iloc[0]
    assert row["status"] == "ok"


# ── every thesis can match its own statement (self-consistency) ───────────────

def test_each_thesis_is_on_topic_for_its_own_statement(theses):
    """A thesis whose key terms miss its own wording can never match a paper.

    This is the cheapest recall check available before the harvest: if the
    mandatory primary family does not fire on the statement plus rationale the
    thesis was written from, it has been cut too narrow.
    """
    failures = []
    for t in theses:
        text = f"{t.statement} {t.rationale} {' '.join(t.search_queries)}"
        if not t.is_on_topic(text):
            failures.append(
                f"{t.id}: primary_hit={t.primary_hit(text)} "
                f"families={t.families_hit(text)}")
    assert not failures, "theses that cannot match their own text: " + "; ".join(failures)


# ── calibration sampling (precision) ──────────────────────────────────────────

def _candidates(thesis_id="T20", n=40):
    return pd.DataFrame([{
        "thesis_id": thesis_id, "paper_id": f"p{i}", "doi": f"10.1/{i}",
        "title": f"Paper {i}", "year": 2020,
        "semantic_best_distance": 0.10 + i * 0.01,
        "n_semantic_chunks": 5, "n_keyterm_families_hit": 2,
        "retrieval_origin": "semantic", "prefilter_pass": True,
    } for i in range(n)])


def test_sample_covers_three_strata(theses):
    sample = calibrate.stratified_sample(_candidates(), get_thesis("T20", theses),
                                         per_stratum=5)
    assert set(sample["stratum"]) >= {"top", "middle"}
    assert len(sample) <= 15


def test_sample_ranks_by_distance(theses):
    sample = calibrate.stratified_sample(_candidates(), get_thesis("T20", theses),
                                         per_stratum=5)
    top = sample[sample["stratum"] == "top"]
    assert top["semantic_best_distance"].max() <= sample["semantic_best_distance"].max()


def test_sample_includes_the_band_the_gate_decides(theses):
    """Sampling only the best hits would measure nothing the gate controls."""
    sample = calibrate.stratified_sample(_candidates(n=60),
                                         get_thesis("T20", theses), per_stratum=5)
    near = sample[sample["stratum"] == "near_threshold"]
    assert not near.empty
    assert near["semantic_best_distance"].max() <= calibrate.DISTANCE_GATE


def test_sample_of_an_empty_candidate_set(theses):
    empty = pd.DataFrame(columns=["thesis_id", "semantic_best_distance"])
    assert calibrate.stratified_sample(empty, get_thesis("T20", theses)).empty


def test_build_sheet_writes_labelling_columns(tmp_path, theses):
    _candidates().to_parquet(tmp_path / "thesis_candidates.parquet")
    path = calibrate.build_sheet(tmp_path, theses=theses, per_stratum=5,
                                 only=("T20",))
    sheet = pd.read_csv(path)
    assert list(sheet.columns) == calibrate.SHEET_COLUMNS
    assert sheet["label"].isna().all() or (sheet["label"] == "").all()
    assert sheet["thesis_statement"].notna().all()


def test_build_sheet_without_candidates_is_a_named_error(tmp_path):
    with pytest.raises(FileNotFoundError, match="retrieve"):
        calibrate.build_sheet(tmp_path)


# ── calibration report ────────────────────────────────────────────────────────

def _labelled(labels_by_distance):
    return pd.DataFrame([{
        "thesis_id": "T20", "stratum": "top", "rank": i,
        "semantic_best_distance": d, "label": lab, "note": "",
    } for i, (d, lab) in enumerate(labels_by_distance)])


def test_precision_curve_falls_with_distance():
    sheet = _labelled(
        [(0.10, "relevant")] * 10 + [(0.40, "irrelevant")] * 10)
    curve = calibrate.precision_curve(sheet)
    near = curve[curve["bin_low"] <= 0.15]["precision"].max()
    far = curve[curve["bin_low"] >= 0.35]["precision"].max()
    assert near > far


def test_precision_curve_separates_lenient_and_strict():
    sheet = _labelled([(0.10, "partial")] * 4)
    row = calibrate.precision_curve(sheet).iloc[0]
    assert row["precision"] == 1.0
    assert row["precision_strict"] == 0.0


def test_unlabelled_rows_are_ignored():
    sheet = _labelled([(0.10, "relevant"), (0.10, ""), (0.10, "nonsense")])
    assert calibrate.precision_curve(sheet).iloc[0]["n"] == 1


def test_suggest_gate_returns_the_last_acceptable_bin():
    sheet = _labelled([(0.10, "relevant")] * 10 + [(0.40, "irrelevant")] * 10)
    gate = calibrate.suggest_gate(calibrate.precision_curve(sheet), 0.6)
    assert gate is not None and gate < 0.40


def test_suggest_gate_is_none_when_nothing_qualifies():
    sheet = _labelled([(0.10, "irrelevant")] * 10)
    assert calibrate.suggest_gate(calibrate.precision_curve(sheet), 0.6) is None


def test_report_warns_when_too_few_rows_are_labelled(tmp_path):
    _labelled([(0.10, "relevant")] * 3).to_csv(tmp_path / calibrate.SHEET, index=False)
    text = calibrate.report(tmp_path).read_text(encoding="utf-8")
    assert "Not enough labelled rows" in text


def test_report_flags_a_thesis_whose_best_hits_are_weak(tmp_path):
    """Low precision at the top is a key-terms problem, not a gate problem."""
    sheet = _labelled([(0.10, "irrelevant")] * 9 + [(0.10, "relevant")])
    sheet.to_csv(tmp_path / calibrate.SHEET, index=False)
    text = calibrate.report(tmp_path).read_text(encoding="utf-8")
    assert "best hits are already weak" in text
    assert "T20" in text


# ── the freeze ────────────────────────────────────────────────────────────────

def test_freeze_records_what_determines_the_result(tmp_path):
    payload = freeze.build(tmp_path, tag="test")
    for key in ("git_commit", "retrieval_rules_version", "corpus", "theses",
                "chroma", "retrieval_rules", "evidence_rules",
                "coverage_thresholds"):
        assert key in payload
    from src.paper_3.theses import expected_thesis_ids
    assert payload["theses"]["n_theses"] == len(expected_thesis_ids())
    assert payload["theses"]["sha256"], "theses.yaml must be hashed"
    assert payload["retrieval_rules"]["citation_depth"] == CITATION_DEPTH
    assert payload["evidence_rules"]["unit_of_evidence"] == "paper"


def test_freeze_writes_and_keeps_history(tmp_path):
    freeze.run(tmp_path, tag="before")
    freeze.run(tmp_path, tag="after")
    payload = json.loads((tmp_path / freeze.FREEZE_FILE).read_text(encoding="utf-8"))
    assert payload["tag"] == "after"
    assert len(payload["history"]) == 1
    assert payload["history"][0]["tag"] == "before"


def test_freeze_survives_a_corrupt_previous_manifest(tmp_path):
    (tmp_path / freeze.FREEZE_FILE).write_text("{not json", encoding="utf-8")
    path = freeze.run(tmp_path, tag="recovered")
    assert json.loads(path.read_text(encoding="utf-8"))["tag"] == "recovered"


def test_freeze_markdown_answers_the_reviewer_question(tmp_path):
    freeze.run(tmp_path, tag="t")
    text = freeze.render_md(tmp_path).read_text(encoding="utf-8")
    assert "which corpus produced these numbers" in text
    assert "theses.yaml sha256" in text
    assert "citation depth" in text
    assert "unit of evidence" in text


def test_freeze_markdown_without_a_freeze_is_a_named_error(tmp_path):
    with pytest.raises(FileNotFoundError, match="freeze"):
        freeze.render_md(tmp_path)


def test_rules_version_has_a_changelog_entry():
    assert freeze.RETRIEVAL_RULES_VERSION in freeze.RULES_CHANGELOG


# ── how many theses can be positive-controlled at all ─────────────────────────

#: Theses with no seed DOI yet. Their UNKNOWN cannot be distinguished from a
#: retrieval failure, so the list is written down rather than left implicit.
#: Shrink it as seeds are found; never grow it to make a test pass.
#: T08 and T10 left this set on 2026-09-17 (Trevoho 2021, unread candidate).
THESES_WITHOUT_CONTROLS = {
    "T09", "T11", "T12", "T13", "T14", "T15", "T16",
    "T17", "T18", "T20", "T21", "T23", "T24",
}


def test_the_uncontrolled_theses_are_exactly_the_ones_we_know_about(theses):
    """A thesis silently losing its control would silently lose its recall check."""
    actual = {t.id for t in theses if not t.positive_controls}
    assert actual == THESES_WITHOUT_CONTROLS, (
        f"control coverage changed — gained: "
        f"{sorted(THESES_WITHOUT_CONTROLS - actual)}, "
        f"lost: {sorted(actual - THESES_WITHOUT_CONTROLS)}")


def test_block_b_is_fully_controlled(theses):
    """Block B is the method this manuscript uses; it must be checkable."""
    for t in theses:
        if t.block == "B":
            assert t.positive_controls, f"{t.id} (block B) has no positive control"


def test_controlled_theses_count(theses):
    controlled = [t for t in theses if t.positive_controls]
    assert len(controlled) >= 8, f"only {len(controlled)} theses have a control"


def test_no_control_is_verified_without_evidence_of_checking(theses):
    """`manually_checked: true` with no quote is a claim, not a check."""
    for t in theses:
        for c in t.positive_controls:
            if c.manually_checked:
                assert c.quote, f"{t.id}/{c.doi} marked checked but has no quote"


# ── the acceptance gate ───────────────────────────────────────────────────────

from src.paper_3 import acceptance  # noqa: E402


def _control_csv(rows):
    return pd.DataFrame(rows, columns=[
        "thesis_id", "is_critical", "seed_doi", "paper_id", "in_corpus",
        "recovered", "failure_stage"])


def test_gate_fails_when_theses_lack_controls(tmp_path, theses):
    criteria, ready = acceptance.check(tmp_path, theses)
    assert not ready
    names = {c.name: c for c in criteria}
    assert not names["every thesis has a positive control"].passed
    assert "missing" in names["every thesis has a positive control"].detail


def test_gate_fails_when_controls_are_unverified(tmp_path, theses):
    """A control nobody has read cannot validate anything."""
    criteria, _ = acceptance.check(tmp_path, theses)
    verified = next(c for c in criteria
                    if c.name == "every control has been read and verified")
    assert not verified.passed


def test_gate_reports_recall_failure_stages(tmp_path, theses):
    _control_csv([
        ("T01", False, "10.1/a", "p1", True, False, "semantic_retrieval"),
        ("T02", False, "10.1/b", "p2", True, True, ""),
    ]).to_csv(tmp_path / "POSITIVE_CONTROL.csv", index=False)
    criteria, _ = acceptance.check(tmp_path, theses)
    recall = next(c for c in criteria if c.name.startswith("overall control recall"))
    assert not recall.passed
    assert "semantic_retrieval" in recall.detail


def test_gate_passes_recall_when_high_enough(tmp_path, theses):
    rows = [("T01", False, f"10.1/{i}", f"p{i}", True, True, "") for i in range(10)]
    _control_csv(rows).to_csv(tmp_path / "POSITIVE_CONTROL.csv", index=False)
    criteria, _ = acceptance.check(tmp_path, theses)
    recall = next(c for c in criteria if c.name.startswith("overall control recall"))
    assert recall.passed


def test_gate_blocks_on_a_critical_thesis_with_zero_recall(tmp_path, theses):
    rows = [("T01", False, f"10.1/{i}", f"p{i}", True, True, "") for i in range(20)]
    rows.append(("T09", True, "10.1/crit", "pc", True, False, "semantic_retrieval"))
    _control_csv(rows).to_csv(tmp_path / "POSITIVE_CONTROL.csv", index=False)
    criteria, _ = acceptance.check(tmp_path, theses)
    critical = next(c for c in criteria if c.name == "no critical thesis has zero recall")
    assert not critical.passed
    assert "T09" in critical.detail


def test_gate_requires_a_calibration_sheet(tmp_path, theses):
    criteria, _ = acceptance.check(tmp_path, theses)
    cal = next(c for c in criteria if c.name == "retrieval precision calibrated")
    assert not cal.passed
    assert "calibrate sample" in cal.detail


def test_gate_requires_enough_labelled_rows(tmp_path, theses):
    pd.DataFrame([{"label": "relevant"}] * 5).to_csv(
        tmp_path / "CALIBRATION_SHEET.csv", index=False)
    criteria, _ = acceptance.check(tmp_path, theses)
    cal = next(c for c in criteria if c.name == "retrieval precision calibrated")
    assert not cal.passed
    assert "5/5" in cal.detail


def test_gate_requires_a_freeze(tmp_path, theses):
    criteria, _ = acceptance.check(tmp_path, theses)
    frozen = next(c for c in criteria if c.name == "retrieval manifest frozen")
    assert not frozen.passed


def test_gate_sees_a_freeze_once_written(tmp_path, theses):
    freeze.run(tmp_path, tag="t")
    criteria, _ = acceptance.check(tmp_path, theses)
    frozen = next(c for c in criteria if c.name == "retrieval manifest frozen")
    assert frozen.passed


def test_non_blocking_criteria_do_not_prevent_readiness():
    passing = [acceptance.Criterion("a", True, ""),
               acceptance.Criterion("b", False, "", blocking=False)]
    assert all(c.passed for c in passing if c.blocking)
    assert passing[1].mark == "WARN"


def test_gate_report_explains_why_it_exists(tmp_path, theses):
    criteria, ready = acceptance.check(tmp_path, theses)
    text = acceptance.render_md(criteria, ready)
    assert "NOT READY" in text
    assert "held out" in text
    assert "must not be read as literature gaps" in text


def test_gate_report_says_ready_when_everything_passes():
    text = acceptance.render_md([acceptance.Criterion("x", True, "fine")], True)
    assert "**READY**" in text


def test_gate_run_writes_the_report(tmp_path, theses):
    ready = acceptance.run(tmp_path, theses)
    assert not ready
    assert (tmp_path / "ACCEPTANCE_GATE.md").exists()


def test_critical_theses_are_the_thin_methodological_ones(theses):
    """The blocks where a novelty claim is most likely need the most proof."""
    from src.paper_3.theses import CRITICAL_THESES
    critical = {t.id: t.block for t in theses if t.is_critical}
    assert set(critical) == set(CRITICAL_THESES)
    assert set(critical.values()) <= {"C", "D", "E", "F", "H", "I", "J"}


# ── development vs holdout ────────────────────────────────────────────────────

def test_control_roles_are_closed(theses):
    from src.paper_3.theses import CONTROL_ROLES
    assert set(CONTROL_ROLES) == {"development", "holdout"}
    for t in theses:
        for c in t.positive_controls:
            assert c.role in CONTROL_ROLES


def test_development_and_holdout_partition_the_controls(theses):
    for t in theses:
        assert (len(t.development_controls) + len(t.holdout_controls)
                == len(t.positive_controls))


def test_a_consumed_holdout_must_record_its_rules_version():
    """Otherwise the result cannot be tied to the code that produced it."""
    from src.paper_3.theses import PositiveControl, Thesis, validate_theses

    base = load_theses()
    bad = PositiveControl(doi="10.1/x", role="holdout", manually_checked=True,
                          quote="q", holdout_consumed=True)
    import dataclasses
    patched = [dataclasses.replace(base[0], positive_controls=(bad,))] + base[1:]
    problems = validate_theses(patched)
    assert any("rules version" in p for p in problems)


def test_consumed_cannot_be_set_on_a_development_control():
    from src.paper_3.theses import PositiveControl, validate_theses
    import dataclasses

    base = load_theses()
    bad = PositiveControl(doi="10.1/x", role="development",
                          holdout_consumed=True,
                          consumed_at_rules_version="1.1.0")
    patched = [dataclasses.replace(base[0], positive_controls=(bad,))] + base[1:]
    assert any("not a holdout" in p for p in validate_theses(patched))


def test_gate_requires_a_holdout_for_critical_theses(tmp_path, theses):
    criteria, _ = acceptance.check(tmp_path, theses)
    holdout = next(c for c in criteria
                   if c.name == "critical theses have a holdout control")
    assert not holdout.passed
    assert "T09" in holdout.detail


def test_gate_warns_when_a_holdout_is_already_consumed(tmp_path, theses):
    import dataclasses

    from src.paper_3.theses import PositiveControl

    consumed = PositiveControl(doi="10.1/x", role="holdout", manually_checked=True,
                               quote="q", holdout_consumed=True,
                               consumed_at_rules_version="1.1.0")
    patched = [dataclasses.replace(t, positive_controls=(consumed,))
               if t.id == "T09" else t for t in theses]
    criteria, _ = acceptance.check(tmp_path, patched)
    consumed_criterion = next(c for c in criteria
                              if c.name == "no holdout has been consumed yet")
    assert not consumed_criterion.passed
    assert consumed_criterion.mark == "WARN"


# ── the three recall metrics in the gate ──────────────────────────────────────

def _control_row(thesis="T01", critical=False, direct=True, checked=True,
                 role="development", recovered=True, semantic=True,
                 at_stage=True, stage=""):
    return {
        "thesis_id": thesis, "is_critical": critical, "seed_doi": "10.1/a",
        "paper_id": "p1", "role": role, "relevance": "direct" if direct else "method",
        "is_direct": direct, "manually_checked": checked, "in_corpus": True,
        "recovered": recovered, "recovered_semantically": semantic,
        "recovered_at_expected_stage": at_stage, "failure_stage": stage,
    }


def test_gate_flags_a_missed_verified_direct_control(tmp_path, theses):
    pd.DataFrame([
        _control_row(thesis="T15", direct=True, checked=True, recovered=False,
                     semantic=False, at_stage=False, stage="semantic_retrieval"),
        _control_row(thesis="T01"),
    ]).to_csv(tmp_path / "POSITIVE_CONTROL.csv", index=False)
    criteria, _ = acceptance.check(tmp_path, theses)
    rule = next(c for c in criteria
                if c.name == "every verified direct control recovered")
    assert not rule.passed
    assert "T15" in rule.detail
    assert "CANDIDATE_GAP" in rule.detail


def test_gate_ignores_a_missed_unverified_control(tmp_path, theses):
    pd.DataFrame([_control_row(thesis="T15", checked=False, recovered=False,
                               semantic=False, at_stage=False)]
                 ).to_csv(tmp_path / "POSITIVE_CONTROL.csv", index=False)
    criteria, _ = acceptance.check(tmp_path, theses)
    rule = next(c for c in criteria
                if c.name == "every verified direct control recovered")
    assert rule.passed


def test_gate_reports_semantic_recall_separately(tmp_path, theses):
    """Recovered only via citation passes overall and fails semantic."""
    rows = [_control_row(thesis=f"T{i:02d}", recovered=True, semantic=False,
                         at_stage=False) for i in range(1, 11)]
    pd.DataFrame(rows).to_csv(tmp_path / "POSITIVE_CONTROL.csv", index=False)
    criteria, _ = acceptance.check(tmp_path, theses)
    overall = next(c for c in criteria if c.name.startswith("overall control recall"))
    semantic = next(c for c in criteria if c.name.startswith("semantic-only recall"))
    assert overall.passed, "overall recall is satisfied"
    assert not semantic.passed, "but embedding search found none of them"
    assert "citation expansion is carrying the method" in semantic.detail


def test_semantic_recall_is_a_warning_not_a_blocker(tmp_path, theses):
    rows = [_control_row(thesis=f"T{i:02d}", semantic=False, at_stage=False)
            for i in range(1, 11)]
    pd.DataFrame(rows).to_csv(tmp_path / "POSITIVE_CONTROL.csv", index=False)
    criteria, _ = acceptance.check(tmp_path, theses)
    semantic = next(c for c in criteria if c.name.startswith("semantic-only recall"))
    assert semantic.mark == "WARN"


# ── the control set is frozen too ─────────────────────────────────────────────

from src.paper_3 import control_plan, control_set  # noqa: E402


def test_fingerprint_ignores_fields_that_may_change_after_freezing(theses):
    """Reading a control and adding its quote must not break the freeze."""
    import dataclasses

    before = control_set.fingerprint(theses)
    read = dataclasses.replace(
        theses[0].positive_controls[0], manually_checked=True,
        quote="a quote", supports_what="something", note="read today")
    after = control_set.fingerprint(
        [dataclasses.replace(theses[0], positive_controls=(read,))] + theses[1:])
    assert before == after


def test_fingerprint_changes_when_a_holdout_is_swapped(theses):
    """The soft leakage this exists to stop."""
    import dataclasses

    before = control_set.fingerprint(theses)
    easier = dataclasses.replace(theses[0].positive_controls[0], doi="10.1/easier")
    after = control_set.fingerprint(
        [dataclasses.replace(theses[0], positive_controls=(easier,))] + theses[1:])
    assert before != after


@pytest.mark.parametrize("field,value", [
    ("role", "development"), ("relevance", "method"),
    ("difficulty", "easy"), ("expected_stage", "citation_expansion"),
])
def test_fingerprint_covers_every_identity_field(theses, field, value):
    import dataclasses

    control = theses[0].positive_controls[0]
    if getattr(control, field) == value:
        pytest.skip("already that value")
    before = control_set.fingerprint(theses)
    changed = dataclasses.replace(control, **{field: value})
    after = control_set.fingerprint(
        [dataclasses.replace(theses[0], positive_controls=(changed,))] + theses[1:])
    assert before != after, f"{field} must be part of the control identity"


def test_freeze_then_unchanged_reports_clean(tmp_path, theses):
    control_set.freeze(tmp_path, theses, force=True)
    unchanged, drift = control_set.status(theses, tmp_path)
    assert unchanged and drift == []


def test_freeze_then_swap_is_refused(tmp_path, theses):
    import dataclasses

    control_set.freeze(tmp_path, theses, force=True)
    swapped = [dataclasses.replace(
        theses[0],
        positive_controls=(dataclasses.replace(
            theses[0].positive_controls[0], doi="10.1/easier"),))] + theses[1:]

    with pytest.raises(RuntimeError, match="frozen and has changed"):
        control_set.freeze(tmp_path, swapped)

    unchanged, drift = control_set.status(swapped, tmp_path)
    assert not unchanged
    assert any("REMOVED" in d for d in drift)
    assert any("ADDED" in d for d in drift)


def test_a_refused_freeze_explains_the_exclusion_route(tmp_path, theses):
    import dataclasses

    control_set.freeze(tmp_path, theses, force=True)
    swapped = [dataclasses.replace(theses[0], positive_controls=())] + theses[1:]
    with pytest.raises(RuntimeError, match="exclusion_reason"):
        control_set.freeze(tmp_path, swapped)


def test_force_refreeze_keeps_the_previous_set_in_history(tmp_path, theses):
    import dataclasses

    control_set.freeze(tmp_path, theses, force=True)
    swapped = [dataclasses.replace(
        theses[0],
        positive_controls=(dataclasses.replace(
            theses[0].positive_controls[0], doi="10.1/easier"),))] + theses[1:]
    payload = control_set.freeze(tmp_path, swapped, force=True)
    assert len(payload["history"]) == 1
    assert payload["history"][0]["frozen"]


def test_freeze_records_the_rules_version_it_was_taken_under(tmp_path, theses):
    from src.paper_3.freeze import RETRIEVAL_RULES_VERSION
    payload = control_set.freeze(tmp_path, theses, force=True)
    assert payload["retrieval_rules_version_before_tuning"] == RETRIEVAL_RULES_VERSION


def test_gate_requires_the_control_set_to_be_frozen(tmp_path, theses):
    criteria, _ = acceptance.check(tmp_path, theses)
    frozen = next(c for c in criteria if c.name.startswith("control set frozen"))
    assert not frozen.passed
    assert "freeze-controls" in frozen.detail


def test_gate_sees_the_control_set_once_frozen(tmp_path, theses):
    control_set.freeze(tmp_path, theses, force=True)
    criteria, _ = acceptance.check(tmp_path, theses)
    frozen = next(c for c in criteria if c.name.startswith("control set frozen"))
    assert frozen.passed


# ── excluded controls ─────────────────────────────────────────────────────────

def test_an_excluded_control_does_not_count_as_verified():
    from src.paper_3.theses import PositiveControl
    c = PositiveControl(doi="10.1/x", manually_checked=True, quote="q",
                        excluded=True, exclusion_reason="not direct evidence")
    assert not c.verified


def test_an_exclusion_needs_a_reason(theses):
    import dataclasses

    from src.paper_3.theses import PositiveControl, validate_theses

    bad = PositiveControl(doi="10.1/x", excluded=True)
    patched = [dataclasses.replace(theses[0], positive_controls=(bad,))] + theses[1:]
    assert any("exclusion with no record" in p for p in validate_theses(patched))


def test_a_verified_control_must_say_what_it_supports(theses):
    import dataclasses

    from src.paper_3.theses import PositiveControl, validate_theses

    bad = PositiveControl(doi="10.1/x", manually_checked=True, quote="q")
    patched = [dataclasses.replace(theses[0], positive_controls=(bad,))] + theses[1:]
    assert any("what it supports" in p for p in validate_theses(patched))


# ── the requirement plan ──────────────────────────────────────────────────────

def test_requirements_count_matches_the_rule(theses):
    from src.paper_3.theses import CRITICAL_THESES
    stats = control_plan.summary(theses)
    assert stats["records_required"] == len(theses) + len(CRITICAL_THESES)


def test_critical_theses_require_a_direct_hard_holdout(theses):
    frame = control_plan.requirements(theses)
    crit = frame[(frame["thesis_id"] == "T09") & (frame["need"] != "extra")]
    assert set(crit["role"]) == {"development", "holdout"}
    holdout = crit[crit["role"] == "holdout"].iloc[0]
    assert holdout["relevance"] == "direct"
    assert holdout["difficulty"] == "hard"


def test_worksheet_carries_the_verification_fields(tmp_path, theses):
    path = control_plan.run(tmp_path, theses)
    frame = pd.read_csv(path).fillna("")
    for column in ("supports_what", "does_not_support", "source_of_control",
                   "page", "thesis_statement"):
        assert column in frame.columns
    assert (frame["need"] == "NEW").any()


def test_correlated_holdouts_are_detected(theses):
    import dataclasses

    from src.paper_3.theses import PositiveControl

    shared = PositiveControl(doi="10.1/shared", role="holdout", relevance="direct",
                             manually_checked=True, quote="q",
                             supports_what="x")
    patched = [dataclasses.replace(t, positive_controls=(shared,))
               if t.id in ("T09", "T10") else t for t in theses]
    assert control_plan.correlated_holdouts(patched) == {
        "10.1/shared": ["T09", "T10"]}


def test_uncorrelated_holdouts_are_clean(theses):
    assert control_plan.correlated_holdouts(theses) == {}


def test_gate_blocks_correlated_critical_holdouts(tmp_path, theses):
    import dataclasses

    from src.paper_3.theses import PositiveControl

    shared = PositiveControl(doi="10.1/shared", role="holdout", relevance="direct",
                             manually_checked=True, quote="q", supports_what="x")
    patched = [dataclasses.replace(t, positive_controls=(shared,))
               if t.id in ("T09", "T10") else t for t in theses]
    criteria, _ = acceptance.check(tmp_path, patched)
    rule = next(c for c in criteria if c.name == "critical holdouts are not correlated")
    assert not rule.passed


def test_gate_requires_a_direct_holdout_for_critical_theses(tmp_path, theses):
    criteria, _ = acceptance.check(tmp_path, theses)
    rule = next(c for c in criteria
                if c.name == "critical theses have a verified DIRECT holdout")
    assert not rule.passed
    assert "method or analogue control cannot validate" in rule.detail


def test_freezing_an_incomplete_control_set_is_refused(tmp_path, theses):
    """Freezing early makes every later control look like drift, so forcing
    becomes routine and the freeze stops meaning anything."""
    with pytest.raises(RuntimeError, match="not complete yet"):
        control_set.freeze(tmp_path, theses)


def test_the_refusal_names_what_is_outstanding(tmp_path, theses):
    with pytest.raises(RuntimeError) as err:
        control_set.freeze(tmp_path, theses)
    message = str(err.value)
    import re
    assert re.search(r"\d+ of \d+", message), message
    assert "have not been read" in message
    assert "--step controls" in message


def test_a_complete_set_freezes_without_force(tmp_path, theses):
    import dataclasses

    from src.paper_3.theses import PositiveControl

    def controls_for(t):
        made = [PositiveControl(
            doi=f"10.1/{t.id}-dev", role="development", relevance="direct",
            manually_checked=True, quote="q", supports_what="x")]
        if t.is_critical:
            made.append(PositiveControl(
                doi=f"10.1/{t.id}-hold", role="holdout", relevance="direct",
                difficulty="hard", manually_checked=True, quote="q",
                supports_what="x"))
        return tuple(made)

    complete = [dataclasses.replace(t, positive_controls=controls_for(t))
                for t in theses]
    payload = control_set.freeze(tmp_path, complete)
    assert payload["frozen"]
    assert payload["n_controls"] == len(complete) + sum(t.is_critical for t in complete)
