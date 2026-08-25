# Research Workflow

**Версія**: 2.0 | **Дата**: 2026-06-09  
**Підстава**: paper_my/, src/dashboard_dash/pages/research.py, src/retrieval/

---

## 1. Типовий сценарій дослідника

Система GeoHydroAI підтримує написання систематичного огляду з ~3,875 статей по темі flood inundation mapping.

```
1. Запустити pipeline
       ↓
2. Перевірити якість extraction
       ↓
3. Побудувати граф
       ↓
4. Виконати аналітичні запити
       ↓
5. Отримати evidence reports
       ↓
6. Генерувати секції статті
       ↓
7. Перевірити та уточнити
```

---

## 2. Крок 1 — Завантаження та обробка corpus

```bash
# Переконатись що сервіси запущені
docker compose up -d && ollama serve &

# Запустити Legacy pipeline
TOKENIZERS_PARALLELISM=false RAYON_NUM_THREADS=1 OMP_NUM_THREADS=2 \
OLLAMA_MODEL=mistral-nemo:12b OLLAMA_URL=http://localhost:11434 \
.venv/bin/python -m src.orchestration.pipeline_runner --workers 3

# Перевірити прогрес
python -m src.registry.cli stats
ls data/literature/paper_json/*.paper.json | wc -l

# Post-processing
python -m src.orchestration.normalization_runner --input-dir data/literature/paper_json --output-dir data/normalized
python -m src.orchestration.enrichment_runner
python -m src.graph.build_graph --uri bolt://localhost:7687 --user neo4j --password python2024
python -m src.graph.table_kg_loader
```

---

## 3. Крок 2 — Перевірка якості extraction

```bash
# Аудит entities
python -m src.registry.cli failures -v

# Перевірка coverage (приблизно)
python -c "
import json, glob
papers = [json.load(open(f)) for f in glob.glob('data/literature/paper_json/*.json')[:100]]
has_methods = sum(1 for p in papers if p.get('sections', {}).get('methods', ''))
has_metrics = sum(1 for p in papers if p.get('entities', {}).get('metrics'))
print(f'Methods coverage: {has_methods}/100')
print(f'Metrics coverage: {has_metrics}/100')
"
```

---

## 4. Крок 3 — Аналітичний Dashboard

```bash
python -m src.dashboard_dash.app   # http://localhost:8050
```

### Сторінки Dashboard

| Сторінка | URL | Що показує |
|----------|-----|-----------|
| Overview | `/` | Загальна статистика corpus |
| Methods | `/methods` | Які методи використовуються, частота |
| Analytics | `/analytics` | Тренди, розподіли, co-occurrence |
| Citations | `/citations` | Citation network, impact |
| Geospatial | `/geospatial` | Географічний розподіл досліджень |
| Topics | `/topics` | Тематичний аналіз |
| Research | `/research` | RAG query → Gemini synthesis |
| Explorer | `/explorer` | Paper search і detail view |
| Scientometrics | `/scientometrics` | Бібліометричний аналіз |
| Quality | `/quality` | Pipeline якість |
| Notebooks | `/notebooks` | Jupyter notebook results |

---

## 5. Крок 4 — Research Query (RAG + Synthesis)

Через Dashboard → Research page або програмно:

```python
from src.dashboard_dash.research_query_service import ResearchQueryService

rqs = ResearchQueryService()

# Запит
filter_state = {
    "query": "Which methods achieve highest NSE for flood routing?",
    "method_filter": ["HEC-RAS", "SWAT"],
    "year_range": [2015, 2024],
    "min_cited_by": 5
}

# Отримати evidence pack (3 джерела: DuckDB + Neo4j + ChromaDB)
evidence = rqs.build_evidence_pack(filter_state)

# Структура evidence:
# evidence["papers"]           ← DuckDB parquet papers
# evidence["graph_evidence"]   ← Neo4j method/metric nodes
# evidence["semantic_hits"]    ← ChromaDB semantic chunks
# evidence["numeric_facts"]    ← NumericFact rows
# evidence["abstract_context"] ← 12 paper abstracts
# evidence["limitations"]      ← active limitation strings
```

---

## 6. Крок 5 — Evidence Report для статті

Система генерує evidence_report.md на основі запиту:

**Підтверджено**: `paper_my/evidence_report_v4.md`, `paper_my/graph_report_v4.md`

```
evidence_report містить:
├── Query context
├── Top papers (з DOI і роком)
├── NumericFact table (метрика / значення / стаття / confidence)
├── Methods comparison
├── Sensor usage statistics
└── Geographic coverage
```

---

## 7. Крок 6 — Генерація секцій статті

**Підтверджено**: `paper_my/sections_v4/` (27 секцій), `paper_my/generated_paper_v4_V2_final.md`

Секції генеруються на основі evidence pack через Gemini (ai_gateway.py).

Поточна структура секцій статті (v4):
```
sections_v4/
├── abstract.md
├── introduction.md
├── sar_flood.md          ← SAR-based flood mapping
├── optical_flood.md      ← Optical sensors flood mapping
├── ml_dl.md              ← ML/DL methods
├── dem_hydraulic.md      ← DEM + hydraulic models
├── multi_sensor.md       ← Multi-sensor fusion
├── accuracy_metrics.md   ← Validation metrics (NSE, OA, Kappa...)
├── timeliness.md         ← Temporal analysis
├── tradeoff.md           ← Method trade-offs
├── transferability.md    ← Geographic transferability
├── uncertainty.md        ← Model uncertainty
├── physics_ai.md         ← Physics-AI integration
├── physical_plausibility.md
├── validation.md
├── eastern_europe.md     ← Eastern Europe / Ukraine specific
├── methodology.md        ← Study methodology
├── rag_model.md          ← RAG model description
├── limitations.md
├── future_directions.md
└── conclusions.md
```

