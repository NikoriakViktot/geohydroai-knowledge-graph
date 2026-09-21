# Paper 3 passport — water masks, supervised reconstruction, inundation

*Phase 0 deliverable. Written 2026-09-21 against the p51 run of 03:39 the same day, which supersedes the 09-19 tables.*

## 1. The question

Over a floodplain whose surface was freshly stripped by a dam breach, can multi-temporal optical indices reconstruct the footprint of a flood event that radar mapped only noisily — and does that reconstruction survive a move to ground the model has not seen?

## 2. What the task actually is

**Retrospective event-footprint reconstruction.** Not an instantaneous inundation map, and not a forecast. The predictors run to 2023-07-31 (event window) and into a trace window to 2023-11-30, while the positive label is defined on the 9/13/14 June peak. The model therefore answers "which cells carry the multi-temporal optical trace of the June flood", not "where was water on 13 June". Any text that implies the second would be wrong.

A forecast framing is unavailable and must not be claimed: post-event imagery is in the features by design.

## 3. Feature / label provenance — the leakage check

| | Source | Role |
|---|---|---|
| 65 predictors | Sentinel-2 indices (NDVI, NDWI, MNDWI, NDMI, BSI, AWEIsh, NDTI) at `pre2021`, `post*`, `trace*`, `delta` epochs | features only |
| labels | Sentinel-1 M3 water masks, three states (positive / negative / unlabelled) | labels only |
| `pre_water_frac` | Sentinel-2 pre-breach water frequency | label **filter**, applied identically to positives and negatives |
| HAND | terrain prior | builds the candidate domain; **not** a feature |

**Verdict: no leakage of the discriminative kind.** Predictors and labels come from different sensors, and the feature list (`p51_rf_importance.csv`, 65 rows) contains no water-fraction, HAND, S1 or label-derived variable. The one shared quantity, `pre_water_frac < 20 %`, restricts *both* classes equally, so it narrows the domain rather than separating the classes.

Residual dependency to state in the text: the candidate domain `below_dam_floodplain` is itself `observed flood ∪ HAND < 5 m`, so the HAND agreement is a **conditional physical-consistency check, not independent validation**. `--domain neutral` on ZONE_2 + ZONE_4 is the run that makes it independent.

## 4. Own result

Within-area, under nested spatial cross-validation (outer GroupKFold 5 × inner GroupKFold 3, 5 km blocks, block size justified by a 3.5 km variogram range):

- **AUC 0.9619, AP 0.9338**, precision 0.8025 at recall 0.9131, n = 836 401, prevalence 0.392.
- The threshold is chosen in the inner fold and applied to the outer fold, so the operating point is not scored on the sample that chose it.
- Grouped permutation importance on held-out folds: event epoch −0.207 AP, trace −0.142, pre-breach −0.120. Pre-breach predisposition alone carries real signal; post-event evidence adds more.

**These numbers replace AUC 0.9738 / AP 0.9276 from the 09-19 tables, which used a descriptive threshold read off the sample it scored. Those must not appear anywhere.**

## 5. The result that decides whether this is a paper

**Spatial transfer largely fails.** Leave-one-zone-out:

| Held out | AUC | AP | precision | recall |
|---|---|---|---|---|
| ZONE_4 floodway | 0.7586 | 0.6979 | 0.838 | **0.366** |
| ZONE_2 delta | 0.8750 | 0.8587 | 0.908 | **0.473** |

Precision holds; recall collapses to roughly a third or a half. The model, moved to unseen ground, finds less than half the flooded area. This is the honest headline and it must lead the abstract, not sit in the limitations.

## 6. Borrowed foundation

- Paper 1: the Sentinel-2 water-mask rule and coverage gate.
- Paper 2: nothing. HAND comes from FABDEM, not from the reconstructed bed.

## 7. The Lischenko comparison — currently not a result

`p50` compares our occurrence classes with Lischenko et al. 2025. Checked during planning, the comparison is **not like-for-like**: our window (2023-06-20..2024-09-26) carries 30 dates against their 12 scenes, on a different rule. On that window our `water3` gives 35.76 % and our `NDWI>0` gives 52.80 %, while their published 41.62 % sits *between our two rules*. The 5.86 pp gap is an artefact of rule and sample size.

Required before it is written up: reproduce their published design (their dates, extent, mask, threshold, denominator — resolving the source's own 22 vs 23 vs 26 September 2024 inconsistency by reading the paper), and report our fuller series **separately**. Neither is presented as showing our method is better.

## 8. Key figure

Two panels: (a) the precision–recall curve under nested CV with the inner-fold operating point marked; (b) leave-one-zone-out, the same curve per held-out zone, showing the recall collapse. The independent check is the `--domain neutral` HAND comparison.

## 9. Open limitations at submission

1. Spatial transfer recall 0.37–0.47 (§5).
2. The raster is an **uncalibrated score**, not a probability; `p51_rf_calibration.csv` quantifies the gap (e.g. score bin 0.1–0.2 → observed rate 0.082, gap +0.062).
3. Labels are weak: S1 M3 masks with their own error (P31/P38), three-state so low-confidence cells are never scored — which keeps the metric honest but means the reported prevalence is of the labelled subset, not the landscape.
4. HAND agreement is conditional (§3).
5. The Lischenko comparison is not yet a comparison (§7).

## 10. Verdict

**Has sufficient evidence, provided the paper is about what actually happened**: a retrospective reconstruction that works well within the area it was trained on and transfers poorly. Written that way — with §5 as a finding rather than a disclaimer — it is a publishable and useful negative-leaning result. Written as "AUC 0.96 flood mapping", it would be misleading.
