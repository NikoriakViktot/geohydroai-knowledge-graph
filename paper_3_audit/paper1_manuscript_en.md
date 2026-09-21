# From impounded pool to river: water-surface geometry and post-breach hydraulic transformation of the former Kakhovka Reservoir

## ABSTRACT

**Background.** The destruction of the Kakhovka Dam on 6 June 2023 drained a 2 155 km² reservoir on the lower Dnipro within days. Whether the residual system now behaves as a river and over what bed it flows are questions a water-area time series cannot answer. They require water-surface geometry and a bed that are commensurable in one vertical frame.

**Method.** Seven hydrological gauges, ICESat-2 ATL13 laser altimetry, SWOT PIXC/RiverSP radar interferometry and a legacy navigation-chart survey are harmonised into EVRF2019 normal heights through the EGG2015 quasigeoid and the EPSG:9902 operation, with the permanent-tide convention of each product handled explicitly. Per-overpass longitudinal water-surface slopes are estimated with a robust (Theil–Sen) fit, one overpass being the independent unit. The frame is tested before it is used: against the gauges, between the two satellites, and against a 1970 field survey of the same water surface.

**Results.** Within the former reservoir footprint the per-overpass slope changed from Theil-Sen median +0.090 cm/km pre -> +3.314 cm/km post; difference +3.223 cm/km (95 % CI 95% CI [+1.994, +5.071] cm/km; permutation p = 1.00e-04; Mann-Whitney p = 3.92e-05; positive slopes pre 10/14 (sign test p = 0.180) vs post 14/14 (p = 1.22e-04)), and within-overpass water-surface heterogeneity rose from 0.117 m -> 0.397 m, difference +0.280 m. The fast-sampling SWOT orbit recorded the outlet through the drawdown itself: outlet 5.63 m EVRF2019 on 13 June, from 17.53 m on 31 May.

**Conclusions.** The reservoir did not merely shrink: its water surface acquired a persistent downstream gradient, measurable only once four differently-referenced surfaces were reduced to one frame. Each statement rests on a measured quantity with a stated uncertainty; where a quantity cannot be measured with the data at hand — a post-breach slope–discharge law, a rate of bed change — the manuscript says so rather than inferring it. The bed over which that surface now runs is reconstructed in a companion paper (Paper 2), and the consequences for roughness and conveyance in two further ones.

**Keywords:** satellite altimetry; ICESat-2; SWOT; water-surface slope; vertical datum; EVRF2019; dam breach; reservoir drawdown; hydraulic regime; Kakhovka

# 1. INTRODUCTION

## 1.1 The Kakhovka Reservoir and the 2023 dam breach

The Kakhovka Reservoir was the lowest of the Dnipro cascade: at its normal impoundment level of 16.00 m (Baltic 1977) it held 18.19 km³ over 2 155 km², with a navigation drawdown level of 14.00 m and a dead-volume level of 12.7 m, as tabulated in the original design documentation. On 6 June 2023 the dam was breached and the pool drained within days. What remained was a river running through its former bed, a set of disconnected remnant water bodies, and some two thousand square kilometres of freshly exposed sediment.

## 1.2 Why water area is not enough

Post-breach studies of the site have characterised the event predominantly through planform quantities — water extent, exposed area and their change ((Vyshnevskyi et al., 2023); (Maksymenko et al., 2026)). 

> **[PENDING OPEN41]** §7.8 literature claim LK1.1/LK1.2 not yet supportable (RETRIEVAL_UNVALIDATED)
> `backs: M1.1,M3.1` · `section: 1.2 Why water area is not enough`
> `unblock_by: verify hold-out positive controls for T03, T19; run --step retrieve, calibrate, gate` · `status: blocked`
> `blocks_submission: yes`

 Equal water areas can correspond to fundamentally different hydraulic regimes: a level pool and a sloping channel may occupy the same footprint. A regime change is a change in the geometry of the water surface, and detecting it requires elevations that are commensurable across sensors and epochs.

## 1.3 Longitudinal water-surface geometry from spatial altimetry

ICESat-2 photon-counting lidar and SWOT Ka-band interferometry observe water-surface elevation along tracks and across swaths rather than at virtual stations, so they can resolve the longitudinal gradient of a reach directly ((Scherer et al., 2022); (Musaeus et al., 2024); (Dhote et al., 2024)), and reach-scale slope is now produced globally from ICESat-2 ((Scherer et al., 2023)) and mapped dynamically by SWOT ((Jiang et al., 2025); (Ledauphin et al., 2025)) — provided that thousands of along-track measurements from one pass are not mistaken for thousands of independent observations, and provided that heights reported on different ellipsoids, geoids and permanent-tide conventions are reduced to one frame before they are compared. Hydraulic models informed by such elevations are only as good as that reduction ((Bauer-Gottwein et al., 2023)).

## 1.4 The vertical reference as the enabling step

Gauge stages in Baltic 1977, ICESat-2 heights in a tide-free ellipsoidal system, SWOT heights with tide fields supplied but not applied, and a legacy survey reduced to an operating level are four surfaces. The corrections between them are of the same order as the hydraulic signal sought — centimetres per kilometre over tens of kilometres — so the harmonisation is not a preprocessing convenience but the method itself. Height-datum unification is a mature discipline in geodesy ((Barzaghi et al., 2020); (Featherstone et al., 2011); (Schwabe et al., 2026)), and the consequence of neglecting it — an apparent gradient produced by the levelling network rather than by the water — is documented ((Penna et al., 2013)).

## 1.5 Objectives

