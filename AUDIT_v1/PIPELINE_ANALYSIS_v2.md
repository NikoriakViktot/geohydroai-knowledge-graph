# GeoHydroAI Pipeline Baseline — v2
**Date**: 2026-05-14  
**Corpus**: 20 papers (data/literature/paper_json/ + data/literature/grobid_xml/)  
**Pipeline revision**: post-5-blocker-fix  
**Fixes applied**: TEI section extraction, embedding_matcher fallback, CITES wiring, judge debug, citation retrieval

---

## 1. Section Extraction (SDOM path)

> Source: `_sections_from_doc()` on each paper's matching `.tei.xml`

| Metric | Value |
|--------|-------|
| XMLs matched | 20 / 20 |
| Papers with `methods` > 50 chars | **18 / 20** |
| Papers with empty `methods` | 2 / 20 |

### Per-paper section character counts

| Paper (stem) | abstract | intro | methods | results | total |
|---|---|---|---|---|---|
| 0000577383 | 1542 | 2519 | 14677 | 2724 | 169381 |
| 0030 | — | — | 15365 | — | 32241 |
| 010064143 | — | — | 4350 | — | 46523 |
| 010079873 | — | — | 12879 | — | 37727 |
| 0115-0128 | — | — | 16632 | — | 31952 |
| 0229 | — | — | 8852 | — | 20783 |
| **03-04-FebEDUSAT** | — | — | **0** | — | 12204 |
| 03-Accuracy-Muskingum | — | — | 1793 | — | 18143 |
| **032003_1** | — | — | **0** | — | 53089 |
| 034505_1 | — | — | 1020 | — | 42203 |
| 0399-0405 | — | — | 6204 | — | 18443 |
| 04_prasanchum | — | — | 3434 | — | 23236 |
| 060007_1_online | — | — | 785 | — | 19211 |
| 06_16 | — | — | 5799 | — | 23054 |
| 07_torma_et_al | — | — | 12414 | — | 25482 |
| 08861302 | — | — | 9014 | — | 47416 |
| 1-Hydrological-HEC-HMS | — | — | 4483 | — | 26895 |
| 1-s2.0-S0022169418305407 | — | — | 28531 | — | 57543 |
| 1-s2.0-S0022169421005527 | — | — | 29405 | — | 70844 |
| 1-s2.0-S0022169421007320 | — | — | 10028 | — | 38261 |

### Papers with methods = 0 — root cause

| Paper | Root cause |
|---|---|
| `03-04-FebEDUSAT_Introduction-to-Microwave-Rem` | Educational survey; no methodology section exists in the document |
| `032003_1` | InSAR paper; section headers ("Design Considerations", "Study Events") don't contain method-keywords — text accumulates in `other`. Consider adding "design", "approach" to `section_tags()` |

---

## 2. Entity Extraction

> Source: saved `*.paper.json` in `data/literature/paper_json/`

| Metric | Count | Coverage |
|--------|-------|----------|
| Total methods extracted | 57 | — |
| Total metrics extracted | 58 | — |
| Total satellites extracted | 24 | — |
| Total DEMs extracted | 12 | — |
| Papers with ≥1 method | **17 / 20** | 85 % |
| Papers with ≥1 metric | **9 / 20** | 45 % |
| Papers with ≥1 satellite | 8 / 20 | 40 % |
| Papers with ≥1 DEM | 8 / 20 | 40 % |

### Per-paper entity breakdown

| Paper | methods | satellites | DEMs | metrics |
|---|---|---|---|---|
| 0000577383 | 0 | 0 | 0 | 0 |
| 0030 | 8 | 0 | 1 | 0 |
| 010064143 | 6 | 3 | 0 | 25 |
| 010079873 | 5 | 2 | 0 | 1 |
| 0115-0128 | 2 | 0 | 0 | 0 |
| 0229 | 6 | 1 | 2 | 6 |
| 03-04-FebEDUSAT | 0 | 3 | 0 | 0 |
| 03-Muskingum | 1 | 0 | 0 | 0 |
| 032003_1 | 3 | 5 | 2 | 0 |
| 034505_1 | 0 | 4 | 1 | 2 |
| 0399-0405 | 1 | 0 | 0 | 0 |
| 04_prasanchum | 1 | 0 | 0 | 1 |
| 060007_1_online | 1 | 1 | 0 | 0 |
| 06_16 | 1 | 0 | 0 | 0 |
| 07_torma_et_al | 2 | 0 | 2 | 0 |
| 08861302 | 10 | 5 | 0 | 1 |
| 1-Hydrological-HEC-HMS | 2 | 0 | 2 | 4 |
| 1-s2.0-S0022169418305407 | 2 | 0 | 1 | 11 |
| 1-s2.0-S0022169421005527 | 4 | 0 | 0 | 0 |
| 1-s2.0-S0022169421007320 | 2 | 0 | 1 | 7 |
| **TOTAL** | **57** | **24** | **12** | **58** |

