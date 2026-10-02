"""House BibTeX keys, BibTeX entries and reference styles (registry faked, no network)."""

from __future__ import annotations

import pytest

from src.contracts.api import DoiAuthor, DoiMetadata
from src.services import bibformat as B
from src.services.bibtex import parse_authors, parse_bib

from tests.test_api_biblio import BIANCAMARIA, H, client, registry  # noqa: F401  (fixtures)


@pytest.mark.parametrize("name, key", [("Höhle", "Hohle"), ("Нікоряк", "Nikoriak"), ("Яковлев", "Yakovlev"),
                                       ("Згурський", "Zghurskyi"), ("Їжакевич", "Yizhakevych"), ("D'Odorico", "DOdorico")])
def test_key_parts(name, key):
    assert B.key_part(name) == key


def test_keys_and_collisions():
    two = DoiMetadata(doi="10.1/a", authors=[DoiAuthor(family="Wilson"), DoiAuthor(family="Sader")], year_print=2002)
    assert B.house_key(two)[0] == "Wilson_Sader_2002"
    online = DoiMetadata(doi="10.1/b", authors=[DoiAuthor(family="Biancamaria", given="S.")] * 3,
                         year_online=2015, year_print=2016, year_issued=2015)
    assert B.house_key(online)[0] == "Biancamaria_2016"
    org = DoiMetadata(doi="10.1/c", authors=[DoiAuthor(family="Conflict and Environment Observatory")], year_issued=2023)
    key, notes = B.house_key(org)
    assert key == "CEO_2023" and "house acronym" in notes[0]
    assert B.disambiguate(["Smith_2020", "Smith_2020", "Lee_2021"], {"Lee_2021": "10.1/z"},
                          ["10.1/x", "10.1/y", "10.1/w"]) == [("Smith_2020", False), ("Smith_2020a", True),
                                                             ("Lee_2021a", True)]


def test_bibtex_keeps_organisations_and_drops_page_artefacts():
    meta = DoiMetadata(doi="10.1/c", title="Dam & reservoir", type="report", year_issued=2023,
                       authors=[DoiAuthor(family="Conflict and Environment Observatory"), DoiAuthor(family="Smith", given="J.")],
                       pages="50-50")
    entry = parse_bib(B.bibtex(meta, "CEOBS_2023"))[0]
    assert entry["type"] == "techreport" and "pages" not in entry["fields"]
    assert parse_authors(entry["fields"]["author"])[0][0] == ("Conflict and Environment Observatory", "", True)
    assert entry["fields"]["title"] == r"{Dam \& reservoir}"


FIELDS = {"author": "Biancamaria, Sylvain and Lettenmaier, Dennis P. and Pavelsky, Tamlin M.",
          "title": "{The SWOT Mission and Its Capabilities for Land Hydrology}", "journal": "Surveys in Geophysics",
          "year": "2016", "volume": "37", "number": "2", "pages": "307--337", "doi": "10.1007/s10712-015-9346-y"}


@pytest.mark.parametrize("style, text", [
    ("apa", "Biancamaria, S., Lettenmaier, D. P., & Pavelsky, T. M. (2016). The SWOT Mission and Its Capabilities for "
            "Land Hydrology. Surveys in Geophysics, 37(2), 307–337. https://doi.org/10.1007/s10712-015-9346-y"),
    ("copernicus", "Biancamaria, S., Lettenmaier, D. P., and Pavelsky, T. M.: The SWOT Mission and Its Capabilities for "
                   "Land Hydrology, Surveys in Geophysics, 37, 307–337, https://doi.org/10.1007/s10712-015-9346-y, 2016."),
    ("elsevier-harvard", "Biancamaria, S., Lettenmaier, D.P., Pavelsky, T.M., 2016. The SWOT Mission and Its Capabilities "
                         "for Land Hydrology. Surveys in Geophysics 37, 307–337. https://doi.org/10.1007/s10712-015-9346-y."),
])
def test_styles(style, text):
    assert B.render_entry(FIELDS, style) == text