This paper asks three questions. (i) Can gauges, two satellite altimeters and a legacy hydrographic survey be brought into one vertical frame closely enough that a centimetre-per-kilometre gradient survives the reduction? (ii) Once they are, did the water surface within the former reservoir footprint change hydraulic regime, and does the change persist beyond the drawdown transient? (iii) Does the historical record independently corroborate the impounded state the satellites report before the breach? Where the data cannot answer — a post-breach slope–discharge relation — the paper states the limitation instead of inferring an answer. The bed beneath this surface is reconstructed in a companion paper (Paper 2); what the geometry and the vegetating surface mean for roughness and conveyance is the subject of two further ones.

# 2. STUDY AREA AND OBSERVATION GEOMETRY

The study system is registered in four analysis zones defined once in a geometry registry and loaded by every script: ZONE_1 (the former reservoir and the lower Dnipro, 11 412 km², with the 2 144 km² reservoir core), ZONE_2 (the Kherson delta, 1 677 km²), ZONE_3 (the Dnipro–Buh estuary, 7 621 km²) and ZONE_4 (the dam-to-Kherson floodway, defined as the June 2023 Sentinel-1 flood envelope plus a 1 km buffer, 695 km²). Chainage for the reservoir profiles is measured from the dam along a reservoir-axis centreline; the historical reach boundaries of the design documentation are placed on it where tie points allow — a limitation, since only two of five ties are usable (ratio ~1.36 at two independent points; a ~30 km residual is isolated to reach 1) — and reach 5 lies outside the mapped domain. SWOT's fast-sampling calibration orbit crossed both the reservoir outlet and the reach below the dam daily through the breach fortnight, so the mission observed the drawdown and the flood wave as they happened; its science-orbit LakeSP passes continue over the residual water bodies from late July 2023. What the calibration orbit did not give is repeated coverage of the whole pool on one date, so SWOT constrains the outlet and the downstream profile rather than the reservoir-wide slope.



> **[PENDING FIG01]** Figure 1 — study system, gauges, zones, ICESat-2 tracks by period, SWOT coverage
> `backs: V1.3,M1.1` · `section: 2 Study area`
> `produces: SWOT-DNIPRO scripts (V0 gauge network + SFig01 + Fig12 composite)` · `status: not_started`
> `blocks_submission: yes`



# 3. DATA

## 3.1 Gauges and sea posts

Six reservoir gauges share a zero of 12.000 m BS-77 and the Kherson post 80805 sits at −5.000 m BS-77 (reservoir 12.000 m BS-77; Kherson -5.000 m BS-77); the reservoir series end on 31 December 2021 and Kherson runs daily through 2025. The 2023 UkrHMI yearbooks for the seas and river mouths add the estuary posts, extracted grid by grid and checked against the printed monthly statistics; at Ochakiv the breach surge appears as annual max 573 cm on 8.06, mean 487 cm, min 444 cm.

## 3.2 ICESat-2 ATL13 and ATL08

ATL13 v7 water-surface heights (ITRF2020, tide-free) are used in three samples that must not be confused: the slope sample (14 pre-breach, 5 drawdown and 14 post-breach overpasses with a chainage span of at least 20 km), the heterogeneity sample (all ATL13 dates in the footprint, 192 pre and 23 post) and the exposed-bed sample (28 tracks).

## 3.3 SWOT

SWOT PIXC and RiverSP granules over Kherson (cycles 482, 511 and 521, April–May 2023) supply the cross-sensor and gauge validation. The Ukraine-clipped LakeSP archive exists but is not used here: a lake-averaged product is physically inappropriate for a sloping, fragmenting water body.

## 3.4 Sentinel-2 water masks

Sentinel-2 L2A scenes processed on a 20 m registry grid (EPSG:32636) with the BOA additive offset applied supply the coverage-gated water masks of the planform analysis (Section 4.6); 53 dates cover the former pool. The full surface classification, the Sentinel-1 masks and the supervised inundation work belong to Paper 3.

## 3.5 Legacy hydrography

Eight photographed pages of the Dnipro reservoirs monograph (Tables 19–21, Figures 13–16) supply the design values and the 1970 longitudinal free-surface survey used here as an independent check. The 7 514 chart soundings are the subject of the bed-reconstruction paper (Paper 2); this paper uses them only to establish which reduction level they carry (Section 5.7).

## 3.6 Geodetic reference data

official correction at the posts 0.216 m The EGG2015 quasigeoid grid used for the ICESat-2 and SWOT branches is the 1′×1′ raster whose provenance is discussed in Section 7.9.



> **[PENDING TAB01]** Table 1 — data sources (machine-generated from the snapshot inventories; must state the three ICESat-2 samples and the single full-coverage pre-breach Sentinel-2 date)
> `backs: V1.3,M1.1,M2.1,M3.1,S1.5` · `section: 3 Data`
> `produces: knoweledg_graf src/paper_3/v2/assemble.py TABLE_SPECS T1` · `status: not_started`
> `blocks_submission: yes`



# 4. METHODS

The harmonisation of Section 4.1 is the method of this paper, not preparation for it: the corrections between the four surfaces are of the same order as the hydraulic signal sought, so the result exists only if the frame does. Section 5 therefore tests the frame before Section 6 uses it.

## 4.1 Vertical harmonisation: the four surfaces

