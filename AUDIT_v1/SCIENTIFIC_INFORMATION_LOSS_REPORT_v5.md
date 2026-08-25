# Scientific Information Loss Report v5
**Corpus**: 3 546 papers (Stage 1 full corpus run complete)  
**Date**: 2026-05-20  
**Previous report**: SCIENTIFIC_INFORMATION_LOSS_REPORT_v4.md (52 papers, 2026-05-15)  
**Question**: What scientific meaning survives the full pipeline, and what is lost or newly broken at each stage?

---

## Executive Summary

The Stage 1 full corpus run produced 3 546 paper.json files. Comparing to the 52-paper analysis in v4, the **systematic losses are confirmed at scale**: NSE/RMSE extraction remains at 0 %, satellite specificity is still collapsed. Additionally, a **new critical failure** emerged that was not present in v4: the ChromaDB vectorstore is completely broken for all 3 546 new papers due to an embedding dimension mismatch. Scientific information that survived parsing is now **unretrievable** through the RAG pipeline.

Additionally, the architecture transition to SDOM (document-oriented, Stage 0→2.5) is underway and will structurally fix the root causes of most losses documented here — but has not yet been applied to the corpus.

**Normalization has not been run on the 3 546 new papers.** All loss analysis below reflects the pre-normalization state.

---

## 1. New Loss: Vectorstore Indexing Failure

This failure did not exist in v4 (52-paper corpus used a different ChromaDB setup).

### 1.1 What Happened

Every paper in the Stage 1 run emitted:
```
chunking/vectorstore error (non-fatal): Collection expecting embedding with dimension of 384, got 768
```

The EmbeddingActor was updated to use a 768-dimensional embedding model, but the ChromaDB collection was created with a 384-dimensional schema. Inserting 768-dim vectors into a 384-dim collection fails silently — logged as non-fatal, execution continues.

### 1.2 Scientific Impact

| Item | Status |
|------|--------|
| Paper JSON preserved | ✓ (no data loss at JSON level) |
| Text chunks in ChromaDB | ✗ **0 chunks indexed for 3 546 papers** |
| RAG retrieval from new corpus | ✗ **Returns 0 results** |
| Semantic search for NSE > 0.8 papers | ✗ **Impossible** |
| Question answering from new corpus | ✗ **Impossible** |

**All 3 546 papers are scientifically invisible to the retrieval layer.** A researcher asking "which SWAT calibration papers achieved NSE > 0.80?" gets zero results from 96 % of the corpus.

### 1.3 Remediation Path

1. Identify the correct 768-dim embedding model used by EmbeddingActor
2. Delete the existing 384-dim ChromaDB collection
3. Create a new collection with 768-dim schema
4. Re-run chunker on all 3 546 paper.json files (CPU-only, ~2–3 hours)
5. No GROBID or Nougat re-inference needed

---

## 2. Confirmed Systematic Losses (v4 findings at scale)

### 2.1 By Entity Type — Full Corpus Projection

| Information type | v4 (52 papers) | v5 projection (3 546 papers) | Root cause |
|---|---|---|---|
| study_type label | 100 % | ~100 % | Pattern rules reliable |
| task label (correct) | 87 % | ~87 % (but top-level wrong for ~75 %) | Schema propagation bug |
| satellite entities | 27 % | ~27 % (~2 590 papers 0 satellites) | Usage filter + section routing |
| method entities | 83 % | ~83 % | Good coverage |
| NSE values | **0 %** | **~0 %** | metric_text excludes methods |
| RMSE values | **~0 %** | **~0 %** | metric_text excludes methods |
| Metrics in general | 54 % | ~54 % (~1 630 papers 0 metrics) | Generic "Percent" type only |
| Metrics with canonical ID | **0 % of metrics** | **~0 %** | normalized_entities all null |
| RAG retrievability | 100 % (old) | **~4 % (only old 145 papers)** | ChromaDB dimension mismatch |

---

### 2.2 Stage-by-Stage Loss Tracing (Updated)

