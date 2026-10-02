"""Quote verification, full-text reading and theses validation, on a TEI fixture.

No live stores: identity resolution, the TEI location and the project's theses are
stubbed. The fixture reproduces the cases of the manual check of 2026-10-01
(citation_verification.md): a verbatim quotation, a secondary citation with a
before/after pair written as a range, and signed numbers.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from src.api.app import create_app
from src.contracts.identity import PaperIdentity
from src.document.parser import TEIParser
from src.services import fulltext, identity_store, quotes, thesis_validation

from tests.test_api import READ_KEY, StubKeys, StubManifest

H = {"X-API-Key": READ_KEY}

TEI = """<?xml version="1.0" encoding="UTF-8"?>
<TEI xmlns="http://www.tei-c.org/ns/1.0">
 <teiHeader>
  <fileDesc>
   <titleStmt><title level="a" type="main">Effectiveness of DEMs for flood modelling</title></titleStmt>
   <sourceDesc><biblStruct><analytic><title level="a" type="main">Effectiveness of DEMs for flood modelling</title>
     <idno type="DOI">10.1111/jfr3.12937</idno></analytic><monogr><imprint/></monogr></biblStruct></sourceDesc>
  </fileDesc>
  <profileDesc><abstract><div><p><s>Flood maps are needed for emergency response.</s><s>The HAND method does not accurately capture inundated cells in small streams.</s></p></div></abstract></profileDesc>
 </teiHeader>
 <text>
  <body>
   <div><head n="1">Introduction</head>
    <p><s coords="2,10,10,100,10">Floods are frequent on large floodplains such as the Jamuna.</s><s coords="2,10,30,100,10">Local depressions such as ponds or waterbodies are flooded even if they are not connected with the main stem river.</s></p>
   </div>
   <div><head n="4">Discussion</head>
    <p><s coords="15,10,10,100,10">A mean absolute vertical error of 1.12-1.61 m was found for the FABDEM in built-up areas <ref type="bibr" target="#b0">(Hawker et al., 2022)</ref>, which conforms to the findings for the study area.</s><s coords="15,10,30,100,10">The initial flow rate was (5.7 ± 0.8) × 10 4 m 3 /s and the water level fell to −5.000 m.</s></p>
    <p><s coords="16,10,10,100,10">The “quoted” term – with typographic dashes – appears here.</s></p>
   </div>
   <figure type="table" xml:id="tab_0" coords="17,10,10,200,100"><head>Table 1</head><label>1</label>
    <figDesc><div><p><s>Vertical error of the DEMs.</s><s>Values in metres.</s></p></div></figDesc>
    <table><row><cell>DEM</cell><cell>MAE</cell></row><row><cell>FABDEM</cell><cell>1.12</cell></row></table></figure>
  </body>
  <back><div type="references"><listBibl>
   <biblStruct xml:id="b0"><analytic><title level="a" type="main">A 30 m global map of elevation with forests and buildings removed</title>
    <idno type="DOI">10.1088/1748-9326/ac4d4f</idno></analytic><monogr><title level="j">Environ. Res. Lett.</title><imprint><date type="published" when="2022"/></imprint></monogr></biblStruct>
  </listBibl></div></back>
 </text>
</TEI>
"""

PAPER = PaperIdentity(paper_id="iqbal_2023", doi="10.1111/jfr3.12937", title="Effectiveness of DEMs for flood modelling",
                      year=2023, identity_status="ok")
NO_TEI = PaperIdentity(paper_id="no_tei", doi="10.1000/no-tei", title="A paper without full text", year=2020,
                       identity_status="ok")


@pytest.fixture
def tei_file(tmp_path):
    p = tmp_path / "iqbal_2023.tei.xml"
    p.write_text(TEI, encoding="utf-8")
    return p


@pytest.fixture
def client(monkeypatch, tei_file):
    return make_client(monkeypatch, tei_file)


def make_client(monkeypatch, tei_file):
    """TestClient with identity, the TEI location and the project's theses stubbed."""
    def fake_resolve(*, doi=None, paper_id=None, file=None, openalex_id=None, title=None, year=None,
                     include=frozenset()):
        for p in (PAPER, NO_TEI):
            if (doi and doi.lower() == p.doi) or paper_id == p.paper_id or file == p.paper_id:
                return identity_store.Resolved("exact", 1.0, p, None)
        return None

    monkeypatch.setattr(identity_store, "resolve", fake_resolve)
    monkeypatch.setattr(fulltext, "tei_path", lambda paper_id: tei_file if paper_id == "iqbal_2023" else None)
    monkeypatch.setattr(thesis_validation, "project_theses", lambda project_id: ({"T1", "T2"}, None))
    hawker = PaperIdentity(paper_id="hawker_2022", doi="10.1088/1748-9326/ac4d4f", title="FABDEM", year=2022,
                           identity_status="ok")
    monkeypatch.setattr(identity_store, "match_references",
                        lambda refs: [(hawker, "doi") if d and "ac4d4f" in d.lower() else (None, None)
                                      for d, _t, _y in refs])
    return TestClient(create_app(key_store=StubKeys(), manifest=StubManifest()))


