# Multi-Sensor Validation of Water-Surface Elevation and the Hydraulic Transition of the Kakhovka Reservoir after the 2023 Dam Breach

**Scientific report / first article draft, v1**

Every numerical value in this draft is traceable to a machine-readable output
in `outputs/tables/` or `data/processed/`; the controlling register is
`outputs/tables/manuscript_evidence_matrix.csv`, whose `claim_id` values are
cited inline as `[V6.1]`, `[M1.1]` and so on. Values are quoted at the
precision of their source and no number appears here that is not in that
register.

---

## ABSTRACT

**Background.** Detecting a change of hydraulic regime in a large water body
from satellite elevation data requires that the elevations themselves be
commensurable. Gauge records, laser altimetry and radar interferometry are
natively expressed on different reference surfaces and in different
permanent-tide conventions, and a regime change of a few centimetres per
kilometre is smaller than several of the corrections involved. The Kakhovka
Reservoir on the lower Dnipro, whose dam was breached on 6 June 2023, is an
unusually demanding test case because a complete pre-breach hydraulic
description of the impounded system exists in the original design
documentation.

**Methods.** We harmonise seven hydrological gauges, ICESat-2 ATL13 laser
altimetry, SWOT PIXC/RiverSP radar interferometry, Sentinel-2 water masks and
a historical bathymetric survey into one vertical frame (EVRF2019 normal
heights via the EGG2015 quasigeoid and the official EPSG:9902 operation), and
we treat the permanent-tide convention of each product explicitly. Per-date
robust (Theil-Sen) longitudinal water-surface slopes are computed along a
SWORD-based chainage, with one satellite overpass as the independent unit.

**Validation.** Six independent checks were performed before any result was
interpreted. The SWOT PIXC correction chain reproduces RiverSP node heights to
−0.0010 m over 1 023 nodes, whereas the three alternative branches fail by
+0.072, +0.145 and −23.89 m [V2.1]. Under close temporal collocation
(Δt = 2.3 h) SWOT and ICESat-2 agree to +0.0036 m with no correction applied
[V3.1]. SWOT agrees with the Kherson gauge to +0.026 m over nine overpasses
[V4.1]. The historical sounding datum is shown to be the published navigation
drawdown level of 14.00 m rather than the assumed normal impoundment level of
16.00 m: on 14.00 m the 1960s survey and post-breach ICESat-2 exposed-bed
heights agree to +0.098 m with zero inside the confidence interval, while
16.00 m leaves a −1.90 m systematic misfit [V6.1]. A digitised field-measured
historical free-surface profile reproduces the independently tabulated profile
to 0.047 m over the pool [V7.1], and an independently reconstructed bed
surface reproduces the historical drawdown exposure fraction to 0.6 percentage
points, under all four cross-validated interpolators [V10.1].

**Main result.** Pre-breach per-date longitudinal water-surface slopes are
near zero and inconsistently signed (Theil-Sen median +0.090 cm/km, 10 of 14
dates positive, sign test p = 0.18); post-breach they are uniformly positive
(median +3.314 cm/km, 14 of 14 dates positive). The difference is
+3.223 cm/km, 95 % CI [+1.994, +5.071], permutation p = 1.0 × 10⁻⁴ [M1.1].
Water-surface heterogeneity within the footprint rose from 0.117 to 0.397 m
[M2.1], and Sentinel-2 shows a single continuous 2 033 km² surface replaced by
a fragmented, channel-dominated system [M3.1].

**Interpretation.** The impounded reservoir has been replaced by a
river-dominated system with a persistent directional longitudinal gradient and
disconnected residual water bodies. The historical documentation both
validates the modern framework and supplies the physical scales — documented
seiches and wind setup of 0.2–1.5 m — needed to interpret individual
instantaneous profiles.

**Limitations.** SWOT's cal/val orbit never covered the reservoir pool, so the
SWOT branch is anchored only downstream. One cross-sensor epoch (2023-04-05,
−0.181 m) is not explained by the gauge hydrograph and is reported as
unresolved. The reservoir-derived empirical alignment does not transfer to the
Kherson reach. Restricted to the main channel, the slope contrast has only six
qualifying post-breach dates and its confidence interval includes zero. The
bathymetric survey epoch is unrecorded, so no rate of bed change can be
formed.

**Keywords:** satellite altimetry; ICESat-2; SWOT; vertical datum; EVRF2019;
water-surface slope; dam breach; Kakhovka Reservoir; historical hydraulics;
reservoir bathymetry

---

# 1. INTRODUCTION

## 1.1 The Kakhovka Reservoir and the 2023 dam breach

The Kakhovka Reservoir was the lowest and largest impoundment of the Dnipro
cascade, extending some 238 km along the reservoir axis from the Kakhovka
hydroelectric dam upstream to the Dnipro HPP, with a surface area of
2 155 km² and a total volume of 18.2 km³ at the normal impoundment level
(НПГ) of 16.0 m. Its design documentation defines four operating levels that
recur throughout this study: НПГ 16.0 m (normal impoundment), НУФ 17.5 m
(highest forced level), УНС 14.0 m (the navigation drawdown level) and ГМО
12.7 m (the dead-volume level). All are expressed in a historical Baltic
system whose realisation the source does not name — a point that becomes
material in Section 5.5.

On 6 June 2023 the dam was breached. The impoundment drained over the
following weeks, exposing the former bed and leaving the Dnipro flowing
through it. This study treats that event as a natural experiment in hydraulic
regime change, observed by instruments that were never designed to be compared
with one another.

## 1.2 Why post-breach water-surface geometry matters

The distinction between an impounded reservoir and a river is, hydraulically,
a statement about the water surface. A reservoir at rest has a nearly
horizontal surface: the backwater curve flattens to the impounded level, and
the longitudinal gradient approaches zero except in the uppermost reach where
the river enters. A river has a surface that slopes continuously downstream at
a gradient set by discharge, roughness and channel geometry.

Quantifying that difference from space therefore requires resolving a
longitudinal gradient of a few centimetres per kilometre over tens of
kilometres — that is, height differences of a few centimetres to a few
decimetres. This is the same order as the vertical corrections that separate
the products being compared, which is why the validation problem must be
solved first.

## 1.3 The satellite-altimetry challenge

Three properties of the available observations shape the method.

First, **the products live on different reference surfaces.** ICESat-2 ATL13
reports ellipsoidal heights in a tide-free system; SWOT PIXC reports raw
ellipsoidal heights with geophysical corrections supplied but not applied;
gauges report stage above a local zero in a Baltic levelling network. The
target frame, EVRF2019 normal heights, is a fourth surface.

Second, **coverage is asymmetric.** SWOT's cal/val orbit never covered the
Kakhovka pool. Post-breach, SWOT's LakeSP product still assigns the drained
channel fragments to a single PLD lake polygon, producing "lake-average"
heights spanning a 200 km sloping channel — a number that is not a water
level. ICESat-2 is therefore the only altimeter that observes the reservoir
itself across the breach, and SWOT enters as a downstream validation partner.

Third, **the independent unit is the overpass, not the measurement.** A single
ICESat-2 overpass yields thousands of segments across six beams spanning
~3.3 km cross-track; treating those as independent samples would inflate
apparent precision by orders of magnitude. Every inferential statement in this
study uses one date, one overpass or one station as the unit.

## 1.4 The need for vertical-reference and historical validation

Two kinds of validation are usually unavailable for a reservoir that no longer
exists, and both are available here.

The first is **geodetic**: the official BS-77 → EVRF2019 transformation
(EPSG:9902) can be applied at every gauge, and its spatial variation measured,
so that the gauge network becomes an in-situ reference rather than an
assumption.

The second is **historical**. The reservoir's design documentation contains a
level–area–volume table, a set of calculated and field-measured longitudinal
free-surface profiles, morphometric statistics by reach including drawdown
exposure areas, and descriptions of seiches and wind setup with magnitudes. A
bathymetric survey of the impounded reservoir also exists as S-57 soundings.
Together these permit a modern satellite framework to be tested against
independent pre-breach hydraulic and morphological measurements — and, as
Section 5.5 shows, permit an error in the modern interpretation of the
historical data to be found and corrected.

## 1.5 Research questions

1. Can gauge, ICESat-2 and SWOT water-surface elevations be brought into one
   vertical frame with a demonstrable, quantified consistency?
2. Does the SWOT PIXC vertical processing chain behave as documented?
3. Do independent sensors agree when genuinely collocated in space and time?
4. Is an empirical alignment constant derived in the reservoir transferable
   downstream?
5. On what vertical reference are the historical soundings expressed, and does
   the resulting bed reconstruction reproduce independent historical
   morphological statistics?
6. Do pre-breach satellite water-surface profiles reproduce the documented
   historical free-surface geometry?
7. Did the longitudinal water-surface gradient change after the breach, by how
   much, and is the change persistent?

Questions 1–6 are validation. Question 7 is the scientific result, and it is
addressed only after 1–6 are answered.

---

# 2. STUDY AREA

## 2.1 The Kakhovka Reservoir

