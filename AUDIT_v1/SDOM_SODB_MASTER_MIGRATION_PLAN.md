# SDOM/SODB Master Migration Plan
## GeoHydroAI: From PDF Pipeline to Scientific Operating System

**Classification: Principal Architecture Document**
**Version: 1.0 | Date: 2026-05-18**
**Synthesized from:** ARCHITECTURE_REVIEW.md, SDOM_MIGRATION.md, PIPELINE_ROOT_CAUSE_ANALYSIS_v4.md,
SCIENTIFIC_INFORMATION_LOSS_REPORT_v4.md, ONTOLOGY_FORENSIC_AUDIT_v4.md,
ONTOLOGY_QA_AUDIT_v4.md, PIPELINE_TRUST_REPORT_v1.md, JUDGE_AUDIT_v1.md,
SODB_DESIGN.md, GEOHYDROAI_ARCHITECTURE.md

---

## 1. Executive Summary

### What GeoHydroAI Currently Is

A scientific literature intelligence platform for the geo-hydrology and flood-modeling domain, operating on a 3,692-paper corpus. The system ingests scientific PDFs, parses them with GROBID, extracts entities and methods, enriches with OpenAlex metadata, and loads a Neo4j knowledge graph. Current pipeline trust score: **6.8/10**.

The system has undergone meaningful architectural evolution over multiple engineering passes. Individual modules — the `FailureType` failure ontology, `TEIDocument` domain model, `PipelineRegistry`, coordinate-aware chunking infrastructure — represent principal-level engineering decisions. They exist alongside a 2,148-line god object (`pipeline.py`) that remains the live production path and a completely disconnected Nougat semantic region pipeline (`nougat_region_pipeline.py`) that produces rich scientific output consumed by nothing.

### What It Is Evolving Into

A **Scientific Operating System for Hydrology Intelligence** — a platform where:
- Scientific objects (formulas, tables, figures, numeric facts) are **first-class persistent entities** with stable identities and provenance chains
- The extraction quality of any single component can be improved without triggering full corpus re-ingestion
- Every quantitative claim in the knowledge graph is **traceable** to a specific cell in a specific table on a specific PDF page
- The system can answer: *"Which papers report NSE > 0.80 in validation? Which method, which watershed, which satellite input data?"*

### Main Architectural Bottlenecks

**Bottleneck 1 (Most Severe): 0% NSE/RMSE extraction**
The system's primary scientific purpose is to extract model performance evidence from hydrology papers. NSE — the single most important metric in hydrology — is extracted from 0% of papers. This is not a parsing failure. It is a precise, traceable code bug: `metric_text` in `pipeline.py:~472` excludes the methods section, where 77% of NSE mentions appear. The pipeline works; the scope is wrong.

**Bottleneck 2: Disconnected Nougat Pipeline**
`nougat_region_pipeline.py` performs sophisticated semantic region reconstruction and multimodal inference. Its output — LaTeX formulas, table markdown, figure crops — lands in `data/nougat_regions/*.json` and is consumed by nothing in the main pipeline. The GPU compute runs, the results disappear.

**Bottleneck 3: No Scientific Object Persistence**
There is no intermediate layer between parsing and `paper.json`. Improving any extraction component requires re-running GROBID, re-running Nougat, re-running NER, re-running embeddings, re-running the Ollama judge. The lack of a persistent scientific object database (SODB) makes iterative improvement exponentially expensive.

**Bottleneck 4: 5/11 Ontology Files Dead**
The ontology stores 15 disambiguation rules for the most ambiguous tokens in the domain (SCS, ANN, MLP, LISFLOOD, HEC-RAS 1D/2D). These rules are loaded in `ontology_disambiguation_rules.json` but never read by `load_knowledge_base()`. Sentinel-1, Sentinel-2, and Sentinel-3 are collapsed to the single token `SENTINEL`, losing the SAR/optical distinction that determines the paper's entire methodology.

**Bottleneck 5: Architectural Bifurcation**
Two parallel architectures coexist: the new `src/document/` layer (TEIDocument, LayoutAwareChunker, DocumentParser Protocol) and the old `pipeline.py` production path. SDOM_MIGRATION.md marks several migration steps as "Done" — TEIParser for NER text, LayoutAwareChunker→ChromaDB, SPECTER2 embedding. These are wired in the new path. The old `pipeline.py` path still contains direct lxml parsing, blocking full migration benefits.

### Current Maturity Level

```
Layer                    Maturity    Notes
─────────────────────────────────────────────────────────────────
Ingestion (GROBID)       ████████░░  Production-grade. FailureType excellent.
TEIDocument (SDOM)       ██████░░░░  Architecturally correct. Partially wired.
Entity extraction        █████░░░░░  Methods: good. Metrics: broken. Satellites: 27%.
Ontology                 ███████░░░  v2 complete. 5/11 files dead. Disambiguation unloaded.
Nougat pipeline          █████░░░░░  Implemented. Fully disconnected from main pipeline.
NumericFact extraction   ████░░░░░░  Table extractor exists. Not in main pipeline loop.
SODB (Parquet layer)     ░░░░░░░░░░  Does not exist. Designed in docs only.
Knowledge graph          █████░░░░░  Paper/method/entity nodes. No formulas, no NumericFacts.
Retrieval                ████░░░░░░  ChromaDB wired. Citation boost inactive. 43% coverage.
```

---

## 2. Current Architecture Analysis

### 2.1 What Exists and Is Good

**`FailureType` taxonomy** (`src/ingestion/failure_types.py`)
The failure ontology with `is_retriable()`, `to_registry_status()`, and `classify_500_body()` represents principal-level thinking. Every error class owns its retry policy. GROBID's embedded error codes (`[NO_BLOCKS]`, `[PDFALTO_CONVERSION_FAILURE]`) are correctly parsed. This is data-platform-grade failure handling that most research systems never implement.

**`PipelineRegistry`** (`src/registry/registry_db.py`)
DuckDB-backed ingestion ledger with heartbeats, optimistic locking, audit log append, and idempotency. Two-level idempotency (orchestrator pre-filter + task-level guard) prevents duplicate work. This is production-grade infrastructure.

