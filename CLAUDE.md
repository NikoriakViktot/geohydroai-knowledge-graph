# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

---

## Services (must be running)

```bash
docker compose up -d          # GROBID (8070) + Neo4j (7687/7474)
ollama serve &                # LLM judge (port 11434)
ollama pull mistral-nemo:12b
```

Neo4j credentials (hardcoded in docker-compose.yml): `neo4j / python2024`

---

## Commands

### Tests

```bash
# Full suite (606 tests, no GPU/Ollama/Ray required)
python -m pytest tests/ -q

# Individual test groups
python -m pytest tests/test_11_stage0.py  -v   # Stage 0 (48 tests)
python -m pytest tests/test_12_stage1.py  -v   # Stage 1 (51 tests)
python -m pytest tests/test_13_stage2.py  -v   # Stage 2 (51 tests)
python -m pytest tests/test_14_stage25.py -v   # Stage 2.5 (84 tests)
python -m pytest tests/test_parser_routing.py -v

# Disable Nougat for fast CPU-only parser tests
NOUGAT_ENABLED=false python -m pytest tests/test_parser_routing.py -v
```

Tests never require Ollama, Ray, or a GPU — all actors are mocked in `conftest.py`.

### Legacy Ray Pipeline (Stage 1 — XML → paper.json)

```bash
# Required env vars (always use these — prevents Ray/Rust deadlocks)
TOKENIZERS_PARALLELISM=false \
RAYON_NUM_THREADS=1 \
OMP_NUM_THREADS=2 \
OLLAMA_MODEL=mistral-nemo:12b \
OLLAMA_URL=http://localhost:11434 \
SPACY_MODEL=en_core_web_sm \
.venv/bin/python3 -m src.orchestration.pipeline_runner --workers 3
```

`--workers 3` is the stable limit for this WSL2 system (~3 GB RAM). Never use `--workers 8` or higher.

### Post-Pipeline Steps

```bash
# Normalization (ontology grounding; run after pipeline_runner)
python -m src.orchestration.normalization_runner \
    --input-dir data/literature/paper_json \
    --output-dir data/normalized

# ChromaDB re-index (768-dim SPECTER2; run if collection is empty or corrupt)
# WARNING: ChromaDB 1.5.9 bug — verify vs.count() immediately after run; if HNSW
# fails to reload, delete the collection and re-run into a fresh one.
TOKENIZERS_PARALLELISM=false EMBEDDING_MODEL=allenai/specter2_base \
python -m src.orchestration.reindex_chromadb \
    --xml-dir data/literature/grobid_xml \
    --json-dir data/literature/paper_json \
    --collection-name flood_papers_768d \
    --batch-size 32

# OpenAlex enrichment (citation counts, author/institution data)
python -m src.orchestration.enrichment_runner

# Build Neo4j knowledge graph
python -m src.graph.build_graph \
    --uri bolt://localhost:7687 --user neo4j --password python2024

# NumericFact loader (TEI tables → Neo4j NumericFact nodes)
python -m src.graph.table_kg_loader

# Parquet analytics layer
python -m src.enrichment.build_parquet_layer
```

### NougatRegionPipeline (Stage 1.5 — visual regions)

```bash
# Recommended (3 workers, GPU ~15-25%)
TOKENIZERS_PARALLELISM=false NOUGAT_GPU_FRACTION=1.0 \
python -m src.ingestion.nougat_region_pipeline --workers 3

# CPU-only (no CUDA)
TOKENIZERS_PARALLELISM=false NOUGAT_GPU_FRACTION=0 \
python -m src.ingestion.nougat_region_pipeline --workers 2
```

### Registry CLI

```bash
python -m src.registry.cli stats          # overall status
python -m src.registry.cli failures -v    # error details
python -m src.registry.cli pending        # unprocessed papers
python -m src.registry.cli reset-stale    # unblock hung tasks
python -m src.registry.cli query "SELECT paper_id, status FROM pipeline_registry LIMIT 10"
```

### Dashboard

```bash
python -m src.dashboard_dash.app   # http://localhost:8050
```

---

## Architecture: Two Parallel Pipelines

This repo has **two separate pipelines** that coexist:

### A. Legacy Ray Pipeline (operational, 3 546 papers complete)

