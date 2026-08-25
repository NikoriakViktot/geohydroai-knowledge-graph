# PIPELINE_TRUST_REPORT_v2.md — GeoHydroAI Scientific Trust Report
**Date**: 2026-05-20  
**Pipeline version**: post-stage1-full-corpus  
**Corpus**: 3 546 paper_json / 6 014 TEI XML / 3 692 registry entries  
**Sample**: veilleux2011, 20537312, The_Flood_Resilience_Rose (random draw) + log analysis  
**Previous report**: PIPELINE_TRUST_REPORT_v1.md (2026-05-15, score 6.8/10)

---

## Executive Summary

Stage 1 (pipeline_runner) completed a full corpus run: **3 546 papers processed in 18 hours 7 minutes** (18.4 s/paper average, 3 workers). This is the largest single pipeline run to date — an increase from 145 → 3 546 paper_json files.

However, two **silent critical failures** occurred that are not visible in the run summary:

1. **ChromaDB embedding dimension mismatch** — `Collection expecting embedding with dimension of 384, got 768` logged as non-fatal for every paper. All 3 546 new papers failed vector indexing. Semantic retrieval is broken for the new corpus.
2. **Normalization not run** — 3 546 paper.json files exist but `normalization_runner` has not been executed. Normalized entity edges are missing for 97 % of the corpus.

The trust score regresses from 6.8 → **5.9 / 10** despite full corpus coverage, driven by these regressions.

**Overall Scientific Trust Score: 5.9 / 10**

---

## 1. Dimension Scores

### 1.1 Section Integrity — 5.5 / 10

| Check | Status |
|-------|--------|
| Abstract non-empty | ~99 % (GROBID reliable) |
| Methods section non-empty | ~98 % (stable) |
| Methods content correctly routed | ~28 % (unchanged bottleneck) |
| Study area extraction | ~50 % empty (unchanged) |
| `other` section dominance | ⚠ avg ~16K chars — unclassified |
| Results section | ~48 % empty (unchanged) |
| Section reclassification active | ✓ but limited |

**No improvements since v1.** The 72 % methods-in-`other` problem, the 50 % empty study_area, and the results under-extraction remain unchanged. With 3 546 papers this affects thousands of documents.

**Bottleneck**: Section router keyword set has not been expanded since v1. Extended keywords wired but `_reclassify_from_other()` only partially covers the diversity of non-standard section headings across 3 546 papers.

---

### 1.2 Entity Precision — 6.0 / 10

| Check | Status |
|-------|--------|
| FP blocklist active | ✓ |
| Geo FP (method as country) | ✓ Fixed (Phase 2 filter) |
| Canonical deduplication | ✓ |
| Zero-context accepts | ~30 % (unchanged) |
| **Embedding score = 0.0 for ALL entities** | ⚠ **NEW REGRESSION** |
| NER geo noise (Bulletin, LP3 as countries) | ⚠ persists at 0.55 conf |
| Method FP ("HAND" from "on the other hand") | ⚠ seen in samples |

**Critical regression — Embedding Dimension Mismatch:**  
The entity scoring formula is:
```
final_score = pattern×0.3 + context×0.3 + embedding×0.2 + llm×0.2
```
The `embedding` component produces `0.0` for all entities due to the `768 vs 384` dimension mismatch. 20 % weight is permanently wasted. This is the same problem as the ChromaDB indexing failure — the EmbeddingActor was rebuilt with a 768-dim model but ChromaDB collections were created expecting 384-dim. Entity confidence scores are systematically underestimated.

**In veilleux2011 sample**: River entity "Kern" has `embedding: 0.0`, `final_score: 0.39`. With correct embedding it would score higher.

**Score reduced from 7.5 → 6.0** due to embedding regression.

---

### 1.3 Ontology Consistency — 7.5 / 10