Gauge stages are carried into EVRF2019 normal heights by the official EPSG:9902 operation, sampled from the production grid at each station (10,194 daily values transformed); the offset is spatially varying, 0.1715 to 0.2157 m, so no single national constant is admissible. ICESat-2 ATL13 heights are reduced with the EGG2015 quasigeoid after adding the ATL03 tide-free-to-mean-tide term, which moves the six-station corrector by . SWOT PIXC heights are reduced by subtracting the solid-earth, load and pole tides that the product supplies but does not apply, never the geoid field, and then by the same quasigeoid. The residual permanent-tide term between a mean-tide crust and a zero-tide quasigeoid (1–4 cm at this latitude) is carried as its own line in the uncertainty budget.

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

# 5. THE FRAME: DOES THE HARMONISATION HOLD?

## 5.1 The gauge network in one geodetic frame

All seven gauge records are carried into EVRF2019 by the official operation applied at each station's own coordinates, not by a national mean: 10,194 daily values transformed (grid sampler agrees with an independent implementation to 0.000 mm). The offset is not constant along the reach — 0.1715 to 0.2157 m (spread 44 mm; grid accuracy 0.068 m (EPSG registry)) — which is why a single number would have been wrong at both ends of the pool.

**Table 2. The vertical chain: each step of the harmonisation and the residual it leaves.**

| Step | Result | n |
|---|---|---|
| Gauge stages carried to EVRF2019 (EPSG:9902) | 10,194 daily values transformed | 7 |
| Spatial variation of the BS-77 → EVRF2019 offset | 0.1715 to 0.2157 m | 8 |
| SWOT PIXC correction chain against RiverSP | -0.0010 m for the documented chain | 1023 |
| SWOT against ICESat-2, best collocation | best pair 2023-05-14: +0.0036 m (dt -2.28 h, 3417 segments) | 3 |
| SWOT against the Kherson gauge | +0.0264 m (satellite - gauge) | 9 |
| Transfer of the reservoir alignment constant to Kherson | reservoir -0.130 m [-0.184, -0.081] (n=5 stations) vs Kherson +0.0051 m [-0.0515, +0.0608] | 6 |
| Reduction level of the legacy soundings | 14.00 m: +0.0979 m [-0.0756, +0.3028], zero inside CI / 16.00 m: -1.9021 m [-2.0787, -1.6848], zero outside CI | 28 |
| Historical reference level in EVRF2019 | 14.162 to 14.216 m EVRF2019, median 14.185 m | 7514 |
| Digitised 1970 free-surface curve against Table 20 | median deviation +0.022 m; NMAD 0.045 m | 12 |
| Pre-breach ICESat-2 slopes in the impounded reach | 0-180 km: +0.00, -0.06, +0.06 cm/km; 210-240 km: +0.13 cm/km | 5 |
| Production BS-77 → EVRF2019 grid | official correction at the posts 0.216 m | — |

*Residuals are medians unless a confidence interval is given. The quasigeoid raster in production (G2) has no citable upstream source; see Section 7.9.*


## 5.2 The SWOT PIXC vertical chain

The documented correction chain reproduces RiverSP node heights: -0.0010 m for the documented chain (alternatives: +0.0720 m uncorrected, +0.1451 m sign-reversed, -23.89 m geoid subtracted; n = 1023). RiverSP derives from the same SWOT observation, so this establishes the correction chain, not independent accuracy.

## 5.3 SWOT against ICESat-2

Where the two sensors pass within hours of each other they agree at the centimetre level with nothing applied between them: best pair 2023-05-14: +0.0036 m (dt -2.28 h, 3417 segments) (full set: 2023-04-05 dt +14.2 h -> -0.1810 m; 2023-05-04 dt +11.1 h -> +0.0597 m; 2023-05-14 dt -2.3 h -> +0.0036 m). With three co-located overpasses no regression is fitted and no universal centimetric agreement is claimed.

## 5.4 Kherson as a local anchor

Against the one continuous in-situ record in the downstream reach, the satellite branch sits within a few centimetres of the gauge: +0.0264 m (satellite - gauge) (NMAD 0.0413 m; 95% CI [-0.0420, +0.0394]; median moves 8.4 cm across aggregation radii 0.5-5 km; n = 9). The agreement is local to Kherson and is not evidence for the pool.

## 5.5 A validated negative result

observed -18.1 cm; expected +5.9 cm from gauge +10.0 cm/day over dt 14.2 h (residual after applying the hydrological term: -24.0 cm (worse)). No wind or pressure record exists for that day, so wind setup can be neither confirmed nor excluded; this remains a limitation.

## 5.6 Spatial limits of an empirical correction

An alignment constant estimated in the reservoir does not survive the journey downstream: reservoir -0.130 m [-0.184, -0.081] (n=5 stations) vs Kherson +0.0051 m [-0.0515, +0.0608] (station-to-station NMAD 0.033 m; radius swing 1.8 cm (Rozumivka) and 5.0 cm (Kherson) over 1-10 km). The two estimates have disjoint intervals, so the correction is treated as local rather than as a property of the sensor.

## 5.7 The historical sounding datum

14.00 m: +0.0979 m [-0.0756, +0.3028], zero inside CI | 16.00 m: -1.9021 m [-2.0787, -1.6848], zero outside CI (NMAD 0.498 m over n=94 points; the 16.00 m assumption is rejected at ~1.90 m). That the charted soundings were reduced to the navigation drawdown level is the inference of this study, corroborated by three routes, not a statement of the source. Carried into EVRF2019, 14.162 to 14.216 m EVRF2019, median 14.185 m (median offset +0.1854 m; spread 54 mm).

## 5.8 The historical free-surface curve and the pre-breach slopes

