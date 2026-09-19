# ARTICLE_FIGURE_TABLE_BLUEPRINT — Paper 3

Minimum set that proves the paper: **7 figures (one a map), 4 tables, 1 supplement block.** Every panel names its input table and generating script; "existing" means a file with that name exists in `SWOT-DNIPRO/outputs/figures/` on 2026-09-18 with the stated mtime. Publication readiness is per the audit, not per appearance. File paths are relative to `SWOT-DNIPRO/`.

---

## F1 — Study system and observation network (MAP, §2)
**Message.** Where the reservoir was, the dam, the Dnipro, the seven gauges, the chainage axis; what each satellite observed; the historical reach boundaries.
| panel | content | inputs | script | existing | status |
|---|---|---|---|---|---|
| a | footprint (reservoir polygon), dam, Dnipro/SWORD, 6 reservoir gauges + Kherson, chainage axis (0 at dam), reaches 1+2 / 3 / 4 | `Kakhovka_SA_2.geojson`; `config/spatial_domains.yaml`; `gauge_vertical_reference_summary.csv`; `data/historical/historical_reservoir_reaches.csv`; SWORD v16 | V0 script (validation_series) — extend | `validation_series/png/V0_gauge_network_en.png` 09-09 | build composite |
| b | ICESat-2 beam-pass geometry coloured by period (PRE 14 / DRAWDOWN 5 / POST 14 slope dates) | `atl13_water_classification.parquet`; `FigD_kakhovka_profile_points.csv` | SFig01 script | `publication/png/SFig01_swot_icesat_track_geometry.png` 09-07 | regenerate with the three samples marked |
| c | SWOT coverage: pool never covered; Kherson cycles 482/511/521 | `swot_dataset_inventory.csv`; `Fig12_coverage_timeline.csv` | Fig12 script | `publication/png/Fig12_swot_prebreach_coverage.png` | reuse |
Axes: EPSG:32636 for panels, lat/lon ticks. Units: km. Uncertainty: none (geometry). Caption must state the chainage convention (reservoir axis, not SWORD) and that SWOT coverage is a mission fact. **Readiness: inputs ready; composite to build.**

## F2 — Vertical-reference harmonisation (FIGURE, §4.1/§5.1)
**Message.** Four native surfaces and two tide conventions reduced to one frame; every arrow is an operation with a magnitude and an uncertainty.
| panel | content | inputs | existing | status |
|---|---|---|---|---|
| a | chain diagram with magnitudes: gauge BS-77 +Δ9902 (+0.17…+0.22 m, ±0.068) → EVRF2019; ATL13 h −ζ_EGG2015 (+free2mean +0.037 m) → EGG2015 frame (+c −0.13…−0.22 m, empirical); SWOT PIXC height −(SET+load+pole) −ζ → same frame; residual permanent tide 1–4 cm as a dashed box | `vertical_reference_audit.csv`; `Fig04_permanent_tide_correctors.csv`; `egg2015_to_evrf2019_by_station.csv` | `validation_series/png/V1_vertical_reference_framework.png` 09-10 (also `_en/_uk`) | redraw with numbers |
| b | Δ_EPSG9902 at the 8 stations vs longitude (0.1715–0.2157 m) with the 0.068 m accuracy band | `gauge_vertical_reference_summary.csv` `delta_epsg9902_m` | **does not exist** | build (10 lines of matplotlib) |
| c | free2mean term by latitude and the six station correctors before/after (−0.1695 → −0.1321 m) | `Fig04_permanent_tide_correctors.csv`; `Fig04_regional_median_summary.csv` | `publication/png/Fig04_permanent_tide_effect_correctors.png` 09-07 | reuse |
Units m; y-axes symmetric where signed. Caption: the 4.4 cm station spread is why a national constant is inadmissible; the grid accuracy exceeds the spread; PIXC vs RiverSP is internal. **Readiness: blocked only by G2 (EGG2015 citation) for the caption's reference.**