def verify(client, *items, **extra):
    return client.post("/v1/quotes/verify", headers=H, json={"items": list(items), **extra})


# ── the parser no longer glues GROBID sentences ────────────────────────────────

def test_abstract_sentences_are_separated(tei_file):
    doc = TEIParser().parse_file(tei_file, "iqbal_2023")
    assert "emergency response. The HAND method" in doc.abstract


# ── /quotes/verify ─────────────────────────────────────────────────────────────

def test_verbatim_quote_is_found_exact_with_the_source_sentence(client):
    r = verify(client, {"source": "10.1111/JFR3.12937", "quote": "does not accurately capture inundated cells"})
    assert r.status_code == 200
    item = r.json()["items"][0]
    assert item["status"] == "FOUND_EXACT" and item["score"] == 1.0
    assert item["span"]["passage_id"] == "abstract"
    assert item["span"]["text"] == "The HAND method does not accurately capture inundated cells in small streams."
    assert item["attribution"]["cites_other_sources"] is False
    assert r.json()["summary"] == {"FOUND_EXACT": 1}


def test_secondary_citation_is_flagged_with_the_cited_doi(client):
    item = verify(client, {"source": "iqbal_2023", "expected_numbers": ["1.61", "1.12"],
                           "quote": "A mean absolute vertical error of 1.12-1.61 m was found for the FABDEM"}).json()["items"][0]
    assert item["status"] == "FOUND_EXACT"
    assert item["span"]["page"] == 15 and item["span"]["section_n"] == "4"
    assert item["attribution"] == {"cites_other_sources": True, "in_text_refs": ["(Hawker et al., 2022)"],
                                   "resolved_dois": ["10.1088/1748-9326/ac4d4f"]}
    # a range dash is not a minus sign
    assert [n["found"] for n in item["numbers"]] == [True, True]


def test_numbers_keep_their_sign(client):
    item = verify(client, {"source": "iqbal_2023", "quote": "The initial flow rate was (5.7 ± 0.8)",
                           "expected_numbers": ["5.7", "0.8", "-5.7", "-5.000", "5.000", "1.61"]}).json()["items"][0]
    found = {n["value"]: n for n in item["numbers"]}
    assert found["5.7"]["found"] and found["5.7"]["distance_chars"] == 0
    assert found["0.8"]["found"]
    assert not found["-5.7"]["found"]
    assert found["-5.000"]["found"]            # written with U+2212 in the source
    assert not found["5.000"]["found"]         # the source says minus five
    assert found["1.61"]["found"] and found["1.61"]["distance_chars"] > 0     # same paragraph, other sentence
    intro = verify(client, {"source": "iqbal_2023", "quote": "Floods are frequent on large floodplains",
                            "expected_numbers": ["1.61"]}).json()["items"][0]
    assert intro["numbers"] == [{"value": "1.61", "found": False, "distance_chars": None, "context": None,
                                 "elsewhere": ["s1.p0"]}]


