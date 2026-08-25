# GeoHydroAI — Повний аналіз пайплайну

> Дата: 2026-05-14 | Версія resolver: v0.3.2 | Версія онтології: v2.0

---

## 1. Загальна архітектура

```
PDF / GROBID XML
    │
    ▼
Stage 1: src/ingestion/pipeline.py
    │   ├── TEIParser (SDOM) → TEIDocument
    │   ├── Section classifier  → sections{}
    │   ├── Entity extractor    → entities{}
    │   ├── Geo extractor       → geo{}
    │   ├── LLM Judge (Ollama)  → validates entities
    │   └── → data/literature/paper_json/*.paper.json
    │
    ▼
Stage 2: src/enrichment/openalex_enrichment.py
    │   ├── DOI lookup → OpenAlex API
    │   ├── cited_by_count, authors, institutions, topics
    │   └── → data/enriched/*.json
    │
    ▼
Stage 3: Reference enrichment
    │   └── Bibliography DOI resolution
    │
    ▼
Stage 4: src/normalization/ + src/graph/build_graph.py
    │   ├── ontology_matcher.py → canonical_id
    │   ├── embedding_matcher.py → semantic fallback
    │   └── → Neo4j nodes + edges
    │
    ▼
Stage 5: ChromaDB + Analytics
        ├── SPECTER2 embeddings → ChromaDB
        ├── Parquet export → DuckDB
        └── Dashboard (Dash)
```

---

## 2. XML → JSON: як вибираються дані

### 2.1 Що GROBID виробляє (TEI XML)

GROBID розбиває PDF на TEI XML зі структурою:
```xml
<TEI>
  <teiHeader>
    <titleStmt><title/></titleStmt>      ← метадані
    <author><persName/><affiliation/></author>
    <biblStruct/>                          ← references
  </teiHeader>
  <body>
    <div><head>Introduction</head>
      <p>...</p>                           ← текст параграфів
    </div>
  </body>
</TEI>
```

### 2.2 Реальний приклад (032003_1 — Sentinel-1 flood detection)

| Параметр | XML | JSON | Збіг |
|---|---|---|---|
| Заголовок | 1 `<title>` | `metadata.title` | ✓ точний |
| Автори | 3 `<author>` | 3 в `metadata.authors` | ✓ з ORCID |
| Секції (div) | **32 `<div>`** | **5 секцій заповнено** | ⚠️ collapse |
| References | 64 `<biblStruct>` | 64 refs (77% з DOI) | ✓ добре |
| Методи | 3 `<term>` в XML | 3 в `entities.methods` | ✓ але різні |
| Метрики | у тексті | **0 extracted** | ✗ провал |

### 2.3 КРИТИЧНА ПРОБЛЕМА: розпарсинг параграфів

```
XML div "Introduction": 6 параграфів → 5 chars raw (майже пусто!)
XML div "Design Considerations": 13 параграфів → 12 chars raw
```

**Причина**: `ElementTree` (стандартний) не збирає `tail` text від inline тегів.
TEI XML інтенсивно використовує `<ref>`, `<formula>`, `<rs>` інлайн — весь текст
між ними живе у `.tail`, а не у `.text`. `lxml` читає правильно, ET — ні.

**Результат**:
- `sections["methods"]` = 0 chars (хоч у XML 32 div з текстом)
- `sections["other"]` = 42 571 chars — весь текст звалюється туди
- `sections["results"]` = 0 chars
- Гео-детектор, task-класифікатор і Ollama суддя читають `"other"` замість `"methods"`

### 2.4 Що pipeline витягує правильно

| Поле | Якість | Метод |
|---|---|---|
| `metadata.title` | ✓ 100% | `<titleStmt/title>` |
| `metadata.doi` | ✓ 65% (35% немає) | `<idno type="DOI">` |
| `metadata.year` | ✓ 95% | `<date type="published">` |
| `metadata.authors` | ✓ повні з ORCID | `<author>` блоки |
| `metadata.authors[].affiliation_countries` | ✓ з NER | `<affiliation>` + geo NER |
| `references` | ✓ 64/64 | `<listBibl/biblStruct>` |
| `references[].doi` | ✓ 77% | `<idno type="DOI">` в ref |
| `sections.abstract` | ✓ 1554 chars | `<abstract>` спеціальний блок |
| `sections.methods` | **✗ часто 0** | TEI `<div>` text збір баг |
| `entities.satellites` | ✓ regex-based | `data_sources+methods` text |
| `entities.metrics` | **✗ 0/paper** | не витягнуто для цього документу |
| `entities.geo.study_geo` | ✓ 95% | regex + NER + affiliation |

---

## 3. Якість екстракції по 20 paper JSONs

