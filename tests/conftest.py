"""
conftest.py  —  GeoHydroAI extraction pipeline test fixtures
============================================================

Shared fixtures, TEI XML builders, and pipeline helpers used across all
test groups.  Tests work WITHOUT:
  - Ollama running
  - Ray cluster
  - Embedding model (encode_fn=None → graceful degradation)
  - spaCy (ner_entities=None → graceful degradation)
  - External geocoding API (mocked at module level)

TEI XML builder mirrors the XPath queries used by pipeline.py:
  - //tei:titleStmt/tei:title/text()
  - //tei:idno[@type='DOI']/text()
  - //tei:date/@when
  - //tei:sourceDesc//tei:monogr/tei:title/text()
  - //tei:sourceDesc//tei:analytic/tei:author
  - //tei:abstract//tei:p//text()
  - //tei:body//tei:div
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path
from typing import Any, Optional
from unittest.mock import patch

import pytest


# ─────────────────────────────────────────────────────────────────────────────
# TEI XML builder
# ─────────────────────────────────────────────────────────────────────────────

def _author_xml(
    first: str = "John",
    last: str = "Doe",
    email: str = "john.doe@example.com",
    institution: str = "University of Example",
    country: str = "Ukraine",
    country_key: str = "UA",
) -> str:
    return f"""
        <author>
          <persName>
            <forename type="first">{first}</forename>
            <surname>{last}</surname>
          </persName>
          <email>{email}</email>
          <affiliation key="aff1">
            <orgName type="institution">{institution}</orgName>
            <address>
              <country key="{country_key}">{country}</country>
            </address>
          </affiliation>
        </author>"""


def build_tei_xml(
    title: str = "Flood Mapping Using Sentinel-1 SAR Imagery",
    doi: str = "10.1234/test.2023",
    year: str = "2023",
    journal: str = "Remote Sensing",
    authors: Optional[list[dict]] = None,
    abstract: str = "This study investigates flood mapping using Sentinel-1 SAR data.",
    introduction: str = "",
    study_area: str = "",
    data_sources: str = "",
    methods: str = "",
    results: str = "",
    conclusion: str = "",
    extra_sections: str = "",
    references: str = "",
) -> str:
    """
    Build a valid GROBID-style TEI XML string for testing.

    All section text is placed in the correct XPath locations that
    pipeline.py's parse_sections() and parse_metadata() expect.
    """
    if authors is None:
        authors = [{}]  # one default author

    authors_xml = "\n".join(_author_xml(**a) for a in authors)

    def section(head: str, text: str) -> str:
        if not text:
            return ""
        return f"""
      <div>
        <head>{head}</head>
        <p>{text}</p>
      </div>"""

    body_sections = "".join([
        section("Introduction", introduction),
        section("Study area", study_area),
        section("Data sources", data_sources),
        section("Methods", methods),
        section("Results", results),
        section("Conclusion", conclusion),
        extra_sections,
    ])

    refs_xml = ""
    if references:
        refs_xml = f"<listBibl>{references}</listBibl>"

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<TEI xml:space="preserve" xmlns="http://www.tei-c.org/ns/1.0">
  <teiHeader>
    <fileDesc>
      <titleStmt>
        <title level="a" type="main">{title}</title>
      </titleStmt>
      <publicationStmt><publisher/></publicationStmt>
      <sourceDesc>
        <biblStruct>
          <analytic>
            <title level="a" type="main">{title}</title>
            {authors_xml}
            <idno type="DOI">{doi}</idno>
          </analytic>
          <monogr>
            <title level="j">{journal}</title>
            <imprint>
              <date type="published" when="{year}"/>
            </imprint>
          </monogr>
        </biblStruct>
      </sourceDesc>
    </fileDesc>
    <profileDesc>
      <abstract>
        <p>{abstract}</p>
      </abstract>
    </profileDesc>
  </teiHeader>
  <text>
    <body>
      {body_sections}
    </body>
    <back>
      {refs_xml}
    </back>
  </text>
</TEI>"""


