# Аудит-Історія GeoHydroAI

**Версія**: 2.0 | **Дата**: 2026-06-09  
**Підстава**: повний аналіз AUDIT_v1/ (40 файлів)

---

## 1. Хронологія версій (v1 → поточна)

| Версія | Дата | Оцінка | Головна подія |
|--------|------|--------|---------------|
| **v0 (baseline)** | до 2026-05-12 | ~2.5/10 | Монолітний pipeline.py (2,148 рядків), god object, XML boundary violations, 0 citation graph edges |
| **v1** | 2026-05-12–13 | **3.5/10** | Перший аудит: TEI tail bug (methods=0), CITES не підключено, embedding_matcher crash, all-MiniLM domain-agnostic |
| **v2** | 2026-05-14 | **4.5/10** | 5 критичних виправлень: TEI tail fix, CITES wired, embedding fallback, citation retrieval, methods секція читається |
| **v2.1** | 2026-05-14 | **5.5/10** | Додаткові v2 fixes: judge fault tolerance, candidate schema, DEM trigger narrowing |
| **v3** | 2026-05-15 | **6.8/10** | 100-paper regression audit, 4 judge bugs fixed, FP suppression, embedding clamping, geo-filter |
| **v4** | 2026-05-15 | **5.5/10** | Root cause analysis: 10 системних помилок ranked. Section routing 24% fail виявлено. |
| **v5** | 2026-05-18 | **6.0/10** | pipeline.py рефакторинг (2,960→stages/), 3 нові bugs fixed, 3,640 papers run |
| **post-v5** | 2026-05-20 | **5.9/10** | Повний корпус оброблено (3,546 papers), BUT регресія: ChromaDB 768 vs 384 dim (100% failure), normalization не запущено |
| **post-ChromaDB** | 2026-05-20 | **7.5/10** | ChromaDB rebuilt (flood_papers_768d, 986K chunks), normalization run, enrichment run, 5 metric patches |
| **post-enrichment** | 2026-05-24 | **~7.5/10** | Neo4j 57,603 nodes/92,003 CITES, 4,587 enriched, missing reference analysis (60,678 DOIs) |
| **поточна** | 2026-06-09 | **~7.5/10** | ~4,850 paper.json, задокументовані відкриті проблеми |

---

## 2. Детальна хронологія виправлень

### Цикл v1 → v2 (2026-05-12–14)
*Файли: AUDIT_v1/PIPELINE_ANALYSIS.md, SDOM_MIGRATION.md, ARCHITECTURE_REVIEW.md*

**Виявлені критичні проблеми:**
- TEI tail bug — `xml.tail` ігнорувався при text extraction → methods=0 для більшості papers
- CITES edges не підключені — citation graph builder побудований але не wywвикався
- `embedding_matcher` crash — `all-MiniLM-L6-v2` domain-agnostic (не підходить для наукового retrieval)
- lxml import в 3 файлах поза parser.py — порушення XML boundary
- `pipeline.py` 2,148-рядковий god object — XML parsing + geo + NER + embedding + JSON serialization в одному файлі

**Виправлення:**
- ✅ TEI tail fix → methods секція читається
- ✅ CITES wired до neo4j_writer
- ✅ Embedding fallback при crash
- ✅ lxml видалено з `process_paper.py`
- ✅ TEIParser підключений як primary parser
- ✅ LayoutAwareChunker → ChromaDB (coordinate-aware chunks)
- ✅ EmbeddingActor перемикається на SPECTER2 (domain-specific)

**Підсумок v2**: система почала реально знаходити методи та будувати citation graph.

---

### Цикл v2 → v3 (2026-05-14–15)
*Файли: PIPELINE_ANALYSIS_v2.md → PIPELINE_ANALYSIS_v3.md*

**Проведено**: 100-paper regression audit