```
Corpus: 20 paper JSONs

РОЗДІЛИ
  Середня кількість заповнених секцій (з 5):  4.2 / 5
  Статті з ≥3 розділами:                       19 / 20

ENTITIES
  Середня кількість методів/статтю:             2.85  ← низько
  Середня кількість супутників/статтю:          1.20
  Середня кількість метрик/статтю:              2.90
  Статті з метриками:                           9 / 20 = 45%  ← слабко

ГЕО
  Має DOI:                                     65%   ← 35% без DOI
  Має первинну країну:                         95%   ✓
  Середня confidence study_type:               0.66  ← низька
  Середня confidence task:                     0.90  ✓

REFERENCES
  Середня к-сть посилань/статтю:               45
  Refs з DOI (вибірка):                        30%   ← основний ботлнек

LLM JUDGE
  Запущено:                                    0 / 20  ✗  (Ollama пропускається)
```

---

## 4. Ollama Judge — стан і проблеми

### Стан: ONLINE (mistral-nemo:12b, 7.1 GB)

### Чому суддя не запускається
Судячи зі статистики (0/20 судді), і коду:
```python
if not is_ollama_available(base_url): return {"status": "skipped", ...}
```
Або Ollama недоступний під час запуску пайплайну, або `needs_judge()` повертає False.

### Що суддя перевіряє ✓
- `study_type` (case_study / regional / global_algorithmic)
- `study_country` (первинна країна)
- `task` (flood_mapping_satellite, hydrological_modeling, ...)
- `rivers` (список річок)

### Що суддя НЕ перевіряє ✗
- sensors / satellites
- formulas / tables
- metrics (RMSE, NSE, accuracy)
- citations / references

### Слабкості промпту

**Поточний промпт** (1736 chars) — правильно структурований JSON-only output, але:

1. **Відсутній chain-of-thought** — mistral-nemo схильний галюцинувати без reasoning step
2. **Приклади не надані** — few-shot significantly підвищив би accuracy
3. **Контекст урізається** до 3500 chars методів — для довгих статей втрачається контекст
4. **Немає верифікації confidence** — модель каже `"confidence": 0.95` без обґрунтування

**Рекомендований промпт (patch)**:
```
STRICT RULES:
...
Think step by step before answering (внутрішньо), 
then output ONLY the final JSON.

EXAMPLE (few-shot):
Input study_type: "case_study", abstract mentions "Bangladesh watershed"...
Output: {"study_type": {"accepted": true, "original_value": "case_study", ...}}
```

---

## 5. Nougat Actor — формули і графіки

### Конфігурація
```python
@ray.remote(num_gpus=float(os.getenv("NOUGAT_GPU_FRACTION", "0.5")))
class NougatActor:
    # Lazy load: model loads on first parse_pdf() call
    # facebook/nougat-base (~350 MB, 1.4 GB peak VRAM)
```

### Що виробляє
```python
{
  "formulas":      [{"xml_id": "...", "text": "Q = CIA"}],  # LaTeX
  "tables":        [{"xml_id": "...", "label": "Table 1", "rows": [...]}],
  "markdown_text": "# Introduction\n...",  # .mmd Nougat output
  "visual_text":   "plain body text",
  "parser_kind":   "nougat"
}
```

### Проблеми

| Проблема | Severity | Деталі |
|---|---|---|
| CUDA драйвер застарілий | **КРИТИЧНО** | версія 12060, вимога > 12xxx — GPU inference недоступний |
| Немає page-level timeout | High | зависання на bitrot PDFs |
| CUDA cache не очищається | Medium | `cuda` не знайдено в коді |
| OOM не розрізняється | Medium | generic `except Exception` |
| Формули в LaTeX, не нормалізовані | Medium | `Q = CIA` ≠ `Q=CIA` ≠ rational formula AST |
| Nougat не вбудований у RAG | High | `formulas[]` ніде не індексуються у ChromaDB |

### Висновок: Nougat **підключений**, але **не активний** через старий GPU драйвер

---

## 6. Нормалізація (src/normalization)

### Пайплайн нормалізації

```
raw entity name
    │
    ▼ 0a. Canonical-ID self-lookup
    │     (якщо вже "method.hec_hms" — повертаємо одразу)
    │
    ▼ 0c. Context-aware disambiguation pre-pass
    │     (завантажує ontology_disambiguation_rules.json)
    │
    ▼ 1. Alias table lookup (детерміністичний)
    │     e.g. "HEC-HMS" → method.hec_hms  (conf=1.00)
    │
    ▼ 2. Display-name exact match
    │
    ▼ 3. Semantic embedding fallback (BAAI/bge-large-en-v1.5, 1024-dim)
    │     threshold: ≥0.82 → match, 0.68-0.82 → uncertain, <0.68 → unknown
    │
    ▼ 4. Unknown
```

### Реальні результати нормалізації