**`TEIDocument` + `DocumentParser` Protocol** (`src/document/`)
A runtime-checkable Protocol enabling parser virtualization. GROBID, Nougat, future VLMs — all produce `TEIDocument`. The coordinate system handles multi-segment elements and cross-page distance. `CitationMarker` separated from `Reference` is architecturally correct. `LayoutAwareChunker` with proximity-based figure grounding is 18 months ahead of the retrieval layer it serves.

**Ontology v2** (`data/flood_modeling_ontology_v2.json`)
46 v2 models, 1,118 entities, 3,161 aliases. `schema_version: 1.0` on all papers. Resolver version tracked. Edge confidence propagation computed. The ontology system is the most mature extraction component at 8.0/10.

**`NougatRegionPipeline`** (`src/ingestion/nougat_region_pipeline.py`)
Semantically sophisticated region reconstruction: merge-distance constants with documented calibration, context expansion per region family, full-page fallback on threshold, Ray-distributed inference, crop persistence. The design is correct. The integration is missing.

**`table_extractor.py`** (`src/extraction/table_extractor.py`)
Produces `NumericFact` objects with `column_context`, `row_context`, `page`, `table_id`, `metric`, `value`, `unit`, `confidence`. Two table layout patterns handled. This is the most important extraction component for GeoHydroAI's scientific purpose — and it runs as a standalone script, not in the main pipeline.

### 2.2 What Is Legacy

**`pipeline.py`** (2,148 lines, 87 functions)
The god object that handles XML parsing, geo lookup, GeoNames HTTP, NER postprocessing, embedding classification, entity extraction, and JSON assembly simultaneously. It is the live production path. Module-level mutable globals (`GEO_CACHE`, `_KB`, `LAST_CALL`) make it non-unit-testable. The `classify_with_embeddings()` function is a shadow ML model with no versioning.

**`src/processing/chunker.py`** (character-window chunker)
Non-deterministic chunk IDs (`f"{filename}::c{N}"`), no section awareness, no citation awareness, no coordinate awareness. Connected to ChromaDB. Superseded by `LayoutAwareChunker` which is not yet fully canonical.

**`data/nougat_regions/*.json`**
Current output format of `nougat_region_pipeline.py`. Filesystem JSON files with no schema. SODB migration replaces these with `regions.parquet`.

### 2.3 What Is Dangerous

**0% NSE extraction** (PIPELINE_ROOT_CAUSE_ANALYSIS_v4.md, RC-1)
`metric_text = abstract + results + conclusion`. Methods section excluded. 77% of NSE mentions are in methods. Every KG query about model performance returns empty results. This is the primary scientific failure of the system.

**73% satellite extraction failure** (RC-4)
The `_USAGE_POSITIVE` list does not contain "obtained", "acquired", "downloaded", "derived from" — the primary acquisition verbs in data science papers. The strict usage filter rejects legitimate satellite mentions.

**Sentinel version collapse** (RC-5, ONTOLOGY_FORENSIC_AUDIT_v4.md)
All Sentinel missions → single token `SENTINEL`. A graph query for "SAR-based flood mapping papers" cannot distinguish Sentinel-1 (SAR) from Sentinel-2 (optical) papers. This conflation affects every paper in the corpus that uses Sentinel data.

**`study_type` at wrong JSON path** (RC-7)
`paper["study_type"]` = None in all serialized JSONs. The value is correctly extracted at `paper["entities"]["geo"]["study_type"]["label"]` but never promoted to the top level. All downstream consumers receive None.

**5/11 ontology files dead** (ONTOLOGY_FORENSIC_AUDIT_v4.md)
`ontology_disambiguation_rules.json` contains 15 rules for the most ambiguous tokens (SCS, ANN, MLP, LISFLOOD, HEC-RAS 1D/2D, Prophet). None are loaded. The ontology QA suite achieves 100% pass rate by testing only exact-match paths — the semantic embedding path and all disambiguation paths are completely untested.

**Double XML parse** (ARCHITECTURE_REVIEW.md)
`process_paper.py` parses XML twice: once for NER text extraction, once inside `build_paper_json()`. Each paper pays 2× file I/O and 2× XML parsing. SDOM_MIGRATION.md marks C1-b as "Done" — this needs verification.

### 2.4 What Blocks Scaling

**GPU coupling**: Any downstream improvement requires full Nougat re-inference because there is no Nougat output cache and no intermediate storage layer.

**SpacyActor singleton**: With MAX_IN_FLIGHT=4 tasks, three tasks wait on one SpacyActor. No `max_concurrency` set. CPU-bound work blocks the actor event loop.

**Synchronous GeoNames HTTP**: `geonames_lookup()` inside extraction pipeline with module-level `LAST_CALL` rate limiter — thread-unsafe, blocking, unrecoverable on API downtime.

**43% corpus coverage**: Only 1,600 of 3,692 papers are in ChromaDB. The other 2,092 XMLs are unprocessed.

**`data/normalized/` empty**: The normalization runner has not been executed. Entity edges are missing from Neo4j.

---

## 3. Root Cause Synthesis

### 3.1 Top 10 Architectural Failures

| # | Failure | Severity | Evidence Source |
|---|---------|----------|-----------------|
| 1 | `metric_text` excludes methods section — 0% NSE extraction | CRITICAL | PIPELINE_ROOT_CAUSE_ANALYSIS_v4 RC-1 |
| 2 | `NougatRegionPipeline` output consumed by nothing | CRITICAL | ARCHITECTURE_REVIEW §3, SODB_DESIGN §2.1 |
| 3 | No SODB layer — every improvement requires full re-ingestion | CRITICAL | SODB_DESIGN §2.1, GEOHYDROAI_ARCHITECTURE §2 |
| 4 | 5/11 ontology files dead — disambiguation never applied | HIGH | ONTOLOGY_FORENSIC_AUDIT_v4 §1, §3.1 |
| 5 | `_USAGE_POSITIVE` missing acquisition verbs — 73% satellite loss | HIGH | PIPELINE_ROOT_CAUSE_ANALYSIS_v4 RC-4 |
| 6 | Sentinel-1/2/3 collapsed to SENTINEL — SAR/optical distinction lost | HIGH | ONTOLOGY_FORENSIC_AUDIT_v4 §3.5 |
| 7 | `study_type` at wrong JSON path — all consumers receive None | HIGH | PIPELINE_ROOT_CAUSE_ANALYSIS_v4 RC-7 |
| 8 | Two parallel architectures (new SDOM unwired, old pipeline.py live) | HIGH | ARCHITECTURE_REVIEW §3, §13 |
| 9 | Semantic embedding path and disambiguation completely untested | MEDIUM | ONTOLOGY_QA_AUDIT_v4 §3.3 |
| 10 | Citation graph exists in infrastructure but CITES edges not built | MEDIUM | ARCHITECTURE_REVIEW §8 (pre-SDOM_MIGRATION fix) |