**Виявлені проблеми:**
- 4 judge bugs: template leakage (`paper_id: '12345'` у промпті), null-safe access, timeout handling, verdict parsing
- Satellite detection: 37% coverage (acquisition verbs відсутні в _USAGE_POSITIVE)
- geo NER false positives: "Bulletin", "LP3" прийняті як країни
- Section routing: ~24% papers мають methods в "other" секції
- Від'ємні embedding scores (13.8% entities)

**Виправлення:**
- ✅ 4 judge bugs виправлено
- ✅ FP suppression для "HTTP", "J", "NNT"
- ✅ Geo FP filter (method-as-country)
- ✅ Embedding clamping max(0.0, score)
- ✅ geo_filter — відкидати geo entities якщо вони схожі на методи

**Score 6.8/10** — перший раз система вважається "research-capable"

---

### Цикл v3 → v5 (2026-05-15–18)
*Файли: PIPELINE_ROOT_CAUSE_ANALYSIS_v4.md, PIPELINE_ANALYSIS_v5.md*

**Root cause analysis v4**: 10 системних помилок ranked по severity

**Найкритичніші (O1-O3):**
- O1: Section routing fail — 24% papers → methods=0 → entities не знаходяться
- O2: 46.7% тексту в "other" → половина контенту поза structured extraction  
- O3: metric_text = abstract+results (без methods) → NSE/RMSE ~0%

**Архітектурний рефакторинг v5:**
- ✅ `pipeline.py` (2,960 рядків) → 326-рядковий оркестратор + `stages/` (5 файлів)
  - `stages/parse_stage.py` (213 рядків)
  - `stages/geo_stage.py` (437 рядків)
  - `stages/judge_stage.py` (410 рядків)
  - `stages/entity_stage.py` (1,681 рядків)
  - `stages/sdom_bridge.py` (109 рядків)
- ✅ BUG-1: datetime not JSON serializable → json_safe() fix
- ✅ BUG-2: MatchType.disambiguation відсутній у схемі → додано
- ✅ BUG-3: transformers==4.37.2 несумісний з sentence_transformers==5.4.1 → upgrade
- ✅ BUG-4: Конкуруючі Ray instances → ray stop --force protocol

**3,640 papers оброблено** (перший повний run)

---

### Цикл post-v5 (2026-05-20)
*Файли: PIPELINE_TRUST_REPORT_v2_UA.md*

**Критичні тихі збої виявлені після повного run:**

**ЗБІЙ 1 — ChromaDB 768 vs 384 dim (CRITICAL)**
- Симптом: `Collection expecting embedding with dimension of 384, got 768` логувалось як non-fatal
- Вплив: **100% нових papers (3,546) не проіндексовано в ChromaDB**
- Семантичний пошук зламаний для всього нового corpus
- Причина: стара колекція `flood_papers` (384-dim) лишилась після переходу на SPECTER2 (768-dim)
- Виправлення: Rebuild ChromaDB → `flood_papers_768d` (768-dim, SPECTER2)
  - 986,832 chunks / 3,686 papers ✅

**ЗБІЙ 2 — Normalization не запущена**
- Симптом: `data/normalized/` містив лише 132 файли після 3,546 paper run
- Вплив: Neo4j відображав лише 132 papers. 97% corpus не нормалізовано.
- Причина: normalization_runner не включено в основний pipeline flow
- Виправлення: окремий запуск normalization_runner ✅

**ЗБІЙ 3 — Task label propagation**
- Judge виправляє task label (наприклад: spectral→flood_frequency)
- Зберігає corrected_value в `llm_judge.task.corrected_value`
- Але `paper["task"]` на top-level лишається старим (null або неправильним)
- Вплив: ~75% papers з judge validation мають неправильний task label на top-level
- Виправлення: додано `paper["task"] = entities.get("task", paper.get("task", {}))` у judge_stage.py
- Retroactive repair: `src/orchestration/repair_task_labels.py` застосовано до 652 existing files

---

### Цикл Metric Extraction Patches (2026-05-20)
*Файл: METRIC_EXTRACTION_PATCH_REPORT.md*

**5 patches для metric extraction:**

