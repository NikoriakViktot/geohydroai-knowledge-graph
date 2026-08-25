# NougatRegionPipeline — Семантичний конструктор регіонів

> `src/ingestion/nougat_region_pipeline.py`
> Мова: Python 3.11+ | Ray | PyMuPDF | lxml | PyArrow

---

## Зміст

1. [Що робить цей модуль](#1-що-робить-цей-модуль)
2. [Місце в архітектурі](#2-місце-в-архітектурі)
3. [Використання GPU](#3-використання-gpu)
4. [Передумови](#4-передумови)
5. [Варіанти запуску](#5-варіанти-запуску)
6. [Всі CLI-прапори та змінні середовища](#6-всі-cli-прапори-та-змінні-середовища)
7. [Вихідні дані](#7-вихідні-дані)
8. [Ідемпотентність та пропуск](#8-ідемпотентність-та-пропуск)
9. [Алгоритм зсередини](#9-алгоритм-зсередини)
10. [Типи регіонів](#10-типи-регіонів)
11. [Усунення проблем](#11-усунення-проблем)

---

## 1. Що робить цей модуль

GROBID дає координати структурних елементів PDF як сирі **фрагменти bbox** — вони є семантичними *якорями*, а не готовими регіонами. Цей модуль:

1. **Парсує TEI XML** (GROBID) → витягує `SemanticBlock` з координатами
2. **Зливає фрагменти** в цілісні `ScientificRegion` (рисунок + підпис + легенда = один регіон)
3. **Розширює bbox** з padding залежно від типу
4. **Рендерить crop** з PDF через PyMuPDF @ 300 DPI
5. **Надсилає зображення в NougatActor** (Ray remote) для мультимодального inference
6. **Зберігає результати** у parquet (SODB) та JSON

```
data/literature/grobid_xml/{id}.tei.xml  ──┐
data/literature/pdf/{id}.pdf             ──┤
                                           ↓
                              [ TEI parse → RegionBuilder ]
                                           ↓
                              [ PyMuPDF crop → NougatActor ]
                                           ↓
                 data/sodb/{paper_id}/regions.parquet
                 data/nougat_regions/{paper_id}/regions.json
                 data/nougat_regions/{paper_id}/crops/*.png
```

---

## 2. Місце в архітектурі

Модуль є **Stage 1.5** — між GROBID-інгестією та новим пайплайном:

```
[Stage A] GROBID: PDF → TEI XML
              ↓
[Stage 1.5] nougat_region_pipeline   ← ЦЕЙ МОДУЛЬ
              ↓  data/sodb/{id}/regions.parquet
[Stage 1]  Stage1Parser              ← читає regions.parquet якщо є
              ↓  data/parsed/{id}/*.parquet
[Stage 2]  Stage2Engineer            ← класифікація об'єктів
              ↓  data/sodb/{id}/scientific_objects.parquet
[Stage 2.5] SemanticValidatorStage   ← семантичні ребра
              ↓  data/sodb/{id}/semantic_annotations.parquet
[Stage 3A] parquet_builder           ← аналітичний шар
[Stage 4]  Neo4j graph               ← тільки граф-запити
```

**Архітектурні правила, які дотримуються:**

| Правило | Як виконується |
|---------|----------------|
| Не мішати XML + OpenAlex + Neo4j в одній стадії | Тільки TEI → regions → parquet |
| Ray + actor (без глобального стану) | `NougatActor.remote()`, один екземпляр |
| lxml тільки в парсингу | Тільки в `extract_semantic_blocks()` |
| Ідемпотентність | SODBManifest `region_extract=done` |
| Parquet як canonical шар даних | `data/sodb/{id}/regions.parquet` |

---

## 3. Використання GPU

```python
# src/actors/nougat_actor.py, рядок 29
@ray.remote(num_gpus=float(os.getenv("NOUGAT_GPU_FRACTION", "0.5")))
class NougatActor: ...
```

**Один актор → 0.5 GPU за замовчуванням.**

| Сценарій | Змінна | GPU |
|----------|--------|-----|
| За замовчуванням | `NOUGAT_GPU_FRACTION=0.5` | **0.5 GPU** (половина) |
| Вся GPU | `NOUGAT_GPU_FRACTION=1.0` | **1.0 GPU** |
| CPU-режим | `NOUGAT_GPU_FRACTION=0` | **0 GPU** (повільніше) |

- Pipeline створює **рівно 1** NougatActor → максимум **1 GPU** при `NOUGAT_GPU_FRACTION=1.0`
- Якщо GPU немає і `NOUGAT_GPU_FRACTION > 0` — Ray буде чекати (actor не запуститься)
- WSL2: зазвичай потрібно `NOUGAT_GPU_FRACTION=0` або налаштований CUDA в WSL

**Перевірити доступність GPU:**
```bash
python -c "import torch; print(torch.cuda.is_available(), torch.cuda.device_count())"
```

---

## 4. Передумови

### 4.1 Python-оточення

```bash
cd /home/niko/projects/knoweledg_graf
source .venv/bin/activate
python -c "import ray, fitz, pyarrow, lxml; print('OK')"
```

### 4.2 Вхідні дані

Для кожного PDF потрібен відповідний TEI XML від GROBID:

```
data/literature/pdf/0030.pdf
data/literature/grobid_xml/0030.tei.xml   ← обов'язково
```

Якщо XML немає — папір мовчки пропускається (`[SKIP no-tei]`).

**Запустити GROBID-інгестію якщо XML відсутні:**
```bash
docker compose up -d   # GROBID на порту 8070

TOKENIZERS_PARALLELISM=false \
python -m src.ingestion.grobid_ingest \
    --pdf-dir data/literature/pdf \
    --xml-dir data/literature/grobid_xml
```

### 4.3 Nougat модель

Nougat завантажується автоматично при першому запуску (Hugging Face cache).

- Розмір моделі: ~1.6 GB
- VRAM при інференсі: ~2–4 GB (залежить від розміру crop)
- Потрібен `HF_TOKEN` у `.env` якщо модель приватна

---

## 5. Варіанти запуску

### Чому GPU = 3% за замовчуванням

```
PDF → [CPU: TEI parse ~50ms] → [CPU: render crops ~150ms] → [GPU: inference ~50ms] → [CPU: write]
                                                                ↑
                                         GPU active лише тут — решту часу idle
```

Один PDF обробляється **послідовно**. GPU простоює поки CPU рендерить наступний crop.
`NOUGAT_GPU_FRACTION=0.5` — це **Ray scheduling-слот**, не відсоток завантаження.

---

### Варіант 1 — Базовий (sequential)

**GPU ~3–5% | Throughput ~5 паперів/год**

```bash
source .venv/bin/activate

TOKENIZERS_PARALLELISM=false \
python -m src.ingestion.nougat_region_pipeline
```

Коли використовувати: перший тест, стабільне фонове виконання, мало RAM.

---

### Варіант 2 — Повна GPU (sequential + GPU fraction 1.0)

**GPU ~5–10% | Throughput ~6 паперів/год**

```bash
TOKENIZERS_PARALLELISM=false \
NOUGAT_GPU_FRACTION=1.0 \
python -m src.ingestion.nougat_region_pipeline
```

Що змінюється: Ray надає акторові пріоритетний доступ до всього GPU коли він активний.
CPU overhead той самий — загальне прискорення невелике (~10–20%).

---

### Варіант 3 — Паралельні PDF (workers=3)

**GPU ~15–25% | Throughput ~15 паперів/год**

```bash
TOKENIZERS_PARALLELISM=false \
NOUGAT_GPU_FRACTION=1.0 \
python -m src.ingestion.nougat_region_pipeline --workers 3
```

Як це працює:
```
Worker 1: [CPU parse+render PDF-A] → [GPU inference PDF-A] → [write]
Worker 2:        [CPU parse+render PDF-B] → [GPU inference PDF-B]
Worker 3:               [CPU parse+render PDF-C] → [GPU inference PDF-C]
                         ↑
                CPU роботи перекриваються — GPU отримує задачі безперервніше
```

Три PDF в flight одночасно. CPU-робота (TEI parse + PDF render) для PDF-B і PDF-C
перекривається з GPU inference для PDF-A. `ray.get()` та `actor.remote()` — thread-safe.

RAM: кожен worker тримає ~1–2 GB (PDF + crops в пам'яті).

---

### Варіант 4 — Агресивний (workers=5 + повна GPU)

**GPU ~30–45% | Throughput ~25 паперів/год**

```bash
TOKENIZERS_PARALLELISM=false \
NOUGAT_GPU_FRACTION=1.0 \
OMP_NUM_THREADS=1 \
python -m src.ingestion.nougat_region_pipeline --workers 5
```

5 PDF паралельно. NougatActor обробляє черги inference з 5 потоків.
Вузьке місце стає сам GPU (бажано), не CPU.

RAM: ~6–10 GB. Якщо не вистачає → зменшити до `--workers 3`.

---

### Варіант 5 — CPU-only (немає GPU або WSL2 без CUDA)

**GPU 0% | Throughput ~2 папери/год**

```bash
TOKENIZERS_PARALLELISM=false \
NOUGAT_GPU_FRACTION=0 \
python -m src.ingestion.nougat_region_pipeline --workers 2
```

Nougat запускається на CPU. Повільно, але не потребує CUDA.
`--workers 2` трохи допомагає бо CPU-паралелізм все одно є.

---

### Варіант 6 — Тест на N паперах

```bash
TOKENIZERS_PARALLELISM=false NOUGAT_GPU_FRACTION=1.0 \
python -m src.ingestion.nougat_region_pipeline --limit 10 --workers 3
```

---

### Таблиця порівняння

| Варіант | Workers | GPU fraction | GPU util | Throughput | RAM |
|---------|---------|-------------|----------|------------|-----|
| Базовий | 1 | 0.5 | ~3–5% | ~5 пап/год | ~3 GB |
| Повна GPU | 1 | 1.0 | ~5–10% | ~6 пап/год | ~3 GB |
| Паралельний | 3 | 1.0 | ~15–25% | ~15 пап/год | ~6 GB |
| Агресивний | 5 | 1.0 | ~30–45% | ~25 пап/год | ~10 GB |
| CPU-only | 2 | 0 | 0% | ~2 пап/год | ~4 GB |

> Точні цифри залежать від розміру PDF та кількості регіонів на сторінці.

---

## 6. Всі CLI-прапори та змінні середовища

### Аргументи CLI

| Прапор | За замовчуванням | Опис |
|--------|-----------------|------|
| `--limit N` | `None` (всі) | Обробити не більше N паперів |
| `--workers N` | `1` | PDF одночасно (>1 перекриває CPU+GPU) |
| `--overwrite` | `False` | Перепроцесувати навіть якщо `regions.parquet` існує |
| `--pdf-dir PATH` | `data/literature/pdf` | Директорія з PDF |
| `--tei-dir PATH` | `data/literature/grobid_xml` | Директорія з TEI XML |

### Змінні середовища

| Змінна | За замовчуванням | Опис |
|--------|-----------------|------|
| `NOUGAT_GPU_FRACTION` | `0.5` | Ray scheduling-слот для NougatActor (0 = CPU) |
| `TOKENIZERS_PARALLELISM` | — | Встановити `false` щоб уникнути deadlock |
| `RAYON_NUM_THREADS` | — | Встановити `1` для стабільності rust/arrow |
| `OMP_NUM_THREADS` | — | Обмеження OpenMP (`1`–`2` при workers>3) |
| `SODB_DIR` | `data/sodb` | Корінь SODB (де пишуться parquet) |
| `HF_TOKEN` | — | Токен Hugging Face для Nougat моделі |

### Константи злиття (в коді)

```python
MERGE_DISTANCE_Y    = 120   # pt (~1.7 cm) — вертикальний зазор між блоками
MERGE_DISTANCE_X    =  80   # pt (~1.1 cm) — горизонтальний зазор
MIN_BLOCK_AREA      =  50   # pt² — фільтр шуму
PAGE_FULL_THRESHOLD =  0.55 # якщо регіон >55% сторінки → full-page render
DPI                 = 300   # роздільність crop-зображень
```

---

## 7. Вихідні дані

### 7.1 `data/sodb/{paper_id}/regions.parquet`

Основний вихід — підхоплюється Stage 1 та parquet analytics.

| Поле | Тип | Зміст |
|------|-----|-------|
| `region_id` | string | `{paper_id}_p{page}_{type}_{n:03d}` |
| `paper_id` | string | Ідентифікатор паперу (stem PDF) |
| `page` | int32 | Номер сторінки (1-indexed) |
| `bbox_x0/y0/x1/y1` | float32 | Розширений bbox в PDF-points |
| `region_type` | string | `FIGURE_REGION`, `TABLE_REGION`, тощо |
| `source_parser` | string | `"HYBRID"` (GROBID coords + Nougat content) |
| `nougat_text` | string\|null | Візуальний текст (підписи, опис) |
| `nougat_latex` | string\|null | LaTeX (тільки для `FORMULA_REGION`) |
| `crop_path` | string\|null | Відносний шлях до PNG crop |
| `pipeline_hash` | string | `sha256:{16hex}` — ідентифікатор версії моделі |
| `created_at` | timestamp | Час запису |

### 7.2 `data/nougat_regions/{paper_id}/regions.json`

Детальний JSON з усіма полями для дебагу:
```json
{
  "paper_id": "0030",
  "blocks_extracted": 47,
  "regions_count": 12,
  "regions": [
    {
      "region": { "region_id": "0030_p3_fig_001", "region_type": "FIGURE_REGION", ... },
      "crop_path": "data/nougat_regions/0030/crops/0030_p3_fig_001.png",
      "nougat_result": { "visual_text": "...", "markdown_text": null, "error": null }
    }
  ]
}
```

### 7.3 `data/nougat_regions/{paper_id}/crops/*.png`

PNG-зображення кожного регіону @ 300 DPI. Зберігаються для ретроспективного аналізу та повторного inference.

---

## 8. Ідемпотентність та пропуск

Pipeline пропускає папір якщо **обидві** умови виконані:

1. `data/sodb/{paper_id}/regions.parquet` існує
2. `SODBManifest` має мітку `region_extract=done` для поточного `pipeline_hash`

```python
# Якщо змінилася модель Nougat — pipeline_hash зміниться
# → всі вже оброблені папери будуть перепроцесовані
pipeline_hash = "sha256:" + sha256(f"{model_name}:{model_version}").hexdigest()[:16]
```

**Примусово перепроцесувати:**
```bash
python -m src.ingestion.nougat_region_pipeline --overwrite
```

**Перевірити скільки вже оброблено:**
```bash
ls data/sodb/ | wc -l
ls data/nougat_regions/ | wc -l
```

---

## 9. Алгоритм зсередини

### Крок 1: Парсинг TEI XML → SemanticBlocks

```
<figure coords="3,12,34,400,120">        → FIGURE_BODY  (page=3)
  <graphic coords="3,20,40,380,90">      → GRAPHIC
  <figDesc coords="3,130,40,380,25">     → CAPTION  (text="Fig. 2. Hydrograph...")
  <label coords="3,128,40,50,12">        → LABEL    (text="Fig. 2")
</figure>

<table coords="5,10,50,400,200">         → TABLE_BODY
  <head coords="5,10,50,400,15">         → TABLE_HEAD
<note coords="5,255,50,400,20">          → CAPTION  (text="Table 3...")

<formula coords="4,100,100,300,30">      → FORMULA
<s coords="4,70,100,300,25">             → CONTEXT  (surrounding sentence)
```

### Крок 2: RegionBuilder — злиття по сімействах

Три незалежних сімейства (ніякого перехресного злиття):

```
Сімейство FIGURE:  FIGURE_BODY + GRAPHIC
Сімейство TABLE:   TABLE_BODY + TABLE_HEAD
Сімейство FORMULA: FORMULA
```

Алгоритм злиття (жадібний, до збіжності):
1. Злити блоки де bbox **перекриваються**
2. Злити блоки де вертикальний зазор ≤ 120pt і горизонтальні смуги перекриваються
3. Злити **side-by-side** панелі (multi-panel figure): `|mid_y1 - mid_y2| < 40` і горизонтальний зазор ≤ 80pt
4. Прикріпити CAPTION/LABEL до найближчого figure/table регіону
5. Прикріпити CONTEXT sentences до формул
6. Повторно злити після прикріплення

### Крок 3: Класифікація + розширення bbox

```python
padding = {
    "FIGURE_REGION":      top=15, bottom=25, sides=10,
    "TABLE_REGION":       top=20, bottom=30, sides=10,
    "FORMULA_REGION":     top=35, bottom=35, sides=20,
    "CHART_REGION":       top=15, bottom=25, sides=10,
    "MULTI_PANEL_FIGURE": top=10, bottom=20, sides=8,
    "SCIENTIFIC_DIAGRAM": top=15, bottom=25, sides=10,
}
```

Якщо `expanded_area / page_area ≥ 0.55` → `crop_strategy = "full_page"` (рендерити всю сторінку).

### Крок 4: Рендеринг + NougatActor

```python
# Всі crops відправляються конкурентно (Ray futures)
for region in regions:
    image = get_render_image(pdf_path, region)   # PyMuPDF @ 300 DPI
    future = actor.parse_image.remote(image, region_id)

# Збір результатів
results = [ray.get(f) for f in futures]
```

---

## 10. Типи регіонів

| Тип | Умова класифікації |
|-----|-------------------|
| `FIGURE_REGION` | Фігура без особливих ознак |
| `SCIENTIFIC_DIAGRAM` | Фігура з bbox > 300×200 pt |
| `CHART_REGION` | Підпис містить: hydrograph, map, watershed, basin, catchment, satellite, raster, dem, spatial, contour |
| `MULTI_PANEL_FIGURE` | ≥2 `<graphic>` блоки злито разом |
| `TABLE_REGION` | Містить `<table>` блоки |
| `FORMULA_REGION` | Містить `<formula>` блоки; `nougat_latex` замість `nougat_text` |

---

## 11. Усунення проблем

### NougatActor не запускається (GPU помилка)

```
RuntimeError: CUDA not available / no GPU resources
```

**Вирішення:**
```bash
NOUGAT_GPU_FRACTION=0 python -m src.ingestion.nougat_region_pipeline
```

### Deadlock у tokenizers

```
huggingface/tokenizers: The current process just got forked...
```

**Вирішення:**
```bash
TOKENIZERS_PARALLELISM=false python -m src.ingestion.nougat_region_pipeline
```

### Папір пропускається але регіони відсутні

```bash
# Перевірити manifest
python -c "
from src.document.sodb_manifest import SODBManifest
from pathlib import Path
m = SODBManifest('0030', None, Path('data/sodb'))
print(m.is_done('region_extract'))
"

# Примусово перепроцесувати
python -m src.ingestion.nougat_region_pipeline --overwrite --limit 1
```

### Перевірити вихід одного паперу

```bash
python -c "
import pyarrow.parquet as pq
t = pq.read_table('data/sodb/0030/regions.parquet')
print(t.schema)
print(t.to_pandas()[['region_id','region_type','page','nougat_text']].head(10))
"
```

### Ray не може знайти GPU (WSL2)

WSL2 потребує CUDA-драйверів від Microsoft. Якщо не налаштовано:
```bash
# Запуск без GPU
NOUGAT_GPU_FRACTION=0 TOKENIZERS_PARALLELISM=false \
python -m src.ingestion.nougat_region_pipeline --limit 5
```
