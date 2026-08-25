# Scientific Information Loss Report v4
**Corpus**: 52 papers (pipeline stopped at 52/3692)  
**Date**: 2026-05-15  
**Question**: What scientific meaning survives the full pipeline, and what is lost at each stage?

---

## Executive Summary

Across 52 processed papers, the pipeline loses the majority of scientific measurement data. Performance metrics (NSE, RMSE) — the primary quantitative evidence in hydrology papers — are extracted in 0% of cases. Nearly three-quarters of papers have no satellite data source identified. The losses are systematic and traceable to specific code paths, not to data quality.

---

## 1. Information Loss Quantified

### 1.1 By Entity Type

| Information type | Papers with any | Papers with none | Loss rate |
|---|---|---|---|
| study_type label | 52/52 (100%) | 0/52 | 0% |
| task label | 45/52 (87%) | 7/52 | 13% |
| satellite entities | 14/52 (27%) | 38/52 (73%) | **73%** |
| method entities | 43/52 (83%) | 9/52 (17%) | 17% |
| DEM entities | — | — | — |
| metric values | 28/52 (54%) | 24/52 (46%) | **46%** |
| NSE specifically | 0/52 (0%) | 52/52 (100%) | **100%** |
| RMSE specifically | ~0/52 (~0%) | ~52/52 (~100%) | **~100%** |

### 1.2 By Section

| Section | Empty rate | Content at risk |
|---|---|---|
| results | 48% empty | All metric values for ~50% of papers |
| study_area | ~50% empty | Geographic scope, watershed description |
| methods | 2% empty | Extraction coverage good here |
| other | 8% empty | Avg 16 276 chars of unclassified content |

### 1.3 Entity Specificity Loss

| XML entity | Paper JSON entity | Information lost |
|---|---|---|
| Sentinel-1 (SAR, C-band) | `SENTINEL` | Mission number, wavelength band |
| Sentinel-2 (optical, 10m) | `SENTINEL` | Mission number, optical vs SAR distinction |
| Sentinel-1A / Sentinel-1B | `SENTINEL` | Specific satellite in constellation |
| NSE = 0.82 (in methods) | (not extracted) | The actual value, and the metric itself |
| NSE (mentioned, no value) | (not extracted) | Even the metric name |
| `HEC-RAS 1D` | `one_dimensional_hydrodynamic_model` | Human-readable name (correct normalization) |

---

## 2. Stage-by-Stage Loss Tracing

### Stage 1: XML → sections

**Content preserved**: All text extracted from TEI XML by GROBID is available in memory.

**Content lost**:
- Sections with non-standard titles fall to `other` (avg 16 276 chars)
- `study_area` empty for ~50% of papers because GROBID titles like "Description of Study Basin", "Study Site", "Watershed Characteristics" are not in the router's trigger set
- `results` titles that include "Discussion" are routed to `results` — discussion content absorbed
- No content from `other` is searched for metrics

**Quantified loss**: Every paper with an unrecognized section title contributes all that section's scientific content to `other`, where it is partially inaccessible.

### Stage 2: Sections → entity extraction

**Metrics loss** (most critical):

```
metric_text = abstract + results + conclusion
              ← methods EXCLUDED
```

NSE distribution across sections (from XML cross-check):
- Methods: 77% of papers have NSE in methods section
- Abstract: present in ~50% as concept name, no value
- Results: present in ~31% of papers
- Conclusion: present in ~15% of papers

The `metric_text` scope means:
- Papers where NSE appears only in methods: **0% captured**
- Papers where NSE appears in results: **captured only if value is numeric**
- Papers where NSE is in abstract as concept: **not captured** (METRIC_PATTERN requires digit)

**Metric pattern requirement**: `\bnse\b\s*(?:=|:|of)?\s*(\d+(?:\.\d+)?)` — the capturing group requires at least one digit. "NSE improved performance" → 0 matches. "NSE = 0.82" → captured. "NSE values ranged from 0.65 to 0.85" → captures 0.65 only.

**Satellite loss**:
- `satellite_text` includes methods → coverage should be reasonable
- 73% papers with no satellites suggests most papers don't use explicit satellite names in methods text, OR the usage filter (`_is_real_usage()`) blocks legitimate mentions
- `strict=True` requires positive usage keywords within 200 chars: "SAR imagery was obtained from the ESA Sentinel-1 archive" — "obtained" is NOT in `_USAGE_POSITIVE`, so this would be blocked even though it is clearly real usage

