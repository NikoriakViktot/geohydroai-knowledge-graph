"""L-series literature matrix: claim cards, status precedence, eligibility by run
identity, and a §7.8 renderer that never asserts priority.

No network, no ChromaDB, no Neo4j, no model. Frames are synthetic.
"""
from __future__ import annotations

import json

import pandas as pd
import pytest

from src.paper_3 import gap_matrix
from src.paper_3.control_set import CONTROL_SET_FILE
from src.paper_3.freeze import FREEZE_FILE, RETRIEVAL_RULES_VERSION
from src.paper_3.theses import load_theses
from src.paper_3.v2 import claim_map, literature_matrix as lm, markers, slice_screening


@pytest.fixture(scope="module")
def theses():
    return load_theses()


@pytest.fixture(scope="module")
def slices():
    return slice_screening.load_slices()


@pytest.fixture(scope="module")
def claims():
    return lm.load_claims()[0]


@pytest.fixture(scope="module")
def draft_ids():
    return claim_map.claim_ids(claim_map.load_draft())


def _claim(**kw) -> lm.LiteratureClaim:
    base = dict(id="LK9.1", layer="K", gap_type="empirical", evidence_mode="thesis",
                claim="The corpus positions this against T01.", thesis_ids=("T01",),
                backs=("M1.1",), limitations="synthetic")
    base.update(kw)
    return lm.LiteratureClaim(**base)


def _matrix(rows: list[dict], run_id: str = "R") -> pd.DataFrame:
    out = []
    for r in rows:
        row = {"thesis_id": "T01", "verdict": gap_matrix.RETRIEVAL_UNVALIDATED,
               "n_contradicting_papers": 0, "novelty_eligible": False,
               "corpus_adequacy": "thin", "retrieval_validated": False,
               "evidence_level_mix": gap_matrix.FULL_TEXT, "run_id": run_id}
        row.update(r)
        out.append(row)
    return pd.DataFrame(out)


def _relations(rows: list[dict]) -> pd.DataFrame:
    out = []
    for r in rows:
        row = {"thesis_id": "T01", "paper_id": "p", "doi": "10.1/a", "title": "A",
               "year": 2024, "relation": "SUPPORTS", "confidence": 0.9,
               "evidence_quote": "a verified sentence", "quote_verified": True,
               "quote_section": "Results"}
        row.update(r)
        out.append(row)
    return pd.DataFrame(out)


# ── claim cards ───────────────────────────────────────────────────────────────

def test_shipped_claims_validate_against_theses_and_draft(theses, slices, claims, draft_ids):
    assert lm.validate_claims(claims, theses, draft_ids, slices) == []
    assert slice_screening.validate_slices(slices) == []
    assert {c.id for c in claims} >= {"LK1.1", "LT1.1", "LD1.1"}


def test_all_three_layers_are_populated_and_ordered(claims):
    """K carries the novelty case; T and D carry the interpretation and the method
    defence. A review with only one layer cannot do all three jobs."""
    by_layer = {}
    for c in claims:
        by_layer.setdefault(c.layer, []).append(c.id)
    assert set(by_layer) == set(lm.LAYER_CODES)
    assert [c.layer for c in claims] == sorted(
        (c.layer for c in claims), key=lm.LAYER_CODES.index)
    for layer, ids in by_layer.items():
        assert all(i.startswith(f"L{layer}") for i in ids), layer


def test_a_claim_whose_id_contradicts_its_layer_is_refused(theses, slices):
    problems = lm.validate_claims([_claim(id="LD9.1", layer="K")], theses, ["M1.1"], slices)
    assert any("does not carry its layer" in p for p in problems)
    problems = lm.validate_claims([_claim(layer="Z")], theses, ["M1.1"], slices)
    assert any("layer 'Z' not in" in p for p in problems)


def test_each_slice_declares_the_layer_it_serves():
    import yaml
    raw = yaml.safe_load(lm.CLAIMS_PATH.read_text(encoding="utf-8"))
    for sid, body in raw["slices"].items():
        assert body.get("layer") in lm.LAYER_CODES, sid
        assert body.get("question"), sid


def test_unresolved_citations_are_never_marked_citable():
    _, unresolved = lm.load_claims()
    assert unresolved, "the two proposed-but-unresolved citations must be on record"
    for u in unresolved:
        assert u.do_not_cite_until_resolved
        assert u.checked and u.method
    statuses = {u.text: u.status for u in unresolved}
    assert statuses["Stelmaszczuk-Gorska et al., 2023"] == "UNRESOLVED_OPENALEX"
    assert statuses["Kuzova, 2023"] == "UNRESOLVED_OPENALEX"