**PATCH 1**: Metrics missing `accepted=True` (CRITICAL)
- Метрики обходили `run_entity_pipeline()` → accepted flag не встановлювався
- Downstream filter на `entity["accepted"]` тихо відкидав всі метрики
- Виправлення: `metrics = [dict(m, accepted=True) for m in metrics]`

**PATCH 2**: `resolve()` ігнорував `v2_metrics` (CRITICAL)
- `kb.resolve()` шукав тільки в EntityRecord glossary
- `KnowledgeBase.v2_metrics` (NSE, RMSE, KGE...) — окремий dict що resolve() не перевіряв
- Результат: `canonical_id = None` для всіх метрик навіть якщо вони в онтології
- Виправлення: новий метод `resolve_metric()` у KnowledgeBase

**PATCH 3**: Percent deduplication
- "Percent" витягувалась кілька разів як окрема метрика
- Виправлення: dedup logic у `extract_metrics()`

**PATCH 4**: NougatParser generic `do_*` NoneType scan
- `do_split()`, `do_analyze()` і т.д. могли повернути None
- Виправлення: None-safe scanning

**PATCH 5**: Nougat formula/table semantic bridge через regions.parquet
- Nougat output частково підключено до metric_text pipeline (Stage1Parser path)

---

### Цикл Missing Reference Recovery (2026-05-24)
*Файл: MISSING_REFERENCE_ANALYSIS.md*

**Аналіз citation gaps:**
- 60,678 unique DOIs у references corpus
- Тільки 41% resolved → 35,600 missing
- 48,391 highly-cited (≥10 citations)
- Топ missing: Random Forests (124K cit), LSTM (97K cit), Adam optimizer (85K cit)
- Найчастіше цитовані в corpus: Nash-Sutcliffe 1970 paper (221 references)

**CSV для пріоритизації**: `data/analytics/missing_rs_priority.csv`

---

## 3. Зведена таблиця виправлень

| Дата | ID | Патч | Файл | Вплив |
|------|----|------|------|-------|
| 2026-05-14 | FIX-01 | TEI tail bug | parse_stage.py | methods=0 → fixed |
| 2026-05-14 | FIX-02 | CITES wired | neo4j_writer.py | citation graph |
| 2026-05-14 | FIX-03 | Embedding fallback | embedding_classifier.py | no more crashes |
| 2026-05-14 | FIX-04 | lxml boundary fix | process_paper.py | architecture |
| 2026-05-14 | FIX-05 | SPECTER2 switch | embedding_actor.py | domain-specific embeddings |
| 2026-05-15 | FIX-06 | Judge fault tolerance | judge_stage.py | 4 judge bugs |
| 2026-05-15 | FIX-07 | FP suppression | entity_extractor.py | HTTP/J/NNT FPs |
| 2026-05-15 | FIX-08 | Geo FP filter | geo_stage.py | method-as-country FPs |
| 2026-05-15 | FIX-09 | Embedding clamp | entity_stage.py | max(0.0, score) |
| 2026-05-18 | FIX-10 | pipeline.py refactor | stages/ | 2960→326+stages |
| 2026-05-18 | FIX-11 | datetime JSON | ingestion/utils.py | 15% failure rate |
| 2026-05-18 | FIX-12 | MatchType.disambiguation | schemas/normalized_paper.py | schema validation |
| 2026-05-18 | FIX-13 | transformers compat | requirements.txt | blocking import error |
| 2026-05-20 | FIX-14 | ChromaDB rebuild 768d | reindex_chromadb.py | 100% indexing restored |
| 2026-05-20 | FIX-15 | PATCH 1: accepted=True | entity_pipeline.py | all metrics survive |
| 2026-05-20 | FIX-16 | PATCH 2: resolve_metric() | knowledge_loader.py | canonical_id for metrics |
| 2026-05-20 | FIX-17 | PATCH 3: Percent dedup | entity_extractor.py | clean metric list |
| 2026-05-20 | FIX-18 | PATCH 4: NougatParser NoneType | nougat_parser.py | crash prevention |
| 2026-05-20 | FIX-19 | Task label propagation | judge_stage.py | correct task labels |
| 2026-05-20 | FIX-20 | repair_task_labels.py | 652 existing files | retroactive fix |

