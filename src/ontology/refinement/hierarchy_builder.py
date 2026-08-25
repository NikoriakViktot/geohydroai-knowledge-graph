"""
hierarchy_builder.py  —  GeoHydroAI canonical entity hierarchy definitions
===========================================================================

Defines:
  1. TYPE_GROUP_MAP   — assigns a fine-grained type_group to every entity
                        whose ID matches a pattern or prefix
  2. HIERARCHY_PATCHES — new canonical entities to add to the registry
                         (expanding generic entries into specific ones)
  3. ALIAS_PATCHES    — additional aliases injected into existing entities
  4. GENERIC_ENTITIES — entities flagged as too generic to use in graph nodes
                         (they still exist for alias resolution but emit a warning)

All definitions here are DETERMINISTIC and curated by hand.
No clustering or embeddings are used to assign type_groups.
"""

from __future__ import annotations

# ─────────────────────────────────────────────────────────────────────────────
# Fine-grained type_group taxonomy
# Maps canonical_id → type_group string
# Applied as a patch pass over the full registry
# ─────────────────────────────────────────────────────────────────────────────

TYPE_GROUP_MAP: dict[str, str] = {

    # ── SAR sensors ──────────────────────────────────────────────────────────
    "sensor.sentinel_1":    "sar_sensor",
    "sensor.sentinel_1_sar":"sar_sensor",
    "sensor.sentinel_1_iw": "sar_sensor",
    "sensor.sentinel_1_grd":"sar_sensor",
    "sensor.sentinel_1_slc":"sar_sensor",
    "sensor.alos_palsar":   "sar_sensor",
    "sensor.terrasar_x":    "sar_sensor",
    "sensor.cosmo_skymed":  "sar_sensor",
    "sensor.radarsat":      "sar_sensor",
    "sensor.radarsat_2":    "sar_sensor",
    "sensor.iceye":         "sar_sensor",
    "sensor.sar":           "sar_sensor",
    "sensor.asar":          "sar_sensor",
    "sensor.ers_1":         "sar_sensor",
    "sensor.ers_2":         "sar_sensor",
    "sensor.jers_sar":      "sar_sensor",
    "sensor.palsar":        "sar_sensor",
    "sensor.palsar_2":      "sar_sensor",
    "sensor.sir_c_x_sar":  "sar_sensor",
    "sensor.sar_lupe":      "sar_sensor",
    "sensor.uavsar":        "sar_sensor",

    # ── Optical multispectral sensors ────────────────────────────────────────
    "sensor.sentinel_2":    "optical_sensor",
    "sensor.sentinel_2_msi":"optical_sensor",
    "sensor.modis":         "optical_sensor",
    "sensor.landsat_8":     "optical_sensor",
    "sensor.landsat_9":     "optical_sensor",
    "sensor.avhrr":         "optical_sensor",
    "sensor.viirs":         "optical_sensor",
    "sensor.aster":         "optical_sensor",
    "sensor.worldview_2":   "optical_sensor",
    "sensor.worldview_3":   "optical_sensor",
    "sensor.pleiades":      "optical_sensor",
    "sensor.planet":        "optical_sensor",
    "sensor.spot":          "optical_sensor",
    "sensor.cbers":         "optical_sensor",
    "sensor.goes":          "optical_sensor",
    "sensor.abi":           "optical_sensor",
    "sensor.msg":           "optical_sensor",
    "sensor.aatsr":         "optical_sensor",
    "sensor.etm_plus":      "optical_sensor",
    "sensor.oli":           "optical_sensor",

    # ── Microwave / passive microwave ─────────────────────────────────────────
    "sensor.smap":          "microwave_sensor",
    "sensor.smos":          "microwave_sensor",
    "sensor.amsr_e":        "microwave_sensor",
    "sensor.ssm_i":         "microwave_sensor",
    "sensor.trmm":          "precipitation_sensor",
    "sensor.gpm":           "precipitation_sensor",
    "sensor.tmpa":          "precipitation_sensor",
    "sensor.grace":         "gravimetry_sensor",
    "sensor.grace_fo":      "gravimetry_sensor",
    "sensor.swot":          "altimetry_sensor",
    "sensor.cryosat":       "altimetry_sensor",
    "sensor.envisat":       "optical_sensor",
    "sensor.sentinel_3":    "optical_sensor",

    # ── LiDAR ────────────────────────────────────────────────────────────────
    "sensor.lidar":         "lidar_sensor",

    # ── Thermal ──────────────────────────────────────────────────────────────
    "sensor.aster":         "thermal_sensor",

    # ── Generic (needs refinement) ────────────────────────────────────────────
    "sensor.sentinel":      "generic_sensor",
    "sensor.optical":       "generic_sensor",
    "sensor.radar":         "generic_sensor",

    # ── Hydraulic models ─────────────────────────────────────────────────────
    "method.hec_ras":               "hydraulic_model",
    "method.hec_ras_2d":            "hydraulic_model",
    "method.lisflood_fp":           "hydraulic_model",
    "method.telemac":               "hydraulic_model",
    "method.mike_flood":            "hydraulic_model",
    "method.mike_11":               "hydraulic_model",
    "method.mike_21":               "hydraulic_model",
    "method.sobek":                 "hydraulic_model",
    "method.flo_2d":                "hydraulic_model",
    "method.infoworks_icm":         "hydraulic_model",
    "method.two_dimensional_hydrodynamic_model": "hydraulic_model",
    "method.one_dimensional_hydraulic_modeling": "hydraulic_model",
    "method.one_dimensional_hydrodynamic_model": "hydraulic_model",

    # ── Hydrological models ──────────────────────────────────────────────────
    "method.hec_hms":               "hydrological_model",
    "method.swat":                  "hydrological_model",
    "method.swat_plus":             "hydrological_model",
    "method.topmodel":              "hydrological_model",
    "method.vic":                   "hydrological_model",
    "method.wrf_hydro":             "hydrological_model",
    "method.hbv":                   "hydrological_model",
    "method.mgb_iph":               "hydrological_model",
    "method.hms":                   "hydrological_model",
    "method.muskingum_routing":     "hydrological_model",
    "method.rainfall_runoff_modeling": "hydrological_model",

    # ── Machine learning ─────────────────────────────────────────────────────
    "method.random_forest":         "machine_learning",
    "method.support_vector_machine":"machine_learning",
    "method.xgboost":               "machine_learning",
    "method.lightgbm":              "machine_learning",
    "method.gradient_boosting":     "machine_learning",
    "method.naive_bayes":           "machine_learning",
    "method.k_nearest_neighbors":   "machine_learning",
    "method.decision_tree":         "machine_learning",
    "method.linear_discriminant_analysis": "machine_learning",
    "method.maximum_likelihood_classification": "machine_learning",

    # ── Deep learning ─────────────────────────────────────────────────────────
    "method.artificial_neural_network": "deep_learning",
    "method.convolutional_neural_network": "deep_learning",
    "method.long_short_term_memory": "deep_learning",
    "method.u_net":                 "deep_learning",
    "method.transformer":           "deep_learning",
    "method.resnet":                "deep_learning",
    "method.densenet":              "deep_learning",
    "method.gru":                   "deep_learning",
    "method.autoencoder":           "deep_learning",
    "method.gan":                   "deep_learning",
    "method.anfis":                 "deep_learning",

    # ── Statistical methods ───────────────────────────────────────────────────
    "method.arima":                 "statistical_method",
    "method.stepwise_multiple_linear_regression": "statistical_method",
    "method.flood_frequency_analysis": "statistical_method",
    "method.regional_regression_equations": "statistical_method",
    "method.quantile_regression":   "statistical_method",
    "method.logistic_regression":   "statistical_method",
    "method.3d_var":                "data_assimilation",
    "method.4d_var":                "data_assimilation",
    "method.ensemble_kalman_filter":"data_assimilation",

    # ── Remote sensing methods ────────────────────────────────────────────────
    "method.sar_thresholding":      "remote_sensing_method",
    "method.sar_change_detection":  "remote_sensing_method",
    "method.object_based_image_analysis": "remote_sensing_method",
    "method.synthetic_aperture_radar_flood_mapping": "remote_sensing_method",
    "method.obia":                  "remote_sensing_method",
    "method.insar":                 "remote_sensing_method",
    "method.sebal":                 "remote_sensing_method",
    "method.metric_et":             "remote_sensing_method",
    "method.alexi":                 "remote_sensing_method",

    # ── BFE estimation methods ────────────────────────────────────────────────
    "method.base_flood_profile_extrapolation": "bfe_estimation",
    "method.point_of_the_boundary_method":     "bfe_estimation",
    "method.redelineation":                    "bfe_estimation",
    "method.contour_interpolation_method":     "bfe_estimation",
    "method.historical_high_water_mark_plus_safety_factor": "bfe_estimation",
    "method.water_control_structures_plus_freeboard": "bfe_estimation",
    "method.stream_gage_data":                 "bfe_estimation",
    "method.flood_study_multi_agency":         "bfe_estimation",
    "method.profiles_from_flood_insurance_study": "bfe_estimation",
    "method.floodway_data_tables_from_fis":    "bfe_estimation",
    "method.firm":                             "bfe_estimation",

    # ── Terrain / geomorphometry ─────────────────────────────────────────────
    "method.hand":                  "terrain_analysis",
    "method.muskingum_routing":     "terrain_analysis",

    # ── Evaluation metrics ────────────────────────────────────────────────────
    "metric.overall_accuracy":      "accuracy_metric",
    "metric.kappa":                 "accuracy_metric",
    "metric.f1_score":              "accuracy_metric",
    "metric.iou":                   "accuracy_metric",
    "metric.auc":                   "accuracy_metric",
    "metric.pod":                   "accuracy_metric",
    "metric.far":                   "accuracy_metric",
    "metric.map_accuracy":          "accuracy_metric",
    "metric.thematic_accuracy":     "accuracy_metric",
    "metric.positional_accuracy":   "accuracy_metric",

    # ── Regression / calibration metrics ─────────────────────────────────────
    "metric.rmse":                  "regression_metric",
    "metric.mae":                   "regression_metric",
    "metric.r2":                    "regression_metric",
    "metric.r2_adj":                "regression_metric",
    "metric.rms":                   "regression_metric",
    "metric.rmsep_rmsecv":          "regression_metric",
    "metric.snr":                   "uncertainty_metric",
    "metric.noise_figure":          "uncertainty_metric",

    # ── Hydrological performance metrics ─────────────────────────────────────
    "metric.nse":                   "hydrological_metric",
    "metric.kge":                   "hydrological_metric",
    "metric.image_statistics":      "regression_metric",
}


