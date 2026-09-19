"""p2_literature — on-topic screening and reference parsing are pure; no network here."""
from __future__ import annotations

from src.paper_3 import p2_literature as p2


def test_paper2_theses_are_t25_to_t34():
    ids = [t.id for t in p2.paper2_theses()]
    assert ids == [f"T{i}" for i in range(25, 35)]


def test_screening_rejects_willow_physiology_and_accepts_bed_revegetation():
    theses = p2.paper2_theses()
    physiology = ("Biomass and Bioenergy: cadmium uptake by Salix clones in short-rotation coppice "
                  "grown on sewage sludge; phytoremediation yields and heavy metal accumulation.")
    assert p2.matched_theses(physiology, theses, abstract_mode=True) == []
    bed = ("Revegetation of the drained reservoir bed after dam removal was mapped with Sentinel-2 "
           "NDVI time series: plant colonisation and succession on exposed sediment reached most of the "
           "former reservoir within two seasons.")
    assert "T25" in p2.matched_theses(bed, theses, abstract_mode=True)
    manning = ("Manning's n for a vegetated floodplain with willow and reed was estimated from land cover "
               "classes for a HEC-RAS hydraulic model of the flood plain.")
    assert "T33" in p2.matched_theses(manning, theses, abstract_mode=True)


def test_parse_tei_references_reads_the_didukh_bibliography():
    from pathlib import Path
    xml = Path("data/literature/grobid_xml/10.32999_ksu1990-553x_2024-20-3-5.tei.xml")
    if not xml.exists():
        import pytest
        pytest.skip("Didukh TEI not present")
    refs = p2.parse_tei_references(xml)
    assert len(refs) > 60
    assert any(r["doi"] == "10.21203/rs.3.rs-4137799/v1" for r in refs)   # Kuzemko 2024 preprint
    assert all(set(r) == {"surname", "year", "title", "doi"} for r in refs)


def test_density_gate_rejects_a_single_mention_in_full_text():
    theses = p2.paper2_theses()
    t33 = next(t for t in theses if t.id == "T33")
    once = ("A 2D flood model of the floodplain was built in HEC-RAS with land cover classes; Manning "
            "coefficients were taken from the default table. " + "The flood extent was compared with imagery. " * 80)
    assert not p2.passes_density(t33, once)
    many = ("Manning roughness for the vegetated floodplain: flow resistance of willow and reed was measured; "
            "Manning's n varied with vegetation stage; roughness coefficient values per land cover class. " * 6)
    assert p2.passes_density(t33, many)
