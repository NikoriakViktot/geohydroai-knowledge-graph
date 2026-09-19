# ARTICLE_REPOSITORY_AUDIT — Paper 3 (Kakhovka hydraulic transition)

Phase 1, read-only. Audited 2026-09-18 by the article-planning agent. Nothing was modified, regenerated, committed or repaired. Every path below was opened; every number was read from a machine-readable file, not from prose.

## 0. Environment and state at audit time

| item | value |
|---|---|
| this session's distro | WSL "Ubuntu" (`/home/niko/projects/knoweledg_graf`) |
| scientific repos | WSL "Ubuntu-24.04", reached with `wsl.exe -d Ubuntu-24.04 --cd <repo> -- bash -s < script` (interop works; `$`-expansion in inline args does not, so scripts were passed on stdin) |
| bulk data | `F:\data_kakhovka_dem_swot` = `/mnt/f/data_kakhovka_dem_swot` (mounted in both distros) |
| `knoweledg_graf` | commit `c3ec671` (the only commit), **58 modified/untracked paths** (dirty) |
| `SWOT-DNIPRO` | commit `6bf0c40` 2026-09-18 14:32 "p38: pilot report (ZONE_4) …", **269 dirty paths** incl. `outputs/tables/manuscript_evidence_matrix.csv`, `fragmentation_metrics_by_date.csv`, `channel_connectivity_by_date.csv`, `config/spatial_domains.yaml` |
| `icesat2-atl13-kakhovka` | commit `b1c8720` 2026-09-02 "Initial commit", 18 dirty paths |
| manuscript v1 | `Kakhovka_scientific_report_article_draft_v1.md` — byte-identical in both repos, sha256 `8fca683bb2b8aaf7af7b78e8697ff8380ff037cbbf1d4a1470a079e361676c0a`, dated 2026-09-10 11:26, 11 554 words |
| production BS-77→EVRF2019 grid | `icesat2-atl13-kakhovka/data/external/datum/ua_2019z.asc` sha256 `297440243e2ed8d67b1a61c6099db173b5fb13f85973854198b167c6c67b1a95` (167×99, 0.15°, 3776 valid nodes, ETRS89/GRS80, NODATA −9999) |
| production quasigeoid | `icesat2-atl13-kakhovka/data/1_data/egg_2015.tif` sha256 `933cb8372fa5832b76d2acefba115c34c07af225bc9ec1d12a71d9b18e756d40` (1′×1′, **provenance unresolved**) |
| latest SWOT-DNIPRO audit | `audit_runs/20260918T175108Z/` (started today 17:51 UTC; Phase 0 partially done; carries C-01…C-16 from `20260916T093000Z`) |
| retrieval freeze (RAG) | rules v1.2.0, 3680 papers, ChromaDB `flood_papers_768d` 1 313 665 chunks at freeze (1 350 310 today after the paper_3 ingest), theses.yaml sha `12ec441b…`, acceptance gate **NOT READY** |

A dirty tree means no commit hash identifies the code that produced the tables. This is recorded, not fixed.

## 1. Repository map and roles

```
knoweledg_graf  (Ubuntu)                        SWOT-DNIPRO  (Ubuntu-24.04)
├── src/paper_3/        24 theses, harvest,     ├── src/swot_dnipro/   config (datums, breach date, free2mean),
│   retrieve, gap matrix, controls, v2 claim    │   vertical.py (three chains), sword.py, watermask.py,
│   map + literature matrix, insitu yearbooks   │   spatial_domains.py (authoritative geometry)
├── paper_3_audit/      published RAG outputs   ├── src/swotdl/        SWOT downloader (tests)
│   (GAP/LITERATURE matrices, manifests,        ├── scripts/           numbered lab notebook: hist*, k*, part*,
│   controls, slice denominators, insitu_2023)  │   f*, p1*, phase19/20, p2x–p38, ms1–ms4, qa*
├── data/literature, ChromaDB, Neo4j, OpenAlex  ├── outputs/tables/    ~400 CSV/parquet (machine-readable results)
└── src/document, ingestion (corpus pipeline)   ├── outputs/figure_data/  per-figure CSVs (Fig03…FigG, P19*)
                                                ├── outputs/figures/   441 png/pdf in 12 subdirs (namespace collision V2–V8)
icesat2-atl13-kakhovka (Ubuntu-24.04, READ-ONLY ├── outputs/reports/   SCIENTIFIC_FINDINGS, FULL_SYSTEM_STATUS, article_figure_plan,
companion of SWOT-DNIPRO)                        │   article_open_questions, DATA_GAPS, vertical audit, datum semantics …
├── ATL13 → EGG2015 → EVRS per beam-pass         ├── outputs/planning/  00–14 rebuild/design docs; recompute DAG; registries
├── EPSG:9902 grid + official_datum.py           ├── audit_runs/        3 runs (09-16 ×2, 09-18): claim_evidence_matrix, inventories
├── gauge yearbooks; station correctors          ├── data/historical/   digitised level/area/volume, longitudinal WSE, reaches
└── outputs/tables (by_station, by_radius, …)    └── data/processed/    bathymetry (soundings EVRF2019, contours), gauges, emodnet, water_masks_s1
F:\data_kakhovka_dem_swot (BULK)  s2_zone_fetch 390 G · data_swot 93 G · s1_zone_cache 82 G · s1_variants 71 G · zone_spectral 54 G · swot_ua 35 G (LakeSP UA clip) · spectral_indices 30 G · dynamic_world 10 G · terrain 1.7 G · fabdem 0.5 G · hist26/gate7c fields
```