# ─────────────────────────────────────────────────────────────────────────────
# NEW entities to inject into the registry (hierarchy expansion)
# These expand generic entries into specific canonical sub-entities
# ─────────────────────────────────────────────────────────────────────────────

HIERARCHY_PATCHES: list[dict] = [

    # ── Sentinel-1 sub-products ───────────────────────────────────────────────
    {
        "id": "sensor.sentinel_1_sar",
        "display_name": "Sentinel-1 SAR",
        "type": "sensor", "type_group": "sar_sensor",
        "domain": "remote_sensing",
        "contexts": ["flood_mapping"],
        "aliases": ["Sentinel-1 SAR", "S1 SAR", "Sentinel1 SAR", "Sentinel-1 SAR data"],
        "definition": "SAR imagery products from the Sentinel-1 constellation (C-band, VV/VH).",
        "used_for": ["flood extent mapping", "soil moisture", "displacement"],
        "inputs": [], "outputs": ["SAR_imagery"],
        "limitations": ["wind roughening", "layover"],
        "related": ["sensor.sentinel_1", "data.sar"],
        "source_files": ["hierarchy_builder"],
        "parent_id": "sensor.sentinel_1",
    },
    {
        "id": "sensor.sentinel_1_iw",
        "display_name": "Sentinel-1 IW",
        "type": "sensor", "type_group": "sar_sensor",
        "domain": "remote_sensing",
        "contexts": ["flood_mapping"],
        "aliases": ["Sentinel-1 IW", "Sentinel-1 Interferometric Wide", "IW mode", "S1 IW", "Sentinel-1 IW SLC", "Sentinel-1 IW GRD"],
        "definition": "Interferometric Wide (IW) swath mode of Sentinel-1, default mode over land (250 km swath, 5×20 m).",
        "used_for": ["flood mapping", "deformation monitoring"],
        "related": ["sensor.sentinel_1", "sensor.sentinel_1_grd", "sensor.sentinel_1_slc"],
        "source_files": ["hierarchy_builder"],
        "parent_id": "sensor.sentinel_1",
    },
    {
        "id": "sensor.sentinel_1_grd",
        "display_name": "Sentinel-1 GRD",
        "type": "sensor", "type_group": "sar_sensor",
        "domain": "remote_sensing",
        "contexts": ["flood_mapping"],
        "aliases": ["Sentinel-1 GRD", "S1 GRD", "Sentinel-1 Ground Range Detected", "GRD product", "Sentinel1 GRD"],
        "definition": "Sentinel-1 Ground Range Detected product — multi-looked, projected to ground range (typical flood mapping input).",
        "used_for": ["flood extent mapping", "land cover"],
        "related": ["sensor.sentinel_1", "sensor.sentinel_1_iw"],
        "source_files": ["hierarchy_builder"],
        "parent_id": "sensor.sentinel_1",
    },
    {
        "id": "sensor.sentinel_1_slc",
        "display_name": "Sentinel-1 SLC",
        "type": "sensor", "type_group": "sar_sensor",
        "domain": "remote_sensing",
        "contexts": [],
        "aliases": ["Sentinel-1 SLC", "S1 SLC", "Single Look Complex", "Sentinel-1 Single Look Complex"],
        "definition": "Sentinel-1 Single Look Complex product — complex I/Q data used for InSAR processing.",
        "used_for": ["InSAR", "deformation monitoring"],
        "related": ["sensor.sentinel_1", "method.insar"],
        "source_files": ["hierarchy_builder"],
        "parent_id": "sensor.sentinel_1",
    },
    {
        "id": "sensor.sentinel_1a",
        "display_name": "Sentinel-1A",
        "type": "sensor", "type_group": "sar_sensor",
        "domain": "remote_sensing",
        "contexts": [],
        "aliases": ["Sentinel-1A", "S1A"],
        "definition": "First satellite of the Sentinel-1 constellation, launched April 2014.",
        "used_for": ["flood mapping", "SAR interferometry"],
        "related": ["sensor.sentinel_1", "sensor.sentinel_1b"],
        "source_files": ["hierarchy_builder"],
        "parent_id": "sensor.sentinel_1",
    },
    {
        "id": "sensor.sentinel_1b",
        "display_name": "Sentinel-1B",
        "type": "sensor", "type_group": "sar_sensor",
        "domain": "remote_sensing",
        "contexts": [],
        "aliases": ["Sentinel-1B", "S1B"],
        "definition": "Second satellite of the Sentinel-1 constellation (retired 2021-12).",
        "used_for": ["flood mapping", "SAR interferometry"],
        "related": ["sensor.sentinel_1", "sensor.sentinel_1a"],
        "source_files": ["hierarchy_builder"],
        "parent_id": "sensor.sentinel_1",
    },

    # ── Sentinel-2 sub-products ───────────────────────────────────────────────
    {
        "id": "sensor.sentinel_2_msi",
        "display_name": "Sentinel-2 MSI",
        "type": "sensor", "type_group": "optical_sensor",
        "domain": "remote_sensing",
        "contexts": [],
        "aliases": ["Sentinel-2 MSI", "S2 MSI", "MSI", "Sentinel-2 MultiSpectral Instrument", "Sentinel2 MSI"],
        "definition": "Sentinel-2 MultiSpectral Instrument: 13-band multispectral sensor (10–60 m).",
        "used_for": ["flood mapping", "vegetation monitoring", "water detection"],
        "limitations": ["cloud contamination"],
        "related": ["sensor.sentinel_2", "data.ndwi", "data.ndvi"],
        "source_files": ["hierarchy_builder"],
        "parent_id": "sensor.sentinel_2",
    },
    {
        "id": "sensor.sentinel_2a",
        "display_name": "Sentinel-2A",
        "type": "sensor", "type_group": "optical_sensor",
        "domain": "remote_sensing", "contexts": [],
        "aliases": ["Sentinel-2A", "S2A"],
        "definition": "First satellite of the Sentinel-2 constellation.",
        "related": ["sensor.sentinel_2"],
        "source_files": ["hierarchy_builder"],
        "parent_id": "sensor.sentinel_2",
    },
    {
        "id": "sensor.sentinel_2b",
        "display_name": "Sentinel-2B",
        "type": "sensor", "type_group": "optical_sensor",
        "domain": "remote_sensing", "contexts": [],
        "aliases": ["Sentinel-2B", "S2B"],
        "definition": "Second satellite of the Sentinel-2 constellation.",
        "related": ["sensor.sentinel_2"],
        "source_files": ["hierarchy_builder"],
        "parent_id": "sensor.sentinel_2",
    },
    {
        "id": "sensor.sentinel_3",
        "display_name": "Sentinel-3",
        "type": "sensor", "type_group": "optical_sensor",
        "domain": "remote_sensing", "contexts": [],
        "aliases": ["Sentinel-3", "S3", "OLCI", "SLSTR", "Sentinel-3 OLCI"],
        "definition": "ESA Sentinel-3 mission for ocean and land surface monitoring (OLCI/SLSTR).",
        "used_for": ["sea surface temperature", "land surface temperature", "ocean colour"],
        "related": ["sensor.sentinel_1", "sensor.sentinel_2", "org.esa"],
        "source_files": ["hierarchy_builder"],
    },

    # ── Additional Landsat variants ───────────────────────────────────────────
    {
        "id": "sensor.landsat_8",
        "display_name": "Landsat-8",
        "type": "sensor", "type_group": "optical_sensor",
        "domain": "remote_sensing", "contexts": [],
        "aliases": ["Landsat-8", "Landsat 8", "OLI", "TIRS", "Landsat8", "L8", "Landsat-8 OLI"],
        "definition": "USGS/NASA Landsat-8 with OLI (multispectral) and TIRS (thermal) instruments at 30 m.",
        "used_for": ["flood mapping", "land cover", "water detection"],
        "limitations": ["16-day revisit", "cloud contamination"],
        "related": ["sensor.landsat_9", "data.ndwi", "org.usgs"],
        "source_files": ["hierarchy_builder"],
    },
    {
        "id": "sensor.landsat_9",
        "display_name": "Landsat-9",
        "type": "sensor", "type_group": "optical_sensor",
        "domain": "remote_sensing", "contexts": [],
        "aliases": ["Landsat-9", "Landsat 9", "Landsat9", "L9"],
        "definition": "USGS/NASA Landsat-9 continuation satellite launched 2021.",
        "related": ["sensor.landsat_8"],
        "source_files": ["hierarchy_builder"],
    },

    # ── New method entities ───────────────────────────────────────────────────
    {
        "id": "method.xgboost",
        "display_name": "XGBoost",
        "type": "method", "type_group": "machine_learning",
        "domain": "flood_mapping", "contexts": [],
        "aliases": ["XGBoost", "xgboost", "extreme gradient boosting", "XGB"],
        "definition": "Scalable gradient-boosted decision tree algorithm with regularisation for classification and regression.",
        "used_for": ["flood susceptibility mapping", "land cover classification"],
        "related": ["method.random_forest", "method.lightgbm"],
        "source_files": ["hierarchy_builder"],
    },
    {
        "id": "method.lightgbm",
        "display_name": "LightGBM",
        "type": "method", "type_group": "machine_learning",
        "domain": "flood_mapping", "contexts": [],
        "aliases": ["LightGBM", "lightgbm", "Light GBM", "LGBM"],
        "definition": "Gradient boosting framework using histogram-based algorithm for efficient large-scale tree learning.",
        "used_for": ["flood classification", "feature importance"],
        "related": ["method.xgboost", "method.random_forest"],
        "source_files": ["hierarchy_builder"],
    },
    {
        "id": "method.hec_ras_2d",
        "display_name": "HEC-RAS 2D",
        "type": "method", "type_group": "hydraulic_model",
        "domain": "flood_mapping", "contexts": [],
        "aliases": ["HEC-RAS 2D", "HEC-RAS 2D model", "HEC RAS 2D", "HEC-RAS two-dimensional"],
        "definition": "Two-dimensional unsteady flow solver in HEC-RAS for floodplain hydrodynamics.",
        "used_for": ["urban flood modeling", "floodplain inundation"],
        "inputs": ["DEM", "discharge", "roughness", "boundary_conditions"],
        "outputs": ["water_depth", "velocity", "flood_extent"],
        "limitations": ["computationally expensive"],
        "related": ["method.hec_ras", "method.lisflood_fp", "data.dem"],
        "source_files": ["hierarchy_builder"],
        "parent_id": "method.hec_ras",
    },
    {
        "id": "method.hec_hms",
        "display_name": "HEC-HMS",
        "type": "method", "type_group": "hydrological_model",
        "domain": "flood_mapping", "contexts": [],
        "aliases": ["HEC-HMS", "HEC HMS", "Hydrologic Modeling System", "HMS"],
        "definition": "USACE hydrological model for simulating rainfall-runoff in dendritic watersheds.",
        "used_for": ["flood hydrograph generation", "design flood estimation"],
        "inputs": ["precipitation", "soil", "land_cover", "DEM"],
        "outputs": ["discharge_hydrograph"],
        "related": ["method.hec_ras", "org.usace"],
        "source_files": ["hierarchy_builder"],
    },
    {
        "id": "method.swat_plus",
        "display_name": "SWAT+",
        "type": "method", "type_group": "hydrological_model",
        "domain": "hydrology", "contexts": [],
        "aliases": ["SWAT+", "SWAT Plus", "swat_plus", "SWAT+2012"],
        "definition": "Revised and restructured version of SWAT for watershed-scale hydrological modelling.",
        "used_for": ["water quality", "streamflow prediction", "NPS pollution"],
        "related": ["method.swat"],
        "source_files": ["hierarchy_builder"],
    },
    {
        "id": "method.swat",
        "display_name": "SWAT",
        "type": "method", "type_group": "hydrological_model",
        "domain": "hydrology", "contexts": [],
        "aliases": ["SWAT", "Soil and Water Assessment Tool", "SWAT model"],
        "definition": "Semi-distributed watershed model for simulating hydrological processes at basin scale.",
        "used_for": ["streamflow", "sediment transport", "nutrient loading"],
        "related": ["method.swat_plus"],
        "source_files": ["hierarchy_builder"],
    },
    {
        "id": "method.transformer",
        "display_name": "Transformer",
        "type": "method", "type_group": "deep_learning",
        "domain": "flood_mapping", "contexts": [],
        "aliases": ["Transformer", "Vision Transformer", "ViT", "Swin Transformer", "attention mechanism"],
        "definition": "Self-attention-based neural architecture for image and sequence modelling.",
        "used_for": ["image segmentation", "flood mapping", "time series forecasting"],
        "related": ["method.u_net", "method.convolutional_neural_network"],
        "source_files": ["hierarchy_builder"],
    },
    {
        "id": "method.telemac",
        "display_name": "TELEMAC",
        "type": "method", "type_group": "hydraulic_model",
        "domain": "flood_mapping", "contexts": [],
        "aliases": ["TELEMAC", "TELEMAC-2D", "TELEMAC2D", "TELEMAC system"],
        "definition": "Open-source finite-element hydraulic model suite for river and coastal flows.",
        "used_for": ["flood inundation", "coastal flooding", "hydraulic routing"],
        "related": ["method.hec_ras", "method.lisflood_fp"],
        "source_files": ["hierarchy_builder"],
    },

    # ── KGE metric ───────────────────────────────────────────────────────────
    {
        "id": "metric.kge",
        "display_name": "Kling-Gupta Efficiency",
        "type": "metric", "type_group": "hydrological_metric",
        "domain": "hydrology", "contexts": [],
        "aliases": ["KGE", "Kling-Gupta Efficiency", "Kling Gupta", "KGE score"],
        "definition": "Multi-objective efficiency metric decomposing model performance into correlation, bias, and variability components.",
        "used_for": ["hydrological model calibration", "streamflow validation"],
        "related": ["metric.nse", "metric.rmse"],
        "source_files": ["hierarchy_builder"],
    },

    # ── Data products ─────────────────────────────────────────────────────────
    {
        "id": "data.mndwi",
        "display_name": "MNDWI",
        "type": "data", "type_group": "spectral_index",
        "domain": "remote_sensing", "contexts": [],
        "aliases": ["MNDWI", "Modified NDWI", "Modified Normalized Difference Water Index"],
        "definition": "Modified NDWI using SWIR band instead of NIR for improved water/built-up discrimination.",
        "used_for": ["surface water detection", "flood mapping"],
        "related": ["data.ndwi", "data.ndvi"],
        "source_files": ["hierarchy_builder"],
    },
    {
        "id": "data.awei",
        "display_name": "AWEI",
        "type": "data", "type_group": "spectral_index",
        "domain": "remote_sensing", "contexts": [],
        "aliases": ["AWEI", "Automated Water Extraction Index", "AWEInsh", "AWEIsh"],
        "definition": "Automated Water Extraction Index using multiple spectral bands for robust water mapping.",
        "used_for": ["surface water detection", "flood mapping"],
        "related": ["data.ndwi", "data.mndwi"],
        "source_files": ["hierarchy_builder"],
    },
    {
        "id": "data.hand",
        "display_name": "HAND",
        "type": "data", "type_group": "terrain_derivative",
        "domain": "terrain_analysis", "contexts": [],
        "aliases": ["HAND", "Height Above Nearest Drainage", "HAND model"],
        "definition": "Terrain-normalised raster expressing elevation relative to the nearest drainage network — flood proxy.",
        "used_for": ["flood hazard mapping", "rapid inundation estimate"],
        "related": ["data.dem", "data.dtm"],
        "source_files": ["hierarchy_builder"],
    },
    {
        "id": "data.twi",
        "display_name": "Topographic Wetness Index",
        "type": "data", "type_group": "terrain_derivative",
        "domain": "terrain_analysis", "contexts": [],
        "aliases": ["TWI", "Topographic Wetness Index", "CTI"],
        "definition": "Compound topographic index ln(a/tan β) quantifying propensity for soil moisture accumulation.",
        "used_for": ["flood susceptibility", "soil moisture modelling"],
        "related": ["data.dem", "data.slope"],
        "source_files": ["hierarchy_builder"],
    },
]


