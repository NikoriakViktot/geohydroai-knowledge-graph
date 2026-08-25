# Ontology QA Audit v4
**Scope**: `data/ontology_qa/normalization_backfill_summary.json` and `data/ontology_qa/normalization_validation.json`  
**Date**: 2026-05-15  
**Question**: What does the QA suite actually test, what does it miss, and what blind spots exist?

---

## 1. QA File Overview

| File | Generated | Coverage |
|---|---|---|
| `normalization_backfill_summary.json` | 2026-05-07 | File-level pipeline success/failure |
| `normalization_validation.json` | 2026-05-12 | Unit test suite — canonical ID resolution |

---

## 2. `normalization_backfill_summary.json`

### 2.1 Content

- Processed: **3 095 entities** across all papers in 333 seconds (9.29 entities/sec)
- Result: **100% success** — 3 095 succeeded, 0 failed at any stage (load/normalize/save)

### 2.2 What "success" means here

This file tracks **file-level pipeline completion** — it records whether `process_paper()` could open, normalize, and save each paper JSON without raising an unhandled exception. A "success" means:

- The paper file was readable
- The normalization loop ran to completion
- The output file was written

### 2.3 What it does NOT track

- Whether any entity was actually normalized (a paper with 0 extracted entities counts as "success")
- Per-entity-type pass/fail breakdown (satellites vs methods vs metrics vs concepts)
- Confidence score distributions
- Whether `normalized_entities` is populated vs empty
- Whether disambiguation was triggered and resolved correctly
- Partial failures within a paper (e.g., 5 of 7 entities normalized, 2 silently dropped)

**This summary cannot distinguish between a paper where all entities normalized correctly and a paper where zero entities were extracted (and thus zero normalization was attempted).**

---

## 3. `normalization_validation.json`

### 3.1 Content

- Tests: **63 total, 63 passed**, 0 failed (100% pass rate)
- Generated: 2026-05-12

### 3.2 Test Coverage by Type

| Type ID | Test focus | Sample cases |
|---|---|---|
| T01 | Canonical-ID self-lookup | Entity resolves to itself |
| T02 | Alias/acronym resolution | Abbreviation → canonical |
| T03 | Phrase + trailing noun | Multi-word entity matching |
| T04 | Full-phrase aliases | Expanded name matching |
| T05 | Unknown/empty input | Graceful null handling |
| T06 | Type-filtered lookup | Scoped by entity type |
| T07 | Hyphenated models | HEC-RAS-1D/2D, MIKE-11/21/FLOOD, LISFLOOD-FP |
| T08 | Time-series models | SARIMA, Prophet, WA-LSTM, Mamdani-FIS, Takagi-Sugeno-FIS |
| T09 | Hybrid models | SARIMA-ANN, Wavelet-SVR-Prophet |
| T10 | Specialized models | SWMM, iRIC, TELEMAC-MASCARET |
| T11 | Metric + type filter | NSE, PBIAS, CSI, KGE |
| T12 | Task inference from coupled models | study_type inferred from method combination |

All test cases return confidence = **1.0** (alias/exact match) or **0.0** (unknown). No test exercises intermediate confidence values.

### 3.3 Blind Spots in the QA Suite

#### CRITICAL: Semantic path never tested

The `ontology_matcher.py` uses a semantic embedding path (`BAAI/bge-large-en-v1.5`) with:
- Accept threshold: **0.82**
- Uncertain threshold: **0.68**

No test in the suite exercises this path. Every test case matches by exact alias (confidence=1.0) or fails (confidence=0.0). The semantic path (which handles novel terminology and paraphrases) is completely untested.

#### HIGH: Disambiguation path never tested

The disambiguation path handles ambiguous tokens — SCS (hydrology vs remote sensing), ANN (general ML vs time-series), MLP, HEC-HMS vs HEC-RAS context confusion. No test in the suite triggers any disambiguation rule. This means:

- The 15 rules in `ontology_disambiguation_rules.json` are untested
- The `acronym_collisions` resolution logic is untested
- Context-dependent resolution (which requires surrounding text) is untested

#### HIGH: False positive confirmed — `gandaki` as country

`gandaki` (a river/region in Nepal) appears in `study_geo.countries` with confidence=0.55 in at least one paper. The `_GEO_NOT_COUNTRY` exclusion list contains only 10 terms: `muskingum, yangtze, pamir, manning, darcy, thiessen, voronoi, euler, navier, amazonia`. `gandaki` is not in this list, so the false positive is not suppressed. No test covers this case.

#### MEDIUM: Metric numeric value extraction not tested

T11 tests metric normalization (NSE→Nash-Sutcliffe, etc.) but does not test whether numeric values are correctly captured from text. The `METRIC_PATTERNS` regex requiring `\d+(?:\.\d+)?` after the metric name is not exercised in QA.

#### MEDIUM: Entity blocklist not exercised

The `_ENTITY_BLOCKLIST` (30 terms including "REMOTE", "SENSING", "ANALYSIS") is never tested. No test verifies that a paper mentioning "remote sensing" does not create a false entity `REMOTE_SENSING`.

#### LOW: Multi-language text not tested

GROBID sometimes outputs TEI with non-English section text (transliterated river names, author affiliations). The normalization pipeline is not tested on mixed-language input.

#### LOW: Confidence boundary cases not tested

Confidence values between 0.68 and 0.82 (the "uncertain" band of the semantic matcher) are never exercised. Behavior in the uncertain band (disambiguation triggered vs accepted at 0.68) is untested.

---

## 4. Coverage Matrix

| Extraction stage | Tested | Not tested |
|---|---|---|
| Exact alias lookup | YES (T01-T04, T07-T12) | – |
| Acronym collision resolution | NO | Completely untested |
| Semantic embedding path | NO | All cases untested |
| Disambiguation by context | NO | All 15 rules untested |
| False positive suppression | NO | Untested |
| Metric value capture regex | NO | Not tested |
| Entity blocklist | NO | Not tested |
| Geographic false positives | NO | gandaki confirmed FP |
| Study_type detection | NO | No unit tests |
| Task classification | NO | No unit tests |
| Empty/degenerate sections | Partial (T05) | Partial null case only |

---

## 5. QA Gap Risk Assessment

| Gap | Risk | Papers affected |
|---|---|---|
| Semantic path untested | HIGH — semantic matching is the fallback for all novel entities | All papers with non-standard terminology |
| Disambiguation untested | HIGH — SCS, ANN, MLP, HEC-RAS 1D/2D ambiguous in 20%+ of corpus | ~10-15 papers |
| gandaki FP | MEDIUM — inflates country count | Papers with Gandaki basin |
| Metric pattern untested | MEDIUM — NSE extraction failure visible in production | 52/52 papers (0% NSE extracted) |
| study_type at wrong path | MEDIUM — consumers get None | All downstream consumers |

---

## 6. Summary

The QA suite achieves 100% pass rate across 63 tests, but tests only the **exact-match path** of normalization. The two most impactful resolution paths — semantic embedding and disambiguation — are completely untested. The backfill summary's 100% success rate is a measure of pipeline stability, not normalization quality. A paper that extracts 0 entities and normalizes 0 entities is indistinguishable from a paper that normalizes 20 entities correctly.
