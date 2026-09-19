# 2. STUDY AREA AND INHERITED FRAMEWORK

The analysis zones, chainage convention and vertical frame (EVRF2019 normal heights via EGG2015 and EPSG:9902) are those of the companion paper and are not re-derived here. The terrain input for every model is the companion paper's bed: the reservoir surface ({{claim:V10.3}}) and the downstream zone surfaces ({{claim:B2.1}}; {{claim:B2.2}}; {{claim:B2.3}}), each with its soundings-only cross-validation score. FABDEM supplies the floodplain outside the former footprint; over the former bed FABDEM shows the impounded surface and is invalid.

# 3. DATA

## 3.1 Sentinel-2, Sentinel-1 and Dynamic World

Sentinel-2 L2A scenes on a 20 m registry grid (EPSG:32636) with the BOA additive offset applied, into seven indices and a ten-class surface classification; 53 dates cover the former pool. Sentinel-1 RTC γ⁰ water masks (Supplementary Methods S2). Dynamic World annual composites for 2022–2024; 2025–2026 are not yet available.

## 3.2 ICESat-2 ATL08

ATL08 terrain and canopy segments at 100 m and 20 m over the zones ({{claim:H1.1.n}}) supply canopy-height distributions per year.

## 3.3 Hydraulic observations

Legacy backwater profiles at nine discharges (design Table 20) and the 1970 field-measured curve, the reservoir and Kherson gauge records, and the 2023 estuary posts are the calibration data. The post-breach per-overpass slopes and within-overpass heterogeneity of the companion paper are the validation data and enter no calibration. Post-breach DniproHES discharge for 2024–2025 has not been recovered; the current-state runs are therefore scenario runs until it is.

{{pending:TAB01|Table 1 — Paper 2 data sources (machine-generated)|backs=S1.5,H1.1,K1.2|section=3 Data|blocks_submission=yes}}

# 4. METHODS

## 4.1 Surface classes and vegetation succession

Seven indices are computed per date with scene-classification masking; the ten-class rule uses NDVI thresholds of 0.15 and 0.30, NDMI 0.10 and BSI 0.10 with morphological cleaning. On the former water surface the mode class per leaf-on season, class shares per date and year, transition matrices between years and interval-censored first-event dates (first seen non-water, first seen vegetated) are computed. A 320-point stratified reference sample (40 per class) was drawn for a design-based accuracy assessment; it is not yet labelled, so no error-adjusted area is reported.

## 4.2 Woody cover

Spectral indices cannot separate shrub or tree from herbaceous cover, so woody encroachment is followed with Dynamic World trees-plus-shrub probability on the same surface, the frozen 2022 composite being the pre-breach reference.

## 4.3 Roughness translation

Each pixel of a state map (breach 2023, first exposure 2023, 2024, 2025; floodway 2026) is assigned one of eleven roughness classes from surface class, hydroperiod and woody probability; each class carries n_low, n_base and n_high from published priors (Table 1). Area-weighted n and its change between states are computed per zone. This layer is a hydraulically interpreted translation of mapped classes; its calibration is the subject of Section 4.5, not of this step.

## 4.4 Channel transects

{{claim:K1.2}}. On each date the water mask is intersected with each transect; wetted width, main-arm width and the number of arms are recorded and compared at matched Kherson stage.

## 4.5 Hydraulic models: calibration and validation kept apart

{{pending:OPEN62|HEC-RAS model description: pre-breach (legacy DniproGES1D as comparison only; new 1D on Table 20 backwater profiles) and current (2D on Paper 1 terrain + roughness layer)|backs=P2-AT01,P2-AT03|section=4.5 Hydraulic models|produces=SWOT-DNIPRO pilots/roughness + HEC-RAS project|blocks_submission=yes}}

Calibration uses only the legacy backwater profiles, historical levels and the Kherson gauge. Validation uses the per-overpass ICESat-2 slopes and within-overpass heterogeneity of the companion paper and the observed channel width; none of these enters calibration. Roughness sensitivity runs use n_low, n_base and n_high per class.
