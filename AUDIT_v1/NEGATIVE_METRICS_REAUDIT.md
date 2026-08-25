# Negative Metrics Re-Audit — Corpus Impact of F-EXT-1

**Date**: 2026-06-11 | **Status**: ✅ fix applied, corpus re-scanned
**Scope**: 4,852 paper.json (results + discussion + conclusion sections)
**Related**: CODE_REVIEW_v1/02 F-EXT-1; Phase 2.1–2.2 remediation; `tests/test_15_metric_ranges.py` (34 tests)
**Artifact**: `data/analytics/negative_metrics_reaudit.csv`

---

## 1. Problem Summary

Until 2026-06-11, **every metric-extraction path silently lost negative values**:

1. `regex_extractor.py` — `_NUM` had no sign (`([\d]+\.?[\d]*)`), and NSE lived inside
   `R2_PATTERNS` with the comment *"same 0–1 scale"* — scientifically wrong: NSE ∈ (−∞, 1].
2. `entity_extractor.py` (KB path) — `(\d+(?:\.\d+)?)` value groups for NSE/KGE/R2/PBIAS/Kappa/R.
3. `metric_ontology.py` — `RATIO_METRICS` wrongly included kappa/mcc/r2/bias/correlation;
   `_normalize_ratio` returned `None` for anything < 0.
4. `section_extractor.py` — its own local normalizer clamped to (0, 100].

A negative NSE means *"the model performs worse than the mean of observations"* — a
meaningful, publishable result. Dropping it biases the knowledge graph toward successful
models (**systematic optimism bias**).

## 2. Fix Applied (Phase 2.1)

- `METRIC_RANGES` model: NSE/KGE/R² ∈ (−∞, 1], Kappa/MCC/correlation ∈ [−1, 1],
  bias/PBIAS signed unbounded, ratios [0, 1], magnitudes [0, ∞).
- New classes: `SIGNED_RATIO_METRICS`, `EFFICIENCY_METRICS`, `metric_scale()`,
  `validate_metric_value()` → `"ok" | "suspect"` (out-of-range values are flagged,
  not silently dropped).
- Sign-aware patterns in all three extraction paths; NSE/KGE got their own pattern
  families (`NSE_PATTERNS`, `KGE_PATTERNS`), removed from `R2_PATTERNS`.
- Deliberate non-rescue: `NSE = 1.7` is **not** reinterpreted as 1.7 % — impossible
  values return `None`/`suspect` rather than fabricating a plausible number.
- 34 regression tests in `tests/test_15_metric_ranges.py`.

## 3. Corpus Re-Scan Results

Sign-aware scan over results/discussion/conclusion of 4,852 papers
(scale-description mentions like "NSE can range from −∞ to 1" filtered out):

| Metric | Lost negative values | Papers affected |
|--------|---------------------|-----------------|
| Bias   | 20 | 13 |
| PBIAS  | 13 | 10 |
| KGE    |  2 |  1 |
| NSE    |  1 |  1 |
| **Total** | **36** | **25** |

Context: 426 papers mention NSE and 89 mention KGE in results-type sections.

**Representative recovered values**:
- `remotesensing-13-05083`: *"the mean NSE is **−1.22** for calibration and **−1.33** for
  validation"* — model failure on flash floods; previously invisible to the KG.
- `essoar.174584998`: *"the KGE was **−0.750**, indicating that the LsRSQ-DA simulation…"*
- 33 signed Bias/PBIAS values where the sign encodes over- vs under-estimation direction.

## 4. Honest Assessment of Impact

- The headline fear from the review ("widespread loss of negative NSE") is **smaller than
  hypothesized**: 1 lost NSE + 2 KGE values corpus-wide. Flood-mapping literature
  predominantly reports successful calibrations in [0, 1].
- The **dominant real loss is signed bias metrics (33 values)**: PBIAS/Bias signs encode
  systematic over-/under-estimation — scientifically essential for model comparison.
- The fix's main value is **forward-looking correctness**: future corpus growth
  (hydrological modeling papers report negative NSE routinely) and the training-dataset
  plan both depend on range-correct extraction.

## 5. Follow-ups

1. Existing `paper.json` entities/`NumericFacts` were extracted with the old patterns —
   the 25 affected papers need re-extraction during the next pipeline maintenance run
   (paper list in the CSV). Not urgent given magnitude.
2. `validate_metric_value()` should be wired into NumericFact loading (table path) so
   out-of-range table values get `suspect` flags — Phase 3 scope.
