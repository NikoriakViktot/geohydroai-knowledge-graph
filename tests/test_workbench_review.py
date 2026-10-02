"""Review engine (rules as data), anchored revisions, and the reproduction of the Paper 3 audit checks."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from src.workbench.decommission import DEFAULT_NAME, FROZEN_DIR
from src.workbench.review import engine as E
from src.workbench.review import revise as R

FIXTURE = Path(__file__).parent / "fixtures" / "workbench" / "review_rules_floodstate_paper3.yaml"
FREEZE = FROZEN_DIR / DEFAULT_NAME


def run(rules: list[dict], files: dict[str, str], tables: dict[str, str] | None = None) -> list[E.Finding]:
    doc = {"schema": "ghai.review_rules/v1",
           "files": {name: {"path": f"f/{name}", "label": f"{name}.md"} for name in files},
           "tables": {name: {"path": f"t/{name}", "label": f"tables/{name}.csv"} for name in (tables or {})},
           "rules": rules}
    store = {f"f/{k}": v for k, v in files.items()} | {f"t/{k}": v for k, v in (tables or {}).items()}
    return E.run(doc, store.get)


def test_forbidden_phrases_survive_line_breaks_and_rank_by_file():
    text = "Intro.\nThe physical\nreconstruction was done.\n"
    out = run([{"id": "C", "type": "forbidden", "in": ["ms", "notes"], "major_in": ["ms"],
                "phrases": ["physical reconstruction"], "observed": "forbidden phrase '{phrase}'",
                "split_note": " (split)"}], {"ms": text, "notes": "physical reconstruction"})
    assert [(f.severity, f.location, f.observed) for f in out] == [
        ("MAJOR", "ms.md:2", "forbidden phrase 'physical reconstruction' (split)"),
        ("MINOR", "notes.md:1", "forbidden phrase 'physical reconstruction'")]


def test_near_cases_sections_and_skip_before():
    ms = "# Abstract\nVolume 566 hm³ (p05 500).\n# Results\nVolume 400 hm³ alone. Range 300–450 hm³ here.\n"
    rule = {"id": "B", "type": "near", "in": "ms", "pattern": r"\b(\d{2,4})\s*hm³", "skip_if_before": r"[–-]\s*$",
            "window": 25, "cases": [
                {"missing": ["p05", "interval"], "severity": "MINOR", "observed": "'{match}' without interval"},
                {"present": ["p05"], "missing": ["median"], "severity": "MINOR",
                 "severity_in_section": {"abstract": "MAJOR"}, "observed": "'{match}' without median"}]}
    out = run([rule], {"ms": ms})               # 450 hm³ closes the range 300–450: skipped
    assert [(f.severity, f.observed) for f in out] == [("MAJOR", "'566 hm³' without median"),
                                                     ("MINOR", "'400 hm³' without interval")]


def test_pattern_requirements_cooccurrence_variants_absent():
    ms = "The peak by about a third. About 620 km² flooded. Gaps −9 %, −14 %, 9–14 %, −20 %."
    rules = [
        {"id": "E", "type": "pattern", "in": "ms", "pattern": r"\bthe peak\b(?!\s+stage)", "after_chars": 6,
         "observed": "bare '{match_plus}…'"},
        {"id": "H", "type": "requirements", "in": "ms", "values": ["620"], "pattern": r"{value}\s*km²", "window": 30,
         "require": [{"name": "temporal", "any": "snapshot|cumulative"}, {"name": "land", "any": "land"}],
         "observed": "'{match}' without: {missing}"},
        {"id": "F", "type": "co_occurrence", "all": [{"in": "ms", "contains": "third"}, {"in": "claims", "contains": "x"}],
         "location": "ms vs claims", "observed": "inconsistent"},
        {"id": "G", "type": "variants", "in": ["ms"], "pattern": r"[-−–]?\s?\d{1,2}\s?%", "more_than": 3,
         "location": "ms", "observed": "{variants}"},
        {"id": "H", "type": "absent", "in": "ms", "none_of": ["410", "420"], "severity": "INFO", "observed": "missing"},
    ]
    out = {(f.check_id, f.observed) for f in run(rules, {"ms": ms, "claims": "x"})}
    assert ("E", "bare 'The peak by ab…'") not in out                      # case-sensitive pattern
    assert ("H", "'620 km²' without: temporal, land") in out
    assert ("F", "inconsistent") in out and ("H", "missing") in out
    assert any(c == "G" for c, _ in out)


def test_table_rules():
    t12 = ("date,region,A_p50_km2,A_p05_km2,A_p95_km2,A_central_km2,definition_note\n"
           "2023-06-07,DNIPRO_CORRIDOR,247,238,255,235,comparable with operational products\n")
    t16 = "quantity,area_semantics,verify\nNASA,literature_reported,\nOwn,observed_S1,\n"
    ms = "We map 235 km² (p05–p95 238–255 km²) at the maximum.\n"
    rules = [
        {"id": "A", "type": "table_interval", "table": "T12",
         "row": [{"columns": ["date"], "contains": "2023-06-07"}, {"column": "region", "contains": "corridor"}],
         "missing_row": {"severity": "INFO", "observed": "no row"}, "location": "T12",
         "quantities": [{"name": "A_new", "unit": "km²", "columns": ["a_"], "quote_severity": "MAJOR"}],
         "outside": {"severity": "MAJOR", "observed": "{name} {central:.0f} outside {p05:.0f}–{p95:.0f}"},
         "quoted_in": "ms", "quote": {"window": 80, "interval": ["p05"], "median": ["median"],
                                       "observed": "'{match}' without median {p50:.0f}"}},
        {"id": "H", "type": "table_rows", "table": "T16", "where": [{"column": "area_semantics", "contains": "literature"}],
         "required": "verify", "observed": "row '{row[quantity]}' unverified"},
        {"id": "H", "type": "table_text", "table": "T12", "column": "definition_note", "rows": "first",
         "contains": "comparable with operational", "location": "T12 note", "observed": "note"},
    ]
    out = [(f.check_id, f.severity, f.observed) for f in run(rules, {"ms": ms}, {"T12": t12, "T16": t16})]
    assert ("A", "MAJOR", "A_new 235 outside 238–255") in out
    assert ("A", "MAJOR", "'235 km²' without median 247") in out
    assert ("H", "MINOR", "row 'NASA' unverified") in out and ("H", "MINOR", "note") in out


def test_rules_must_declare_their_schema_and_known_types():
    with pytest.raises(ValueError):
        E.run({"rules": []}, lambda p: None)
    with pytest.raises(ValueError):
        E.run({"schema": "ghai.review_rules/v1", "rules": [{"id": "X", "type": "magic"}]}, lambda p: None)


@pytest.mark.skipif(not FREEZE.exists(), reason="needs the P6 freeze (data/frozen, local only)")
def test_floodstate_rules_reproduce_the_audit_checks_of_2026_09_28():
    """tools/paper3_audit/checks.py found 8 findings on the bundle; the rules as data find the same 8."""
    def read(path):
        pub = "case_studies/kakhovka_2023/publication/"
        if path.startswith(pub):
            p = FREEZE / "paper_unet-case-kakhovka/publication" / path[len(pub):]
        elif path == "tests/test_terminology_freeze.py":
            p = FREEZE / "paper_unet-case-kakhovka/literature_audit_paper3/_work/test_terminology_freeze.py"
        else:
            return None
        return p.read_text(encoding="utf-8") if p.exists() else None
    doc = yaml.safe_load(FIXTURE.read_text(encoding="utf-8"))
    got = E.as_dicts(E.run(doc, read))
    want = json.loads((FREEZE / "paper_unet-case-kakhovka/literature_audit_paper3/_work/checks_findings.json")
                      .read_text(encoding="utf-8"))
    assert got == want


def test_revisions_apply_only_documented_unique_edits():
    texts = {"m.md": "Alpha beta\ngamma. Delta."}
    new, log = R.apply([{"id": "R1", "change_type": "terminology", "original": "beta gamma", "revised": "BETA GAMMA"},
                        {"id": "R2", "change_type": "citation", "insert_after": "Delta.", "revised": "Added."}],
                       texts, "m.md")
    assert new["m.md"] == "Alpha BETA GAMMA. Delta.\n\nAdded." and [e["id"] for e in log] == ["R1", "R2"]
    with pytest.raises(ValueError, match="not found"):
        R.apply([{"id": "R3", "change_type": "stylistic", "original": "omega", "revised": "x"}], texts, "m.md")
    with pytest.raises(ValueError, match="change_type"):
        R.apply([{"id": "R4", "change_type": "other", "original": "Alpha", "revised": "x"}], texts, "m.md")
    assert "BETA GAMMA" in R.render_log(log, "Change log")
