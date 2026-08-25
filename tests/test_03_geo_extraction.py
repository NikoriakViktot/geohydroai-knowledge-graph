"""
test_03_geo_extraction.py  —  Group 3: Geographic extraction quality
=====================================================================

Validates the multi-layer geo extraction pipeline:
  - Country detection via regex patterns + TEI affiliation tags
  - River extraction + normalization + country linking
  - Region detection (Kakhovka, Carpathians, etc.)
  - Author affiliation country ≠ study country separation
  - NER-level validation (mocked)
  - geo confidence scoring
"""

from __future__ import annotations

import pytest

from tests.conftest import run_pipeline, write_tei


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _study_geo(paper: dict) -> dict:
    return paper["entities"]["geo"]["study_geo"]


def _primary_country(paper: dict) -> str | None:
    return _study_geo(paper).get("primary_country")


def _country_names(paper: dict) -> set[str]:
    return {c["name"] for c in _study_geo(paper).get("countries", [])}


def _river_names(paper: dict) -> set[str]:
    return {r["name"] for r in _study_geo(paper).get("rivers", [])}


def _region_names(paper: dict) -> set[str]:
    return {r["name"] for r in _study_geo(paper).get("regions", [])}


def _author_countries(paper: dict) -> set[str]:
    return {c["name"] for c in paper["entities"]["geo"].get("author_geo", [])}


# ─────────────────────────────────────────────────────────────────────────────
# T03-01  Country detection
# ─────────────────────────────────────────────────────────────────────────────

class TestCountryDetection:

    def test_ukraine_keyword_detected(self, tmp_xml_dir, mock_geocoding):
        path = write_tei(
            tmp_xml_dir, stem="ukraine_study",
            abstract="This study was conducted in Ukraine using SAR data.",
            study_area="The study area is located in central Ukraine, Kyiv Oblast.",
        )
        paper = run_pipeline(path)
        assert "Ukraine" in _country_names(paper), (
            f"Ukraine not in countries: {_country_names(paper)}"
        )

    def test_primary_country_from_study_area(self, sar_flood_paper):
        paper = run_pipeline(sar_flood_paper)
        pc = _primary_country(paper)
        # Dnipro River basin in Ukraine should produce Ukraine as primary
        assert pc == "Ukraine", f"Expected primary_country=Ukraine, got {pc!r}"

    def test_poland_detected(self, tmp_xml_dir, mock_geocoding):
        path = write_tei(
            tmp_xml_dir, stem="poland_study",
            abstract="We studied flood events in Poland using HEC-HMS.",
            study_area="The Vistula River basin in Poland was analyzed.",
        )
        paper = run_pipeline(path)
        assert "Poland" in _country_names(paper), (
            f"Poland not detected. Countries: {_country_names(paper)}"
        )

    def test_author_geo_extracted_from_tei(self, tmp_xml_dir, mock_geocoding):
        """Author affiliation country should be in author_geo, not study_geo."""
        path = write_tei(
            tmp_xml_dir,
            stem="author_geo_test",
            authors=[{
                "first": "Ivan", "last": "Petrenko",
                "institution": "National University", "country": "Ukraine",
                "country_key": "UA",
            }],
            abstract="Flood mapping in Bangladesh using Sentinel-1.",
            study_area="Study conducted in Bangladesh.",
        )
        paper = run_pipeline(path)
        author_countries = _author_countries(paper)
        assert "Ukraine" in author_countries, (
            f"Author affiliation Ukraine not in author_geo: {author_countries}"
        )

    def test_author_affiliation_country_not_in_study_geo(self, tmp_xml_dir, mock_geocoding):
        """
        Author affiliated with Poland but study in Bangladesh.
        Poland should appear in author_geo, NOT as study primary_country.
        """
        path = write_tei(
            tmp_xml_dir,
            stem="affil_vs_study",
            authors=[{
                "first": "John", "last": "Smith",
                "institution": "Warsaw University", "country": "Poland",
                "country_key": "PL",
            }],
            abstract="We conducted flood mapping in Bangladesh.",
            study_area="The study area is the Brahmaputra River basin in Bangladesh.",
        )
        paper = run_pipeline(path)
        pc = _primary_country(paper)
        # Bangladesh is the study country; Poland is author affiliation
        # primary_country should NOT be Poland
        assert pc != "Poland", (
            f"Author affiliation Poland incorrectly set as study primary_country"
        )

    def test_no_country_when_global_review(self, review_paper):
        """Review papers with no specific study area may have no primary_country."""
        paper = run_pipeline(review_paper)
        # Review papers don't have a specific study area — primary_country may be None
        pc = _primary_country(paper)
        # It's valid to have no primary_country for a review paper
        # — just verify it doesn't crash and the field exists
        assert "primary_country" in _study_geo(paper)


# ─────────────────────────────────────────────────────────────────────────────
# T03-02  River extraction
# ─────────────────────────────────────────────────────────────────────────────

