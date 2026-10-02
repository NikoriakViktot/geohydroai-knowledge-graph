"""The literature evidence run (src/workbench/literature, ported from tools/paper3_audit) — stubs only:
no Chroma, no Neo4j, no network, no data/. Parity with the original tool on the frozen bundle at the end."""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from src.workbench.literature import claims as claims_mod, corpus, export, references, retrieve, screen
from src.workbench.literature.bibtex import parse_bib
from src.workbench.literature.claims import AtomicClaim, ThesisRef, ThesisRow


def _thesis(tid="TH-INT-06", priority="high", category="method"):
    return ThesisRow(id=tid, section="1 Introduction", category=category, priority=priority, tables=("T14",),
                     thesis="…", needs="…", refs=(ThesisRef("Zhao_2021", "SUPPORTED_BY", "verified", False),
                                                  ThesisRef("Tsyganskaya_2018", "NEEDS_SOURCE", "missing", True)),
                     search_queries=("SAR flood look-alikes", "flooded vegetation C-band"))


def _claim(tid="TH-INT-06", aid="TH-INT-06.A", primary=("flooded vegetation",), roles=("SUPPORTS",), counter=("x",)):
    t = _thesis(tid)
    return AtomicClaim(thesis_id=tid, atomic_id=aid, statement="SAR misses water under vegetation",
                       required_roles=tuple(roles), key_terms=(tuple(primary), ("sar", "radar"), ("flood",)),
                       negative_terms=(), queries=tuple((q, "original") for q in t.search_queries) + tuple((q, "counter") for q in counter),
                       thesis=t)


# ── claims ────────────────────────────────────────────────────────────────────

def test_validate_rejects_generic_primary_and_missing_thesis():
    t = _thesis()
    bad = _claim(primary=("satellite",))
    problems = claims_mod.validate([t, _thesis("TH-INT-07")], [bad])
    assert any("generic primary" in p for p in problems)
    assert any("TH-INT-07: no atomic claim" in p for p in problems)


def test_validate_requires_counter_queries_only_for_high_supports_claims():
    ok_method = _claim(roles=("METHOD_FROM",), counter=())
    assert not [p for p in claims_mod.validate([_thesis()], [ok_method]) if "counterevidence" in p]
    bad = _claim(roles=("SUPPORTS",), counter=())
    assert any("counterevidence" in p for p in claims_mod.validate([_thesis()], [bad]))


def test_to_thesis_keeps_primary_family_first_and_query_kinds():
    c = _claim()
    th = c.to_thesis()
    assert th.primary_family == ("flooded vegetation",)
    assert th.search_queries[:2] == c.thesis.search_queries
    assert c.counter_queries == ("x",)
    assert claims_mod.queries_sha256([c]) == claims_mod.queries_sha256([c])


def test_load_atomic_claims_appends_never_replaces_original_queries(tmp_path):
    y = tmp_path / "a.yaml"
    y.write_text(json.dumps({"claims": [{"thesis_id": "TH-INT-06", "atomic_id": "TH-INT-06.A", "statement": "s",
                                         "required_roles": ["SUPPORTS"], "key_terms_primary": ["flooded vegetation"],
                                         "key_terms_support": [["sar"]], "extra_queries": ["extra q"],
                                         "counterevidence_queries": ["counter q"]}]}))
    cl, nq, meta = claims_mod.load_atomic_claims(y, theses=[_thesis()])
    kinds = [k for _, k in cl[0].queries]
    assert kinds == ["original", "original", "extra", "counter"]


# ── corpus / bib ──────────────────────────────────────────────────────────────

def test_clean_doi_strips_trailing_year_and_punctuation():
    assert corpus.clean_doi("https://doi.org/10.1038/s41559-024-02373-0(2024).") == "10.1038/s41559-024-02373-0"
    assert corpus.openalex_short("https://openalex.org/W4404133645") == "W4404133645"


def _index():
    return pd.DataFrame([
        {"paper_id": "a", "doi_clean": "10.1/a", "title": "Flood mapping under vegetation using single SAR acquisitions", "title_norm": "flood mapping under vegetation using single sar acquisitions", "duplicate_of": ""},
        {"paper_id": "b_dup", "doi_clean": "10.1/a", "title": "Flood mapping under vegetation using single SAR acquisitions", "title_norm": "flood mapping under vegetation using single sar acquisitions", "duplicate_of": "a"},
        {"paper_id": "c", "doi_clean": "", "title": "Other", "title_norm": "other", "duplicate_of": ""},
    ])


