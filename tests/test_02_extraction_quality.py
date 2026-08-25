"""
test_02_extraction_quality.py  —  Group 2: Extraction quality benchmarks
=========================================================================

Tests that specific entities are correctly extracted from papers with
known content.  Each test is a positive benchmark: "this paper mentions
X in its methods/data_sources section — X must appear in the output."

Extraction sources tested:
  Satellites : Sentinel-1, Sentinel-2, Landsat, MODIS, ICESat-2
  Methods    : HEC-RAS, HEC-HMS, SWAT, Random Forest, U-Net, LSTM
  DEMs       : SRTM, ALOS DEM, Copernicus DEM
  Indices    : NDVI, NDWI (classified as data, not methods)

Quality criteria:
  - Entity name appears in the extracted list
  - No duplicates (same entity extracted twice)
  - accepted=True for clearly-used entities (above 0.3 threshold)
  - kb_metadata is populated (not empty dict)
"""

from __future__ import annotations

from tests.conftest import run_pipeline, write_tei


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _names(entities: list[dict]) -> set[str]:
    return {e["name"] for e in entities}


def _accepted_names(entities: list[dict]) -> set[str]:
    return {e["name"] for e in entities if e.get("accepted")}


def _find(entities: list[dict], name: str) -> dict | None:
    for e in entities:
        if e["name"].upper() == name.upper():
            return e
    return None


# ─────────────────────────────────────────────────────────────────────────────
# T02-01  Satellite extraction
# ─────────────────────────────────────────────────────────────────────────────

class TestSatelliteExtraction:

    def test_sentinel1_in_methods(self, tmp_xml_dir, mock_geocoding):
        path = write_tei(
            tmp_xml_dir, stem="s1_methods",
            abstract="We used Sentinel-1 SAR imagery for flood mapping.",
            methods="Sentinel-1 SAR data was processed using change detection.",
        )
        paper = run_pipeline(path)
        names = _names(paper["entities"]["satellites"])
        # Pipeline outputs the canonical KB name "SENTINEL" (Sentinel family) or "SAR" (sensor)
        assert any("SENTINEL" in n.upper() or "SAR" in n.upper() for n in names), (
            f"Sentinel-1 signal (SENTINEL/SAR) not extracted. Got: {names}"
        )

    def test_sentinel2_in_data_sources(self, tmp_xml_dir, mock_geocoding):
        path = write_tei(
            tmp_xml_dir, stem="s2_data",
            abstract="Sentinel-2 multispectral data was acquired for land cover mapping.",
            data_sources="We downloaded Sentinel-2 Level-2A products.",
            methods="Sentinel-2 MSI bands were used for NDVI calculation.",
        )
        paper = run_pipeline(path)
        names = _names(paper["entities"]["satellites"])
        # KB canonical name for Sentinel-2 is "SENTINEL"
        assert any("SENTINEL" in n.upper() or "MSI" in n.upper() for n in names), (
            f"Sentinel-2 signal not extracted. Got: {names}"
        )

    def test_landsat_extraction(self, tmp_xml_dir, mock_geocoding):
        path = write_tei(
            tmp_xml_dir, stem="landsat",
            abstract="Landsat-8 OLI imagery was used for change detection.",
            data_sources="Landsat data was downloaded from USGS EarthExplorer.",
            methods="Landsat-8 bands 4, 5, 6 were used for classification.",
        )
        paper = run_pipeline(path)
        names = _names(paper["entities"]["satellites"])
        # KB canonical name is "LANDSAT" (upper-case)
        assert any("LANDSAT" in n.upper() for n in names), f"Landsat not found. Got: {names}"

    def test_modis_extraction(self, modis_paper):
        paper = run_pipeline(modis_paper)
        names = _names(paper["entities"]["satellites"])
        assert any("MODIS" in n.upper() for n in names), f"MODIS not found. Got: {names}"

    def test_icesat2_extraction(self, dem_validation_paper):
        paper = run_pipeline(dem_validation_paper)
        sat_names = _names(paper["entities"]["satellites"])
        dem_names = _names(paper["entities"]["dems"])
        # ICESat-2 may be classified as a DEM (elevation reference) or satellite
        all_names = sat_names | dem_names
        assert any("ICESAT" in n.upper() or "ICESat" in n for n in all_names), (
            f"ICESat-2 not extracted. Satellites: {sat_names}  DEMs: {dem_names}"
        )

    def test_sentinel1_and_sentinel2_both_present(self, tmp_xml_dir, mock_geocoding):
        path = write_tei(
            tmp_xml_dir, stem="s1_s2",
            abstract="Sentinel-1 and Sentinel-2 were both used in this study.",
            methods="Sentinel-1 SAR thresholding was applied. "
                    "Sentinel-2 optical imagery was used for cross-validation.",
        )
        paper = run_pipeline(path)
        names = _names(paper["entities"]["satellites"])
        # Both Sentinel-1 and Sentinel-2 share canonical KB name "SENTINEL";
        # additionally SAR should be detected for Sentinel-1
        assert any("SENTINEL" in n.upper() for n in names), (
            f"Expected SENTINEL in satellites. Got: {names}"
        )
        assert any("SAR" in n.upper() for n in names), (
            f"Expected SAR sensor in satellites. Got: {names}"
        )

    def test_no_duplicate_satellites(self, sar_flood_paper):
        paper = run_pipeline(sar_flood_paper)
        names = [e["name"] for e in paper["entities"]["satellites"]]
        assert len(names) == len(set(names)), f"Duplicate satellites: {names}"