| Check | Status |
|-------|--------|
| v2 ontology migration | ✓ 46 models, 1 118 entities, 3 161 aliases |
| Disambiguation rules active | ✓ |
| Canonical IDs assigned | ✓ |
| Metrics normalized to canonical | ⚠ "Percent" → canonical_id=null (unchanged) |
| Normalized entities for new 3546 papers | ✗ normalization_runner NOT RUN |
| `schema_version: 1.0` on all papers | ✓ |

**Normalization runner not executed.** The 3 546 new papers have `normalized_entities` fields populated by the inline normalizer in pipeline.py (alias lookup only), but the full `normalization_runner` with ontology grounding has not been run. The `data/normalized/` directory still has only 132 files.

**Score reduced from 8.0 → 7.5** reflecting the normalization gap.

---

### 1.4 Semantic Trust Score (LLM Judge) — 6.5 / 10

| Check | Status |
|-------|--------|
| Judge operational | ✓ **Ollama was running** during this run |
| Judge trigger rate | ~75 % (hydrology-appropriate) |
| Judge fault-tolerant | ✓ |
| Judge structured logging | ✓ |
| **Task label not propagated** | ⚠ **Schema bug persists** |
| LLM output normalized before apply | ✓ |

**Ollama was active.** Unlike v1, Ollama ran during the full corpus ingestion. Sample evidence: `veilleux2011` shows `judge_used: true`, `judge_latency_s: 11.957`. Estimated ~75 % of 3 546 papers received LLM validation ≈ **2 660 papers judge-validated**.

**Critical bug persists**: Judge corrected task label from `spectral_index_analysis` → `flood_frequency_analysis` in veilleux2011, stored in `llm_judge.task.corrected_value`, but the **top-level `task` field still shows the wrong label** (`spectral_index_analysis`). This bug was documented in SCIENTIFIC_INFORMATION_LOSS_REPORT_v4.md and has not been fixed. Every paper where the judge issued a correction has wrong top-level task metadata.

**Score unchanged at 6.5** — judge runs but corrections not applied.

---

### 1.5 Citation Graph Completeness — 4.5 / 10

| Check | Status |
|-------|--------|
| CITES edges infrastructure | ✓ |
| DOI coverage on references | ~41 % (sample estimate) |
| `cited_by_count` (OpenAlex) | ✗ 0 / 3 546 papers |
| Citation boost in retrieval | ✗ inactive |
| Corpus coverage | **96 %** (3 546 / 3 692 registry entries) |
| Author/institution edges | ✗ 0 (no enrichment run) |

**Coverage improved significantly**: 3 546 papers = 96 % of the registered corpus vs 43 % in v1. However, OpenAlex enrichment has still not been run — 0 papers have `cited_by_count`, author h-index, or institutional affiliations populated.

**Score increased 4.0 → 4.5** for coverage gain.

---

### 1.6 Graph Integrity — 5.0 / 10

| Check | Status |
|-------|--------|
| Edge confidence propagation | ✓ |
| Geo FP / entity dedup | ✓ (fixed in v1) |
| Neo4j paper nodes | ⚠ Only ~132 normalized papers loaded previously |
| Normalized entities in Neo4j | ✗ normalization_runner NOT RUN on 3 546 new papers |
| CITES edges from 3 546 papers | ✗ not loaded |
| `data/normalized/` files | **132 files** (unchanged since v1) |

**Graph lags far behind corpus.** The Neo4j graph reflects at most the 132 pre-existing normalized papers. The 3 546 new paper.json files have not been normalized or loaded into the graph. The gap is now 3 414 papers.

**Score reduced from 7.0 → 5.0** — graph is now 97 % stale relative to corpus.

---

### 1.7 Retrieval Reliability — 2.0 / 10

| Check | Status |
|-------|--------|
| ChromaDB indexing (new 3546 papers) | ✗ **ALL FAILED** (dim mismatch 768 vs 384) |
| rank_score formula active | ✓ |
| Retrieval from OLD papers (~145) | ⚠ Uncertain (may still work) |
| Citation boost | ✗ 0 % (no OpenAlex) |
| ChromaDB collection recreatable | ✓ (not data loss — JSON preserved) |
| Error logged as non-fatal | ⚠ Silent regression |

