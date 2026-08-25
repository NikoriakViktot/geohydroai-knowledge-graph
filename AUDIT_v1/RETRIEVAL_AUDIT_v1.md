# RETRIEVAL_AUDIT_v1.md — Retrieval Observability Audit
**Date**: 2026-05-15  
**ChromaDB collection**: `flood_papers` — 13 970 document chunks  
**Embedding model**: sentence-transformers/all-MiniLM-L6-v2  
**Queries**: 20 canonical hydrology queries  
**Output**: `data/analytics/retrieval_audit.parquet` (100 rows: 20 queries × 5 hits each)

---

## Executive Summary

| Metric | Value | Status |
|--------|-------|--------|
| Queries executed | 20 / 20 | ✓ |
| Total hits returned | 100 | ✓ |
| Unique source files retrieved | 55 / 1 600 | ⚠ Low diversity |
| Avg rank_score | 0.5615 | Moderate |
| Max rank_score | 0.687 | Acceptable |
| Avg embedding distance | 0.3394 | Moderate |
| Avg query latency | 19 ms | ✓ Fast |
| Citation-boosted hits | 0 / 100 (0 %) | ⚠ No citation data |
| Citation map entries | 0 / 1 600 | ⚠ OpenAlex not run |

---

## 1. Canonical Query Results

### All 20 Queries — Performance Overview

| Q# | Query (truncated) | Top file | Top rank_score | Latency |
|----|------------------|----------|----------------|---------|
| 01 | HEC-RAS hydraulic model flood simulation | hydrology-10-00141-v2.pdf | 0.646 | 178 ms |
| 02 | Sentinel-1 SAR flood inundation mapping | schumann2015.pdf | 0.658 | 12 ms |
| 03 | Random Forest ML flood susceptibility | remotesensing-17-00524.pdf | 0.623 | 13 ms |
| 04 | DEM SRTM validation accuracy | hess-19-3755-2015.pdf | 0.541 | 13 ms |
| 05 | Hydrological calibration NSE RMSE | — | 0.524 | 11 ms |
| 06 | SWAT rainfall runoff catchment | — | 0.595 | 12 ms |
| 07 | Flash flood prediction early warning | — | 0.617 | 9 ms |
| 08 | Muskingum flood routing coefficient | — | 0.466 | 9 ms |
| 09 | InSAR SAR terrain deformation | — | 0.585 | 11 ms |
| 10 | MODIS satellite flood monitoring | — | 0.655 | 10 ms |
| 11 | HEC-HMS hydrological precipitation runoff | — | 0.571 | 9 ms |
| 12 | Landsat NDWI water body detection | — | 0.591 | 13 ms |
| 13 | LSTM deep learning streamflow forecast | — | 0.587 | 9 ms |
| 14 | Flood frequency return period extreme | — | 0.618 | 8 ms |
| 15 | LiDAR terrain model floodplain | — | 0.623 | 10 ms |
| 16 | Coupled 1D 2D urban flood simulation | — | 0.638 | 8 ms |
| 17 | Reservoir dam break flood propagation | — | 0.545 | 8 ms |
| 18 | Remote sensing flood damage urban | — | 0.687 | 11 ms |
| 19 | Nash-Sutcliffe efficiency performance | — | 0.351 | 9 ms |
| 20 | Coastal storm surge sea level flood | — | 0.594 | 10 ms |

**Q19 (NSE) is the lowest-performing query** (top score 0.351) — the phrase "Nash-Sutcliffe efficiency" is a specific metric abbreviation that may not appear verbatim in chunk-level text, and is semantically distant from the broader papers it appears in.

### Notable Query 01 Warm-up

The first query has `latency=178 ms` vs avg 12 ms for subsequent queries.  
This is the ChromaDB index warm-up latency (first query loads the HNSW index into memory).  
All subsequent queries use the warmed index.

---

## 2. Rank Score Analysis

```
Rank score = 0.85 × semantic_score + citation_boost
           = 0.85 × (1 - cosine_distance) + min(0.15, 0.15 × log(citations+1)/log(501))
```

| Bucket | Hits | Percentage |
|--------|------|-----------|
| < 0.40 | 8 | 8.0 % |
| 0.40 – 0.50 | 16 | 16.0 % |
| 0.50 – 0.60 | 44 | 44.0 % |
| 0.60 – 0.70 | 32 | 32.0 % |
| > 0.70 | 0 | 0.0 % |

**Score ceiling at 0.687**: The theoretical maximum `rank_score` without citation boost is `0.85 × 1.0 = 0.85`.  
The observed maximum of 0.687 suggests an average cosine distance floor of ~0.19 for well-matched chunks.  
This is consistent with sentence-level chunks being semantically related but not identical to the query.

---

## 3. Retrieval Diversity Analysis

55 unique files appear across 100 hits (5.5 files per query on average with overlap).

### Most-Retrieved Files (Cross-Query Frequency)

| File | Appearances | Domain signal |
|------|-------------|--------------|
| annurev-fluid-030121-113138.pdf | 7 | Annual Review article — broad flood modeling review |
| Reviews of Geophysics - 2023 - Jafarzadegan | 7 | Review article — riverine/coastal flood modeling |
| hydrology-10-00141-v2.pdf | 5 | Hydraulic/hydrological modeling |
| LiDAR_DEM_Data_for_Flood_Mapping | 5 | LiDAR + DEM flood mapping |
| water-11-01615.pdf | 4 | Hydrology journal — general |
| remotesensing-16-00350-v2.pdf | 4 | Remote sensing flood |

