# Paper 5 passport — hydraulic modelling

*Phase 0 deliverable, 2026-09-21. Numbers are quoted from OWN_EVIDENCE.csv; each traces to a hashed snapshot table.*

## 1. The question
How do the reconstructed terrain and the changed surface roughness alter stage, velocity and inundation, before the breach and in the current state?

## 2. Own result
**None. No model has been run.** All five theses (P5-AT01…P5-AT05) stand at `NOT_YET_SUPPORTED`, and this passport exists to record that honestly rather than to imply otherwise.

## 3. What must exist before this is a paper
1. ~~The seam defect of paper 2 repaired~~ — **done 21 September**: the gap-fill class is gone and the feathered class agrees with FABDEM to +0.02 m (B8.2). The terrain is no longer the obstacle.
2. HEC-RAS pre-breach and current-state runs on the repaired terrain.
3. `manning_n_scenarios.csv` — the script exists and has never been run, so the n_low / n_base / n_high sensitivity has no output.
4. Post-breach discharge, which has not been recovered; without it the current-state runs are **scenario** runs, not calibrated simulations.

## 4. Borrowed foundation
Paper 2 supplies the terrain; paper 4 the roughness field; paper 1 the observed post-breach slope.

## 5. The calibration rule, fixed in advance
**Manning n is never calibrated on the satellite-derived slope.** Calibration uses the legacy Table 20 backwater profiles and the gauge records; the observed post-breach slope (+3.31 cm/km) and the ICESat-2 / SWOT profiles are held back as an **independent validation target**. Tuning roughness to the profiles and then reporting that the profiles are reproduced would test nothing.

## 6. Key figure
Modelled against observed longitudinal water surface, current state, with the observed slope as a target the model either meets or does not.

## 7. Verdict
**Exploratory direction, not a paper.** It should be described as planned work and not drafted beyond a skeleton. With the terrain now repaired, the binding constraint is items 2-4 of §3: the model runs themselves, the roughness scenarios, and the missing post-breach discharge.