def test_resolver_maps_duplicates_filenames_and_titles():
    r = corpus.PaperResolver(_index())
    assert r.from_hit("b_dup") == "a"
    assert r.from_hit("c.tei.paper.json") == "c"
    assert r.from_doi("https://doi.org/10.1/A") == "a"
    assert r.from_title("Flood mapping under vegetation using single SAR acquisition") == "a"
    assert r.from_hit("nope") is None and r.unmapped == ["nope"]


def test_parse_bib_nested_braces_and_doi_from_url():
    text = '@article{K_2020, title={A {nested} title}, url={http://dx.doi.org/10.1/X}, year={2020}}\n@misc{U, title={x}}'
    es = parse_bib(text)
    assert [e["key"] for e in es] == ["K_2020", "U"]
    assert es[0]["fields"]["title"] == "A {nested} title"
    from src.workbench.literature.bibtex import bib_doi
    assert bib_doi(es[0]) == "10.1/X"


# ── retrieve ──────────────────────────────────────────────────────────────────

class FakeStore:
    def query(self, vec, top_k=10, where=None):
        return [{"chunk_id": "c1", "paper_id": "a", "page": 3, "section_title": "Results", "distance": 0.05, "text": "t1"},
                {"chunk_id": "c2", "paper_id": "b_dup", "page": 4, "section_title": "Methods", "distance": 0.08, "text": "t2"},
                {"chunk_id": "c3", "paper_id": "ghost.pdf", "page": 1, "section_title": "", "distance": 0.5, "text": "t3"}]


class FakeEmb:
    def embed_query(self, q):
        return [0.0]


def test_route_semantic_keeps_chunk_ids_and_maps_duplicates():
    c = _claim()
    res = corpus.PaperResolver(_index())
    by_paper, hits, stats = retrieve.route_semantic(c, FakeStore(), FakeEmb(), res, top_k=3)
    assert set(by_paper) == {"a"}                       # b_dup → a, ghost unmapped
    assert by_paper["a"]["n_chunks"] == 2 and by_paper["a"]["best"][0][1] == "c1"
    assert stats["n_hits"] == 9 and "ghost.pdf" in res.unmapped


def test_route_kg_reports_unavailable_when_neo4j_silent():
    res = corpus.PaperResolver(_index())
    rows, stats = retrieve.route_kg(["a"], available=False, resolver=res)
    assert rows == [] and stats["status"] == "route_unavailable"
    rows, stats = retrieve.route_kg(["a"], available=True, resolver=res, runner=lambda cy, **p: [{"paper_id": "c", "via": "USES_SENSOR", "n_shared": 3}])
    assert rows[0]["paper_id"] == "c" and stats["status"] == "ok"


def test_citation_graph_uses_openalex_edges():
    oa = pd.DataFrame({"openalex_id": ["W1", "W2"], "paper_id": ["a", "c"]})
    edges = pd.DataFrame({"paper_id": ["a"], "referenced_openalex_id": ["W2"]})
    g = retrieve.CitationGraph(oa, edges)
    rows, stats = g.expand(["a"])
    assert rows[0]["paper_id"] == "c" and rows[0]["relation"] == "references"
    rows, _ = g.expand(["c"])
    assert rows[0]["paper_id"] == "a" and rows[0]["relation"] == "cited_by"


def test_assemble_exact_always_passes_and_below_gate_recorded():
    c = _claim()
    ft = lambda thesis, pid: (3, {}, False, False)
    rows = retrieve.assemble(c, exact={"a": {"key": "Zhao_2021", "relation": "SUPPORTED_BY", "status": "verified", "how": "exact_doi"}},
                             semantic={"c": {"paper_id": "c", "n_chunks": 1, "best_distance": 0.9, "best": [(0.9, "c9", 2, "Intro")], "from_counter": True}},
                             citation=[], kg=[], index_by_pid={"a": {"title": "A"}, "c": {"title": "C"}}, fulltext=ft)
    by = {r["paper_id"]: r for r in rows}
    assert by["a"]["prefilter_pass"] and by["a"]["stage_found"] == "exact"
    assert not by["c"]["prefilter_pass"] and by["c"]["stage_found"] == "semantic_below_gate" and by["c"]["from_counter_query"]


