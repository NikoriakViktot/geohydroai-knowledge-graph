# GeoHydroAI — Knowledge Graph for Scientific Literature Analysis

## Що це

GeoHydroAI — research-платформа для автоматизованого аналізу наукової літератури в галузі **гідрології, картування повеней та дистанційного зондування**. Система обробляє корпус PDF-статей, витягує наукові знання (методи, сенсори, метрики, географічні об'єкти, числові факти), будує граф знань у Neo4j і надає семантичний пошук та аналітичний дашборд.

**Домен**: flood inundation mapping, SAR/optical remote sensing, hydrological modelling, DEM analysis, ML/DL for hydrology.

**Поточний корпус**: ~3,875 PDF / 6,014 TEI XML / ~4,850 paper.json / 986,832 ChromaDB chunks.

---

## Для чого використовується

| Сценарій | Як система допомагає |
|----------|----------------------|
| Аналіз літератури | Автоматичне витягування методів, сенсорів, метрик із тисяч статей |
| Порівняння методів | Граф Neo4j: які методи використовуються для flood mapping |
| Пошук числових фактів | NumericFact шар: NSE/RMSE/KGE по статтях, таблицях, підтверджені провенансом |
| Семантичний пошук | ChromaDB (SPECTER2 768-dim): "Sentinel-1 flood Ukraine" → топ-10 релевантних статей |
| Написання наукового огляду | paper_my/ — генерація секцій, evidence reports, посилання з корпусу |
| Аналіз citation impact | OpenAlex збагачення, citation graph в Neo4j |
| Виявлення research gaps | Онтологія + граф: які комбінації методів недостатньо досліджені |

---

## Основні можливості

Підтверджено кодом:

- **GROBID інтеграція** — HTTP-клієнт з exponential backoff, rate limiting, pre-flight PDF triage
- **TEI/XML парсинг** — власна SDOM архітектура (TEIDocument, frozen dataclass), строга ізоляція lxml
- **KB-driven extraction** — 1,118 сутностей, 3,161 alias, 46 v2 моделей онтології (методи, сенсори, DEMs, метрики)
- **LLM judge** — Ollama (mistral-nemo:12b) для семантичної валідації entity labeling (~75% papers)
- **Ray distributed processing** — 3 воркери, SpacyActor/EmbeddingActor/OllamaActor, sliding window, idempotency
- **NumericFact extraction** — TEI tables → structured metric-value nodes у Neo4j (16,308 фактів)
- **Nougat visual pipeline** — PDF → формули/таблиці через GPU inference (180 papers)
- **Neo4j Knowledge Graph** — 57,603 вузли, 92,003 CITES, Cypher MERGE (no accidental DELETEs)
- **ChromaDB semantic search** — SPECTER2 embeddings (768-dim), 986,832 chunks
- **OpenAlex enrichment** — citation counts, authors, institutions, topics (2,963 papers)
- **Plotly Dash dashboard** — 11 сторінок: overview, analytics, citations, geospatial, methods, research, topics
- **Research query service** — multi-source retrieval (DuckDB + Neo4j + ChromaDB) + Gemini synthesis
- **DuckDB pipeline registry** — single-writer, RLock, idempotency ledger
- **606 automated tests** — без GPU/Ollama/Ray (усі моки в conftest.py)

---

## Технологічний стек

| Рівень | Технологія | Призначення |
|--------|-----------|-------------|
| PDF parsing | GROBID (Docker, port 8070) | TEI XML extraction з PDF |
| Visual parsing | Nougat (facebook/nougat-base, 250M) | Формули/таблиці з PDF images |
| NLP | SpaCy (en_core_web_sm) | NER для geo-entities |
| Document model | TEIDocument (власна SDOM) | Canonical document object |
| Distributed compute | Ray | Parallel paper processing (3 workers) |
| LLM judge | Ollama / mistral-nemo:12b | Entity semantic validation |
| Embeddings | SPECTER2 (allenai/specter2_base, 768-dim) | Scientific document embeddings |
| Vector store | ChromaDB 1.5.9 | Semantic search (~987K chunks) |
| Graph DB | Neo4j (Docker, 7687/7474) | Knowledge graph |
| Analytics DB | DuckDB | Pipeline registry + analytics queries |
| Analytics files | Apache Parquet | Intermediate artifacts |
| AI synthesis | Gemini (via ai_gateway.py) | Research query synthesis |
| Dashboard | Plotly Dash (port 8050) | Interactive analytics UI |
| Testing | pytest | 606 tests |
| Task schema | Pydantic v2 | paper.json validation |
| Ontology | JSON files (src/ontology/) | Entity definitions, aliases, rules |

---

## Як запустити

### Передумови

```bash
# Docker сервіси (GROBID + Neo4j)
docker compose up -d

# LLM judge
ollama serve &
ollama pull mistral-nemo:12b
```

### Legacy Ray Pipeline (XML → paper.json)

```bash
TOKENIZERS_PARALLELISM=false \
RAYON_NUM_THREADS=1 \
OMP_NUM_THREADS=2 \
OLLAMA_MODEL=mistral-nemo:12b \
OLLAMA_URL=http://localhost:11434 \
SPACY_MODEL=en_core_web_sm \
.venv/bin/python3 -m src.orchestration.pipeline_runner --workers 3
```

> **Увага**: `--workers 3` — стабільний ліміт для цієї системи (~3 GB RAM). Ніколи `--workers 8+`.

### Post-pipeline кроки

```bash
# Нормалізація (онтологічне заземлення)
python -m src.orchestration.normalization_runner \
    --input-dir data/literature/paper_json \
    --output-dir data/normalized

# ChromaDB переіндексація (якщо порожня або пошкоджена)
TOKENIZERS_PARALLELISM=false EMBEDDING_MODEL=allenai/specter2_base \
python -m src.orchestration.reindex_chromadb \
    --xml-dir data/literature/grobid_xml \
    --json-dir data/literature/paper_json \
    --collection-name flood_papers_768d

# OpenAlex збагачення
python -m src.orchestration.enrichment_runner

# Побудова Neo4j графа
python -m src.graph.build_graph \
    --uri bolt://localhost:7687 --user neo4j --password python2024

# NumericFact loader
python -m src.graph.table_kg_loader

# Parquet analytics
python -m src.enrichment.build_parquet_layer
```

### Dashboard

```bash
python -m src.dashboard_dash.app   # http://localhost:8050
```

### Тести

```bash
python -m pytest tests/ -q   # 606 тестів, без GPU/Ollama/Ray
```

---

## Структура документації

| Файл | Призначення |
|------|-------------|
| [ARCHITECTURE.md](ARCHITECTURE.md) | Повна архітектура системи |
| [PIPELINE.md](PIPELINE.md) | Pipeline обробки статей (два паралельні) |
| [KNOWLEDGE_GRAPH.md](KNOWLEDGE_GRAPH.md) | Опис графа знань |
| [DATA_MODEL.md](DATA_MODEL.md) | Модель даних і структури |
| [CODEMAP.md](CODEMAP.md) | Карта коду та залежності |
| [RESEARCH_WORKFLOW.md](RESEARCH_WORKFLOW.md) | Workflow дослідника |
| [AUDIT_HISTORY.md](AUDIT_HISTORY.md) | Еволюція системи, виправлення, відкриті проблеми |