def test_thesis_mode_claim_may_not_use_absence_wording(theses, slices):
    bad = _claim(claim="No study reports a longitudinal gradient after the breach.")
    problems = lm.validate_claims([bad], theses, ["M1.1"], slices)
    assert any("absence/prevalence wording" in p for p in problems)


def test_claim_text_may_not_assert_novelty(theses, slices):
    bad = _claim(claim="This is the first study to harmonise the datums.")
    problems = lm.validate_claims([bad], theses, ["M1.1"], slices)
    assert any("novelty word" in p for p in problems)


def test_bad_ids_theses_backs_and_slices_are_named(theses, slices):
    problems = lm.validate_claims([
        _claim(id="V1.1"),
        _claim(id="LK9.2", thesis_ids=("T99",)),
        _claim(id="LK9.3", backs=("Z1.1",)),
        _claim(id="LK9.4", evidence_mode="slice_absence", slice_id="S9_nope",
               target_attribute="x", claim="none reports x"),
        _claim(id="LK9.5", evidence_mode="slice_absence",
               slice_id="S1_kakhovka_status_quo", target_attribute="not_an_attribute",
               claim="none reports x"),
    ], theses, ["M1.1"], slices)
    joined = "\n".join(problems)
    assert "must look like LK1.1" in joined
    assert "unknown thesis ids ['T99']" in joined
    assert "backs ['Z1.1'] not cited" in joined
    assert "unknown slice_id 'S9_nope'" in joined
    assert "no attribute 'not_an_attribute'" in joined


# ── precedence ────────────────────────────────────────────────────────────────

def test_retrieval_validity_outranks_a_contradiction():
    matrix = _matrix([{"verdict": gap_matrix.RETRIEVAL_UNVALIDATED, "n_contradicting_papers": 1}])
    frame = lm.build([_claim()], matrix, _relations([{"relation": "CONTRADICTS"}]))
    assert frame.iloc[0]["validation_status"] == lm.RETRIEVAL_UNVALIDATED
    assert frame.iloc[0]["n_contradicting"] == 1


def test_incomplete_outranks_unvalidated():
    matrix = _matrix([{"thesis_id": "T01", "verdict": gap_matrix.RETRIEVAL_INCOMPLETE},
                      {"thesis_id": "T02", "verdict": gap_matrix.RETRIEVAL_UNVALIDATED}])
    frame = lm.build([_claim(thesis_ids=("T01", "T02"))], matrix, None)
    assert frame.iloc[0]["validation_status"] == lm.RETRIEVAL_INCOMPLETE
    assert "T01" in frame.iloc[0]["status_rationale"]


@pytest.mark.parametrize("verdicts, contra, eligible, expected", [
    ([gap_matrix.KNOWN, gap_matrix.CANDIDATE_GAP], [1, 0], [False, True], lm.CONTESTED),
    ([gap_matrix.KNOWN, gap_matrix.CANDIDATE_GAP], [0, 0], [False, True], lm.PARTIAL),
    ([gap_matrix.METHOD_ONLY], [0], [False], lm.PARTIAL),
    ([gap_matrix.CANDIDATE_GAP, gap_matrix.CANDIDATE_GAP], [0, 0], [True, True], lm.CANDIDATE_GAP),
    ([gap_matrix.CANDIDATE_GAP], [0], [False], lm.UNRESOLVED),
    ([gap_matrix.NOT_FOUND], [0], [False], lm.UNRESOLVED),
])
def test_each_thesis_status_is_reachable(verdicts, contra, eligible, expected):
    ids = [f"T0{i + 1}" for i in range(len(verdicts))]
    matrix = _matrix([{"thesis_id": t, "verdict": v, "n_contradicting_papers": c,
                       "novelty_eligible": e, "retrieval_validated": True}
                      for t, v, c, e in zip(ids, verdicts, contra, eligible)])
    frame = lm.build([_claim(thesis_ids=tuple(ids))], matrix, None)
    assert frame.iloc[0]["validation_status"] == expected


def test_a_thesis_missing_from_the_matrix_is_unvalidated():
    frame = lm.build([_claim(thesis_ids=("T01", "T07"))], _matrix([{}]), None)
    assert frame.iloc[0]["validation_status"] == lm.RETRIEVAL_UNVALIDATED
    assert "T07" in frame.iloc[0]["status_rationale"]