def test_select_for_screening_quota_exact_and_counter_reserve():
    c = _claim()
    rows = []
    for i in range(20):
        rows.append({"atomic_claim_id": c.atomic_id, "paper_id": f"p{i}", "prefilter_pass": True, "routes": "exact" if i == 0 else "semantic",
                     "from_counter_query": i in (18, 19), "semantic_best_distance": 0.06 + i * 0.003, "citation_relation": None,
                     "kg_relation": None, "n_keyterm_families_hit": 3, "priority": "high"})
    frame = pd.DataFrame(rows)
    out = retrieve.select_for_screening(frame, [c], quota={"high": 5}, loader=lambda pid: "flooded vegetation " * (int(pid[1:]) + 1))
    sel = out[out.screen_selected]
    assert len(sel) == 5 and "p0" in set(sel.paper_id) and {"p18", "p19"} <= set(sel.paper_id)


# ── screen ────────────────────────────────────────────────────────────────────

def test_prompt_lists_contrasts_first_and_all_roles():
    from src.paper_3.evidence import EvidencePassage
    p = [EvidencePassage("P1", "a", "some text", "results", 0, "a.json")]
    prompt = screen.build_prompt(_claim(), {"title": "T"}, p)
    assert prompt.index("CONTRASTS") < prompt.index("SUPPORTS")
    for role in screen.ROLES:
        assert role in prompt
    assert "asked whether the paper confirms" in " ".join(prompt.split())


def _raw(role, quote, passage="The SAR could not detect water under the trees in this test."):
    return {"atomic_claim_id": "TH-INT-06.A", "thesis_id": "TH-INT-06", "paper_id": "a", "model": "m", "lane": "m",
            "raw_response": json.dumps({"role": role, "confidence": 0.9, "passage_id": "P1", "evidence_quote": quote,
                                        "rationale": "r", "is_own_result": True, "quantity": None}),
            "passages_offered": [{"passage_id": "P1", "text": passage, "section": "results", "char_offset": 0,
                                  "source_file": "a.json", "chunk_id": "c1", "page": 7}]}


def test_finalize_rejects_supports_without_quote_keeps_method_from():
    row, rej = screen.finalize_screen_row(_raw("SUPPORTS", "totally invented sentence about nothing"))
    assert row["role"] == "NOT_RELEVANT" and rej["reason"] and "without verified quote" in row["demotion_reason"]
    row, rej = screen.finalize_screen_row(_raw("SUPPORTS", "could not detect water under the trees"))
    assert row["role"] == "SUPPORTS" and row["quote_verified"] and row["quote_chunk_id"] == "c1" and row["quote_page"] == 7 and rej is None
    row, rej = screen.finalize_screen_row(_raw("METHOD_FROM", ""))
    assert row["role"] == "METHOD_FROM" and rej is None


def test_finalize_demotes_comparator_without_quantity_or_quote():
    row, _ = screen.finalize_screen_row(_raw("COMPARATOR", "nonsense"))
    assert row["role"] == "BACKGROUND" and "COMPARATOR without" in row["demotion_reason"]


def test_quota_ledger_stops_at_cap(tmp_path):
    q = screen.Quota(tmp_path / "q.json", cap=2)
    q.record("m"); q.record("m")
    assert not q.allow("m") and screen.Quota(tmp_path / "q.json", cap=2).used("m") == 2


def test_pin_lane_sets_single_model_pool():
    from src.dashboard_dash import ai_gateway
    before = list(ai_gateway.MODEL_POOL)
    try:
        screen.pin_lane("gemini-3.5-flash-lite")
        assert ai_gateway.MODEL_POOL == ["gemini-3.5-flash-lite"]
    finally:
        ai_gateway.MODEL_POOL[:] = before


# ── checks ────────────────────────────────────────────────────────────────────

def _judged(rows):
    return pd.DataFrame(rows, columns=["role", "quote_verified", "paper_id"])