### 3.2 Top 10 Scientific Information Loss Causes

| # | Loss | Scope | Papers affected |
|---|------|-------|-----------------|
| 1 | NSE/RMSE values — 0% captured | Primary quantitative evidence | 100% |
| 2 | Satellite identity (Sentinel-1 vs 2) — collapsed | Methodology classification | ~100% Sentinel papers |
| 3 | Satellite data source — 73% papers have none | Data provenance | 73% |
| 4 | methods section excluded from metric scope | Full section | 100% |
| 5 | `other` section (avg 16KB/paper) partially inaccessible | 16KB × 3692 papers = 59MB scientific text | 40%+ |
| 6 | SCS/ANN/MLP/HEC-RAS ambiguity — 15 rules defined, 0 applied | Incorrect method classification | 20-30% |
| 7 | Nougat formula LaTeX — never enters knowledge base | All formulas in all papers | 100% |
| 8 | Numeric facts from tables — table_extractor not in main pipeline | All quantitative table evidence | 100% |
| 9 | Figure semantic classification — no figure nodes in KG | Scientific visualization evidence | 100% |
| 10 | Per-period (calibration/validation) metric semantics — never extracted | Model evaluation context | 100% |

### 3.3 Why the System Loses Scientific Meaning

The system loses scientific meaning at four structural levels:

**Level 1: Scope error (fixable in minutes)**
`metric_text` excludes methods. `_USAGE_POSITIVE` missing acquisition verbs. `study_type` at wrong JSON path. These are bugs in 5-10 lines of code. Their impact is catastrophic because they systematically prevent entire categories of evidence from entering the pipeline.

**Level 2: Architecture disconnect (fixable in days)**
`NougatRegionPipeline` and `table_extractor.py` are correct implementations of valuable extraction logic. They produce no output for the main pipeline because no handoff contract exists between them and `process_paper.py`. The SODB `regions.parquet` is the missing handoff contract.

**Level 3: Persistence gap (fixable in weeks)**
There is no intermediate storage between parsing and `paper.json`. Scientific objects (formulas, tables, numeric facts) are computed transiently in memory and either written to `paper.json` (if they made it through) or lost. The SODB layer provides this persistence.

**Level 4: Ontology closure (fixable in weeks)**
The ontology contains known gaps documented in `ontology_normalization_report.json` — 32 missing models, 9 missing metrics, 11 missing concepts. These gaps are acknowledged but never remediated. Every paper mentioning an unknown entity contributes to the `unmatched_entities` list in provenance and nowhere else.

### 3.4 Contradiction Analysis Across Documents

**Contradiction 1: SDOM_MIGRATION.md vs ARCHITECTURE_REVIEW.md**
SDOM_MIGRATION.md marks C1 (Wire TEIParser for NER text) as ✅ Done. ARCHITECTURE_REVIEW.md describes the XML boundary violations as live production issues. **Resolution**: C1-a (NER text extraction) is done — `process_paper.py` now uses TEIParser for the NER future. But `build_paper_json()` in `pipeline.py` still contains direct lxml parsing (line 2044). The migration is partial, not complete.

**Contradiction 2: PIPELINE_TRUST_REPORT (Ontology 8.0/10) vs ONTOLOGY_FORENSIC_AUDIT (5/11 dead)**
The trust report scores ontology at 8.0/10 based on the v2 migration completeness, canonical ID format, and disambiguation confidence tracking. The forensic audit reveals that 5 of 11 ontology files are dead. **Resolution**: Both are correct. The *loaded* ontology is well-designed (8/10). The *coverage* of the ontology system is poor because 5 files (including the critical disambiguation rules) are never read. These are measuring different things. The composite ontology score should be closer to 6/10.

**Contradiction 3: SDOM_MIGRATION.md C2 (LayoutAwareChunker) "Done" vs ARCHITECTURE_REVIEW.md "disconnected"**
ARCHITECTURE_REVIEW.md was written before the SDOM migration session. SDOM_MIGRATION.md records C2 as done — `chroma_store.py` modified to accept `DocumentChunk`. This is the correct resolution: ARCHITECTURE_REVIEW describes the state before the migration, SDOM_MIGRATION describes the state after. Accept SDOM_MIGRATION.md as current.

**Contradiction 4: Trust score 6.8/10 vs 0% NSE extraction**
The trust score includes Ontology Consistency (8.0/10, weight 15%) which inflates the aggregate. Entity Precision (7.5/10, weight 20%) does not penalize for 0% NSE extraction because NSE is not in the precision sample (which audited entity FP rate, not recall). The trust report measures what was extracted correctly, not what was systematically missed. True scientific value score accounting for NSE and satellite loss should be closer to **4.5/10**.

---

## 4. SDOM/SODB Migration Strategy

### 4.1 Target Architecture

