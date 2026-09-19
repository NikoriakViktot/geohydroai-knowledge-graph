# 4. METHODS

## 4.1 Vertical harmonisation: the four surfaces

Gauge stages are carried into EVRF2019 normal heights by the official EPSG:9902 operation, sampled from the production grid at each station ({{claim:V1.1}}); the offset is spatially varying, {{claim:V1.2}}, so no single national constant is admissible. ICESat-2 ATL13 heights are reduced with the EGG2015 quasigeoid after adding the ATL03 tide-free-to-mean-tide term, which moves the six-station corrector by {{claim:C-11}}. SWOT PIXC heights are reduced by subtracting the solid-earth, load and pole tides that the product supplies but does not apply, never the geoid field, and then by the same quasigeoid. The residual permanent-tide term between a mean-tide crust and a zero-tide quasigeoid (1–4 cm at this latitude) is carried as its own line in the uncertainty budget.

## 4.2 Chainage

Reservoir profiles use a centreline built from the reservoir polygon by principal-axis binning, with chainage measured from the dam; SWORD v16 is used only for the Kherson reach gradient, for the residual-body stem and for the channel-width transects.

## 4.3 Per-overpass slope and its inference

For every overpass the beam-median water-surface elevation of each of the six beams is regressed on chainage; the Theil–Sen estimator is primary and ordinary least squares the sensitivity check. One overpass is the independent unit: segments and beams within a pass share the atmosphere and the water state and are never treated as replicates. The pre/post contrast uses a bootstrap on the difference of medians, a two-sided permutation test and a sign test, and is repeated at minimum spans of 10, 20, 30 and 40 km.

## 4.4 Water-surface heterogeneity

Heterogeneity is the within-overpass p95–p05 range of water-surface elevation on all ATL13 dates in the footprint — not a standard deviation and not the within-profile range of the slope sample.

## 4.5 Historical datum and free-surface curve

The reduction level of the S-57 soundings is tested by three routes: closure of the sounding-implied capacity curve against the published level–volume table, the elevation of the exposed bed under ICESat-2, and the published navigation drawdown level. The 1970 field-measured free-surface curve is digitised from the monograph figure and validated against the tabulated backwater profile.

## 4.6 Bed reconstruction

For the reservoir, the bed is interpolated from soundings only (ordinary kriging, inverse distance, linear and radial-basis interpolators) on a 250 m canonical grid, pinned at the observed 5 June 2023 shoreline, and scored by spatially blocked cross-validation at 1 km, matched to the median sounding spacing. For the downstream zones the same design is repeated with three arms — soundings only, soundings with Sentinel-2 shoreline soft constraints, and soundings with p18 pseudo-points — and the arm is chosen on the soundings-only score.

## 4.7 Sentinel-2 indices, surface classes and Sentinel-1 water masks

Seven indices are computed per date on the 20 m registry grid with the BOA offset applied and scene-classification masking; the ten-class rule uses NDVI thresholds of 0.15 and 0.30, NDMI 0.10 and BSI 0.10 with morphological cleaning. Classes are: open water, shallow or mixed water, wet sediment, dry bare sediment, sparse herbaceous, dense herbaceous, reed or flooded vegetation, built surface and ambiguous. Shrub and tree cover are not separable from herbaceous cover by these indices alone. Sentinel-1 water masks use an anchored two-class discriminant on VV/VH validated against optical water; the variants tested and rejected are described in Supplementary Methods S2 and play no part in the argument of this paper.

## 4.8 Vegetation succession and first-event timing

On the former water surface ({{claim:S1.5}}) the mode class of each pixel is computed per leaf-on season, class shares are summarised per date and per year, transition matrices are formed between consecutive years, and the first date on which a pixel is seen non-water and vegetated is bracketed as an interval between consecutive valid observations. A 320-point stratified reference sample (40 per class) was drawn for a design-based accuracy assessment; it is not yet labelled.

## 4.9 Woody cover

Dynamic World annual composites (2022–2024) supply the trees-plus-shrub probability and the label share on the same surface; the frozen 2022 composite is the pre-breach reference.

## 4.10 Channel transects

{{claim:K1.2}}. On each date the water mask is intersected with each transect; the wetted width, the main-arm width and the number of arms are recorded, and dates are compared at matched Kherson stage.

## 4.11 Roughness translation

Each pixel of a state map (breach 2023, first exposure 2023, 2024, 2025, and for the floodway 2026) is assigned one of eleven roughness classes from its surface class, hydroperiod and Dynamic World woody probability, and each class carries a low, base and high Manning n from published priors (Table 5). Area-weighted n and the change between states are computed per zone. The product is a hydraulically interpreted roughness layer derived from mapped surface classes; no hydraulic observation enters this step and no value is a calibrated Manning coefficient.

## 4.12 Statistical rules

Every statistic names its independent unit. Absence statements about the literature are made only for a screened sample with a stated denominator, and only after retrieval has been validated against hold-out controls.
