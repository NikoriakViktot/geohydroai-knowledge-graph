# 5. VALIDATION RESULTS

## 5.1 The gauge network in one geodetic frame

{{claim:V1.1}} ({{claim:V1.1.unc}}). {{claim:V1.2}} ({{claim:V1.2.unc}}).

{{table:T2}}

## 5.2 The SWOT PIXC vertical chain

The documented correction chain reproduces RiverSP node heights: {{claim:V2.1}} ({{claim:V2.1.unc}}; n = {{claim:V2.1.n}}). RiverSP derives from the same SWOT observation, so this establishes the correction chain, not independent accuracy.

## 5.3 SWOT against ICESat-2

{{claim:V3.1}} ({{claim:V3.1.unc}}). With three co-located overpasses no regression is fitted and no universal centimetric agreement is claimed.

## 5.4 Kherson as a local anchor

{{claim:V4.1}} ({{claim:V4.1.unc}}; n = {{claim:V4.1.n}}).

## 5.5 A validated negative result

{{claim:V4.2}} ({{claim:V4.2.unc}}). No wind or pressure record exists for that day, so wind setup can be neither confirmed nor excluded; this remains a limitation.

## 5.6 Spatial limits of an empirical correction

{{claim:V5.1}} ({{claim:V5.1.unc}}).

## 5.7 The historical sounding datum

{{claim:V6.1}} ({{claim:V6.1.unc}}). That the charted soundings were reduced to the navigation drawdown level is the inference of this study, corroborated by three routes, not a statement of the source. Carried into EVRF2019, {{claim:V6.2}} ({{claim:V6.2.unc}}).

## 5.8 The historical free-surface curve and the pre-breach slopes

{{claim:V7.1}} ({{claim:V7.1.unc}}). {{claim:V7.2}}. {{claim:V7.3}}. The documented seiche and wind-setup magnitudes for this reservoir exceed the mean hydraulic rise over the pool ({{claim:V8.1}}), which is why pre-breach scatter of several centimetres within an overpass is physical.

## 5.9 Bed-surface reconstruction and cross-validation

{{claim:V10.3}} ({{claim:V10.3.unc}}; n = {{claim:V10.3.n}}). The boundary condition is the single most consequential choice: {{claim:V10.2}}. The error is one of local bed morphology, not of the vertical frame: {{claim:V10.4}}.

## 5.9.1 An independent test of the reconstructed bed

The cross-validation of Section 5.9 withholds soundings from a surface built out of soundings, so it measures interpolation error and nothing else. ICESat-2 ground returns acquired over the bed after it dried are independent of every input to the reconstruction, and against them the surface holds to {{claim:B4.1}} ({{claim:B4.1.unc}}; n = {{claim:B4.1.n}}). The residual is not uniform, but neither does it grow smoothly with distance: it is essentially flat within a kilometre of a sounding ({{claim:B4.2a}} in the nearest band, and marginally better between 250 and 500 m, where the bed is less steep than in the near-shore belt that holds most of the survey) and only degrades once the interpolation is extrapolating — {{claim:B4.2}}, on a handful of points. What the stratification shows is a threshold of support rather than a gradient of it. Part of what remains is real — the soundings predate the breach and the photons postdate it, so bed change is inside the residual and cannot be separated from reconstruction error ({{claim:B4.1.caveat}}).

{{table:T8}}

Figure 13 shows both the support dependence and the stability across epoch, season and recession zone.

## 5.9.2 The datum of the surrounding terrain

The bed is seamed into FABDEM for the land above the water line, so the two must share a datum. Sampled on stable bare land against the same ICESat-2 frame, FABDEM shows {{claim:B4.3}} (n = {{claim:B4.3.n}}), a decimetre-level offset an order below the bed error.

## 5.10 Area-weighted exposure

{{claim:V10.1}} ({{claim:V10.1.unc}}). The historical fraction was used as validation, not calibration. The reconstructed bed also reproduces the published level–area curve ({{claim:V10.5}}) and the exposure fraction is converged with grid resolution ({{claim:V11.1}}).

## 5.11 Downstream zone beds

In the delta, estuary and floodway the soundings-only arm wins in every zone: {{claim:B2.1}}; {{claim:B2.2}}; {{claim:B2.3}}. Shoreline soft constraints and pseudo-points add metre-scale positive bias where the near-shore band holds most of the soundings.

Support, not method, sets what these numbers mean: in the estuary {{claim:B6.1}} ({{claim:B6.1.caveat}}).

## 5.11.1 The terrain delivered to the companion paper

The bed, the downstream zone surfaces and FABDEM are merged into one seamless terrain, which is the object the hydraulic models of the companion paper stand on. Against night-time ICESat-2 returns over the whole domain it achieves {{claim:B7.1}} ({{claim:B7.1.unc}}; n = {{claim:B7.1.n}}). That figure is an average over land covers that behave differently ({{claim:B7.1.caveat}}), and the per-class breakdown rather than the pooled statistic is what a modeller needs.

{{table:T7}}

Figure 14 breaks the pooled figure down by land cover.

## 5.11.2 A second lidar on the same terrain

ICESat-2 and the soundings share an ancestry — both are profiling measurements reduced through the same vertical chain — so a check by a sensor with a different footprint, a different retrieval and a different failure mode is worth more than a larger sample of the same kind. GEDI ground returns over the exposed bed give {{claim:B8.1}} ({{claim:B8.1.unc}}; n = {{claim:B8.1.n}}) on the stratum built to match the ICESat-2 night comparison. The agreement is not unqualified ({{claim:B8.1.caveat}}), but it is reached from a different direction.

That second view also found something the first could not. Sampled against FABDEM as an external control, two source classes of the seam depart from it grossly, and the departure is in the seam rather than in the control. This is a defect, not an uncertainty, and it is reported as such in Section 7.9 rather than folded into an error budget.

## 5.12 Why multi-level contours cannot constrain the shallow bed

{{claim:B3.1}}. {{claim:B3.2}}. The optical shoreline sits at the edge of the emergent-vegetation belt rather than at the water–land boundary, so the signal of level change is smaller than the position bias; the experiment is reported as a negative result.

## 5.13 Shoreline elevation uncertainty

{{claim:I1.1}}. {{claim:I1.2}}.



## 5.14 Validation synthesis

Six independent checks were performed before any hydraulic or surface result was interpreted; one (the 5 April 2023 anomaly) failed and is reported as failed. The reconstructed bed carries a cross-validated point accuracy and a validated area statistic; it carries no rate of change and no accuracy beyond 250 m, and it is released with those limits stated.
