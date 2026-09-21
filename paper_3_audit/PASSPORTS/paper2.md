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

## 3. The seam defect — found, and since repaired

On 19 September, sampled against FABDEM as an external control, the seam's gap-fill class (30.8 km²) sat **21.75 m** below it and a feathered class of 133.1 km² sat 4.36 m below. Re-checked on 21 September after the repair: the gap-fill class no longer exists and the feathered class, now 59.6 km², agrees with FABDEM to **+0.02 m**. `B8.2` moves from CONTRADICTED to SUPPORTED and **no longer blocks submission, nor paper 5**.

The pool bed still reads about −4.7 m against FABDEM over 2 144 km². That is not a seam error: FABDEM carries the pre-breach water surface there, and the reconstruction carries the dry bed. The difference is the drained depth, and it is the expected sign and magnitude.

The episode is worth one sentence in the paper's methods: the defect was invisible to the night-time ICESat-2 validation because no validation point fell in those classes, and only an external control with independent coverage exposed it.

## 4. Borrowed foundation
Paper 1: the vertical frame, and the finding that the S-57 soundings are reduced to the 14.00 m navigation drawdown level (V6.1) — a datum result established there and consumed here.

## 5. Key figure and independent check
Error against ICESat-2 stratified by distance to the nearest sounding. The stratification shows a **threshold** of support, not a gradient: flat within a kilometre (1.535 m nearest band, 1.285 m at 250–500 m) and degrading only where the interpolation extrapolates (2.48 m beyond 2 km, on 42 points).

## 6. Open limitations
The soundings' survey epoch is unrecorded, so no rate of bed change can be stated and genuine 2019-22→2023-24 change sits inside the residual. Multi-level optical contours cannot constrain the shallow bed (reported as a negative result). Four fifths of the estuary rests on EMODnet, not on soundings.

## 7. Verdict
**Sufficient evidence.** The pool, the downstream beds and — since the 21 September repair — the merged terrain all carry their own validation. B7.1 should be re-run on the repaired surface so the quoted accuracy describes what is actually shipped.