```
PDF (data/literature/pdf/)
    ↓
[0. Triage]       pdf_triage.py → PipelineRegistry
    ↓
[1. Parsing]      GROBID → TEI XML → TEIParser → TEIDocument (SDOM)
    ↓
[2. Region Construction]
    TEIDocument + TEI XML coordinates
    → NougatRegionPipeline (semantic block merging + context expansion)
    → NougatActor (GPU inference with L1 cache)
    → SODBWriter → regions.parquet      ← HANDOFF CONTRACT
    ↓
[3a. Scientific Object Extraction]
    regions.parquet → TableExtractor    → tables.parquet + numeric_facts.parquet
    regions.parquet → FormulaExtractor  → formulas.parquet
    regions.parquet → FigureClassifier  → figures.parquet
    ↓
[3b. Text Entity Extraction]
    TEIDocument → EntityExtractor       → entities in paper_metadata.parquet
    (metric_text now includes methods)
    ↓
[4. Enrichment]
    paper_metadata + SODB → OpenAlexActor → enriched paper_metadata.parquet
    ↓
[5. Ontology Grounding]
    numeric_facts → OntologyGrounder    → canonical_id in numeric_facts.parquet
    formulas → FormulaGrounder         → hydrology_grounding in formulas.parquet
    entities → ontology_matcher        → normalized_entities in paper_metadata.parquet
    ↓
[6. Aggregation]
    All SODB parquets → PaperJsonBuilder → paper.json (derived artifact)
    ↓
[7. Graph Loading]
    paper.json + SODB global indexes → Neo4j KG
    ↓
[8. Vector Synchronization]
    TEIDocument → LayoutAwareChunker → ChromaDB (SPECTER2 embeddings)
    ↓
Scientific Intelligence Platform
```

### 4.2 Canonical Storage Layer

The SODB is the canonical storage layer. It has three components:

**Per-paper namespace** (`data/sodb/{paper_id}/`)
Contains all scientific objects for one paper as typed parquet files. This is the authoritative source. Per-paper isolation means reprocessing one paper cannot corrupt another.

**Global indexes** (`data/sodb/_index/`)
DuckDB UNION ALL views over per-paper parquets. Derived, not authoritative. Rebuilt on-demand. Enable corpus-wide analytics without loading all per-paper files.

**SODB manifest** (`data/sodb/{paper_id}/.sodb_manifest.json`)
Per-stage completion status with pipeline_hash. The hash is `SHA256(code_version + config_hash)`. Stage is skipped if manifest shows `status: complete` and `pipeline_hash` matches current. This is the incremental reprocessing mechanism.

### 4.3 Canonical Parser Boundary

```
CANONICAL RULE: No code outside src/document/ may import lxml.

Current violations (from ARCHITECTURE_REVIEW.md):
  - pipeline.py line 48: from lxml import etree
  - extract_references.py: from lxml import etree
  - process_paper.py line 129: from lxml import etree (SDOM_MIGRATION: being resolved)

Target state:
  - src/document/parser.py: the ONLY file that imports lxml
  - All other modules receive TEIDocument objects
  - GROBID TEI XML is a transport format; TEIDocument is the domain model
```

### 4.4 Provenance Architecture

Every scientific object carries a complete lineage chain. The chain must be reconstructable from any endpoint (Neo4j node, paper.json field, or SODB parquet row) back to the source PDF pixel:

```
Neo4j NumericFact node {fact_id: "sha1_abc"}
    ↓ fact_id
numeric_facts.parquet row
    ↓ source_region_id
regions.parquet row
    ↓ crop_path
data/sodb/{paper_id}/assets/crops/{region_id}.png
    ↓ grobid_block_ids
GROBID TEI XML <figure coords="6,120,450,400,600">
    ↓ coords
PDF page 6, pixels at bbox [120, 450, 400, 600]
```

The `provenance.parquet` file is append-only. Every extraction event, grounding event, and correction event is recorded as a row. This creates an immutable audit log with full scientific evidence lineage.

### 4.5 Stage Decoupling Strategy

The SODB enables true stage decoupling by defining precise input/output contracts:

| Stage | Reads | Writes | GPU? |
|-------|-------|--------|------|
| Parsing | PDF, TEI XML | TEIDocument (in-memory), regions.parquet | No |
| Nougat inference | regions.parquet (crop paths) | regions.parquet (nougat_output field) | YES |
| Table extraction | regions.parquet (TABLE_BODY) | tables.parquet, numeric_facts.parquet | No |
| Formula extraction | regions.parquet (FORMULA) | formulas.parquet | No |
| Figure classification | regions.parquet (FIGURE_BODY) | figures.parquet | Minimal |
| Text entity extraction | TEIDocument.body_text() | paper_metadata.parquet | No |
| Ontology grounding | numeric_facts, formulas | Updates to same parquets | No (cached) |
| Aggregation | All SODB parquets | paper.json | No |
| Graph loading | paper.json | Neo4j nodes/edges | No |

**Key property**: Any stage can be re-run independently by clearing its output parquet and updating the manifest. GPU stages (Nougat) are protected by L1 cache — re-runs do not trigger re-inference for unchanged regions.

---

## 5. Scientific Object Model

### 5.1 Object Identity and Stability

All scientific object IDs are deterministic functions of their content. Same input always produces the same ID. This enables deduplication, cross-paper matching, and stable provenance chains across pipeline re-runs.

```python
formula_id   = SHA256(paper_id + str(page) + latex_normalized[:64])
fact_id      = SHA1("|".join([paper_id, table_id, col_ctx_hash, row_ctx_hash, str(value), str(seq)]))
region_id    = SHA256(paper_id + str(page) + "|".join(sorted(grobid_block_ids)))
table_id     = SHA256(paper_id + str(page) + table_label)
figure_id    = SHA256(paper_id + str(page) + figure_label)
```

### 5.2 Core Parquet Schemas

**regions.parquet** — The provenance anchor. Every other object traces to a region.

```python
REGIONS_SCHEMA = pa.schema([
    pa.field("region_id",          pa.string(),  nullable=False),
    pa.field("paper_id",           pa.string(),  nullable=False),
    pa.field("page",               pa.int32()),
    pa.field("region_family",      pa.string()),   # FIGURE_BODY | TABLE_BODY | FORMULA
    pa.field("bbox_x0",            pa.float32()),  # Original GROBID bbox
    pa.field("bbox_y0",            pa.float32()),
    pa.field("bbox_x1",            pa.float32()),
    pa.field("bbox_y1",            pa.float32()),
    pa.field("expanded_bbox_x0",   pa.float32()),  # After context expansion
    pa.field("expanded_bbox_y0",   pa.float32()),
    pa.field("expanded_bbox_x1",   pa.float32()),
    pa.field("expanded_bbox_y1",   pa.float32()),
    pa.field("merge_count",        pa.int32()),    # GROBID blocks merged
    pa.field("is_full_page",       pa.bool_()),
    pa.field("grobid_block_ids",   pa.list_(pa.string())),
    pa.field("nougat_output",      pa.string()),   # Raw Nougat markdown
    pa.field("nougat_tokens",      pa.int32()),
    pa.field("nougat_duration_ms", pa.float32()),
    pa.field("nougat_cache_hit",   pa.bool_()),    # Was L1 cache used?
    pa.field("crop_path",          pa.string()),   # For audit
    pa.field("pipeline_version",   pa.string()),
    pa.field("processed_at",       pa.timestamp("us")),
])
```

