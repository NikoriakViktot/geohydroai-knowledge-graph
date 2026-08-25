# Metric Extraction Patch Report

**Date:** 2026-05-20  
**Status:** All 5 patches applied and smoke-tested ✅  
**Scope:** Surgical fixes only — no architecture changes, no pipeline re-runs required

---

## Problem Summary

Stage 1 extracted scientific metrics (NSE, RMSE, KGE, F1, IoU, OA) from paper text but
they disappeared downstream. Root cause was not OCR quality — all five failures were
semantic propagation gaps in the pipeline.

---

## Patches Applied

### PATCH 1 — Metrics missing `accepted=True` (CRITICAL)

**File:** `src/ingestion/stages/entity_pipeline.py:360`

**Problem:** `extract_entities()` routes satellites/DEMs/methods through `run_entity_pipeline()`,
which sets `entity["accepted"]` based on scoring. Metrics bypassed `run_entity_pipeline()`
entirely, so `accepted` was never set. Downstream consumers that filter on
`entity["accepted"]` silently dropped all metrics.

**Fix:**
```python
metrics = extractor.extract_metrics(ctx.metric_text)
# PATCH 1: metrics bypass run_entity_pipeline() so they never get the
# accepted flag; downstream filters on entity["accepted"] silently drop them.
metrics = [dict(m, accepted=True) for m in metrics]
```

**Impact:** All extracted metrics now survive downstream filtering.

---

### PATCH 2 — `resolve()` ignores `v2_metrics` (CRITICAL)

**Files:**
- `src/ingestion/knowledge/knowledge_loader.py:389` — new `resolve_metric()` method
- `src/ingestion/knowledge/entity_extractor.py:345` — caller in `extract_metrics()`

**Problem:** `kb.resolve()` searches only the EntityRecord glossary. `KnowledgeBase.v2_metrics`
holds `MetricRecord` objects with canonical IDs (NSE, RMSE, MAE, KGE, etc.) — a completely
separate dict that `resolve()` never checked. Result: `canonical_id` was always `None` in
`kb_metadata` even for metrics that exist in the ontology.

**Fix — new method in `KnowledgeBase`:**
```python
def resolve_metric(self, name: str) -> Optional[MetricRecord]:
    # resolve() only searches EntityRecord glossary; v2_metrics holds canonical
    # MetricRecords (NSE, RMSE, KGE, ...) that resolve() misses.
    name_u = name.upper()
    return next((m for k, m in self.v2_metrics.items() if k.upper() == name_u), None)
```

**Fix — independent lookup after EntityRecord check in `extract_metrics()`:**
```python
mrec = self.kb.resolve_metric(metric_type)
if mrec:
    kb_meta.setdefault("full_name", mrec.full_name)
    kb_meta.setdefault("domain", "hydrology")
    kb_meta["canonical_id"]  = mrec.id
    kb_meta["optimal"]       = mrec.optimal
    kb_meta["applicable_to"] = mrec.applicable_to
```

Note: this is an independent check (not `else:`). For metrics in both glossary and
`v2_metrics` (e.g. NSE, RMSE), the EntityRecord path fires first but never sets
`canonical_id`; the v2_metrics path always follows and sets it.

**Coverage of `v2_metrics` canonical IDs:**
`RMSE`, `MAE`, `NSE`, `R2`, `KGE`, `MAPE`, `PBIAS`, `RSR`, `POD`, `FAR`, `Bias_score`,
`CSI`, `Success_Index`, `MARE`, `MSE`, `IoA`, `AUC`, `Kappa`

**Not covered** (no v2_metrics entry): `F1`, `IoU`, `OA` — these have regex patterns but
no MetricRecord. `canonical_id` remains `None` for them.

**Impact:** NSE → `canonical_id='NSE'`, RMSE → `canonical_id='RMSE'` confirmed in smoke test.

---

### PATCH 3 — Percent pattern pollution (MEDIUM)

**File:** `src/ingestion/knowledge/entity_extractor.py:365–368`

**Problem:** `METRIC_PATTERNS` includes a `"Percent"` regex that fires on every percentage
value in text. When F1=91.3% or OA=94.2% appears, both an `F1`/`OA` entity AND a
`Percent` entity are extracted for the same numerical value, creating spurious duplicates
that inflate metric counts and confuse normalization.

**Fix:**
```python
# PATCH 3: "F1 = 92.5%" fires both F1 and Percent patterns for the same
# value; remove Percent entries that duplicate a named metric's value.
named_values = {m["value"] for m in metrics if m["type"] != "Percent"}
metrics = [m for m in metrics if m["type"] != "Percent" or m["value"] not in named_values]
```

**Impact:** Percent entries whose value already appears under a named metric type are
removed. Standalone percentages (e.g. "coverage was 87%") are unaffected.

---

### PATCH 4 — Nougat bool `NoneType` runtime bug (MEDIUM)

**File:** `src/document/nougat_parser.py:308–312`

**Problem:** `NougatProcessor` image processor attributes prefixed `do_*` can be `None`
when loaded from HuggingFace Hub (depends on model version). The explicit `bool_defaults`
dict in `_ensure_model_loaded()` covers the attributes known at development time, but
newer model checkpoints may add additional `do_*` fields not in that list. A `None`
bool attribute causes `TypeError` during inference.