# ── slice modes ───────────────────────────────────────────────────────────────

def _slice_claim(mode="slice_absence", **kw):
    return _claim(id="LK9.7", evidence_mode=mode, slice_id="S1_kakhovka_status_quo",
                  target_attribute="reports_wse_slope", min_screened=5,
                  claim="none of the screened works reports a gradient", **kw)


def _den(n_screened=20, n_target=0, worldwide=30, n_full=5):
    return pd.DataFrame([{
        "slice_id": "S1_kakhovka_status_quo", "worldwide_count": worldwide,
        "n_candidates": n_screened, "n_screened": n_screened, "n_fulltext": n_full,
        "n_abstract_only": n_screened - n_full,
        "screened_fraction": round(n_screened / worldwide, 4) if worldwide else 0.0,
        "reports_wse_slope_n": n_target, "reports_extent_n": 12,
    }])


VALIDATED = {"verdict": gap_matrix.NOT_FOUND, "retrieval_validated": True}


def test_slice_claim_is_gated_by_retrieval_before_its_denominator():
    frame = lm.build([_slice_claim()], _matrix([{}]), None, denominators=_den())
    assert frame.iloc[0]["validation_status"] == lm.RETRIEVAL_UNVALIDATED
    assert frame.iloc[0]["denominator_n_screened"] == 0


def test_slice_not_run_when_no_denominators():
    frame = lm.build([_slice_claim()], _matrix([VALIDATED]), None, denominators=None)
    assert frame.iloc[0]["validation_status"] == lm.SLICE_NOT_RUN


def test_slice_undersampled_below_min_screened_or_fraction():
    small = lm.build([_slice_claim()], _matrix([VALIDATED]), None, denominators=_den(n_screened=3))
    assert small.iloc[0]["validation_status"] == lm.SLICE_UNDERSAMPLED
    thin = lm.build([_slice_claim()], _matrix([VALIDATED]), None,
                    denominators=_den(n_screened=20, worldwide=500))
    assert thin.iloc[0]["validation_status"] == lm.SLICE_UNDERSAMPLED
    assert "worldwide" in thin.iloc[0]["status_rationale"]


def test_slice_absence_observed_and_contradicted():
    ok = lm.build([_slice_claim()], _matrix([VALIDATED]), None, denominators=_den(n_target=0))
    assert ok.iloc[0]["validation_status"] == lm.SLICE_ABSENCE_OBSERVED
    bad = lm.build([_slice_claim()], _matrix([VALIDATED]), None, denominators=_den(n_target=2))
    assert bad.iloc[0]["validation_status"] == lm.SLICE_CONTRADICTED
    assert bad.iloc[0]["denominator_n_target"] == 2


def test_slice_prevalence_reports_the_fraction():
    frame = lm.build([_slice_claim(mode="slice_prevalence")], _matrix([VALIDATED]), None,
                     denominators=_den(n_screened=20, n_target=5))
    row = frame.iloc[0]
    assert row["validation_status"] == lm.SLICE_PREVALENCE_OBSERVED
    assert row["denominator_fraction"] == 0.25


def test_no_slice_status_is_ever_candidate_gap():
    assert lm.CANDIDATE_GAP not in lm.SLICE_STATUSES
    for status in lm.SLICE_STATUSES:
        assert status != lm.CANDIDATE_GAP


# ── unit of evidence ──────────────────────────────────────────────────────────

def test_two_passages_from_one_doi_are_one_paper():
    rel = _relations([{"passage_id": "P1"}, {"passage_id": "P2", "confidence": 0.5}])
    frame = lm.build([_claim()], _matrix([VALIDATED]), rel)
    assert frame.iloc[0]["n_papers_bearing"] == 1
    assert frame.iloc[0]["closest_quote"] == "a verified sentence"
    assert frame.iloc[0]["closest_quote_doi"] == "10.1/a"


def test_method_relevant_counts_without_a_quote_but_supports_does_not():
    rel = _relations([
        {"doi": "10.1/m", "relation": "METHOD_RELEVANT", "quote_verified": False, "evidence_quote": ""},
        {"doi": "10.1/s", "relation": "SUPPORTS", "quote_verified": False, "evidence_quote": "x"},
    ])
    frame = lm.build([_claim()], _matrix([VALIDATED]), rel)
    assert frame.iloc[0]["n_papers_bearing"] == 1
    assert frame.iloc[0]["source_dois"] == "10.1/m"
    assert frame.iloc[0]["closest_quote"] == ""