The reservoir occupied the lower Dnipro valley between roughly 32.3° and
35.4° E. Its design documentation divides it into five morphometric reaches,
whose published statistics are used directly in this study:

| reach | extent | area at НПГ (km²) | area at ГМО (km²) | drying area (km²) | mean depth (m) |
|---|---|---:|---:|---:|---:|
| 1 | Kakhovka HPP – Babyne | 495 | 469 | 26 | 13.4 |
| 2 | Babyne – Nikopol | 532 | 518 | 14 | 10.1 |
| 3 | Nikopol – Verkhnia Tarasivka | 363 | 329 | 34 | 7.2 |
| 4 | Verkhnia Tarasivka – Blahovishchenka | 693 | 513 | 180 | 4.6 |
| 5 | Blahovishchenka – Dnipro HPP | 72 | 47 | 25 | 4.9 |
| **total** | 238 km | **2 155** | **1 876** | **279** | |

Source: historical monograph Tables 19–21, transcribed to
`data/historical/historical_reservoir_reaches.csv` with arithmetic checksums.
The drying area is 279/2 155 = **12.95 %** of the area at НПГ, and it
increases strongly upstream — from 5.3 % in reach 1 to 26.0 % in reach 4.
This statistic becomes the validation target of Section 5.8.

One transcription note is recorded rather than silently corrected: reach 2's
area at НПГ is printed as 582 km² in the source, but the table's own
arithmetic (area at ГМО plus drying area, and the reach-total column) requires
532 km². The value 532 is used, and the discrepancy is flagged as a probable
source typographic error, not a misreading.

## 2.2 The lower Dnipro and Kherson

Below the dam the Dnipro flows some 60 km to Kherson and into the
Dnipro–Buh liman. Gauge 80805 (Kherson) is the only station in the study with
both a long continuous record spanning the breach and genuine SWOT
collocation: SWOT open water is available within 1 km of the gauge, and it is
this station that anchors the SWOT branch. Its graph zero is **−5.000 m**
BS-77, entirely unlike the reservoir stations' **+12.000 m**, and confusing
the two would introduce a 17 m blunder [V1.3].

## 2.3 Historical hydraulic structure

The design documentation describes the impounded reservoir as a
near-horizontal pool passing upstream into a backwater limb. Its calculated
free-surface profiles give a mean hydraulic rise from the dam to Verkhnia
Tarasivka (183 km) of **0 cm** at minimum discharge, **6 cm** at Q20 %
(7 300 m³/s) and **42 cm** at Q1 % (16 200 m³/s) [V8.1]. In other words, at
ordinary flows the impounded surface was flat to within the measurement
accuracy of the era, which the source itself states as 2 cm.

The same documentation describes dynamic disturbances that are an order of
magnitude larger than that mean gradient: seiches with periods of 13.4 h
(uninodal), 7.3 h (binodal, peak-to-peak 20–35 cm) and 3.5 h (trinodal), and
wind setup reaching 35 cm at the reservoir ends under a moderate gale (15 m/s)
and 148 cm under an extreme storm (25 m/s), against 23 cm in the centre. This
asymmetry between a near-zero mean gradient and decimetre-to-metre
instantaneous disturbances is central to interpreting individual satellite
overpasses (Section 7.4).

## 2.4 Pre-breach reservoir zones and the chainage convention