def test_status_derivation_table():
    c = _claim(roles=("SUPPORTS", "LIMITATION"))
    assert export.derive_status(c, _judged([]), set())[0] == "NO_EVIDENCE_IN_CORPUS"
    assert export.derive_status(c, _judged([]), {"Zhao_2021"})[0] == "SOURCE_FOUND_METADATA_UNVERIFIED"
    assert export.derive_status(c, _judged([("SUPPORTS", True, "a"), ("LIMITATION", False, "b")]), set())[0] == "VERIFIED_SUPPORTED"
    assert export.derive_status(c, _judged([("SUPPORTS", True, "a")]), set())[0] == "VERIFIED_PARTIAL"
    assert export.derive_status(c, _judged([("SUPPORTS", False, "a")]), set())[0] == "NO_EVIDENCE_IN_CORPUS"
    assert export.derive_status(c, _judged([("COMPARATOR", False, "a")]), set())[0] == "VERIFIED_COMPARATOR_ONLY"
    assert export.derive_status(c, _judged([("CONTRASTS", True, "a"), ("SUPPORTS", True, "b")]), set())[0] == "CONTRADICTED_OR_QUALIFIED"
    assert export.thesis_status(["VERIFIED_SUPPORTED", "NO_EVIDENCE_IN_CORPUS"]) == "NO_EVIDENCE_IN_CORPUS"
    assert export.thesis_status(["NOT_NEEDED_FOR_MANUSCRIPT"]) == "NOT_NEEDED_FOR_MANUSCRIPT"


def test_overrides_require_justification(tmp_path):
    p = tmp_path / "o.yaml"; p.write_text("overrides:\n  - atomic_claim_id: TH-INT-06.A\n    status: VERIFIED_SUPPORTED\n")
    with pytest.raises(ValueError):
        export.load_overrides(p)


# ── references (no network) ───────────────────────────────────────────────────

def test_verify_all_offline_paths(tmp_path):
    entries = {"Zhao_2021": {"key": "Zhao_2021", "type": "article", "fields": {"title": "Flood mapping under vegetation using single SAR acquisitions", "year": "2020", "doi": "10.1/a"}},
               "Otsu_1979": {"key": "Otsu_1979", "type": "article", "fields": {"title": "A threshold selection method", "year": "1979"}},
               "U": {"key": "U", "type": "misc", "fields": {"title": "product", "howpublished": "https://example.org/x"}}}
    res = corpus.PaperResolver(_index())
    recs = {"10.1/a": {"title": "Flood mapping under vegetation using single SAR acquisitions", "year": "2020", "journal": "RSE",
                       "authors": [{"family": "Grimaldi", "given": "S."}], "volume": "237", "issue": "", "pages": "111582", "article_number": "", "type": "journal-article", "publisher": "", "url": ""},
            "10.1/otsu": {"title": "A threshold selection method", "year": "1979", "journal": "IEEE TSMC",
                          "authors": [{"family": "Otsu", "given": "N."}], "volume": "9", "issue": "1", "pages": "62-66", "article_number": "", "type": "journal-article", "publisher": "", "url": ""}}
    cr = lambda doi: recs.get(doi)
    oa = lambda doi: {"openalex_id": "W1", "publication_year": 2020, "cited_by_count": 5, "title": "Flood mapping under vegetation using single SAR acquisitions"}
    ts = lambda title, year="": {"doi": "10.1/otsu", "openalex_id": "W2", "publication_year": 1979, "cited_by_count": 1, "title": "A threshold selection method", "ratio": 0.97}
    frame = references.verify_all(entries, res, tmp_path, offline=False, crossref=cr, openalex=oa, urlcheck=lambda u: "http_200", title_search=ts)
    by = frame.set_index("bib_key")
    assert by.loc["Zhao_2021", "resolution"] == "crossref+openalex" and by.loc["Zhao_2021", "in_corpus"]
    assert "doi_from_openalex_title_search" in by.loc["Otsu_1979", "flags"]
    assert by.loc["U", "resolution"] == "no_doi_url_checked"
    n = references.write_bib(frame, entries, tmp_path / "v.bib")
    assert n == 2 and "@article{Zhao_2021" in (tmp_path / "v.bib").read_text()


