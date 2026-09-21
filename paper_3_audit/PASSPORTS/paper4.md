# Paper 4 passport — revegetation and hydraulic roughness

*Phase 0 deliverable, 2026-09-21. Numbers are quoted from OWN_EVIDENCE.csv; each traces to a hashed snapshot table.*

## 1. The question
How does the structure of the vegetation that colonised the exposed bed justify spatial and seasonal estimates of hydraulic roughness?

## 2. Novelty boundary — stated first, because it is tight
Kuzemko et al. (2025) have **already published** the revegetation of this bed from satellite data combined with field survey, and Didukh et al. (2024) the habitat classification. This paper is therefore **not** the first mapping of the revegetation and must not read as though it were. The contribution is the step they do not take: from vegetation structure to a defensible roughness field.

## 3. Own result — kept in three separated layers
The passport requirement is that these never blur into one number.

| Layer | What it is | Example |
|---|---|---|
| **Observed** | measured from imagery and lidar | vegetated share 83.8 % of the former water surface by 2025; woody probability ≥0.3 rising 0.03 % → 25.2 %; GEDI canopy in dense young woody 3.17 → 4.21 m on a season-matched window while bare and water classes stay on the 2.7 m sensor floor |
| **Modelled** | class assignment and its areas | roughness-class areas per state; channel wetted width 690 m median in 2025 |
| **Assumed** | literature Manning priors per class | area-weighted base n 0.0446 → 0.0888 over the mosaic |

The roughness figure is an **assumption propagated through an observation**, never a measurement. Wording fixed for every occurrence: "hydraulically interpreted roughness derived from mapped surface classes", not "calibrated Manning n".

## 4. Borrowed foundation
Paper 1: the water-mask rule. Paper 2: nothing — the roughness layer is a surface property, independent of the bed.

## 5. Key figure and independent check
Canopy height by roughness class, two seasons on a matched window. The independent check is cross-sensor: GEDI and ICESat-2 agree on the **direction** of woody expansion and not on its level (76 % vs 25 % woody cells, Spearman 0.26), which is why height supports the class assignment and never calibrates the coefficient.

## 6. Open limitations
The surface classes carry **no accuracy assessment**: the 320-point stratified sample is unlabelled, so no area-error statement is admissible. Canopy change is not identifiable from ATL08 (n_pairs = 1). GEDI cannot resolve below its 2.7 m noise floor, so bare and water classes are floor values. Dynamic World composites for 2025–26 are missing. No hydraulic calibration exists.

## 7. Verdict
**Sufficient evidence for the observed and modelled layers.** The roughness layer is publishable only as an explicitly literature-based prior with its spread carried; the unlabelled validation sample is the one item that most limits what may be claimed.
