# ARTICLE_MASTER_PLAN — Paper 3

Kakhovka: reservoir-to-river hydraulic transition from harmonised satellite altimetry, gauges and historical hydrography. Plan built 2026-09-18 from the repository audit (`ARTICLE_REPOSITORY_AUDIT.md`), the claim registry (`ARTICLE_CLAIM_REGISTRY.csv`, 54 rows) and the data-evidence matrix (7 article theses AT1–AT7). Statuses come from the SWOT-DNIPRO audit, not from the v1 prose.

## 0. What the paper is actually about (after the audit)

The evidence supports a **narrower** statement than "the reservoir became a river":

> Within the former Kakhovka Reservoir footprint, the per-overpass longitudinal water-surface slope changed from indistinguishable-from-zero (+0.09 cm/km, 10/14 positive) to persistently positive (+3.3 cm/km, 14/14 positive, difference +3.2 cm/km, 95 % CI [+2.0, +5.1]), the change is present already during the 2023 drawdown and persists through 2025, and within-overpass heterogeneity rose from 0.12 to 0.40 m. This is measurable only because seven gauges, ICESat-2, SWOT and a 1950s–70s hydrographic survey were brought into one vertical frame (EVRF2019 via EGG2015 + EPSG:9902, permanent tide handled explicitly) and because the historical documentation independently bounds both the pre-breach surface and the reconstructed bed.