**Fix** (after the explicit `bool_defaults` loop):
```python
# Generic scan: any do_* attribute still None after the explicit list
# triggers TypeError in newer Nougat model versions not covered above.
for attr in vars(ip):
    if attr.startswith("do_") and attr not in bool_defaults and getattr(ip, attr) is None:
        setattr(ip, attr, False)
```

**Impact:** Future-proofs model loading against unanticipated `do_*` NoneType fields.

---

### PATCH 5 — Nougat semantic bridge for formula/table regions (IMPORTANT)

**File:** `src/ingestion/stages/entity_pipeline.py:303–325, 349–352`

**Problem:** Scientific metrics often appear only in formulas and tables (e.g.
`NSE = 0.82`, `RMSE = 12.4 mm`). GROBID TEI XML does not preserve equation/table
text faithfully. `NougatRegionPipeline` writes OCR output for FORMULA_REGION and
TABLE_REGION into `data/sodb/{paper_id}/regions.parquet` → `nougat_text` column,
but `ctx.metric_text` was assembled only from TEI prose sections — the Nougat output
was never consulted.

**Fix — new helper:**
```python
_SODB_DIR = Path(__file__).resolve().parents[3] / "data" / "sodb"

def _augment_metric_text_from_nougat(ctx: PipelineContext, paper_id: str) -> None:
    # Metrics inside equations/tables are never in TEI prose; Nougat OCR output
    # stored in SODB regions.parquet is the only source for them.
    regions_file = _SODB_DIR / paper_id / "regions.parquet"
    if not regions_file.exists():
        return
    try:
        import pandas as pd
        rdf = pd.read_parquet(regions_file, columns=["region_type", "nougat_text"])
        mask = (
            rdf["region_type"].isin(["FORMULA_REGION", "TABLE_REGION"]) &
            rdf["nougat_text"].notna()
        )
        supplement = " ".join(rdf.loc[mask, "nougat_text"])
        if supplement.strip():
            ctx.metric_text = ctx.metric_text + " " + supplement
    except Exception as exc:
        log.debug("[nougat-bridge] %s: skipped (%s)", paper_id, exc)
```

**Called in `extract_entities()` before metric extraction:**
```python
paper_id = metadata.get("paper_id", metadata.get("id", ""))
if paper_id:
    _augment_metric_text_from_nougat(ctx, paper_id)
```

**Impact:** For the 180 papers with Nougat regions, `ctx.metric_text` gains formula/table
text. Papers without `regions.parquet` are unaffected (fast `exists()` check, no IO).

---

## Smoke Test Results (10 papers)

Run against 10 papers from `data/literature/paper_json/` using `.venv/bin/python3`:

| Check | Result |
|-------|--------|
| Total metrics extracted | 63 |
| `accepted=True` on all metrics | ✅ 63/63 |
| Papers with `canonical_id` set (NSE/RMSE/KGE) | ✅ confirmed |
| Percent dedup (no named-value duplicates) | ✅ 0 duplicates |
| Nougat bridge (regions.parquet augmentation) | ✅ 9/10 papers augmented |

Example canonical IDs resolved from real papers:
- `NSE` → `canonical_id='NSE'`, `optimal='maximize'`, `applicable_to=['streamflow', 'calibration']`
- `RMSE` → `canonical_id='RMSE'`, `optimal='minimize'`

---

## Remaining Limitations

1. **F1, IoU, OA have no `canonical_id`** — these metrics exist as regex patterns but have
   no `MetricRecord` in `v2_metrics`. They will extract correctly with `accepted=True` but
   `canonical_id` remains `None`. Fix: add MetricRecord entries to the ontology for these
   three metrics.

2. **Nougat bridge covers only 180/3,875 papers** — only papers processed by
   `NougatRegionPipeline` have `regions.parquet`. The remaining ~3,695 papers rely solely
   on TEI prose for metric text. Fix: run `NougatRegionPipeline` on the full corpus (GPU
   required, ~15-25% utilization, ~3 workers).

3. **Metrics still bypass `run_entity_pipeline()`** — PATCH 1 sets `accepted=True`
   unconditionally. This means no section-boost or embedding scoring for metrics. A metric
   mentioned only in the appendix gets the same `accepted=True` as one in the abstract.
   This is intentional: metrics are precise numerical facts and should not be rejected by
   heuristic scoring.

4. **Retroactive fix requires pipeline re-run** — Existing 3,546 `paper.json` files were
   produced before these patches. To apply patches retroactively, re-run
   `pipeline_runner.py` or `process_paper.py` per paper. The patches affect only
   runtime extraction; they do not modify stored paper.json files in place.

---

## Files Modified

| File | Change | Patch |
|------|--------|-------|
| `src/ingestion/stages/entity_pipeline.py` | Added `accepted=True` after `extract_metrics()` | P1 |
| `src/ingestion/stages/entity_pipeline.py` | Added `_augment_metric_text_from_nougat()` + call | P5 |
| `src/ingestion/knowledge/knowledge_loader.py` | Added `resolve_metric()` to `KnowledgeBase` | P2 |
| `src/ingestion/knowledge/entity_extractor.py` | v2_metrics lookup in `extract_metrics()` | P2 |
| `src/ingestion/knowledge/entity_extractor.py` | Percent dedup at end of `extract_metrics()` | P3 |
| `src/document/nougat_parser.py` | Generic `do_*` None scan in `_ensure_model_loaded()` | P4 |

No new files created. No tests broken (606 tests pass).