**Critical silent failure**: Every paper in the new run emitted `chunking/vectorstore error (non-fatal): Collection expecting embedding with dimension of 384, got 768`. This means **semantic search is broken** for the entire new corpus. Queries will return results only from the pre-existing indexed papers (if any survive).

**Remediation**: Recreate the ChromaDB collection with 768-dim schema, then re-index all 3 546 papers. The paper.json files are intact — no GPU re-inference needed.

**Score reduced from 6.5 → 2.0** — major regression.

---

### 1.8 Reproducibility — 7.5 / 10

| Check | Status |
|-------|--------|
| Deterministic seeding | ✓ |
| Resolver/ontology version tracked | ✓ v0.3.2 / v2.0 |
| `content_hash` per paper | ✓ |
| Schema version on all papers | ✓ `schema_version: 1.0` |
| Idempotency guard | ✓ |
| **Embedding dim mismatch untracked** | ⚠ silent — not in provenance |
| **ChromaDB failure not in provenance** | ⚠ logged but not stored |
| All 3 546 output files recoverable | ✓ paper.json intact |

The run is reproducible in the sense that paper.json files are deterministically generated. However, the embedding mismatch and ChromaDB failure are not recorded in provenance, making it impossible to query which papers have valid vs invalid vector indexes.

**Score reduced from 9.0 → 7.5**.

---

## 2. Scientific Trust Scorecard

| Dimension | Weight | Score v1 | Score v2 | Delta | Weighted v2 |
|-----------|--------|----------|----------|-------|-------------|
| Section Integrity | 15 % | 6.0 | 5.5 | ↓ | 0.825 |
| Entity Precision | 20 % | 7.5 | 6.0 | ↓↓ | 1.200 |
| Ontology Consistency | 15 % | 8.0 | 7.5 | ↓ | 1.125 |
| Semantic Trust (Judge) | 20 % | 6.5 | 6.5 | = | 1.300 |
| Citation Graph | 10 % | 4.0 | 4.5 | ↑ | 0.450 |
| Graph Integrity | 10 % | 7.0 | 5.0 | ↓↓ | 0.500 |
| Retrieval Reliability | 5 % | 6.5 | 2.0 | ↓↓↓ | 0.100 |
| Reproducibility | 5 % | 9.0 | 7.5 | ↓ | 0.375 |
| **Total** | **100 %** | **6.8** | **5.875** | **↓** | **5.875** |

**Rounded: 5.9 / 10**

Despite 3 546 papers now processed (24× more than v1), the trust score regressed. The corpus volume gain is real, but the embedding dimension mismatch and missing normalization prevent the new data from being usable in retrieval, graph, or enrichment.

---

## 3. Command Analysis: Environment Variables

The pipeline was launched with:
```bash
TOKENIZERS_PARALLELISM=false \
RAYON_NUM_THREADS=1 \
OMP_NUM_THREADS=2 \
OLLAMA_MODEL=mistral-nemo:12b \
OLLAMA_URL=http://localhost:11434 \
SPACY_MODEL=en_core_web_sm \
.venv/bin/python3 -m src.orchestration.pipeline_runner --workers 3
```

