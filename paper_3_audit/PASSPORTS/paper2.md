# Paper 2 passport — reconstruction of the reservoir bed

*Phase 0 deliverable, 2026-09-21. Numbers are quoted from OWN_EVIDENCE.csv; each traces to a hashed snapshot table.*

## 1. The question
What bed surface can be recovered from a legacy navigation survey, and where does its error limit what the surface may be used for?

## 2. Own result, judged separately for three objects
The passport requirement is that these are **not** averaged together; they have different support and different error.

| Object | Result | Support |
|---|---|---|
| Reservoir pool | blocked CV RMSE 2.76 m, bias −0.03 m; **independent** test against ICESat-2 on the dry bed RMSE 1.448 m, bias +0.204 m (n = 16 164) | 7 509 soundings |
| Downstream beds | soundings-only wins in every zone (RMSE 2.20–2.62 m) | estuary only **20.2 %** of area within sounding support |
| Merged terrain | vs night ICESat-2 RMSE 1.092 m, LE90 1.036 m; vs GEDI ground RMSE 1.66 m | whole domain |

The independent test is the contribution: blocked cross-validation withholds soundings from a surface built of soundings and measures interpolation error only. ICESat-2 and GEDI entered none of the reconstruction.

## 3. The defect that blocks submission
Against FABDEM as an external control, the seam's gap-fill class — **30.8 km² — sits 21.75 m below it**, with a further 133.1 km² of feathered class at −4.36 m, while FABDEM and GEDI agree there to within half a metre. Recorded as `B8.2`, status CONTRADICTED. The pooled night-time accuracy does not see it because no validation point falls there. **The terrain is not fit to carry a hydraulic model until the seam is repaired**, which also blocks paper 5.

## 4. Borrowed foundation
Paper 1: the vertical frame, and the finding that the S-57 soundings are reduced to the 14.00 m navigation drawdown level (V6.1) — a datum result established there and consumed here.

## 5. Key figure and independent check
Error against ICESat-2 stratified by distance to the nearest sounding. The stratification shows a **threshold** of support, not a gradient: flat within a kilometre (1.535 m nearest band, 1.285 m at 250–500 m) and degrading only where the interpolation extrapolates (2.48 m beyond 2 km, on 42 points).

## 6. Open limitations
The soundings' survey epoch is unrecorded, so no rate of bed change can be stated and genuine 2019-22→2023-24 change sits inside the residual. Multi-level optical contours cannot constrain the shallow bed (reported as a negative result). Four fifths of the estuary rests on EMODnet, not on soundings.

## 7. Verdict
**Sufficient evidence for the pool and the downstream beds; the merged terrain is blocked** by §3 until the seam is repaired and B7.1 re-run.