## 2. Classification of material

| class | items (representative, not exhaustive) |
|---|---|
| **RAW DATA** | ATL13 v7 granules (icesat repo `data/raw`); SWOT PIXC Nova Kakhovka 3.2 G, RiverSP, LakeSP UA-clip (F:/swot_ua, 402 G fetched 09-16); Sentinel-2 L2A per zone (F:/s2_zone_fetch); S1 RTC (F:/s1_zone_cache); gauge yearbooks (icesat repo); ГМЦ ЧАМ 2023 sea/estuary yearbooks (knoweledg_graf `insitu_2023`, PDFs sha `05ffef84…`, `bf74c22a…`); monograph photographs (8 pages); S-57 chart soundings; `ua_2019z.asc`; `egg_2015.tif`; SWORD EU v16; FABDEM; EMODnet DTM 2024; Dynamic World; DniproHES releases (to 2023-12-31) |
| **INTERMEDIATE DATA** | per-beam-pass EVRS levels (icesat repo `atl13_pass_levels*.csv`); `FigD_kakhovka_profile_points.csv`; `kakhovka_longitudinal_profiles.csv`; `kakhovka_soundings_evrf2019.parquet` (H_ref 14.0, written 2026-09-09 19:48); hist14 bed surface; zone masks/composites (`outputs/rasters/zone*`); p25 index stacks |
| **FINAL SCIENTIFIC OUTPUT** | `kakhovka_transition_statistics.csv`, `kakhovka_perdate_slopes_robust.csv`, `kakhovka_pre_post_slope_summary.csv`, `FigG_span_sensitivity.csv`, `reservoir_to_river_statistics.csv`, `allwater_vs_channel_slopes.csv`, `pre_post_fragmentation_statistics.csv`, `residual_water_offset_summary.csv`, `fragmentation_metrics_by_date.csv`, `gauge_vertical_reference_summary.csv`, `part4_colocated_swot_icesat.csv`, `validation_summary_table.csv`, `historical_reference_sensitivity.csv`, `hist14_interpolator_cv.csv`, `hist17_grid_resolution.csv`, `historical_exposure_area_validation.csv`, `hist6_reach_slopes.csv`, `hist7_slope_geometry.csv`, `hist12_bed_morphology_tests.csv`, `egg2015_to_evrf2019_by_station.csv` |
| **QA / AUDIT OUTPUT** | `outputs/tables/manuscript_evidence_matrix.csv` (35 claims V/M/X, 2026-09-16); `audit_runs/*/claim_evidence_matrix.csv` (C-01…C-16); `audit_runs/20260916T093000Z/kriging/independent_check.py`; `ms4_qc.py` list; `historical_datum_semantics.md`; `swot_vertical_reference_audit.md`; `hydraulic_slope_vs_Q_analysis.md`; `Q1-Q8_post_q_recovery.md`; `qa1_field_provenance.csv`; knoweledg_graf `ACCEPTANCE_GATE.md`, `RUN_MANIFEST.md`, `RETRIEVAL_MANIFEST.md` |
| **MANUSCRIPT EVIDENCE** | manuscript_evidence_matrix.csv (each V/M/X row names table + figure + n + value + uncertainty + limitation) — the single best existing artefact; `SCIENTIFIC_FINDINGS.md` (2026-09-07) |
| **FIGURE INPUT** | `outputs/figure_data/*.csv` (Fig03…Fig17, FigA–FigG, P19_*) |
| **TABLE INPUT** | as FINAL SCIENTIFIC OUTPUT; `ms1_evidence_matrix.py` is the existing table generator |
| **MAP INPUT** | reservoir polygon `Kakhovka_SA_2.geojson`; `config/spatial_domains.yaml` (authoritative); SWORD; gauges.yaml; `outputs/maps/zones_openstreetmap.html`; atlas rasters per zone; historical reaches CSV |
| **LITERATURE / RAG** | knoweledg_graf: `theses.yaml` (24), GAP/LITERATURE matrices (EXPLORATORY), `SLICE_DENOMINATORS.csv` (S1–S4), `SLICE_SCREENING.csv` (1971 rows), `SCREENING_SHEET_reviewed.csv` (57: 20 usable/27 marginal/10 irrelevant), `GEODESY_B_CALIBRATION_SUBSET.csv` (20), `CONTROL_WORKSHEET.csv` (35: 15 needs reading, 20 to find; 28 development / 7 holdout), `technical_sources.yaml`, `v2/literature_claims.yaml` (9 L-claims), `v2/scientific_claim_status.yaml` (**all 29 UNKNOWN — audit never imported**), `harvest_report.md` (10 498 discovered, 88 selected) |
| **ARCHIVED / SUPERSEDED** | `outputs/tables/legacy_p20/*`; `DRAFT_kakhovka_fragmentation_article.md` + `SLIDES_*` (pre-A4/B-2b fragmentation, C-02/C-03); figures `V7_atl08_lakebed.png`, `M1_lakebed_elevation.png` (16.00 m datum, 2.00 m too high); `Fig01–Fig17`, `M1–M5`, `SFig11` (predate 14.00 m correction); `swot_icesat_hydrological_adjustment.csv` (superseded by part4); p19 pooled CV accuracies (C-04); `.chromadb_backup_20260520`; emodnet 31.50 °E pull on F: |
| **EXPERIMENTAL** | hist24 three-contour soft kriging (err_var sign bug C-07); p23 LakeSP cal/val transition (C-15); p28 mask-constrained zone DEMs (C-16 contradicted); p31–p38 S1 classifier pilots (ZONE_4); Manning/HEC-RAS branch (`/mnt/d/ras/DniproGES1D`, MODEL_COMPARISON_ONLY); hist25–hist28 contour gates |
| **UNKNOWN** | EGG2015 1′ raster upstream source; historical Baltic realisation (BS-42 vs BS-77); S-57 survey epoch; whether `kakhovka_transition_statistics.csv` was regenerated after 2026-09-07 (values identical in SCIENTIFIC_FINDINGS 09-07 and matrix 09-16, so consistent either way); mtime of `kakhovka_transition_statistics.csv` not checked |