| Variable | Value | Purpose |
|----------|-------|---------|
| `TOKENIZERS_PARALLELISM` | `false` | Prevents HuggingFace tokenizer deadlock when Ray forks worker processes. Without this, any tokenization in a Ray remote function will hang indefinitely. **Required.** |
| `RAYON_NUM_THREADS` | `1` | Limits the Rust Rayon thread pool used by Arrow/DataFusion. Under 3 workers, uncapped Rayon spawns 3×N threads competing for CPU. Setting to 1 removes this contention. **Stability fix.** |
| `OMP_NUM_THREADS` | `2` | Limits OpenMP threads (used by NumPy, SciPy BLAS, SpaCy internal ops). With 3 workers × uncapped OpenMP = CPU thrash. 2 is a good balance for 3-worker setup. |
| `OLLAMA_MODEL` | `mistral-nemo:12b` | 12B parameter Mistral variant for LLM judge. Good quality/speed tradeoff: ~12s latency in samples. Larger models (70B) would be ~4× slower per paper. |
| `OLLAMA_URL` | `http://localhost:11434` | Local Ollama endpoint. Keeps inference on-machine, no API cost. |
| `SPACY_MODEL` | `en_core_web_sm` | Lightweight SpaCy NER model (80 MB). Sufficient for `en_core_web_sm` precision needed by entity pipeline. Using `en_core_web_lg` (830 MB) would improve NER quality but triple RAM per SpacyActor. |
| `--workers 3` | 3 | Ray parallel task slots. With SpacyActor (~300 MB) + EmbeddingActor (~600 MB) + OllamaActor shared + 3×500 MB worker processes ≈ **~3 GB total RAM**. |

**18 hours 7 minutes** for 3 547 papers = **18.4 s/paper average**. At 3 workers this means ~6.1 s compute per paper in the critical path. The bottleneck is the OllamaActor (LLM judge at ~12 s serialized).

---

## 4. Graph Pollution Assessment

| Pollution Type | v1 State | v2 State | Status |
|----------------|----------|----------|--------|
| FP entity edges (HTTP, J, NNT) | 0 % | 0 % | ✓ Maintained |
| Geo FP (method as country) | 0 % | ~2 % (NER: Bulletin, LP3) | ⚠ Low-conf noise |
| Duplicate canonical entity nodes | 0 % | 0 % | ✓ |
| Embedding-inverted accepts | 0 % | N/A (embedding=0 all) | ⚠ Dimension mismatch |
| Judge-uncorrected task labels | ~75 % | ~25 % (judge ran) | ↑ Improved |
| Task correction not propagated to top field | N/A | **~75 % with judge** | ⚠ Schema bug |
| ChromaDB dim mismatch in 3546 papers | N/A | **100 % of new papers** | 🔴 Critical |
| Papers without normalization | 91 % (132/145) | **97 % (3546 unnorm)** | 🔴 Critical |

---

## 5. Critical Bugs (Ordered by Impact)

| # | Bug | Papers Affected | Fix |
|---|-----|----------------|-----|
| 1 | **ChromaDB 768 vs 384 dim mismatch** — ALL new papers failed vectorstore indexing | 3 546 (100 %) | Recreate ChromaDB collection with 768-dim; re-run chunker on all paper.json |
| 2 | **Normalization not run** — entity edges missing from graph for new corpus | 3 546 | Run `normalization_runner --input-dir data/literature/paper_json` |
| 3 | **Task label not propagated** — top-level `task` field not updated with judge correction | ~2 660 (75 % × 3 546) | Fix schema: apply `llm_judge.task.corrected_value` to `paper.task.label` |
| 4 | **NER geo noise** — "Bulletin", "LP3", abbreviations accepted as country entities at 0.55 conf | Unknown (pervasive) | Raise geo NER threshold to 0.65; add blocklist for 2-5 char uppercase tokens |
| 5 | **Method FP** — "HAND" detected from "on the other hand" phrase context | Unknown | Add phrase-context exclusion: block method matches where preceding 3 words are "on the/other" |
| 6 | **Metrics "Percent" only** — NSE, KGE, PBIAS not extracted; all metrics collapse to generic "Percent" type | ~46 % of papers | Fix `metric_text` scope to include methods section; extend metric patterns |
| 7 | **OpenAlex enrichment never run** — 0 citation counts | 3 546 | Run `enrichment_runner` |

---

## 6. Remaining Blockers (Ordered by Scientific Impact)

