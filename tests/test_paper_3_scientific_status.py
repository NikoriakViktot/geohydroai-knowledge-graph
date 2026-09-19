"""The audit layer: what our own data support, and what that forbids.

The rule this file exists to pin is the asymmetry. A claim nobody has audited
must block, because the cost of wrongly blocking a good claim is a review cycle
and the cost of wrongly passing a bad one is a retraction.
"""
from __future__ import annotations

import pandas as pd
import pytest

from src.paper_3.v2 import claim_map, scientific_status as ss


@pytest.fixture(scope="module")
def draft_claim_ids():
    return claim_map.claim_ids(claim_map.load_draft())


@pytest.fixture(scope="module")
def status():
    return ss.load_status()


# ── the checked-in file ───────────────────────────────────────────────────────

def test_every_draft_claim_has_a_status(status, draft_claim_ids):
    # The file now also carries own-evidence ids the v1 draft never cited
    # (S1.x vegetation, K1.x channel, C-nn audit rows …); they are legal extras.
    assert ss.validate(status, draft_claim_ids, allow_extra=True) == []
    import re
    extras = set(status) - set(draft_claim_ids)
    assert all(re.fullmatch(r"[A-Z]{1,2}-?\d+(\.\d+[a-z]?)?", e) for e in extras), sorted(extras)[:5]


def test_the_draft_has_the_29_claim_ids_we_think_it_has(draft_claim_ids):
    assert len(draft_claim_ids) == 29
    for expected in ("V1.1", "V6.1", "V10.4", "M1.1", "M3.2", "X2.1"):
        assert expected in draft_claim_ids


def test_claim_ids_sort_numerically_not_lexically(draft_claim_ids):
    """V2.1 must come before V10.1, or the YAML reads as if V10 were missing."""
    v_ids = [c for c in draft_claim_ids if c.startswith("V")]
    assert v_ids.index("V2.1") < v_ids.index("V10.1")


# ── unknown blocks ────────────────────────────────────────────────────────────

def test_an_unaudited_claim_blocks_publication():
    entry = ss.ClaimStatus(claim_id="V9.9")
    assert entry.audit_status == "UNKNOWN"
    assert entry.blocks_publication
    assert not entry.audited


def test_a_claim_absent_from_the_file_blocks(status):
    """Silence in the audit is not approval."""
    assert ss.blocks_publication("NOT.1", status)


def test_only_supported_passes_unchanged():
    for verdict in ss.AUDIT_STATUSES:
        entry = ss.ClaimStatus(claim_id="X", audit_status=verdict)
        assert entry.blocks_publication is (verdict != "SUPPORTED")


def test_the_shipped_file_reflects_the_imported_audit(status):
    """Since 2026-09-18 the SWOT-DNIPRO audit is imported via own_evidence:
    most claims carry a verdict, and the known blockers still block."""
    assert ss.summary(status)["n_audited"] > 0
    blocking = set(ss.blocking_claims(status))
    assert "G2" in blocking                      # EGG2015 provenance — UNKNOWN by design
    assert "M1.1" not in blocking                # the headline result is SUPPORTED
    assert 0 < len(blocking) < len(status)


# ── validation catches the ways this file can lie ─────────────────────────────

def test_requires_reanalysis_must_name_an_action():
    bad = {"M3.1": ss.ClaimStatus("M3.1", "REQUIRES_REANALYSIS", findings=("F-02",))}
    problems = ss.validate(bad, ["M3.1"])
    assert any("names no action" in p for p in problems)


def test_a_negative_verdict_must_cite_a_finding():
    """A verdict with no evidence behind it is an opinion."""
    for verdict in ("UNSUPPORTED", "CONTRADICTED"):
        bad = {"M3.1": ss.ClaimStatus("M3.1", verdict)}
        problems = ss.validate(bad, ["M3.1"])
        assert any("cites no finding" in p for p in problems)


def test_forcing_blocks_false_on_a_non_supported_claim_is_flagged():
    bad = {"M3.1": ss.ClaimStatus("M3.1", "UNSUPPORTED", findings=("F-02",),
                                  blocks_publication_override=False)}
    problems = ss.validate(bad, ["M3.1"])
    assert any("overrides the default safety rule" in p for p in problems)
    assert not bad["M3.1"].blocks_publication, "the override still applies"