### Known gaps
- `0000577383`: 0 entities — paper is in Spanish (GROBID extracts but entity extractor regex is English-only)
- 3 papers have 0 methods: one Spanish, one educational survey, one InSAR (methods in "other" bucket)
- Metrics coverage 45 % — extractor is regex/rule-based; improves after section fix propagates to re-parsed JSONs

---

## 3. LLM Judge Analysis

> `needs_judge()` evaluated on all 20 saved paper JSONs

| Metric | Value |
|--------|-------|
| needs_judge = True | **19 / 20** (95 %) |
| needs_judge = False | 1 / 20 |

### Trigger breakdown

| Trigger reason | Count |
|---|---|
| `study_type.needs_judge=True` (classifier set flag) | 8 |
| DEMs present in entities | 5 |
| `label=multi_site` with specific geo data | 2 |
| `study_geo.confidence` < 0.75 | 3 |
| Accepted rivers + study_geo.confidence < 0.9 | 1 |
| No judge needed | 1 |

### Per-paper judge decision

| Paper | Judge | Trigger |
|---|---|---|
| 0000577383 | **YES** | study_type.needs_judge=True (label=regional) |
| 0030 | **YES** | DEMs present: ['DEM'] |
| 010064143 | **YES** | label=multi_site with specific geo data |
| 010079873 | no | passed all checks |
| 0115-0128 | **YES** | study_type.needs_judge=True (label=case_study) |
| 0229 | **YES** | DEMs present: ['DEM', 'SRTM'] |
| 03-04-FebEDUSAT | **YES** | study_type.needs_judge=True (label=regional) |
| 03-Muskingum | **YES** | study_type.needs_judge=True (label=case_study) |
| 032003_1 | **YES** | label=multi_site with specific geo data |
| 034505_1 | **YES** | DEMs present: ['DEM'] |
| 0399-0405 | **YES** | study_geo.confidence=0.40 < 0.75 |
| 04_prasanchum | **YES** | study_type.needs_judge=True (label=case_study) |
| 060007_1_online | **YES** | study_type.needs_judge=True (label=case_study) |
| 06_16 | **YES** | 1 accepted rivers but study_geo.confidence=0.75 < 0.9 |
| 07_torma_et_al | **YES** | DEMs present: ['DEM', 'DTM'] |
| 08861302 | **YES** | study_geo.confidence=0.60 < 0.75 |
| 1-Hydrological-HEC-HMS | **YES** | DEMs present: ['DEM', 'DTM'] |
| 1-s2.0-S0022169418305407 | **YES** | study_type.needs_judge=True (label=case_study) |
| 1-s2.0-S0022169421005527 | **YES** | study_geo.confidence=0.65 < 0.75 |
| 1-s2.0-S0022169421007320 | **YES** | study_type.needs_judge=True (label=case_study) |