def test_normalised_and_fuzzy_matches_are_told_apart(client):
    norm = verify(client, {"source": "iqbal_2023", "quote": 'The "quoted" term - with typographic dashes - appears'})
    assert norm.json()["items"][0]["status"] == "FOUND_NORMALIZED"
    fuzzy = verify(client, {"source": "iqbal_2023",
                            "quote": "Local depresions such as ponds or water bodies are flooded even if they are not connected"})
    item = fuzzy.json()["items"][0]
    assert item["status"] == "FOUND_FUZZY" and 0.92 <= item["score"] < 1
    assert "waterbodies" in item["span"]["text"]   # the source's wording, not the request's


def test_ellipsis_fragments_must_be_in_order(client):
    ok = verify(client, {"source": "iqbal_2023",
                         "quote": "Local depressions such as ponds or waterbodies … even if they are not connected with the main stem river"})
    assert ok.json()["items"][0]["status"] == "FOUND_EXACT"
    swapped = verify(client, {"source": "iqbal_2023",
                              "quote": "even if they are not connected with the main stem river … Local depressions such as ponds"})
    item = swapped.json()["items"][0]
    assert item["status"] == "FOUND_FUZZY" and "not in order" in item["detail"]


def test_not_found_and_source_unavailable(client):
    r = verify(client,
               {"source": "iqbal_2023", "quote": "the method perfectly captures every inundated cell in every stream"},
               {"source": "10.1000/no-tei", "quote": "any quotation that is long enough to check"},
               {"source": "10.9999/not-in-corpus", "quote": "any quotation that is long enough to check"})
    a, b, c = r.json()["items"]
    assert a["status"] == "NOT_FOUND" and a["text_source"] == "corpus_tei" and a["searched"] == {"sections": 2, "passages": 5, "chars": a["searched"]["chars"]}
    assert b["status"] == "SOURCE_UNAVAILABLE" and b["paper"]["paper_id"] == "no_tei"
    assert c["status"] == "SOURCE_UNAVAILABLE" and c["paper"] is None
    assert r.json()["summary"] == {"NOT_FOUND": 1, "SOURCE_UNAVAILABLE": 2}


def test_short_quotes_are_rejected(client):
    r = verify(client, {"source": "iqbal_2023", "quote": "by design"})
    assert r.status_code == 422 and r.json()["code"] == "VALIDATION_FAILED"
    assert r.json()["errors"][0]["loc"] == ["body", "items", 0, "quote"]


def test_open_citations_items_are_expanded(client):
    item = {"key": "Iqbal_2023", "doi": "https://doi.org/10.1111/jfr3.12937", "citations": [
        {"section": "Intro", "sentence": "…", "quotations": ["does not accurately capture inundated cells", "by design"]}]}
    body = verify(client, {"open_citations_item": item}).json()
    assert [i["key"] for i in body["items"]] == ["Iqbal_2023#0.0"] and body["items"][0]["status"] == "FOUND_EXACT"
    assert body["not_checked"][0]["key"] == "Iqbal_2023#0.1"


def test_quotation_of_a_co_cited_work_points_to_it(client, monkeypatch):
    sentence = 'HAND-type methods "do not accurately capture inundated cells" (Iqbal 2023; Other 2020).'
    items = [{"open_citations_item": {"key": k, "doi": doi, "citations": [
        {"sentence": sentence, "quotations": ["does not accurately capture inundated cells"]}]}}
        for k, doi in (("Iqbal_2023", "10.1111/jfr3.12937"), ("Other_2020", "10.1000/no-tei"))]
    real = quotes.verify_one

    def with_other_text(item, default_project=None):     # give Other_2020 a text that lacks the words
        r = real(item, default_project)
        if item.key.startswith("Other"):
            r = r.model_copy(update={"status": "NOT_FOUND", "text_source": "corpus_tei"})
        return r

    monkeypatch.setattr(quotes, "verify_one", with_other_text)
    body = verify(client, *items).json()
    assert body["items"][0]["found_in"] == []
    assert body["items"][1]["status"] == "NOT_FOUND" and body["items"][1]["found_in"] == ["Iqbal_2023#0.0"]


def test_batch_limits_and_acquisition(client):
    many = {"key": "K", "doi": "10.1111/jfr3.12937",
            "citations": [{"sentence": "s", "quotations": ["does not accurately capture inundated cells"] * 51}]}
    assert verify(client, {"open_citations_item": many}).status_code == 413
    r = verify(client, {"source": "iqbal_2023", "quote": "does not accurately capture inundated cells"}, acquire_missing=True)
    assert r.status_code == 501