# ---------------------------------------------------------------- final article + docx


def _write_table(d: Path, tid: str, header, rows, caption="cap"):
    md = [f"**{tid}.** {caption}  ", "*Evidence level: mixed.*", "",
          "| " + " | ".join(header) + " |", "|" + "|".join("---:" for _ in header) + "|"]
    md += ["| " + " | ".join(map(str, r)) + " |" for r in rows]
    (d / f"{tid}.md").write_text("\n".join(md) + "\n", encoding="utf-8")


# ── parity with the original tool, on the frozen bundle ───────────────────────

FREEZE = Path(__file__).resolve().parents[1] / "data" / "frozen" / "paper_folders_20261002" / "paper_unet-case-kakhovka"


@pytest.mark.skipif(not FREEZE.exists(), reason="needs the P6 freeze (data/frozen, local only)")
def test_export_and_report_equal_the_original_tool_on_the_frozen_bundle(tmp_path):
    """02 / 03 / 05 of the port equal the original tools/paper3_audit run on the same inputs, and 03 equals
    the frozen deliverable (02 and 05 were written before the last overrides.yaml edit of 2026-09-28)."""
    import re
    import shutil
    from src.workbench.literature import claims, config as cfg, export as ex, report
    original = pytest.importorskip("tools.paper3_audit.export")
    import tools.paper3_audit.claims as oclaims
    import tools.paper3_audit.config as oc
    import tools.paper3_audit.report as oreport
    for name in ("new", "old"):
        shutil.copytree(FREEZE / "literature_audit_paper3", tmp_path / name)
        for f in [tmp_path / name, *(tmp_path / name).rglob("*")]:     # the freeze is read-only
            f.chmod(f.stat().st_mode | 0o200)
    shutil.copy(Path(__file__).parent / "fixtures/workbench/claims_theses_floodstate_paper3.yaml",
                tmp_path / "new/claims_theses.yaml")
    theses_json = FREEZE / "publication/literature/theses.json"
    saved = cfg.PATHS
    try:
        cfg.configure(cfg.Paths(pub_dir=FREEZE / "publication", out_dir=tmp_path / "new", work_dir=tmp_path / "new/_work",
                                theses_json=theses_json, chroma_dir=tmp_path, collection="x", repo=None))
        th = claims.load_theses(cfg.THESES_JSON)
        cl, nov, _ = claims.load_atomic_claims(theses=th)
        ex.run(th, cl, nov)
        report.fill_novelty()
    finally:
        cfg.configure(saved)
    old = tmp_path / "old"
    oth = oclaims.load_theses(theses_json, supplement=old / "theses_supplement.yaml")
    ocl, onov, _ = oclaims.load_atomic_claims(old / "atomic_claims.yaml", theses=oth)
    keep = (oc.OVERRIDES_YAML, oc.MANIFEST_JSON, oreport.NOVELTY_YAML)
    import tools.paper3_audit.manifest as omanifest
    try:
        original.load_overrides.__defaults__ = (old / "overrides.yaml",)
        omanifest.load.__defaults__ = (old / "run_manifest.json",)
        omanifest.update.__defaults__ = (old / "run_manifest.json",)
        oreport.NOVELTY_YAML = old / "novelty_verdicts.yaml"
        original.run(oth, ocl, onov, work_dir=old / "_work", out_dir=old)
        oreport.fill_novelty(out_dir=old)
    finally:
        original.load_overrides.__defaults__ = (keep[0],)
        omanifest.load.__defaults__ = (keep[1],)
        omanifest.update.__defaults__ = (keep[1],)
        oreport.NOVELTY_YAML = keep[2]
    date = re.compile(r"Generated \d{4}-\d{2}-\d{2}")
    for name in ("02_thesis_evidence.csv", "03_source_ledger.csv", "05_claim_citation_matrix.md"):
        assert date.sub("", (tmp_path / "new" / name).read_text(encoding="utf-8")) == \
            date.sub("", (old / name).read_text(encoding="utf-8")), name
    assert (tmp_path / "new/03_source_ledger.csv").read_bytes() == \
        (FREEZE / "literature_audit_paper3/03_source_ledger.csv").read_bytes()
