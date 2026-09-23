"""own_evidence.py — values are read from snapshot rows, never typed; selectors must be unique."""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest
import yaml

from src.paper_3 import own_evidence as oe


def _make_snapshot(tmp_path: Path) -> Path:
    root = tmp_path / "snap"
    files = {
        "swot/outputs/tables/manuscript_evidence_matrix.csv":
            "claim_id,claim,result_type,n,value,uncertainty,source_table,source_figure,validation_status,limitations\n"
            "M1.1,slope changed,MAIN,14,+3.31,CI,ts.csv,F4,VALIDATED,local\n"
            "V9.4,bimodal,retracted,0,,,x.csv,,REJECTED,withdrawn\n",
        "swot/pilots/vegetation/out/class_shares_by_year_summary.csv":
            "year,n_dates,share_veg_5_6_7_median\n2023,10,0.2687\n2024,5,0.7199\n2025,12,0.8384\n",
        "swot/pilots/channel/out/width_summary_by_year.csv":
            "year,width_total_median_m\n2025.0,690.0\n",
    }
    entries = []
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        import hashlib
        entries.append({"repo": rel.split("/")[0], "rel_path": rel.split("/", 1)[1],
                        "local_path": rel, "sha256": hashlib.sha256(text.encode()).hexdigest(),
                        "bytes": len(text)})
    (root / "SNAPSHOT_MANIFEST.json").write_text(json.dumps({"files": entries, "n_files": len(entries),
                                                            "missing_patterns": [], "repos": {}}))
    return root


def _claims(tmp_path: Path, extra_claims=None, overrides=None) -> Path:
    spec = {
        "imports": [{
            "table": "manuscript_evidence_matrix.csv", "family": "legacy", "status_map": "matrix",
            "columns": {"id": "claim_id", "statement": "claim", "type": "result_type",
                        "value": "value", "uncertainty": "uncertainty", "n": "n",
                        "table": "source_table", "figure": "source_figure",
                        "limitations": "limitations", "status": "validation_status"}}],
        "claims": [{
            "id": "S1.1", "family": "vegetation", "claim_type": "area_series",
            "statement": "vegetated share rose",
            "table": "class_shares_by_year_summary.csv", "select": {"year": "2025"},
            "value": "{share_veg_5_6_7_median:.1%}", "n": "{n_dates}", "unit": "share",
            "status_source": "manual", "audit_status": "SUPPORTED_WITH_LIMITATION",
            "article_thesis": "AT8", "literature_thesis_ids": ["T25"],
        }] + (extra_claims or []),
        "overrides": overrides or {},
        "article_theses": [{"id": "AT8", "thesis": "vegetation", "claims": ["S1.1", "M1.1"]}],
    }
    p = tmp_path / "own_claims.yaml"
    p.write_text(yaml.safe_dump(spec))
    return p


def test_build_reads_values_from_rows_and_maps_statuses(tmp_path):
    snap = _make_snapshot(tmp_path)
    ev, at = oe.build(snap, _claims(tmp_path))
    by = ev.set_index("claim_id")
    assert by.loc["M1.1", "audit_status"] == "SUPPORTED"
    assert by.loc["M1.1", "value_resolved"] == "+3.31"
    assert by.loc["V9.4", "audit_status"] == "UNSUPPORTED"
    assert by.loc["V9.4", "value_resolved"] == ""            # nan → blank
    assert by.loc["S1.1", "value_resolved"] == "83.8%"
    assert by.loc["S1.1", "n_resolved"] == "12"
    assert by.loc["S1.1", "snapshot_sha256"]
    assert at.iloc[0]["n_claims"] == 2 and at.iloc[0]["n_supported"] == 2


def test_selector_must_match_exactly_one_row(tmp_path):
    snap = _make_snapshot(tmp_path)
    bad = [{"id": "S1.9", "statement": "x", "table": "class_shares_by_year_summary.csv",
            "select": {"year": "1999"}, "value": "{n_dates}"}]
    with pytest.raises(oe.SelectorError):
        oe.build(snap, _claims(tmp_path, extra_claims=bad))
    bad2 = [{"id": "S1.9", "statement": "x", "table": "class_shares_by_year_summary.csv",
             "select": {"n_dates": "10"}, "value": "{no_such_col}"}]
    with pytest.raises(oe.SelectorError):
        oe.build(snap, _claims(tmp_path, extra_claims=bad2))


def test_overrides_and_duplicates_are_policed(tmp_path):
    snap = _make_snapshot(tmp_path)
    ev, _ = oe.build(snap, _claims(tmp_path, overrides={"M1.1": {"audit_status": "REVISE_UNCERTAINTY"}}))
    assert ev.set_index("claim_id").loc["M1.1", "audit_status"] == "REVISE_UNCERTAINTY"
    with pytest.raises(ValueError):
        oe.build(snap, _claims(tmp_path, overrides={"M1.1": {"audit_status": "GREAT"}}))
    with pytest.raises(KeyError):
        oe.build(snap, _claims(tmp_path, overrides={"ZZ.1": {"audit_status": "SUPPORTED"}}))
    dup = [{"id": "M1.1", "statement": "again", "status_source": "manual", "audit_status": "SUPPORTED"}]
    with pytest.raises(ValueError):
        oe.build(snap, _claims(tmp_path, extra_claims=dup))


def test_unmapped_status_falls_to_unknown():
    assert oe.map_status("WHATEVER", "matrix") == "UNKNOWN"
    assert oe.map_status("supported_with_limitations", "audit") == "SUPPORTED_WITH_LIMITATION"


def test_shipped_claims_file_parses_and_declares_known_statuses():
    spec = oe.load_claims()
    assert spec["imports"] and spec["claims"] and spec["article_theses"]
    for c in spec["claims"]:
        if c.get("status_source", "manual") == "manual":
            assert c.get("audit_status") in oe.STATUSES, c["id"]


def test_a_derived_claim_resolves_its_value_like_a_snapshot_one(tmp_path, monkeypatch):
    """The `derived:` source loaded its table and then filled in nothing, because
    the rendering block sat inside the `table:` branch. Every number quoted from
    a derived table came out blank."""
    import pandas as pd
    from src.paper_3 import own_evidence as oe, derived as dv

    d = tmp_path / "derived"
    d.mkdir()
    pd.DataFrame([{"r": "-0.399", "p": "0.433", "n": "6"}]).to_csv(d / "t.csv", index=False)
    monkeypatch.setattr(dv, "DERIVED_DIR", d)

    row = oe.build_claim({
        "id": "X1", "statement": "s", "derived": "t.csv",
        "value": "Pearson r {r} (p {p})", "n": "{n} stations",
        "status_source": "manual", "audit_status": "SUPPORTED",
    }, tmp_path)
    assert row["value_resolved"] == "Pearson r -0.399 (p 0.433)"
    assert row["n_resolved"] == "6 stations"
    assert row["source_table"] == "derived/t.csv"
    assert row["snapshot_sha256"]          # provenance recorded, not blank