### Notes
- 95 % judge rate is **expected** for hydrology papers — they inherently contain DEMs, site-specific geo, and uncertain study_type labels.
- The `dems` trigger is very broad (any DEM mentioned → judge). Consider raising threshold to "DEM + satellite" only (already has a compound check that's currently unreachable due to the preceding single-DEM check).
- Ollama is not running in the current environment → all judge calls skip with `status=skipped`.

---

## 4. CITES Edges

> `load_cites_edges()` from all 20 paper JSONs (references at `doc["references"]`)

| Metric | Value |
|--------|-------|
| Total CITES candidates | **898** |
| With DOI (authoritative stub) | **332** (37 %) |
| Title-only fallback | **566** (63 %) |
| Average refs per paper | 44.9 |

### Per-paper reference counts (sorted by total)

| Paper | refs | w/ DOI |
|---|---|---|
| 1-s2.0-S0022169421005527 | 109 | 93 |
| 1-s2.0-S0022169421007320 | 99 | 28 |
| 034505_1 | 83 | 61 |
| 1-s2.0-S0022169418305407 | 69 | 0 |
| 010064143 | 68 | 4 |
| 010079873 | 65 | 58 |
| 032003_1 | 63 | 49 |
| 08861302 | 58 | 1 |
| 0030 | 56 | 17 |
| 0000577383 | 45 | 0 |
| 0115-0128 | 30 | 0 |
| 060007_1_online | 28 | 17 |
| 0399-0405 | 25 | 0 |
| 04_prasanchum | 24 | 4 |
| 06_16 | 22 | 0 |
| 07_torma_et_al | 19 | 0 |
| 0229 | 17 | 0 |
| 1-Hydrological-HEC-HMS | 10 | 0 |
| 03-Muskingum | 8 | 0 |
| 03-04-FebEDUSAT | — | — |

### Notes
- Fix: `_iter_enriched()` now falls back to `data/literature/paper_json/` when `data/enriched/` is empty.
- Fix: references read from `doc["references"]` (top-level) not `doc["paper"]["references"]` (wrong nested path).
- DOI coverage: papers from journals.elsevier.com tend to have DOIs; conference papers and reports do not.
- `1-s2.0-S0022169418305407` has 69 refs but 0 DOIs — GROBID failed to extract DOIs from its reference list.

---

## 5. Citation-Weighted Retrieval

> `retriever.py` — `rank_score = 0.85 × (1 - distance) + citation_boost`

| Metric | Value |
|--------|-------|
| `rank_score` field present on every hit | **YES** |
| Papers with `cited_by_count` in metadata | **0 / 20** |
| Current citation boost | 0.0 (uniform) |

### Status
- Infrastructure implemented and tested. Formula: `citation_boost = min(0.15, 0.15 × log1p(cited) / log1p(500))`.
- Citation counts will populate once OpenAlex enrichment (Stage 2) runs. At 500 citations, a paper gets the full +0.15 boost over a purely semantic score.
- Retrieval is currently sorted by `rank_score` descending (was: ascending distance), so the formula is active.

---

## 6. Open Issues & Next Steps

### P1 — Run OpenAlex enrichment (Stage 2)
- `data/enriched/` is empty → no citation counts, no author/institution edges in Neo4j.
- Run: `python -m src.enrichment.openalex_enrichment --input data/literature/paper_json/`
- After enrichment, re-run graph build for `cited_by_count` to populate citation boosts.

### P2 — Extend `section_tags()` keywords
- Add `"design"`, `"approach"`, `"framework"`, `"calibration"` to the `methods` keyword set.
- This will recover the `032003_1` InSAR paper's method section.

### P3 — Re-parse & re-save all 20 paper JSONs
- The saved paper JSONs were built before the `_sections_from_doc()` fix.
- Re-run: `python -m src.ingestion.pipeline` to regenerate with corrected sections.
- Expected improvement: `0000577383` (Spanish) still 0 entities; others should gain method text.

### P4 — DEM judge trigger too broad
- Currently: any single DEM triggers LLM judge.
- Proposed: only trigger when `dems AND satellites` (the compound check already exists but is unreachable after single-DEM check).
- Will reduce judge rate from 95 % → ~60 %.

### P5 — Run normalization runner
- `data/normalized/` is empty → `load_entity_edges_from_enriched()` returns empty list.
- Run: `python -m src.orchestration.normalization_runner` after enrichment.
- Entity edges (Paper→Method, Paper→Sensor, Paper→Metric) in Neo4j require this step.

---

## Baseline Summary

| Check | Status | Value |
|---|---|---|
| Section extraction — methods non-empty | ✓ | 18/20 (was: ~0/20 before fix) |
| Entity extraction — papers w/ methods | ✓ | 17/20 |
| Entity extraction — papers w/ metrics | ✓ | 9/20 |
| LLM judge — debug visibility | ✓ | structured per-check logging |
| CITES edges — total loaded | ✓ | 898 (was: 0 before fix) |
| CITES edges — with DOI | ✓ | 332 |
| Citation map — populated | ⚠ | 0/20 (awaiting OA enrichment) |
| rank_score field on hits | ✓ | always present |
| embedding_matcher graceful fallback | ✓ | no ValueError crash |
| `_iter_enriched()` directory fallback | ✓ | uses paper_json when enriched empty |