---

## 4. Відкриті проблеми (станом на 2026-06-09)

### CRITICAL (блокують наукову якість)

| ID | Проблема | Де | Очікуваний вплив | LOC |
|----|----------|----|------------------|-----|
| O1 | **Section routing fail** — 24-28% papers без methods | stages/parse_stage.py | entity/metric extraction для ~900+ papers | ~30 |
| O2 | **46.7% тексту в "other"** — половина контенту некласифікована | stages/parse_stage.py | study_area, results з other не рятуються | ~50 |
| O3 | **metric_text без methods секції** | stages/entity_stage.py:run_entity_pipeline | NSE/RMSE ~0% coverage → одна строчка fix | **1** |

### HIGH

| ID | Проблема | Де | Fix |
|----|----------|----|-----|
| O4 | **Satellite acquisition verbs відсутні** | entity_extractor.py:_USAGE_POSITIVE | Додати "obtained from", "acquired from"... |
| O5 | **Disambiguation rules не завантажуються** | knowledge_loader.py | Додати до `files` dict |
| O6 | **Chunk ID collisions** (30-40% papers) | sdom_bridge.py / chunker | Додати позиційний salt до hash |

### MEDIUM

| ID | Проблема | Де | Fix |
|----|----------|----|-----|
| O7 | **study_type/task не на top-level** | pipeline.py:build_paper_json | 5 рядків serialization |
| O8 | **SENTINEL version collapse** (1/2/3 → SENTINEL) | knowledge_loader.py | Розділити в KB |
| O9 | **12 truncated Elsevier JSON** | data/literature/paper_json/ | pipeline re-run для 1-s2.0-* files |
| O10 | **DOI coverage 41%** в references | GROBID output | CrossRef DOI fallback |

### LOW / FUTURE

| ID | Проблема | Де |
|----|----------|----|
| O11 | Per-entity LLM scoring unimplemented (20% weight пустий) | entity_stage.py |
| O12 | Nougat pipeline disconnected від Legacy pipeline | nougat_region_pipeline.py |
| O13 | Немає CLI-оркестратора для SDOM pipeline | src/orchestration/ |
| O14 | geo NER шум ("Bulletin", "LP3" як countries) | geo_stage.py |
| O15 | Missing reference DOIs: 60,678 unresolved | data/analytics/missing_rs_priority.csv |

---

## 5. Пріоритетний список виправлень (наступний цикл)

| Priority | Fix | Очікуваний gain | LOC |
|----------|-----|-----------------|-----|
| **P0** | Додати `methods` до `metric_text` | NSE/RMSE coverage ~0% → ~70% | **1** |
| **P0** | Promote study_type/task to top-level | Фіксує Neo4j+dashboard для всіх papers | 5 |
| **P1** | Розширити `_USAGE_POSITIVE` (acquisition verbs) | Satellite +40-50% coverage | 8 |
| **P1** | Load `ontology_disambiguation_rules.json` | SCS/ANN/MLP disambiguation | 3 |
| **P1** | Fix chunk ID collisions (position salt) | Eliminate 30-40% upsert warnings | 5 |
| **P2** | Extend `section_tags()` keywords | Methods coverage 76% → ~85% | 10 |
| **P2** | `_reclassify_from_other` для study_area+results | Rescue misrouted content | 30 |
| **P2** | Split SENTINEL-1/2/3 у KB | SAR vs optical distinction | 15 |
| **P3** | CrossRef DOI fallback | DOI 41% → ~60% | ~50 |
| **P3** | CLI-оркестратор для SDOM pipeline | Новий pipeline можна запустити | ~200 |
| **P3** | Підключити Nougat до Legacy pipeline | Visual content у extraction | ~100 |

---

## 6. Аналіз файлів AUDIT_v1

