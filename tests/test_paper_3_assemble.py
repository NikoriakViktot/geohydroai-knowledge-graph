"""assemble.py — numbers come from OWN_EVIDENCE, citations from REFERENCES, blocked claims become markers."""
from __future__ import annotations

import pandas as pd
import pytest

from src.paper_3.v2 import assemble as asm
from src.paper_3.v2 import markers


def _inputs():
    ev = pd.DataFrame([
        {"claim_id": "M1.1", "claim_text": "slope changed", "value_resolved": "+0.09 → +3.31 cm/km",
         "uncertainty_resolved": "[+1.99, +5.07]", "n_resolved": "14/14", "scientific_caveat": "local",
         "audit_status": "SUPPORTED", "remaining_action": ""},
        {"claim_id": "C-02", "claim_text": "F2 rose", "value_resolved": "0→0.31", "uncertainty_resolved": "",
         "n_resolved": "3", "scientific_caveat": "", "audit_status": "UNSUPPORTED", "remaining_action": "recompute"},
        {"claim_id": "C-14", "claim_text": "slope-Q", "value_resolved": "n_POST_with_Q = 0", "uncertainty_resolved": "",
         "n_resolved": "", "scientific_caveat": "", "audit_status": "NOT_TESTABLE", "remaining_action": ""},
    ]).set_index("claim_id")
    refs = pd.DataFrame([
        {"cite_key": "arcement1989", "doi": "10.3133/wsp2339", "title": "Guide", "authors": "Arcement, G.; Schneider, V.",
         "year": "1989", "journal": "WSP", "source": "doi", "resolved": "True", "note": ""},
        {"cite_key": "ghost2020", "doi": "10.1/x", "title": "", "authors": "", "year": "", "journal": "",
         "source": "doi", "resolved": "False", "note": "unresolved"},
        {"cite_key": "EPSG_9902", "doi": "", "title": "", "authors": "", "year": "", "journal": "",
         "source": "technical", "resolved": "True", "note": "IOGP EPSG 9902"},
    ]).set_index("cite_key")
    return {"evidence": ev, "references": refs, "section_7_8": "## 7.8 stub\n\n> **[PENDING OPEN41]** x\n"}


def test_claim_and_cite_placeholders_render():
    used, cited = set(), set()
    text = "# 6.3 Slope\n\nSlope: {{claim:M1.1}} ({{claim:M1.1.unc}}; n = {{claim:M1.1.n}}) {{cite:arcement1989}} {{cite:EPSG_9902}}."
    out = asm.render_template(text, _inputs(), used, cited)
    assert "+0.09 → +3.31 cm/km ([+1.99, +5.07]; n = 14/14) (Arcement & Schneider, 1989) (EPSG_9902)." in out
    assert used == {"M1.1"} and cited == {"arcement1989", "EPSG_9902"}


def test_blocked_claim_replaces_the_paragraph_with_a_marker():
    out = asm.render_template("# 6.6\n\nFragmentation rose {{claim:C-02}} strongly.", _inputs(), set(), set())
    ids = markers.find_marker_ids(out)
    assert len(ids) == 1 and "Fragmentation rose" not in out
    assert "backs: C-02" in out


def test_not_testable_only_in_limitation_context():
    limited = "# 7.10 Limitations\n\n{{claim:C-14}}."
    assert "n_POST_with_Q = 0." in asm.render_template(limited, _inputs(), set(), set())
    finding = "# 6.3 Results\n\nWe find {{claim:C-14}}."
    assert markers.find_marker_ids(asm.render_template(finding, _inputs(), set(), set()))


def test_unresolved_citation_becomes_marker_and_unknown_claim_raises():
    out = asm.render_template("# x\n\nsee {{cite:ghost2020}}", _inputs(), set(), set())
    assert markers.find_marker_ids(out)
    with pytest.raises(asm.AssemblyError):
        asm.render_template("# x\n\n{{claim:ZZ.9}}", _inputs(), set(), set())


def test_forbidden_priority_wording_is_rejected_but_first_season_is_fine():
    with pytest.raises(asm.AssemblyError):
        asm.render_template("# x\n\nWe are the first to show this.", _inputs(), set(), set())
    assert "first season" in asm.render_template("# x\n\nIn the first season.", _inputs(), set(), set())


def test_section_7_8_is_inserted_and_open_items_parse():
    out = asm.render_template("# 7.8\n\n{{section:7_8}}", _inputs(), set(), set())
    assert "PENDING OPEN41" in out
    items = asm.open_items(out)
    assert "OPEN41" in items


def test_shipped_templates_reference_only_known_placeholders():
    import re
    for p in [t for paper in asm.PAPERS for t in asm.template_order(asm.template_dir(paper))]:
        for m in asm._PH.finditer(p.read_text(encoding="utf-8")):
            assert m.group(1) in ("claim", "cite", "section", "table", "pending"), p.name
        assert not re.search(r"\{\{(?!claim:|cite:|section:|table:|pending:)", p.read_text(encoding="utf-8")), p.name


# ── tables print science, not the claim registry (2026-09-21) ────────────────

def test_table_prints_row_labels_and_hides_internal_fields():
    """A results table with columns claim_id / audit_status is an internal QA
    artefact. The reader gets the name of the quantity; traceability lives in
    the supplementary registry."""
    asm.TABLE_SPECS["_t"] = {
        "title": "Table X.", "note": "a note",
        "columns": [("Step", "__label__"), ("Result", "value_resolved")],
        "rows": [("Slope, footprint-wide", "M1.1"), ("Absent claim", "ZZ.9")],
    }
    try:
        used = set()
        out = asm.render_table("_t", _inputs()["evidence"], used)
        assert "| Step | Result |" in out
        assert "| Slope, footprint-wide | +0.09 → +3.31 cm/km |" in out
        assert "claim_id" not in out and "audit_status" not in out and "M1.1 |" not in out
        assert "Absent claim" not in out          # a missing claim drops its row
        assert "*a note*" in out
        assert used == {"M1.1"}
    finally:
        del asm.TABLE_SPECS["_t"]


def test_every_shipped_table_row_resolves_to_a_real_claim():
    import pandas as pd
    from src.paper_3._utils import OUT_DIR
    ev_path = OUT_DIR / "OWN_EVIDENCE.csv"
    if not ev_path.exists():
        pytest.skip("OWN_EVIDENCE not built")
    ev = pd.read_csv(ev_path, dtype=str, keep_default_na=False).set_index("claim_id")
    for tid, spec in asm.TABLE_SPECS.items():
        missing = [c for _, c in spec["rows"] if c not in ev.index]
        assert not missing, f"{tid} references unknown claims: {missing}"
        assert spec["columns"][0][1] == "__label__", f"{tid} must lead with the row label"