# ── /papers/{paper_id}/sections and /text ──────────────────────────────────────

def test_sections_outline(client):
    body = client.get("/v1/papers/iqbal_2023/sections", headers=H).json()
    assert [(s["id"], s["n"], s["title"], s["pages"]) for s in body["sections"]] == [
        ("s0", "1", "Introduction", [2]), ("s1", "4", "Discussion", [15, 16])]
    assert body["has_abstract"] is True and body["paper"]["doi"] == "10.1111/jfr3.12937"
    assert client.get("/v1/papers/no_tei/sections", headers=H).json()["code"] == "SOURCE_UNAVAILABLE"
    assert client.get("/v1/papers/unknown/sections", headers=H).status_code == 404


def test_text_by_section_query_and_page(client):
    r = client.get("/v1/papers/iqbal_2023/text", headers=H, params={"section": "4"}).json()
    assert [s["passage_id"] for s in r["spans"]] == ["s1.p0", "s1.p1"] and r["truncated"] is False
    q = client.get("/v1/papers/iqbal_2023/text", headers=H, params={"q": "FABDEM built-up"}).json()
    assert [s["passage_id"] for s in q["spans"]] == ["s0.p0", "s1.p0", "s1.p1"]   # the hit ± 1 paragraph
    page = client.get("/v1/papers/iqbal_2023/text", headers=H, params={"page": 16}).json()
    assert [s["passage_id"] for s in page["spans"]] == ["s1.p1"]
    cut = client.get("/v1/papers/iqbal_2023/text", headers=H, params={"section": "abstract", "max_chars": 20}).json()
    assert cut["truncated"] is True and cut["spans"][0]["char_end"] == 20
    assert client.get("/v1/papers/iqbal_2023/text", headers=H).status_code == 400


def test_references_resolved_against_the_corpus(client):
    body = client.get("/v1/papers/iqbal_2023/references", headers=H).json()
    ref = body["references"][0]
    assert (ref["n"], ref["xml_id"], ref["doi"], ref["year"], ref["cited_in_text"]) == (1, "b0", "10.1088/1748-9326/ac4d4f", 2022, 1)
    assert ref["in_corpus"]["paper_id"] == "hawker_2022" and ref["match"] == "doi"
    assert body["counts"] == {"total": 1, "with_doi": 1, "in_corpus": 1, "cited_in_text": 1}


def test_tables_with_rows_and_a_caption_that_is_not_glued(client):
    body = client.get("/v1/papers/iqbal_2023/tables", headers=H).json()
    tab = body["tables"][0]
    assert tab["table_id"] == "tab_0" and tab["page"] == 17 and tab["facts"] is None
    assert tab["caption"] == "Vertical error of the DEMs. Values in metres."
    assert tab["rows"] == [["DEM", "MAE"], ["FABDEM", "1.12"]]
    text = client.get("/v1/papers/iqbal_2023/text", headers=H, params={"q": "vertical error metres"}).json()
    assert "tab_0" in [x["passage_id"] for x in text["spans"]]


# ── /theses/validate ───────────────────────────────────────────────────────────

THESES = [{"id": "T1", "thesis": "SAR misses water under dense vegetation.", "tables": "T16; T21",
           "refs": [{"key": "Pulvirenti_2021", "relation": "SUPPORTED_BY", "status": "VERIFY"}],
           "search_queries": ["flooded vegetation SAR"]}]


def test_valid_theses_with_counts_and_warnings(client):
    r = client.post("/v1/theses/validate", headers=H,
                    json={"kind": "theses", "project_id": "floodstate-eo:paper3", "document": THESES})
    body = r.json()
    assert r.status_code == 200 and body["valid"] is True
    assert body["counts"] == {"theses": 1, "refs": 1, "search_queries": 1, "tables_as_string": 1}
    assert any("authorship not recorded" in w for w in body["warnings"])