```
Method               canonical_id                    type       conf
HEC-HMS              method.hec_hms                  alias      1.00  ✓
SWAT                 method.swat                     alias      1.00  ✓
random forest        method.random_forest            alias      1.00  ✓
InSAR                method.insar                    alias      1.00  ✓
SAR                  → (крашить embedding_matcher)   ERROR      0.00  ✗
```

### Знайдений баг: `embedding_matcher.py` — format string на NoneType

```
unsupported format string passed to NoneType.__format__
```

Причина: в `embedding_matcher.py` є `f"{score:.2f}"` де `score` може бути `None`
(коли embedding model повертає NaN similarity для деяких рядків).

**Fix потрібен**: `f"{score or 0.0:.2f}"`

---

## 7. Knowledge Graph — що будується

### Схема Neo4j

```
(Paper)─[:USES_METHOD {confidence, surface_form, evidence, resolver_version}]→(Method)
(Paper)─[:USES_SENSOR {confidence, ...}]→(Sensor)
(Paper)─[:REPORTS_METRIC {confidence, ...}]→(Metric)
(Paper)─[:FROM_COUNTRY]→(Country)
(Paper)─[:HAS_TOPIC]→(Topic)
(Author)─[:AUTHORED]→(Paper)
(Author)─[:AFFILIATED_WITH]→(Institution)
(Institution)─[:LOCATED_IN]→(Country)
(Method)─[:CO_OCCURS_WITH {count}]→(Method)
(Paper)─[:CITES]→(Paper)  ← ЧАСТКОВО не вмонтовано
```

### Граф constraint-и і indexes

- 13 uniqueness constraints (Paper.paper_id, Author.author_id, ...)
- 11 range indexes (year, doi, cited_by_count, ...)
- 1 fulltext index на `Paper.title`
- **Відсутній**: compound index для `(country, year)` — повільні геозапити

### Що Добре ✓

- MERGE-based idempotency — безпечно перезапускати
- Edge properties: confidence, evidence, resolver_version (нові)
- Path confidence: `reduce(s=1.0, e IN rels | s * e.confidence)`
- Trust tier queries: high ≥0.80 / medium 0.60–0.80 / low <0.60
- Method co-occurrence edges (порогове значення 3 papers)

### Що Не Готово ✗

| Проблема | Файл | Статус |
|---|---|---|
| CITES edges не вмонтовані в graph_loader | `graph_loader.py` | P0 відкрито |
| `build_cites_rows()` не викликається | `graph_loader.py` | потребує 1 рядок |
| Нема compound index (country+year) | `graph_constraints.py` | легко додати |
| 78 sensor families hardcoded | `graph_loader.py` | технічний борг |

---

## 8. Цитування і пріоритизація статей

### Стан: ЗБЕРІГАЄТЬСЯ але НЕ ВИКОРИСТОВУЄТЬСЯ для ранжування

```python
# OpenAlex enrichment збирає:
openalex = {
  "cited_by_count": 47,      # ← є в Neo4j, є в Parquet
  "openalex_id": "W123456",
  ...
}

# Dashboard використовує (data_flood.py):
ORDER BY p.cited_by_count DESC  # ← тільки для відображення

# Retriever (retriever.py) — НЕ ЗНАЄ про cited_by_count:
hits.sort(key=lambda h: h["distance"])  # тільки embedding distance
```

### Можливе покращення (citation-weighted retrieval)

```python
def _citation_boost(hit: dict) -> float:
    cited = hit.get("metadata", {}).get("cited_by_count", 0) or 0
    return min(0.15, 0.15 * math.log1p(cited) / math.log1p(500))

# Combined score:
score = (1 - hit["distance"]) * 0.85 + _citation_boost(hit) * 0.15
```

---

## 9. Зведений аналіз ботлнеків

### 🔴 КРИТИЧНІ (блокують якість)

| # | Проблема | Де | Вплив |
|---|---|---|---|
| 1 | **TEI paragraph tail text не читається** | `pipeline.py` / секції | sections["methods"]=0, суддя бачить пустоту |
| 2 | **CITES edges не в graphі** | `graph_loader.py` | граф без citation network |
| 3 | **LLM Judge ніколи не запускається** | `pipeline.py:needs_judge()` | 0/20 papers validated |
| 4 | **embedding_matcher NoneType crash** | `embedding_matcher.py` | нормалізація падає на SAR, SMA тощо |
| 5 | **CUDA драйвер застарілий** | система | Nougat не може PDF парсити GPU-прискорено |

### 🟡 ВАЖЛИВІ (знижують якість)