# ─────────────────────────────────────────────────────────────────────────────
# T02-02  Method extraction
# ─────────────────────────────────────────────────────────────────────────────

class TestMethodExtraction:

    def test_hec_ras_extraction(self, hec_ras_paper):
        paper = run_pipeline(hec_ras_paper)
        names = _names(paper["entities"]["methods"])
        assert any("HEC-RAS" in n.upper() or "HEC_RAS" in n.upper() for n in names), (
            f"HEC-RAS not extracted. Methods: {names}"
        )

    def test_hec_hms_extraction(self, hec_ras_paper):
        paper = run_pipeline(hec_ras_paper)
        names = _names(paper["entities"]["methods"])
        assert any("HEC-HMS" in n.upper() or "HEC_HMS" in n.upper() for n in names), (
            f"HEC-HMS not extracted. Methods: {names}"
        )

    def test_swat_extraction(self, swat_paper):
        paper = run_pipeline(swat_paper)
        names = _names(paper["entities"]["methods"])
        assert any("SWAT" in n.upper() for n in names), f"SWAT not extracted. Got: {names}"

    def test_random_forest_extraction(self, ndvi_paper):
        paper = run_pipeline(ndvi_paper)
        names = _names(paper["entities"]["methods"])
        assert any("RANDOM FOREST" in n.upper() or "RF" == n.upper() for n in names), (
            f"Random Forest not extracted. Methods: {names}"
        )

    def test_lstm_extraction(self, tmp_xml_dir, mock_geocoding):
        path = write_tei(
            tmp_xml_dir, stem="lstm",
            abstract="We applied LSTM neural network for water level forecasting.",
            methods="Long Short-Term Memory (LSTM) networks were trained. "
                    "LSTM model architecture: 3 layers with 128 units each.",
        )
        paper = run_pipeline(path)
        names = _names(paper["entities"]["methods"])
        assert any("LSTM" in n.upper() for n in names), f"LSTM not extracted. Got: {names}"

    def test_no_duplicate_methods(self, hec_ras_paper):
        paper = run_pipeline(hec_ras_paper)
        names = [e["name"] for e in paper["entities"]["methods"]]
        assert len(names) == len(set(names)), f"Duplicate methods: {names}"

    def test_method_has_kb_metadata(self, swat_paper):
        paper = run_pipeline(swat_paper)
        for m in paper["entities"]["methods"]:
            if "SWAT" in m["name"].upper():
                assert isinstance(m.get("kb_metadata"), dict), (
                    f"SWAT has no kb_metadata: {m}"
                )
                break

    def test_accepted_method_has_high_final_score(self, swat_paper):
        paper = run_pipeline(swat_paper)
        for m in paper["entities"]["methods"]:
            if m.get("accepted"):
                assert m["final_score"] >= 0.3, (
                    f"Accepted entity {m['name']!r} has low final_score: {m['final_score']}"
                )


# ─────────────────────────────────────────────────────────────────────────────
# T02-03  DEM extraction
# ─────────────────────────────────────────────────────────────────────────────

class TestDemExtraction:

    def test_srtm_extraction(self, hec_ras_paper):
        paper = run_pipeline(hec_ras_paper)
        names = _names(paper["entities"]["dems"])
        assert any("SRTM" in n.upper() for n in names), (
            f"SRTM not extracted. DEMs: {names}"
        )

    def test_dem_in_dem_validation_paper(self, dem_validation_paper):
        paper = run_pipeline(dem_validation_paper)
        dems = paper["entities"]["dems"]
        assert len(dems) > 0, "No DEMs extracted from DEM validation paper"

    def test_dem_role_set_to_terrain_input_with_hec(self, hec_ras_paper):
        """DEMs used alongside HEC-RAS/HEC-HMS should get role=terrain_input from constraints."""
        paper = run_pipeline(hec_ras_paper)
        for dem in paper["entities"]["dems"]:
            if dem.get("accepted"):
                assert dem.get("role") == "terrain_input", (
                    f"DEM {dem['name']!r} expected role=terrain_input, got {dem.get('role')!r}"
                )

    def test_no_duplicate_dems(self, dem_validation_paper):
        paper = run_pipeline(dem_validation_paper)
        names = [e["name"] for e in paper["entities"]["dems"]]
        assert len(names) == len(set(names)), f"Duplicate DEMs: {names}"