**formulas.parquet** — LaTeX formulas as scientific objects.

```python
FORMULAS_SCHEMA = pa.schema([
    pa.field("formula_id",          pa.string(),  nullable=False),
    pa.field("paper_id",            pa.string(),  nullable=False),
    pa.field("page",                pa.int32()),
    pa.field("latex",               pa.string()),         # Full LaTeX
    pa.field("latex_normalized",    pa.string()),         # Variable-replaced form
    pa.field("semantic_type",       pa.string()),         # water_balance|performance_metric|...
    pa.field("hydrology_grounding", pa.string()),         # metric.nse | concept.water_balance
    pa.field("variables",           pa.list_(pa.string())),  # ["NSE", "Q_obs", "Q_sim"]
    pa.field("is_numbered",         pa.bool_()),
    pa.field("equation_number",     pa.string()),
    pa.field("is_display",          pa.bool_()),
    pa.field("tex_valid",           pa.bool_()),          # KaTeX compilation check
    pa.field("context_before",      pa.string()),
    pa.field("context_after",       pa.string()),
    pa.field("section_title",       pa.string()),
    pa.field("confidence",          pa.float32()),
    pa.field("source_parser",       pa.string()),         # nougat | grobid | hybrid
    pa.field("source_region_id",    pa.string()),         # FK to regions.parquet
    pa.field("bbox_x0",             pa.float32()),        # None for Nougat-extracted
    pa.field("bbox_y0",             pa.float32()),
    pa.field("bbox_x1",             pa.float32()),
    pa.field("bbox_y1",             pa.float32()),
    pa.field("pipeline_version",    pa.string()),
    pa.field("extracted_at",        pa.timestamp("us")),
])
```

**numeric_facts.parquet** — Atomic quantitative evidence units.

```python
NUMERIC_FACTS_SCHEMA = pa.schema([
    pa.field("fact_id",          pa.string(),  nullable=False),
    pa.field("paper_id",         pa.string(),  nullable=False),
    pa.field("table_id",         pa.string()),              # FK to tables.parquet
    pa.field("table_label",      pa.string()),
    pa.field("page",             pa.int32()),
    pa.field("metric",           pa.string()),              # "NSE"
    pa.field("canonical_id",     pa.string()),              # "metric.nse"
    pa.field("metric_family",    pa.string()),              # "efficiency"|"error"|"bias"
    pa.field("value",            pa.float64()),
    pa.field("value_range_min",  pa.float32()),
    pa.field("value_range_max",  pa.float32()),
    pa.field("unit",             pa.string()),
    pa.field("raw_cell",         pa.string()),
    pa.field("column_context",   pa.list_(pa.string())),    # ["Calibration", "NSE"]
    pa.field("row_context",      pa.list_(pa.string())),    # ["W280"]
    pa.field("period",           pa.string()),              # calibration|validation|unknown
    pa.field("basin_id",         pa.string()),
    pa.field("event_id",         pa.string()),
    pa.field("plausible",        pa.bool_()),
    pa.field("is_outlier",       pa.bool_()),
    pa.field("confidence",       pa.float32()),
    pa.field("source",           pa.string()),              # grobid_tei|nougat_table
    pa.field("source_region_id", pa.string()),
    pa.field("pipeline_version", pa.string()),
    pa.field("extracted_at",     pa.timestamp("us")),
])
```

**figures.parquet** — Figure scientific objects with hydrology-specific classification.

```python
FIGURES_SCHEMA = pa.schema([
    pa.field("figure_id",                 pa.string(),  nullable=False),
    pa.field("paper_id",                  pa.string(),  nullable=False),
    pa.field("page",                      pa.int32()),
    pa.field("figure_label",              pa.string()),
    pa.field("caption",                   pa.string()),
    pa.field("bbox_x0",                   pa.float32()),
    pa.field("bbox_y0",                   pa.float32()),
    pa.field("bbox_x1",                   pa.float32()),
    pa.field("bbox_y1",                   pa.float32()),
    pa.field("image_path",                pa.string()),
    pa.field("figure_type",               pa.string()),  # chart|map|diagram|photograph
    pa.field("chart_type",                pa.string()),  # hydrograph|scatter|bar|...
    pa.field("contains_hydrograph",       pa.bool_()),
    pa.field("contains_flood_map",        pa.bool_()),
    pa.field("contains_dem",              pa.bool_()),
    pa.field("contains_confusion_matrix", pa.bool_()),
    pa.field("contains_calibration_plot", pa.bool_()),
    pa.field("contains_watershed_map",    pa.bool_()),
    pa.field("ocr_text",                  pa.string()),
    pa.field("axis_labels",               pa.list_(pa.string())),
    pa.field("legend_items",              pa.list_(pa.string())),
    pa.field("semantic_tags",             pa.list_(pa.string())),
    pa.field("source_region_id",          pa.string()),
    pa.field("pipeline_version",          pa.string()),
    pa.field("extracted_at",              pa.timestamp("us")),
])
```

**provenance.parquet** — Append-only audit log.

```python
PROVENANCE_SCHEMA = pa.schema([
    pa.field("provenance_id",     pa.string(),  nullable=False),
    pa.field("paper_id",          pa.string(),  nullable=False),
    pa.field("object_id",         pa.string(),  nullable=False),
    pa.field("object_type",       pa.string()),  # formula|table|figure|numeric_fact
    pa.field("event_type",        pa.string()),  # extracted|grounded|validated|corrected
    pa.field("event_timestamp",   pa.timestamp("us")),
    pa.field("stage_name",        pa.string()),
    pa.field("stage_version",     pa.string()),
    pa.field("source_parser",     pa.string()),  # grobid|nougat|hybrid|regex|llm
    pa.field("source_page",       pa.int32()),
    pa.field("source_bbox",       pa.string()),  # JSON bbox dict
    pa.field("region_id",         pa.string()),  # FK to regions.parquet
    pa.field("raw_source_text",   pa.string()),  # Exact extraction input
    pa.field("extraction_rule",   pa.string()),  # Pattern name or rule ID
    pa.field("confidence_before", pa.float32()),
    pa.field("confidence_after",  pa.float32()),
    pa.field("notes",             pa.string()),
    pa.field("is_correction",     pa.bool_()),
    pa.field("supersedes_id",     pa.string()),  # Immutable audit trail
])
```