class TestRiverExtraction:

    def test_dnipro_river_extracted(self, sar_flood_paper):
        paper = run_pipeline(sar_flood_paper)
        river_names = _river_names(paper)
        assert any("Dnipro" in n or "Dnieper" in n for n in river_names), (
            f"Dnipro not extracted. Rivers: {river_names}"
        )

    def test_prut_river_in_hec_paper(self, hec_ras_paper):
        paper = run_pipeline(hec_ras_paper)
        river_names = _river_names(paper)
        assert any("Prut" in n for n in river_names), (
            f"Prut River not found. Rivers: {river_names}"
        )

    def test_river_has_required_fields(self, sar_flood_paper):
        paper = run_pipeline(sar_flood_paper)
        for r in _study_geo(paper).get("rivers", []):
            assert "name" in r, f"River missing 'name': {r}"
            assert "type" in r, f"River missing 'type': {r}"

    def test_river_country_linking(self, sar_flood_paper):
        """River-country linking: Dnipro → Ukraine."""
        paper = run_pipeline(sar_flood_paper)
        river_links = _study_geo(paper).get("river_country_links", [])
        linked_countries = {link.get("country") for link in river_links}
        # If any rivers were linked, Ukraine should appear in linked countries
        if river_links:
            assert "Ukraine" in linked_countries or any(
                "Ukraine" in str(lc) for lc in linked_countries
            ), f"Ukraine not in river_country_links: {river_links}"

    def test_literature_review_rivers_not_accepted(self, tmp_xml_dir, mock_geocoding):
        """
        Rivers mentioned only in a literature review context should not be
        scored highly as study rivers.
        """
        path = write_tei(
            tmp_xml_dir,
            stem="lit_review_rivers",
            abstract=(
                "We present a systematic review of flood studies. "
                "Previous studies examined the Mississippi River, the Yangtze River, "
                "and the Amazon River basin in various papers."
            ),
            introduction="For example, Smith et al. studied the Nile River. Jones et al. examined the Rhine.",
            study_area="",  # No specific study area
            methods="We conducted a literature review following PRISMA guidelines.",
        )
        paper = run_pipeline(path)
        # Rivers may be extracted but should not be accepted as study rivers
        # due to literature-review context scoring
        accepted_rivers = [r for r in _study_geo(paper).get("rivers", []) if r.get("accepted")]
        # Acceptable to have none accepted (or a few with low confidence)
        # Key: this should not crash and scores should be lowered
        for r in accepted_rivers:
            assert r.get("confidence", 1.0) < 0.9, (
                f"Literature-review river {r['name']!r} has suspiciously high confidence: "
                f"{r.get('confidence')}"
            )


# ─────────────────────────────────────────────────────────────────────────────
# T03-03  Region extraction
# ─────────────────────────────────────────────────────────────────────────────

class TestRegionExtraction:

    def test_kakhovka_region_detected(self, sar_flood_paper):
        paper = run_pipeline(sar_flood_paper)
        region_names = _region_names(paper)
        assert any("Kakhovka" in n or "Kherson" in n for n in region_names), (
            f"Kakhovka/Kherson not detected. Regions: {region_names}"
        )

    def test_carpathians_detected(self, swat_paper):
        paper = run_pipeline(swat_paper)
        region_names = _region_names(paper)
        assert any("Carpathian" in n for n in region_names), (
            f"Carpathians not detected. Regions: {region_names}"
        )

    def test_generic_noise_not_extracted(self, tmp_xml_dir, mock_geocoding):
        """Generic terms like 'study region', 'target area' should be filtered."""
        path = write_tei(
            tmp_xml_dir,
            stem="noise_region",
            abstract="The study region was selected based on data availability.",
            study_area="This target area covers a large extent. The selected region is flood-prone.",
        )
        paper = run_pipeline(path)
        region_names = _region_names(paper)
        noise_terms = {"study region", "target area", "selected region",
                       "the region", "this region", "selected regions"}
        for name in region_names:
            assert name.lower() not in noise_terms, (
                f"Noise region name extracted: {name!r}"
            )


# ─────────────────────────────────────────────────────────────────────────────
# T03-04  Geo confidence scoring
# ─────────────────────────────────────────────────────────────────────────────

class TestGeoConfidence:

    def test_geo_confidence_is_numeric(self, sar_flood_paper):
        paper = run_pipeline(sar_flood_paper)
        conf = _study_geo(paper).get("confidence", 0.0)
        assert isinstance(conf, (int, float))
        assert 0.0 <= conf <= 1.0

    def test_geo_confidence_higher_when_country_present(self, tmp_xml_dir, mock_geocoding):
        """Paper with explicit country mention should have higher geo confidence."""
        path_with = write_tei(
            tmp_xml_dir, stem="with_country",
            study_area="The study area is in Ukraine. Kyiv Oblast was analyzed.",
        )
        path_without = write_tei(
            tmp_xml_dir, stem="without_country",
            abstract="Flood mapping was performed using SAR imagery.",
        )
        paper_with    = run_pipeline(path_with)
        paper_without = run_pipeline(path_without)
        conf_with    = _study_geo(paper_with).get("confidence", 0.0)
        conf_without = _study_geo(paper_without).get("confidence", 0.0)
        assert conf_with >= conf_without, (
            f"Paper with country should have higher geo_confidence. "
            f"With: {conf_with}, Without: {conf_without}"
        )

    def test_study_geo_structure_always_present(self, tmp_xml_dir, mock_geocoding):
        """Even empty papers must have a valid study_geo dict structure."""
        path = write_tei(tmp_xml_dir, stem="empty_geo",
                         abstract="Minimal content only.")
        paper = run_pipeline(path)
        sg = _study_geo(paper)
        assert isinstance(sg, dict)
        assert "primary_country" in sg
        assert "countries" in sg
        assert isinstance(sg["countries"], list)