# ── eligibility by run identity ───────────────────────────────────────────────

def _write_run(tmp_path, run_id="R2", gate=True, control_ok=True, rules_ok=True):
    (tmp_path / "run_manifest.json").write_text(json.dumps({
        "run_id": run_id, "acceptance_gate_ready": gate, "control_set_sha256": "abc"}))
    (tmp_path / FREEZE_FILE).write_text(json.dumps({
        "retrieval_rules_version": RETRIEVAL_RULES_VERSION if rules_ok else "0.0.1"}))
    (tmp_path / CONTROL_SET_FILE).write_text(json.dumps({
        "sha256": "abc" if control_ok else "zzz"}))


def test_stale_canonical_matrix_is_skipped_for_the_current_exploratory_one(tmp_path):
    _write_run(tmp_path)
    _matrix([{"retrieval_validated": True}], run_id="R1").to_parquet(
        tmp_path / gap_matrix.CANONICAL_MATRIX, index=False)
    _matrix([{}], run_id="R2").to_parquet(tmp_path / gap_matrix.EXPLORATORY_MATRIX, index=False)
    frame, eligible, reasons = lm.matrix_compatible(tmp_path)
    assert frame is not None and set(frame["run_id"]) == {"R2"}
    assert eligible is False
    assert any("stale run_id" in r and gap_matrix.CANONICAL_MATRIX in r for r in reasons)
    assert any("exploratory" in r for r in reasons)


def test_canonical_matrix_with_matching_run_but_gate_not_ready_is_not_eligible(tmp_path):
    _write_run(tmp_path, gate=False)
    _matrix([{"retrieval_validated": True}], run_id="R2").to_parquet(
        tmp_path / gap_matrix.CANONICAL_MATRIX, index=False)
    _, eligible, reasons = lm.matrix_compatible(tmp_path)
    assert eligible is False
    assert any("acceptance gate" in r for r in reasons)


@pytest.mark.parametrize("kw, needle", [
    ({"control_ok": False}, "control set"),
    ({"rules_ok": False}, "retrieval rules"),
])
def test_frozen_identity_mismatches_block_eligibility(tmp_path, kw, needle):
    _write_run(tmp_path, **kw)
    _matrix([{"retrieval_validated": True}], run_id="R2").to_parquet(
        tmp_path / gap_matrix.CANONICAL_MATRIX, index=False)
    _, eligible, reasons = lm.matrix_compatible(tmp_path)
    assert eligible is False
    assert any(needle in r for r in reasons)


def test_everything_matching_is_eligible(tmp_path):
    _write_run(tmp_path)
    _matrix([{"retrieval_validated": True}], run_id="R2").to_parquet(
        tmp_path / gap_matrix.CANONICAL_MATRIX, index=False)
    frame, eligible, reasons = lm.matrix_compatible(tmp_path)
    assert frame is not None and eligible is True and reasons == []


def test_no_matrix_for_the_run_is_none(tmp_path):
    _write_run(tmp_path)
    frame, eligible, _ = lm.matrix_compatible(tmp_path)
    assert frame is None and eligible is False


# ── rendering ─────────────────────────────────────────────────────────────────

def _rendered(status_rows):
    return lm.render_section_7_8(status_rows)


def test_section_7_8_marks_every_unsupportable_row_and_is_deterministic():
    matrix = _matrix([{}])
    frame = lm.build([_claim(), _slice_claim()], matrix, None, denominators=_den())
    text = _rendered(frame)
    assert text == _rendered(frame)
    ids = markers.find_marker_ids(text)
    assert len(ids) == 2 and ids == sorted(ids)
    parsed = markers.parse_markers(text)
    assert all(m.kind == "open_item" for m in parsed)
    assert markers.validate_all(parsed) == []
    assert all(m.blocks_submission for m in parsed)
    assert "[LK9.1]" in text and "[LK9.7]" in text
    assert "withheld" in text


def test_a_candidate_gap_in_the_kakhovka_layer_gets_no_marker_and_no_novelty_words():
    matrix = _matrix([{"verdict": gap_matrix.CANDIDATE_GAP, "novelty_eligible": True,
                       "retrieval_validated": True}])
    frame = lm.build([_claim(layer="K")], matrix, None)
    assert frame.iloc[0]["validation_status"] == lm.CANDIDATE_GAP
    text = _rendered(frame)
    assert markers.find_marker_ids(text) == []
    assert not lm._FORBIDDEN.search(text)


