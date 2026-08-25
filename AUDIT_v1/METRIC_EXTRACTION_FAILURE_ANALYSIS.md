# Metric Extraction Failure Analysis

**Date**: 2026-05-20  
**Scope**: NSE, RMSE, MAE, R², KGE, IoU, F1-score, Accuracy, Precision, Recall  
**Conclusion**: Metrics ARE partially extracted for some papers. Six distinct failure modes cause incomplete coverage, missing canonical IDs, and zero Nougat-derived metric values.

---

## What Already Works

- **Pattern matching fires** for well-formatted inline text: NSE, RMSE, MAE, R², KGE regex patterns in `entity_extractor.py:28-42` correctly capture numeric values from structured results sections.
- **Real examples confirmed**: `1-s2.0-S0022169423003451-main.tei.paper.json` → `NSE=0.72`; `1-s2.0-S0022169421007320-main.tei.paper.json` → `RMSE=0.163`
- **metric_text scope is correct**: `entity_pipeline.py:96-98` includes abstract + methods + results + conclusion — methods IS included.
- **Nougat inference runs** on a subset of papers: `1-s2.0-S1470160X25005898-main` has 8 formula/table regions with valid `nougat_text` in `regions.parquet`.
- **Normalization runs inline** via `process_paper.py:_save_normalized()` — `data/normalized/` has 3,680 files already.

---

## Failure Mode 1 — Metrics Skip Semantic Validation

**Location**: `src/ingestion/stages/entity_pipeline.py:323-327`

**What happens**:
```python
# Lines 325-327 — satellites/dems/methods get full semantic processing:
satellites = run_entity_pipeline(ctx, ..., "satellite")
dems       = run_entity_pipeline(ctx, ..., "dem")
methods    = run_entity_pipeline(ctx, ..., "method")

# Line 323 — metrics are extracted and returned raw:
metrics = extractor.extract_metrics(ctx.metric_text)
# metrics go straight to output — no run_entity_pipeline() call
```

`run_entity_pipeline()` (defined at `entity_pipeline.py:~180`) does:
- Probabilistic scoring against embeddings
- Disambiguation loop
- Sets `entity["accepted"] = True/False`
- Deduplication

Metrics skip all of this. They have no `accepted` field, no embedding score, no deduplication pass. Downstream consumers that filter on `entity["accepted"]` silently drop all metrics.

**Impact**: All metric entities missing `accepted` flag → downstream consumers that gate on `accepted` see zero metrics.

**Minimal fix**: Either set `entity["accepted"] = True` unconditionally after `extract_metrics()`, or run metrics through a lightweight version of the validation loop. No architecture change required — one line:

```python
# entity_pipeline.py, after line 323
metrics = [dict(m, accepted=True) for m in metrics]
```

---

## Failure Mode 2 — Ontology Linkage Bypassed for Metrics

**Location**: `src/ingestion/knowledge/entity_extractor.py:334` and `src/ingestion/knowledge/knowledge_loader.py:376-384`

**What happens**:
```python
# entity_extractor.py:334
rec = self.kb.resolve(metric_type)

# knowledge_loader.py:376-384 — resolve() searches ONLY self.entities (glossary):
def resolve(self, name: str) -> Optional[EntityRecord]:
    ...
    for ent in self.entities.values():   # ← glossary EntityRecord only
        if name in ent.aliases:
            return ent
    return None
```

`self.v2_metrics` (a `dict[str, MetricRecord]`) is populated with canonical IDs like `metric.nse`, `metric.rmse`, `metric.f1_score`, `metric.iou` — but `resolve()` never looks there.

**Result**: `rec` is always `None` for any metric name → `kb_metadata` stays `{}` → `canonical_id` is never set → `src/normalization/normalization_utils.py` defaults `canonical_id: None` for all metrics.

**Confirmed by**: `grep -n "canonical_id" normalization_utils.py` shows `canonical_id: None` as the default, with no special-case lookup for metrics.

**Minimal fix**: Extend `resolve()` to check `v2_metrics` when `entities` lookup fails:

```python
# knowledge_loader.py, inside resolve():
if result is None and name in self.v2_metrics:
    return self.v2_metrics[name]  # MetricRecord has .canonical_id
```

Or add a dedicated `resolve_metric(metric_type)` method in KnowledgeBase and call it from `entity_extractor.py:334`.

---

## Failure Mode 3 — No Nougat→Metric Extraction Stage