---

## 6. Migration Phases

### Phase 0 — Immediate Bug Fixes (1-3 days, no architecture changes)

**Goal**: Stop the worst scientific information loss with surgical fixes to existing code.

**Changes**:

1. **Fix RC-1: Add methods to metric_text** (`pipeline.py:~472`)
   ```python
   # Before:
   metric_text = " ".join([self.abstract, self.results, self.conclusion])
   # After:
   metric_text = " ".join([self.abstract, self.methods, self.results, self.conclusion])
   ```
   Expected gain: NSE extraction for ~77% of papers.

2. **Fix RC-4: Expand `_USAGE_POSITIVE`** (`entity_extractor.py:50-68`)
   Add: "obtained", "acquired", "downloaded", "derived from", "provided by",
   "sourced from", "collected from", "retrieved from", "courtesy of".
   Expected gain: satellite extraction for ~40-50% more papers.

3. **Fix RC-5: Split SENTINEL into SENTINEL-1/2/3** (`knowledge_loader.py`)
   Create separate KB entries with distinct IDs: `sensor.sentinel_1`, `sensor.sentinel_2`, `sensor.sentinel_3`.
   Pattern for SENTINEL-1: `\bsentinel[\s\-]?1[abc]?\b`

4. **Fix RC-3: Load `ontology_disambiguation_rules.json`** (`knowledge_loader.py`)
   Add to the `files` dict. Wire rules application in `EntityExtractor` or `ontology_matcher.py`.

5. **Fix RC-7: Promote study_type to top-level** (`pipeline.py` serialization)
   At JSON assembly: `paper["study_type"] = get_nested(paper, "entities.geo.study_type.label")`

6. **Fix judge template leakage** (`pipeline.py:2470`)
   Replace `paper_id: '12345'` with `paper_id: 'EXAMPLE_PAPER'` in judge prompt.

**Validation**: Re-run on 52-paper sample from SCIENTIFIC_INFORMATION_LOSS_REPORT_v4.md.
Target: NSE extraction >50%, satellite extraction >50%, study_type at top level 100%.

**Rollback**: All changes are in `pipeline.py` and `entity_extractor.py`. Revert commit.

**Risk**: LOW. These are additive changes to existing code, not architectural changes.

---

### Phase 1 — SODB Foundation (2-3 weeks)

**Goal**: Create the SODB layer. Connect NougatRegionPipeline to the main pipeline via `regions.parquet`.

**Changes**:

1. **Create `src/sodb/` module**
   - `src/sodb/__init__.py`
   - `src/sodb/schemas.py` — single source of truth for all parquet schemas
   - `src/sodb/writer.py` — SODBWriter with atomic write + manifest tracking
   - `src/sodb/reader.py` — DuckDB query layer
   - `src/sodb/cache.py` — L1 Nougat cache (SHA256(model:crop_bytes) → text)

2. **Wire NougatRegionPipeline → regions.parquet**
   Modify `nougat_region_pipeline.py` to:
   - Check L1 cache before every NougatActor call
   - Write `regions.parquet` per paper via SODBWriter
   - Store crops to `data/sodb/{paper_id}/assets/crops/`
   - Update `.sodb_manifest.json` after successful write

3. **Wire table_extractor → numeric_facts.parquet**
   Modify `table_extractor.py` to:
   - Accept `regions.parquet` path as input (TABLE_BODY regions)
   - Write `numeric_facts.parquet` + `tables.parquet` to SODB
   - Add `period`, `basin_id`, `metric_family` fields

4. **Create SODBRegionWriterActor** (`src/actors/sodb_writer_actor.py`)
   Ray actor separate from NougatActor. GPU memory released after inference, before write.

**Dependencies**: Phase 0 complete. `src/sodb/schemas.py` before any writer.

**Validation**:
- Run on 10 papers. Verify `regions.parquet` schema matches `REGIONS_SCHEMA`.
- Verify `numeric_facts.parquet` contains NSE values (after Phase 0 fix also applied).
- Verify L1 cache produces identical output to original inference.
- Verify `.sodb_manifest.json` is updated atomically.

**Rollback**: Phase 1 adds new files and new code paths. `process_paper.py` is not modified in Phase 1. Rollback: delete `src/sodb/`, revert `nougat_region_pipeline.py`.

**Risk**: MEDIUM. New module with atomic file I/O. Test atomic rename on WSL2 filesystem.

---

### Phase 2 — Full Scientific Object Extraction (3-4 weeks)

**Goal**: Extract formulas, figures, and complete the scientific object model.

**Changes**:

1. **Create `src/extraction/formula_extractor.py`**
   Input: `regions.parquet` (FORMULA regions with `nougat_output`)
   Process: Parse LaTeX, validate with KaTeX, extract variables, run hydrology grounding
   Output: `formulas.parquet`

2. **Create `src/extraction/figure_classifier.py`**
   Input: `regions.parquet` (FIGURE_BODY regions), crop images
   Process: Caption-text classification + CLIP zero-shot for hydrology figure types
   Output: `figures.parquet`

3. **Create `src/extraction/formula_grounder.py`**
   Input: LaTeX string + context sentences
   Process: Regex pattern matching + variable set intersection + Ollama for ambiguous cases
   Output: `hydrology_grounding` canonical ID

4. **Create `src/aggregation/paper_json_builder.py`**
   Input: All SODB parquets for one paper
   Process: Join parquets, apply conflict resolution (GROBID > Nougat for structure, Nougat > GROBID for LaTeX), build paper.json v2
   Output: `data/enriched/{paper_id}.paper.json`