# ─────────────────────────────────────────────────────────────────────────────
# ALIAS_PATCHES — additional aliases injected into EXISTING entities
# dict: canonical_id → list[str] aliases to add
# ─────────────────────────────────────────────────────────────────────────────

ALIAS_PATCHES: dict[str, list[str]] = {
    "sensor.sentinel_1": [
        "Sentinel-1A", "Sentinel-1B", "S1A", "S1B",
        "Sentinel 1 SAR", "Sentinel-1 C-band", "Sentinel-1 GRD",
        "Sentinel-1 IW", "sentinel1",
    ],
    "sensor.sentinel_2": [
        "Sentinel-2A", "Sentinel-2B", "S2A", "S2B",
        "Sentinel-2 MSI", "Sentinel 2 optical", "sentinel2",
    ],
    "sensor.modis": [
        "Terra MODIS", "Aqua MODIS", "MODIS Terra", "MODIS Aqua",
        "MOD09", "MYD09", "MOD13", "MODIS 500m",
    ],
    "sensor.alos_palsar": [
        "ALOS-1 PALSAR", "PALSAR-2", "ALOS-2 PALSAR", "ALOS2", "ALOS PALSAR-2",
    ],
    "sensor.terrasar_x": [
        "TerraSAR-X", "TSX", "TerraSAR X", "TanDEM-X",
    ],
    "sensor.cosmo_skymed": [
        "COSMO-SkyMed", "CSK", "COSMO SkyMed 2G",
    ],
    "sensor.radarsat": [
        "RADARSAT-1", "RADARSAT-2", "RS-2", "RCM", "RADARSAT Constellation",
    ],
    "sensor.gpm": [
        "GPM IMERG", "IMERG", "Global Precipitation Measurement", "GPM DPR",
        "GPM 0.1°", "GPM half-hourly",
    ],
    "sensor.grace": [
        "GRACE", "GRACE-FO", "GRACE Follow-On", "Gravity Recovery and Climate Experiment",
    ],
    "method.hec_ras": [
        "HEC-RAS 1D", "HEC-RAS 2D", "HEC-RAS 5.0", "HEC-RAS 6.0",
        "Hydrologic Engineering Center River Analysis System",
    ],
    "method.random_forest": [
        "RF model", "RF classifier", "Random Forest classification",
        "Random Forest regressor", "RF regression",
    ],
    "method.u_net": [
        "U-Net segmentation", "UNet architecture", "U-Net CNN",
    ],
    "method.artificial_neural_network": [
        "neural network", "feedforward neural network", "multilayer perceptron",
        "deep neural network", "DNN",
    ],
    "method.support_vector_machine": [
        "SVM classifier", "Support Vector Classifier", "SVC", "SVR",
        "kernel SVM",
    ],
    "method.convolutional_neural_network": [
        "CNN classifier", "deep convolutional", "ConvNet", "VGG", "ResNet CNN",
    ],
    "method.sar_thresholding": [
        "global threshold", "Otsu threshold", "adaptive threshold",
        "SAR water detection", "backscatter threshold",
    ],
    "method.object_based_image_analysis": [
        "OBIA segmentation", "GEOBIA classification", "object-based SAR",
        "eCognition", "MultiRes segmentation",
    ],
    "method.ensemble_kalman_filter": [
        "EnKF assimilation", "ensemble filter", "Kalman filter",
    ],
    "data.dem": [
        "digital elevation model", "elevation raster", "terrain model",
        "SRTM", "ALOS DEM", "Copernicus DEM", "TanDEM-X DEM", "ASTER GDEM",
    ],
    "data.ndwi": [
        "water index", "NDWI Gao", "McFeeters NDWI",
    ],
    "metric.rmse": [
        "RMSE error", "root mean squared error", "RMS error",
    ],
    "metric.nse": [
        "Nash-Sutcliffe model efficiency", "E_ns", "Nash Sutcliffe",
    ],
    "metric.f1_score": [
        "F1 measure", "harmonic mean", "Dice loss",
    ],
    "metric.iou": [
        "Jaccard similarity", "intersection over union score",
    ],
    "concept.base_flood_elevation": [
        "100-year flood elevation", "regulatory flood elevation",
        "design flood elevation", "BFE contour",
    ],
}


# ─────────────────────────────────────────────────────────────────────────────
# GENERIC_ENTITIES — IDs that are too coarse for graph node use
# Maps canonical_id → reason string
# ─────────────────────────────────────────────────────────────────────────────

GENERIC_ENTITIES: dict[str, str] = {
    "sensor.sentinel": (
        "Generic Sentinel series — use sensor.sentinel_1, sensor.sentinel_2, etc."
    ),
    "sensor.optical": (
        "Generic optical category — use specific sensor IDs"
    ),
    "sensor.radar": (
        "Generic radar category — use specific SAR sensor IDs"
    ),
    "sensor.sar": (
        "Generic SAR data type — use specific satellite IDs for sensors, "
        "or data.sar for SAR imagery as a data source"
    ),
    "method.ann": (
        "Duplicate alias entry — canonical is method.artificial_neural_network"
    ),
    "concept.bfe": (
        "Duplicate — canonical is concept.base_flood_elevation"
    ),
    "concept.far": (
        "Ambiguous — metric.far is the canonical metric; "
        "this concept entry should be suppressed"
    ),
}