median deviation +0.022 m; NMAD 0.045 m (flat pool <180 km (n=5): median +0.047 m, max |dev| 0.057 m). 0-180 km: +0.00, -0.06, +0.06 cm/km; 210-240 km: +0.13 cm/km. all fits in the band are single overpasses; chainage span / ground span median 1.34 (p90 1.94, max 2.65). The documented seiche and wind-setup magnitudes for this reservoir exceed the mean hydraulic rise over the pool (mean hydraulic rise over 183 km: 0 cm (Qmin), 6 cm (Q20%), 42 cm (Q1%) | seiche half-range at an antinode 17.5 cm | wind setup at an end 35 cm (15 m/s) to 148 cm (25 m/s)), which is why pre-breach scatter of several centimetres within an overpass is physical.

## 5.9 What the frame is worth

Six independent checks were performed before any hydraulic or surface result was interpreted; one (the 5 April 2023 anomaly) failed and is reported as failed. The reconstructed bed carries a cross-validated point accuracy and a validated area statistic; it carries no rate of change and no accuracy beyond 250 m, and it is released with those limits stated.

# 6. THE MEASUREMENT

## 6.1 Pre-breach water-surface geometry

Before the breach the impounded pool was near-level. Per-reach ICESat-2 slopes (0-180 km: +0.00, -0.06, +0.06 cm/km; 210-240 km: +0.13 cm/km) lie inside the historical discharge envelope of the design tables, and the 22–25 April 1970 field-measured free-surface curve, digitised and validated against Table 20 (median deviation +0.022 m; NMAD 0.045 m), bounds them from above. The apparent slope in the 180–210 km band is a chainage-geometry artefact, not a hydraulic signal (all fits in the band are single overpasses; chainage span / ground span median 1.34 (p90 1.94, max 2.65)).

## 6.2 Drawdown

The five overpasses of July–September 2023 already carry the post-breach gradient: Theil-Sen median +3.245 cm/km (2023-07-07..2023-09-07) (n = 5). The structural change is therefore visible during the transient drainage itself, not only once the system had settled.

## 6.2.1 The drawdown and the flood wave from orbit

That SWOT caught this event is not a new observation: the flood below the dam has been described from the same mission and used to test outburst-flood models ((Lehnigk et al., 2026)). What is added here is the outlet itself, carried in the same vertical frame as the gauges and the bed. Because the calibration orbit repeated daily over the outlet, the emptying of the pool was recorded directly rather than inferred: outlet 5.63 m EVRF2019 on 13 June, from 17.53 m on 31 May (n = 3 SWOT nodes on the date). Below the dam the same passes describe the wave that carried that water away, rising rise 9.10 m at 15 km (peak 10.24 m on 2023-06-07) and decaying downstream to about two metres by 80 km. Both series are snapshots from a handful of nodes per date (Node counts per date are small (1-3) and some dates rest on a single node; the series is a sequence of snapshots, not a gauged hydrograph.), so they are read as a sequence of observed water surfaces, not as a hydrograph.

**Table 4. The drawdown and the flood wave, as recorded by SWOT.**

| Quantity | Value | n |
|---|---|---|
| Outlet water surface, 31 May → 13 June 2023 | outlet 5.63 m EVRF2019 on 13 June, from 17.53 m on 31 May | 3 SWOT nodes on the date |
| Rise above the pre-breach surface, 15 km below the dam | rise 9.10 m at 15 km (peak 10.24 m on 2023-06-07) | 48 nodes in the bin |
| Longitudinal slope during the drawdown overpasses | Theil-Sen median +3.245 cm/km (2023-07-07..2023-09-07) | 5 |

*Node counts per date are small; the series is a sequence of observed surfaces, not a gauged hydrograph.*


Figure 15 shows the outlet series against the upstream gauge and the downstream rise profile.

## 6.3 Post-breach longitudinal gradient

Across the 14 pre-breach and 14 post-breach overpasses with a chainage span of at least 20 km, the per-overpass Theil–Sen slope moved from Theil-Sen median +0.090 cm/km pre -> +3.314 cm/km post; difference +3.223 cm/km; the difference has a 95 % bootstrap confidence interval of 95% CI [+1.994, +5.071] cm/km; permutation p = 1.00e-04; Mann-Whitney p = 3.92e-05; positive slopes pre 10/14 (sign test p = 0.180) vs post 14/14 (p = 1.22e-04). Every one of the 14 post-breach overpasses is positive. The result is estimator-robust: ordinary least squares gives OLS +0.038 -> +2.781 cm/km, difference +2.744 cm/km (95% CI [+1.454, +4.323]; permutation p = 3.00e-04). Each per-overpass slope is a fit through the six beam-median points of one pass over roughly 20–70 km of a reservoir-axis centreline; the claim is about the distribution of these local slopes across dates, not about a single whole-reservoir gradient.

**Table 3. Water-surface geometry before and after the breach.**

