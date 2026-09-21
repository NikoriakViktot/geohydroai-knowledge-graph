# 4. METHODS

The harmonisation of Section 4.1 is the method of this paper, not preparation for it: the corrections between the four surfaces are of the same order as the hydraulic signal sought, so the result exists only if the frame does. Section 5 therefore tests the frame before Section 6 uses it.

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


## 4.6 Sentinel-2 water masks

Water masks follow a frozen NDWI/MNDWI/scene-classification rule on the 20 m registry grid with the BOA offset applied; a coverage gate admits a date for the pre/post planform contrast only when at least 80 % of the footprint is observed, and both periods need three such dates before any inferential statistic is formed. Shoreline contours at three pre-breach levels are extracted with a sub-pixel marching-squares estimator whose numerical correctness was tested against an analytic field before use.

## 4.7 Statistical rules

Every statistic names its independent unit. Absence statements about the literature are made only for a screened sample with a stated denominator, and only after retrieval has been validated against hold-out controls.