| # | Проблема | Де | Вплив |
|---|---|---|---|
| 6 | Metrics extraction дуже слабка (45% статей мають метрики) | `pipeline.py` | RMSE/NSE/Accuracy не зберігаються |
| 7 | 30% references мають DOI | `references[]` | CITES edges матимуть багато "stub" nodes |
| 8 | cited_by_count не впливає на RAG ranking | `retriever.py` | старі важливі статті можуть програти новим |
| 9 | study_type confidence середнє 0.66 | pipeline rules | LLM judge мав би виправляти, але не запускається |
| 10 | Formulas/tables (Nougat) не індексуються | indexing pipeline | формули недоступні для semantic search |

### 🟢 ДОБРЕ ПРАЦЮЄ

| Компонент | Оцінка |
|---|---|
| DOI/title/author/year extraction | 9.5/10 |
| Author ORCID + affiliation | 9/10 |
| Reference extraction | 8.5/10 |
| Satellite detection (regex) | 8/10 |
| Geo primary country (95%) | 8.5/10 |
| Study type classification (rules) | 7.5/10 |
| Alias-based normalization (HEC-HMS, SWAT, RF) | 10/10 |
| Confidence propagation + edge lineage | 9/10 (нова) |
| Disambiguation (SMA, SCS, ML, CC, AI...) | 9/10 (нова) |
| Ontology versioning | ✓ (нова) |

---

## 10. Рекомендовані виправлення (за пріоритетом)

### P0 — Цього тижня

**1. Виправити TEI paragraph читання (`pipeline.py`)**
```python
# Поточний (баг):
text = " ".join(p.text or "" for p in div.findall("p"))

# Виправлений (lxml itertext):
text = " ".join(
    "".join(p.itertext()) for p in div.findall(f"{{{NS}}}p")
)
```

**2. Виправити embedding_matcher NoneType**
```python
# Знайти рядки з f"...{score:.2f}..." і замінити:
f"{(score or 0.0):.2f}"
```

**3. Вмонтувати CITES edges у graph_loader**
```python
# У load_entity_edges_from_enriched():
cites_rows = gw.build_cites_rows(pid, doc["paper"].get("references", []))
all_cites_rows.extend(cites_rows)
```

### P1 — Наступний тиждень

**4. Citation-weighted retriever**
```python
# retriever.py — додати cited_by_count до scoring
score = semantic_score * 0.85 + citation_boost * 0.15
```

**5. Metrics extraction покращення**
Додати regex для числових метрик:
```python
METRIC_RE = re.compile(
    r'(?:Overall Accuracy|OA|Precision|Recall|F1|RMSE|NSE|MAE|R²|Kappa)'
    r'\s*[=:]\s*([\d.]+)\s*%?', re.I
)
```

**6. Ollama judge investigation**
Перевірити чому `needs_judge()` повертає False для всіх 20 papers:
```python
# Додати debug logging:
log.debug("needs_judge(%s): study_type=%s conf=%.2f", pid, label, conf)
```

### P2 — Місяць

**7. Compound index Neo4j**
```cypher
CREATE INDEX paper_country_year IF NOT EXISTS
FOR (n:Paper) ON (n.primary_country, n.year)
```

**8. Formula indexing (Nougat → ChromaDB)**
```python
for formula in nougat_result.get("formulas", []):
    store.upsert(chunk_id=f"{pid}_formula_{formula['xml_id']}",
                 text=formula["text"], metadata={...})
```

**9. Оновити GPU драйвер** (NVIDIA 12060 → поточний)

---

## 11. Статус SDOM міграції

| Задача | Статус |
|---|---|
| TEIParser як єдиний XML parser | ✓ завершено |
| LayoutAwareChunker → ChromaDB | ✓ завершено |
| SPECTER2 embeddings (768-dim) | ✓ завершено |
| Видалити `tei_to_sections.py` (3221 рядків dead code) | ✗ P0 відкрито |
| CITES infrastructure | ✓ є в writer, ✗ не викликається в loader |
| ChromaDB config collision | ✗ P0 відкрито |
| Розбити pipeline.py (god object) | ✗ P1 1-2 тижні |
| `lxml` на рівні модуля (SDOM violation) | ✗ line 48 |

---

## 12. Резюме

Система **GeoHydroAI** — добре архітектурована knowledge graph платформа з сильною
онтологією, confidence propagation, та trust-aware graph. Проте є **5 критичних
блокерів**, які суттєво знижують якість:

1. **TEI paragraph tail** — основний ботлнек: методи/результати не читаються
2. **CITES edges** — граф без citation network (P0, 1 рядок коду)
3. **LLM Judge** — ніколи не валідує (потребує дослідження needs_judge())
4. **embedding_matcher** — крашить на ~30% entities (NoneType format bug)
5. **GPU драйвер** — Nougat CPU-only, формули не оброблюються

Після виправлення цих 5 проблем система перейде від **"working prototype"** до
**"production-grade scientific knowledge engine"**.