| Quantity | Pre → post | Uncertainty | n |
|---|---|---|---|
| Per-overpass longitudinal slope, footprint-wide | Theil-Sen median +0.090 cm/km pre -> +3.314 cm/km post; difference +3.223 cm/km | 95% CI [+1.994, +5.071] cm/km; permutation p = 1.00e-04; Mann-Whitney p = 3.92e-05; positive slopes pre 10/14 (sign test p = 0.180) vs post 14/14 (p = 1.22e-04) | 14 pre / 14 post |
| Slope during the drawdown | Theil-Sen median +3.245 cm/km (2023-07-07..2023-09-07) | sits between the pre-breach and post-breach medians | 5 |
| Slope, sensitivity to the chainage span | OLS +0.038 -> +2.781 cm/km, difference +2.744 cm/km | 95% CI [+1.454, +4.323]; permutation p = 3.00e-04 | 14 pre / 14 post |
| Slope, channel-restricted control | Theil-Sen difference +1.109 cm/km, 95% CI [-0.086, +2.186] | permutation p = 0.0009; post positive 4/6; Cliff's delta -0.467 | 20 pre / 6 post |
| Within-overpass water-surface heterogeneity | 0.117 m -> 0.397 m, difference +0.280 m | 95% CI [+0.138, +0.367] m; permutation p = 5.00e-05; Mann-Whitney p = 1.57e-11 | 192 pre / 23 post |
| Planform: water area | pre-breach 2023-06-05: 2 water bodies, 2,129 km2, largest component 99.995% of water area / post-breach median 503 bodies, 289 km2, largest component 58.2% | post-breach range 161-634 bodies; largest-component fraction 31.5-80.6% | 1 pre / 4 post |
| Planform: residual water bodies | median offset -0.595 m | NMAD 0.459 m; p05 -1.19, p95 +0.51 m; 15.3% lie above the stem | 678 |
| Drawdown exposure, reconstructed vs historical | whole mapped reservoir: historical 12.9% vs reconstructed 12.77% (diff -0.18 pp) | across interpolators 12.61-13.29% (spread 0.68 pp); by reach historical 3.9 / 9.4 / 26.0 % vs reconstructed 7.5 / 9.3 / 21.7 % | 3 |


## 6.4 The channel-restricted control

Restricting both periods to the classified main channel weakens the contrast and its interval includes zero: Theil-Sen difference +1.109 cm/km, 95% CI [-0.086, +2.186] on 20 pre / 6 post. This is reported as a limitation of the headline result, which must therefore be read as a statement about the water surface within the former reservoir footprint, not strictly about the channel.

## 6.5 Water-surface heterogeneity

On all ATL13 dates in the footprint, the within-overpass p95–p05 range of water-surface elevation rose from 0.117 m -> 0.397 m, difference +0.280 m (95% CI [+0.138, +0.367] m; permutation p = 5.00e-05; Mann-Whitney p = 1.57e-11; 192 pre / 23 post). This metric is computed on the full ATL13 sample and is distinct from the within-profile range of the slope sample.

## 6.6 Planform transformation

Sentinel-2 water masks with a coverage gate show the transition from one continuous impounded surface to a channel with disconnected remnants: pre-breach 2023-06-05: 2 water bodies, 2,129 km2, largest component 99.995% of water area | post-breach median 503 bodies, 289 km2, largest component 58.2%. Only one pre-breach date has near-complete coverage, so this contrast is descriptive and carries no significance test; water-body counts are not reported because they scale with the observed fraction of the footprint.

## 6.7 Residual water bodies

Classified residual water bodies sit below the adjacent channel stem: median offset -0.595 m (NMAD 0.459 m; p05 -1.19, p95 +0.51 m; 15.3% lie above the stem; n = 678), consistent with disconnected remnants perched in depressions rather than with a continuous surface.

## 6.8 Persistence

Every post-breach overpass from January 2024 to November 2025 carries a positive per-overpass slope; the 14/14 sign consistency and the effect size, not the p-value, are the evidence that the new state persists beyond the drainage transient.

## 6.9 Downstream geometry

The channel between the dam and Kherson has also changed geometry — its wetted width on comparable dates is larger after the breach — but that result, its method and its figure belong to the companion paper, where it serves as a validation target for the two-dimensional model.

# 7. INTERPRETATION

Reaches released from an impoundment adjust in ways that are neither uniform nor immediate ((Nichols et al., 2017)), and incisional channels formed after dam removal widen and narrow through migrating fronts rather than settling monotonically ((Cantelli et al., 2004); (Cantelli et al., 2007)).

## 7.1 From an impounded to a river-dominated state

A pool held at a normal impoundment level has no longitudinal gradient to speak of: its surface is set by the dam, and the hydraulic rise over the reach is smaller than the wind setup and seiche that cross it (mean hydraulic rise over 183 km: 0 cm (Qmin), 6 cm (Q20%), 42 cm (Q1%) | seiche half-range at an antinode 17.5 cm | wind setup at an end 35 cm (15 m/s) to 148 cm (25 m/s)). A river's surface is set by its bed and its discharge, and it slopes. The measured change is between those two conditions, and it is the persistence that distinguishes it from the drawdown transient: the gradient is present in the drawdown overpasses and still present through 2025 (Theil-Sen median +3.245 cm/km (2023-07-07..2023-09-07)).

Reaches released from an impoundment adjust in ways that are neither uniform nor immediate ((Nichols et al., 2017)), and incisional channels formed after dam removal widen and narrow through migrating fronts rather than settling monotonically ((Cantelli et al., 2004); (Cantelli et al., 2007)). What is observed here is the surface expression of the earliest part of that adjustment, over a reach two orders of magnitude larger than the removals from which the canon is drawn.

Before the breach the sign of the per-overpass slope was indistinguishable from a coin flip and the surface varied by centimetres over tens of kilometres; after it, every overpass shows water rising upstream at a few centimetres per kilometre, with metre-scale variability, and the change was already present during drainage. The comparison with reservoir-to-river transitions documented elsewhere belongs to Section 7.8 and is withheld until the literature retrieval has been validated.

## 7.2 Why area alone misses the transition

The planform record shows one connected surface becoming a channel with remnants, but only one pre-breach date has full coverage and body counts scale with the observed fraction. The altimetric quantities — slope and within-overpass range — are independent of tile coverage and carry the inference.

## 7.3 What the satellites can and cannot resolve

