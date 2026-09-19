# ARTICLE_OPEN_ITEMS — Paper 3 (prioritised, 2026-09-18)

P0 blocks the manuscript · P1 blocks a claim/figure · P2 strengthens · P3 optional. Each item: why, which claim, which visual, exact next action. Repos: `SW` = SWOT-DNIPRO (Ubuntu-24.04), `IC` = icesat2-atl13-kakhovka, `KG` = knoweledg_graf.

## P0 — blocks manuscript

| id | item | why | claims | visuals | exact next action |
|---|---|---|---|---|---|
| P0-1 | **Literature layer unvalidated** — 0/24 positive controls verified; POSITIVE_CONTROL.csv and CALIBRATION_SHEET.csv never produced | every §7.8/§7.2/abstract novelty sentence is RETRIEVAL_UNVALIDATED; rule 4/13 | LK1.1–LD1.4, AT7 | T4 | KG: read the 15 "needs reading" rows of `CONTROL_WORKSHEET.csv` (start: `10.1029/2025gl120832`, `10.1029/2025gl119771`), locate the 20 "to find" (dam-removal canon; hand-add PDFs to `data/literature/pdf_missing/`); add holdout + hard controls for T09 T10 T14 T15 T16 T17 T22; commit; `python -m src.paper_3.cli --step retrieve`; `python -m src.paper_3.calibrate sample`; `--step publish` |
| P0-2 | **Exposure numbers in draft are stale** (12.38 % / −0.57 pp / 34 959 cells / 12.32 % at 50 m) vs hist17 2026-09-13 (12.77 / −0.18 / 37 419 / 12.79) | rule 2; `ms4_qc.py` already flags it | V10.1, V11.1, AT5 | F5 b/d, T3 | SW: regenerate `V12_bed_surface_validation`, `V13_exposure_area_validation`, `V15_grid_resolution_convergence` from the 09-13 tables (hist14/15/17 figure scripts); replace every occurrence in §5.10, §5.11, §5.12, §8 of the draft; run `ms4_qc.py` until clean |
| P0-3 | **Exposure agreement quoted at artificial precision** — datum 1σ alone ±1.14 pp, 3.3× the published ±0.35 pp band (audit C-12/F-11); premise "soundings reduced to 14.00 m" is our inference (C-13) | rule 16 | V10.1, V6.1 | F5 d, T3 | SW: propagate the 0.40 m reference-level spread (13.71/14.00/14.11) through hist15 (re-run exposure at H_ref ±0.20 m) → band column in `historical_exposure_area_validation.csv`; requote as "12.77 % (band 11.6–13.9 %) vs 12.95 %" |
| P0-4 | **EGG2015 1′ raster has no citable source** (public ISG release is 10′×15′) | rule 9; a Methods section cannot cite an unidentified grid | G2, AT4 | F2 a, T2 | IC: request the 1′ EGG2015 grid with licence from BKG/LUH (Denker); **or** re-run `build_pass_levels.py` on the public 10′×15′ grid and report the difference in H_EVRS (expected cm-level over the reservoir) — then cite the public grid |
| P0-5 | **Own-data audit statuses never imported into KG** — `v2/scientific_claim_status.yaml` has all 29 IDs UNKNOWN although `manuscript_evidence_matrix.csv` + C-01…C-16 exist | the KG claim map blocks on UNKNOWN by design | all V/M/X | — | KG: copy `SW/outputs/tables/manuscript_evidence_matrix.csv` and `audit_runs/20260918T175108Z/claim_evidence_matrix.csv` into `paper_3_audit/audit_run/`; run `scientific_status.import_audit`; reconcile with `ARTICLE_CLAIM_REGISTRY.csv` audit_status column |
| P0-6 | **Dirty working trees** — SW 269 paths (incl. the evidence matrix), KG 58, IC 18; the retrieval manifest says "working tree dirty" | no commit identifies the code that produced any table | all | all | commit each repo (SW first: tables + p38 report), re-freeze KG manifest, record hashes in the plan |

## P1 — blocks a specific claim or figure

