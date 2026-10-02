"""Manuscript assembly: the ghai and floodstate_fill dialects, numbering, the final gate, the golden Paper 1 port."""

from __future__ import annotations

import difflib
import io
import json
import shutil

import pandas as pd
import pytest

from src.workbench import steps
from src.workbench.assemble import fill as F
from src.workbench.assemble import markers
from src.workbench.assemble.render import AssemblyError, Assembler, Inputs, parse_captions
from src.workbench.decommission import DEFAULT_NAME, FROZEN_DIR
from src.workbench.remote import LocalRunner, RemoteRepo
from src.workbench.steps import assemble as step

FREEZE = FROZEN_DIR / DEFAULT_NAME

EVIDENCE = """claim_id,claim_text,value_resolved,n_resolved,uncertainty_resolved,scientific_caveat,audit_status,remaining_action
M1,Slope rose,0.090 → 3.314 cm/km,41,± 0.2,,SUPPORTED,
M2,Gauge drift,0.05 m,6,,,REVISE_UNCERTAINTY,re-run
M3,Unclear,,,,,NOT_SUPPORTED,collect data
"""
REFERENCES = """cite_key,doi,title,authors,cite_as,year,journal,source,resolved,resolution_method,url,note
smith2024,10.1/a,Title A,"Smith, John; Doe, Jane",,2024,J A,crossref,True,doi,,
nasa,,Doc,NASA JPL,JPL D-1,,Rev C,technical,True,url,,NASA JPL. Doc. Rev C.
lost,,Lost,X,,2020,J,crossref,False,none,,no DOI found
"""
SPECS = {"T9": {"title": "Table 5. Closure offsets.", "columns": [["Quantity", "__label__"], ["Value", "value_resolved"]],
                "rows": [["Slope", "M1"]], "note": "Medians."}}


def inputs(**extra) -> Inputs:
    ev = pd.read_csv(io.StringIO(EVIDENCE), dtype=str, keep_default_na=False).set_index("claim_id")
    refs = pd.read_csv(io.StringIO(REFERENCES), dtype=str, keep_default_na=False).set_index("cite_key")
    return Inputs(evidence=ev, references=refs, table_specs=SPECS, **extra)


def test_claims_citations_and_the_paragraph_rules():
    a = Assembler(inputs(), number_tables=False)
    out = a.template("# Results\n\nSlope {{claim:M1}} (n = {{claim:M1.n}}) {{cite:smith2024,nasa}}.\n\n"
                     "Drift {{claim:M2}} is uncertain.\n\n# Limitations\n\nDrift {{claim:M2}} remains.\n\n"
                     "Unclear {{claim:M3}}.\n\nAs {{cite:lost}} says.")
    assert "Slope 0.090 → 3.314 cm/km (n = 41) (Smith & Doe, 2024; JPL D-1)." in out
    assert "M2 is REVISE_UNCERTAINTY: may appear only as a limitation" in out
    assert "Drift 0.05 m remains." in out
    assert "paragraph withheld: M3 is NOT_SUPPORTED" in out
    assert "unresolved reference lost" in out
    assert {"M1", "M2"} <= a.used and a.cited == {"smith2024", "nasa"}
    with pytest.raises(AssemblyError, match="novelty"):
        a.template("# Intro\n\nA novel method {{claim:M1}}.")
    with pytest.raises(AssemblyError, match="unknown claim"):
        a.template("# Intro\n\n{{claim:NOPE}}")


def test_tables_and_figures_are_numbered_by_first_appearance():
    caps = parse_captions("**F2 Second figure.** It shows B.\n\n**F1 First figure.** It shows A.\n\n**T2 Areas.** km².")
    a = Assembler(inputs(table_csv=lambda t: "region,km2\nA,1\n" if t == "T2" else None, captions=caps,
                         figures={"F1": "figures/F1_a.png", "F2": "figures/F2_b.png"}))
    text = a.assemble([("10.md", "# A\n\nSee {{ref:table:T9}} and {{ref:figure:F2}}.\n\n{{figure:F2}}\n\n{{table:T2}}"),
                       ("20.md", "# B\n\n{{table:T9}}\n\nAs Table 5 shows.\n\n{{figure:F1}}")])
    assert "See Table 2 and Figure 1." in text
    assert "**Table 2. Closure offsets.**" in text and "**Table 1.** Areas. km²." in text
    assert "![Figure 1](figures/F2_b.png)" in text and "**Figure 2.** First figure. It shows A." in text
    assert any("'Table 5' typed by hand" in w for w in a.warnings)
    missing = Assembler(inputs()).assemble([("x.md", "# A\n\n{{table:T7}}\n\n{{figure:F9}}")])
    assert set(markers.find_marker_ids(missing)) and "no spec and no tables/T7.csv" in missing


def test_bib_citations_use_the_bib_entries():
    bib = {"Ivanenko_2021": {"key": "Ivanenko_2021", "fields": {"author": "Іваненко, Петро and Smith, A.", "year": "2021"}}}
    a = Assembler(Inputs(bib=bib))
    assert a.cite("Ivanenko_2021") == "(Ivanenko & Smith, 2021)"
    assert "not in the .bib" in a.cite("Nobody_1900")