| Файл | Тема | Ключовий висновок |
|------|------|-------------------|
| ARCHITECTURE_REVIEW.md | Principal audit v1 | God object pipeline.py, 2 parallel architectures, citation graph missing |
| PIPELINE_ANALYSIS.md | v1 baseline | TEI tail bug, methods=0, pipeline flow diagram |
| PIPELINE_ANALYSIS_v2.md | v2 post-fixes | 5 критичних виправлень, SPECTER2 switch |
| PIPELINE_ANALYSIS_v3.md | v3 100-paper audit | Section routing 24% fail, judge bugs, geo FP |
| PIPELINE_ROOT_CAUSE_ANALYSIS_v4.md | v4 root cause | 10 системних помилок ranked |
| PIPELINE_ANALYSIS_v5.md | v5 refactor | pipeline.py→stages/, 3 bugs fixed, 3640 run |
| PIPELINE_TRUST_REPORT_v2_UA.md | Post-v5 trust | 5.9/10, ChromaDB dim mismatch, normalization missing |
| PIPELINE_TRUST_REPORT_v2.md | Same (English) | — |
| METRIC_EXTRACTION_PATCH_REPORT.md | 5 metric patches | accepted=True, resolve_metric, Percent dedup |
| GEOHYDROAI_ARCHITECTURE.md | Deep architecture | Nougat internals (Swin+mBART), DPI analysis |
| SDOM_MIGRATION.md | SDOM migration plan | Phase 1 complete, Phase 2 in progress |
| SDOM_SODB_EXPLAINED_UA.md | SODB rationale (UA) | Чому paper.json недостатньо, 6 failure modes |
| SODB_DESIGN.md | SODB complete design | Scientific Object DB, Parquet schema, provenance |
| SDOM_AGENT_EXECUTION_PLAN.md | SDOM execution | Stage 0-2.5 agent plan |
| SDOM_SODB_MASTER_MIGRATION_PLAN.md | Migration roadmap | Full plan від Legacy до SDOM |
| MISSING_REFERENCE_ANALYSIS.md | Citation gaps | 60,678 unresolved DOIs, top missing papers |
| RETRIEVAL_AUDIT_v1.md | ChromaDB audit | 384-dim, 13,970 chunks, 20 queries, low diversity |
| ONTOLOGY_FORENSIC_AUDIT_v4.md | Ontology audit | Entity quality, alias coverage |
| ONTOLOGY_QA_AUDIT_v4.md | Ontology QA | Disambiguation, collision detection |
| SEMANTIC_CONSISTENCY_AUDIT_v1.md | Semantic consistency | — |
| SEMANTIC_CONSISTENCY_AUDIT_v4.md | Semantic consistency v4 | — |
| ENTITY_FP_AUDIT_v1.md | False positive audit | FP patterns, suppression rules |
| JUDGE_AUDIT_v1.md | LLM judge audit | Judge quality, trigger rate, bias |
| EMBEDDING_ANALYSIS_v1.md | Embedding analysis | Score distribution, negative scores |
| PROVENANCE_AWARE_RESEARCH_LAYER_REPORT.md | Provenance layer | DOI in evidence, anti-hallucination |
| RESEARCH_QUERY_LAYER_REPORT.md | Research query | Multi-source retrieval architecture |
| SCIENTIFIC_INFORMATION_LOSS_REPORT_v4.md | Info loss v4 | Де губляться наукові факти |
| SCIENTIFIC_INFORMATION_LOSS_REPORT_v5.md | Info loss v5 | Updated loss analysis |
| NOUGAT_REGION_PIPELINE.md | Nougat pipeline | 180/3875, regions.parquet structure |
| PIPELINE_RUNNING_GUIDE.md | Operations guide | Як запускати, env vars, monitoring |
| PIPELINE_OBSERVABILITY_REPORT.md | Observability | Logging, monitoring gaps |
| REPAIR_EXECUTION_REPORT_v1.md | Repair report | Task label repair, retroactive fix |
| NOTEBOOK_ANALYSIS_PLAN.md | Notebooks plan | Jupyter analysis plan |
| analiz_v5.md | Agent analysis | Current architecture ASCII diagram, modality silos |
| start_piplaine.md | Start guide | Quick start |
| generated_paper_v2.md | Generated paper v2 | Early version of review paper |
| paper_validation_report.md | Paper validation | Validating generated paper claims |