@pytest.mark.parametrize("layer", sorted(lm.SUPPORTING_LAYERS))
def test_a_gap_in_a_supporting_layer_is_a_weakness_not_a_contribution(layer):
    """Layers T and D exist to be populated. Silence there means the physical
    interpretation or the method defence has no literature behind it."""
    matrix = _matrix([{"verdict": gap_matrix.CANDIDATE_GAP, "novelty_eligible": True,
                       "retrieval_validated": True}])
    frame = lm.build([_claim(id=f"L{layer}9.1", layer=layer)], matrix, None)
    assert frame.iloc[0]["validation_status"] == lm.CANDIDATE_GAP
    assert lm.needs_marker(layer, lm.CANDIDATE_GAP)
    text = _rendered(frame)
    assert len(markers.find_marker_ids(text)) == 1
    marker = markers.parse_markers(text)[0]
    assert "has no supporting literature" in marker.title
    assert "weakens the defence" in marker.fields["next_step"]
    assert not lm._FORBIDDEN.search(text)


def test_section_is_grouped_into_the_three_layers_in_order():
    matrix = _matrix([{"thesis_id": "T01"}])
    frame = lm.build([_claim(id="LD9.1", layer="D"), _claim(id="LK9.1", layer="K"),
                      _claim(id="LT9.1", layer="T")], matrix, None)
    text = _rendered(frame)
    positions = [text.index(f"### {code}.") for code, *_ in lm.LAYERS]
    assert positions == sorted(positions), "layers must render K, T, D"
    assert "is the novelty case" in text
    assert "weakness, not a contribution" in text


def test_observed_slice_row_reports_its_denominator_in_prose():
    frame = lm.build([_slice_claim()], _matrix([VALIDATED]), None, denominators=_den(n_target=0))
    text = _rendered(frame)
    assert "Among 20 works recovered by Boolean slice S1_kakhovka_status_quo" in text
    assert "5 in full text, 15 by abstract only" in text
    assert markers.find_marker_ids(text) == []


def test_renderer_refuses_to_emit_a_novelty_word():
    frame = lm.build([_claim(claim="A claim that is otherwise fine.")], _matrix([VALIDATED]), None)
    frame.loc[0, "claim_text"] = "The first such study."
    with pytest.raises(ValueError, match="novelty word"):
        _rendered(frame)


def test_markdown_carries_banner_reasons_and_unresolved_citations():
    _, unresolved = lm.load_claims()
    frame = lm.build([_claim()], _matrix([{}]), None)
    md = lm.render_md(frame, unresolved, eligible=False, reasons=["acceptance gate not ready"])
    assert "EXPLORATORY" in md and "acceptance gate not ready" in md
    assert "Stelmaszczuk-Gorska" in md and "do not cite until resolved" in md
    assert "No slice has been screened" in md


# ── the step ──────────────────────────────────────────────────────────────────

def test_run_writes_exploratory_stems_when_the_gap_matrix_is_exploratory(tmp_path, theses):
    _write_run(tmp_path, gate=False)
    rows = [{"thesis_id": t.id} for t in theses]
    _matrix(rows, run_id="R2").to_parquet(tmp_path / gap_matrix.EXPLORATORY_MATRIX, index=False)
    md = lm.run(out_dir=tmp_path, theses=theses)
    assert md.name == f"{lm.EXPLORATORY_STEM}.md"
    assert (tmp_path / f"{lm.EXPLORATORY_STEM}.csv").exists()
    assert not (tmp_path / f"{lm.CANONICAL_STEM}.csv").exists()
    stub = (tmp_path / lm.STUB_FILE).read_text(encoding="utf-8")
    frame = pd.read_csv(tmp_path / f"{lm.EXPLORATORY_STEM}.csv")
    assert set(frame["validation_status"]) == {lm.RETRIEVAL_UNVALIDATED}
    # Nothing is supportable on an unvalidated run, so every claim carries a marker.
    assert len(markers.find_marker_ids(stub)) == len(frame)
    assert markers.validate_all(markers.parse_markers(stub)) == []
    assert set(frame["layer"]) == set(lm.LAYER_CODES)


def test_run_without_a_current_matrix_is_a_named_error(tmp_path, theses):
    _write_run(tmp_path)
    with pytest.raises(FileNotFoundError, match="run --step matrix"):
        lm.run(out_dir=tmp_path, theses=theses)