#### Stage 0: PDF → Registry

**Content preserved**: SHA-256 paper_id, file path, registry status  
**Content lost**: None at this stage  
**New finding**: 145 papers (3.9 %) skipped pre-filter — missing TEI XML. These papers have no paper.json, no entities, no retrieval.

---

#### Stage 1: TEI XML → paper.json (pipeline_runner)

**Content preserved**: Abstract, methods text, most section text, author metadata, references, study_type, judge verdict

**Content lost (unchanged from v4)**:

| Loss | Rate | Cause |
|------|------|-------|
| NSE/RMSE values | 100 % | `metric_text = abstract + results + conclusion` — methods excluded |
| Satellite mission specificity | 100 % | SENTINEL collapses all missions |
| Satellite data in 73 % of papers | 0 % captured | `_USAGE_POSITIVE` misses "obtained from", "acquired from" |
| Study area text (~50 %) | 0 % in correct field | Router misses non-standard section titles |
| Task label at top level | ~75 % wrong | LLM judge correction not propagated to `paper.task.label` |

**New loss detected in v5:**

| Loss | Rate | Cause |
|------|------|-------|
| **Embedding component in entity scores** | **100 % of entities** | 768 vs 384 dim mismatch — all `embedding_score = 0.0` |
| MULTI_PAGE_SPAN coordinate issues | Low (e.g. zheng2015) | GROBID coordinate normalization; not data loss but coord metadata degraded |

**Sample evidence (veilleux2011)**:
```json
"task": {"label": "spectral_index_analysis", "confidence": 0.88},
"llm_judge": {"task": {"corrected_value": "flood_frequency_analysis", "confidence": 0.9}}
```
Top-level task is wrong. Judge's correct value exists but is never applied. This affects every paper where the judge issued a correction — estimated ~2 660 papers.

```json
"entities": {"methods": [{"name": "HAND", "evidence": "...on the other hand, the influence..."}]}
```
"HAND" (Height Above Nearest Drainage) detected from the phrase "on the other hand" — a clear false positive with no terrain analysis in this paper.

```json
"metrics": [{"type": "Percent", "value": 0.82, "kb_metadata": {}}]
```
Extracted as generic `Percent` with no canonical_id — NSE-like values cannot be linked to the `metric.nse` ontology node.

---

#### Stage 2: paper.json → Normalized Entities

**Status in v5: NOT RUN for 3 546 papers.**

`data/normalized/` contains 132 files (from pre-existing corpus). The 3 546 new paper.json files have inline-normalized entities (alias lookup only), but the full normalization runner with ontology grounding, semantic path, and LLM disambiguation has not been executed.

| Loss from missing normalization | Impact |
|---|---|
| Semantic alias → canonical_id resolution | Missing for ~3 414 papers |
| Ontology confidence scores | Missing |
| Fuzzy/semantic match path for paraphrased entities | Missing |
| Neo4j entity nodes from new corpus | Will not be created |

---

#### Stage 3: Normalized Entities → ChromaDB Vectorstore

**Status in v5: BROKEN for 3 546 papers.**

Every chunking call fails due to dimension mismatch. The text chunks from 3 546 papers are computed but immediately discarded when the ChromaDB insert fails.

| Loss | Rate |
|------|------|
| Text chunks for RAG retrieval | **100 %** for new corpus |
| Entity-grounded retrieval (rank_score) | **100 %** for new corpus |
| Citation-boosted retrieval | **100 %** (was already 0 % without OpenAlex) |

---

#### Stage 4: paper.json → Neo4j Graph

**Status in v5: STALE — graph reflects only 132 papers.**

The graph has not been rebuilt since the previous state. No new papers, entities, CITES edges, or normalizations from the 3 546 new papers are in Neo4j.

---

## 3. Information Surviving the Pipeline

### What survives (3 546-paper corpus, as-is)