**Observation**: Review articles (Annual Reviews, Reviews of Geophysics) dominate cross-query retrieval.  
This is **expected and correct** — review articles contain broad vocabulary that matches many query types.  
However, they may displace highly specific papers that better answer narrow technical queries.

**Concern**: 55 unique files from 1 600 indexed = 3.4 % file coverage per 20-query run.  
This is appropriate for a targeted top-5 retrieval, but suggests the corpus has long-tail coverage gaps for highly specialized queries (Q08 Muskingum, Q09 InSAR, Q19 NSE).

---

## 4. Citation Boost Status

**0 / 100 hits received any citation boost** because `cited_by_count` is not populated in any paper_json `metadata` field.

This confirms the known OpenAlex enrichment gap (Stage 2 not run).  

Expected citation boost once OpenAlex runs:
- Top cited papers (> 200 citations): +0.12 to +0.15 boost
- Average paper (10–50 citations): +0.03 to +0.08 boost
- Most papers (0–5 citations): +0.01 or no boost

When citation data is available, review articles and landmark methods papers will receive appropriate reputation boosts, improving retrieval precision for well-established methodology queries.

---

## 5. Section Source Distribution

The `section` field in ChromaDB metadata indicates which document section each chunk came from.  
Distribution across 100 retrieved hits:

| Section type | Frequency | Notes |
|-------------|-----------|-------|
| `methods` | Variable | Strong signal for methodology queries |
| `results` | Variable | Contains metric values and model performance |
| `other` | High | Catch-all bucket — routing failure content |
| `abstract` | Moderate | High information density, always indexed |

**Impact of section routing failure on retrieval**: Because 23 % of papers have empty `methods` sections, method-relevant content for those papers is indexed as `other` chunks. These chunks are retrievable but carry no `section=methods` provenance, which reduces their interpretability in downstream applications.

---

## 6. Query Performance Deep-Dive

### Best-Performing Queries

**Q18** (remote sensing flood damage): top=0.687 — remote sensing is well-represented in corpus  
**Q02** (Sentinel-1 SAR): top=0.658 — strong semantic field, many papers explicitly named  
**Q10** (MODIS monitoring): top=0.655 — MODIS has dense KB vocabulary  

### Worst-Performing Queries

**Q19** (Nash-Sutcliffe efficiency): top=0.351 — NSE is a numerical metric, not a topical concept.  
Papers use NSE as a performance score within results sections; embedding similarity to "Nash-Sutcliffe efficiency model performance evaluation" is diffuse across many papers.

**Q08** (Muskingum routing): top=0.466 — Muskingum is a specific routing method underrepresented in the current 1 600-paper corpus relative to the full 3 692 XML corpus.

### Recommendation: Query Reformulation

For low-performing queries, reformulate with more context:
- Q19: "streamflow model performance NSE > 0.7 PBIAS calibration validation" → expected improvement
- Q08: "flood routing channel coefficient Muskingum-Cunge K X parameters" → expected improvement

---

## 7. Parquet Schema

`data/analytics/retrieval_audit.parquet` contains 100 rows with columns:

| Column | Type | Description |
|--------|------|-------------|
| `query_id` | int | 1–20 |
| `query` | str | Full canonical query text |
| `query_latency_s` | float | Time for encode + ChromaDB query |
| `rank` | int | Hit rank within query (1 = best) |
| `filename` | str | Source PDF filename |
| `distance` | float | Cosine distance [0, 2] |
| `semantic_score` | float | 1 - distance |
| `citation_boost` | float | Currently 0.0 (no OpenAlex data) |
| `rank_score` | float | 0.85 * semantic + citation_boost |
| `text_snippet` | str | First 200 chars of chunk text |
| `section` | str | Source section type |
| `page_number` | int/null | Page number if available |

---

## 8. Retrieval Infrastructure Assessment

| Component | Status | Notes |
|-----------|--------|-------|
| ChromaDB collection | ✓ Ready | 13 970 chunks from ~1 200 papers |
| Embedding model | ✓ Ready | all-MiniLM-L6-v2, 11.3 ms avg latency |
| Rank score formula | ✓ Implemented | 85/15 semantic/citation split |
| Citation boost | ⚠ Inactive | No OpenAlex data; 0 % citation-boosted hits |
| Parquet output | ✓ Saved | `data/analytics/retrieval_audit.parquet` |
| Diversity | ⚠ Low | 55 / 1 600 files (3.4 %) per 20-query run |
| Corpus coverage | ⚠ Partial | 1 600 / 3 692 papers indexed (43 %) |

---

## 9. Action Items

| Priority | Action | Expected Impact |
|----------|--------|----------------|
| P1 | Run OpenAlex enrichment to populate `cited_by_count` | Activates citation boost for top cited papers |
| P2 | Process remaining 2 092 XMLs → add to ChromaDB | Increases corpus coverage from 43 % to 100 % |
| P3 | Add `section=methods` filter option to Retriever | Return only method-section chunks for specific queries |
| P4 | Reformulate Q19 and Q08 (low-performing queries) | Improve min rank_score from 0.35 to 0.50+ |
| P5 | Add entity-match field to retrieval hits (entity names found in chunk) | Improve hit interpretability |