Two distance conventions coexist in the sources and must not be mixed. The
historical morphometric tables measure along the **reservoir axis** ("по его
оси"); the historical Fig. 13 axis and the modern SWORD network measure along
the **river channel** ("по руслу"). The ratio between them is
**1.358** at two independent settlements, agreeing to within 0.02 [X1.1].
This is a distance convention, not a zero-point error and not a source
inconsistency.

A residual of about 30 km nevertheless remains unexplained, and it is isolated
to the reach 1 / reach 2 boundary at Babyne — the only reach length not
printed in the source, which had to be inferred as a remainder. Consequently
**reaches 1 and 2 are merged** in every statistic in this study, and
historical reach boundaries are not otherwise used for final numbers.

---

# 3. DATA

## 3.1 ICESat-2 ATL13

ATL13 inland water-surface heights (`ht_water_surf`) provide the only
altimetric time series spanning the breach over the reservoir itself. Six
beams span ~3.3 km cross-track. ATL13 inherits the **tide-free** system of
ATL03, in which the solid-Earth, load and pole tides have already been applied
upstream. ATL08/ATL03 terrain products (via SlideRule `atl08p`, PhoREAL) are
used separately for exposed dry-bed elevations.

## 3.2 SWOT PIXC and RiverSP

SWOT L2_HR_PIXC pixel clouds supply high-resolution water-surface heights
downstream at Kherson. PIXC `height` is a **raw ellipsoidal** height: the
`geoid`, `solid_earth_tide`, `load_tide_fes` and `pole_tide` fields are
reported for reference but *not applied* (PIXC PDD D-56411 Rev C, p. 53).
RiverSP node products of the same cycle and pass are used to validate the
correction chain. Because `solid_earth_tide` excludes the zero-frequency
permanent tide, the corrected height retains the permanent crustal
deformation — a **mean-tide** crust, which is the convention a zero-tide
quasigeoid expects.

**Coverage limitation, stated once and carried throughout:** SWOT's cal/val
orbit never covered the reservoir pool. No amount of additional download can
change this. SWOT is a downstream validation partner, not a reservoir sensor.

## 3.3 Gauge records

Seven stations are transformed and used: six reservoir stations (80977 Nova
Kakhovka, 80971 Velyka Lepetykha, 80964 Nikopol, 80963 Blahovishchenka, 80961
Plavni, 80959 Rozumivka) at a zero of +12.000 m BS-77, and 80805 Kherson at
−5.000 m BS-77. Together they supply **10 194 daily values** over 2019–2025
[V1.1]. Two stations spanning the breach (Rozumivka and Kherson) carry records
to 2025; the other four reservoir stations end in 2021.

Not covered, and stated rather than hidden: Mykolaiv 98027 has coordinates but
no level series in the donor layout, and eight further reservoir stations exist
in the donor CSVs with daily data for 2019–2021 but carry no coordinates, so
they cannot be transformed at all.

## 3.4 EPSG:9902 and vertical-reference data

The EPSG:9902 operation (grid `ua_2019z.asc`) maps BS-77 normal heights to
EVRF2019 (EPSG:9389) normal heights. Over this reach it takes values of
**+0.1715 to +0.2157 m** at the gauge sites — a 4.4 cm spread — and
+0.1617 to +0.2160 m over the sounding domain [V1.2, V6.2]. Its stated
accuracy is 0.068 m. It is a normal-height-to-normal-height operation and is
never applied to an ellipsoidal or geoid-referenced height.

## 3.5 EGG2015

The EGG2015 gravimetric quasigeoid converts ellipsoidal heights to normal
heights. It is a **zero-tide** surface; for crustal ellipsoidal heights,
zero-tide and mean-tide coincide, so a mean-tide crust is the correct input.

## 3.6 Historical bathymetric and hydraulic source

Two historical bodies of evidence are used.

**S-57 soundings**: 7 514 records over the reservoir. Five exact duplicate
coordinates are averaged, leaving 7 509 for interpolation. Median sounding
spacing is ~357 m. All 19 SOUNDG attributes — including SORDAT and RECDAT — are
empty in every record, so **the survey epoch is unrecoverable** [X2.1]. A total
of 161 soundings are negative, which in S-57 denotes drying heights; these sit
a median 302 m *inside* the full-pool waterline, which is incompatible with a
full-pool reduction datum and was the first clue to the datum problem.

**Design documentation**, photographed and hand-transcribed with arithmetic
checksums into `data/historical/`: Table 19 (level–area–volume, 17 levels over
10.0–18.0 m), Table 20 (calculated longitudinal free-surface profiles at four
discharges), Table 21 (morphometry and drawdown exposure by reach), Fig. 13
(chainage axis), Fig. 16 (four longitudinal free-surface curves, one of which
is field-measured), Fig. 46 (seiches), Fig. 57 (wind setup), Fig. 128
(terraces) and Table 200 (level-measurement accuracy).

## 3.7 Sentinel-2

Sentinel-2 L2A scenes provide water masks from a dual-index test conditioned
on the scene classification layer: water requires NDWI > 0 **and** MNDWI > 0
**and** an SCL class compatible with water, with cloud, cirrus, shadow, snow
and saturated classes vetoing the classification outright. Because tiles cover
the footprint only partially on most dates, every metric carries
`footprint_observed_fraction`, and coverage is treated as a first-class
variable rather than an afterthought (Section 6.5).

## 3.8 SWORD

The SWORD v16 river network supplies the longitudinal chainage against which
all profiles are referenced. SWORD is used strictly as a **modern-channel
proxy**; it is not a reconstruction of the pre-1956 channel, and no claim in
this study depends on it being one.

---

# 4. METHODS

## 4.1 Vertical harmonisation: the four surfaces

The observations are natively expressed on four reference surfaces in two
permanent-tide conventions:

| system | native quantity | reference surface | tide system |
|---|---|---|---|
| Gauges | stage above a local zero | Baltic 1977 normal heights | levelling network |
| ICESat-2 ATL13 | `ht_water_surf` | WGS84/ITRF ellipsoid | **tide-free** |
| SWOT PIXC | `height` | WGS84 ellipsoid | **mean-tide** (uncorrected) |
| target | normal height | EGG2015 / EVRF2019 | **zero-tide** |

## 4.2 Gauge branch

    H_BS77      = gauge_zero_BS77 + stage
    H_EVRF2019  = H_BS77 + Δ_EPSG9902(x)

with Δ sampled at each station's own coordinates. The bilinear grid sampler
was cross-checked against an independent implementation in a separate project
and agrees to 0.000 mm; the script aborts if it does not [V1.1].

## 4.3 ICESat-2 branch

    h_mean_tide = ht_water_surf + tide_earth_free2mean(φ)
    H_EGG2015   = h_mean_tide − ζ_EGG2015(φ, λ)

where `tide_earth_free2mean = 0.06029 − 0.180873 sin²φ` (ATL03 ATBD v007,
p. 125), which over this latitude range is −0.035 to −0.039 m.

## 4.4 SWOT branch

    h_SWOT    = height − (solid_earth_tide + load_tide_fes + pole_tide)
    H_EGG2015 = h_SWOT − ζ_EGG2015(φ, λ)

The PIXC `geoid` field is **never** subtracted from PIXC `height`; doing so is
a 23.9 m error, quantified in Section 5.2.

## 4.5 Permanent-tide treatment

Because EGG2015 and EVRF2019 are both zero-tide, and because zero-tide and
mean-tide coincide for crustal ellipsoidal heights, no tide-system mixing
occurs once ATL13 is moved out of the tide-free system. Neglecting this step
would leave a latitude-dependent 3.5–3.9 cm error between the ICESat-2 and
SWOT branches — comparable to the sensor agreement being tested.

## 4.6 Spatial and temporal matchup

Sensors are matched **to each other**, not through the gauge. ICESat-2 never
passes closer than 13 km to the Kherson gauge, so any gauge-mediated
sensor difference would be spatially confounded with the along-channel
gradient. Each ATL13 segment is compared with the median of SWOT open-water
pixels within 500 m on the same day, and the independent unit is one
co-located overpass.

## 4.7 Historical bathymetry reconstruction

Bed elevations follow

    H_bed_EVRF2019(x) = (H_ref + Δ_EPSG9902(x)) − DEPTH

with `H_ref` the reduction level of the soundings. Establishing `H_ref` is
itself a result (Section 5.5); it is **not** assumed. Δ is applied per
sounding, exactly as for the gauge zeros, because it varies across the
reservoir.

## 4.8 Shoreline boundary condition

The soundings stop short of the shore: only 6.3 % lie within 250 m of the
footprint boundary, the closest is ~167 m out, and the 250 nearest have a
median bed elevation of 12.15 m against a waterline near 17.1 m. An
unconstrained interpolator therefore carries a 12 m bed outward and models the
margins ~5 m too deep — biasing the drying fraction *low* precisely where
drying occurs. Unconstrained RBF extrapolated to +67.9 m.

The surface is therefore pinned with boundary pseudo-points at the **observed
2023-06-05 waterline**, whose elevation is independently constrained by two
unrelated routes: the Rozumivka gauge on that date (16.88 m BS-77 =
**17.08 m EVRF2019**) and the historical level–area curve evaluated at the
mapped area of 2 192 km² (≈ 17.1 m BS-77). The 2023 shoreline thus sat about
1 m *above* project НПГ — the pool had been raised that spring — so a
constraint at НПГ would itself have been wrong [V10.2].

**Two quantities must not be conflated:**

| quantity | value | role |
|---|---|---|
| interpolation boundary elevation | 17.08 m EVRF2019 | pins the surface at the observed 2023 shoreline |
| exposure reference / denominator | 16.18 m EVRF2019 (НПГ) | restricts the domain to the reservoir at НПГ, as Table 21 does |

## 4.9 Interpolation and cross-validation

Four interpolators are fitted independently: inverse-distance weighting,
local ordinary kriging (SciKit-GStat variogram with a batched local solver),
thin-plate-spline RBF, and Delaunay-linear. Three cross-validation schemes are
reported:

- **spatially blocked at 1 km — PRIMARY**, matched to the ~357 m data spacing;
- **spatially blocked at 5 km — STRESS TEST only.** The fitted variogram range
  is ~2 km, so 5 km blocks hold out cells beyond the correlation range of any
  training point; this scores extrapolation, which no method can pass, and is
  not how the surface is used;
- **random holdout — an OPTIMISTIC reference**, because survey lines put a
  near-twin of each held-out point in the training set.

Method selection is made on the primary blocked score alone, never on
proximity to any historical statistic.

## 4.10 Longitudinal chainage

Positions are projected onto SWORD chainage. A known limitation is quantified
rather than assumed away: because the six ICESat-2 beams span ~3.3 km
cross-track and the pool is wide, the *chainage* span of a single overpass can
exceed its true along-flow ground span. Measured inflation is a median of
**1.34×** (p90 1.94, max 2.65) [V7.3]. This is a property of the geometry, not
of the water, and Section 5.6 shows where it matters.

## 4.11 Slope estimation

For each date, a longitudinal water-surface profile is fitted by **Theil-Sen**
regression (primary, robust to outlying segments) and by **OLS** (secondary,
sensitivity). One date yields one slope. Per-date fits use a median of 6 points
over spans of ~24–27 km.

## 4.12 Sentinel planform metrics

Per date: number of connected water bodies, total water area, largest
connected component and its fraction of total water area, a fragmentation
index, and `footprint_observed_fraction`. Dates are classified by coverage, and
only near-complete coverage (> 0.8) is admitted to any pre/post comparison.

## 4.13 Statistical testing

Inference is on independent units only — one date, one overpass, one station.
Statistics are robust throughout: medians, NMAD, and percentile bootstrap
confidence intervals; where observations cluster by track, the bootstrap is
track-clustered. Group differences use a permutation test and Mann-Whitney;
sign consistency uses an exact sign test; effect size uses Cliff's delta. The
random seed is fixed at 42. Emphasis is placed on effect size and sign
persistence rather than on p-values, which with n ≈ 14 are dominated by a few
observations.

## 4.14 Uncertainty framework

Four uncertainty components are tracked separately and never summed silently:

1. **geodetic** — EPSG:9902 stated accuracy 0.068 m; EGG2015 quasigeoid error;
2. **sensor** — within-overpass NMAD (0.007–0.119 m across the three
   cross-sensor epochs) and post-tie residual NMAD 0.044 m against gauges;
3. **historical reference level** — the 0.40 m spread among three independent
   estimates (13.71 / 14.00 / 14.11 m), deliberately **not** folded into any
   single confidence interval;
4. **interpolation** — blocked cross-validated RMSE 2.76 m with NMAD 1.32 m,
   bias −0.03 m and median absolute error 0.89 m; the error is heavy-tailed,
   with the worst 5 % of observations carrying 51 % of the squared error
   (Section 5.9.1). The integrated area statistics are nonetheless stable,
   because they are a different quantity at a different spatial scale.

---

# 5. VALIDATION RESULTS

## 5.1 The gauge network in one geodetic frame

All seven stations with both coordinates and data are transformed to EVRF2019,
yielding 10 194 daily values in one frame [V1.1]. The transformation is an
official operation, not a fit.

The offset is **spatially varying**: +0.1715 m at Nikopol to +0.2157 m at Nova
Kakhovka, +0.2076 m at Kherson — a 4.4 cm spread across the reach [V1.2]. A
single national constant would therefore be wrong by up to ~2 cm at the
extremes, which is the same order as the sensor agreement being tested. The
spread is smaller than the grid's own stated accuracy (0.068 m), so the
*spatial variation* is used while the *absolute* level carries that accuracy —
a distinction that matters because the variation is what makes the stations
mutually comparable.

After tying, the residual of satellite against gauge over 55 matched
observations has a median of +0.000 m by construction, **NMAD 0.044 m and RMSE
0.052 m** — the real quality of the tie. The master table holds 10 350 rows in
one frame (ICESat-2 120, SWOT 36, gauge 10 194), with the official geodetic
transform, any empirical correction, and the corrected level kept in three
separate columns so they are never silently summed.

## 5.2 The SWOT PIXC vertical chain

Four candidate chains were compared against RiverSP node heights of the same
cycle and pass over 1 023 nodes [V2.1]:

| chain | median difference from RiverSP |
|---|---:|
| `height − (solid_earth_tide + load_tide_fes + pole_tide)` — documented | **−0.0010 m** |
| `height` uncorrected | +0.0720 m |
| correction applied with reversed sign | +0.1451 m |
| `height − geoid` | −23.89 m |

Only the documented chain returns zero. The sign and content of the correction
are therefore established **empirically**, not assumed from documentation.

**This is an internal product-chain validation, not an independent accuracy
assessment.** RiverSP is derived from the same SWOT observation, so the test
establishes that our processing reproduces the mission's own, and nothing more.
It is presented as such.

## 5.3 SWOT against ICESat-2

Three genuinely co-located overpasses exist [V3.1]:

| date | Δt (h) | segments | beams | distance from gauge | H_SWOT − H_ICESat-2 |
|---|---:|---:|---:|---:|---:|
| 2023-04-05 | +14.22 | 718 | 6 | ~40 km | **−0.1810 m** |
| 2023-05-04 | +11.10 | 422 | 4 | ~57 km | **+0.0597 m** |
| 2023-05-14 | −2.28 | 3 417 | 6 | ~19 km | **+0.0036 m** |

The best temporally matched overpass gives **+0.0036 m** — the two satellite
surfaces agree to a few millimetres in the common frame **with no correction
applied**. Within-overpass NMAD on that date is 0.034 m and per-beam NMAD
0.007 m.

The residual orders monotonically with |Δt|, which is consistent with
temporal and hydrodynamic mismatch rather than a vertical-datum offset. With
n = 3, this is **suggestive, not established**: no regression is fitted, and no
universal centimetric agreement between the two missions is claimed. The
2023-05-04 epoch additionally carries a per-beam spread above 1 m and is
flagged as suspect on ICESat-2 quality grounds.

## 5.4 Kherson as a local anchor

Across nine PIXC overpasses, SWOT open water within 1 km of gauge 80805
differs from the gauge in EVRF2019 by **+0.0264 m**, with NMAD 0.041 m
[V4.1]. The sign convention is *satellite minus gauge* throughout.

The median is **radius-dependent**, moving 8.4 cm across aggregation radii of
0.5–5 km (+0.055 m at 0.5 km, +0.026 m at 1 km, −0.000 m at 2 km, −0.029 m at
5 km). This is the along-channel gradient entering the aggregation window, so
the radius is a genuine methodological choice rather than noise; at 1–2 km the
value stays within 3 cm of zero.

The ICESat-2 tie at the same station is weaker by construction: ICESat-2 never
passes closer than 13 km, and the station's matchup NMAD (0.084 m) is the
largest of any station, consistent with a wind-setup-dominated site. **Kherson
anchors the SWOT branch well and the ICESat-2 branch only loosely.**

## 5.5 A validated negative result: the 2023-04-05 anomaly

On 2023-04-05 the two sensors differ by −0.181 m, and the Kherson hydrograph
does **not** explain it [V4.2]. The gauge was *rising* at +10.0 cm/day
(central difference), and SWOT was acquired +14.2 h later, so the hydrograph
predicts SWOT **higher** by +5.9 cm. SWOT reads 18.1 cm **lower**. Applying
the hydrological term therefore *worsens* the residual, to −24.0 cm.

Context makes the excursion itself unusual: at this site the day-to-day |ΔH|
has median 3.0 cm, p90 9.0 cm and maximum 20.0 cm over 96 days, so an 18 cm
discrepancy is a ~99th-percentile event. No local wind or pressure record
exists, so wind setup can be neither confirmed nor excluded.

**The anomaly is reported as unresolved.** It is retained in the manuscript
because a validation framework that reports only its successes is not a
validation framework.

## 5.6 The historical sounding datum

This is the result that changed the study's conclusions, and it began as a
failure.

Carried through the pipeline on the assumption that the S-57 depths were
reduced to the normal impoundment level of 16.00 m, the reconstructed bed sat
**−1.90 m** below post-breach ICESat-2 exposed-bed heights, with a 95 % CI of
[−2.08, −1.68] — 3.8 NMAD from zero. The question was then reframed: rather
than asking why ICESat-2 was low, we asked what reduction level the depths
themselves require. Four candidates were tested against 94 confirmed
exposed-bed points on 28 independent tracks, with track-clustered inference
[V6.1]:

| reference level | source | median difference | 95 % CI | zero inside? | independent of ICESat-2? |
|---|---|---:|---|---|---|
| 16.00 m (НПГ) | assumption | **−1.9021 m** | [−2.079, −1.685] | no | yes |
| **14.00 m (УНС)** | **published, Table 19** | **+0.0979 m** | **[−0.076, +0.303]** | **yes** | **yes** |
| 13.71 m | capacity-curve fit | +0.3879 m | [+0.214, +0.605] | no | yes |
| 14.11 m | ICESat-2 fit | −0.0121 m | [−0.181, +0.192] | yes | **no — circular** |

Three independent routes converge on ~14 m: the **published** navigation
drawdown level УНС of 14.00 m; a fit of the sounding-derived hypsometry to the
published capacity curve, giving 13.705 m [13.570, 13.840] and using **no
ICESat-2 data at all**, reproducing Table 19 to an RMS of 0.28 km³ across
10.0–18.0 m; and an ICESat-2 fit of 14.11 m [13.91, 14.32], which is circular
against ICESat-2 and is therefore never used as the working reference.

**14.00 m is adopted** — not because it fits best (14.11 m fits marginally
better), but because it is the only *published* level of the four. On it, the
1960s survey and post-breach ICESat-2 agree to **+0.098 m with zero inside the
interval**, against a point scatter of 0.50 m.

Carried into EVRF2019 the reference becomes **14.162 to 14.216 m**, median
**14.185 m**, again spatially varying [V6.2]. For quoting, 14.19 m EVRF2019;
for computing, the per-sounding value.

Three caveats are attached and must survive into any published version:

1. The honest uncertainty is **not** that confidence interval. It is the
   **0.40 m spread** among the three independent estimates, and it is
   deliberately not folded into any single interval.
2. The source **does not name its Baltic realisation**. EPSG:9902 is defined
   for BS-77; if these design tables are on BS-42, a further step of a few
   centimetres is unaccounted for. That is small against the 2.00 m being
   corrected, but it is not zero.
3. The wording "the depths were reduced to 14.00 m" is an **inference from the
   data**, not a statement found in the source. The source publishes 14.00 m
   as the navigation drawdown level; that it is also the sounding reduction
   datum is this study's conclusion.

Independent corroboration comes from the full bed rebuild: median ATL08 minus
survey is **−0.00 m** over 2 650 matches, **+0.03 m** on the 1 037 tightest
matches (≤ 50 m) with NMAD 0.70 m, and **+0.06 m** over genuinely dry exposed
bed, with r = 0.777.

Residual structure after correction is small but not zero: Theil-Sen of the
residual against depth is +0.0148 m/m with CI [+0.0008, +0.0295] — just
excluding zero, about 0.3 m across the 0–20 m depth range — and there is a
chainage slope of +0.0038 m/km whose CI excludes zero. **None of this is
claimed as morphology.** The survey epoch is unknown, so no rate can be
formed, and these magnitudes sit inside the reference-level uncertainty.

## 5.7 The historical free-surface curve

The historical Fig. 16 carries four longitudinal free-surface curves, of which
**curve 2 is field-measured** (22–25 April 1970, Q = 8 400 m³/s, dam level
16.0 m) — the only in-situ longitudinal profile of the impounded reservoir
available to us.

Digitising it required solving two problems that first produced wrong answers.
The figure's **vertical axis is non-linear**; calibrating on a linear
assumption made every elevation read 0.2–0.4 m too high, and an early version
returned exactly 22.00 m for every point because `np.interp` silently clamps
when its reference array is decreasing. The axis is now calibrated on detected
gridlines, and the horizontal calibration on seven gridlines gives 10.29 px/km
with residuals of ≤ 0.6 km. Curve 2's symbols are then extracted as enclosed
light regions, which works even where a symbol touches another curve.

Validated against Table 20 interpolated to the same discharge — an
independent numerical source — the digitisation gives, over 12 points
[V7.1]:

| statistic | value |
|---|---|
| median deviation | **+0.022 m** |
| NMAD | **0.045 m** |
| flat pool < 180 km (n = 5), median | **+0.047 m** |
| flat pool < 180 km, max abs. deviation | **0.057 m** |

The three large deviations on the steep upper backwater limb (≥ 210 km:
−0.77, −0.66, −0.10 m) are **not** counted as digitisation error: Table 20 has
only two points across the limb and the true curve is convex, so the linear
reference is itself wrong there by decimetres. The agreement over the pool also
independently confirms the non-linear axis calibration.

**Curve 4 remains not digitised.** It is dash-dot and cannot be separated from
dashed curve 3 by the enclosed-region method; it would require curve tracing.
This is recorded as outstanding, not as complete.

Pre-breach ICESat-2 slopes by reach then reproduce the documented geometry
[V7.2]:

| reach (km) | n dates | ICESat-2 slope (cm/km) | historical envelope, Qmin → Q0.1 % |
|---|---:|---:|---|
| 0–60 | 12 | +0.004 | 0.00 → 0.22 |
| 60–120 | 15 | −0.064 | 0.00 → 0.42 |
| 120–180 | 15 | +0.065 | 0.00 → 0.80 |
| 180–210 | 6 | −0.324 | 0.04 → 4.00 |
| 210–240 | 7 | +0.127 | 0.10 → 17.75 |

Every reach lies inside the historical discharge envelope. The flat pool is
reproduced (0–180 km: −0.06 to +0.06 cm/km against a low-flow expectation of
0.00–0.04) and the uppermost reach steepens, as a backwater limb must.

## 5.8 The 180–210 km anomaly, diagnosed

The negative 180–210 km value is a **geometric artefact, not a hydraulic
signal** [V7.3]. Every fit in that band is a single overpass, and its chainage
span is inflated over the true ground span by a median factor of 1.34 (p90
1.94, max 2.65): six beams spread 3.3 km across a wide pool, and the sign of
the fit depends on which beam happens to snap furthest upstream.

The first hypothesis — that chainage assignment fails where the pool is
widest — was tested and **rejected**: that band has the *smallest* median
offset from the channel line of any reach (2.5 km, against 15.1 km at
210–240 km). It is recorded here because it was tested and failed, not quietly
dropped.

Critically, this artefact **cannot manufacture the main result**. The geometry
defect is nearly identical in both periods (55 % vs 47 % single-pass; median
inflation 1.35 vs 1.39). What differs is the **numerator**: the median
within-fit water-surface span rises from **0.072 m** pre-breach to
**0.762 m** post-breach — a tenfold change in the signal while the geometric
distortion stays put.

One further date is flagged: **2023-06-04** sits in the pre-breach sample at a
water level of 17.16 m, some 1.2 m above every other date, two days before the
breach while the pool was being raised. It does not drive the median
(leave-one-out spans −0.38 to −0.27) but it does not belong in a "normal
pre-breach operation" sample.

## 5.9 Bed-surface reconstruction and cross-validation

The four interpolators score as follows on the primary 1 km blocked scheme
over 7 509 soundings [V10.3]:

| method | RMSE (m) | MAE (m) | bias (m) | NMAD (m) |
|---|---:|---:|---:|---:|
| **OK (ordinary kriging)** | **2.761** | 1.733 | **−0.032** | 1.322 |
| IDW | 2.781 | 1.721 | −0.088 | 1.265 |
| LINEAR | 2.822 | 1.721 | +0.177 | 1.241 |
| RBF | 3.228 | 1.972 | +0.040 | 1.511 |

Ordinary kriging is preferred on this score. Under the 5 km stress test the
ranking compresses and RBF degrades badly (5.087 m), as extrapolation should;
under random holdout every method improves (OK 2.648 m), as the optimistic
reference should. Residuals of the preferred surface are largest within 1–3 km
of the corridor, where the bed is steepest — a resolution limit, not a bias.

**The RMSE alone is a misleading summary and is never quoted alone.** The
package for the preferred surface is:

| statistic | value |
|---|---|
| RMSE | **2.76 m** |
| NMAD | **1.32 m** |
| bias | **−0.03 m** |
| median absolute error | **0.89 m** |
| share of squared error from the worst 5 % of observations | **51 %** |

The defensible statement is therefore: *the median absolute prediction error
was approximately 0.9 m, whereas RMSE rose to 2.76 m because of a small number
of large errors concentrated in morphologically complex zones.* It would **not**
be defensible to say that most of the DEM is accurate to ~1 m: 0.89 m is the
median of the residual distribution **at sounding locations**, not an
area-weighted accuracy of the interpolated surface.

### 5.9.1 What the 2.76 m actually measures

Because a single number invites the wrong reading, the error was decomposed
under five hold-out geometries and six covariates, with the kriging parameters
held fixed [V10.4].

**Blocked cross-validation is not the cause.** Random hold-out already gives
2.648 m; 1 km blocking adds only 1.04× (2.761 m) and even 5 km blocking 1.20×
(3.189 m). The reason is visible in the sampling geometry: the median distance
to the nearest *training* point moves only 366 → 468 m, because the survey is
line-based at 357 m spacing, so removing a 1 km block still leaves the adjacent
survey lines in the training set. Decisively, the worst 20 residuals are as
large under random hold-out (median |e| 14.54 m) as under blocked hold-out
(15.26 m) — the hold-out does not create them.

**Sub-spacing morphological relief is the cause.** Ranked by how much RMSE
varies across their bins, the covariates are:

| covariate | RMSE range | ratio |
|---|---|---:|
| bed elevation class | 1.66 → 11.59 m (worst bin only 18 points) | 7.0× |
| **local roughness** (bins of 751–1878 points) | **0.88 → 5.46 m** | **6.2×** |
| distance to shoreline | 1.18 → 4.24 m | 3.6× |
| distance to channel corridor | 2.11 → 4.05 m | 1.9× |
| reach | 1.78 → 3.31 m | 1.9× |
| distance to nearest training point | 2.17 → 4.02 m, non-monotonic | 1.9× |

The bed has 34.7 m of amplitude and is sampled every 357 m, so relief shorter
than that spacing **cannot be reliably recovered from the sounding points alone
by an ordinary unconstrained interpolator.** That is a statement about the
present data being under-determined, not about mathematical impossibility:
adding the historical channel axis, breaklines, hydrographic cross-sections or
archival charts as constraints could recover a narrow trough considerably
better, and is listed as future work.

The signature confirms the mechanism: the smoothing bias **reverses sign with
elevation** — the deepest bin reads +11.03 m (predicted too shallow) and the
highest −3.12 m (predicted too deep). That is regression toward the local mean,
which is what any smooth interpolator does to a surface it cannot resolve.

The second mechanism is the shoreline margin: within 500 m of the shoreline
RMSE is 4.24 m, and those 23 % of soundings carry 54 % of the squared error,
against 1.18 m beyond 4 km. This is the same margin problem the shoreline
constraint of Section 4.8 was introduced to address.

**The vertical reference is excluded**, which matters because a datum error
would invalidate Section 5.6. Overall bias is −0.032 m, per-reach bias spans
only −0.105 to +0.102 m, and the elevation-dependent bias changes sign. A datum
error is a constant offset; nothing here behaves like one.

**The error is strongly non-uniform.** The worst 1 % of soundings carry 20 % of
the squared error and the worst 5 % carry 51 %; excluding that 5 % the RMSE
falls to 1.98 m. Reach 1+2 — the deep lower reservoir containing the former
channel — carries 79 % of the squared error on 56 % of the points.

The consequence for Section 5.10 is that two apparently discordant statements
are both true and are not in conflict, because they concern different spatial
scales and different quantities: the local depth of a narrow channel trough
carries uncertainty of several metres, while the large-scale morphology and
integrated area statistics are stable to a fraction of a percentage point.

### 5.9.2 Reproducibility of the cross-validated score

The fold partition was originally drawn from a seeded generator inside each
script, which made the score depend on how many other random draws a script
happened to make first: the same seed produced 2.761 m in one script and
2.784 m in another. The partition is now stored as data
(`hist14_cv_fold_assignments.parquet`, five hold-out schemes, seed 42, with
row alignment asserted against the soundings), and the generating script
refuses to write the file unless it reproduces the published 2.7613 m to
within 10⁻⁶ m. Both scripts now return 2.761 m exactly. The 0.023 m gap that
prompted this is a useful figure in its own right: it is the fold-assignment
noise on a cross-validated score of this kind.

Reaching this required fixing a failure worth recording: local kriging
initially returned an RMSE of 5.7 × 10¹³ because the fitted nugget was 0.00
and five coincident coordinates made local systems singular — and
`np.linalg.solve` returns garbage rather than raising. The fix was a nugget
floor, diagonal regularisation, a weights-sum sanity check with IDW fallback,
and averaging of duplicate coordinates.

## 5.10 Area-weighted exposure: an independent morphological validation

The historical Table 21 reports the **area** that dries between НПГ and ГМО,
by reach. Reconstructing that from the bed surface is a genuinely independent
test, because the historical fraction never enters the surface's construction.

An earlier version of this test was **invalid by construction** and is
retained as a methodological caveat [V9.6]: it counted sounding *points*, but
Table 21 reports an area fraction, and the survey follows the navigable channel
and avoids exactly the shallow margins where drying occurs. The replacement is
an area-weighted count over grid cells, with the denominator restricted to
cells whose modelled bed lies below НПГ — matching Table 21's own denominator.

On the canonical 250 m grid [V10.1]:

| reach | km | area (km²) | historical | reconstructed | across 4 methods | diff (pp) |
|---|---|---:|---:|---:|---|---:|
| 1+2 merged | 0–133 | 1 026 | 3.9 % | 6.9 % | 6.6–8.2 % | +3.0 |
| 3 | 133–183 | 474 | 9.4 % | 9.0 % | 8.5–10.4 % | −0.3 |
| 4 | 183–248 | 686 | 26.0 % | 22.1 % | 21.4–24.1 % | −3.9 |
| **whole mapped reservoir** | | 2 185 | **12.95 %** | **12.38 %** | 11.8–12.8 % | **−0.57** |
| 5 | — | 0 | 34.7 % | **NA** | — | — |

The whole-reservoir fraction agrees to **0.57 percentage points**, and the
historical upstream increase is reproduced by **all four** interpolators. Reach
5 is recorded as **NA, never as zero**: it lies outside the mapped 2023
shoreline domain, with 0 km² of grid cells.

Two statements about this result are deliberately withheld. First, **Table 21
was validation, not calibration**: the historical 12.95 % never entered the
surface, and method selection came from independent cross-validation, not from
proximity to the historical value. Second, with only **three** resolved reach
units, no inferential statistic is claimed — the reach-level correlation
(r = 0.993) is descriptive only. The stronger evidence is that all four
methods reproduce the ordering, that the whole-reservoir fraction agrees within
0.6 pp, and that reach deviations stay within a few percentage points despite
fully independent reconstruction.

The shoreline boundary condition dominates this answer: unconstrained, the
whole-reservoir fraction is **6.7 %**, roughly half the constrained
**12.4 %** [V10.2]. The unconstrained surface is retained in the saved stack
as an audit trail and is used for no result.

## 5.11 Grid-resolution sensitivity

Because the drying zone is a thin marginal band — exactly the feature a coarse
cell averages away — the exposure fraction was recomputed on three grids from
the same surfaces [V11.1]:

| cell | cells inside | reconstructed | interpolator spread |
|---|---:|---:|---|
| 250 m | 34 959 | 12.38 % | 11.84–12.75 % |
| 50 m | 874 491 | 12.32 % | 11.84–12.73 % |
| 30 m | 2 428 899 | 12.32 % | 11.83–12.72 % |

A 70-fold increase in cell count moves the answer by **−0.06 pp**, and the
final refinement step by **+0.00 pp**. Per reach the largest drift is 0.23 pp
and the last step never exceeds 0.02 pp. The reach areas agree across
resolutions to within 1.53 km², and the whole-reservoir area to 1.29 km² on
2 186 km² (0.06 %), converging on the fine-grid value — an independent check
that the three grids discretise the same domain.

**Grid resolution is therefore not a limiting uncertainty.** The interpolator
spread (0.89 pp) is 15× the resolution effect, and the difference from the
historical value (0.57 pp) is 10×. The 250 m grid is adopted as the canonical
statistical grid; the 30–50 m grids are for visualisation. Refining does not
add information — it interpolates the same 7 509 soundings more finely, and the
cross-validated RMSE is unchanged by construction.

## 5.12 Validation synthesis

| question | test | result | status |
|---|---|---|---|
| One geodetic frame? | BS-77 + EPSG:9902 → EVRF2019, 7 stations | 10 194 values; sampler agrees to 0.000 mm; post-tie NMAD 0.044 m | **VALIDATED** |
| SWOT PIXC chain correct? | vs RiverSP, n = 1 023 nodes | −0.0010 m; alternatives +0.072/+0.145/−23.89 m | **VALIDATED** (internal) |
| Can SWOT and ICESat-2 agree? | 3 co-located overpasses | +0.0036 m at Δt = 2.3 h | **VALIDATED** (n = 3) |
| SWOT vs gauge? | 9 overpasses at Kherson | +0.026 m, NMAD 0.041 m | **VALIDATED** |
| Is 2023-04-05 explained? | gauge hydrograph over Δt | wrong sign; residual worsens to −24.0 cm | **UNRESOLVED** |
| Is the correction transferable? | reservoir (n = 5) vs Kherson | −0.130 m vs +0.005 m, disjoint CIs | **NOT TRANSFERABLE** |
| Historical datum? | 4 candidate levels vs S7 bed | 14.00 m → +0.098 m, zero inside CI | **VALIDATED** |
| Historical free surface? | Fig. 16 curve 2 vs Table 20 | +0.022 m median, 0.057 m max in pool | **VALIDATED** |
| Bed morphology? | 4 geomorphological tests | depressions 1.8× enriched near corridor; S7 −0.19 m | **SUPPORTED** |
| Exposure area? | vs Table 21, 4 interpolators | 12.38 % vs 12.95 %, −0.57 pp | **VALIDATED** |
| Grid resolution? | 250 / 50 / 30 m | drift −0.06 pp | **VALIDATED** |

Eight independent checks pass with stated tolerances; one fails and is
reported as failing; one establishes the spatial limit of an empirical
correction.

---

# 6. HYDRAULIC TRANSITION RESULTS

## 6.1 Pre-breach water-surface geometry

Over 14 pre-breach dates (2019-07-11 to 2022-07-31), per-date Theil-Sen
longitudinal slopes have a **median of +0.090 cm/km** with NMAD 0.176 cm/km
[M1.1]. Crucially, they are **not consistently signed**: 10 of 14 are
positive, and an exact sign test gives p = 0.18. Median within-profile
water-surface range is 0.090 m and median r² is 0.155 — that is, the fits
explain almost nothing, which is precisely what fitting a line to a flat
surface should produce.

"Near-level" is therefore the correct description, and it is supported by two
independent facts: the observed within-overpass water-surface span (median
7.2 cm) *exceeds* the entire mean hydraulic rise across the pool at ordinary
discharge (0–6 cm over 183 km), and the historical documentation independently
tabulates that same near-zero gradient.

## 6.2 Drawdown

Five dates (2023-07-07 to 2023-09-07) fall in the drawdown period, with a
Theil-Sen median slope of **+3.245 cm/km** [M1.2] — already at post-breach
magnitude — and median r² 0.812, the highest of any period, indicating a
coherent sloping surface. Median within-profile range is 1.230 m. No
confidence interval is quoted: n = 5.

## 6.3 Post-breach longitudinal gradient

Over 14 post-breach dates (2024-01-05 to 2025-11-03) the Theil-Sen median
slope is **+3.314 cm/km**, and **14 of 14 dates are positive** (sign test
p = 1.2 × 10⁻⁴) [M1.1]. Median within-profile range is 1.827 m — twenty times
the pre-breach value.

The pre-to-post difference is:

| estimator | pre | post | difference | 95 % CI | permutation p | post positive |
|---|---:|---:|---:|---|---:|---|
| **Theil-Sen (primary)** | +0.090 | +3.314 | **+3.223** | **[+1.994, +5.071]** | 1.0 × 10⁻⁴ | **14/14** |
| OLS (sensitivity) | +0.038 | +2.781 | +2.744 | [+1.454, +4.323] | 3.0 × 10⁻⁴ | 13/14 |

The two estimators agree in sign, magnitude and conclusion [M1.3].

The strength of this result is **not** the p-value. It is the effect size — a
change of roughly two orders of magnitude in the median, from a value
indistinguishable from zero to one an order of magnitude above the historical
low-flow expectation for the whole pool — together with the fact that every
one of 14 independent post-breach dates has the same sign, spanning nearly two
years.

## 6.4 An honest weakness: the channel-restricted control

Restricting both periods to the classified main channel weakens the contrast
[M1.4]. Theil-Sen gives a difference of **+1.109 cm/km with 95 % CI
[−0.086, +2.186]** — the interval **includes zero** — over 20 pre-breach and
only **6** post-breach qualifying dates, with 4 of 6 positive and Cliff's
delta −0.47. The all-water comparison over the same framework gives
+1.873 cm/km [+1.013, +4.454] with 14/14 positive and Cliff's delta −0.99.

This is reported as a limitation, not buried: part of the all-water contrast
may derive from including residual water bodies standing at different levels,
and the channel-restricted test cannot yet separate the two because too few
dates qualify. It is underpowered, not a refutation — but it means the
headline number should be read as *the water surface within the former
reservoir footprint*, not strictly *the channel*.

## 6.5 Water-surface heterogeneity

Independently of any slope fit, the water surface became more heterogeneous.
The p95−p05 range of ATL13 heights within a date rose from a median of
**0.117 m** pre-breach to **0.397 m** post-breach, a difference of
**+0.280 m** with 95 % CI [+0.138, +0.367], permutation p = 5.0 × 10⁻⁵ and
Mann-Whitney p = 1.6 × 10⁻¹¹, over 192 pre-breach and 23 post-breach dates
[M2.1].

Two clarifications are essential. First, this metric (F4) is computed over
**all** ATL13 dates in the footprint, and must not be confused with the
within-profile range of the 14/14 slope-profile sample (0.090 → 1.827 m),
which is a different sample *and* a different spatial restriction. Second,
being altimetric, it is **independent of Sentinel-2 tile coverage**, and so
provides evidence that does not share the planform analysis's weaknesses.

## 6.6 Sentinel-2 planform transformation

Coverage must be handled before anything else. Of the Sentinel-2 dates with
water masks, only **one pre-breach date (2023-06-05)** achieves near-complete
footprint coverage, against 12 post-breach dates [M3.1]:

| | pre-breach 2023-06-05 | post-breach (n = 12, median) |
|---|---:|---:|
| footprint observed | 100 % | ≥ 95 % |
| connected water bodies | **2** | **224** (range 48–602) |
| total water area | **2 033 km²** | **258 km²** |
| largest component | **99.995 %** of water area | **78.7 %** (range 32.7–95.6 %) |

The contrast is stark and physically unambiguous: a single continuous
impounded surface has been replaced by a fragmented, channel-dominated system.

**No p-value is computed, and none may be.** With n_pre = 1 there is no
pre-breach distribution. An earlier version of this analysis treated partial
pre-breach scenes as full-reservoir observations and produced a significance
test; that test is **invalid** and is superseded. The planform evidence is
descriptive and supports the altimetric result; it is never the primary
inferential evidence.

## 6.7 Residual water bodies

Residual water bodies sit systematically **below** the adjacent channel stem:
median offset **−0.595 m** with NMAD 0.459 m over 678 high-confidence matched
observations, with only 15.3 % lying above the stem [M3.2]. This is consistent
with disconnected remnant ponds in bed depressions rather than a continuous
water surface, and it explains why an all-water slope fit and a
channel-restricted one need not agree (Section 6.4).

The all-classified population (median −0.436 m, maximum +12.59 m) is
contaminated by a 2023-09-01 non-water cluster 5.6 km off the stem and is not
quoted.

## 6.8 Temporal persistence

The post-breach gradient is not a transient of the drainage event. The 14
post-breach dates span **2024-01-05 to 2025-11-03** — beginning seven months
after the breach and continuing for a further 22 months — and every one has a
positive slope. The drawdown period (July–September 2023) already shows
post-breach magnitudes. The change is therefore a **regime shift**, not a
relaxation transient.

---

# 7. DISCUSSION

## 7.1 From a reservoir-like to a river-dominated regime

The observations describe a system that has changed its hydraulic character.
Pre-breach, the water surface was flat to within the instantaneous
disturbances acting on it, with slopes of either sign and negligible
explanatory power. Post-breach, a directional gradient of ~3.3 cm/km is
present on every observation date across two years, with twentyfold greater
within-profile relief and a planform of hundreds of disconnected water bodies.

The term used throughout is **river-dominated system**, not "became a river".
The distinction is not cosmetic: several hundred residual water bodies persist,
standing a median of 0.6 m below the channel stem, and they are part of the
present state rather than noise around a river.

## 7.2 What the historical documentation contributes

The historical material plays three distinct roles that are worth separating.

As a **vertical reference check**, it supplied the capacity curve that
independently located the sounding datum at 13.71 m without using any
satellite data, corroborating the published 14.00 m and rejecting the assumed
16.00 m.

As a **hydraulic reference**, it supplied both calculated and field-measured
longitudinal free-surface profiles, against which pre-breach ICESat-2 slopes
could be tested reach by reach — a validation of modern satellite altimetry
against in-situ hydraulic measurements made over 50 years earlier.

As a **morphological reference**, it supplied reach drawdown exposure areas,
reproduced to 0.6 pp by an independently reconstructed bed.

We are not aware of another study that uses mid-20th-century reservoir design
documentation as a quantitative validation target for modern satellite
altimetry in this way, but this observation is marked **preliminary**: no
formal literature review has been carried out, and novelty claims of this kind
require one (Section 8).

## 7.3 Seiches, wind setup, and why the pre-breach scatter is physical

The historical documentation resolves what would otherwise look like noise. A
seiche antinode contributes ~17.5 cm at a point (half the binodal range), and
wind setup reaches 35 cm at the reservoir ends in a moderate gale and 148 cm in
an extreme storm. Against these, the *entire* mean hydraulic rise across the
183 km pool is 0–6 cm at ordinary discharge [V8.1].

The observed pre-breach within-overpass span — median 7.2 cm, p90 11.3 cm —
sits inside the documented dynamic range and *exceeds* the mean gradient. So
individual pre-breach profiles with small positive or negative slopes are
exactly what a flat pool subject to documented seiches and wind setup should
produce, and the near-zero *median* over 14 dates is the regime-level signal.

**No causal attribution is made for any individual date.** No simultaneous
wind or pressure forcing data exist for these epochs, so attributing a specific
satellite anomaly — including the 2023-04-05 residual — to wind or seiches
would be speculation. This is a scale argument, and it must not be confused
with the mean hydraulic slope.

## 7.4 Interpreting instantaneous satellite slopes

Three cautions follow, and they generalise beyond this site.

First, a **single overpass is an instantaneous sample** of a surface that
oscillates on periods of 3.5–13.4 h. A slope fitted to one overpass of a
near-level pool measures the instantaneous disturbance field at least as much
as the mean gradient.

Second, **cross-track beam geometry inflates apparent along-flow distance.**
Six beams spanning 3.3 km across a wide pool produce a chainage span inflated
by a median 1.34×, which distorts any slope computed over a short window and
can flip its sign (Section 5.8).

Third, **fit span matters more than fit quality.** Pre-breach fits span a
median of 8.3 km (p75 13.1 km, max 74.4 km) in the reach analysis and ~24 km in
the per-date analysis; none spans the reservoir. The per-date slope median is a
statement about the *distribution of local slopes*, and the earlier phrasing
"the pre-breach reservoir slope was +0.09 cm/km" was withdrawn precisely
because it implies a whole-reservoir measurement that was never made.

## 7.5 Spatial transferability of empirical corrections

The empirical alignment constants are tight within the reservoir
(−0.184, −0.152, −0.130, −0.112, −0.081 m; median −0.130 m, station-to-station
NMAD 0.033 m) and indistinguishable from zero at Kherson (+0.005 m, CI
[−0.052, +0.061]), with disjoint confidence intervals [V5.1]. Each is stable
against its own matchup radius (1.8 cm swing at Rozumivka, 5.0 cm at Kherson
over 1–10 km), so the gap is a property of the two domains, not of the
estimator.

This is a **positive methodological finding**, not a failure. It establishes
the *spatial limit* of an empirical correction and separates three things that
are routinely conflated: an official geodetic transformation (EPSG:9902, valid
by construction), a local empirical alignment (valid where estimated), and an
independent validation (which tests the first two). The recommended strategy is
domain-specific and pre-breach only: a reservoir-domain constant of −0.130 m
is defensible, nothing should be applied at Kherson, and the two must never be
cross-applied. **No post-breach constant exists at any station**, because
ICESat-2 water coverage collapses at the gauges after the breach — Rozumivka
retains a single segment within 2 km — so post-breach satellite levels are
reported uncorrected and flagged.

## 7.6 Implications for satellite hydrology

Four transferable lessons emerge.

1. **Validate the processing chain, not just the product.** Four candidate
   PIXC chains differ by up to 23.9 m, and only an empirical test against an
   independent product of the same mission identified the right one.
2. **Permanent-tide conventions are not negligible** at the accuracy now
   available: the tide-free-to-mean-tide term is 3.5–3.9 cm here, comparable to
   the sensor agreement being tested.
3. **Vertical transformations are spatially varying.** The 4.4 cm spread in
   EPSG:9902 across 250 km would be absorbed into any single national constant.
4. **Collocation quality dominates cross-sensor comparison.** The residual
   between SWOT and ICESat-2 falls from 18 cm to 0.4 cm as Δt falls from 14 h
   to 2 h — so cross-sensor "biases" reported without collocation statistics
   may be measuring hydrodynamics.

## 7.7 Implications for Kakhovka monitoring and reconstruction

The reconstructed bed surface, now on a defensible vertical reference, is a
usable pre-breach baseline: it reproduces an independent historical
morphological statistic to 0.6 pp and is converged with respect to grid
resolution. Any future reconstruction scenario requires exactly this — a bed in
a modern frame, with a stated point-scale uncertainty (2.76 m RMSE) and stated
integrated reliability.

Two operational cautions follow. First, SWOT's LakeSP product should not be
used for this system post-breach: it still assigns the fragments to a single
PLD polygon and produces "levels" spanning 2.35–12.07 m across a sloping
channel. RiverSP is the correct product. Second, no post-breach empirical
correction is available at any gauge, so post-breach satellite levels here
carry only the geodetic transformation.

## 7.8 Comparison with literature

**Deferred.** No systematic literature review has been carried out, and this
draft deliberately does not assert priority. Section 8 lists the specific
comparisons a v2 must make. Statements in Section 7.2 about the novelty of
using historical design documentation as a validation target are marked
preliminary for this reason.

## 7.9 Limitations

Collected in one place, with none omitted:

1. **SWOT never observed the reservoir pool.** The SWOT branch is validated
   only downstream at Kherson, and the reservoir result rests on ICESat-2.
2. **2023-04-05 is unresolved.** The gauge hydrograph has the wrong sign and
   enlarges the residual; no local wind data exist.
3. **The channel-restricted control includes zero** with 6 qualifying
   post-breach dates (Section 6.4).
4. **Sentinel-2 pre-breach full coverage is n = 1**, so the planform contrast
   is descriptive.
5. **The historical Baltic realisation is unresolved** (BS-42 vs BS-77), a
   few-centimetre unaccounted step.
6. **The survey epoch is unrecorded**, so no bed-change rate can be formed and
   none is claimed.
7. **SWORD is a modern-channel proxy**, not a pre-1956 channel reconstruction.
8. **Local bed depth carries metre-scale uncertainty.** RMSE 2.76 m with
   median absolute error 0.89 m, heavy-tailed and concentrated in steep
   channel walls and the near-shore margin (Section 5.9.1). Narrow incised
   forms are under-determined by a 357 m survey and would need breaklines or
   a channel axis as constraints. Integrated area statistics are unaffected.
9. **Reaches 1 and 2 cannot be separated** (~30 km residual at Babyne), and
   **reach 5 is outside the mapped domain** (recorded NA).
10. **Cross-sensor agreement rests on n = 3** overpasses; no universal bias is
    claimed.
11. **The reference-level uncertainty (0.40 m) is not propagated** into the
    exposure confidence intervals.
12. **The longitudinal roughness contrast is weak**, resting on one 20 km band.
13. **Historical Table 21 comparison uses three reach units**, too few for
    inference.
14. **Fig. 16 curve 4 is not digitised.**

---

# 8. CONCLUSIONS

1. Gauge, ICESat-2 and SWOT water-surface elevations can be brought into one
   vertical frame with demonstrable consistency: post-tie residual NMAD is
   0.044 m against gauges over 55 matched observations, and the geodetic
   transformation is an official operation applied at each station's own
   coordinates, with a 4.4 cm spatial spread that a national constant would
   have hidden.

2. The SWOT PIXC vertical chain behaves as documented, and only as documented:
   the correct branch reproduces RiverSP to −0.0010 m while the alternatives
   fail by +0.072, +0.145 and −23.89 m.

3. Under close temporal collocation SWOT and ICESat-2 agree to +0.0036 m with
   no correction applied. On n = 3 overpasses this demonstrates that agreement
   is *achievable*, not that it is universal.

4. An empirical alignment derived in the reservoir does **not** transfer
   downstream: reservoir −0.130 m against Kherson +0.005 m, disjoint intervals.
   The validation therefore establishes the spatial limit of the correction.

5. The historical soundings are reduced to the published navigation drawdown
   level of **14.00 m**, not the normal impoundment level of 16.00 m. The
   corrected bed agrees with post-breach ICESat-2 exposed-bed heights to
   **+0.098 m** with zero inside the interval, against −1.90 m under the former
   assumption. Three independent routes agree to within 0.40 m.

6. A field-measured historical longitudinal free-surface profile is reproduced
   to **0.047 m** over the pool, and pre-breach ICESat-2 slopes reproduce the
   documented flat-pool-plus-backwater-limb geometry within the historical
   discharge envelope.

7. An independently reconstructed bed reproduces the historical drawdown
   exposure fraction to **0.57 percentage points** and its upstream increase
   under all four cross-validated interpolators, with the result converged
   against grid resolution to 0.06 pp.

8. **The main result.** The pre-breach impounded reservoir had a near-level
   water surface (Theil-Sen median +0.090 cm/km, 10/14 positive, sign test
   p = 0.18); the post-breach system has a persistent directional gradient
   (+3.314 cm/km, 14/14 positive). The difference is **+3.223 cm/km, 95 % CI
   [+1.994, +5.071]**, sustained across 22 months. Water-surface heterogeneity
   rose from 0.117 to 0.397 m, and Sentinel-2 shows a single 2 033 km² surface
   replaced by a fragmented system of hundreds of water bodies.

9. The system is best described as **river-dominated with persistent residual
   water bodies**, not as "a river": several hundred remnant ponds stand a
   median 0.6 m below the channel stem.

## Next steps for v2

- A systematic literature review, to replace every preliminary novelty claim
  in Section 7.2 and to write Section 7.8.
- Digitise Fig. 16 curve 4 by curve tracing.
- Expand the channel-restricted sample so Section 6.4 is no longer
  underpowered.
- Resolve the historical Baltic realisation (BS-42 vs BS-77).
- Recover a wind/pressure record for 2023-04-05.
- Second-echelon historical material not yet used: Fig. 177 (dynamic volumes),
  Fig. 178 (1966 flood discharges), Fig. 167 (gauge network).
- Propagate the 0.40 m reference-level uncertainty into the exposure intervals.

---

# 9. DATA AND CODE AVAILABILITY

All processing code is in the project repository under `scripts/` and
`src/swot_dnipro/`. Every number in this draft is traceable through
`outputs/tables/manuscript_evidence_matrix.csv`, which names the source table,
report and figure for each claim.

The reconstructed bed is released as GeoTIFF in EPSG:32636, EVRF2019 normal
heights, nodata −9999:

| raster | content |
|---|---|
| `kakhovka_bed_OK_epoch_250m.tif` | bed elevation, **canonical scientific grid** |
| `kakhovka_bed_OK_epoch_50m.tif`, `..._30m.tif` | the same surface for display; hist17 shows the integrated exposure fraction moves only −0.06 pp between them, so they add resolution rather than information |
| `kakhovka_bed_method_spread_250m.tif` | range across the four cross-validated interpolators (median 0.40 m, p90 2.38 m). A **lower bound** on uncertainty and **not** a kriging variance: four smooth interpolators on sparse points agree with one another more than any agrees with the bed |
| `kakhovka_bed_local_roughness_250m.tif` | the strongest measured predictor of error |
| `kakhovka_bed_dist_to_sounding_250m.tif` | data-geometry layer |
| `kakhovka_bed_confidence_class_250m.tif` | 1 high / 2 moderate / 3 lower, from the band edges of the Section 5.9.1 decomposition |

The confidence class is worth using rather than treating the surface as
uniformly reliable: 38.9 % of the area is class 1, 31.5 % class 2 and 29.6 %
class 3, and the independent inter-interpolator spread rises 8.2× across them
(0.16 → 1.34 m), which the class was not fitted to reproduce.

The cross-validation fold partition is released as
`hist14_cv_fold_assignments.parquet` so the reported scores are reproducible
independently of random-number call order.

Third-party inputs: ICESat-2 ATL03/ATL08/ATL13 (NASA NSIDC, via SlideRule);
SWOT L2_HR_PIXC and L2_HR_RiverSP (NASA/CNES, via Earthdata); Sentinel-2 L2A
(ESA Copernicus); SWORD v16 river network; EGG2015 quasigeoid (Denker 2015 /
ISG); EPSG:9902 transformation grid `ua_2019z.asc` (EPSG registry / BKG
EVRF2019). Gauge records are from the national hydrological yearbooks. The
historical bathymetry is an S-57 dataset; the historical hydraulic tables were
transcribed from photographed pages of the Dnipro reservoirs monograph and are
included as CSV in `data/historical/` with arithmetic checksums, because no
script can regenerate them.

**Full source citations for the historical monograph, the S-57 dataset
provenance, and the gauge yearbooks must be completed before submission** —
they are recorded in the repository as descriptive labels, not formal
citations.

# 10. ACKNOWLEDGEMENTS

To be completed.

# REFERENCES

**To be completed.** The draft cites the following by document rather than by
formal reference, and each must become a proper citation:

- SWOT PIXC Product Description Document D-56411 Rev C (tide fields not
  applied; p. 53)
- ICESat-2 ATL03 ATBD Release 007 (tide-free system, pp. 8, 20, 26;
  `tide_earth_free2mean`, pp. 125–126)
- ICESat-2 ATL13 ATBD
- Denker, H. (2015) — EGG2015 European Gravimetric Quasigeoid
- BKG — EVRF2019 realisation (EPSG:9389)
- EPSG registry — coordinate operation 9902 (BS-77 → EVRF2019), grid
  `ua_2019z.asc`, stated accuracy 0.068 m
- IAG Resolution 16 (1983) — permanent tide conventions
- SWORD v16 — Altenau et al., SWOT River Database
- McFeeters (1996) — NDWI; Xu (2006) — MNDWI
- Sen (1968), Theil (1950) — robust slope estimation
- Dnipro reservoirs monograph — Tables 19–21, 200; Figs 13, 16, 46, 57, 128
  (full bibliographic details required)

# SUPPLEMENTARY MATERIAL PLAN

**S1 Tables.** Per-station gauge metadata and EPSG:9902 offsets; the full
per-date slope table (33 dates); the full cross-sensor matchup table; the
four-level historical reference sensitivity; the 12-row interpolator × scheme
cross-validation; grid-resolution results by reach; Fig. 16 digitisation with
per-point deviations; the historical reach chainage crosswalk.

**S2 Figures.** The 180–210 km geometry diagnosis; historical seiche and
wind-setup context (4 panels); geomorphological tests B/C/D/F; fine-grid
surfaces at 50 m and 30 m; the dry-bed classification QA chain; aggregation-
radius and water-mask sensitivity.

**S3 Methods notes.** The invalid point-count exposure test and why it was
superseded; the unconstrained-shoreline experiment as an audit trail; the
withdrawn "bimodal bed" framing; the kriging singularity failure and its fix;
the non-linear axis calibration of Fig. 16.

**S4 Evidence matrix.** `manuscript_evidence_matrix.csv` in full — 33 claims
with source table, source report, source figure, validation status,
limitations and supersession notes.