def test_fill_dialect_resolves_cells_or_says_missing():
    tables = {"T12": "region,date,A_km2,d\nC,2023-06-07,234.6,-13.8\nC,2023-06-08,200,1\n"}
    text, missing, n = F.fill("A {{T12|region=C,date=2023-06-07|A_km2||.0f}} km², fell by {{T12|region=C|d|min|neg.1f}}; "
                              "{{T12|region=C|A_km2|count|}} rows; {{T12|region=Z|A_km2||.0f}}; {{T99|a=b|c||}}",
                              tables.get)
    assert text.startswith("A 235 km², fell by 13.8; 2 rows; [[MISSING: {{T12|region=Z|A_km2||.0f}}]]")
    assert n == 5 and len(missing) == 2 and "<!-- UNRESOLVED PLACEHOLDERS -->" in text


@pytest.mark.skipif(not FREEZE.exists(), reason="needs the P6 freeze (data/frozen, local only)")
def test_golden_paper1_identical_without_numbering_and_only_renumbered_with_it(tmp_path):
    A = pytest.importorskip("src.paper_3.v2.assemble")      # the original, until src/paper_3 is retired
    for name in ("OWN_EVIDENCE.csv", "REFERENCES.csv", "SECTION_7_8_STUB.md"):
        shutil.copy(FREEZE / "data/paper_3_audit" / name, tmp_path / name)
        (tmp_path / name).chmod(0o644)
    original = A.assemble(out_dir=tmp_path, paper=1).read_text(encoding="utf-8")
    ev = pd.read_csv(tmp_path / "OWN_EVIDENCE.csv", dtype=str, keep_default_na=False).set_index("claim_id")
    refs = pd.read_csv(tmp_path / "REFERENCES.csv", dtype=str, keep_default_na=False).set_index("cite_key")
    ins = Inputs(evidence=ev, references=refs, table_specs=A.TABLE_SPECS,
                 sections={"7_8": (tmp_path / "SECTION_7_8_STUB.md").read_text(encoding="utf-8")})
    templates = [(p.name, p.read_text(encoding="utf-8")) for p in A.template_order(A.template_dir(1))]
    plain = Assembler(ins, number_tables=False, number_figures=False)
    assert plain.assemble(templates) + "\n" + plain.references_csv_section() == original
    numbered = Assembler(ins)
    text = numbered.assemble(templates) + "\n" + numbered.references_csv_section()
    changed = [l for l in difflib.ndiff(original.splitlines(), text.splitlines()) if l[:2] in ("- ", "+ ")]
    assert changed and all(l[2:].startswith("**Table ") for l in changed)


def test_assemble_step_stages_the_manuscript_and_the_final_gate_refuses_markers(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    (repo / "pub" / "templates").mkdir(parents=True)
    (repo / "pub" / "ghai.project.yaml").write_text(
        "schema: ghai.project/v1\nproject_id: t:p\nrepo: {path: /x}\npublication_dir: pub\n"
        "paths: {manuscript_templates: pub/templates/, manuscript_out: 'pub/ms_{lang}.md', own_evidence: pub/ev.csv}\n"
        "assembly: {references_csv: pub/refs.csv, table_specs: pub/specs.yaml}\n", encoding="utf-8")
    (repo / "pub" / "ev.csv").write_text(EVIDENCE, encoding="utf-8")
    (repo / "pub" / "refs.csv").write_text(REFERENCES, encoding="utf-8")
    (repo / "pub" / "specs.yaml").write_text(json.dumps(SPECS), encoding="utf-8")
    (repo / "pub" / "templates" / "10_results.md").write_text("# Results\n\nSlope {{claim:M1}} {{cite:smith2024}}.\n\n"
                                                              "{{table:T9}}\n\nUnclear {{claim:M3}}.\n", encoding="utf-8")
    monkeypatch.setattr(steps, "WORKBENCH_DIR", tmp_path / "wb")
    ctx = steps.Context("t:p", {"project_id": "t:p", "repo_path": str(repo), "distro": "local", "publication_dir": "pub",
                                "manifest_path": "pub/ghai.project.yaml", "public": True},
                        RemoteRepo("local", str(repo), runner=LocalRunner()), local_target=True)
    assert step.run(ctx, final=True) == 3
    assert not (steps.project_dir("t:p") / "out/pub/ms_en.md").exists()
    assert step.run(ctx) == 0
    out = steps.project_dir("t:p") / "out/pub"
    text = (out / "ms_en.md").read_text(encoding="utf-8")
    assert "Slope 0.090 → 3.314 cm/km (Smith & Doe, 2024)." in text and "**Table 1. Closure offsets.**" in text
    assert "# REFERENCES" in text and "- [smith2024] Smith, John; Doe, Jane (2024)" in text
    assert "1 marker(s) remain" in (out / "OPEN_ITEMS.md").read_text(encoding="utf-8")
    assert (out / "private" / "ms_en.docx").read_bytes()[:2] == b"PK"
    assert json.loads((out / "assembly_report.json").read_text())["markers"]


def test_translation_locks_numbers_and_keeps_rejected_paragraphs_in_english():
    from src.workbench.assemble import translate as T
    masked, spans = T.lock("Slope 3.314 cm/km (Smith et al., 2024), doi 10.1029/2024GL1.")
    assert "3.314" not in masked and T.unlock(masked, spans).endswith("doi 10.1029/2024GL1.")
    good = lambda prompt: prompt.split("PARAGRAPH:\n", 1)[1].replace("Slope", "Нахил")
    bad = lambda prompt: "Нахил без чисел"
    text, failed = T.translate_text("Slope 3.314 cm/km.\n\n# Head\n\n| a | b |", {}, good, delay=0)
    assert text.startswith("Нахил 3.314 cm/km.") and "# Head" in text and failed == []
    text, failed = T.translate_text("Slope 3.314 cm/km.", {}, bad, delay=0)
    assert failed == [0] and "translation rejected" in text and "Slope 3.314 cm/km." in text