Each per-overpass slope is local, through six beam points; agreement between SWOT and ICESat-2 is centimetric only under close temporal collocation; a lake-averaged product cannot describe a sloping, fragmenting body. Canopy-height change from ICESat-2 requires repeat-track pairs that the current geometry does not provide.

## 7.4 The vertical frame

One official grid, one quasigeoid and an explicit tide term are the minimum; empirical station correctors are local and do not transfer between reaches (reservoir -0.130 m [-0.184, -0.081] (n=5 stations) vs Kherson +0.0051 m [-0.0515, +0.0608]). The provenance of the quasigeoid raster is an open citation problem (Section 7.9).

## 7.5 What the legacy hydrography contributes

The design documentation fixes the pre-breach surface twice — the operating levels and the 1970 field curve — and the chart soundings, once their reduction level is established, give a bed whose drawdown exposure agrees with the historical statistic within the datum band. The 0.40 m spread among the three routes to that level, not a confidence interval, is the honest uncertainty.

## 7.6 What the geometry is for

The per-overpass slopes are the independent observation a hydraulic model of the current system must reproduce without having been calibrated on them, and they are handed to Paper 5 on that condition. The terrain such a model stands on is reconstructed in Paper 2.

## 7.8 Comparison with literature

Positioning statements below are constructed from the L-series literature evidence matrix; each is traceable to its theses, to paper-level relations and to DOIs with verified passages. No priority is asserted here: where a claim is not yet supportable, the marker says why.

The section is organised in three parallel layers, because the study proves three different things and each rests on a different body of literature. An absence in the Kakhovka layer is what this study contributes; an absence in the transition or the data layer is a weakness in its argument, not a finding.

### K. What has already been observed at Kakhovka

*What has already been studied at this site after the breach?* — a gap here is the novelty case.

Boolean slice S1_kakhovka_status_quo has not yet been screened under validated retrieval; the statement “Among the post-breach Kakhovka studies recovered and screened by slice S1, none quantifies a longitudinal water-surface gradient.” is withheld. [LK1.1]

> **[PENDING OPEN41]** §7.8 literature claim LK1.1 is not yet supportable (RETRIEVAL_UNVALIDATED)
> `backs: M1.1, M3.1` · `section: 7.8 Comparison with literature`
> `next_step: verify a hold-out positive control for T03, T19` · `status: blocked`
> `blocks_submission: yes`

Boolean slice S1_kakhovka_status_quo has not yet been screened under validated retrieval; the statement “The screened Kakhovka literature characterises the event predominantly through planform quantities — water extent, exposed area and their change — and the measured share is reported as such.” is withheld. [LK1.2]

> **[PENDING OPEN42]** §7.8 literature claim LK1.2 is not yet supportable (RETRIEVAL_UNVALIDATED)
> `backs: M3.1` · `section: 7.8 Comparison with literature`
> `next_step: verify a hold-out positive control for T19, T03` · `status: blocked`
> `blocks_submission: yes`

Published hydrodynamic reconstructions of the Kakhovka outburst attribute stage error to their terrain input, and the vertical reference of that input is treated as a secondary concern rather than as a harmonised frame. Closest precedent in the corpus: [doi:10.1111/j.1752-1688.2008.00263.x]. [LK1.3]

> **[PENDING OPEN43]** §7.8 literature claim LK1.3 is not yet supportable (RETRIEVAL_UNVALIDATED)
> `backs: V6.1, V10.3` · `section: 7.8 Comparison with literature`
> `next_step: verify a hold-out positive control for T02, T08` · `status: blocked`
> `blocks_submission: yes`

### T. What is known about reservoir-to-river hydraulic transitions

*Is the physical transition established, and how is it diagnosed?* — a gap here is a weakness, not a contribution: this layer should be well populated.

Dam removal, dam failure and rapid drawdown are established as producing a transition from impounded conditions towards channelised fluvial hydraulics, expressed through water-surface slope, velocity, shear stress and connectivity. Closest precedent in the corpus: [doi:10.1111/j.1752-1688.2008.00263.x]. [LT1.1]

> **[PENDING OPEN44]** §7.8 literature claim LT1.1 is not yet supportable (RETRIEVAL_UNVALIDATED)
> `backs: M1.1` · `section: 7.8 Comparison with literature`
> `next_step: verify a hold-out positive control for T01, T02` · `status: blocked`
> `blocks_submission: yes`

Longitudinal water-surface slope is treated in the literature as a diagnostic state variable for hydraulic regime, separable from water-surface area, and a sustained change in it is read as a regime shift rather than a relaxation transient. Closest precedent in the corpus: [doi:10.1029/2018gl077933]. [LT1.2]

> **[PENDING OPEN45]** §7.8 literature claim LT1.2 is not yet supportable (RETRIEVAL_UNVALIDATED)
> `backs: M1.1, M2.1` · `section: 7.8 Comparison with literature`
> `next_step: verify a hold-out positive control for T03, T23` · `status: blocked`
> `blocks_submission: yes`

### D. What the data and methods can measure

*What can SWOT, ICESat-2, gauges and legacy surveys measure, and how must they be harmonised?* — a gap here is a weakness, not a contribution: this layer should be well populated.

SWOT and ICESat-2 can each resolve inland water-surface geometry at the precision a longitudinal gradient requires, while reporting heights on different reference surfaces and in different permanent-tide conventions. Closest precedent in the corpus: [doi:10.1016/j.rse.2021.112876]. [LD1.1]

> **[PENDING OPEN46]** §7.8 literature claim LD1.1 is not yet supportable (RETRIEVAL_UNVALIDATED)
> `backs: V2.1, V3.1` · `section: 7.8 Comparison with literature`
> `next_step: verify a hold-out positive control for T05, T06, T07` · `status: blocked`
> `blocks_submission: yes`

