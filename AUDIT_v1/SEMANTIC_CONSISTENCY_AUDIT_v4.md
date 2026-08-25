# Semantic Consistency Audit v4
**Pipeline**: TEI XML → section parser → entity extractor → paper_json  
**Corpus**: 52 papers (pipeline stopped at 52/3692)  
**Date**: 2026-05-15  
**Scope**: Cross-layer trace of scientific meaning from source XML to final JSON

---

## Executive Summary

Of the five extraction targets audited (satellites, DEMs, methods, metrics, geographic metadata), **three show critical information loss** in the current pipeline run. The dominant failure modes are a metric-text scope bug that excludes the methods section (where 77% of NSE values appear), satellite version collapse (Sentinel-1/2/3 all stored as "SENTINEL"), and an oversized `other` section that absorbs content the router cannot classify.

| Layer | Finding | Severity |
|---|---|---|
| Section routing | 48% of results sections are empty; other avg 16 276 chars | HIGH |
| Metric extraction | 0% NSE extracted, 0% RMSE extracted | CRITICAL |
| Satellite extraction | 73% of papers have 0 satellites | HIGH |
| Method extraction | 17% of papers have 0 methods | MEDIUM |
| study_type | Correctly extracted (100%) at `entities.geo.study_type` | OK |
| task label | Correctly extracted (87%) at `entities.task.label` | OK |
| judge_used | 45/52 papers judged (87%) | OK |

---

## 1. Section Routing Analysis

### 1.1 Section Statistics (52 papers)

| Section | Avg chars | Empty count | Empty % |
|---|---|---|---|
| abstract | ~1 500 | 0 | 0% |
| introduction | ~8 900 | 0 | 0% |
| methods | ~13 514 | 1 | 2% |
| results | ~3 454 | 25 | **48%** |
| other | ~16 276 | 4 | 8% |
| study_area | ~0 | ~50% | ~50% |
| data_sources | ~855 | – | – |
| discussion | – | high | – |
| conclusion | ~2 569 | – | – |

**Critical**: Results sections are empty in 48% of papers. The `other` section holds on average more content than methods+results combined.

### 1.2 Section Router Keywords

The router assigns sections based on title keyword matching (`pipeline.py:585`):

- `results` triggered by: "result", "results", "accuracy", "evaluation", "assessment"
- `methods` triggered by: 43 keywords including "method", "methodology", "simulation", "calibration", "algorithm"
- Sections like "Study Area and Data" and "Results and Discussion" — the dual-labeled sections fall to the first match, typically `study_area` for the former and `results` for the latter (losing discussion content)
- `study_area` triggers require exact phrases: "study area", "study region", "study site", "area of interest" — survey papers with "Description of Watershed" or "Basin Characteristics" fall to `other`

### 1.3 `_reclassify_from_other()` Behavior

Reclassifies `other` → `methods` only if:
1. `methods` section is empty
2. `other` >= 3 000 chars  
3. Method-phrase density >= 5 per 1 000 chars

This is a rescue heuristic only — it does not recover `study_area`, `results`, or `data_sources` content from `other`.

### 1.4 Semantic Loss at Section Routing

Content from XML that is not assigned to a named section goes to `other`. `other` content participates in extraction only for methods (via `_reclassify_from_other()`) and partially for satellite/method text (they include `other` in their search scope), but **not** for metrics.

---

## 2. Entity Extraction Cross-Layer Trace

### 2.1 Section Text Scopes per Entity Type

| Entity type | Sections searched |
|---|---|
| satellites | abstract + data_sources + methods + other |
| DEMs | abstract + data_sources + methods + other |
| methods | abstract + data_sources + methods + results + other |
| **metrics** | **abstract + results + conclusion only** |

**The metric_text scope excludes methods.** This is the primary cause of metric extraction failure.

### 2.2 Confirmed Cross-Layer Loss (Paper `032003_1.tei`)

XML source contains:
- "Sentinel-1" in abstract → extracted as `SENTINEL` (version lost)
- "NSE" × 3 in abstract (as concept, no numeric value) → not extracted (no digit after NSE)
- "NSE" × N in methods (performance values) → NOT SEARCHED (methods excluded from metric_text)
- "SAR" in abstract + methods → correctly extracted as `SAR`
- Results section: 0 chars → entire `results` contribution to metric_text is empty

### 2.3 Satellite Extraction (52 papers)

- Papers with 0 satellites: **38/52 (73%)**
- Papers with ≥1 satellite: 14/52 (27%)
- Most common extracted satellites: SAR, SENTINEL, RADAR, LANDSAT, LIDAR