**Usage filter analysis** (`_USAGE_POSITIVE` list):
- Covered: "used", "applied", "using", "employ", "adopt", "utilize" and passive forms
- NOT covered: "obtained", "acquired", "downloaded", "derived from", "provided by", "courtesy of", "from the archive", "sourced from", "collected from", "generated from"

Satellite data acquisition is commonly described with "obtained from", "downloaded from", "acquired from" — none of which are in `_USAGE_POSITIVE`.

### Stage 3: Entity extraction → scoring → paper JSON

**LLM score always 0**: The entity scoring formula is:
```
final_score = pattern×0.3 + context×0.3 + embedding×0.2 + llm×0.2
```
Per-entity judge is not wired (`llm_score=0` for all entities). The 20% LLM weight contributes nothing.

**Acceptance threshold = 0.3**: Very permissive — all 67 entities found across 20 audited papers were accepted. No false negatives from threshold filtering were detected in the audit sample, but the low threshold may accept false positives.

**Context score computation**: The `context` component (weight 0.3) uses surrounding text analysis. "SAR" in "synthetic aperture radar (SAR)" should score highly but requires the pattern extractor to find the match and the context scorer to evaluate the surrounding phrase.

### Stage 4: Paper JSON → normalized entities

`normalized_entities` field is populated with canonical IDs from the v2 ontology. The normalization step correctly maps `HEC-RAS` → `one_dimensional_hydrodynamic_model` and similar. However:

- Normalization operates only on entities that were extracted in Stage 2. If extraction failed, normalization cannot recover the entity.
- Confidence distribution in `normalization_validation.json`: all tests return 1.0 or 0.0. Real-world papers with paraphrased entity mentions (not exact aliases) take the semantic path (untested).

### Stage 5: Paper JSON field mapping

`paper["study_type"]` and `paper["task"]` are `None` at the top level. The actual values are at:
- `paper["entities"]["geo"]["study_type"]["label"]` (e.g., "case_study")
- `paper["entities"]["task"]["label"]` (e.g., "hydrological_modeling")

Any downstream system reading `paper.study_type` gets `None` regardless of extraction quality. This is a schema serialization bug, not an extraction failure.

---

## 3. Information Surviving the Pipeline

### What survives (for the 52-paper sample)

| Information | Survival rate | Notes |
|---|---|---|
| Paper title, authors, DOI | ~100% | From GROBID TEI metadata |
| Abstract text | 100% | Full text in sections.abstract |
| Methods section text | 98% | Very reliable |
| study_type label | 100% | At entities.geo.study_type.label |
| task label | 87% | At entities.task.label |
| Primary study country | ~80% | From regex + author affiliation |
| Method entities | 83% | HEC-RAS, SWAT, ANN, etc. |
| Judge verdict (llm_judge) | 87% | Stored in llm_judge field |
| Normalized method entities | 83% | In normalized_entities.methods |

### What is systematically lost

| Information | Recovery rate | Primary cause |
|---|---|---|
| NSE values | 0% | metric_text excludes methods |
| RMSE values | ~0% | metric_text excludes methods |
| Satellite mission specificity | 0% | SENTINEL collapses all missions |
| Satellite data in 73% of papers | 0% | Usage filter + 48% empty results |
| Study area text (50% of papers) | 0% | Router misses non-standard titles |
| Other section content (16K chars avg) | Partial | Methods reclassification only |
| Per-entity LLM confidence | 100% lost | LLM judge not wired to entities |

---

## 4. Scientific Impact Assessment

**For hydrology papers**, the extracted paper JSON currently:
- Identifies what model was used (HEC-RAS, SWAT, etc.) — **preserved**
- Identifies the study location — **mostly preserved**
- Records what performance was achieved (NSE = 0.82) — **systematically lost**
- Identifies which satellite data was used — **lost in 73% of papers**
- Records what DEM was used — **likely lost** (20 DEM entries, mostly undetected)

The pipeline successfully captures the "what model" dimension but fails to capture the "how well did it perform" and "what data was used" dimensions. This severely limits the ability to answer scientific questions like "which models achieve NSE > 0.80 in urban catchments" or "which studies used Sentinel-1 for flood mapping in Asia."

---

## 5. Estimated Full-Corpus Impact

Projecting the 52-paper findings to the full 3 692-paper corpus:

| Metric | Expected in full corpus |
|---|---|
| Papers with 0 satellites | ~2 700 (73%) |
| Papers with 0 metrics | ~1 700 (46%) |
| Papers with NSE extracted | ~0 (0%) |
| Papers with study_type (correct path) | ~3 690 (100%) |
| Papers with no methods | ~630 (17%) |

The NSE/RMSE loss affects every hydrology paper in the corpus that reports model performance — which is nearly all of them.