| id | item | why | claims | visuals | exact next action |
|---|---|---|---|---|---|
| P1-1 | Channel-restricted control underpowered (6 post dates; CI [−0.086, +2.186] includes zero) | headline must stay "footprint", not "channel" | M1.4 → wording of M1.1 | F4 d | SW: relax the classified-channel point-count threshold in `phase19_profiles.py` and/or process the ATL13 cycles not yet in `CFG.KAKHOVKA_ATL13`; if CI still spans zero, publish that as the result |
| P1-2 | Three pre-breach windows in use (14 slope dates 2019–22; SWOT pilot 2023-04…06; 192 F4 dates) | reader will assume one sample | M1.1, M2.1, V3.1 | T1, S7 | SW: one generated table (`ms5`) listing analysis · window · n · unit |
| P1-3 | Fragmentation contrast not inferential; counts coverage-confounded (C-02 UNSUPPORTED, C-03 CONTRADICTED) | rule 7 | M3.1 | F7 b | SW: recompute F1/F2 with both periods restricted to `coverage_class==HIGH`; drop counts; re-emit `P19_Fig1` post-A4/B-2b; keep descriptive wording |
| P1-4 | F2 panel (b) (Δ9902 vs longitude) does not exist | AT4's "spatially varying" needs a picture | V1.2 | F2 b | SW/IC: plot `delta_epsg9902_m` vs lon from `gauge_vertical_reference_summary.csv` with ±0.068 band |
| P1-5 | Chainage wording: abstract says "SWORD-based chainage"; reservoir profiles use a reservoir-axis centreline (`make_longitudinal.py`) | rule 3 | M1.1 | F1 a, F3P | draft: correct §4.10 and abstract; state SWORD is used for Kherson gradient and residual-body stem only |
| P1-6 | Two pre-breach areas in circulation (2 033 km² C-01 vs ~2 129 km² M3.1) | one number, one table row | M3.1 | F7 a | SW: cite `fragmentation_metrics_by_date.csv` row 2023-06-05 as re-emitted after B-2b; retire 2 033 |
| P1-7 | Figures V12/V13/V15/V16 dated 09-10 predate the 09-13 rerun; V-namespace collision (V2–V8 ×2) | rule 16 of brief §16 | V10.x | F5, S3 | SW: regenerate; rename `VAL*`/`HIS*`; update `article_figure_plan.md` |
| P1-8 | Permanent-tide residual (1–4 cm, SWOT mean-tide crust vs zero-tide ζ) not a line in any uncertainty table | audit §3 of `swot_vertical_reference_audit.md` | C-11, V3.1 | T2 | SW: add row to `vertical_reference_audit.csv`; cite ATL13 ATBD check (already in `config.py`) |
| P1-9 | Technical sources all `status: url_needed` (PDD D-56411, ATL03/ATL13 ATBD, Denker 2015, EVRF2019 report, IAG Res 16, EPSG:9902, SWORD) | rule 11 | LD1.x, G1 | T2, refs | KG: fetch by hand, record sha256 in `technical_sources.yaml` (`url_verified`) |

## P2 — strengthens the paper

| id | item | why | claims | visuals | exact next action |
|---|---|---|---|---|---|
| P2-1 | Post-2023 DniproHES discharge absent → slope–Q interaction NOT TESTABLE | would turn AT6 from limitation into result | C-14 | new T3 row | obtain Ukrhydroenergo "Таблиця №2" daily pages for 2024–2025 (site navigation, no URL guessing) or UkrHMI request; then `h_hydraulic_slope_vs_q.py` with POST |
| P2-2 | 2023-04-05 anomaly: no wind/pressure record | V4.2 stays NOT_TESTABLE | V4.2 | F3 d | accept ERA5 licence on CDS; `python scripts/fetch_era5.py && python scripts/analyse_wind_setup.py` |
| P2-3 | Historical Baltic realisation (BS-42 vs BS-77) unstated | few-cm unaccounted step in V6.x | V6.1, V6.2 | T2 | find the monograph's datum statement or a regional BS-42→BS-77 relation; else bound the magnitude in §7.8 |
| P2-4 | Fig 16 curve 4 (Hп 16.16 m, Q 9 040 m³/s, 28–30 IV 1970) not digitised | second measured profile for F3P | V7.1 | F3P | curve tracing along the dash-dot path |
| P2-5 | SWOT LakeSP UA clip (402 GB) unused; p23 transition exploratory (C-15) | could add a SWOT view of the pool post-breach — but LakeSP is lake-averaged and physically inappropriate for a sloping channel (T22) | C-15 | none | only after: separate Obs/Unassigned, check prior-matched areas, restrict PRE to 4 weeks; likely stays out of Paper 3 |
| P2-6 | SWOT RiverSP node cross-check over the drained pool (targeted ~10–15 GB) | independent slope partner for AT1 post-breach | M1.1 | F4 | FULL_SYSTEM_STATUS phase 21 — not started |
| P2-7 | Sub-daily Kherson stage 2023 | quantifies the sub-daily term in every Kherson matchup | V4.1 | — | UkrHMI request (local search exhausted) |
| P2-8 | hist24 `ok_soft` err_var sign inverted (C-07) | no effect on Paper 3 DEM (hist14) but wrong code in repo | — | — | fix `hist24_three_contour_dem.py:134` sign; rerun only if hist24 products are ever cited |

## P3 — optional

| id | item | next action |
|---|---|---|
| P3-1 | Second-echelon historical material (Fig 177 dynamic volumes, Fig 178 1966 flood, Fig 167 gauge network) | digitise if §2.3 needs it |
| P3-2 | Manning/HEC-RAS roughness branch (audit 09-18 phases 7–8) | separate paper; MODEL_COMPARISON_ONLY |
| P3-3 | ZONE_2/3/4 bed DEMs (p19/p28) | downstream paper; C-04/C-16 make them unusable here anyway |
| P3-4 | DuckDB views / unified `all_matchups.parquet` | reproducibility convenience |
| P3-5 | Update memory note: Ubuntu-24.04 repos are reachable via `wsl.exe` interop (old note said unreachable) | done in this session's memory |

## Status summary
- Publication-ready as worded: 31 of 54 registry rows (SUPPORTED / SUPPORTED_WITH_LIMITATION).
- REVISE_UNCERTAINTY 3 (M1.4, V9.5, X1.1) · NOT_TESTABLE 3 (V4.2, C-14, X2.1) · REQUIRES_REANALYSIS 1 (C-15) · UNSUPPORTED 4 (V9.4, V9.6, C-02, C-04) · CONTRADICTED 3 (C-03, C-07, C-16) · **UNKNOWN 10 (G2 + 9 literature claims) — these block.**
