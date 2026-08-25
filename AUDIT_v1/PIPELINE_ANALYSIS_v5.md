# GeoHydroAI Pipeline — Повний аналіз v5
**Дата**: 2026-05-18  
**Охоплення**: Архітектурний рефакторинг + запуск повного корпусу (3,692 XML)  
**Базова версія**: v4 (PIPELINE_ROOT_CAUSE_ANALYSIS_v4.md)

---

## 0. Еволюція пайплайну v1 → v5

| Версія | Дата | Подія | Зрілість |
|--------|------|-------|----------|
| **v1** | 2026-05-14 | Baseline audit (20 papers). TEI tail bug (methods=0), CITES не підключено, embedding_matcher crash | **3.5/10** |
| **v2** | 2026-05-14 | 5 критичних виправлень: TEI tail fix, CITES wired, embedding fallback, citation retrieval. Методи читаються. | **4.5/10** |
| **v3** | 2026-05-15 | 100-paper regression audit. 4 judge баги виправлено. DEMs trigger звужено. Section routing 24% fail. | **5.5/10** |
| **v4** | 2026-05-15 | Root cause analysis: 10 системних помилок ранжовані. Mettic text + satellite usage filter + disambiguation gap | **5.5/10** |
| **v5** | 2026-05-18 | **Цей цикл**: архітектурний рефакторинг pipeline.py → stages/, 3 нові баги виправлено, запуск 3,640 papers | **6.0/10** |

---

## 1. Що зроблено в цьому циклі

### 1.1 Архітектурний рефакторинг: pipeline.py → stages/

Головний файл `src/ingestion/pipeline.py` розбито з **2,960 рядків** на тонкий оркестратор + 4 stage-файли:

| Файл | Рядків | Вміст |
|------|--------|-------|
| `src/ingestion/pipeline.py` | **326** | Оркестратор: `build_paper_json()` + `run()` + імпорти |
| `stages/parse_stage.py` | 213 | TEI/XML парсинг, секції, `normalize_country_name` |
| `stages/geo_stage.py` | 437 | Всі GEO константи, NER helper, geocode |
| `stages/judge_stage.py` | 410 | OllamaJudge, `needs_judge()`, `apply_judge_verdict()` |
| `stages/entity_stage.py` | 1,681 | KB, класифікація, ентіті пайплайн, `run_entity_pipeline()` |
| `stages/sdom_bridge.py` | 109 | SDOM lazy import bridge |
| **Разом stages/** | **2,850** | +importer re-exports в pipeline.py |

**Граф залежностей** (направлений ациклічний):
```
utils ← parse_stage ← geo_stage ← entity_stage
                                 ↑
                         judge_stage ←┘
pipeline.py ← (всі stage файли)
```

**Результат**: backward-compatible — всі модулі, що імпортують з `src.ingestion.pipeline`, продовжують працювати без змін.

---

### 1.2 Виправлені баги

#### BUG-1: `datetime` не серіалізується в JSON — **CRITICAL**

**Симптом**: `TypeError: Object of type datetime is not JSON serializable` при записі кожного paper.json

**Де**: `pipeline_runner.py:282` → `json.dump(json_safe(result), fh, ...)`

**Причина**: `json_safe()` в `src/ingestion/utils.py` обробляла `np.float32`, `np.int64`, `Path`, але не `datetime`/`date` об'єкти, які потрапляли в paper з GROBID-метаданих (дата публікації).

**Вплив**: ~15-20% papers падали з цією помилкою (11 з перших 74 = **14.9% failure rate**).

**Виправлення** (`src/ingestion/utils.py`):
```python
def json_safe(obj):
    import datetime as _dt
    ...
    if isinstance(obj, (_dt.datetime, _dt.date)):
        return obj.isoformat()
    return obj
```

---

#### BUG-2: `match_type='disambiguation'` відхиляється схемою — **MEDIUM**

**Симптом**: `[schema] paper failed validation: Input should be 'alias', 'exact', 'semantic', 'unknown' or 'error'`

**Де**: `src/schemas/normalized_paper.py` — `MatchType` enum не містив `'disambiguation'`

**Причина**: `ontology_matcher.py` повертає `match_type="disambiguation"` при context-aware disambiguation (крок 0c), але схема це значення не знала.

**Вплив**: Schema validation fails для ~5-10% ентіті (ті що проходять через disambiguation path). Папери все одно записуються (validation non-fatal), але `NormalizedPaper.model_validate()` кидає виключення в логах.

**Виправлення** (`src/schemas/normalized_paper.py`):
```python
class MatchType(str, Enum):
    alias          = "alias"
    exact          = "exact"
    semantic       = "semantic"
    disambiguation = "disambiguation"   # ← НОВИЙ
    unknown        = "unknown"
    error          = "error"
```

**Супутнє виправлення** (`src/normalization/ontology_matcher.py`): якщо disambiguation знайшла sense але `canonical_id = None` (normalize_alias не відповів), тепер fallthrough на alias/exact/semantic замість повернення `{canonical_id: None, match_type: 'disambiguation'}` — що порушило б validator `canonical_id_required_when_matched`.

---

#### BUG-3: `transformers==4.37.2` несумісний з `sentence_transformers==5.4.1` — **BLOCKING**

**Симптом**: `ImportError: cannot import name 'UdopConfig' from 'transformers'` при старті будь-якого воркера

**Причина**: `sentence_transformers==5.4.1` вимагає `transformers>=4.40.0` (для `UdopConfig`), але в `.venv` був пінований `transformers==4.37.2`.

**Виправлення**: `pip install "transformers>=4.40.0"` → апгрейд до `transformers==5.8.1`

**Перевірка сумісності**: `VisionEncoderDecoderModel` та `NougatProcessor` (використовуються в `NougatParser`) протестовано — продовжують працювати.

---

#### BUG-4: Конкуруючі Ray-інстанції — **OPERATIONAL**

**Симптом**: `RuntimeError: Module EventHead failed to start. Received EOF from pipe.` при запуску нового Ray. Dashboard не стартує.

**Причина**: Кілька попередніх запусків `pipeline_runner` залишили живі Ray процеси. При новому запуску Ray не може зайняти порти.

**Виправлення**: `ray stop --force` + один чистий запуск.

---

### 1.3 Запуск повного корпусу

```
Корпус:  3,692 XML файлів у data/literature/grobid_xml/
Вже оброблено (pre-filter): 52 papers (.paper.json вже існують)
До обробки: 3,640 papers
Конфігурація: --workers 3, MAX_IN_FLIGHT=3, ~2.4 GB memory budget
Змінні середовища: TOKENIZERS_PARALLELISM=false RAYON_NUM_THREADS=1
```

**Стан на момент зупинки для виправлення BUG-1**: 128 papers written, 11 failures (all datetime)

**Поточний стан**: pipeline ЗУПИНЕНО для застосування виправлень. Потрібен перезапуск.

**Команда перезапуску**:
```bash
TOKENIZERS_PARALLELISM=false RAYON_NUM_THREADS=1 \
  nohup .venv/bin/python -m src.orchestration.pipeline_runner --workers 3 \
  > /tmp/pipeline_run_$(date +%Y%m%d_%H%M).log 2>&1 &
```

---

## 2. Поточний стан після v5 виправлень

### 2.1 Що зараз ДОБРЕ ПРАЦЮЄ

| Компонент | Оцінка | Статус |
|-----------|--------|--------|
| KB metadata coverage | 10/10 | 100% ✅ |
| DOI syntax (0% malformed) | 10/10 | ✅ |
| Judge fault tolerance | 9/10 | Виправлено у v3 ✅ |
| Paper metadata (title/doi/year) | 9/10 | 94% title, 80% DOI ✅ |
| Alias normalization | 10/10 | HEC-HMS, SWAT, RF → conf=1.0 ✅ |
| Confidence propagation + edge lineage | 9/10 | ✅ |
| json_safe datetime handling | 10/10 | Виправлено у v5 ✅ |
| MatchType schema validation | 9/10 | Виправлено у v5 ✅ |
| pipeline.py maintainability | 8/10 | Рефакторинг у v5 ✅ |
| Ray distributed execution | 8/10 | Sliding window, 3 workers ✅ |
| Idempotency | 9/10 | Level-1 pre-filter + Level-2 task guard ✅ |

### 2.2 Відомі відкриті проблеми

| # | Проблема | Severity | Версія виявлення | Файл | Очікуваний вплив |
|---|---------|----------|------------------|------|-----------------|
| O1 | Section router fail — 24% papers | CRITICAL | v3 | `parse_stage.py:section_tags()` | methods=0 → ентіті не знаходяться |
| O2 | `other` секція = 46.7% всього тексту | CRITICAL | v3 | `parse_stage.py` | Половина контенту поза structured extraction |
| O3 | metric_text не включає methods | CRITICAL | v4 | `entity_stage.py:run_entity_pipeline()` | NSE/RMSE 0% coverage |
| O4 | Satellite usage filter (acquisition verbs) | HIGH | v4 | `entity_extractor.py:_USAGE_POSITIVE` | ~73% papers без satellites |
| O5 | Disambiguation rules не завантажуються | HIGH | v4 | `ontology_matcher.py` | SCS/ANN/MLP плутаються |
| O6 | Від'ємні embedding scores (13.8%) | HIGH | v3 | `embedding_matcher.py` | Entity acceptance нестабільний |
| O7 | study_type не промоується на top-level | MEDIUM | v4 | `entity_stage.py` serialization | Neo4j читає None |
| O8 | Sentinel version collapse (1/2/3 → SENTINEL) | MEDIUM | v4 | `knowledge_loader.py` | SAR vs optical не розрізняється |
| O9 | DOI coverage 41% в references | MODERATE | v3 | GROBID output | Citation graph неповний |
| O10 | Per-entity LLM scoring unimplemented | LOW | v3 | `entity_stage.py` | 20% scoring weight мертвий |
| O11 | Дубльовані chunk IDs в ChromaDB upsert | LOW | v5 (live) | `sdom_bridge.py` / chunker | Non-fatal, warns on every paper |

---

## 3. Детальний аналіз відкритих проблем

### O1+O2: Section routing failure (24% papers)

**Проблема**: `section_tags()` маршрутизує секції за заголовком. Якщо заголовок не містить ключових слів (`"method"`, `"approach"`, etc.), контент потрапляє в `other`.

**Поточна логіка `_reclassify_from_other()`**:
```python
# Рекласифікує other → methods ТІЛЬКИ якщо methods == '' і other >= 3000c і density >= 5/1000
```

Це означає:
- Якщо `methods` має хоч щось → `other` контент ІГНОРУЄТЬСЯ
- `study_area`, `results`, `data_sources` з `other` НІКОЛИ не рятуються

**Приклади провалу** (з v3 deep spot):
- `Investigationofflowresistance...`: methods=0, 13,575c в `other` (вся методологія)
- `3003.tei`: methods=0, frequency analysis в `other`

**Рекомендація**:
```python
# Додати до INLINE_HEADINGS:
"design", "approach", "framework", "calibration", "experiment",
"procedure", "analysis", "implementation", "algorithm"

# Розширити _reclassify_from_other для study_area і results:
if not sections.get("study_area") and other_has_geo_density():
    sections["study_area"] = rescue_from_other("study_area_keywords")
```

---

### O3: metric_text без methods секції

**Поточний код** (entity_stage.py):
```python
metric_text = " ".join([abstract, results, conclusion])
# ← methods ВІДСУТНІЙ
```

**Вплив**: NSE зустрічається в methods у 77% гідрологічних papers, results часто empty (48% papers). Результат: `metrics=[]` для більшості papers де NSE/RMSE є.

**Виправлення** (одна строчка):
```python
metric_text = " ".join([abstract, methods, results, conclusion])
```

---

### O4: Satellite acquisition verbs

**Поточний `_USAGE_POSITIVE`** не містить:
- `"obtained from"`, `"acquired from"`, `"downloaded from"`
- `"derived from"`, `"provided by"`, `"sourced from"`, `"collected from"`

**Результат**: papers де "SAR data was obtained from ESA" → satellite не виявляється (strict=True).

---

### O5: Disambiguation rules не завантажуються

`data/ontology_disambiguation_rules.json` (15 rules) існує на диску, але `load_knowledge_base()` його не читає. Ambiguous tokens: SCS, ANN, MLP, HEC-RAS, NSE, LISFLOOD, Prophet.

**Виправлення**: додати `"disambiguation_rules"` до `files` dict в `knowledge_loader.py`.

---

### O6: Від'ємні embedding scores

**Розподіл** (100-paper audit):
- < 0 (від'ємні): 64/463 = **13.8%**
- 0.0–0.1: 156/463 = 33.7%
- ≥ 0.3: 52/463 = 11.2%

**Наслідок**: `RF` accepted з embedding=-0.133 (Pattern+context=1.20); `Random-Forest` rejected з embedding=+0.148 (Pattern+context=0.88). Гірший варіант перемагає.

**Виправлення**: clamp `embedding_score()` → `max(0.0, score)`.

---

### O7: study_type не на top-level

`paper["study_type"]` = `None` у всіх JSON.  
`paper["entities"]["geo"]["study_type"]["label"]` = `"case_study"` (правильно, але тільки тут).

Neo4j builder, dashboard, API — всі читають `paper.get("study_type")` → отримують `None`.

**Виправлення** (після `run_entity_pipeline()`):
```python
paper["study_type"] = entities.get("geo", {}).get("study_type", {}).get("label")
paper["task"]       = entities.get("task", {}).get("label")
```

---

### O11: Дубльовані chunk IDs в ChromaDB (нова спостереження)

**Симптом** (з live pipeline log):
```
chunking/vectorstore error (non-fatal): Expected IDs to be unique, 
found 95 duplicated IDs: c8ae60a56b..., 5cab392f2b..., ...
```

**Частота**: виникає для ~30-40% papers. Non-fatal (ChromaDB логує, але продовжує).

**Можлива причина**: `make_hash()` генерує однакові IDs для chunks з однаковим текстом (boilerplate, заголовки, повторювані параграфи). Потрібне додавання позиційного salt до хешу.

---

## 4. Статистика якості (прогнозна для 3,640-paper run)

На основі v3 100-paper audit + v5 fixes:

| Метрика | v3 baseline | v5 очікувано | Тренд |
|---------|------------|--------------|-------|
| Papers processed/час | — | ~90 papers/год (при --workers 3) | — |
| datetime failures | ~15% | **0%** | ✅ Fixed |
| schema validation fails | ~5-10% entity items | **~0%** | ✅ Fixed |
| Methods section non-empty | 76% | 76% | → Unchanged |
| Metrics coverage | 45% | 45% | → Unchanged |
| Satellite detection | 37% | 37% | → Unchanged |
| Country detection | 100% | 100% | ✅ Stable |
| KB normalization coverage | 100% | 100% | ✅ Stable |
| Judge fault tolerance | Fixed | Fixed | ✅ Stable |

---

## 5. Архітектурна карта після рефакторингу

```
src/ingestion/
├── pipeline.py                ← 326 рядків (оркестратор)
├── stages/
│   ├── parse_stage.py         ← 213 рядків (TEI→секції)
│   ├── geo_stage.py           ← 437 рядків (гео-ентіті)
│   ├── judge_stage.py         ← 410 рядків (OllamaJudge)
│   ├── entity_stage.py        ← 1,681 рядків (KB, class, pipeline)
│   └── sdom_bridge.py         ← 109 рядків (lazy SDOM)
├── utils.py                   ← json_safe(datetime), ensure_dict
└── knowledge/
    ├── knowledge_loader.py    ← KB loading
    └── entity_extractor.py   ← regex extraction

src/normalization/
├── ontology_matcher.py        ← fix: disambiguation fallthrough
└── ...

src/schemas/
└── normalized_paper.py        ← fix: MatchType.disambiguation added

src/orchestration/
├── pipeline_runner.py         ← Ray distributed executor
└── process_paper.py           ← Ray task (ensure_dict import fixed)
```

**Dependency flow** (тільки вниз, без циклів):
```
utils ──────────────────────────────────────────► parse_stage
utils ──────────────► geo_stage ← parse_stage
utils ──────────────► judge_stage
utils ──────────────► entity_stage ← geo_stage ← judge_stage
pipeline.py ◄──────────────────── (всі stage-файли re-exported)
```

---

## 6. Priority fix list (наступний цикл)

| Priority | Fix | File | Expected gain | LOC |
|----------|-----|------|---------------|-----|
| **P0** | Перезапустити pipeline після datetime fix | — | Завершити 3,512 remaining papers | 0 |
| **P0** | Add `methods` to `metric_text` | `entity_stage.py:run_entity_pipeline` | NSE/RMSE для ~77% papers | 1 |
| **P0** | Clamp negative embeddings → 0.0 | `entity_stage.py:embedding_score` | Стабільний entity acceptance | 1 |
| **P0** | Promote study_type/task to top-level | `pipeline.py:build_paper_json` | Фікс для всіх downstream consumers | 5 |
| **P1** | Expand `_USAGE_POSITIVE` (acquisition verbs) | `entity_extractor.py` | Satellite +40-50% coverage | 8 |
| **P1** | Load `ontology_disambiguation_rules.json` | `knowledge_loader.py` | SCS/ANN/MLP disambiguation | 3 |
| **P1** | Fix chunk ID collisions (add position salt) | `sdom_bridge.py` / chunker | Eliminate upsert warnings | 5 |
| **P2** | Extend `section_tags()` keywords | `parse_stage.py` | Methods coverage 76% → ~85% | 10 |
| **P2** | `_reclassify_from_other` for study_area+results | `parse_stage.py` | Rescue misrouted content | 30 |
| **P2** | Split SENTINEL-1/2/3 in KB | `knowledge_loader.py` | SAR vs optical distinction | 15 |
| **P3** | CrossRef DOI fallback for references | `enrichment/` | DOI 41% → ~60% | 50 |
| **P3** | Remove judge prompt `paper_id: '12345'` example | `judge_stage.py` | Eliminate template leakage | 1 |

---

## 7. Зведена оцінка зрілості

```
┌──────────────────────────────────────────────────────────────────┐
│  MATURITY: 6.0 / 10  —  Research-Grade Alpha (Improved)          │
│                                                                   │
│  Порівняно з v4 (5.5/10):                                         │
│  + datetime serialization fixed (was: 15% failure rate)          │
│  + MatchType.disambiguation added (was: schema validation errors) │
│  + pipeline.py refactored (was: 2,960-line god object)            │
│  + transformers compatibility fixed (was: blocking import error)  │
│  + Ray multi-instance conflict fixed                              │
│                                                                   │
│  Найбільший залишковий блокер: section routing (24% papers)       │
│  Другий: metric_text без methods (NSE/RMSE ~0% coverage)          │
│  Третій: від'ємні embeddings (entity acceptance нестабільний)     │
│  Четвертий: pipeline не завершено (3,512 papers в черзі)          │
└──────────────────────────────────────────────────────────────────┘
```

### Scoreboard по компонентах

| Шар | v4 | v5 | Зміна |
|-----|----|----|-------|
| TEI parsing | 6/10 | 6/10 | → |
| Section extraction | 4/10 | 4/10 | → |
| Entity extraction | 6/10 | 6/10 | → |
| KB normalization | 9/10 | **9.5/10** | ↑ (disambiguation fix) |
| Disambiguation | 6/10 | **6.5/10** | ↑ (match_type schema) |
| Confidence propagation | 5/10 | 5/10 | → (neg embeddings open) |
| JSON serialization | 6/10 | **9/10** | ↑ (datetime fixed) |
| Schema validation | 6/10 | **9/10** | ↑ (MatchType.disambiguation) |
| Citation graph | 5/10 | 5/10 | → |
| Fault tolerance | 8/10 | 8/10 | → |
| Code maintainability | 3/10 | **8/10** | ↑↑ (pipeline.py → stages/) |

---

## 8. Команди для моніторингу і перезапуску

### Перезапуск після виправлень
```bash
# Переконатися що Ray зупинено
.venv/bin/ray stop --force

# Запустити з новим логом
TOKENIZERS_PARALLELISM=false RAYON_NUM_THREADS=1 \
  nohup .venv/bin/python -m src.orchestration.pipeline_runner --workers 3 \
  > /tmp/pipeline_run_$(date +%Y%m%d_%H%M).log 2>&1 &

echo "PID=$!"
```

### Моніторинг прогресу
```bash
# Кількість записаних papers
ls data/literature/paper_json/*.paper.json | wc -l

# Live tail
tail -f /tmp/pipeline_run_*.log | grep -E "written|FAILED|fail=|ok="

# Failure analysis
grep "FAILED" /tmp/pipeline_run_*.log | sed 's/.*FAILED: //' | cut -d' ' -f3 | sort | uniq -c | sort -rn
```

### Перевірка після завершення
```bash
# Papers written
ls data/literature/paper_json/*.paper.json | wc -l  # очікувано: ~3,640+52=3,692

# Failure rate
grep -c "FAILED" /tmp/pipeline_run_*.log
grep -c "written" /tmp/pipeline_run_*.log

# Schema validation failures
grep "\[schema\].*failed" /tmp/pipeline_run_*.log | wc -l
```

---

*Аудит відтворюваний: `random.Random(42).sample(sorted(os.listdir("data/literature/paper_json")), 100)`*