What it is **not**: a channel-restricted gradient (the channel control's CI includes zero), a discharge-dependent slope law (no post-2023 discharge exists), an inferential fragmentation result (one pre-breach full-coverage date), or a novelty claim (retrieval not validated).

Two facts govern the structure: (i) the headline result does not depend on SWOT at all — SWOT enters only downstream at Kherson as a validation partner; (ii) the historical block is a validation resource, not a second results chapter.

## 1. Article theses (AT) and their evidence

| AT | statement | status | claims | numbers (source table) | visuals |
|---|---|---|---|---|---|
| **AT1** Hydraulic regime transition | Per-overpass footprint slope: near-zero → persistently positive; present in drawdown; persists 2024–2025 | SUPPORTED_WITH_LIMITATION | M1.1 M1.2 M1.3 (M1.4 limitation) V7.2 | TS +0.0905 → +3.3137 cm/km; Δ +3.2232 [+1.9944, +5.0707]; perm p 1.0e-4; MW p 3.9e-5; sign 14/14 p 1.2e-4; drawdown +3.2449 (n 5); OLS Δ +2.744 [+1.454, +4.323]; span-robust 10/20/30/40 km — `kakhovka_transition_statistics.csv`, `FigG_span_sensitivity.csv` | F4, F3P, T3 |
| **AT2** Geometry beats area | Heterogeneity F4 0.117 → 0.397 m; residual bodies −0.60 m below stem; extent/fragmentation descriptive only | M2.1 SUPPORTED; M3.2 SWL; M3.1 descriptive; C-02/C-03 withdrawn | M2.1 M3.1 M3.2 | Δ +0.2798 m [+0.1380, +0.3669], p 5e-5 (192/23 dates) — `pre_post_fragmentation_statistics.csv`; −0.5951 m NMAD 0.459 (n 678) — `residual_water_offset_summary.csv`; 2 → 161–634 bodies, largest 99.995 % → 31.5–80.6 % (1 pre / 4 post full-coverage dates) — `fragmentation_metrics_by_date.csv` | F6, F7, T3 |
| **AT3** SWOT + ICESat-2 complementary | cm agreement only under close collocation; SWOT covers Kherson only | V3.1 SWL (n 3); V2.1 internal; V4.2 NOT_TESTABLE | V2.1 V3.1 V4.1 V4.2 | +0.0036 m at Δt −2.3 h; −0.181 m at +14.2 h (unexplained, hydrology wrong sign); PIXC–RiverSP −0.0010 m (n 1023); gauge +0.0264 m NMAD 0.041 (n 9) — `part4_colocated_swot_icesat.csv`, `validation_summary_table.csv` | F3, T2 |
| **AT4** Harmonisation prerequisite | One grid (EPSG:9902 = `ua_2019z.asc`), spatially varying 0.17–0.22 m; EGG2015; tide term +0.037 m; station correctors do not transfer | SUPPORTED except **G2 UNKNOWN** | V1.1–V1.3 G1 G2 C-11 V5.1 V6.2 | Δ9902 0.1715–0.2157 m (8 stations, spread 44 mm, grid acc. 0.068 m); c −0.128…−0.217 m; free2mean +0.0374 m; Kherson +0.005 vs reservoir −0.130 m — `gauge_vertical_reference_summary.csv`, `egg2015_to_evrf2019_by_station.csv` | F2, T2 |
| **AT5** Historical constraint | 1970 measured free-surface bounds pre-breach slopes; sounding datum 14.00 m by 3 routes (0.40 m spread); bed CV 2.76 m; exposure 12.95 vs 12.77 % | SWL; **draft numbers stale**; datum band not propagated | V6.1 V6.2 V7.1 V7.2 V10.1–V10.5 V11.1 X1.1 X2.1 | +0.098 m [−0.076, +0.303] at 14.00 vs −1.90 at 16.00; Fig 16 NMAD 0.045 m; OK blocked-CV RMSE 2.761 / NMAD 1.322 / bias −0.032 (n 7509); exposure −0.18 pp (interpolators 12.61–13.29 %) — `hist17_grid_resolution.csv` 2026-09-13 | F5, F3P overlay, T3 |
| **AT6** Slope–discharge | NOT TESTABLE post-breach (n_POST_with_Q = 0) | NOT_TESTABLE | C-14 | Q coef 1.5e-4 p 0.26 on PRE+DRAWDOWN only — `slope_Q_regression_results.csv` | none (text) |
| **AT7** Positioning | Kakhovka novelty vs physical precedent vs method validity | UNKNOWN — gate NOT READY | LK1.1–LD1.4 | S1: 0/226 screened report WSE slope; S2: 1/361 name permanent tide — `SLICE_DENOMINATORS.csv` (unvalidated) | T4 |

## 2. Section-by-section plan

Structure follows the argument, not v1. Sections marked ★ carry the paper; sections marked ◦ are validation or limitation.

### 1 Introduction
- **1.1 Kakhovka as an unprecedented large-reservoir drawdown** — purpose: place the event; claim: none of ours; evidence: monograph design values (16.00 m NPG, 2 155 km², 18.19 km³ — Table 19/21, RESOLVED in `historical_datum_semantics.md`); literature: K-layer (blocked); caveat: no priority asserted.
- **1.2 Why water area is not enough** — claim: AT2 motivation; literature: LT1.2, T04/T19 (unvalidated); caveat: keep as motivation, not finding.
- **1.3 Longitudinal geometry from spatial altimetry** — claim: method validity; literature: LD1.1 (T05–T07); caveat: cite ATBDs/PDDs from `technical_sources.yaml` (URLs still `url_needed`).
- **1.4 Vertical reference as the enabling step** — claim: AT4 motivation; literature: LD1.2/LD1.3 (T08–T11).
- **1.5 Objectives** — three questions: did slope regime change (AT1/AT2); can SWOT+ICESat-2 be read together (AT3/AT4); does history constrain both ends (AT5). State up front what is not testable (AT6).

### 2 Study area and observation geometry ★map
- Purpose: fix space, chainage convention and sensor coverage. Major claim: SWOT never covered the pool (coverage fact, `swot_dataset_inventory.csv`). Numbers: chainage 0 at dam, historical reaches 1+2 / 3 / 4 (reach 5 outside domain, X1.1). Visual: **F1** (map, 3 panels). Caveat: historical vs modern chainage ratio ~1.36 at two ties (X1.1) — say it here once.

### 3 Data ◦ (Table T1, machine-generated)
- 3.1 Gauges: 6 reservoir (zero 12.000 m BS-77, series end 2021-12-31) + Kherson 80805 (zero −5.000 m; daily 2019–2025); V1.3. Note the yearbook tailwater post 80802 ≠ 80977.
- 3.2 ICESat-2 ATL13 v7 (ITRF2020, tide-free) — three samples must be stated explicitly: 14+5+14 slope dates (min span 20 km, 2019-07-11…2025-11-03), 192/23 F4 dates, S7 exposed-bed tracks (28). Unit: one overpass; 6 beams → 6 points per profile.
- 3.3 SWOT PIXC/RiverSP (Kherson, 2023-04…05; cycles 482/511/521); LakeSP UA clip exists (402 GB) but is **not used** (C-15 exploratory).
- 3.4 Sentinel-2 L2A coverage-aware masks; state that only 2023-06-05 has near-complete pre-breach coverage.
- 3.5 Historical: monograph (8 photographed pages: Tables 19–21, Figs 13–16), S-57 soundings (7 514; epoch unknown, X2.1), digitised 1970 curve.
- 3.6 Geodetic: `ua_2019z.asc` (EPSG:9902/CRS-EU/Stopkhai — one product, G1), `egg_2015.tif` (**G2 blocker**), SWORD v16.

### 4 Methods
- **4.1 Vertical harmonisation** ★ — the three chains from `vertical.py` docstring; EPSG:9902 only on BS-77 normal heights; PIXC `geoid` never subtracted; `free2mean` +0.037 m; the 1–4 cm residual permanent-tide term as its own budget line. Visual **F2**, Table **T2** with a *validation-type* column (internal arithmetic vs cross-sensor vs in-situ).
- 4.2 Chainage — reservoir-axis centreline (60-bin principal axis, EPSG:32636), **not SWORD** for the reservoir profiles (SWORD is used for the Kherson gradient and residual-body stem). The v1 abstract's "SWORD-based chainage" must be corrected or qualified.
- 4.3 WSE extraction and QC; 4.4 per-overpass slope (Theil-Sen primary, OLS sensitivity, min span 20 km, span sensitivity 10/20/30/40); 4.5 heterogeneity = within-overpass p95–p05 range (F4), not SD/NMAD; 4.6 historical digitisation and datum tests; 4.7 bed reconstruction (hist14, soundings only, 250 m canonical, blocked 1 km CV; shoreline boundary 17.08 m vs exposure denominator 16.18 m — different quantities); 4.8 planform metrics with coverage gate (`inferential` rule: both periods need ≥3 dates at HIGH coverage); 4.9 statistical inference (unit = overpass; bootstrap; permutation; sign test; Cliff's δ for the channel control).

### 5 Validation results ◦
- 5.1 gauge frame (V1.1–V1.3, G1); 5.2 PIXC chain — *internal* (V2.1); 5.3 SWOT vs ICESat-2 (V3.1, n 3); 5.4 Kherson anchor (V4.1); 5.5 the 2023-04-05 negative result (V4.2); 5.6 station correctors do not transfer (V5.1); 5.7 historical datum (V6.1/V6.2 with the permitted phrasing: "the reduction level is our inference, corroborated by three routes"); 5.8 1970 free surface vs pre-breach ICESat-2 (V7.1–V7.3); 5.9 bed reconstruction: CV 2.76 m and what it measures (V10.3/V10.4), boundary sensitivity (V10.2), exposure 12.95 vs 12.77 % with a datum-propagated band (V10.1 — **numbers to update**), convergence (V11.1). Visual **F3**, **F5**.

### 6 Hydraulic transition results ★
- 6.1 pre-breach: near-level pool inside the historical envelope (V7.2; F3P top row with 1970 curve).
- 6.2 drawdown: +3.24 cm/km already (M1.2).
- 6.3 post-breach: +3.31 cm/km, 14/14, Δ +3.22 [+1.99, +5.07] (M1.1, M1.3) — **F4 a–c**, T3.
- 6.4 the channel-restricted control: +1.11 [−0.09, +2.19], 6 dates — reported as a limitation, not hidden (M1.4) — F4 d.
- 6.5 heterogeneity F4 (M2.1) — F6.
- 6.6 planform: one full-coverage pre-breach date vs post; descriptive, no p-value, no counts (M3.1) — F7.
- 6.7 residual bodies below the stem (M3.2) — F7 d.
- 6.8 persistence 2024–2025 (from `kakhovka_perdate_slopes_robust.csv`: every POST date positive except 2025-05-06 at +0.08).

### 7 Discussion
- 7.1 what changed hydraulically (AT1/AT2; T-layer precedent LT1.1/LT1.2 once validated); 7.2 why area misses it (AT2); 7.3 comparison with other reservoir-to-river transitions (T-layer; **empty until gate passes**); 7.4 what satellites can and cannot resolve — local ~25 km slopes, 6 points, temporal collocation, LakeSP inappropriate for a fragmenting body (T22; C-15 not shown); 7.5 the vertical frame — G1/G2, transfer failure V5.1, the 1–4 cm tide residual; 7.6 what history contributes (AT5) with the 0.40 m spread stated as the uncertainty; 7.7 Kakhovka literature (K-layer; **blocked**); 7.8 limitations: M1.4, C-14 (NOT TESTABLE — never "no relationship"), X2.1, BS-42/BS-77, EGG2015 citation, n = 3 collocations, wind data absent.

### 8 Conclusions — one paragraph per AT, numbers only from T3/T2.

## 3. Old v1 claims that must be weakened or removed

| v1 wording | action | reason |
|---|---|---|
| exposure "12.38 % vs 12.95 %, −0.57 pp", "34 959 cells", "12.32 % at 50 m" | **replace** with 12.77 / −0.18 pp / 37 419 cells / 12.79 % | hist17 rerun 2026-09-13 (registry domain); `ms4_qc.py` flags this |
| "agrees to 0.57 percentage points" as precision | requote "consistent within the ±1.1 pp datum band" | C-12: datum 1σ alone ±1.14 pp |
| "SWORD-based chainage" (abstract) | qualify: reservoir-axis centreline for the pool, SWORD downstream | `make_longitudinal.py` |
| any "channel" gradient wording for M1.1 | "water surface within the former reservoir footprint" | M1.4 CI includes zero |
| PIXC vs RiverSP as validation | "internal product-chain check" | same observation |
| "independent" for anything sharing an observation or method | reserve for gauge (V4.1), historical (V7.2, V10.1), S7 vs soundings (V9.2) | audit rule 5 |
| fragmentation F2 0 → 0.31, body counts, any fragmentation p-value | remove; descriptive only | C-02, C-03 |
| "bimodal", "antimode", point-count exposure | remove (already retracted V9.4/V9.6) | grep of v1 is clean — keep it so |
| slope–discharge "no effect" | NOT TESTABLE | C-14 |
| all §7.2 novelty and §7.8 comparison sentences | keep the [PENDING OPEN41–43] markers; nothing citable | RETRIEVAL_UNVALIDATED |
| 2 033 km² (C-01) vs 2 129 km² (M3.1) | choose the post-B-2b row and cite it | footprint revision |

## 4. Writing order (recommended)

1. **T1–T3 as generated tables** (extend `ms1_evidence_matrix.py`) — every number in the text will be pulled from them.
2. **Methods 4.1–4.4** + F2/F1 — they define vocabulary (unit = overpass, footprint vs channel, three pre-breach samples).
3. **Results 6.1–6.8** + F4/F3P/F6/F7 — the paper's spine.
4. **Validation 5.x** + F3/F5 — after regenerating V12/V13/V15 and updating V10.1 numbers.
5. **Discussion 7.4–7.6, 7.8** (limitations) — writable now.
6. **Introduction** — last among writable parts, once T-/D-layer wording is settled.
7. **7.1–7.3, 7.7, T4, abstract novelty sentence** — only after the acceptance gate passes.

## 5. Success-chain check (example)

"After the breach every one of 14 overpasses shows water rising upstream at a median +3.31 cm/km" → M1.1 → T3 row *slope (Theil-Sen)* / F4 b → `kakhovka_transition_statistics.csv` (estimator = Theil-Sen, median_post_cm_km 3.3137, post_positive 14/14) → `make_transition_stats.py` → `FigD_kakhovka_profile_points.csv` → `make_longitudinal.py` → `CFG.KAKHOVKA_ATL13` (icesat repo beam-pass EVRS) → ATL13 v7 `ht_water_surf` − ζ_EGG2015 + free2mean.