```
GROBID TEI XML  →  pipeline_runner.py  →  paper.json  →  normalization_runner  →  enrichment_runner  →  build_graph
src/ingestion/pipeline.py (entity extraction, LLM judge, embeddings)
src/orchestration/process_paper.py (Ray remote task per paper)
src/actors/ (SpacyActor, EmbeddingActor, OllamaActor — Ray remote)
```

Output directories: `data/literature/paper_json/`, `data/normalized/`, `data/enriched/`

### B. New SDOM Pipeline (under development, tests pass, not yet run on full corpus)

```
PDF  →  Stage0Ingestor  →  Stage1Parser  →  Stage2Engineer  →  SemanticValidatorStage
        data/raw/           data/parsed/     data/sodb/           data/sodb/ (annotations)
```

- `src/ingestion/stage0/ingestor.py` — SHA-256 content-addressed PDF ingestion
- `src/ingestion/stage1/parser_runner.py` — structured Parquet artifacts per paper; reads `regions.parquet` from NougatRegionPipeline if available
- `src/ingestion/stage2/engineer.py` — scientific object classification, no NLP/embeddings
- `src/semantic_objects/semantic_validator.py` — 15 IF-THEN rules, semantic graph edges
- No CLI orchestrator exists yet for this pipeline; run programmatically

### NougatRegionPipeline (Stage 1.5 — bridge between A and B)

```
PDF + TEI XML  →  nougat_region_pipeline.py  →  data/sodb/{paper_id}/regions.parquet
                                                 data/nougat_regions/{paper_id}/crops/*.png
```

180/3 875 papers processed. Stage1Parser reads `regions.parquet` automatically when present.

---

## SDOM: Scientific Document Object Model (`src/document/`)

`TEIDocument` is the **single canonical domain object**. Every parser produces it; every consumer reads it.

```python
from src.document import TEIDocument, TEIParser, ParserRouter, LayoutAwareChunker
```

**Parser hierarchy**:
- `TEIParser` — GROBID TEI XML → TEIDocument (primary; text PDFs)
- `NougatParser` — PDF images → Nougat → TEIDocument (scanned/formula-heavy)
- `HybridParser` — GROBID + Nougat merged → TEIDocument
- `ParserRouter` — dispatches based on `PARSER_STRATEGY` env var

**Merge policy** (HybridParser): GROBID wins structure/metadata/coords; Nougat wins formulas/visual text.

**`LayoutAwareChunker`** splits `TEIDocument` into `DocumentChunk` objects preserving section, page, bbox, and chunk type (`abstract` / `body` / `figure` / `table` / `formula`).

---

## Architectural Invariants

These rules are enforced by design — violating them breaks the pipeline:

| Rule | Why |
|------|-----|
| **Only `src/document/{parser.py, tei_io.py}` may import `lxml`** — other modules use the `tei_io` facade | Isolates XML parsing attack surface |
| **Only `src/document/{parser.py, nougat_parser.py, pdf_io.py}` may import `fitz`** — other modules use the `pdf_io` facade | Isolates PDF byte handling |
| **GROBID is the only source of coordinates** — Nougat never claims `COORDINATES` capability | Nougat reads images, not PDF geometry |
| **Never mix XML parsing, OpenAlex HTTP, and Neo4j writes in one stage** | Each stage must be independently restartable |
| **`TEIDocument` is frozen** (`@dataclass(frozen=True)`) — never mutate after construction | Prevents silent downstream corruption |
| **`GraphWriter` uses only Cypher `MERGE`** | No accidental DELETEs reach Neo4j |
| **Ray actors are the only holders of GPU handles** | Centralises CUDA memory management |
| **paper.json is built from SODB in the new pipeline** — never from raw XML directly | Prevents GPU re-inference on downstream-only changes |

---

## Known Critical Issues (as of 2026-05-20)

1. **ChromaDB rebuilt as `flood_papers_768d`** ✅ — 986,832 chunks from 3,686 papers indexed at 768-dim (SPECTER2). Retrieval smoke test passed 10/10 queries. Old 384-dim `flood_papers` archived in `.chromadb_backup_20260520/`. Use `COLLECTION_NAME=flood_papers_768d` (default in both config files). **ChromaDB 1.5.9 bug**: do NOT let the re-indexer exit with items in `embeddings_queue` (max_seq_id gap corrupts HNSW on reload) — if re-running, verify `vs.count()` matches expected immediately after the run.