---

## 7. Матриця зрілості компонентів

| Компонент | v1 (05-12) | v3 (05-15) | v5 (05-18) | Поточна |
|-----------|------------|------------|------------|---------|
| TEI parsing | 4/10 | 7/10 | 7/10 | 7/10 |
| Section extraction | 2/10 | 4/10 | 4/10 | **4/10** ← відкрита проблема |
| Entity extraction | 3/10 | 6/10 | 6/10 | 6/10 |
| Metric extraction | 0/10 | 3/10 | 5/10 | **6/10** (after 5 patches) |
| KB normalization | 6/10 | 9/10 | 9.5/10 | 9.5/10 |
| LLM judge | 4/10 | 8/10 | 8/10 | 8/10 |
| Embedding pipeline | 3/10 | 7/10 | 8/10 | 8/10 |
| JSON serialization | 5/10 | 6/10 | 9/10 | 9/10 |
| Schema validation | 5/10 | 6/10 | 9/10 | 9/10 |
| Citation graph | 0/10 | 5/10 | 5/10 | 6/10 |
| ChromaDB retrieval | 2/10 | 5/10 | 2/10 (dim bug) | **9/10** (post-rebuild) |
| Neo4j graph | 0/10 | 5/10 | 5/10 | 7/10 |
| OpenAlex enrichment | 0/10 | 0/10 | 0/10 | 9/10 |
| NumericFacts | 0/10 | 0/10 | 5/10 | 7/10 |
| Code maintainability | 2/10 | 3/10 | 8/10 | 8/10 |
| Dashboard | 0/10 | 3/10 | 5/10 | 8/10 |
| Test coverage | 2/10 | 5/10 | 7/10 | **8/10** (606 tests) |
| **OVERALL** | **3.5/10** | **6.8/10** | **6.0/10** | **~7.5/10** |

---

## 8. Що залишається неправдою у системі

Це — відверта оцінка наукової достовірності системи на сьогодні.

### Що система *каже* vs що *насправді*

| Твердження системи | Реальність |
|--------------------|---------  |
| "3,875 papers processed" | ~4,850 paper.json, але ~24% без methods секції → entity extraction неповна |
| "Method extraction complete" | 76% papers мають methods, але metric_text не включає methods → NSE/RMSE ~0% coverage |
| "NumericFacts: 16,308" | Тільки з 667/3,680 papers з GROBID-розпізнаними таблицями |
| "Semantic search ready" | ChromaDB rebuilt і працює (після патчу) ✅ |
| "study_type classified" | Завжди null на top-level через propagation bug (виправлено у 652 papers retroactively) |
| "Disambiguation rules active" | 15 правил у файлі, але knowledge_loader їх не завантажує |
| "SENTINEL detection" | Sentinel-1/2/3 collapse до "SENTINEL" — SAR vs optical не розрізняється |

### Найбільший архітектурний борг

**Nougat pipeline — острів**: `nougat_region_pipeline.py` пройшов 180 papers, але ні Legacy pipeline, ні Neo4j, ні ChromaDB не читають results.parquet. 180 papers × повне visual analysis = **дані є**, але **ніхто не читає**.

Виправлення: SODB design (AUDIT_v1/SODB_DESIGN.md) — regions.parquet як handoff contract між Nougat і extraction pipeline. Поки не реалізовано.

---

## Аудит графу Neo4j і NumericFact (2026-10-03)

14 проблем у шарах Paper, CITES, USES_METHOD, INVESTIGATES і NumericFact; 13 виправлено в коді та даних, лишився 1 (порожній візуальний шар). Подробиці, причини, збої під час виправлення і список залишку: [GRAPH_AUDIT_20261003.md](GRAPH_AUDIT_20261003.md).