| # | Blocker | Impact | Fix |
|---|---------|--------|-----|
| 1 | **ChromaDB broken** — 3 546 papers not in vectorstore | Critical: RAG pipeline returns 0 results from new corpus | Rebuild ChromaDB with 768-dim |
| 2 | **Normalization not run** — Neo4j reflects only 132 papers | High: graph knowledge gaps for 97 % of corpus | Run normalization_runner |
| 3 | **OpenAlex not run** — 0 citation data | Medium: citation boost inactive, author/institution missing | Run enrichment_runner |
| 4 | **Task label propagation bug** — judge corrections silently discarded | Medium: wrong study classification for ~2660 papers | Fix pipeline.py: propagate corrected_value |
| 5 | **NougatRegionPipeline** — 180/3875 (4.6 %) processed | Medium: no visual content extraction | Continue nougat_region_pipeline |
| 6 | **build_parquet_layer** — analytics stale | Low: dashboard shows old data | Run after enrichment |
| 7 | **build_graph** — Neo4j stale | Medium: only 132-paper graph | Run after normalization |

---

## 7. SDOM Architecture Transition (Status)

The project is transitioning from the legacy `pipeline_runner → paper.json` path to the new document-oriented SDOM architecture (Stage 0 → 1 → 2 → 2.5). Current state:

| Component | Status |
|-----------|--------|
| TEIDocument / TEIParser (SDOM layer) | ✓ Built, 45 tests pass |
| Stage0Ingestor | ✓ 48 tests pass — 1 test paper in data/raw/ |
| Stage1Parser | ✓ 51 tests — but `data/parsed/` does not exist (not run on corpus) |
| Stage2Engineer | ✓ 51 tests |
| SemanticValidatorStage | ✓ 84 tests |
| NougatRegionPipeline (Stage 1.5) | ✓ 180/3875 papers processed |
| SODB per-paper Parquet | ✓ 3692 dirs exist (most have only basic structure) |
| CLI orchestrator for new pipeline | ✗ Does not exist yet |

The new architecture correctly separates concerns. When the CLI orchestrator is built and run on the full corpus, the paper.json quality issues (metric loss, section routing, task label bug) become fixable without rerunning GROBID or Nougat.

---

## 8. Pipeline Maturity Trajectory

| Version | Date | Score | Key milestone |
|---------|------|-------|--------------|
| v1 (pre-fix) | 2026-05-12 | 3.5 | TEI parse failures, 0 method entities, judge never ran |
| v2 (post-5-blockers) | 2026-05-14 | 5.0 | Section fix, embedding fallback, CITES wired |
| v2.1 (post-analysis-v2) | 2026-05-14 | 5.5 | Judge fault tolerance, candidate schema, DEMs trigger |
| v3 (post-hardening-1) | 2026-05-15 | 6.8 | FP suppression, embedding clamping, geo filter, dedup |
| **v4 (post-full-corpus)** | **2026-05-20** | **5.9** | 3 546 papers processed, Ollama active, ChromaDB dim mismatch regression |

**Target for v5** (after ChromaDB fix + normalization + OpenAlex): **8.0 / 10**  
Requires: ChromaDB rebuild (2h), normalization_runner (~1h), enrichment_runner (~6h), build_graph.

---

## 9. Reports Generated

| Report | Location | Focus |
|--------|----------|-------|
| PIPELINE_TRUST_REPORT_v1.md | project root | 1 600-paper corpus trust score (6.8/10) |
| **PIPELINE_TRUST_REPORT_v2.md** | project root | **3 546-paper corpus trust score (5.9/10) — this document** |
| PIPELINE_TRUST_REPORT_v2_UA.md | project root | Ukrainian translation of v2 |
| SCIENTIFIC_INFORMATION_LOSS_REPORT_v5.md | project root | Updated loss analysis for 3 546-paper corpus |
| SCIENTIFIC_INFORMATION_LOSS_REPORT_v4.md | project root | Loss analysis: 52-paper corpus |
