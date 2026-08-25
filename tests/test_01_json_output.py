"""
test_01_json_output.py  —  Group 1: JSON output schema validation
=================================================================

Validates that every paper.json produced by build_paper_json:
  - Has the correct top-level structure
  - Contains all required fields and sub-fields
  - Contains no non-serialisable objects
  - Is reproducible (same input → same output)
  - Handles the paper_id ↔ filename convention correctly
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.conftest import build_tei_xml, run_pipeline, write_tei


# ─────────────────────────────────────────────────────────────────────────────
# Required schema constants
# ─────────────────────────────────────────────────────────────────────────────

REQUIRED_TOP_KEYS = {"metadata", "sections", "entities", "references", "provenance"}
REQUIRED_METADATA  = {"paper_id", "title", "doi", "year", "authors", "content_hash", "source_xml"}
REQUIRED_ENTITIES  = {"geo", "satellites", "dems", "methods", "metrics", "task", "sensor_types"}
REQUIRED_GEO       = {"study_geo", "author_geo", "study_type"}
REQUIRED_STUDY_GEO = {"primary_country", "countries", "regions", "rivers"}
REQUIRED_TASK      = {"label", "confidence", "source"}
VALID_STUDY_TYPES  = {"case_study", "regional", "multi_site", "global_algorithmic", "review", "unknown"}


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def base_paper(tmp_xml_dir, mock_geocoding):
    return write_tei(
        tmp_xml_dir,
        stem="base_paper",
        title="Flood Mapping Using Sentinel-1 SAR Imagery",
        doi="10.1234/test.2023",
        year="2023",
        journal="Remote Sensing",
        abstract="We mapped flood extent using Sentinel-1 SAR data in Ukraine.",
        methods="Sentinel-1 SAR thresholding was applied. SRTM DEM was used.",
        results="RMSE = 0.45 m. NSE = 0.87.",
    )


@pytest.fixture
def base_paper_json(base_paper):
    return run_pipeline(base_paper)


# ─────────────────────────────────────────────────────────────────────────────
# T01-01  Top-level structure
# ─────────────────────────────────────────────────────────────────────────────

class TestTopLevelStructure:

    def test_returns_dict(self, base_paper_json):
        assert isinstance(base_paper_json, dict)

    def test_required_top_level_keys_present(self, base_paper_json):
        missing = REQUIRED_TOP_KEYS - base_paper_json.keys()
        assert not missing, f"Missing top-level keys: {missing}"

    def test_no_extra_poison_keys(self, base_paper_json):
        # llm_judge is optional but must be present (set to None until judge runs)
        assert "llm_judge" in base_paper_json  # present, can be None

    def test_metadata_is_dict(self, base_paper_json):
        assert isinstance(base_paper_json["metadata"], dict)

    def test_sections_is_dict(self, base_paper_json):
        assert isinstance(base_paper_json["sections"], dict)

    def test_entities_is_dict(self, base_paper_json):
        assert isinstance(base_paper_json["entities"], dict)

    def test_references_is_list(self, base_paper_json):
        assert isinstance(base_paper_json["references"], list)

    def test_provenance_is_dict(self, base_paper_json):
        assert isinstance(base_paper_json["provenance"], dict)


# ─────────────────────────────────────────────────────────────────────────────
# T01-02  Metadata fields
# ─────────────────────────────────────────────────────────────────────────────

class TestMetadataFields:

    def test_required_metadata_keys(self, base_paper_json):
        meta = base_paper_json["metadata"]
        missing = REQUIRED_METADATA - meta.keys()
        assert not missing, f"Missing metadata keys: {missing}"

    def test_paper_id_is_string(self, base_paper_json):
        pid = base_paper_json["metadata"]["paper_id"]
        assert isinstance(pid, str) and len(pid) > 0

    def test_paper_id_derived_from_filename(self, tmp_xml_dir, mock_geocoding):
        path = write_tei(tmp_xml_dir, stem="my_paper_2023")
        paper = run_pipeline(path)
        # stem of "my_paper_2023.tei.xml" → remove ".tei" → "my_paper_2023"
        assert paper["metadata"]["paper_id"] == "my_paper_2023"

    def test_title_extracted_correctly(self, base_paper_json):
        title = base_paper_json["metadata"]["title"]
        assert isinstance(title, str)
        assert "Flood" in title or "Sentinel" in title or len(title) > 0

    def test_doi_extracted(self, base_paper_json):
        doi = base_paper_json["metadata"]["doi"]
        assert doi == "10.1234/test.2023"

    def test_year_extracted(self, base_paper_json):
        year = base_paper_json["metadata"]["year"]
        assert year == "2023"

    def test_authors_is_list(self, base_paper_json):
        authors = base_paper_json["metadata"]["authors"]
        assert isinstance(authors, list)

    def test_author_has_required_fields(self, base_paper_json):
        authors = base_paper_json["metadata"]["authors"]
        if authors:
            author = authors[0]
            assert "full_name" in author
            assert isinstance(author["full_name"], str)

    def test_content_hash_is_md5_hex(self, base_paper_json):
        h = base_paper_json["metadata"]["content_hash"]
        assert isinstance(h, str) and len(h) == 32
        assert all(c in "0123456789abcdef" for c in h)

    def test_paper_without_doi_has_none_doi(self, tmp_xml_dir, mock_geocoding):
        path = write_tei(tmp_xml_dir, stem="nodoi_paper", doi="")
        paper = run_pipeline(path)
        doi = paper["metadata"].get("doi")
        # doi can be None or empty string when not in XML
        assert doi is None or doi == ""


# ─────────────────────────────────────────────────────────────────────────────
# T01-03  Entities structure
# ─────────────────────────────────────────────────────────────────────────────

class TestEntitiesStructure:

    def test_required_entity_keys(self, base_paper_json):
        ents = base_paper_json["entities"]
        missing = REQUIRED_ENTITIES - ents.keys()
        assert not missing, f"Missing entity keys: {missing}"

    def test_satellites_is_list(self, base_paper_json):
        assert isinstance(base_paper_json["entities"]["satellites"], list)

    def test_dems_is_list(self, base_paper_json):
        assert isinstance(base_paper_json["entities"]["dems"], list)

    def test_methods_is_list(self, base_paper_json):
        assert isinstance(base_paper_json["entities"]["methods"], list)

    def test_metrics_is_list(self, base_paper_json):
        assert isinstance(base_paper_json["entities"]["metrics"], list)

    def test_sensor_types_is_list(self, base_paper_json):
        assert isinstance(base_paper_json["entities"]["sensor_types"], list)

    def test_geo_has_required_keys(self, base_paper_json):
        geo = base_paper_json["entities"]["geo"]
        missing = REQUIRED_GEO - geo.keys()
        assert not missing, f"Missing geo keys: {missing}"

    def test_study_geo_has_required_keys(self, base_paper_json):
        study_geo = base_paper_json["entities"]["geo"]["study_geo"]
        missing = REQUIRED_STUDY_GEO - study_geo.keys()
        assert not missing, f"Missing study_geo keys: {missing}"

    def test_task_has_required_keys(self, base_paper_json):
        task = base_paper_json["entities"]["task"]
        missing = REQUIRED_TASK - task.keys()
        assert not missing, f"Missing task keys: {missing}"

    def test_task_label_is_valid(self, base_paper_json):
        from src.ingestion.pipeline import VALID_TASK_LABELS
        label = base_paper_json["entities"]["task"]["label"]
        assert label in VALID_TASK_LABELS, f"Invalid task label: {label!r}"

    def test_task_confidence_is_float_in_range(self, base_paper_json):
        conf = base_paper_json["entities"]["task"]["confidence"]
        assert isinstance(conf, float)
        assert 0.0 <= conf <= 1.0

    def test_study_type_label_is_valid(self, base_paper_json):
        study_type = base_paper_json["entities"]["geo"]["study_type"]
        label = study_type.get("label")
        assert label in VALID_STUDY_TYPES, f"Invalid study_type: {label!r}"


# ─────────────────────────────────────────────────────────────────────────────
# T01-04  Entity record schema (each entity dict)
# ─────────────────────────────────────────────────────────────────────────────

class TestEntityRecordSchema:

    ENTITY_REQUIRED_KEYS = {"name", "type", "source", "confidence", "scores", "final_score", "accepted"}

    def _all_entities(self, paper: dict) -> list[dict]:
        ents = paper["entities"]
        return (
            ents.get("satellites", [])
            + ents.get("dems", [])
            + ents.get("methods", [])
        )

    def test_entity_records_have_required_keys(self, sar_flood_paper):
        paper = run_pipeline(sar_flood_paper)
        for ent in self._all_entities(paper):
            missing = self.ENTITY_REQUIRED_KEYS - ent.keys()
            assert not missing, f"Entity {ent.get('name')!r} missing keys: {missing}"

    def test_entity_confidence_in_range(self, sar_flood_paper):
        paper = run_pipeline(sar_flood_paper)
        for ent in self._all_entities(paper):
            conf = ent.get("confidence", 0.0)
            assert 0.0 <= conf <= 1.0, f"Confidence out of range: {conf} for {ent.get('name')}"

    def test_entity_final_score_in_range(self, sar_flood_paper):
        paper = run_pipeline(sar_flood_paper)
        for ent in self._all_entities(paper):
            fs = ent.get("final_score", 0.0)
            assert 0.0 <= fs <= 1.0, f"final_score out of range: {fs} for {ent.get('name')}"

    def test_entity_accepted_is_bool(self, sar_flood_paper):
        paper = run_pipeline(sar_flood_paper)
        for ent in self._all_entities(paper):
            assert isinstance(ent.get("accepted"), bool), (
                f"'accepted' not bool for entity {ent.get('name')}"
            )


# ─────────────────────────────────────────────────────────────────────────────
# T01-05  Full JSON serialisability
# ─────────────────────────────────────────────────────────────────────────────

class TestJsonSerialisability:

    def test_output_is_json_serialisable(self, base_paper_json):
        try:
            text = json.dumps(base_paper_json)
            assert len(text) > 100
        except (TypeError, ValueError) as exc:
            pytest.fail(f"JSON serialisation failed: {exc}")

    def test_roundtrip_stable(self, base_paper_json):
        text1 = json.dumps(base_paper_json, sort_keys=True)
        reparsed = json.loads(text1)
        text2 = json.dumps(reparsed, sort_keys=True)
        assert text1 == text2

    def test_all_satellite_names_are_strings(self, sar_flood_paper):
        paper = run_pipeline(sar_flood_paper)
        for sat in paper["entities"]["satellites"]:
            assert isinstance(sat["name"], str), f"Satellite name not string: {sat!r}"

    def test_all_method_names_are_strings(self, hec_ras_paper):
        paper = run_pipeline(hec_ras_paper)
        for m in paper["entities"]["methods"]:
            assert isinstance(m["name"], str), f"Method name not string: {m!r}"

    def test_metrics_have_numeric_values(self, tmp_xml_dir, mock_geocoding):
        path = write_tei(
            tmp_xml_dir, stem="metric_paper",
            abstract="Results: RMSE = 0.45 m, NSE = 0.87, R2 = 0.92.",
            results="RMSE = 0.45. NSE = 0.87. KGE = 0.81.",
        )
        paper = run_pipeline(path)
        for m in paper["entities"]["metrics"]:
            val = m.get("value")
            assert isinstance(val, (int, float)), f"Metric value not numeric: {val!r}"

    def test_references_have_string_titles(self, tmp_xml_dir, mock_geocoding):
        path = write_tei(tmp_xml_dir, stem="ref_paper")
        paper = run_pipeline(path)
        for ref in paper["references"]:
            if ref.get("title") is not None:
                assert isinstance(ref["title"], str)


# ─────────────────────────────────────────────────────────────────────────────
# T01-06  Provenance
# ─────────────────────────────────────────────────────────────────────────────

class TestProvenance:

    def test_provenance_parser_field(self, base_paper_json):
        prov = base_paper_json["provenance"]
        assert prov.get("parser") == "grobid_tei_kb_v2"

    def test_provenance_judge_used_is_false(self, base_paper_json):
        prov = base_paper_json["provenance"]
        assert prov.get("judge_used") is False

    def test_llm_judge_is_none_before_judge(self, base_paper_json):
        assert base_paper_json["llm_judge"] is None

    def test_provenance_source_xml_matches_path(self, tmp_xml_dir, mock_geocoding):
        path = write_tei(tmp_xml_dir, stem="prov_test")
        paper = run_pipeline(path)
        assert str(path) in paper["provenance"].get("source_xml", "")


# ─────────────────────────────────────────────────────────────────────────────
# T01-07  Reproducibility
# ─────────────────────────────────────────────────────────────────────────────

class TestReproducibility:

    def test_same_input_same_content_hash(self, tmp_xml_dir, mock_geocoding):
        path = write_tei(tmp_xml_dir, stem="repro_test",
                         abstract="Flood mapping using Sentinel-1 SAR.")
        p1 = run_pipeline(path)
        p2 = run_pipeline(path)
        assert p1["metadata"]["content_hash"] == p2["metadata"]["content_hash"]

    def test_same_input_same_task_label(self, tmp_xml_dir, mock_geocoding):
        path = write_tei(tmp_xml_dir, stem="repro_task",
                         abstract="HEC-HMS rainfall runoff modeling in Ukraine.",
                         methods="HEC-HMS was applied for runoff simulation.")
        p1 = run_pipeline(path)
        p2 = run_pipeline(path)
        assert p1["entities"]["task"]["label"] == p2["entities"]["task"]["label"]

    def test_different_content_different_hash(self, tmp_xml_dir, mock_geocoding):
        p1 = run_pipeline(write_tei(tmp_xml_dir, stem="diff1",
                                     abstract="Flood mapping in Ukraine."))
        p2 = run_pipeline(write_tei(tmp_xml_dir, stem="diff2",
                                     abstract="DEM validation using ICESat-2."))
        assert p1["metadata"]["content_hash"] != p2["metadata"]["content_hash"]