def test_csv_dump_theses_fail_without_coercion(client):
    dump = [dict(THESES[0], refs="Pulvirenti_2021[SUPPORTED_BY:verified]", search_queries="a | b")]
    r = client.post("/v1/theses/validate", headers=H,
                    json={"kind": "theses", "project_id": "kakhovka-terrain:paper2", "document": json.dumps(dump)})
    assert r.status_code == 422
    locs = [e["loc"] for e in r.json()["errors"]]
    assert ["theses", 0, "refs"] in locs and ["theses", 0, "search_queries"] in locs


def test_atomic_claims_yaml(client):
    doc = """authored_by: Claude (Fable 5.1) for the audit
claims:
  - {atomic_id: T1.A, thesis_id: T1, statement: SAR misses water under canopy., required_roles: [SUPPORTS]}
  - {atomic_id: T9.A, thesis_id: T9, statement: Another claim of enough length., negative_terms: "['urban']"}
  - {atomic_id: T2.A, thesis_id: T2, not_needed: true, note: covered by T1}
"""
    r = client.post("/v1/theses/validate", headers=H,
                    json={"kind": "atomic_claims", "project_id": "floodstate-eo:paper3", "document": doc})
    assert r.status_code == 422
    assert r.json()["errors"][0]["loc"] == ["claims", 1, "negative_terms"]
    fixed = doc.replace('"[\'urban\']"', "[urban]")
    ok = client.post("/v1/theses/validate", headers=H,
                     json={"kind": "atomic_claims", "project_id": "floodstate-eo:paper3", "document": fixed}).json()
    assert ok["valid"] and ok["counts"] == {"claims": 2, "not_needed": 1}
    assert any("T9" in w for w in ok["warnings"])


def test_unknown_project_is_a_contract_violation(client):
    r = client.post("/v1/theses/validate", headers=H, json={"kind": "theses", "project_id": "paper9", "document": []})
    assert r.status_code == 422


def test_quote_fragments_helper():
    assert quotes.fragments("a … b ... c") == ["a", "b", "c"]
    assert quotes.too_short("short … also short") and not quotes.too_short("x" * 25)


def test_entities_endpoint(client, monkeypatch, tmp_path):
    import json as _json

    from src.services import entities as ent
    norm = tmp_path / "iqbal_2023.json"
    norm.write_text(_json.dumps({
        "entities": {"methods": [{"name": "HAND", "role": "used", "evidence": "a HAND map", "provenance": {"page": 3}}],
                     "task": {"label": "flood_mapping", "confidence": 0.9, "source": "rules"},
                     "geo": {"study_type": {"label": "case_study", "confidence": 0.8, "source": "rules"},
                             "study_geo": {"primary_country": "Bangladesh", "rivers": ["Jamuna"],
                                           "countries": [{"name": "Bangladesh", "source": "regex", "confidence": 0.75},
                                                         {"name": "WGS84", "source": "ner", "confidence": 0.55},
                                                         {"name": "al.", "source": "ner", "confidence": 0.55}]}}},
        "normalized_entities": {"methods": [{"raw_name": "HAND", "canonical_id": "method.hand", "confidence": 1.0,
                                             "display_name": "Height Above Nearest Drainage", "source_field": "methods"}]}}),
        encoding="utf-8")
    monkeypatch.setattr(ent, "normalized_path", lambda pid: norm if pid == "iqbal_2023" else None)
    monkeypatch.setattr(ent, "grounding", lambda: {("USES_METHOD", "iqbal_2023", "method.hand"):
                                                   {"grounded": True, "tei_mentions": 2, "tei_evidence": ["The HAND method"]}})
    r = client.get("/v1/papers/iqbal_2023/entities", headers=H)
    body = r.json()
    assert r.status_code == 200, body
    m = body["methods"][0]
    assert (m["canonical_id"], m["role"], m["page"], m["grounded"], m["tei_evidence"]) == (
        "method.hand", "used", 3, True, ["The HAND method"])
    assert body["task"]["label"] == "flood_mapping" and body["study_type"]["label"] == "case_study"
    assert [c["name"] for c in body["study_area"]["countries"]] == ["Bangladesh"]
    assert body["study_area"]["dropped_country_names"] == 2 and body["study_area"]["rivers"] == ["Jamuna"]
    assert client.get("/v1/papers/no_tei/entities", headers=H).json()["code"] == "SOURCE_UNAVAILABLE"