def test_format_endpoint(client, monkeypatch):
    from src.api.routers import biblio
    monkeypatch.setattr(biblio, "_project_keys", lambda project_id: {"Monti_2024": "10.9999/other"})
    body = client.post("/v1/bib/format", headers=H, json={
        "dois": [BIANCAMARIA["doi"], "10.24425/agg.2023.146162", "10.9999/ghost", "nope"],
        "project_id": "floodstate-eo:paper3"}).json()
    a, b, ghost, bad = body["entries"]
    assert a["key"] == "Biancamaria_2016" and "online 2015-10-27, print 2016-03" in a["bibtex"]
    assert b["key"] == "Monti_2024a" and b["collision"] and b["in_corpus"]["paper_id"] == "10.24425_agg.2023.146162"
    assert "pages" not in b["bibtex"]                       # the 50-50 artefact
    assert ghost["bibtex"] is None and "no registry knows" in ghost["notes"][0]
    assert bad["bibtex"] is None and "not a DOI" in bad["notes"][0]


def test_render_endpoint(client):
    bib = ("@article{Bianc_2016, author={Biancamaria, Sylvain}, title={SWOT}, journal={Surv. Geophys.}, year={2016}}\n"
           "@article{Alpha_2020, author={Alpha, A.}, title={First}, journal={J}, year={2020}}\n"
           "@article{Unused_2021, author={Zed, Z.}, title={Never cited}, journal={J}, year={2021}}")
    r = client.post("/v1/bib/render", headers=H, json={"bibtex": bib, "keys": ["Bianc_2016", "Alpha_2020", "Ghost_1999"],
                                                     "style": "apa"}).json()
    assert [x["key"] for x in r["references"]] == ["Alpha_2020", "Bianc_2016"] and r["unresolved_keys"] == ["Ghost_1999"]
    ms = "# Intro\nAs shown before (Biancamaria 2016; Missing et al. 2020)."
    r = client.post("/v1/bib/render", headers=H, json={"bibtex": bib, "manuscript": ms, "style": "copernicus"}).json()
    assert [x["key"] for x in r["references"]] == ["Bianc_2016"]
    assert r["unresolved_keys"] == ["(Missing et al. 2020)"] and r["uncited_entries"] == ["Alpha_2020", "Unused_2021"]
    assert client.post("/v1/bib/render", headers=H, json={"bibtex": "nothing"}).status_code == 422


def test_audit_endpoint(client, monkeypatch):
    from src.services import bibaudit
    monkeypatch.setattr(bibaudit, "find_doi", lambda title, family, year: "10.1007/s10712-015-9346-y"
                        if "SWOT" in title else None)
    bib = """@article{Monti_2024, author={Monti, R. and Rossi, L. and Reguzzoni, M.}, year={2024},
  title={The Nova Kakhovka dam collapse flooding as seen from Sentinel-1 SAR satellite images},
  journal={Advances in Geodesy and Geoinformation}, doi={10.24425/agg.2023.146162}, note={VERIFY pages}}
@article{Biancamaria_2016, author={Biancamaria, Sylvain}, title={The SWOT Mission and Its Capabilities for Land Hydrology},
  journal={Surveys in Geophysics}, year={2016}}
@article{Copy_2024, author={Monti, R.}, title={Copy}, year={2024},
  DOI={10.24425/agg.2023.146162}}
@misc{UNOSAT_3616_2023, author={{UNOSAT}}, title={Flood extent}, year={2023}}
@article{Ghost_2020, author={Nobody, A.}, title={An article nobody can find anywhere at all}, year={2020}}"""
    body = client.post("/v1/bib/audit", headers=H, json={"bibtex": bib}).json()
    e = {x["key"]: x for x in body["entries"]}
    assert e["Monti_2024"]["status"] == "fix" and any("VERIFY" in p for p in e["Monti_2024"]["problems"])
    assert any("Copy_2024" in p for p in e["Monti_2024"]["problems"])          # one DOI under two keys
    assert e["Biancamaria_2016"]["suggested_doi"] == "10.1007/s10712-015-9346-y"
    assert e["Biancamaria_2016"]["suggested_bibtex"].startswith("@article{Biancamaria_2016,")
    assert any("not lower case" in w for w in e["Copy_2024"]["warnings"])
    assert e["UNOSAT_3616_2023"]["status"] == "ok"                              # 3616 is not a year
    assert e["Ghost_2020"]["status"] == "unresolved"
    assert body["summary"]["entries"] == 5 and body["mixed_field_names"] == ["doi"]