def test_illegal_status_is_rejected():
    bad = {"M3.1": ss.ClaimStatus("M3.1", "PROBABLY_FINE")}
    assert any("illegal audit_status" in p for p in ss.validate(bad, ["M3.1"]))


def test_a_status_for_a_claim_not_in_the_draft_is_flagged(draft_claim_ids):
    extra = {c: ss.ClaimStatus(c) for c in draft_claim_ids}
    extra["Z9.9"] = ss.ClaimStatus("Z9.9")
    assert any("not in the draft" in p for p in ss.validate(extra, draft_claim_ids))


# ── importing a copied audit run ──────────────────────────────────────────────

def test_import_from_a_missing_directory_is_a_named_error(tmp_path):
    with pytest.raises(FileNotFoundError, match="audit_run"):
        ss.import_audit(tmp_path / "nope", ["M3.1"])


def test_import_from_an_empty_directory_leaves_everything_unknown(tmp_path):
    (tmp_path / "readme.txt").write_text("nothing useful", encoding="utf-8")
    assert ss.import_audit(tmp_path, ["M3.1"]) == {}


def test_import_reads_a_yaml_run(tmp_path):
    (tmp_path / "claim_status.yaml").write_text(
        "M3.1:\n"
        "  audit_status: REQUIRES_REANALYSIS\n"
        "  findings: [F-02, F-03]\n"
        "  required_actions: [A3, A4, A5]\n"
        "  note: coverage-confounded pre-breach cohort\n",
        encoding="utf-8")
    out = ss.import_audit(tmp_path, ["M3.1"])
    assert out["M3.1"].audit_status == "REQUIRES_REANALYSIS"
    assert out["M3.1"].findings == ("F-02", "F-03")
    assert out["M3.1"].required_actions == ("A3", "A4", "A5")
    assert out["M3.1"].blocks_publication


def test_import_reads_a_csv_run(tmp_path):
    pd.DataFrame([
        {"claim_id": "M3.1", "status": "unsupported",
         "findings": "F-02; F-03", "actions": "A3, A4", "note": "coverage"},
        {"claim_id": "V10.1", "status": "REVISE_UNCERTAINTY",
         "findings": "F-01", "actions": "A1", "note": ""},
    ]).to_csv(tmp_path / "claims.csv", index=False)

    out = ss.import_audit(tmp_path, ["M3.1", "V10.1", "V1.1"])
    assert out["M3.1"].audit_status == "UNSUPPORTED"
    assert out["M3.1"].findings == ("F-02", "F-03")
    assert out["V10.1"].required_actions == ("A1",)
    assert "V1.1" not in out, "a claim the audit does not mention stays UNKNOWN"


def test_import_ignores_a_csv_without_the_needed_columns(tmp_path):
    pd.DataFrame([{"something": 1, "else": 2}]).to_csv(
        tmp_path / "other.csv", index=False)
    assert ss.import_audit(tmp_path, ["M3.1"]) == {}


def test_import_leaves_an_unmapped_status_unknown(tmp_path):
    pd.DataFrame([{"claim_id": "M3.1", "status": "looks fine to me"}]).to_csv(
        tmp_path / "claims.csv", index=False)
    assert ss.import_audit(tmp_path, ["M3.1"]) == {}


# ── round trip ────────────────────────────────────────────────────────────────

def test_write_then_load_round_trips(tmp_path, draft_claim_ids):
    original = {"M3.1": ss.ClaimStatus("M3.1", "REQUIRES_REANALYSIS",
                                       findings=("F-02",),
                                       required_actions=("A3",),
                                       note="coverage-confounded")}
    path = ss.write_status(original, tmp_path / "s.yaml", claim_ids=draft_claim_ids)
    reloaded = ss.load_status(path)

    assert len(reloaded) == len(draft_claim_ids)
    assert reloaded["M3.1"].audit_status == "REQUIRES_REANALYSIS"
    assert reloaded["M3.1"].required_actions == ("A3",)
    assert reloaded["V1.1"].audit_status == "UNKNOWN", "unmentioned claims filled in"


def test_write_fills_in_every_claim_so_none_is_silently_absent(tmp_path, draft_claim_ids):
    path = ss.write_status({}, tmp_path / "s.yaml", claim_ids=draft_claim_ids)
    assert set(ss.load_status(path)) == set(draft_claim_ids)
