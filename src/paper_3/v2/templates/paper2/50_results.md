# 4. THE RESERVOIR BED

## 4.1 Reconstruction and blocked cross-validation

{{claim:V10.3}} ({{claim:V10.3.unc}}; n = {{claim:V10.3.n}}). The boundary condition is the single most consequential choice: {{claim:V10.2}}. The error is one of local bed morphology, not of the vertical frame: {{claim:V10.4}}.

## 4.2 An independent test of the reconstructed bed

The cross-validation of Section 5.9 withholds soundings from a surface built out of soundings, so it measures interpolation error and nothing else. ICESat-2 ground returns acquired over the bed after it dried are independent of every input to the reconstruction, and against them the surface holds to {{claim:B4.1}} ({{claim:B4.1.unc}}; n = {{claim:B4.1.n}}). The residual is not uniform, but neither does it grow smoothly with distance: it is essentially flat within a kilometre of a sounding ({{claim:B4.2a}} in the nearest band, and marginally better between 250 and 500 m, where the bed is less steep than in the near-shore belt that holds most of the survey) and only degrades once the interpolation is extrapolating — {{claim:B4.2}}, on a handful of points. What the stratification shows is a threshold of support rather than a gradient of it. Part of what remains is real: the soundings predate the breach and the photons postdate it, so genuine bed change sits inside the residual and cannot be separated from reconstruction error.

{{table:T8}}

Figure 13 shows both the support dependence and the stability across epoch, season and recession zone.

## 4.3 The datum of the surrounding terrain

The bed is seamed into FABDEM for the land above the water line, so the two must share a datum. Sampled on stable bare land against the same ICESat-2 frame, FABDEM shows {{claim:B4.3}} (n = {{claim:B4.3.n}}), a decimetre-level offset an order below the bed error.

## 4.4 Area-weighted exposure against the design-era hydrography

{{claim:V10.1}} ({{claim:V10.1.unc}}). The historical fraction was used as validation, not calibration. The reconstructed bed also reproduces the published level–area curve ({{claim:V10.5}}) and the exposure fraction is converged with grid resolution ({{claim:V11.1}}).

## 5.1 The downstream zone beds

In the delta, estuary and floodway the soundings-only arm wins in every zone: {{claim:B2.1}}; {{claim:B2.2}}; {{claim:B2.3}}. Shoreline soft constraints and pseudo-points add metre-scale positive bias where the near-shore band holds most of the soundings.

Support, not method, sets what these numbers mean: in the estuary {{claim:B6.1}} ({{claim:B6.1.caveat}}).

## 5.2 The terrain delivered to the hydraulic model

The bed, the downstream zone surfaces and FABDEM are merged into one seamless terrain, which is the object the hydraulic models of the hydraulic-modelling paper (Paper 5) stand on. Against night-time ICESat-2 returns over the whole domain it achieves {{claim:B7.1}} ({{claim:B7.1.unc}}; n = {{claim:B7.1.n}}). That figure is an average over land covers that behave differently ({{claim:B7.1.caveat}}), and the per-class breakdown rather than the pooled statistic is what a modeller needs.

{{table:T7}}

Figure 14 breaks the pooled figure down by land cover.

## 5.3 A second lidar on the same terrain

ICESat-2 and the soundings share an ancestry — both are profiling measurements reduced through the same vertical chain — so a check by a sensor with a different footprint, a different retrieval and a different failure mode is worth more than a larger sample of the same kind. GEDI ground returns over the exposed bed give {{claim:B8.1}} ({{claim:B8.1.unc}}; n = {{claim:B8.1.n}}) on the stratum built to match the ICESat-2 night comparison. The agreement is not unqualified ({{claim:B8.1.caveat}}), but it is reached from a different direction.

That second view also found something the first could not. Sampled against FABDEM as an external control, two source classes of the seam depart from it grossly, and the departure is in the seam rather than in the control. This is a defect, not an uncertainty, and it is reported as such in Section 7.9 rather than folded into an error budget.

## 6.1 Why multi-level contours cannot constrain the shallow bed

{{claim:B3.1}}. {{claim:B3.2}}. The optical shoreline sits at the edge of the emergent-vegetation belt rather than at the water–land boundary, so the signal of level change is smaller than the position bias; the experiment is reported as a negative result.

## 6.2 Shoreline elevation uncertainty

The elevation attached to an optical shoreline carries its own spread, which sets the floor for any contour-based constraint: {{claim:I1.1}}. Broken out by zone, {{claim:I1.2}}.