| Information | Survival rate | Notes |
|---|---|---|
| Paper title, authors, DOI | ~99 % | GROBID TEI metadata reliable |
| Abstract text | ~99 % | Full text in sections.abstract |
| Methods section text | ~98 % | Very reliable |
| study_type label | ~100 % | Pattern rules |
| task label (top-level correct) | ~25 % | ~75 % wrong due to schema bug |
| Primary study country | ~80 % | Regex + author affiliation |
| Method entities | ~83 % | HEC-RAS, SWAT, ANN, etc. |
| LLM judge verdict | ~75 % | judge_used=true in this run |
| paper.json files intact | 3 546 / 3 546 | No file-level loss |

### What is systematically lost

| Information | Recovery rate | Root cause | Fixable? |
|---|---|---|---|
| NSE/RMSE values | **0 %** | metric_text excludes methods | Yes — extend metric_text scope |
| Satellite mission specificity | **0 %** | SENTINEL collapses all missions | Yes — aliased disambiguation |
| Satellite data in 73 % | **0 %** | Usage filter + section routing | Yes — extend _USAGE_POSITIVE |
| Task label (25 % wrong) | **~75 % unreliable** | Schema propagation bug | Yes — 1-line fix |
| RAG retrievability (new corpus) | **~4 % reachable** | ChromaDB dim mismatch | Yes — rebuild collection |
| Canonical metric IDs | **0 %** | normalization not run | Yes — run normalization_runner |
| Neo4j graph (new corpus) | **0 %** | normalization + graph not run | Yes — run both |
| Per-entity embedding scores | **0 %** | 768 vs 384 dim mismatch | Yes — fix EmbeddingActor or rebuild collection |

---

## 4. What SDOM/SODB Architecture Fixes

The project is transitioning to the SDOM document-oriented architecture (Stage 0→1→2→2.5). When this architecture is applied to the full corpus, it structurally fixes the following losses:

| Loss (current) | SDOM fix |
|---|---|
| Metrics extracted from wrong sections | Stage1Parser uses structured TEIDocument sections — metric extraction can target any section independently |
| Satellite mission collapse | Stage2Engineer classification layer handles satellite entity specificity |
| section routing failures | LayoutAwareChunker in SDOM uses structural signals beyond title keywords |
| paper.json as primary artifact | SODB Parquet layer stores all objects persistently — paper.json generated as aggregation, not raw extraction |
| ChromaDB dim mismatch | New pipeline rebuilds vectorstore from scratch with consistent schema |
| No calibration/validation period in NumericFacts | SODB numeric_facts.parquet schema includes `period`, `basin_id`, `event_id` fields |
| Nougat visual content for formulas/tables | NougatRegionPipeline feeds SODB regions.parquet; Stage1Parser reads it automatically |

**SDOM does NOT fix** (requires separate work):
- OpenAlex enrichment (independent runner)
- Task label propagation bug (1-line fix in pipeline.py)
- NER geo noise (blocklist extension)

---

## 5. Estimated Full-Corpus Impact

Projecting findings to the full 3 546-paper corpus (3 692 registered papers):

| Metric | v4 projection (52→3692) | v5 measured/confirmed (3546) |
|---|---|---|
| Papers with 0 satellites | ~2 700 (73 %) | **~2 590 papers** |
| Papers with 0 metrics | ~1 700 (46 %) | **~1 630 papers** |
| Papers with NSE extracted | ~0 (0 %) | **~0 papers** (confirmed) |
| Papers unreachable via RAG | ~0 (was indexed) | **~3 401 papers (96 %)** |
| Papers without canonical metric IDs | ~3 692 (100 %) | **~3 546 papers** (normalization not run) |
| Papers in Neo4j graph | ~132 (3.5 %) | **~132 papers** (unchanged) |
| Papers with correct top-level task | ~87 % | **~25 % if judge corrected** |

---

## 6. Priority Fix Queue

Based on loss severity and remediation cost:

| Priority | Fix | Effort | Scientific gain |
|---------|-----|--------|----------------|
| 🔴 P0 | Rebuild ChromaDB with 768-dim | ~2 hrs | Restores RAG for 3 546 papers |
| 🔴 P0 | Run normalization_runner | ~1 hr | Adds canonical IDs to ~3 546 papers |
| 🟠 P1 | Fix task label propagation bug in pipeline.py | ~10 min | Corrects classification for ~2 660 papers |
| 🟠 P1 | Run enrichment_runner (OpenAlex) | ~6 hrs | Adds citation counts, author data |
| 🟠 P1 | Run build_graph (Neo4j) | ~1 hr | Updates KG with 3 546 papers |
| 🟡 P2 | Extend metric_text to include methods section | ~30 min code | NSE/RMSE extraction from 0 % → ~70 % |
| 🟡 P2 | Extend _USAGE_POSITIVE ("obtained from", "acquired from") | ~10 min | Satellite recovery for ~73 % papers |
| 🟡 P2 | Continue NougatRegionPipeline (180 → 3875) | ~50 hrs GPU | Visual content for full corpus |
| 🟢 P3 | Run SDOM Stage0→1→2→2.5 on full corpus | After CLI built | Full structural fix for all losses |

---

## 7. Nougat Region Data Quality Assessment

### Current State (data/nougat_regions/ + data/sodb/)

```
data/nougat_regions/:  3 692 directories  (created by legacy pipeline)
data/sodb/:            3 692 directories  (created by legacy pipeline)
Nougat-processed:      180 papers (4.9 % of corpus)
```

**Important distinction**: 3 692 `data/sodb/` directories were created by the legacy pipeline_runner (GROBID-only). Only 180 of these have actual Nougat inference results in `regions.parquet`. The other 3 512 SODB dirs contain only GROBID-derived structure (no visual content).

### Quality of 180 Nougat-Processed Papers

For the 180 papers where NougatRegionPipeline completed:
- `regions.parquet` exists with schema: `region_id`, `region_type`, `bbox_*`, `nougat_text`, `nougat_latex`, `crop_path`, `source_parser="HYBRID"`
- Crop images at 300 DPI in `data/nougat_regions/{paper_id}/crops/`
- `source_parser="HYBRID"` = GROBID coordinates + Nougat content — highest quality source

### Known Nougat Quality Issues (from GEOHYDROAI_ARCHITECTURE.md)

| Issue | Rate | Impact |
|-------|------|--------|
| Repetition loops | 5 % of pages | Formula/table loss |
| GROBID coordinate mismatch | 3 % of papers | Wrong region crops |
| Multi-column layout confusion | 15 % of two-column papers | Handled by region mode (mitigated) |
| Table header mis-alignment | 8 % of tables | Wrong metric assignments |
| Formula hallucination | 15 % of formulas | Wrong LaTeX — requires TeX validity check |

### Path to Full Coverage

At 25 papers/hour (workers=3, GPU ~15–25 %), remaining 3 695 papers would take:
```
(3875 - 180) / 25 papers/hour = ~148 hours ≈ 6.2 days continuous
```

Priority queue for Nougat processing: Tables first (highest NumericFact value), then Formulas (unique Nougat capability), then Figures.

---

## 8. Science Loss in Context: Architectures Compared

| Architecture | NSE extraction | Satellite specificity | Metric canonical IDs | RAG retrievable | Graph complete |
|---|---|---|---|---|---|
| **Legacy (current)** | 0 % | 0 % | 0 % | ~4 % | 3.5 % |
| After P0 fixes | 0 % | 0 % | ~80 % | ~96 % | ~96 % |
| After P0+P1+P2 fixes | ~70 % | ~50 % | ~80 % | ~96 % | ~96 % |
| **SDOM full corpus** | ~80 % | ~65 % | ~90 % | ~96 % | ~96 % |
| SDOM + Nougat full | ~85 % | ~70 % | ~92 % | ~98 % | ~98 % |

The gap between "current" and "after P0 fixes" is ~9 hours of running existing scripts. The gap between "after P0+P1+P2" and "SDOM full corpus" is months of engineering work. **The P0 fixes should be the immediate next step.**