5. **Integrate SODB stages into `process_paper.py` orchestration**
   Add SODB stage checks to the existing Ray task. Papers skip stages where manifest shows complete + matching pipeline_hash.

**Dependencies**: Phase 1 complete. L1 Nougat cache operational.

**Validation**:
- Run on 50 papers. Verify `formulas.parquet` contains LaTeX for formula-rich papers.
- Verify `tex_valid` field correctly identifies ~85% as valid.
- Verify `figures.parquet` classifies hydrographs correctly on known papers.
- Run `paper_json_builder.py` and compare output with existing `paper.json` — new version should contain formulas, numeric_facts fields.

**Rollback**: `paper_json_builder.py` is new. Existing `build_paper_json()` in `pipeline.py` remains as fallback.

**Risk**: MEDIUM-HIGH. Formula extraction and figure classification require tuning.

---

### Phase 3 — Knowledge Graph Extension (2-3 weeks)

**Goal**: Load all SODB objects into Neo4j. Enable quantitative graph queries.

**Changes**:

1. **Extend Neo4j schema** (`neo4j_schema.cypher`)
   Add node types: `Formula`, `NumericFact`, `ScientificTable`, `Figure`
   Add relationships: `HAS_FORMULA`, `HAS_NUMERIC_FACT`, `MEASURES`, `FOR_BASIN`, `SOURCED_FROM`

2. **Extend `src/graph/build_graph.py`**
   Read from SODB global indexes (`_index/global_numeric_facts.parquet`, `_index/global_formulas.parquet`)
   Load Formula nodes, NumericFact nodes, evidence edges with provenance properties

3. **Add contradiction detection queries** (`src/graph/kg_analysis.py`)
   Cypher queries for: same watershed + same period + same metric + different value > 0.20

4. **Add meta-analysis query layer** (`src/analytics/meta_analysis.py`)
   DuckDB queries over `global_numeric_facts.parquet` for performance distribution analysis

**Dependencies**: Phase 2 complete. All SODB parquets populated for >1000 papers.

**Validation**:
- KG query: `MATCH (nf:NumericFact {metric: "NSE"}) RETURN avg(nf.value)` — should return ~0.75 for calibration, ~0.68 for validation.
- KG query: contradiction detection returns plausible results (not noise).
- DuckDB meta-analysis: method × metric breakdown returns sensible distributions.

**Risk**: MEDIUM. Neo4j schema changes require careful constraint management.

---

### Phase 4 — Pipeline.py Decomposition (3-4 weeks)

**Goal**: Eliminate the god object. Complete the parser boundary enforcement.

**Changes**:

1. **Decompose `pipeline.py`** into focused modules:
   - `src/ingestion/geo_extractor.py` — GeoNames + country/river patterns (async actor)
   - `src/ingestion/embedding_classifier.py` — cosine similarity classification
   - `src/ingestion/paper_assembler.py` — JSON construction (now uses SODB, not raw extraction)
   - Remove remaining `lxml` imports from `pipeline.py`

2. **Create `src/actors/geonames_actor.py`**
   Async Ray actor with SQLite cache. Replace synchronous `geonames_lookup()`.

3. **Delete `src/ingestion/tei_to_sections.py`** (3,221 lines dead code)

4. **Unify Neo4j writers** (remove `src/graphstore/neo4j_writer.py` duplicate)

5. **Add `schema_version` to TEIDocument** and `parser_capabilities` field

**Dependencies**: Phase 3 complete. SODB fully operational. paper_json_builder.py canonical.

**Validation**: Full test suite passes (>372 tests). No `lxml` import outside `src/document/parser.py`. Pipeline processes 100 papers end-to-end via new path.

**Risk**: HIGH. Touches the live production path. Requires 2-week migration window with parallel running of old and new paths.

---

## 7. Critical Path

### What MUST Happen First

```
1. Phase 0 (bug fixes) → immediately enables NSE extraction
   WITHOUT THIS: The SODB can be perfectly implemented and still store
   0% NSE data because metric_text will exclude it.

2. src/sodb/schemas.py → before ANY SODB writer
   Without a schema source of truth, different writers will produce
   incompatible parquet files.

3. L1 Nougat cache → before connecting NougatRegionPipeline to main pipeline
   Without the cache, every pipeline re-run retriggers full GPU inference.
   With 18.5 days GPU time for the full corpus, this is a hard blocker.

4. regions.parquet as handoff contract → before any extraction stage reads Nougat output
   Without this contract, every extraction component needs its own Nougat integration.
```

### What Blocks Everything Else

**The Phase 0 fixes block all downstream scientific value**
If `metric_text` excludes methods, the SODB will be perfectly built with 0 NSE facts. Every phase downstream of Phase 0 depends on Phase 0 being correct.

**The L1 cache blocks practical operation**
Without it, re-running any part of the pipeline retriggers 18.5 days of GPU inference. No practical iteration is possible.

**`regions.parquet` schema blocks all extraction components**
All three extraction stages (formula, table, figure) read from `regions.parquet`. Its schema must be finalized before any extractor is implemented.

### What Can Be Delayed

- Phase 4 (pipeline.py decomposition) — the god object is ugly but functional. Can wait until Phases 1-3 are proven.
- Figure classification (Phase 2) — high complexity, lower immediate scientific value than NumericFact extraction.
- Contradiction detection (Phase 3) — requires sufficient corpus coverage to be meaningful.
- Async GeoNames actor — synchronous GeoNames is slow but not blocking.

---

## 8. Production Architecture Target

### Actor Topology

```
Ray Cluster
├── Driver (orchestration, DuckDB writes)
│   ├── PipelineRegistry (single writer, RLock)
│   └── SODBIndexer (global index rebuilds, on-demand)
│
├── Stage 1: GROBID Parsing (8 concurrent workers)
│   └── GROBIDWorker × 8          (num_cpus=1, num_gpus=0)
│
├── Stage 2: Nougat Inference (2 GPU actors)
│   ├── NougatActor × 2           (num_cpus=2, num_gpus=0.5 each)
│   │   └── L1 cache check → inference → return dict
│   └── SODBRegionWriterActor × 2 (num_cpus=1, num_gpus=0)
│       └── atomic_write_parquet → manifest update
│
├── Stage 3: Extraction (4-8 concurrent, CPU-only)
│   ├── TableExtractorActor × 2   (reads TABLE_BODY regions)
│   ├── FormulaExtractorActor × 2 (reads FORMULA regions)
│   └── FigureClassifierActor × 2 (reads FIGURE_BODY regions)
│
├── Stage 4: Grounding (4 concurrent)
│   └── OntologyGrounderActor × 4 (Ollama for ambiguous + L4 grounding cache)
│
└── Stage 5: Aggregation (8 concurrent)
    └── PaperJsonBuilder × 8      (reads all SODB parquets → paper.json)
```