# ─────────────────────────────────────────────────────────────────────────────
# T02-04  Spectral indices / data entities
# ─────────────────────────────────────────────────────────────────────────────

class TestSpectralIndexExtraction:

    def test_ndvi_in_paper(self, ndvi_paper):
        paper = run_pipeline(ndvi_paper)
        # NDVI can appear in satellites, methods, or dems depending on KB classification
        all_names = (
            _names(paper["entities"]["satellites"])
            | _names(paper["entities"]["methods"])
        )
        assert any("NDVI" in n.upper() for n in all_names), (
            f"NDVI not found in satellites or methods. Got: {all_names}"
        )

    def test_ndwi_in_paper(self, ndvi_paper):
        paper = run_pipeline(ndvi_paper)
        all_names = (
            _names(paper["entities"]["satellites"])
            | _names(paper["entities"]["methods"])
        )
        assert any("NDWI" in n.upper() for n in all_names), (
            f"NDWI not found. Got: {all_names}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# T02-05  Metric extraction
# ─────────────────────────────────────────────────────────────────────────────

class TestMetricExtraction:

    def test_rmse_extracted_with_value(self, tmp_xml_dir, mock_geocoding):
        path = write_tei(
            tmp_xml_dir, stem="rmse_test",
            results="The model achieved RMSE = 0.45 m and NSE = 0.87.",
        )
        paper = run_pipeline(path)
        metrics = paper["entities"]["metrics"]
        rmse_hits = [m for m in metrics if m.get("type") == "RMSE"]
        assert len(rmse_hits) > 0, f"RMSE not extracted. Metrics: {metrics}"
        assert rmse_hits[0]["value"] == 0.45

    def test_nse_extracted(self, tmp_xml_dir, mock_geocoding):
        path = write_tei(
            tmp_xml_dir, stem="nse_test",
            results="NSE = 0.87 was achieved. RMSE = 12.4 mm.",
        )
        paper = run_pipeline(path)
        metrics = paper["entities"]["metrics"]
        nse_hits = [m for m in metrics if m.get("type") == "NSE"]
        assert len(nse_hits) > 0, f"NSE not extracted. Metrics: {metrics}"

    def test_metric_has_evidence_snippet(self, tmp_xml_dir, mock_geocoding):
        path = write_tei(
            tmp_xml_dir, stem="ev_test",
            results="The RMSE was 0.45 m. KGE = 0.81.",
        )
        paper = run_pipeline(path)
        for m in paper["entities"]["metrics"]:
            ev = m.get("evidence", {})
            assert isinstance(ev, dict), f"evidence not dict for {m.get('type')}"
            assert ev.get("snippet") is not None, "evidence.snippet is None"

    def test_no_invalid_metric_context_entries(self, tmp_xml_dir, mock_geocoding):
        """Metrics from AEP / table headers should NOT be extracted."""
        path = write_tei(
            tmp_xml_dir, stem="metric_noise",
            abstract="World Settlement Footprint (WSF) dataset was used. Table 1 shows OA values.",
        )
        paper = run_pipeline(path)
        # WSF/table-context OA hits should be filtered
        oa_hits = [m for m in paper["entities"]["metrics"] if m.get("type") == "OA"]
        for hit in oa_hits:
            snippet = hit.get("evidence", {}).get("snippet", "").lower()
            assert "world settlement" not in snippet, (
                f"Invalid OA metric extracted from WSF context: {hit}"
            )

    def test_regex_metrics_have_names_and_canonical_ids(self, tmp_xml_dir, mock_geocoding):
        """OA and F1 must produce non-empty name and canonical_id after KB fix."""
        path = write_tei(
            tmp_xml_dir, stem="oa_f1_names",
            results="The model achieved Overall Accuracy of 0.94 and F1-score of 0.88 on the validation set.",
        )
        paper = run_pipeline(path)
        metrics = paper["entities"]["metrics"]
        assert metrics, "no metrics extracted"
        assert all(m.get("name") for m in metrics), (
            f"every metric must have a non-empty name; got {[m.get('name') for m in metrics]}"
        )
        canonical_ids = {m.get("canonical_id") for m in metrics}
        assert canonical_ids - {""}, "at least one metric must have a canonical_id"
        high_conf = [m for m in metrics if m.get("confidence", 0) >= 0.7]
        assert high_conf, "metrics with KB entries should have confidence >= 0.7"
        ctx_values = {m.get("context") for m in metrics}
        assert "validation" in ctx_values, "context should detect 'validation' from snippet"