Vertical-datum choice and permanent-tide convention introduce offsets of centimetre to decimetre size, of the same order as the hydraulic signal being measured, and those offsets vary spatially across a domain of this size. Closest precedent in the corpus: [doi:10.1007/s00190-010-0422-2]. [LD1.2]

> **[PENDING OPEN47]** §7.8 literature claim LD1.2 is not yet supportable (RETRIEVAL_UNVALIDATED)
> `backs: V1.2, V3.1` · `section: 7.8 Comparison with literature`
> `next_step: verify a hold-out positive control for T08, T09, T10, T11` · `status: blocked`
> `blocks_submission: yes`

Boolean slice S2_altimetry_vertical_datum has not yet been screened under validated retrieval; the statement “Among inland-water altimetry studies screened by slice S2 that engage with a vertical reference, the share naming a permanent-tide convention is reported as measured.” is withheld. [LD1.3]

> **[PENDING OPEN48]** §7.8 literature claim LD1.3 is not yet supportable (RETRIEVAL_UNVALIDATED)
> `backs: V1.2` · `section: 7.8 Comparison with literature`
> `next_step: verify a hold-out positive control for T08, T09, T10, T11` · `status: blocked`
> `blocks_submission: yes`

Historical bathymetric surveys and reservoir design records can serve as independent quantitative validation targets for modern satellite measurements once their reduction datum has been established. Closest precedent in the corpus: [doi:10.3390/ijgi9040258]. [LD1.4]

> **[PENDING OPEN49]** §7.8 literature claim LD1.4 is not yet supportable (RETRIEVAL_UNVALIDATED)
> `backs: V6.1, V7.1, V10.1` · `section: 7.8 Comparison with literature`
> `next_step: verify a hold-out positive control for T15, T16, T17, T18` · `status: blocked`
> `blocks_submission: yes`


## 7.8 Implications for monitoring

The quantities that change first and persist — slope and within-overpass heterogeneity — are derivable from open satellite archives once a vertical frame is fixed; the quantities that cannot yet be derived — post-breach discharge and a rate of bed change — name the observations still needed.

## 7.9 Limitations

The channel-restricted slope control is underpowered (Theil-Sen difference +1.109 cm/km, 95% CI [-0.086, +2.186]). The slope–discharge relation after the breach is not testable: . The survey epoch of the soundings is unrecorded (no epoch), the Baltic realisation of the historical tables is unstated, three co-located SWOT–ICESat-2 overpasses exist, and no wind record covers the 5 April 2023 anomaly. The 1′×1′ EGG2015 raster has no citable upstream source; this is a citation problem, not a numerical one, and it blocks submission until resolved. 

> **[PENDING OPEN99]** EGG2015 provenance (G2) unresolved
> `backs: G2` · `section: 7.9 Limitations`
> `unblock_by: obtain the 1′ EGG2015 grid with licence, or re-run on the public 10′×15′ grid and report the difference` · `status: blocked`
> `blocks_submission: yes`



# 8. CONCLUSIONS

Within the former Kakhovka Reservoir footprint the per-overpass longitudinal water-surface slope changed from Theil-Sen median +0.090 cm/km pre -> +3.314 cm/km post; difference +3.223 cm/km, with every post-breach overpass positive, and within-overpass heterogeneity rose from 0.117 m -> 0.397 m, difference +0.280 m. These statements rest on harmonised elevations — one official transformation grid, one quasigeoid, an explicit permanent-tide term — and on a legacy survey whose reduction level was established by three independent routes rather than assumed. The same frame let the drawdown itself be read from orbit (outlet 5.63 m EVRF2019 on 13 June, from 17.53 m on 31 May). What the data cannot yet say — a post-breach slope–discharge law — is stated as such rather than inferred. The bed beneath this surface is reconstructed in Paper 2; what the new geometry means for roughness, conveyance and stage is the question of Papers 4 and 5, which take these profiles as a target they must meet without being calibrated on them.

# SUPPLEMENTARY MATERIAL

## S1. Machine tables and open items

Tables 1–7 are generated from `OWN_EVIDENCE.csv`; every number in the main text carries a claim id that points at a snapshot table row (`SNAPSHOT_MANIFEST.json`). Outstanding items are listed in `V2_OPEN_ITEMS.md`, parsed from the [PENDING] markers of this document.

## S2. The three ICESat-2 samples

The slope, heterogeneity and exposed-bed analyses draw on different subsets of the same archive and must not be conflated: the slope sample admits an overpass only if its chainage span reaches 20 km, the heterogeneity sample takes every ATL13 date in the footprint, and the exposed-bed sample is a ground-return set with no water in it at all. Sample sizes are given per claim in Tables 1–4.

# REFERENCES