### GPU Scheduling and Caching

**L1 Cache (Nougat region-level)**
Key: `SHA256(f"{model_name}:{model_version}:{crop_image_sha256}")`
Store: `data/sodb/_cache/nougat/{key[:2]}/{key}.json`
Hit rate on re-runs: ~95%
Impact: 18.5 days → 22 hours for re-runs

**L4 Cache (Ontology grounding)**
Key: `SHA256(raw_text + ":" + entity_type)`
Store: `data/sodb/_cache/grounding/{key[:2]}/{key}.json`
Impact: eliminates Ollama calls for 80%+ of re-run grounding

**Priority scheduling for GPU work**

```python
class RegionPriority(IntEnum):
    TABLE_WITH_METRICS   = 1  # Tables with NSE/KGE column patterns
    FORMULA              = 2  # All formula regions
    TABLE_GENERAL        = 3  # Tables without metric patterns
    FIGURE_WITH_TEXT     = 4  # Hydrographs, calibration plots
    FIGURE_IMAGE_ONLY    = 5  # Maps, photographs
    SKIP                 = 99 # GROBID output sufficient (table row count matches TEI)
```

### Parquet Indexing Strategy

Per-paper parquets are the authoritative store. Global indexes are rebuilt:
- After each full corpus run
- On-demand via `src/sodb/reader.py rebuild_global_indexes()`
- DuckDB UNION ALL over all per-paper parquets (takes ~30s for 3,692 papers)

Partitioning for `global_numeric_facts.parquet`:
```python
# Partition by metric for fast per-metric queries
pq.write_to_dataset(table, root_path="_index/numeric_facts_partitioned/",
                    partition_cols=["metric"])
```

### Vector Synchronization

After SODB parquets are written, ChromaDB sync:
```python
# LayoutAwareChunker reads from TEIDocument (already in memory)
# ChromaDB upsert uses SODB paper_id for deduplication
chunks = LayoutAwareChunker(strategy="sentence").chunk(doc)
VectorStore().upsert_document_chunks(chunks, encode_fn(chunk_texts))
```

No change needed to the ChromaDB path (already done in SDOM_MIGRATION.md C2).

---

## 9. Final Architecture Verdict

### Current Maturity Assessment

| Dimension | Score | Assessment |
|-----------|-------|------------|
| Ingestion infrastructure | 9/10 | Production-grade. FailureType, PipelineRegistry excellent. |
| Parser abstraction | 7/10 | TEIDocument correct. Partial wiring. God object co-exists. |
| Extraction quality | 4/10 | Methods good. Metrics: 0%. Satellites: 27%. Critical bugs. |
| Ontology completeness | 6/10 | v2 complete. 5/11 files dead. Disambiguation unloaded. |
| Scientific evidence layer | 1/10 | SODB does not exist. NumericFacts not in main pipeline. |
| Knowledge graph | 5/10 | Paper/method/entity nodes. No NumericFact, Formula, Figure nodes. |
| Retrieval | 5/10 | SPECTER2 wired. 43% coverage. Citation boost inactive. |
| **True scientific value** | **4.5/10** | Accounts for 0% NSE, 73% satellite loss, no SODB. |

### Comparison to Production Scientific Platforms

**vs Semantic Scholar (S2ORC)**
Semantic Scholar processes 200M+ papers. The architecture gap is primarily in:
1. Citation graph completeness (S2ORC: 200M+ CITES edges; GeoHydroAI: edges built, data not yet loaded for full corpus)
2. Entity disambiguation (S2ORC: UMLS + network disambiguation; GeoHydroAI: domain-specific KB, good for geo-hydrology)
3. Figure grounding (S2ORC: Semantic Reader multimodal; GeoHydroAI: architecture-ready, not implemented)

**vs OpenAlex**
OpenAlex is a data product, not a parsing pipeline. GeoHydroAI exceeds OpenAlex for domain-specific entity extraction within geo-hydrology. OpenAlex exceeds GeoHydroAI in bibliographic coverage and citation network scale.

**vs S2ORC format**
`TEIDocument` is structurally aligned with S2ORC. The primary gap: S2ORC has `has_pdf_parse` quality flags (analogous to `DocumentQuality`) and preserves section structure verbatim. GeoHydroAI's `Section` hierarchy infers nesting from GROBID `<div>` depth.

**vs Academic data platforms (CORE, Unpaywall, Semantic Scholar API)**
GeoHydroAI's domain-specific NumericFact extraction (NSE, KGE, PBIAS from calibration tables) is unique. No general-purpose platform extracts this level of domain-specific quantitative evidence. This is GeoHydroAI's primary scientific differentiation — once the critical bugs are fixed.

### Production Readiness

**Current state**: Not production-ready. 0% NSE extraction, 73% satellite loss, no SODB, disconnected Nougat pipeline.

**After Phase 0**: Research-ready. NSE/satellite extraction functional. paper.json scientifically valid.

**After Phase 1**: Data-platform-ready. SODB operational. Incremental improvement cycles possible.

**After Phase 2-3**: Production-ready for scientific intelligence queries. NumericFact graph enables meta-analysis. Formula library enables formula search.

**The irreducible architectural truth**: GeoHydroAI is 6-8 weeks of focused engineering away from being the most sophisticated domain-specific scientific intelligence platform for geo-hydrology in existence. The components are all present. The critical bugs are known. The migration path is clear. The bottleneck is execution.

---

*Synthesized from 11 audit/design documents. 10,949 total lines of architectural analysis.*
*Cross-document contradictions resolved: 4. Root causes confirmed: 10. Phases defined: 5.*