**Version collapse**: KB entity `SENTINEL` has pattern `\bsentinel[\s\-]?[123][abc]?\b` — matches Sentinel-1, Sentinel-2, and Sentinel-3 but stores all as `'name': 'SENTINEL'`. The specific mission (sentinel-1 C-band SAR vs sentinel-2 optical) is not preserved.

**strict=True usage filter**: The filter requires a positive usage keyword ("used", "applied", "using", etc.) within 200 chars of the match AND absence of a negative keyword. Papers with passive constructions ("SAR imagery was obtained from…") or gerund forms not in `_USAGE_POSITIVE` will fail.

### 2.4 Metric Extraction (52 papers)

- Papers with 0 metrics: **24/52 (46%)**
- Papers with ≥1 metric: 28/52 (54%)
- Avg metrics per paper: 3.0 (but distribution skewed — some papers capture many percent values)

**NSE extraction rate**: 0% of 52 papers have NSE in `entities.metrics`.  
**Root cause**:
1. `metric_text` excludes methods (NSE is in methods 77% of papers)
2. Results section empty in 48% of papers
3. Pattern `\bnse\b\s*(?:=|:|of)?\s*(\d+(?:\.\d+)?)` requires a numeric digit — "NSE improved significantly" not captured

Verification: `extract_metrics()` on the methods section of paper `032003_1` does return metrics (via "Percent" pattern) but `metric_text` never sees it.

### 2.5 Method Extraction (52 papers)

- Papers with 0 methods: **9/52 (17%)**
- `method_text` includes all sections → better coverage than metrics
- `one_dimensional_hydrodynamic_model` extracted (v2 KB) but as internal ID, not display name

### 2.6 Entity Scoring Distribution

All extracted entities are accepted (threshold = 0.3, hardcoded at `pipeline.py:2376`). Score breakdown for 67 entities across 20 papers:

| Score range | Count |
|---|---|
| 0.3–0.5 | 25 (37%) |
| 0.5–0.7 | 12 (18%) |
| 0.7–0.9 | 13 (19%) |
| 0.9–1.0 | 7 (10%) |

LLM component = 0 for all entities (per-entity judge not wired). The formula is:  
`final_score = pattern×0.3 + context×0.3 + embedding×0.2 + llm×0.2`  
with llm always contributing zero.

---

## 3. Study Type and Task Labels

**Finding: study_type is NOT stored at `paper["study_type"]`.**

- Correct path: `paper["entities"]["geo"]["study_type"]["label"]`
- Top-level `paper["study_type"]` is always `None`
- This is a schema inconsistency — any downstream consumer using `paper.study_type` gets None

**Actual study_type distribution** (correct path):
- case_study: 44/52 (85%)
- multi_site: 6/52 (12%)
- regional: 2/52 (4%)

**task distribution**:
- hydrological_modeling: 45/52 (87%)
- spectral_index_analysis: 4/52 (8%)
- flood_mapping_satellite: 2/52 (4%)
- flood_modeling_hydraulic: 1/52 (2%)

---

## 4. LLM Judge Behavior

- Papers with judge_used=True: **45/52 (87%)**
- Papers with `paper_id='12345'` in llm_judge (template leak): ~4/52 — the LLM copied the example paper_id from the system prompt
- judge verdict study_type `accepted=True` for all validated papers → `apply_judge_verdict()` does NOT update `geo["study_type"]` when accepted (only updates on rejection with corrected_value)
- Judge correctly identified study_type, task, and study_country in most cases

---

## 5. Normalized Entities

`normalized_entities` field exists in all papers with structure `{methods: [...], satellites: [...], dems: [...]}`. These are the ontology-normalized versions of extracted entities. Cross-check: the normalized entities use canonical IDs (e.g., `one_dimensional_hydrodynamic_model`) while raw entities use display names (e.g., `HEC-RAS`).

---

## Recommendations (Audit Findings Only — Not Implementation)

1. **metric_text must include methods section** — the single highest-impact fix
2. **METRIC_PATTERNS should capture bare mentions** — add fallback patterns that extract without requiring a numeric value, or add a separate "metric mentioned" list
3. **Sentinel version specificity** — store as `SENTINEL-1`, `SENTINEL-2`, `SENTINEL-3` separately in KB
4. **Add top-level `study_type` and `task` fields** to the serialized paper JSON for consumer compatibility
5. **Section router: add rescue paths for `other`** — reclassify blocks containing study_area or results keywords