## F3 — Sensor and gauge validation (FIGURE, §5.2–5.5, 4 panels)
| panel | content | inputs | existing (09-09 unless noted) |
|---|---|---|---|
| a | PIXC chain vs RiverSP: four branches (−0.0010 / +0.072 / +0.145 / −23.89 m), n 1023 | `validation_summary_table.csv`; `Fig03_pixc_riversp_sign_validation*.csv` | `V2_pixc_sign_validation.png` |
| b | SWOT − ICESat-2 by date vs |Δt| (three pairs: +0.0036 @ −2.3 h; +0.060 @ +11.1 h; −0.181 @ +14.2 h) with per-beam NMAD bars | `part4_colocated_swot_icesat.csv`; `Fig16/Fig17_*` | `V3_swot_icesat_colocated.png` |
| c | Kherson hydrograph 2023 with SWOT/ICESat epochs | `FigA_kherson_hydrograph_context.csv` | `V4_kherson_hydrograph_epochs.png` |
| d | 2023-04-05: observed −18.1 cm vs hydrological expectation +5.9 cm (residual −24.0) | `part4_colocated_swot_icesat.csv` | `publication/png/FigC_observed_vs_hydrological_expectation.png` 09-07 |
Sign convention SWOT − ICESat-2 printed on every panel. Caption: (a) internal check; (b) n = 3, no regression; (d) negative result. **Readiness: compose; ready.**

## F3P — Longitudinal water-surface profiles (FIGURE, §6.1–6.3) — *the central figure with F4*
**Message.** The shape of the surface itself changed; observations, not conceptual lines.
| row | content | inputs |
|---|---|---|
| PRE | all 14 slope-date profiles (6 beam-median points each, NMAD bars) on common chainage 0–250 km; overlay the digitised 22–25 April 1970 field curve (Fig 16 curve 2) and the Table-20 envelope | `kakhovka_longitudinal_profiles.csv`; `data/processed/historical/historical_fig16_profiles.csv`; `historical_longitudinal_wse.csv` |
| DRAWDOWN | 5 profiles, 2023-07-07 (−5.25 cm/km, 7.4 m range) visibly flagged | same |
| POST | 14 profiles; pond outliers (points >1 m above the fitted line) marked distinctly | same |
Existing: `publication/png/FigD_kakhovka_longitudinal_profiles.png`, `FigF_postbreach_longitudinal_gradient.png` (09-07). Script `make_longitudinal.py`. Axes: chainage km (dam = 0) vs WSE m EVRF2019-compatible (EGG2015 + c). Caption: each profile is one overpass spanning a median 24–27 km; the claim is about the distribution of local slopes; the historical curve is a discharge-dependent design surface, EVRF2019 via 14.00/16.00 m + Δ9902. **Readiness: rebuild as one figure; inputs ready.**

## F4 — Slope regime transition (FIGURE, §6.3–6.4, 4 panels)
| panel | content | inputs | existing |
|---|---|---|---|
| a | Theil-Sen slope per date 2019–2025, breach line 2023-06-06, marker size = span km; OLS as hollow markers | `kakhovka_perdate_slopes_robust.csv` (33 rows) | `publication/png/FigG_reservoir_to_river_transition.png` 09-07 (panel d); `phase19_20/png/P19_Fig4_channel_slope_timeseries.png` 09-08 |
| b | PRE / DRAWDOWN / POST distributions (box + points), medians +0.090 / +3.245 / +3.314, Δ +3.223 [+1.994, +5.071], perm p 1e-4, 14/14 | `kakhovka_transition_statistics.csv` | FigG; `P19_Fig5_channel_slope_distributions.png` |
| c | span-sensitivity: median TS and fraction positive at min span 10/20/30/40 km with n | `FigG_span_sensitivity.csv` | FigG panel |
| d | channel-restricted control: +1.109 [−0.086, +2.186], n 20/6, Cliff's δ −0.47; show that the CI includes zero | `reservoir_to_river_statistics.csv`; `allwater_vs_channel_slopes.csv` | `P19_Fig7_allwater_vs_channel.png` 09-08 |
Units cm/km; y symmetric; n printed in every panel. Caption: unit = overpass; 6 beam points; effect size and 14/14 sign consistency, not the p-value; (d) is the honest weakness. Script: `make_transition_stats.py`, `phase19_profiles.py`. **Readiness: compose; ready.**