2. **Task label propagation bug fixed** ✅ — Added `paper["task"] = entities.get("task", paper.get("task", {}))` before `return paper` in both `apply_judge_verdict` and `apply_constraints` in `src/ingestion/stages/judge_stage.py`. Retroactive repair applied to 652 existing paper.json files via `src/orchestration/repair_task_labels.py`.

3. **5 metric extraction patches applied** ✅ — `accepted=True` always set for metrics (PATCH 1); `resolve_metric()` added to `KnowledgeBase` for v2_metrics canonical IDs (PATCH 2); Percent dedup in `extract_metrics()` (PATCH 3); generic `do_*` NoneType scan in `NougatParser` (PATCH 4); Nougat formula/table semantic bridge via `regions.parquet` (PATCH 5). See `METRIC_EXTRACTION_PATCH_REPORT.md`.

4. **12 truncated paper.json files** — All `1-s2.0-*` Elsevier papers. JSON is truncated mid-write (not NaN/Infinity — actual file truncation). Not in `data/normalized/`. TEI XMLs exist so ChromaDB re-indexer covers them. Require pipeline re-run to fix paper.json.

5. **OpenAlex enrichment complete** ✅ — 2,963 enriched / 620 no-DOI / 97 failed. Output: `data/enriched/` (3,680 files).

6. **Neo4j graph stale** — Reflects only pre-existing normalized papers. Run after ChromaDB re-index and retrieval smoke test:
   ```bash
   docker compose up -d  # ensure Neo4j running
   python -m src.graph.build_graph --uri bolt://localhost:7687 --user neo4j --password python2024
   ```

---

## Data Directory Map

```
data/literature/
    pdf/              ← input PDFs (3 875)
    grobid_xml/       ← GROBID TEI XML (6 014)
    paper_json/       ← legacy pipeline output (3 691 files; 12 truncated JSON)
data/normalized/      ← normalization_runner output (3 680 files)
data/enriched/        ← OpenAlex enrichment (3 680 files; 2 963 enriched)
data/sodb/            ← per-paper SODB Parquet (3 692 dirs; 180 with Nougat regions)
data/nougat_regions/  ← Nougat crop images (180 papers, 2.1 GB)
data/raw/             ← Stage0 content-addressed PDFs (1 test paper)
data/parsed/          ← Stage1 structured Parquet (does not exist — not run)
data/registry/        ← pipeline_registry.duckdb (DuckDB single-writer)
data/analytics/       ← parquet analytics tables (14 files, stale)
data/cache/           ← SQLite API response caches
```

---

## Key Source Locations

| What | Where |
|------|-------|
| Ray pipeline entry point | `src/orchestration/pipeline_runner.py` |
| Single-paper Ray task | `src/orchestration/process_paper.py` |
| Entity extraction (legacy) | `src/ingestion/pipeline.py` |
| Knowledge base (JSON-driven patterns) | `src/ingestion/knowledge/` |
| Ontology (1 118 entities, 3 161 aliases) | `src/ontology/` |
| Normalization | `src/normalization/` |
| SDOM models | `src/document/models.py` |
| SODB manifest | `src/document/sodb_manifest.py` |
| Ray actors | `src/actors/` |
| Pipeline registry | `src/registry/pipeline_registry.py` |
| Neo4j graph build | `src/graph/build_graph.py` |
| NumericFact extraction | `src/extraction/table_extractor.py` |
| Stage 2.5 semantic validation | `src/semantic_objects/semantic_validator.py` |
| Dashboard | `src/dashboard_dash/app.py` |
| All env-var config | `src/config/settings.py` |

---

## Environment Variables (stability-critical)

Always set these when running Ray-based code:

```bash
TOKENIZERS_PARALLELISM=false   # prevents HuggingFace tokenizer deadlock in forked Ray workers
RAYON_NUM_THREADS=1            # prevents Arrow/Rust Rayon thread pool contention
OMP_NUM_THREADS=2              # limits OpenMP (numpy/SpaCy BLAS)
```

Parser strategy (default `grobid_with_nougat_fallback`):
```bash
PARSER_STRATEGY=grobid_only     # no GPU required
PARSER_STRATEGY=nougat_only
PARSER_STRATEGY=hybrid
PARSER_STRATEGY=auto
NOUGAT_ENABLED=false            # kill-switch; disables model load
```