- [barzaghi2020] Barzaghi, Riccardo; De Gaetani, Carlo Iapige; Betti, Barbara (2020). The worldwide physical height datum project. *Rendiconti Lincei. Scienze Fisiche e Naturali*. https://doi.org/10.1007/s12210-020-00948-0
- [bauergottwein2023] Bauer-Gottwein, Peter; Zakharova, Elena; Coppo Frías, Monica; Ranndal, Heidi; Nielsen, Karina; Christoffersen, Linda; Liu, Jun; Jiang, Liguang (2023). A hydraulic model of the Amur River informed by ICESat-2 elevation. *Hydrological Sciences Journal*. https://doi.org/10.1080/02626667.2023.2245811
- [cantelli2004] Cantelli, Alessandro; Paola, Chris; Parker, Gary (2004). Experiments on upstream‐migrating erosional narrowing and widening of an incisional channel caused by dam removal. *Water Resources Research*. https://doi.org/10.1029/2003wr002940
- [cantelli2007] Cantelli, A.; Wong, M.; Parker, G.; Paola, C. (2007). Numerical model linking bed and bank evolution of incisional channel created by dam removal. *Water Resources Research*. https://doi.org/10.1029/2006wr005621
- [dhote2024] Dhote, Pankaj R.; Agarwal, Ankit; Singhal, Gaurish; Calmant, Stephane; Thakur, Praveen K.; Oubanas, Hind; Paris, Adrien; Singh, Raghavendra P. (2024). River Water Level and Water Surface Slope Measurement From Spaceborne Radar and LiDAR Altimetry: Evaluation and Implications for Hydrological Studies in the Ganga River. *IEEE Journal of Selected Topics in Applied Earth Observations and Remote Sensing*. https://doi.org/10.1109/jstars.2024.3379874
- [featherstone2011] Featherstone, W. E.; Kirby, J. F.; Hirt, C.; Filmer, M. S.; Claessens, S. J.; Brown, N. J.; Hu, G.; Johnston, G. M. (2011). The AUSGeoid09 model of the Australian Height Datum. *Journal of Geodesy*. https://doi.org/10.1007/s00190-010-0422-2
- [jiang2025] Jiang, Liguang; Nielsen, Karina; Andersen, Ole B.; Liu, Junguo (2025). SWOT Reveals Detailed Dynamics of Longitudinal River Slope in the Missouri River Basin. *Geophysical Research Letters*. https://doi.org/10.1029/2025gl115953
- [ledauphin2025] Ledauphin, T.; Garambois, P.‐A.; Larnier, K.; Azzoni, M.; Emery, C.; Picot, N.; Amzil, S.; Fjørtoft, R.; Maxant, J.; Yésou, H. (2025). Assessing SWOT's Hydraulic Visibility on the Rhine: Precision Flow Lines and Slope‐Based Flood Wave Propagation Signatures. *Earth and Space Science*. https://doi.org/10.1029/2025ea004309
- [lehnigk2026] Lehnigk, K. E.; Pavelsky, T. M.; Lang, K. A. (2026). SWOT Satellite Observations of the Kakhovka Dam Break Flood Highlight Limitations of Outburst Flood Models. *Geophysical Research Letters*. https://doi.org/10.1029/2025gl120832
- [maksymenko2026] Maksymenko, V. O.; Bezsonnyi, V. L. (2026). Remote sensing assessment of the spatio-temporal transformation of the Kakhovka reservoir after dam destruction using Sentinel-2 data. *Man and Environment Issues of Neoecology*. https://doi.org/10.26565/1992-4224-2026-45-07
- [musaeus2024] Musaeus, Aske Folkmann; Kittel, Cécile Marie Margaretha; Luchner, Jakob; Frias, Monica Coppo; Bauer‐Gottwein, Peter (2024). Hydraulic River Models From ICESat‐2 Elevation and Water Surface Slope. *Water Resources Research*. https://doi.org/10.1029/2023wr036428
- [nichols2017] Nichols, A. L.; Viers, J. H. (2017). Not all breaks are equal: Variable hydrologic and geomorphic responses to intentional levee breaches along the lower Cosumnes River, California. *River Research and Applications*. https://doi.org/10.1002/rra.3159
- [penna2013] Penna, N. T.; Featherstone, W. E.; Gazeaux, J.; Bingham, R. J. (2013). The apparent British sea slope is caused by systematic errors in the levelling-based vertical datum. *Geophysical Journal International*. https://doi.org/10.1093/gji/ggt161
- [scherer2022] Scherer, Daniel; Schwatke, Christian; Dettmering, Denise; Seitz, Florian (2022). ICESat‐2 Based River Surface Slope and Its Impact on Water Level Time Series From Satellite Altimetry. *Water Resources Research*. https://doi.org/10.1029/2022wr032842
- [scherer2023] Scherer, Daniel; Schwatke, Christian; Dettmering, Denise; Seitz, Florian (2023). ICESat-2 river surface slope (IRIS): A global reach-scale water surface slope dataset. *Scientific Data*. https://doi.org/10.1038/s41597-023-02215-x
- [schwabe2026] Schwabe, Joachim; Varbla, Sander; Ågren, Jonas; Teitsson, Hergeir; Ellmann, Artu; Liebsch, Gunter; Forsberg, René; Strykowski, Gabriel; Bilker-Koivula, Mirjam; Liepiņš, Ivars; Paršeliūnas, Eimuntas; Keller, Kristian; Omang, Ove Christian Dahl; Vestøl, Olav; Kaminskis, Jānis; Wilde-Piórko, Monika; Szelachowska, Małgorzata; Pyrchla, Krzysztof; Somla, Jarosław; Westfeld, Patrick; Hammarklint, Thomas; Olsson, Per-Anders; Förste, Christoph; Ince, E. Sinem (2026). The development of the unified Baltic Sea Chart Datum 2000 (BSCD2000) height transformation grid: an unprecedented example of height system unification based on state-of-the-art marine geoid modelling. *Journal of Geodesy*. https://doi.org/10.1007/s00190-026-02096-z
- [vyshnevskyi2023] Vyshnevskyi, Viktor; Shevchuk, Serhii; Komorin, Viktor; Oleynik, Yurii; Gleick, Peter (2023). The destruction of the Kakhovka dam and its consequences. *Water International*. https://doi.org/10.1080/02508060.2023.2247679