# ─────────────────────────────────────────────────────────────────────────────
# Core fixtures
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def mock_geocoding():
    """Disable external geocoding API for all tests.  Returns None (no hit)."""
    with patch("src.ingestion.pipeline.geonames_lookup", return_value=None):
        yield


@pytest.fixture
def tmp_xml_dir(tmp_path):
    """Temporary directory for .tei.xml test fixtures."""
    d = tmp_path / "xml"
    d.mkdir()
    return d


@pytest.fixture
def tmp_out_dir(tmp_path):
    """Temporary directory for .paper.json outputs."""
    d = tmp_path / "paper_json"
    d.mkdir()
    return d


def write_tei(tmp_xml_dir: Path, stem: str, **kwargs) -> Path:
    """Write a TEI XML file and return its path."""
    xml = build_tei_xml(**kwargs)
    path = tmp_xml_dir / f"{stem}.tei.xml"
    path.write_text(xml, encoding="utf-8")
    return path


def run_pipeline(xml_path: Path) -> dict:
    """
    Run build_paper_json with no embeddings, no NER.

    This is the deterministic, unit-testable pipeline call.
    encode_fn=None → embedding score=0, graceful fallback.
    ner_entities=None → NER features skipped.
    """
    from src.ingestion.pipeline import build_paper_json
    return build_paper_json(xml_path, encode_fn=None, ner_entities=None)


# ─────────────────────────────────────────────────────────────────────────────
# Pre-built paper fixtures (used across multiple test groups)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def sar_flood_paper(tmp_xml_dir, mock_geocoding):
    """Paper about SAR flood mapping with Sentinel-1."""
    return write_tei(
        tmp_xml_dir,
        stem="sar_flood_paper",
        title="Flood Mapping Using Sentinel-1 SAR Imagery Over the Dnipro River Basin",
        doi="10.1001/sar.2023",
        abstract=(
            "We used Sentinel-1 SAR data for flood inundation mapping in Ukraine. "
            "The Dnipro River basin in the Kherson region experienced major flooding. "
            "Flood extent was derived from Sentinel-1 C-band SAR imagery."
        ),
        study_area=(
            "The study area is the Dnipro River basin in southern Ukraine, "
            "near Kakhovka in Kherson Oblast."
        ),
        methods=(
            "We applied Sentinel-1 SAR thresholding and change detection. "
            "The SRTM DEM was used as terrain reference. "
            "Flood water extent was mapped using backscatter thresholding."
        ),
        results=(
            "Flood mapping achieved high accuracy. RMSE = 0.45. "
            "NSE = 0.87 for water extent comparison."
        ),
    )


@pytest.fixture
def hec_ras_paper(tmp_xml_dir, mock_geocoding):
    """Paper using HEC-RAS hydraulic model."""
    return write_tei(
        tmp_xml_dir,
        stem="hec_ras_paper",
        title="2D Hydraulic Flood Modeling Using HEC-RAS in the Prut River Basin",
        doi="10.1002/hydraulic.2023",
        abstract=(
            "We performed 2D hydraulic flood simulation using HEC-RAS model "
            "for the Prut River basin in Ukraine and Romania. "
            "The HEC-RAS 2D model was calibrated using observed flood data."
        ),
        methods=(
            "HEC-RAS was used for hydraulic simulation. "
            "The 2D flood model was run with 5-meter DEM resolution. "
            "SRTM DEM served as terrain input for HEC-RAS. "
            "HEC-HMS was used for rainfall-runoff modeling upstream."
        ),
        study_area="The Prut River basin, Ukraine/Romania border region.",
    )