## F5 — Historical validation and reconstructed bed (FIGURE, §5.7–5.9)
| panel | content | inputs | existing | status |
|---|---|---|---|---|
| a | reference-level sensitivity: 16.00 rejected (−1.90 m), 14.00 preferred (+0.10 m [−0.08, +0.30]); three routes 13.71 / 14.00 / 14.11 m spanning 0.40 m | `historical_reference_sensitivity.csv`; `hist2_datum_fit.csv` | `V2_historical_reference_sensitivity.png` 09-09; `HIST2_datum_closure.png` 09-09 | reuse |
| b | reconstructed bed (hist14 OK 250 m) with blocked-CV panel: OK 2.761 / IDW 2.781 / LINEAR 2.822 / RBF 3.228 m | `hist14_interpolator_cv.csv`; `hist14_surface_summary.csv` | `V12_bed_surface_validation.png` 09-10 11:19 | **STALE** — predates 09-13 registry-domain rerun; regenerate |
| c | Fig 16 curve 2 digitised vs Table 20 (median +0.022, NMAD 0.045 m) | `historical_fig16_profiles.csv` | `HIST9_fig16_digitisation.png` 09-10 | reuse (note non-linear source axis) |
| d | exposure fraction by reach and whole: historical 3.9/9.4/26.0/12.95 % vs reconstructed 7.5/9.3/21.7/12.77 % with interpolator range **and a datum-propagated band (to compute, ±1.1 pp)**; grid convergence 250/50/30 m inset | `historical_exposure_area_validation.csv` 09-13; `hist17_grid_resolution.csv` 09-13 | `V13_exposure_area_validation.png` 09-10 06:09; `V15_grid_resolution_convergence.png` 09-10 | **STALE** — regenerate |
Units m / %. Caption: Table 21 was validation not calibration; reach 5 NA; reaches 1+2 merged; boundary 17.08 m ≠ denominator 16.18 m; the agreement sits inside the datum band. **Readiness: P0 — regenerate b/d, add band, update draft numbers.**

## F6 — Water-surface heterogeneity (FIGURE, §6.5)
Within-overpass p95–p05 range on all ATL13 dates in the footprint: 192 pre (median 0.117 m) vs 23 post (0.397 m), Δ +0.280 [+0.138, +0.367]. Input `pre_post_fragmentation_statistics.csv`; existing `P20_Fig2_atl13_by_class.png` 09-16 15:10 (current, post-B-2b). May be folded into F4 as panel (e). Caption: metric definition; distinct from the slope sample's within-profile range (0.090 → 1.827 m). **Ready.**

## F7 — Planform transformation (FIGURE + MAP, §6.6–6.7, descriptive)
| panel | content | inputs | existing | status |
|---|---|---|---|---|
| a | water mask 2023-06-05 (2 bodies, largest 99.995 %) vs one full-coverage post date; coverage shown as hatch | `fragmentation_metrics_by_date.csv`; `phase20_footprint_coverage*.csv`; masks | `P20_Fig1_fragmentation.png` 09-16 (current) | reuse |
| b | largest-component fraction by date with coverage class (no counts, no p-value) | same | `P19_Fig1_water_extent_pre_draw_post.png` 09-08 | **re-emit** post-A4/B-2b |
| c | residual bodies vs channel stem: offset −0.595 m NMAD 0.459, n 678 (population B) | `residual_water_offset_summary.csv`; `P19_residual_per_body.csv` | `P19_Fig6_residual_water_offsets.png` 09-08 | check against 09-16 masks |
Caption: one pre-breach full-coverage date; descriptive; counts are coverage-confounded (ρ = +0.57) and are not shown. **Readiness: partial regeneration.**

---

## Tables (all machine-generated; extend `scripts/ms1_evidence_matrix.py` or add `ms5_manuscript_tables.py`)

### T1 — Data sources (§3)
Columns: dataset · sensor/source · product/version · period · spatial sampling · vertical reference (native) · tide system · role · QC · n. Rows: 6 reservoir gauges; Kherson; ATL13 slope sample (14/5/14 dates, min span 20 km); ATL13 F4 sample (192/23); ATL13 S7 exposed bed (28 tracks); SWOT PIXC (cycles 482/511/521); SWOT RiverSP (482/001); Sentinel-2 L2A (coverage-gated; 1 pre full-coverage date); S-57 soundings (7 514, epoch unknown); monograph Tables 19–21/Fig 16; `ua_2019z.asc`; `egg_2015.tif`; SWORD v16. Sources: `dataset_inventory.csv`, `gauge_vertical_reference_summary.csv`, `k10_modern_observation_inventory.csv`, `kakhovka_perdate_slopes_robust.csv`.