---

## 8. Як додати нову сутність

### Приклад: додати новий метод "FloodGAN"

**Крок 1**: Додати до KB JSON
```bash
# файл: src/ingestion/knowledge/methods_kb.json або відповідний
{
  "id": "floodgan",
  "name": "FloodGAN",
  "category": "deep_learning",
  "aliases": ["Flood GAN", "generative adversarial flood mapping"],
  "domain": "flood_mapping"
}
```

**Крок 2**: Додати до онтології v2
```bash
# src/ontology/ — відповідний JSON файл
```

**Крок 3**: Перевірити disambiguation (якщо потрібно)
```bash
# src/ingestion/data/ontology_disambiguation_rules.json
```

**Крок 4**: Запустити тести
```bash
python -m pytest tests/ -q -k "entity"
```

**Крок 5**: Перезапустити normalization для нових papers або всього corpus
```bash
python -m src.orchestration.normalization_runner \
    --input-dir data/literature/paper_json \
    --output-dir data/normalized
```

**Крок 6**: Rebuild graph
```bash
python -m src.graph.build_graph --uri bolt://localhost:7687 --user neo4j --password python2024
```

---

## 9. Як додати нову метрику

### Приклад: додати "SSIM" (Structural Similarity Index)

**Крок 1**: Додати MetricRecord до v2_metrics
```python
# src/ingestion/knowledge/knowledge_loader.py або відповідний KB файл
{
  "id": "SSIM",
  "full_name": "Structural Similarity Index",
  "optimal": "max",
  "applicable_to": ["image_comparison", "flood_mapping"],
  "range": [0.0, 1.0]
}
```

**Крок 2**: Додати regex pattern
```python
# src/ingestion/knowledge/entity_extractor.py — _METRIC_PATTERNS
r"\bSSIM\b": "SSIM"
```

**Крок 3**: Додати canonical_id до схеми
```python
# src/schemas/normalized_paper.py (якщо потрібно розширити MatchType або schema)
```

**Крок 4**: Перевірити що `extract_metrics()` і `resolve_metric()` підхоплюють нову метрику

**Крок 5**: Запустити тести і перевірити coverage на sample:
```bash
python -m pytest tests/ -q -k "metric"
```

---

## 10. Як перевіряти якість extraction

### Manual validation (рекомендовано)

```bash
# Вибрати random sample
python -c "
import random, glob, json
files = glob.glob('data/literature/paper_json/*.json')
sample = random.Random(42).sample(files, 20)
for f in sample[:5]:
    p = json.load(open(f))
    print(f'Paper: {p[\"title\"][:60]}')
    print(f'  Methods: {[e[\"name\"] for e in p[\"entities\"][\"methods\"][:3]]}')
    print(f'  Metrics: {[m[\"name\"] for m in p[\"entities\"][\"metrics\"][:3]]}')
    print(f'  Countries: {[c[\"name\"] for c in p[\"entities\"].get(\"geo\",{}).get(\"countries\",[])[:3]]}')
    print()
"
```

### Precision/Recall для конкретного entity типу

```bash
# Перевірити методи для відомих papers
python -c "
import json
p = json.load(open('data/literature/paper_json/YOUR_PAPER_ID.paper.json'))
methods = p['entities']['methods']
print('Extracted methods:')
for m in methods:
    if m.get('accepted'):
        print(f'  {m[\"name\"]} → {m[\"canonical_id\"]} (conf={m[\"confidence\"]:.2f})')
"
```

### Важливі false positive кейси (документовані)

| False Positive | Причина | Виправлення |
|----------------|---------|-------------|
| "HAND" → method | "on the other hand" trigger | Додати phrase context exclusion |
| "Bulletin" → country | SpaCy NER noise | Підняти geo NER threshold до 0.65 |
| "LP3" → country | SpaCy NER noise | Blocklist для 2-5 символьних uppercase tokens |
| Percent → не recognized | canonical_id=None | PATCH 3 частково виправив |

---

## 11. Як розширити Dashboard новою сторінкою

```python
# 1. Створити новий файл
# src/dashboard_dash/pages/new_page.py

from dash import html
def layout():
    return html.Div("New page content")

# 2. Зареєструвати в app.py
from src.dashboard_dash.pages import new_page

# 3. Додати до navigation в layout.py

# 4. Додати callbacks якщо потрібно interactive behavior
# src/dashboard_dash/callbacks.py
```

---

## 12. Відомі обмеження системи для дослідника

| Обмеження | Вплив | Workaround |
|-----------|-------|------------|
| NSE/RMSE extraction низька (methods не в metric_text) | Числові результати пропущені для ~73% papers | Планується виправлення (P0) |
| 24% papers без methods секції | Entity extraction для цих papers неповна | Manual verification для критичних papers |
| Citation graph: 41% DOI resolved | Citation analysis неповна | Використовувати cited_by_count з OpenAlex |
| Nougat: 180/3,875 papers | Формули та таблиці без visual parsing | Запустити nougat_region_pipeline поступово |
| LLM judge недетермінований | Різні результати при re-run | Зберігати seed, логувати judge_used |
| study_type/task завжди null | Невірна класифікація в dashboard | Перевіряти llm_judge.task.corrected_value напряму |
