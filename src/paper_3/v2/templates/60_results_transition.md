# 6. HYDRAULIC AND SURFACE TRANSFORMATION RESULTS

## 6.1 Pre-breach water-surface geometry

Before the breach the impounded pool was near-level. Per-reach ICESat-2 slopes ({{claim:V7.2}}) lie inside the historical discharge envelope of the design tables, and the 22–25 April 1970 field-measured free-surface curve, digitised and validated against Table 20 ({{claim:V7.1}}), bounds them from above. The apparent slope in the 180–210 km band is a chainage-geometry artefact, not a hydraulic signal ({{claim:V7.3}}).

## 6.2 Drawdown

The five overpasses of July–September 2023 already carry the post-breach gradient: {{claim:M1.2}} (n = {{claim:M1.2.n}}). The structural change is therefore visible during the transient drainage itself, not only once the system had settled.

## 6.3 Post-breach longitudinal gradient

Across the 14 pre-breach and 14 post-breach overpasses with a chainage span of at least 20 km, the per-overpass Theil–Sen slope moved from {{claim:M1.1}}; the difference has a 95 % bootstrap confidence interval of {{claim:M1.1.unc}}. Every one of the 14 post-breach overpasses is positive. The result is estimator-robust: ordinary least squares gives {{claim:M1.3}} ({{claim:M1.3.unc}}). Each per-overpass slope is a fit through the six beam-median points of one pass over roughly 20–70 km of a reservoir-axis centreline; the claim is about the distribution of these local slopes across dates, not about a single whole-reservoir gradient.

{{table:T3}}

## 6.4 The channel-restricted control

Restricting both periods to the classified main channel weakens the contrast and its interval includes zero: {{claim:M1.4}} on {{claim:M1.4.n}}. This is reported as a limitation of the headline result, which must therefore be read as a statement about the water surface within the former reservoir footprint, not strictly about the channel.

## 6.5 Water-surface heterogeneity

On all ATL13 dates in the footprint, the within-overpass p95–p05 range of water-surface elevation rose from {{claim:M2.1}} ({{claim:M2.1.unc}}; {{claim:M2.1.n}}). This metric is computed on the full ATL13 sample and is distinct from the within-profile range of the slope sample.

## 6.6 Planform transformation

Sentinel-2 water masks with a coverage gate show the transition from one continuous impounded surface to a channel with disconnected remnants: {{claim:M3.1}}. Only one pre-breach date has near-complete coverage, so this contrast is descriptive and carries no significance test; water-body counts are not reported because they scale with the observed fraction of the footprint.

## 6.7 Residual water bodies

Classified residual water bodies sit below the adjacent channel stem: {{claim:M3.2}} ({{claim:M3.2.unc}}; n = {{claim:M3.2.n}}), consistent with disconnected remnants perched in depressions rather than with a continuous surface.

## 6.8 Persistence

Every post-breach overpass from January 2024 to November 2025 carries a positive per-overpass slope; the 14/14 sign consistency and the effect size, not the p-value, are the evidence that the new state persists beyond the drainage transient.

## 6.9 Vegetation succession on the exposed bed

The former water surface — the pool polygon intersected with pre-breach mode water, {{claim:S1.5}} — was classified on every leaf-on Sentinel-2 date into ten surface classes. Median class shares by year moved from {{claim:S1.1a}} through {{claim:S1.1b}} to {{claim:S1.1}} ({{claim:S1.1.unc}}). In mode-class areas the first season was dominated by open water and dry bare sediment ({{claim:S1.2a}}); by 2024 reed and flooded vegetation covered the largest area ({{claim:S1.2b}}); by 2025 dense herbaceous cover had expanded while open water and bare sediment collapsed ({{claim:S1.2}}). The transition matrices show where the vegetation came from: {{claim:S1.3}}; between 2024 and 2025 persistence dominates ({{claim:S1.3.unc}}).

{{table:T6}}

These are measured class shares and areas. They carry no accuracy statement: the 320-point stratified reference sample designed for this purpose has not yet been labelled, and until it is, no error-adjusted area or per-class accuracy is admissible.

## 6.10 Woody encroachment

Spectral-index thresholds cannot separate shrub or tree cover from herbaceous cover, so woody encroachment was followed with Dynamic World probabilities on the same former water surface: {{claim:W1.1}} ({{claim:W1.1.unc}}). Independently, the number of ICESat-2 20 m canopy segments passing quality control over the former pool rose ({{claim:W1.2}}), and the ATL08 canopy-height distribution over the zones is low and right-skewed ({{claim:H1.1}}). Canopy-height *change* is not identifiable from ICESat-2 with the current track geometry (Section 7.10).

## 6.11 Channel widening below the dam

On {{claim:K1.2}}, the wetted width of the channel between the dam and Kherson was measured on every usable Sentinel-1/-2 date from 2017 to 2026. On comparable late-summer dates at Kherson stages within 0.5 m, the width is larger after the breach: {{claim:K1.1}} against the pre-breach {{claim:K1.1a}}. The breach year shows transient braiding: {{claim:K1.1b}}. The water contour is not the geomorphic bank, and bank displacement relative to the September 2023 reference stays at the 40 m detection limit, so the widening is a statement about the wetted surface at matched stage.

## 6.12 Roughness translation

Each surface class was assigned a Manning n range from published priors (Table 5) and the class maps were translated state by state. For the former pool the area-weighted n moved from {{claim:N1.1}}, with {{claim:N1.1.unc}}; the first step at exposure was small ({{claim:N1.1a}}) and the large step came with the 2024 reed expansion ({{claim:N1.1b}}). In the 2025 state the largest classes are {{claim:N1.2}}, against a breach state that was {{claim:N1.2a}}. The floodway below the dam also roughens: {{claim:N1.3}}. These values are a translation of class change through literature priors; no hydraulic observation has calibrated them.

{{table:T5}}

## 6.13 Exposure and first-vegetation timing

An interval-censored first-event analysis on the Sentinel-2 series gives the timing of exposure and of first vegetation: {{claim:S1.4}} ({{claim:S1.4.unc}}). Ninety per cent of the surface that was ever seen non-water had been exposed within about three months of the breach, and most of it was seen vegetated within the first season, with a long tail into 2024.