### T2 — Vertical harmonisation (§4.1/§5.1)
Columns: source · original reference · target · operation (grid/formula) · magnitude (range) · uncertainty · **validation type** (internal-arithmetic / cross-sensor / in-situ / historical) · claim. Rows: gauges BS-77→EVRF2019 (EPSG:9902 `ua_2019z.asc`, +0.1715…+0.2157, ±0.068, sampler 0.000 mm, V1.1/V1.2/G1); ATL13 tide-free→mean (free2mean, +0.0374, C-11); ATL13 h−ζ (EGG2015, −0.145 ± 0.027 vs EGM2008 check, G2); station corrector c (−0.128…−0.217, bootstrap CIs, V5.1); SWOT PIXC chain (−0.0010 vs RiverSP, internal, V2.1); SWOT vs ICESat-2 (+0.0036 @ −2.3 h, cross-sensor, V3.1); SWOT vs Kherson gauge (+0.0264, NMAD 0.041, in-situ, V4.1); historical 14.00 m BS-77 → 14.185 m EVRF2019 (V6.2); residual permanent tide (1–4 cm, budget line). Sources: `vertical_reference_audit.csv`, `validation_summary_table.csv`, `egg2015_to_evrf2019_by_station.csv`, `part4_colocated_swot_icesat.csv`, `hist3_datum_in_evrf2019.csv`.

### T3 — Hydraulic state comparison (§6)
Columns: metric · unit · pre (median) · drawdown · post · Δ post−pre · 95 % CI · test p · n (unit) · interpretation · claim. Rows: slope Theil-Sen (+0.0905 / +3.2449 / +3.3137; +3.2232 [+1.9944, +5.0707]; perm 1.0e-4; 14/5/14 dates; M1.1/M1.2); slope OLS (+0.0376 / +2.3769 / +2.7815; +2.7439 [+1.4542, +4.3225]; M1.3); channel-restricted TS (−0.007 / — / +1.101; +1.1089 [−0.0861, +2.1858]; 20/2/6; M1.4 — flagged underpowered); F4 heterogeneity (0.1169 / — / 0.3967 m; +0.2798 [+0.1380, +0.3669]; 192/23; M2.1); within-profile range of the slope sample (0.090 / 1.230 / 1.827 m; descriptive; `kakhovka_pre_post_slope_summary.csv`); largest-component fraction (99.995 % / — / 31.5–80.6 %; descriptive, 1/4 dates; M3.1); residual-body offset (— / — / −0.595 m NMAD 0.459; n 678; M3.2); exposure fraction historical vs reconstructed (12.95 vs 12.77 %, −0.18 pp, band ±1.1 pp; V10.1). **No discharge row** (C-14 NOT TESTABLE — stated in a footnote).

### T4 — Literature positioning (§7.8) — BLOCKED
Three blocks K / T / D; columns: our thesis (AT) · closest precedent (DOI, verified passage) · what precedent shows · what this study adds · evidence status (screened-absence with denominator and control recall / precedent / method). Generated by `python -m src.paper_3.cli --step publish` after the acceptance gate passes; until then the table is the `[PENDING]` stub.

---

## Supplement
S1 `HIST7_slope_geometry.png` (180–210 km artefact, V7.3); S2 `V7_historical_seiches.png` + `V8_wind_setup_by_gauge.png` (scale argument, V8.1); S3 `V16_cv_error_diagnosis.png` (V10.4; check vs 09-13 rerun); S4 `V17_multilevel_constraints.png` 09-13 (V10.5); S5 `V14_geomorphological_validation.png` 09-13 (V9.1–V9.3); S6 `Fig10_radius_sensitivity_kherson.png`, `Fig09_water_mask_sensitivity.png`; S7 a "which sample" table (three pre-breach windows); S8 span-sensitivity full table.

## Traceability rule for every figure
Before a figure enters the manuscript: (1) script named; (2) input tables named with mtime ≥ the last upstream rerun (09-13 for anything touching hist15/17 or the registry domain; 09-16 for anything touching Sentinel masks/footprint); (3) values in the caption pulled from the table, not typed; (4) V-namespace file renamed (`VAL*`/`HIS*`); (5) figures older than the 2026-09-09 19:48 datum correction never reused for bed elevations.