## 3. Which pipeline produced which output

| output | producer (script → table) | consumed by |
|---|---|---|
| per-beam-pass EVRS WSE | icesat repo `build_pass_levels.py` (ATL13 `ht_water_surf` − ζ_EGG2015) | SWOT-DNIPRO `CFG.KAKHOVKA_ATL13` |
| station correctors c(x,y) | icesat repo `build_corrector_surface.py`, `datum_comparison.py` → `egg2015_to_evrf2019_by_station.csv` | V5.1, F2 |
| chainage + per-date slope | `make_longitudinal.py` (60-bin principal-axis centreline of the reservoir polygon, dam = 0; per-date OLS) → `FigD_*`, `kakhovka_longitudinal_profiles.csv`, `kakhovka_pre_post_slope_summary.csv` | `make_transition_stats.py` |
| robust transition statistics | `make_transition_stats.py` (Theil-Sen per date; bootstrap; permutation 20 000; sign test; min-span 20 km; span sensitivity) → `kakhovka_transition_statistics.csv`, `kakhovka_perdate_slopes_robust.csv`, `FigG_*` | M1.1–M1.3, F4, T3 |
| channel-restricted control | `phase19_profiles.py` → `reservoir_to_river_statistics.csv`, `allwater_vs_channel_slopes.csv`, `residual_water_offset_summary.csv` | M1.4, M3.2 |
| heterogeneity F4; fragmentation | `phase20_fragmentation_report.py`, phase20 water-object chain → `pre_post_fragmentation_statistics.csv`, `fragmentation_metrics_by_date.csv`, `phase20_coverage_sensitivity.csv` | M2.1, M3.1, C-02/C-03 |
| gauge frame | `official_datum.py` + `vertical.py` → `gauge_vertical_reference_summary.csv`, `gauge_levels_evrf2019.parquet` | V1.x |
| SWOT chain checks | part4/part7 scripts → `validation_summary_table.csv`, `part4_colocated_swot_icesat.csv` | V2.1–V5.1 |
| historical datum | hist2 (capacity-curve closure), qa5, `historical_reference_sensitivity.csv`; hist3 → EVRF2019 | V6.x |
| historical free surface | hist9 digitisation → `historical_fig16_profiles.csv`; hist6 → `hist6_reach_slopes.csv`; hist7 → artefact | V7.x |
| bed DEM | hist14 (OK/IDW/LINEAR/RBF, blocked CV) → `hist14_interpolator_cv.csv`, surface; hist18 error decomposition; hist15/16/17 → exposure and grid convergence (**rerun 2026-09-13 on the registry domain, commit b3255e4**) | V10.x, V11.1 |
| slope vs Q | `h_hydraulic_slope_vs_q.py` → `slope_Q_regression_results.csv` (PRE+DRAWDOWN only) | C-14 |
| manuscript evidence matrix | `ms1_evidence_matrix.py` → `manuscript_evidence_matrix.csv`; `ms4_qc.py` cross-checks draft numbers | this plan |