**Location**: Gap between `data/sodb/{paper_id}/regions.parquet` and metric extraction

**What happens**: `regions.parquet` has columns `nougat_text` and `nougat_latex` populated for formula/table regions where Nougat inference succeeded. Zero code reads these columns for metric value extraction.

Tracing the gap:
- `src/ingestion/stage2/engineer.py` reads `regions.parquet` but only sets a boolean `contains_metrics` flag (does not extract values)
- `src/extraction/table_extractor.py` (NumericFact extraction) reads only GROBID TEI XML — `source` hardcoded `"grobid_tei"`, `source_region_id` always `NULL`
- `entity_pipeline.py` uses `ctx.metric_text` (plain text from TEI sections) — never reads `nougat_text`/`nougat_latex`

**Impact**: Papers where metrics appear only in equations or formatted tables (not in prose) have zero metric extraction. Nougat's formula OCR output is computed and stored but semantically dead.

**Minimal fix**: In `entity_pipeline.py`, augment `metric_text` with `nougat_text` from regions.parquet for formula and table regions:

```python
# entity_pipeline.py — after constructing metric_text (line ~98)
sodb_dir = Path("data/sodb") / paper_id
regions_file = sodb_dir / "regions.parquet"
if regions_file.exists():
    import pandas as pd
    rdf = pd.read_parquet(regions_file, columns=["region_type", "nougat_text"])
    nougat_supplement = " ".join(
        rdf.loc[rdf["region_type"].isin(["formula", "table"]) & rdf["nougat_text"].notna(), "nougat_text"]
    )
    ctx.metric_text += " " + nougat_supplement
```

No new stage, no SDOM change — just widens the text window passed to existing pattern matching.

---

## Failure Mode 4 — Percent Pattern Pollution

**Location**: `src/ingestion/knowledge/entity_extractor.py:37`

**What happens**:
```python
METRIC_PATTERNS = {
    ...
    "Percent": r"(\d+(?:\.\d+)?)\s*%",   # too broad
    "F1":      r"[Ff]1[\s\-]?(?:score|Score)?\s*(?:of|=|:)?\s*(\d+(?:\.\d+)?)",
    ...
}
```

When text contains `F1-score = 92.5%`, BOTH patterns fire:
- `F1` pattern captures `92.5`
- `Percent` pattern also captures `92.5` → creates a spurious `Percent=92.5` entity

This doubles the entity count for any percentage-expressed metric (F1, IoU, OA, Accuracy, Precision, Recall — all commonly reported as percentages).

**Impact**: Duplicate entities inflate the metric count; consuming code may see `Percent` entities instead of (or in addition to) the specific metric type, degrading precision in downstream aggregations.

**Minimal fix**: Make `Percent` pattern require no preceding metric keyword, or apply post-extraction deduplication that removes `Percent` matches that overlap with a named metric match:

```python
# After extract_metrics() in entity_extractor.py
# Remove Percent entities whose value appears in a named metric entity
named_values = {m["value"] for m in metrics if m["type"] != "Percent"}
metrics = [m for m in metrics if m["type"] != "Percent" or m["value"] not in named_values]
```

---

## Failure Mode 5 — F1, IoU, OA Missing from KB Glossary

**Location**: `src/ingestion/knowledge/knowledge_loader.py`

**What happens**: PATTERN_OVERRIDES (lines 106-110) provide patterns for RMSE, MAE, NSE, KGE — but F1, IoU, and OA are handled only by hardcoded regex in `METRIC_PATTERNS`. They are NOT in the KB glossary (`self.entities`), so `kb.resolve("F1")` and `kb.resolve("IoU")` return `None`.

The ontology DOES have canonical IDs (`metric.f1_score`, `metric.iou`, `metric.overall_accuracy`) with full alias sets, but these live in `v2_metrics` — which `resolve()` never checks (Failure Mode 2 above).

**Impact**: F1/IoU/OA entities always have empty `kb_metadata` and `canonical_id: None`. Any downstream system that queries by canonical ID misses all remote sensing accuracy metrics.

**Fix**: Resolved by the Failure Mode 2 fix — once `resolve()` checks `v2_metrics`, these canonical IDs will be found automatically. No separate KB glossary entries needed.

---

## Failure Mode 6 — Nougat Runtime Instability

**Location**: `src/document/nougat_parser.py:293-302`

**What happens**: The existing defensive fix at lines 293-302 sets known None-typed fields to `False`:

```python
bool_defaults = {
    "do_thumbnail": False,
    "do_binarize":  False,
    ...
}
for key, default in bool_defaults.items():
    if getattr(config, key, None) is None:
        setattr(config, key, default)
```

But this only covers fields the developer anticipated. If other NoneType bool fields exist in the Nougat config (across model versions), the `TypeError: expected bool, got NoneType` can still occur for fields not in `bool_defaults`.

**Evidence from data**: `data/sodb/remotesensing-12-02693-v2/regions.parquet` — 13 formula/table regions, ALL with `nougat_text = NaN`. Nougat inference silently failed for this paper.

**Impact**: Papers with scanned equations or complex tables that require Nougat get 0 metric values extracted from visual content. Failure is silent — pipeline continues, paper.json is written without Nougat-derived metrics.

**Minimal fix**: Broaden the defensive check in `nougat_parser.py` to cover all bool-typed fields generically:

```python
# Replace the explicit bool_defaults dict with a generic scan
import dataclasses
if dataclasses.is_dataclass(config):
    for field in dataclasses.fields(config):
        if field.type in (bool, "bool") and getattr(config, field.name) is None:
            setattr(config, field.name, False)
```

Also add explicit error logging when `nougat_text` is NaN after inference, so silent failures become visible in the pipeline log.

---

## Summary Table

| # | Failure | Location | Metrics Affected | Impact | Fix Size |
|---|---------|----------|-----------------|--------|----------|
| 1 | Metrics skip `run_entity_pipeline()` → no `accepted` flag | `entity_pipeline.py:323-327` | All | High — downstream filters drop all metrics | 1 line |
| 2 | `kb.resolve()` never checks `v2_metrics` → canonical_id null | `knowledge_loader.py:376-384` | All | High — no ontology linkage, no canonical queries | 3 lines |
| 3 | No code reads `nougat_text`/`nougat_latex` from regions.parquet | Gap: `engineer.py` ↔ `entity_pipeline.py` | Table/formula metrics | Medium — formula-only metrics invisible | ~10 lines |
| 4 | Percent pattern fires on all %-expressed metric values | `entity_extractor.py:37` | F1, IoU, OA, Accuracy, Precision, Recall | Low-medium — duplicate entities, precision loss | 3 lines |
| 5 | F1, IoU, OA not in KB glossary | `knowledge_loader.py` | F1, IoU, OA | Medium — resolved by Fix 2 | 0 lines (Fix 2 covers it) |
| 6 | Nougat bool NoneType → silent inference failure | `nougat_parser.py:293-302` | Any metric in formulas/tables | Low-medium — Failure 3 can't help if Nougat fails | ~5 lines |

---

## Expected Gain After Fixes

| Fix | Expected Gain |
|-----|---------------|
| Fix 1 (`accepted` flag) | Metrics appear in all downstream consumers that filter on `accepted`; currently ~100% of metrics invisible to those consumers |
| Fix 2 (resolve v2_metrics) | `canonical_id` populated for NSE, RMSE, MAE, R², KGE, F1, IoU, OA → enables ontology-backed queries and graph linking |
| Fix 3 (Nougat→metric_text) | Captures metrics reported only in equations/tables; estimated +15-30% coverage for hydrology/remote-sensing papers |
| Fix 4 (Percent dedup) | Eliminates spurious duplicate entries; improves per-paper metric entity precision |
| Fix 6 (Nougat bool guard) | Reduces Nougat silent failures; improves Fix 3 yield for complex papers |

Fixes 1 and 2 are the highest priority — they affect all papers, all metric types, and require <5 lines each.

---

## Execution Order (Recommended)

1. **Fix 1** — `entity_pipeline.py`: set `accepted=True` on extracted metrics (1 line)
2. **Fix 2** — `knowledge_loader.py`: extend `resolve()` to check `v2_metrics` (3 lines)
3. **Fix 4** — `entity_extractor.py`: post-extraction Percent dedup (3 lines)
4. **Fix 6** — `nougat_parser.py`: generic bool NoneType guard (5 lines)
5. **Fix 3** — `entity_pipeline.py`: augment `metric_text` with Nougat formula/table text (~10 lines)

Fixes 1-4 are safe to apply without re-running any heavy pipeline stages. Fix 3 requires the Nougat region pipeline to have run for a paper (180/3,875 currently); its yield will grow as more papers are processed.

After applying Fixes 1-4, re-run `pipeline_runner` on a 50-paper smoke test to verify metric entity counts and canonical_id coverage before committing to a full re-run.