@pytest.fixture
def swat_paper(tmp_xml_dir, mock_geocoding):
    """Paper using SWAT hydrological model."""
    return write_tei(
        tmp_xml_dir,
        stem="swat_paper",
        title="Streamflow Simulation Using SWAT in Ukrainian Carpathians",
        doi="10.1003/swat.2023",
        abstract=(
            "SWAT model was applied for streamflow prediction in the Carpathian "
            "watershed in western Ukraine. Rainfall-runoff modeling was conducted "
            "using SWAT with 10-year calibration period."
        ),
        methods=(
            "SWAT (Soil and Water Assessment Tool) was calibrated and validated. "
            "The watershed simulation used daily precipitation data. "
            "SWAT model parameters were optimized using SWAT-CUP."
        ),
        study_area="Ukrainian Carpathians watershed, western Ukraine.",
    )


@pytest.fixture
def ndvi_paper(tmp_xml_dir, mock_geocoding):
    """Paper about NDVI/NDWI spectral index analysis."""
    return write_tei(
        tmp_xml_dir,
        stem="ndvi_paper",
        title="Vegetation Monitoring Using NDVI and NDWI from Sentinel-2",
        doi="10.1004/ndvi.2023",
        abstract=(
            "We used NDVI and NDWI indices derived from Sentinel-2 multispectral "
            "imagery to monitor vegetation stress and water content in Ukraine. "
            "Landsat-8 data was used for cross-validation."
        ),
        methods=(
            "NDVI (Normalized Difference Vegetation Index) and NDWI "
            "(Normalized Difference Water Index) were calculated from Sentinel-2. "
            "Landsat-8 OLI bands were used for validation. "
            "Random Forest classifier was applied for land cover classification."
        ),
    )


@pytest.fixture
def review_paper(tmp_xml_dir, mock_geocoding):
    """Review paper about flood mapping methods."""
    return write_tei(
        tmp_xml_dir,
        stem="review_paper",
        title="A Systematic Review of Flood Mapping Methods Using Remote Sensing",
        doi="10.1005/review.2023",
        abstract=(
            "This paper reviews existing methods for flood mapping using remote sensing. "
            "We survey the literature on SAR-based flood detection techniques. "
            "A systematic review of flood mapping techniques is presented covering "
            "the period 2010–2023."
        ),
        introduction=(
            "This comprehensive review examines satellite-based flood mapping. "
            "We compare different approaches to flood extent detection. "
            "The literature review covers Sentinel-1, Sentinel-2, MODIS, and Landsat."
        ),
        methods="A systematic review methodology was applied. Literature screening was done.",
        results="The review identified 187 relevant papers. Comparison of different approaches.",
    )


@pytest.fixture
def dem_validation_paper(tmp_xml_dir, mock_geocoding):
    """Paper about DEM validation using ICESat-2."""
    return write_tei(
        tmp_xml_dir,
        stem="dem_validation_paper",
        title="Digital Elevation Model Validation Using ICESat-2 ATL08 Data",
        doi="10.1006/dem.2023",
        abstract=(
            "We validated digital elevation models using ICESat-2 ATL08 reference data. "
            "DEM vertical accuracy assessment was performed for SRTM, ALOS DEM, and "
            "Copernicus DEM datasets. ICESat-2 elevation points served as reference."
        ),
        methods=(
            "DEM vertical accuracy was assessed using ICESat-2 photon data. "
            "SRTM, ALOS DEM, and Fabdem were compared against ICESat-2 ground truth. "
            "RMSE and MAE metrics were computed for each DEM dataset."
        ),
    )


@pytest.fixture
def modis_paper(tmp_xml_dir, mock_geocoding):
    """Paper using MODIS for drought monitoring."""
    return write_tei(
        tmp_xml_dir,
        stem="modis_paper",
        title="Drought Monitoring Using MODIS NDVI and Vegetation Indices",
        doi="10.1007/modis.2023",
        abstract=(
            "MODIS satellite data was used for drought monitoring across Ukraine. "
            "MODIS Terra and Aqua data products provided 8-day composite vegetation "
            "indices. NDVI and NDWI derived from MODIS were analyzed."
        ),
        methods=(
            "MODIS MOD13Q1 product (250m) was used for NDVI time series analysis. "
            "NDVI and NDWI trends were calculated from 2001-2022. "
            "Soil moisture assessment used MODIS land surface products."
        ),
    )
