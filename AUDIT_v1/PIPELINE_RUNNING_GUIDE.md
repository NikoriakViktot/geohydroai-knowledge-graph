# GeoHydroAI — Повний гайд з запуску пайплайну

> Дата аналізу: 2026-05-19 | Pipeline version: 2.0.0 | Papers in corpus: 3 875

---

## Зміст

1. [Архітектура та стан даних](#1-архітектура-та-стан-даних)
2. [Передумови та сервіси](#2-передумови-та-сервіси)
3. [Структура директорій](#3-структура-директорій)
4. [Легасі-пайплайн (операційний)](#4-легасі-пайплайн-операційний)
5. [NougatRegionPipeline — Stage 1.5](#5-nougatregionpipeline--stage-15)
6. [Новий пайплайн Stages 0–2.5](#6-новий-пайплайн-stages-025)
7. [Збагачення та аналітика (Stages 3–5)](#7-збагачення-та-аналітика-stages-35)
8. [Реєстр та інспекція стану](#8-реєстр-та-інспекція-стану)
9. [Дашборд](#9-дашборд)
10. [Змінні середовища](#10-змінні-середовища)
11. [Поточний стан корпусу](#11-поточний-стан-корпусу)
12. [Рекомендований порядок повного перезапуску](#12-рекомендований-порядок-повного-перезапуску)

---

## 1. Архітектура та стан даних

```
PDF-файли (3 875)
│
├── [A] GROBID ingestion ──────────────────────────────────────────────────────
│   grobid_ingest.py
│   └─→ data/literature/grobid_xml/  (6 014 XML)
│
├── [B] NougatRegionPipeline ──────────────────────────────────────────────────
│   nougat_region_pipeline.py   ← PDF + TEI → семантичні регіони → Nougat inference
│   ├─→ data/sodb/{paper_id}/regions.parquet   (180 паперів оброблено)
│   └─→ data/nougat_regions/{paper_id}/        (2.1 GB crop-зображень)
│
├── [C] Легасі Ray-пайплайн ───────────────────────────────────────────────────
│   pipeline_runner → normalization_runner → enrichment_runner
│   └─→ paper_json / normalized / enriched / analytics / Neo4j
│       (145 / 132 / 0 / 14 parquet / 3692 registry SUCCESS)
│
└── [D] Новий чистий пайплайн (Stages 0–2.5) ─────────────────────────────────
    Stage0Ingestor → Stage1Parser → Stage2Engineer → SemanticValidatorStage
    data/raw/ → data/parsed/ → data/sodb/ → semantic_*.parquet
    (1 / не існує / 179 sodb dirs)
```

### Що де живе

| Директорія | Хто пише | Поточний стан |
|---|---|---|
| `data/literature/pdf/` | Вручну | 3 875 PDF |
| `data/literature/grobid_xml/` | `grobid_ingest.py` | 6 014 XML |
| `data/literature/paper_json/` | `pipeline_runner.py` | 145 JSON |
| `data/nougat_regions/` | `nougat_region_pipeline.py` | 180 паперів, 2.1 GB |
| `data/sodb/` | `nougat_region_pipeline.py` + `Stage2Engineer` | 179–180 директорій |
| `data/normalized/` | `normalization_runner.py` | 132 JSON |
| `data/enriched/` | `enrichment_runner.py` | **0 — не запускався** |
| `data/cache/` | `build_author/reference_universe.py` | SQLite cache |
| `data/reference_enriched/` | `reference_enrichment.py` | 0 |
| `data/analytics/` | `build_parquet_layer.py` | 14 parquet-файлів |
| `data/registry/` | `grobid_ingest.py` + runners | DuckDB: 3 692 SUCCESS |
| `data/raw/` | `Stage0Ingestor` | 1 (тест) |
| `data/parsed/` | `Stage1Parser` | **не існує — не запускався** |

---

## 2. Передумови та сервіси

### 2.1 Docker-сервіси

```bash
# Запустити GROBID + Neo4j
docker compose up -d

# Перевірити що запустились
docker compose ps
curl http://localhost:8070/api/isalive   # GROBID
curl http://localhost:7474               # Neo4j Browser
```

| Сервіс | Порт | Призначення |
|---|---|---|
| GROBID | 8070 | PDF → TEI XML |
| Neo4j | 7687 (Bolt) / 7474 (Browser) | Граф знань |

### 2.2 Ollama (локальна LLM)

```bash
# Перевірити що Ollama запущена
curl http://localhost:11434/api/tags

# Якщо не запущена:
ollama serve &
ollama pull mistral-nemo:12b
```

### 2.3 Python-оточення

```bash
cd /home/niko/projects/knoweledg_graf
source .venv/bin/activate
python -c "import ray, pyarrow, duckdb; print('OK')"
```

---

## 3. Структура директорій

```
knoweledg_graf/
├── data/
│   ├── literature/
│   │   ├── pdf/              ← вхідні PDF (3 875 файлів)
│   │   ├── grobid_xml/       ← GROBID TEI XML (6 014 файлів)
│   │   └── paper_json/       ← виходи Ray-пайплайну (145 файлів)
│   ├── nougat_regions/       ← crop-зображення + regions.json (2.1 GB)
│   ├── raw/                  ← Stage 0: незмінні PDF-копії + checksum
│   ├── parsed/               ← Stage 1: структурні parquet-артефакти
│   ├── sodb/                 ← SODB: regions / formulas / tables / semantic
│   ├── normalized/           ← нормалізовані JSON (132 файли)
│   ├── enriched/             ← збагачені JSON + OpenAlex (0 файлів)
│   ├── cache/                ← SQLite кеш DOI/авторів
│   ├── reference_enriched/   ← збагачені посилання
│   ├── analytics/            ← parquet-таблиці для аналітики (14 файлів)
│   ├── parquet/              ← Stage 5 parquet-шар (papers/authors/refs)
│   └── registry/             ← DuckDB реєстр стану пайплайну
├── src/
│   ├── ingestion/
│   │   ├── stage0/               ← Stage0Ingestor, RawStore
│   │   ├── stage1/               ← Stage1Parser, ParsedStore
│   │   ├── stage2/               ← Stage2Engineer
│   │   ├── stages/               ← parse_stage, geo_stage, entity_stage...
│   │   ├── grobid_ingest.py      ← [A] PDF → XML
│   │   ├── nougat_region_pipeline.py  ← [B] семантичні регіони + Nougat
│   │   └── pipeline.py
│   ├── orchestration/
│   │   ├── pipeline_runner.py      ← [C] Ray (XML → paper.json)
│   │   ├── enrichment_runner.py    ← OpenAlex збагачення
│   │   └── normalization_runner.py ← онтологічна нормалізація
│   ├── semantic_objects/     ← Stage 2.5: семантична валідація
│   ├── enrichment/           ← build_parquet_layer, author/ref universe
│   ├── graph/                ← build_graph, table_kg_loader
│   ├── registry/             ← PipelineRegistry, CLI
│   └── dashboard_dash/       ← Dash-дашборд
└── docker-compose.yml
```

---

## 4. Легасі-пайплайн (операційний)

### 4.1 Крок A: PDF → GROBID XML

```bash
source .venv/bin/activate

TOKENIZERS_PARALLELISM=false \
python -m src.ingestion.grobid_ingest \
    --pdf-dir data/literature/pdf \
    --xml-dir data/literature/grobid_xml \
    --workers 1
```

- Читає PDF, надсилає в GROBID HTTP (порт 8070)
- Записує `*.tei.xml` у `data/literature/grobid_xml/`
- Реєстрація стану в DuckDB (`data/registry/pipeline_registry.duckdb`)
- GROBID повинен бути запущений через Docker

> **Поточний стан:** 6 014 XML вже є. Запускати лише для нових PDF.

---

### 4.2 Крок B: XML → paper.json (Ray + SpaCy + Ollama)

```bash
TOKENIZERS_PARALLELISM=false \
RAYON_NUM_THREADS=1 \
OMP_NUM_THREADS=2 \
OLLAMA_MODEL=mistral-nemo:12b \
OLLAMA_URL=http://localhost:11434 \
SPACY_MODEL=en_core_web_sm \
.venv/bin/python3 -m src.orchestration.pipeline_runner --workers 3
```

**Опції:**

| Прапор | Дія |
|---|---|
| `--workers 3` | Максимум 3 задачі паралельно (~3 GB RAM) |
| `--overwrite` | Перепроцесувати вже оброблені |
| `--xml-dir PATH` | Альтернативна папка з XML |
| `--out-dir PATH` | Альтернативна папка для JSON |
| `--ray-address` | Адреса Ray-кластера (без = локально) |

**Архітектура:**
```
Ray Local Runtime
├── SpacyActor      (~300 MB, en_core_web_sm)
├── EmbeddingActor  (~600 MB)
├── OllamaActor     (max_concurrency=2)
└── process_paper × workers   (~500 MB кожна)
```

> **Поточний стан:** 145/6 014 оброблено. Продовжить з точки зупинки.

---

### 4.3 Крок C: Нормалізація онтології

```bash
python -m src.orchestration.normalization_runner \
    --input-dir  data/literature/paper_json \
    --output-dir data/normalized \
    --log-level  INFO
```

| Прапор | Дія |
|---|---|
| `--overwrite` | Перезаписати вже нормалізовані |
| `--limit N` | Обробити лише перші N файлів |
| `--fail-fast` | Зупинитись на першій помилці |

> **Поточний стан:** 132/145 нормалізовано. Не потребує Ray або GROBID.

---

## 5. NougatRegionPipeline — Stage 1.5

**Найважливіший модуль для якості даних.** Знаходиться між GROBID і новим пайплайном. Це єдиний компонент, який **перетворює PDF у зрозумілий машині візуальний зміст** рисунків, таблиць і формул.

### Що він робить

GROBID дає координати як семантичні *якорі* — сирі фрагменти bbox. Цей модуль:

1. **Парсує TEI XML** → витягує `SemanticBlock` (FIGURE_BODY, GRAPHIC, CAPTION, LABEL, TABLE_BODY, TABLE_HEAD, FORMULA, CONTEXT)
2. **Зливає блоки** в єдині `ScientificRegion` за трьома незалежними сімействами (figure / table / formula — без перехресного злиття)
3. **Прикріплює підписи** до найближчої фігури або таблиці
4. **Розширює bbox** з padding залежно від типу (figure +15/25pt, formula +35/35pt)
5. **Рендерить crop** з PDF через PyMuPDF при 300 DPI
6. **Відправляє зображення в NougatActor** (Ray) для мультимодального inference
7. **Записує результати** в `data/sodb/{paper_id}/regions.parquet` і `data/nougat_regions/{paper_id}/regions.json`

### Типи регіонів

| Тип | Умова |
|---|---|
| `FIGURE_REGION` | Фігура без особливих ознак |
| `SCIENTIFIC_DIAGRAM` | Фігура > 300×200pt |
| `CHART_REGION` | Підпис містить: hydrograph, map, watershed, satellite, raster... |
| `MULTI_PANEL_FIGURE` | ≥2 `<graphic>` блоків злито разом |
| `TABLE_REGION` | Містить `<table>` блоки |
| `FORMULA_REGION` | Містить `<formula>` блоки |

### Параметри злиття

```
MERGE_DISTANCE_Y = 120 pt  (~1.7 cm)  — вертикальний зазор між блоками
MERGE_DISTANCE_X =  80 pt  (~1.1 cm)  — горизонтальний зазор
PAGE_FULL_THRESHOLD = 0.55 — якщо регіон займає >55% сторінки → full-page render
```

### Що записується в SODB

`data/sodb/{paper_id}/regions.parquet` — схема:

| Поле | Тип | Зміст |
|---|---|---|
| `region_id` | string | `{paper_id}_p{page}_{type}_{n}` |
| `region_type` | string | FIGURE_REGION / TABLE_REGION / ... |
| `bbox_x0/y0/x1/y1` | float | Розширений bbox в PDF-points |
| `nougat_text` | string | Візуальний текст з Nougat (підписи, опис) |
| `nougat_latex` | string | LaTeX для FORMULA_REGION |
| `crop_path` | string | Відносний шлях до PNG crop |
| `source_parser` | string | `"HYBRID"` (GROBID coords + Nougat content) |

### Запуск

```bash
source .venv/bin/activate

# Базовий (sequential, GPU ~3-5%)
TOKENIZERS_PARALLELISM=false \
python -m src.ingestion.nougat_region_pipeline

# Паралельний — 3 PDF одночасно (GPU ~15-25%, рекомендований)
TOKENIZERS_PARALLELISM=false NOUGAT_GPU_FRACTION=1.0 \
python -m src.ingestion.nougat_region_pipeline --workers 3

# Агресивний — 5 PDF одночасно (GPU ~30-45%, потрібно ~10 GB RAM)
TOKENIZERS_PARALLELISM=false NOUGAT_GPU_FRACTION=1.0 OMP_NUM_THREADS=1 \
python -m src.ingestion.nougat_region_pipeline --workers 5

# CPU-only (WSL2 без CUDA)
TOKENIZERS_PARALLELISM=false NOUGAT_GPU_FRACTION=0 \
python -m src.ingestion.nougat_region_pipeline --workers 2

# Тест на 10 паперах
python -m src.ingestion.nougat_region_pipeline --limit 10 --workers 3

# Перепроцесувати все
python -m src.ingestion.nougat_region_pipeline --overwrite --workers 3
```

> Детальний опис всіх варіантів: [NOUGAT_REGION_PIPELINE.md](NOUGAT_REGION_PIPELINE.md)

**Вимоги:**
- Nougat модель завантажується в NougatActor при старті (~2.4 GB VRAM, ~4 GB RAM для CPU)
- Ray ініціалізується автоматично (локально)
- Потрібні: `data/literature/pdf/` + відповідні `data/literature/grobid_xml/*.tei.xml`

**Ідемпотентність:** Пропускає папір якщо `data/sodb/{paper_id}/regions.parquet` вже існує І в SODBManifest є мітка `region_extract=done` для поточного `pipeline_hash`.

> **Поточний стан:** 180/3 875 паперів оброблено. 2.1 GB crop-зображень у `data/nougat_regions/`. Вихід зберігається в `data/sodb/{paper_id}/regions.parquet`.

---

## 6. Новий пайплайн Stages 0–2.5

Чиста архітектура. Кожна стадія атомарна, ідемпотентна, ніколи не мутує попередні артефакти. Stage 1 читає `regions.parquet` з SODB, якщо `nougat_region_pipeline` вже відпрацював для цього паперу.

```
[nougat_region_pipeline] ──────────────────────────────┐
                                                        ↓
PDF → Stage 0 → Stage 1 → Stage 2 → Stage 2.5   sodb/regions.parquet
      data/raw  data/parsed  data/sodb  semantic_*.parquet
```

> **Важливо:** CLI-оркестратора для цього пайплайну ще немає. Нижче — програматичний запуск.

### 6.1 Stage 0: Raw Ingestion

```python
from pathlib import Path
from src.ingestion.stage0.ingestor import Stage0Ingestor

result = Stage0Ingestor().run(
    pdf_path=Path("data/literature/pdf/0030.pdf"),
    metadata={"doi": "10.1016/...", "title": "..."},
)
print(result.paper_id, result.status)  # SHA-256, "ok"
```

**Вихід (`data/raw/{paper_id}/`):**
```
paper.pdf          ← незмінна копія PDF
checksum.sha256    ← SHA-256 хеш
metadata.json      ← бібліографічні метадані
source.json        ← провенанс
ingestion_log.json ← лог подій
```

**`paper_id` = SHA-256 від байтів PDF** — контентний, незмінний ідентифікатор.

---

### 6.2 Stage 1: Structural Parsing

```python
from src.ingestion.stage1.parser_runner import Stage1Parser

stage1 = Stage1Parser().run(stage0)
print(stage1.section_count, stage1.figure_count, stage1.table_count)
```

Якщо `data/sodb/{paper_id}/regions.parquet` вже є — `Stage1Parser` використовує `nougat_text` звідти для збагачення підписів рисунків.

**Стратегія `PARSER_STRATEGY`** (за замовчуванням `grobid_with_nougat_fallback`):
- GROBID: секції, таблиці, рівняння, посилання, підписи
- Nougat: рисунки, координати bbox, формули LaTeX (fallback)

**Вихід (`data/parsed/{paper_id}/`):**
```
sections.parquet    ← текстові секції з заголовками
figures.parquet     ← рисунки з bbox і підписами
tables.parquet      ← таблиці (header_row, has_numeric_data)
equations.parquet   ← формули (LaTeX-текст)
references.parquet  ← список літератури
captions.parquet    ← уніфікований індекс підписів
parsing_manifest.json
```

**Зміна стратегії парсера:**
```bash
PARSER_STRATEGY=grobid_only     # тільки GROBID
PARSER_STRATEGY=nougat_only     # тільки Nougat (потрібен GPU)
PARSER_STRATEGY=hybrid          # обидва, злиття результатів
PARSER_STRATEGY=auto            # автовибір за якістю
```

---

### 6.3 Stage 2: Scientific Object Engineering

```python
from src.ingestion.stage2.engineer import Stage2Engineer

stage2 = Stage2Engineer().run(stage1)
print(stage2.object_count, stage2.edge_count)
```

- Класифікує кожен елемент за типом (`metrics_table`, `hydrograph`, `scatter_plot`, ...)
- Будує граф об'єктів (ребра `FOLLOWS`, `IN_SECTION`)
- Без NLP, без ембедингів — тільки евристичні правила

**Вихід (`data/sodb/{paper_id}/`):**
```
scientific_objects.parquet  ← типізовані об'єкти з classifier_score
object_graph.parquet        ← направлені ребра між об'єктами
object_manifest.json        ← лог стадії 2
```

**Типи об'єктів:**

| Категорія | Типи |
|---|---|
| Таблиці | `metrics_table`, `comparison_table`, `parameter_table`, `station_table`, `data_table` |
| Рисунки | `hydrograph`, `flood_extent_map`, `scatter_plot`, `satellite_image`, `watershed_map`, `flowchart` |
| Рівняння | `objective_function`, `physical_equation`, `regression_formula` |
| Секції | `methods_section`, `results_section`, `data_section`, `conclusion_section` |

---

### 6.4 Stage 2.5: Semantic Validation

```python
from src.semantic_objects.semantic_validator import SemanticValidatorStage

result = SemanticValidatorStage().run(paper_id)
print(result.annotation_count, result.edge_count, result.status)
```

- 15 декларативних IF-THEN правил (ObjectReasoner R01–R15)
- Семантичні ролі: `model_output`, `model_validation`, `evaluation_visualization`, ...
- Накопичення confidence через EvidenceTrace
- Семантичні ребра: `EVALUATES`, `VISUALIZES`, `VALIDATES`

**Вихід (`data/sodb/{paper_id}/`):**
```
semantic_annotations.parquet  ← по одному рядку на об'єкт
semantic_edges.parquet        ← типізовані семантичні ребра
semantic_manifest.json        ← {"stage": "2.5", ...}
```

```python
# Примусовий перезапуск
SemanticValidatorStage(force=True).run(paper_id)
```

---

### 6.5 Повний новий пайплайн на одному PDF

```python
from pathlib import Path
from src.ingestion.stage0.ingestor import Stage0Ingestor
from src.ingestion.stage1.parser_runner import Stage1Parser
from src.ingestion.stage2.engineer import Stage2Engineer
from src.semantic_objects.semantic_validator import SemanticValidatorStage

pdf = Path("data/literature/pdf/0030.pdf")

s0 = Stage0Ingestor().run(pdf)
assert s0.should_continue, f"Stage 0: {s0.status}"

s1 = Stage1Parser().run(s0)
assert s1.should_continue, f"Stage 1: {s1.status}"

s2 = Stage2Engineer().run(s1)
assert s2.should_continue, f"Stage 2: {s2.status}"

s25 = SemanticValidatorStage().run(s0.paper_id)
print(f"Done: {s25.annotation_count} annotations, {s25.edge_count} edges")
```

Якщо `nougat_region_pipeline` вже запускався для `0030.pdf` — Stage 1 підхопить `regions.parquet` автоматично.

---

## 7. Збагачення та аналітика (Stages 3–5)

Ці кроки не вимагають Ray або GROBID.

### 7.1 OpenAlex Enrichment

```bash
python -m src.orchestration.enrichment_runner
```

- Читає `data/normalized/*.json`
- Шукає DOI в OpenAlex API
- Кешує відповіді в SQLite (`data/cache/openalex_doi.db`)
- Пише `data/enriched/{paper_id}.json`

> **Поточний стан:** 0/132 збагачено. **Треба запустити першим.**

---

### 7.2 Author Universe

```bash
python -m src.enrichment.build_author_universe
```

- Читає `data/enriched/*.json`, збирає унікальні OpenAlex author ID
- Пише `data/cache/author_universe.parquet`

---

### 7.3 Reference Universe

```bash
python -m src.enrichment.build_reference_universe
```

- Читає `data/enriched/*.json`, збирає DOI/OpenAlex ID всіх посилань
- **Нуль API-запитів** — тільки агрегація
- Пише `data/cache/reference_doi_universe.parquet`

---

### 7.4 Reference Enrichment

```bash
python -m src.enrichment.reference_enrichment
```

- Запитує OpenAlex для кожного унікального DOI посилання
- Пише `data/reference_enriched/*.json`

---

### 7.5 Parquet Analytics Layer

```bash
python -m src.enrichment.build_parquet_layer
```

**Вихідні таблиці в `data/analytics/`:**

| Таблиця | Опис |
|---|---|
| `papers.parquet` | Ядро метаданих паперів |
| `authors.parquet` | Наукометрика авторів |
| `paper_author_edges.parquet` | Зв'язки paper ↔ author |
| `references.parquet` | Направлені ребра цитування |
| `topics.parquet` | Оцінки тем paper ↔ topic |

> **Поточний стан:** 14 parquet-файлів вже існують. Застарілі — оновити після enrichment.

---

### 7.6 Neo4j Knowledge Graph

```bash
# Перший запуск (з очищенням)
python -m src.graph.build_graph \
    --uri      bolt://localhost:7687 \
    --user     neo4j \
    --password python2024 \
    --wipe

# Оновлення без очищення
python -m src.graph.build_graph \
    --uri bolt://localhost:7687 --user neo4j --password python2024
```

---

### 7.7 NumericFact Loader (таблиці → Neo4j)

```bash
python -m src.graph.table_kg_loader              # всі папери
python -m src.graph.table_kg_loader 0030         # один папір
python -m src.graph.table_kg_loader --dry-run    # без запису
python -m src.graph.table_kg_loader --stats      # статистика
```

---

## 8. Реєстр та інспекція стану

```bash
python -m src.registry.cli stats                # загальна статистика
python -m src.registry.cli pending --limit 20   # черга
python -m src.registry.cli failures -v          # помилки
python -m src.registry.cli runtime              # час обробки
python -m src.registry.cli reset-stale          # скинути завислі задачі
python -m src.registry.cli query "SELECT paper_id, status FROM pipeline_registry LIMIT 10"
```

**Поточний стан:**
```
SUCCESS  : 3 692
SKIPPED  :    32
TOTAL    : 3 724
```

---

## 9. Дашборд

```bash
source .venv/bin/activate
python -m src.dashboard_dash.app
# http://localhost:8050
```

Читає з: `data/analytics/*.parquet` (DuckDB) + `data/normalized/*.json` + Neo4j.

---

## 10. Змінні середовища

`.env` (вже налаштований):
```bash
HF_TOKEN=hf_...
OLLAMA_MODEL=mistral-nemo:12b
OLLAMA_URL=http://localhost:11434
OPEN_ALEX_API=...
OPEN_ALEX_EMAIL=viktornikoriak@uhmi.org.ua
```

Опціональні override:
```bash
RAW_DIR=/custom/raw
PARSED_DIR=/custom/parsed
SODB_DIR=/custom/sodb
PIPELINE_VERSION=2.1.0
PARSER_STRATEGY=grobid_only   # grobid_only | nougat_only | hybrid | auto | grobid_with_nougat_fallback
NOUGAT_ENABLED=false
RAY_MAX_CONCURRENT=4
```

Обов'язкові для Ray-кроків:
```bash
TOKENIZERS_PARALLELISM=false   # запобігає deadlock у tokenizers
RAYON_NUM_THREADS=1            # стабільність rust/arrow
OMP_NUM_THREADS=2              # обмеження OpenMP
```

---

## 11. Поточний стан корпусу

```
PDF-файли                         3 875
GROBID XML                        6 014   (більше, бо деякі PDF давали кілька XML)
Nougat regions (SODB)               180   (2.1 GB crops у data/nougat_regions/)
Ray paper.json                      145   ← продовжувати pipeline_runner
Normalized JSON                     132
Enriched JSON (OpenAlex)              0   ← треба запустити enrichment_runner
Reference enriched                    0   ← після enrichment
Analytics parquet                14 файлів (застарілі — оновити після enrichment)
Registry DuckDB            3 692 SUCCESS / 32 SKIPPED
Stage 0 raw/                          1   (тест)
Stage 1 parsed/               не існує   ← не запускався
Stage 2/2.5 sodb/                   179   (regions + formulas + tables)
```

---

## 12. Рекомендований порядок повного перезапуску

```bash
cd /home/niko/projects/knoweledg_graf
source .venv/bin/activate

# ── 0. Сервіси ────────────────────────────────────────────────────────────────
docker compose up -d
curl http://localhost:8070/api/isalive   # дочекатись GROBID

# ── 1. GROBID: PDF → XML (тільки нові PDF) ───────────────────────────────────
TOKENIZERS_PARALLELISM=false \
python -m src.ingestion.grobid_ingest \
    --pdf-dir data/literature/pdf \
    --xml-dir data/literature/grobid_xml

# ── 2. Nougat: PDF + XML → регіони + візуальний контент ──────────────────────
#    Потрібен GPU або велика RAM (~4 GB для моделі Nougat)
#    Ідемпотентно — пропускає вже оброблені папери
TOKENIZERS_PARALLELISM=false \
python -m src.ingestion.nougat_region_pipeline

# ── 3. Ray: XML → paper.json (NLP + embeddings + LLM) ───────────────────────
TOKENIZERS_PARALLELISM=false RAYON_NUM_THREADS=1 OMP_NUM_THREADS=2 \
OLLAMA_MODEL=mistral-nemo:12b OLLAMA_URL=http://localhost:11434 \
SPACY_MODEL=en_core_web_sm \
python -m src.orchestration.pipeline_runner --workers 3

# ── 4. Нормалізація онтології ─────────────────────────────────────────────────
python -m src.orchestration.normalization_runner

# ── 5. OpenAlex збагачення ────────────────────────────────────────────────────
python -m src.orchestration.enrichment_runner

# ── 6. Author + Reference universes ──────────────────────────────────────────
python -m src.enrichment.build_author_universe
python -m src.enrichment.build_reference_universe
python -m src.enrichment.reference_enrichment

# ── 7. Parquet analytics layer ────────────────────────────────────────────────
python -m src.enrichment.build_parquet_layer

# ── 8. Neo4j graph ────────────────────────────────────────────────────────────
python -m src.graph.build_graph \
    --uri bolt://localhost:7687 --user neo4j --password python2024
python -m src.graph.table_kg_loader

# ── 9. Дашборд ───────────────────────────────────────────────────────────────
python -m src.dashboard_dash.app
# http://localhost:8050
```

### Пріоритетні незавершені кроки

| # | Крок | Стан | Команда |
|---|---|---|---|
| 1 | Ray pipeline_runner | 145/6 014 (2.4%) | `pipeline_runner --workers 3` |
| 2 | nougat_region_pipeline | 180/3 875 (4.6%) | `nougat_region_pipeline` |
| 3 | enrichment_runner | **0/132** | `enrichment_runner` |
| 4 | build_parquet_layer | застарілий | після enrichment |
| 5 | build_graph | треба оновити | після parquet |
| 6 | Stage 0→1→2→2.5 оркестратор | немає CLI | написати runner |

---

## Тести

```bash
source .venv/bin/activate

python -m pytest tests/ -q                         # всі тести (606/606)
python -m pytest tests/test_11_stage0.py  -v       # Stage 0   (48 тестів)
python -m pytest tests/test_12_stage1.py  -v       # Stage 1   (51 тест)
python -m pytest tests/test_13_stage2.py  -v       # Stage 2   (51 тест)
python -m pytest tests/test_14_stage25.py -v       # Stage 2.5 (84 тести)
```
