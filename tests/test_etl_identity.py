"""Identity ETL: scan + classify on a temporary file store (no database)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.etl import identity as idn


def _paper_json(root: Path, pid: str, title: str | None, doi: str | None, year=2020, raw: str | None = None):
    p = root / "data/literature/paper_json" / f"{pid}.tei.paper.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(raw if raw is not None else json.dumps(
        {"metadata": {"paper_id": pid, "title": title, "doi": doi, "year": year, "journal": "J"}}), encoding="utf-8")


def _touch(root: Path, rel: str, content: bytes = b"x"):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(content)


LONG = "Satellite observations of a dam break flood and the limits of outburst models"


@pytest.fixture
def store(tmp_path):
    root = tmp_path
    # same DOI under two stems: the one named after the DOI is canonical
    _paper_json(root, "10.1029_2025gl120832", LONG, "10.1029/2025GL120832")
    _paper_json(root, "Lehnigk_GRL_copy", LONG, "https://doi.org/10.1029/2025gl120832")
    _touch(root, "data/literature/grobid_xml/10.1029_2025gl120832.tei.xml")
    # truncated paper.json
    _paper_json(root, "1-s2.0-S0022169421000001-main", None, None, raw='{"metadata": {"title": "Cut')
    # not a paper
    _paper_json(root, "EXHIBIT A #3013EURIZON_Prof. Osypov", "Exhibit A", None)
    # header DOI that resolves to another work
    _paper_json(root, "VKNU_geol_2018_3_3", "Modelling of extreme floods on example of mountain regions of Ukraine",
                "10.1126/science.aan2506")
    # no DOI at all
    _paper_json(root, "giustarini2013", "A change detection approach to flood mapping in urban areas", None)
    # title-only duplicates (long title, same year, no DOI)
    _paper_json(root, "model_hec_ras_0001", "Two dimensional hydraulic modelling of an urban flood with HEC-RAS 2D", None, 2019)
    _paper_json(root, "hec_ras_original", "Two dimensional hydraulic modelling of an urban flood with HEC-RAS 2D", None, 2019)
    _touch(root, "data/literature/grobid_xml/hec_ras_original.tei.xml")
    # identical PDF in pdf/ and pdf_missing/
    _touch(root, "data/literature/pdf/giustarini2013.pdf", b"%PDF-1.4 same")
    _touch(root, "data/literature/pdf_missing/giustarini2013.pdf", b"%PDF-1.4 same")
    manifest = {
        "data/literature/pdf/giustarini2013.pdf": "a" * 64,
        "data/literature/pdf_missing/giustarini2013.pdf": "a" * 64,
    }
    scanned = idn.scan(root, manifest)
    titles = {"10.1126/science.aan2506": "Changing climate shifts timing of European floods",
              "10.1029/2025gl120832": LONG}
    return idn.classify(scanned, titles)


def _by_id(classified):
    return {i.paper_id: i for i in classified.identities}


def test_duplicate_by_doi_prefers_the_copy_named_after_the_doi(store):
    ids = _by_id(store)
    assert ids["10.1029_2025gl120832"].identity_status == "ok"
    assert ids["10.1029_2025gl120832"].doi == "10.1029/2025gl120832"
    assert ids["Lehnigk_GRL_copy"].identity_status == "duplicate"
    assert ids["Lehnigk_GRL_copy"].duplicate_of == "10.1029_2025gl120832"


def test_truncated_json_and_not_a_paper(store):
    ids = _by_id(store)
    assert ids["1-s2.0-S0022169421000001-main"].identity_status == "truncated_json"
    assert any(f.status == "truncated" for f in ids["1-s2.0-S0022169421000001-main"].files)
    assert ids["EXHIBIT A #3013EURIZON_Prof. Osypov"].identity_status == "not_a_paper"


def test_header_doi_naming_another_work_is_not_the_papers_identity(store):
    vknu = _by_id(store)["VKNU_geol_2018_3_3"]
    assert vknu.identity_status == "title_doi_mismatch"
    assert vknu.doi is None
    assert "Changing climate shifts timing" in store.notes["VKNU_geol_2018_3_3"]
    assert not any(a.alias_type == "doi" for a in vknu.aliases)


def test_no_doi_and_title_duplicates(store):
    ids = _by_id(store)
    assert ids["giustarini2013"].identity_status == "no_doi"
    pair = {ids["model_hec_ras_0001"].identity_status, ids["hec_ras_original"].identity_status}
    assert pair == {"duplicate", "no_doi"}
    assert ids["model_hec_ras_0001"].duplicate_of == "hec_ras_original"  # the one with more files wins


def test_aliases_are_unique_and_point_at_canonical(store):
    seen = set()
    for i in store.identities:
        for a in i.aliases:
            assert (a.alias_type, a.alias) not in seen
            seen.add((a.alias_type, a.alias))
    ids = _by_id(store)
    canon = ids["10.1029_2025gl120832"]
    assert {(a.alias_type, a.alias) for a in canon.aliases} >= {
        ("doi", "10.1029/2025gl120832"), ("doi_slug", "10.1029_2025gl120832"), ("file_stem", "10.1029_2025gl120832")}
    assert {(a.alias_type, a.alias) for a in ids["Lehnigk_GRL_copy"].aliases} == {("file_stem", "Lehnigk_GRL_copy")}


def test_identical_pdf_copy_is_marked(store):
    files = _by_id(store)["giustarini2013"].files
    statuses = {f.path.split("/")[2]: f.status for f in files if f.kind == "pdf"}
    assert statuses == {"pdf": "ok", "pdf_missing": "duplicate_copy"}


def test_file_name_doi_wins_over_a_different_header_doi(tmp_path):
    _paper_json(tmp_path, "10.1002_2016ef000485", "Earth's future article", "10.1111/eft2.183")
    classified = idn.classify(idn.scan(tmp_path, {}), {})
    paper = classified.identities[0]
    assert paper.doi == "10.1002/2016ef000485"
    assert "10.1111/eft2.183" in classified.notes["10.1002_2016ef000485"]


def test_cyrillic_title_is_not_compared_with_the_english_openalex_title(tmp_path):
    _paper_json(tmp_path, "10.31481_uhmj.32.2023.05",
                "Середній стан і сезонна мінливість структури та динаміки перехідних вод", None)
    titles = {"10.31481/uhmj.32.2023.05": "Average condition and seasonal variability of the structure"}
    paper = idn.classify(idn.scan(tmp_path, {}), titles).identities[0]
    assert paper.identity_status == "ok"
    assert paper.doi == "10.31481/uhmj.32.2023.05"


def test_stem_doi_only_for_doi_named_files():
    assert idn.stem_doi("10.1029_2025gl120832") == "10.1029/2025gl120832"
    assert idn.stem_doi("giustarini2013") is None
    assert idn.stem_doi("1-s2.0-S0022169421000001-main") is None


def test_cohort_resolution_by_stem_and_doi(store):
    rows = [{"paper_id": "Lehnigk_GRL_copy", "doi": ""},
            {"paper_id": "", "doi": "10.1029/2025GL120832"},
            {"paper_id": "not_in_corpus", "doi": "10.9999/none"}]
    members, unresolved = idn.resolve_cohort(rows, store.identities, "paper_3", "test")
    assert [m.paper_id for m in members] == ["10.1029_2025gl120832"]   # both rows → the canonical copy
    assert len(unresolved) == 1
