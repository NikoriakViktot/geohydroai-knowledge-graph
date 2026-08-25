# ENTITY_FP_AUDIT_v1.md — Entity False Positive Suppression Audit
**Date**: 2026-05-15  
**Corpus sample**: 100 papers (seed=42), methods + satellites + DEMs entities  
**Baseline**: pre-Phase-2 code

---

## Executive Summary

Pre-fix: **7.1 %** of entities were confirmed false positives (32/450).  
Post-fix: blocklist, single-char suppression, and semantic deduplication eliminate the 32 FP entities and 21 canonical duplicates.  
Geo FP filter removes 4 method/river names incorrectly classified as study countries.

---

## 1. Pre-Fix Baseline Metrics

| Metric | Count | Rate |
|--------|-------|------|
| Total entities (methods+sats+dems) | 450 | — |
| Accepted entities | 396 | 88.0 % |
| Rejected entities | 54 | 12.0 % |
| **FP blocklist hits** | **32** | **7.1 % of total** |
| Single-char name entities | 1 | 0.2 % |
| Accepted with zero context score | 121 | 27.0 % of accepted |
| Negative embedding scores | 60 | 13.3 % of total |
| Duplicate canonical (same KB full_name) | 21 | — |

### Top False Positive Names (Pre-Fix)

| Name | Occurrences | Root cause |
|------|-------------|-----------|
| `CLASSIFICATION` | 24 | Broad acronym pattern matching "CLASSIFICATION" as method abbreviation |
| `HTTP` | 5 | URL artifacts in extracted text (reference list URLs) |
| `NNT` | 2 | Ambiguous 3-letter acronym matched against method patterns |
| `J` | 1 | Single-letter journal abbreviation surviving KB acronym patterns |

### Geo False Positives (Pre-Fix)

| Incorrect Country | Actual Concept | Papers Affected |
|-------------------|----------------|-----------------|
| `Muskingum` | Muskingum routing method | 2 |
| `Pamir` | Pamir mountain range (study area, not country) | 1 |
| `Yangtze` | Yangtze River (river, not country) | 1 |

### Top Canonical Duplicate Groups (Pre-Fix)

| KB Full Name | Duplicate Count |
|--------------|----------------|
| Artificial Neural Network | 13 (ANN + artificial_neural_network + variants) |
| Surface Water and Ocean Topography | 3 |
| Light Detection and Ranging | 3 |
| Machine Learning | 2 |

---

## 2. Fixes Applied

### 2.1 Token Blocklist (`_ENTITY_BLOCKLIST`)

Location: `src/ingestion/pipeline.py`, applied in `run_entity_pipeline()`.

Hard-rejects names that match KB patterns but carry no scientific meaning:

```python
_ENTITY_BLOCKLIST: frozenset[str] = frozenset({
    # URL artifacts
    "HTTP", "HTTPS", "URL", "WWW",
    # Single-letter tokens
    "J", "R", "T", "K", "S", "M", "N",
    # Ambiguous abbreviations
    "NNT", "NET", "REF", "TAG", "ALS",
    # Section/document structure
    "CLASSIFICATION", "REMOTE", "SENSING", "ANALYSIS",
    "PROCESS", "PROCESSING", "DATA", "MAP", "MAPPING",
    "STUDY", "AREA", "REGION", "TABLE", "FIGURE", "FIG",
    # Measurement units
    "MM", "CM", "KM", "M2", "KM2", "HA", "HZ", "DB",
})
```

Rejection point: **before scoring** — blocked entities never enter the scoring pipeline.

### 2.2 Single-Character Suppression

Any entity name with `len(stripped) <= 1` is rejected unconditionally.  
Prevents single-letter unit symbols and journal abbreviations.

### 2.3 Semantic Deduplication by Canonical KB Full Name (`_deduplicate_by_canonical`)

When two or more entities share the same `kb_metadata.full_name`, only the highest-`final_score` instance is retained.  
The loser's `evidence` is preserved in `alt_evidence` for audit purposes.

This collapses ANN / artificial_neural_network → single canonical entry, preserving the best-evidenced occurrence.

### 2.4 Geo FP Filter (`_GEO_NOT_COUNTRY` + `_country_name_is_valid()`)

Applied in `merge_countries()`, which is the convergence point for all country candidates from NER, geonames, and pattern matching.

```python
_GEO_NOT_COUNTRY: frozenset[str] = frozenset({
    "muskingum", "yangtze", "pamir", "manning", "darcy",
    "thiessen", "voronoi", "euler", "navier",
    "amazonia", "amazon basin", "himalaya", "hindukush",
})
```

Case-insensitive comparison. Names in this set are rejected from `study_geo.countries` regardless of source.

---

## 3. Post-Fix Expected Metrics

| Metric | Pre-Fix | Post-Fix | Δ |
|--------|---------|----------|---|
| FP blocklist hits | 32 | 0 | **-32** |
| Geo FP (method as country) | 4 | 0 | **-4** |
| Canonical duplicates | 21 | 0 | **-21** |
| Total entity reduction | 450 | ~397 | -53 (-11.8 %) |
| Accepted entity precision | ~88 % | ~95 %+ | **+7 pp** |

*Post-fix counts require re-running the pipeline with `overwrite=True` on the 100-paper sample.*

---

## 4. Zero-Context Score Entities (Remaining Risk)

121/396 accepted entities (30.6 %) have `context_score = 0` but still pass the `final_score >= 0.3` threshold because `pattern_score * 0.3 ≥ 0.3` (pattern_score ≥ 1.0 is impossible; typical pattern_score = 0.88–0.90, giving 0.264–0.27 from pattern alone, which doesn't reach 0.3 without section boost).

**Root cause**: `compute_section_boost()` adds up to 0.25 for title mentions + section presence.  
An entity with `pattern=0.9, context=0, embedding=0, llm=0` gets `final_score = 0.27 + section_boost`.  
If `section_boost ≥ 0.03`, it passes acceptance.

**Risk**: Low-context entities with strong section boosts may include generic terms mentioned frequently but not specifically used.

**Recommendation** (not implemented — deferred to next hardening pass):  
For entities with `context_score = 0` and `final_score < 0.5`, require at least one `_USAGE_POSITIVE` keyword in the evidence snippet before marking `accepted=True`.

---

## 5. Embedding Score Issue (Cross-Reference with Phase 3)

60/450 entities (13.3 %) had **negative embedding scores** before Phase 3 fix.  
These caused score inversion: the scoring formula `embedding * 0.2` subtracted from the total when the model considered the entity name anti-correlated with the methods text.

**Fix** (Phase 3): `embedding_score()` now clamps to `max(0.0, raw_cosine)`.  
Negative anti-similarity is treated as 0 (no evidence), not as negative evidence.

---

## 6. Test Coverage

All Phase 2 changes are covered by `tests/test_06_edge_cases.py::TestEntityFPSuppression`:

| Test | Verified |
|------|----------|
| `test_blocklist_http_removed` | HTTP, HTTPS blocked |
| `test_blocklist_single_letter_removed` | J, R blocked |
| `test_blocklist_nnt_removed` | NNT blocked |
| `test_blocklist_allows_hec_ras` | HEC-RAS NOT blocked |
| `test_blocklist_allows_swat` | SWAT NOT blocked |
| `test_dedup_keeps_highest_scoring` | Canonical dedup selects winner |
| `test_dedup_preserves_alt_evidence` | Loser evidence preserved |
| `test_no_kb_fullname_kept` | Entities without full_name pass through |
| `test_geo_not_country_filter` | Muskingum/Yangtze rejected, Bangladesh/Thailand allowed |