## 4. Missing outputs

1. **No machine-generated manuscript tables** — `ms1_evidence_matrix.py` produces the evidence matrix, not the Tables 1–4 of the paper.
2. **F2 panel (b)** (EPSG:9902 offset vs longitude) does not exist.
3. **Datum-propagated uncertainty band** on the exposure fraction (±1.14 pp from the 0.40 m reference-level spread) — required by audit C-12/F-11, not computed.
4. **Post-2023 DniproHES discharge** — absent locally and not retrieved (Q1–Q8 report); slope–Q interaction not fittable.
5. **Hourly wind/pressure for 2023-04-05** — ERA5 fetch coded, licence not accepted.
6. **Positive-control retrieval run** (`POSITIVE_CONTROL.csv`, `CALIBRATION_SHEET.csv`) — never run; 0/24 controls verified.
7. **Fig. 16 curve 4** not digitised.
8. **Historical Baltic realisation** (BS-42 vs BS-77) — unresolved.
9. A single **"which pre-breach sample"** table (three windows in use).

## 5. Duplicate / superseded outputs

- `outputs/figures` V-namespace collision: V2–V8 each mean two different figures (validation series 09-09 vs historical series 09-10). Plan uses F-ids and names files explicitly; rename to `VAL*`/`HIS*` before submission.
- Exposure fraction has three generations: 12.38 % (P20-era, draft v1), 12.8 % (geomorphological_validation_final.md), **12.77 %** (hist17 2026-09-13 = matrix). Only the last is current.
- Pre-breach 2023-06-05 water area: 2 033 km² (C-01, pre-B-2b) vs ~2 129 km² (matrix M3.1, footprint 2 299 km²). Cite one table row.
- `swot_icesat_hydrological_adjustment.csv` (09-07) superseded by `part4_colocated_swot_icesat.csv`.
- `legacy_p20/*` tables and `DRAFT_kakhovka_fragmentation_article.md` superseded by phase20 A4/B-2b re-emit.
- Two hist12 exposure tests (point-count V9.6 vs area-weighted V10.1): only V10.1 is valid.

## 6. Outputs that are scientifically unsafe (do not use as-is)

| output | why |
|---|---|
| any figure with bed elevations dated before 2026-09-09 19:48 (`V7_atl08_lakebed`, `M1_lakebed_elevation`) | 16.00 m datum, 2.00 m too high |
| `V12/V13/V15_*.png` (09-10) | predate the 09-13 registry-domain rerun of hist15/17 (12.38 → 12.77 %) — SUPERSEDED until regenerated |
| p19 pooled CV accuracies 1.84/2.93 m | 62–77 % shoreline pseudo-points in the validation set (C-04); soundings-only 3.19/3.99 m with +0.8/+1.2 m bias |
| any hist24 soft-kriged product | err_var sign inverted (C-07); harmless at current parameters (C-08) but the code is wrong |
| F2 = 0.00 → 0.31 fragmentation contrast; water-body counts | C-02 UNSUPPORTED (n_pre = 3, one at full coverage), C-03 CONTRADICTED (counts ∝ observed fraction) |
| "bimodal bed", "point-count exposure" | retracted (V9.4, V9.6) |
| "0.1 pp agreement" / "0.57 pp" exposure precision | inside ±1.14 pp datum band; numbers stale |
| "no relationship between slope and discharge" | NOT_TESTABLE (C-14) |
| "independent validation" for PIXC vs RiverSP | same SWOT observation (V2.1) |
| SWOT LakeSP transition statistics (p23) | exploratory, C-15 UNRESOLVED |
| every literature-gap / novelty sentence | RETRIEVAL_UNVALIDATED |

## 7. Outputs ready for manuscript use (with the stated wording)

M1.1–M1.3 (footprint slope transition), M2.1 (F4 heterogeneity), M3.2 (residual-body offset), V1.1–V1.3, G1 (grid identity), C-11 (tide term), V2.1 (as internal check), V3.1 (n = 3), V4.1, V4.2 (as negative result), V5.1, V6.1–V6.2 (with the "own inference" wording), V7.1–V7.3, V8.1, V10.2–V10.4, V11.1 (with 12.77/12.79 numbers), X2.1 (limitation). M1.4 and M3.1 are publishable only as limitation / descriptive evidence. V10.1 is ready once the number and band are updated.
