# 01 — Індекс проєкту і реєстр «закрито / відкрито»

**Стан на**: 2026-10-02 · **Гілка**: `paper3-full-transformation-draft` (HEAD `9607a47`)

**Метод**: перевірено за кодом і даними, а не за документами.
- Пункти ремедіації 0.1–5.5 і інваріанти CLAUDE.md звірено з файлами (file:line).
- Повний прогін тестів.
- Лічильники всіх каталогів даних.
- ChromaDB перевірено через SQLite (`mode=ro`), без завантаження HNSW.
- Neo4j зараз вимкнений, тож граф описано за знімком `data/graph/graph_summary.json` від 2026-09-23.

---

## 1. Система одним абзацом

GeoHydroAI — корпус наукових статей про повені, гідрологію й ДЗЗ (≈5 000 статей) і інструменти навколо нього.

- **Legacy-пайплайн**: GROBID → TEI XML → `paper.json` (сутності, метрики, LLM-judge) → нормалізація до онтології (`canonical_id`) → збагачення OpenAlex.
- **Сховища**: Neo4j (граф), ChromaDB (1,35 млн чанків SPECTER2 768-d), DuckDB/Parquet (аналітика й реєстр).
- **Dash-дашборд** з синтезом через Gemini.
- **Інструменти для статей про Каховку** (Paper 1/2/3): тези, атомарні твердження, скринінг літератури Gemini-лейнами, перевірка DOI, збирання рукопису.
- **Паралельно розробляється SDOM-пайплайн** (Stage0–2.5) на рівні об'єктів документа.

---

## 2. Карта коду (`src/`, 315 файлів, ≈77 тис. рядків)

| Пакет | Рядків / файлів | Роль | Тести |
|---|---|---|---|
| `paper_3/` | 16 458 / 62 | інструменти Paper 3: тези, вердикти, когорта, `insitu/` (щорічники), `v2/` (збирання рукопису за шаблонами), знімки | 678 тестів разом із paper1/tools (48 % усіх) |
| `ingestion/` | 10 185 / 37 | legacy-етапи parse/geo/entity/judge, KB, Stage0–2 SDOM, `nougat_region_pipeline`, GROBID-клієнт | частково (stage0–2.5, parser routing) |
| `dashboard_dash/` | 7 861 / 32 | Dash (11 сторінок), `research_query_service`, `ai_gateway` (Gemini) | 1 файл |
| `extraction/` | 6 045 / 16 | `table_extractor` (NumericFact), `regex_extractor`, `metric_ontology` | test_15/16 |
| `paper_audit/` | 5 354 / 34 | списки читання, `recover_missing`, курація бенчмарку (gitignored) | — |
| `document/` | 4 762 / 21 | SDOM: `TEIDocument` (frozen, неглибоко), TEI/Nougat/Hybrid-парсери, роутер, чанкер, `pdf_io`/`tei_io` | так |
| `graph/` | 4 043 / 12 | `build_graph`, `neo4j_writer`, `graph_loader`, `fact_writer`, `table_kg_loader` | 1 файл; **`graph_loader` без тестів** |
| `ontology/` | 3 848 / 12 | злиття й уточнення онтології | test_19 |
| `orchestration/` | 2 680 / 11 | Ray `pipeline_runner`, `process_paper`, runners нормалізації/збагачення, реіндекс Chroma, `retry.py` | test_07/17/18 |
| `analytics/` | 2 657 / 9 | Parquet-аналітика, `missing_reference_recovery` | — |
| `registry/` | 2 348 / 6 | DuckDB-реєстр (`registry_db.py:196`, а не `pipeline_registry.py`, як пише CLAUDE.md) | **немає** |
| `semantic_objects/` | 1 948 / 8 | Stage 2.5: 15 IF-THEN правил | test_14 |
| `normalization/` | 1 468 / 6 | `ontology_matcher`, `embedding_matcher` | 1 файл |
| `enrichment/` | 1 391 / 6 | OpenAlex, Parquet-шар, всесвіт авторів | **немає** |
| `actors/` | 1 299 / 8 | Ray-актори: spaCy, Embedding, Ollama, Nougat, GeoNames, OpenAlex, VectorStore | через conftest-моки |
| `processing/`, `evaluation/`, `pipeline/`, `validation/`, `schemas/`, `retrieval/`, `vectorstore/` | 300–930 кожен | дублікат чанкера, MetricFact-оцінювання, RAG-пайплайн, judge_normalizer, pydantic-схема, retriever, `chroma_store` | частково / немає |
| `discovery/`, `assembly/`, `embedding/`, `utils/`, `config/`, `debug/` | 0–210 | осиротілі (`discovery`, `assembly`), заглушки, порожній `debug/` | немає |

Поза `src/`:

- **`tools/paper3_audit/`** (+ `tools/paper3_literature_audit.py`) — пакет літературного аудиту.
  - Підкоманди: claims, references, bibtex, checks, corpus, article, docx, export, numbers, rebase.
  - **Повністю не закомічений** (`?? tools/` у git status).
- `scripts/` — аналітика для статей (`paper1_*.py`, не закомічені) і разові скрипти.
- `paper_unet-case-kakhovka/`, `paper_terrain-case-kakhovka/`, `paper_my/`, `paper_3_audit/` — дзеркала бандлів і результати аудитів.
- `AUDIT_v1/` (40 звітів), `CODE_REVIEW_v1/` (ревю 2026-06-11), `docs_v2/` (документація станом на 2026-06-09).

---

## 3. Дані (`data/`, 75 GB) і сховища

| Що | Кількість / розмір | Свіжість, примітка |
|---|---|---|
| `literature/pdf` | 4 849 PDF, 24 GB | |
| `literature/grobid_xml` | 5 039 `.tei.xml` (+ 2 322 файли `:Zone.Identifier`), 973 MB | у CLAUDE.md записано 6 014 |
| `literature/paper_json` | 5 038, 540 MB | **11 обрізаних** `1-s2.0-*` (2026-05-18), ніколи не перегенеровуються |
| `normalized` / `enriched` | 5 027 / 5 013 | `normalized_entities` і `references` лежать **під ключем `paper`** |
| `sodb` | 5 039 тек, у 5 008 є `regions.parquet` | Nougat пройшов майже весь корпус (у CLAUDE.md: «180/3 875») |
| `nougat_regions` | 5 008 тек, 44 GB | crop-зображення |
| `analytics` | 32 файли, 42 MB | `numeric_facts.parquet`, `papers.parquet` від 2026-05-21; `missing_rs_priority.csv` |
| `cache` | `openalex_doi.db`, `author_cache.db`, 707 MB | |
| `registry` | `pipeline_registry.duckdb` + `parquet/` + `audit/` | |
| `raw` / `parsed` | 1 / відсутній | SDOM Stage0/1 на корпусі не запускався |
| **ChromaDB** `.chromadb/` | 8,4 GB; колекція `flood_papers_768d`, dim 768, **1 354 158** ембедингів | ⚠ у `embeddings_queue` **252 upsert-и від 2026-09-19 не потрапили в HNSW-сегмент** (vector `max_seq_id` 2 822 609 проти metadata 2 822 861) |
| **Neo4j** (знімок 2026-09-23) | 204 177 вузлів: Paper 57 603, Author 122 174, NumericFact 21 256 … | ⚠ `USES_METHOD = USES_SENSOR = REPORTS_METRIC = REFERENCES = CO_OCCURS_WITH = 0` (див. О-1); `rel_counts()` не рахує `CITES` |

---

## 4. Сервіси та середовище

| Компонент | Стан |
|---|---|
| Neo4j (`course-neo4j`, neo4j:5) | **зупинений** (Exited 255, 2 дні тому) |
| GROBID (`grobid`, 0.9.0-full) | **зупинений** (Exited 143, 8 днів тому); займає ~5,3 GB RAM, коли працює |
| Ollama | працює (:11434), модель `mistral-nemo:12b` (7,1 GB) |
| Чужі контейнери на машині | `kakhovka-dashboard` :8501, `kakhovka-postgis` :5434, `kakhovka-nominatim` :8080 |
| Залізо | 23 GB RAM, 32 ядра, RTX A4000 16 GB (CUDA 12.6, bf16) |
| Python | `.venv` 3.12.3; є `uvicorn 0.46`, `starlette 1.2`, `pydantic 2.13`, `pydantic-settings 2.14`, `httpx 0.28`, `rapidfuzz`, `google-genai 2.5`, `anthropic 0.104`, `torch 2.12+cu126`, `transformers 5.8.1`; **немає `fastapi`**, а також `peft`, `trl`, `bitsandbytes`, `datasets` |
| `requirements.txt` | бракує 23 сторонніх пакетів, які імпортує `src/` (dash, plotly, google, yaml, torch, scipy …) |

---

## 5. Тести

- **1 413 тестів у 62 файлах**: 1 405 пройшли, 8 пропущено, **35 с**, без GPU/Ollama/Ray. У CLAUDE.md досі написано «606».
- Етапи SDOM: 47 / 41 / 62 / 84 тести (у CLAUDE.md записано 48/51/51/84).
- **Пакети без жодного тесту**: `registry` (2 348 рядків), `enrichment` (1 391), `processing`, `validation`, `retrieval`, `embedding`, `pipeline`, `config`, `assembly`, `discovery`.
- `graph_loader` не тестується, і саме там критичний баг О-1.
- Немає `pytest.ini`/`pyproject`/CI, гейтів покриття (`pytest-cov`) і `mypy`, хоча обидва встановлені.

---

## 6. ЗАКРИТО (підтверджено кодом)

| Що | Доказ |
|---|---|
| Дефолт пароля `python2024` прибрано з `src/` | `settings.require_env` (settings.py:15); grep `src/` порожній |
| `TEIDocument` frozen (ремедіація 0.4) | `models.py:235`; `replace()` у `parser_router.py:180,241`, `hybrid_parser.py:199,280` |
| Фасади `pdf_io` / `tei_io` (1.1) | lxml лише в `parser.py:51`, `tei_io.py:13`; fitz — `pdf_io.py:15`, `nougat_parser.py:142` |
| `src/graphstore/` з `CREATE` видалено, Evidence через MERGE (1.2) | `fact_writer.py:439-443` + unique constraint `:133` |
| Тести інваріантів (1.3) | `tests/test_import_invariants.py` (5 тестів) |
| Термін `canonical_id` (1.5) | 0 входжень `ontology_id`; глосарій у `DATA_MODEL.md:315` |
| Ре-аудит від'ємних NSE/kappa (2.2) | `scripts/reaudit_negative_metrics.py`, `AUDIT_v1/NEGATIVE_METRICS_REAUDIT.md` |
| Телеметрія judge/тірів (2.5) | `judge_normalizer.py:413,438`; `pipeline_runner.py:313-375` |
| Ключові банки з KB (2.6) | `regex_extractor.py:394` (без тесту) |
| Тести enrichment / merge_ontology (3.4), фікстури битих входів (3.5) | test_19, test_18 |
| Retry + `max_restarts=1` на акторах, `atexit ray.shutdown` (3.2, частково) | `orchestration/retry.py`; `pipeline_runner.py:171` |
| Помилка поширення міток task | `judge_stage.py:358,411`; `repair_task_labels.py` |
| Nougat після transformers 5.8.1 | `NougatParser._image_kwargs` (`nougat_parser.py:374`) + тест |
| Повне збагачення OpenAlex | 5 013 файлів |
| Перебудова Neo4j | 2026-09-23 (204 177 вузлів, 21 256 NumericFact), але без семантичних ребер, див. О-1 |
| Перевірка `open_citations` Paper 3 | `literature_audit_paper3/valid_artsclt/citation_verification.md`, 2026-10-01 |

---

## 7. ВІДКРИТО (за важкістю)

### Критичні: блокують API і будь-який навчальний датасет

| # | Проблема | Де | Наслідок |
|---|---|---|---|
| **О-1** | `graph_loader` читає `doc["normalized_entities"]` і `doc["references"]` з верхнього рівня, а в усіх 5 013 enriched-файлах вони під `doc["paper"]` (перевірено вибіркою 200/200) | `src/graph/graph_loader.py:448`, `:607` | Граф не має ребер Paper→Method/Sensor/Metric і цитувань із поточних даних. Мовчки відкинуто 15 322 зв'язки з методами, 4 944 — з сенсорами, 1 196 — з метриками й бібліографії 4 905 статей. Тест цього не ловить |
| **О-2** | Ідентичність Paper-вузлів: вузли-заглушки отримують `title`/`year` з розбору посилань цитуючої статті (`ON CREATE SET`); `:Paper` мерджиться за 4 різними ключами (`paper_id`, `doi`, `source_file`, `id`) | `neo4j_writer.py:83,311`; `fact_writer.py:166,393` | Невідповідність назва/DOI/рік у графі, наприклад «MODELLING OF EXTREME FLOODS … UKRAINE», 1966, з DOI `10.1126/science.aan2506`; ризик дублікатів |
| **О-3** | Немає таблиці ідентичності статей: файли названо трьома способами | `data/literature/*` | Відповідь на «чи є DOI X у корпусі» потребує grep; ризик витоку між train/test у датасеті |
| **О-4** | 252 незалиті upsert-и в Chroma (WAL > HNSW) | `.chromadb/chroma.sqlite3` | За CLAUDE.md, саме так виглядає відомий збій 1.5.9 під час перезавантаження. Перевірити кількість і пошук при наступному відкритті |
| **О-5** | `tools/paper3_audit/` (0 файлів у git), тести до нього, `scripts/paper1_*` і ~120 рядків у `nougat_parser.py`/`pdf_io.py`/`grobid_client.py` **не закомічені**. `src/paper_audit/` і `paper_my/` у `.gitignore`, хоча закомічений `src/paper_3/v2/assemble.py:476` імпортує з `src/paper_audit` | git status; `.gitignore:67-68` | Ключовий літературний інструментарій існує лише в робочому дереві. Чистий clone ламає крок docx і `tests/test_paper_3_harvest.py:233` |
| **О-24** | **Колізії `chunk_id` у Chroma.** `LayoutAwareChunker._make_id` хешує лише перші 16 символів `paper_id`; ключ абстракту сталий (`"abstract"`); ID рисунків, таблиць і формул з GROBID повторюються між статтями. 2 762 з 5 038 `paper_id` мають спільний 16-символьний префікс (335 груп) | `src/document/chunker.py:156,311,336,360,420-422` | **Перевірено на живій колекції (SQLite):** чанки має 4 955 статей, а чанк абстракту — **лише 2 462**. Приблизно 2 490 статей втратили абстракт, а також частину рисунків, таблиць і формул: upsert залишив останню статтю групи. 83 статті корпусу не мають жодного чанка |
| **О-25** | **Таблиця посилань у Parquet-шарі затирається** при кожному інкрементальному запуску: дедуп за `paper_id`, а в рядках посилань є лише `source_paper_id` | `src/enrichment/build_parquet_layer.py:145-173,200,212-229,283` | `data/parquet/references.parquet`: **883 рядки** (2026-05-24 було 320 633); `data/analytics/references.parquet`: 0. Список 60 тис. відсутніх DOI не відтворюється |
| **О-26** | **`data/analytics` застарів і не відповідає власній схемі** (`papers.parquet`: 3 680 рядків, 9 з 21 колонки), але його читають дашборд, research_query_service, `build_graph` і `paper_audit` | `src/analytics/parquet_schema.py:33-55`; `data_loader.py:71-72,280`; RQS `:796-799` | Запити за `has_doi`/`abstract_length` падають у DuckDB; 1 358 статей невидимі для цих шляхів |
| **О-27** | Семантичні докази для синтезу шукаються в **довільних 500 статтях** (`LIMIT 500` без `ORDER BY`; фільтр за роком завжди ввімкнений) | `src/dashboard_dash/research_query_service.py:263-264,284-296,676-679` | Відповіді Gemini в дашборді спираються на випадкову десяту частину корпусу. `src/paper_3/retrieve.py:98-111` свідомо обходить цю функцію |

### Високі

| # | Проблема | Де |
|---|---|---|
| О-6 | Gold set не розмічений: `true_*` 0/107, `true_canonical_id` 0/217 (ремедіація 2.4) | `data/evaluation/` |
| О-7 | Метрики поза діапазоном **відкидаються**, а не позначаються `suspect` (2.1). `validate_metric_value` / `units_comparable` викликають лише тести | `metric_ontology.py:220,338-377`; `table_extractor.py:277-278` |
| О-8 | Юнікодний мінус `−` (U+2212) не розбирається: «NSE = −0.27» губиться | `regex_extractor.py:34`; `table_extractor.py:254-261` |
| О-9 | 11 обрізаних `paper.json` ніколи не перегенеровуються (pre-filter перевіряє лише наявність файлу); запис не атомарний | `pipeline_runner.py:99-109`, `:309-310` |
| О-10 | Пароль Neo4j не ротовано (0.5) | `.env`, `docker-compose.yml:14`, `scripts/add_missing_papers_to_neo4j.py:44`, `scripts/revise_paper.py:447`, CLAUDE.md:15 |
| О-11 | GPU-інваріант порушено: Nougat і SentenceTransformer завантажуються поза акторами; модель вантажиться під час імпорту | `parser_router.py:270`, `nougat_parser.py:318-320`, `analytics/method_clustering.py:33` |
| О-12 | `EmbeddingActor` мовчки падає на 384-d MiniLM поруч із 768-d колекцією | `actors/embedding_actor.py:28-34` |
| О-13 | `load_theses` мовчки ламає `theses.json` з kakhovka-terrain: 0 посилань, запити розбиває на окремі символи | `tools/paper3_audit/claims.py` |
| О-14 | Скрипти для статей прив'язані до бандла Paper 3 (`config.py` → `paper_unet`) і тягнуть файли з іншого дистрибутива через `wsl.exe`; `bibtex.wsl_cat` виконує `bash -c "cat {relpath}"` (shell-ін'єкція, якщо шлях колись прийде із запиту) | `tools/paper3_audit/config.py`, `bibtex.py:30`, `src/paper_3/snapshot.py:30-37` |
| О-28 | **«Порятунок» NSE**: `_parse_float` ділить будь-яке значення > 1 на 100, тож NSE 1.7 стає правдоподібним 0.017 | `src/extraction/scientific_extractor.py:133-136` |
| О-29 | Три схеми ID метрик (`metric.overall_accuracy` / `metric.oa` / `"OA"`): OA 97.2 ніколи не перераховується в 0.972 | registry ↔ `metric_ontology.py:234` ↔ KnowledgeBase |
| О-30 | Порядок завантаження конфігурації: `src/config/__init__.py` виконується **до** `load_dotenv()`, тому `CHROMA_DIR`, `COLLECTION_NAME`, `EMBEDDING_DEVICE`, `CHUNK_*`, `OLLAMA_BASE_URL` з `.env` ігноруються | `src/config/__init__.py:17-46`, `settings.py:5` |
| О-31 | Невдалий upsert (наприклад, після фолбеку на MiniLM) мовчки ковтається, і стаття отримує SUCCESS **без векторів** | `src/orchestration/process_paper.py:465-466` (+ О-12) |
| О-32 | Текст Nougat є лише в **424 з 5 008** `regions.parquet` (наслідок бага transformers 5; виправлення не закомічене) | `data/sodb/*/regions.parquet` |
| О-33 | Ін'єкції: SQL і Cypher збираються f-рядками з користувацьких списків — небезпечно, щойно це стане API | RQS `:70,268-273,393-396,496-498,720-723,800`; `data_loader.py:41-45` |
| О-34 | Рік обрізано жорстким вікном 1990–2025: статті 2026 року невидимі | `data_loader.py:33-34`; RQS `:263-264,575` |
| О-35 | **Код завантаження з Sci-Hub** у репозиторії — юридичний ризик; у сервіс не загортати, видалити | `src/analytics/missing_reference_recovery.py:207`, `scripts/download_missing_papers.py` |
| О-36 | Gemini-аудит: перевірка «вже зроблено» йде **після** виклику моделі, тож повторний запуск платить знову; запуск змішує моделі (записується `GEMINI_MODEL`, а шлюз крутить `GEMINI_MODEL_POOL`); невдалі запити (зокрема таймаути) назавжди кешуються як «не знайдено» | `src/paper_3/classify_relation.py:332-340`; `ai_gateway.py:36-47`; `_work/*_cache.json` |

### Середні

| # | Проблема | Де |
|---|---|---|
| О-15 | Дві поверхні конфігурації; розбіжність `OLLAMA_URL` / `OLLAMA_BASE_URL`; `pydantic-settings` не використовується (5.2) | `config/__init__.py`, `settings.py:9`, `judge_stage.py:273` |
| О-16 | 18 голих `ray.get` без таймауту; кеш-актори без `max_restarts` | `reference_enrichment.py:191`, `nougat_region_pipeline.py:793,859`, `openalex_enrichment.py:67` |
| О-17 | Дашборд: `callbacks.py` на 1 963 рядки, 55 `except`, що ковтають помилки (4.1); власний драйвер Neo4j і 14 сирих MATCH (4.4) | `callbacks.py:165-167`, `data_neo4j.py:23-25` |
| О-18 | Особистий email у закоміченому коді; `paper_audit` читає `GOOGLE_API_KEY` замість `GEMINI_API_KEY` | `missing_reference_recovery.py:53`, `paper_3/v2/reference_registry.py:43`, `paper_3/p2_literature.py:57`, `paper_audit/_utils.py:67` |
| О-19 | `google.generativeai` застарів (бібліотека сама попереджає про припинення підтримки) | місця імпорту — під час міграції на `google.genai` |
| О-20 | Немає CI, гейта покриття, `mypy` (3.6); `registry` і `enrichment` без тестів | — |
| О-21 | Логування: 473 `print`, 49 `logging.basicConfig`, `configure_logging` не використовується (4.3) | `src/` |
| О-22 | `kg_synchronizer` рахує збої як 0 (4.5); немає `skipped_inputs.parquet` (4.2) | `kg_synchronizer.py:49` |
| О-23 | Дублікати: два чанкери, три нормалізатори метрик, чотири шляхи до Neo4j, три парсери BibTeX у floodstate-eo + один тут | `processing/chunker.py` ↔ `document/chunker.py` тощо |
| О-37 | Ідентичність DOI: **6 нормалізаторів DOI**, **4 конвенції slug, що не збігаються** (по-різному обробляють `:`; 964 з 48 391 відсутніх DOI містять `:`), ~10 різних перевірок «чи є в корпусі», 144 групи дублікатів DOI | `paper_3/_utils.py:55,71`, `recover_missing.py:61`, `tools/paper3_audit/corpus.py:39` тощо |
| О-38 | HTTP-клієнти розмножені: 5 клієнтів CrossRef, 8+ OpenAlex, 5 копій OA-завантаження PDF, 4 способи викликати Gemini; контактний email вписаний у ≥ 7 місцях, а `OPEN_ALEX_EMAIL` порожній; немає обробки 429/`Retry-After` | див. 02 §3 |
| О-40 | **Документ, що не є статтею, у корпусі**: `EXHIBIT A #3013EURIZON_Prof. Osypov` — адміністративний документ з іменами людей | `data/literature/*` і похідні — виключити з корпусу, Chroma, графа й датасетів |
| О-41 | Дублікати в корпусі: 158 груп DOI/назви, 203 зайві копії (119 груп — копії `model_hec_ras_NNNN`); 214 файлів без назви, частина назв — назви журналів; 5 038 файлів ≈ 4 824 унікальні статті | лічильники «статей» завищені; витік між train/test |
| О-42 | Ліцензії статей **ніде не записані** (в `data/enriched` немає полів OA/licence; офлайн лише 991 DOI в `openalex_doi.db`) | потрібна задача `licence_check` (див. 05 §4, 08 §9) |
| О-43 | **Пастка Docker-тому**: дані графа в томі `neo4jdata` контейнера `course-neo4j`, запущеного через `docker run`, а `docker compose` монтує **порожній** `knoweledg_graf_neo4jdata` | `docker-compose.yml:20,38` → `external: true` |
| О-44 | **Заморожений ретривал Paper 3 (2026-09-16) уже не відтворюється побайтово**: закріплений `references.parquet` (`d6124d0b…`) затерто 2026-09-23 (О-25); Chroma зросла з 1 313 665 до 1 354 158. Другий закріплений вхід `data/analytics/papers.parquet` (`0f7a45a2…`) ще цілий — **не перебудовувати без заморожування** | `data/paper_3_audit/RETRIEVAL_MANIFEST.json`; [10 §4](10_DATA_CLEANUP.md) |
| О-45 | `.hf_cache/` — це `HF_HOME` пайплайну з закріпленими ревізіями (specter2 `3447645e`), але про це ніде не сказано; повторне завантаження без ревізії тихо змінить простір ембедингів | `src/ingestion/pipeline.py:85-86`, `settings.py:45` |
| О-39 | `Retriever`: «буст за цитуваннями» мертвий (поля `cited_by_count` немає в жодному paper.json, ключі не збігаються); `VectorStore` створюється на кожен запит, `count()` — на кожен запит; `RAGPipeline.ingest(force=True)` **очищає колекцію** | `retriever.py:33-62`; `chroma_store.py:172`; `rag_pipeline.py:69-89` |

### Низькі

- `nougat_region_pipeline.py` (1 069 рядків) і `registry_db.py` (1 258) не розбиті (5.3); ADR немає (5.4).
- Тестові файли всередині `src/` (5 шт.), осиротілі `assembly`/`discovery`, порожній `src/debug`.
- Сміття в корені: `=23.0`, `=4.0`, `docker` (0 байт, у git), `rag_pipeline.log`, 3 957 файлів `:Zone.Identifier`.

### Дослідницькі потоки (статті)

| Потік | Стан | Джерело |
|---|---|---|
| Paper 3 — ворота прийняття | **NOT READY**: recall позитивних контролів 7/23 = 30 % (потрібно ≥ 90 %), контролі не заморожені, 9 критичних тез без holdout. Тез **36** (T01–T36, блоки A–K), а не 24. Прогін 20260916T092929Z: 286 рядків `relations.parquet` побудовано лише за анотаціями, 4 різними моделями Gemini; вердикти: 30 `RETRIEVAL_UNVALIDATED`, 5 `NOT_FOUND`, 1 `SUPPORTED_BUT_SPARSE` | `data/paper_3_audit/ACCEPTANCE_GATE.md`; `src/paper_3/theses.py:25` |
| Paper 1 / Paper 2 — §7.8 | OPEN41–49 `blocked`: літературні твердження LK/LT/LD мають статус `RETRIEVAL_UNVALIDATED` | `PAPER1_OPEN_ITEMS.md`, `PAPER2_OPEN_ITEMS.md` |
| Paper 2 — HEC-RAS | OPEN60–68 `blocked` (моделювання, таблиця 1, висновки) | `PAPER2_OPEN_ITEMS.md` |
| Paper 3 (U-Net) — літературний аудит | 7 тез високого пріоритету не розв'язані (TH-INT-02, TH-MET-01/03/06/08, TH-RES-04/14) | `literature_audit_paper3/completion_report.md` |
| Paper 3 — відкриті цитування | перевірено: 2 FIX (FABDEM 1.61→1.12 m; Lehnigk у Discussion), 1 WORDING (Zheng); відкриті ліцензія FABDEM, He 2024, сторінки Zheng/Monti | `valid_artsclt/citation_verification.md` |

---

## 8. Розбіжності документації (виправити разом з О-1…О-5)

| Документ | Пише | Насправді |
|---|---|---|
| CLAUDE.md:24, docs_v2 (README/INDEX/CODEMAP) | 606 тестів | 1 413 |
| CLAUDE.md:246, CODEMAP.md:103,122,192 | `src/registry/pipeline_registry.py` | `registry_db.py:196` |
| CLAUDE.md:199 | `METRIC_EXTRACTION_PATCH_REPORT.md` у корені | у `AUDIT_v1/` |
| CLAUDE.md, мапа даних | pdf 3 875 · TEI 6 014 · json 3 691 · normalized/enriched 3 680 · Nougat 180 | 4 849 · 5 039 · 5 038 · 5 027/5 013 · 5 008 |
| CLAUDE.md:53 | «3 workers, ~3 GB RAM» | 23 GB RAM, 32 ядра; дефолт `RAY_MAX_CONCURRENT=4` (`settings.py:66`) |
| KNOWLEDGE_GRAPH.md:12,15 | 57 603 вузли; 16 308 NumericFact | 57 603 — це лише Paper; усього 204 177; NumericFact 21 256 |
| CODEMAP.md:26,210,212 | `graphstore/`, `src/config.py` | видалені |
| README.md:1105, requirements.txt:3 | дефолтна модель — MiniLM | SPECTER2 |
| DATA_MODEL.md:124 | TEIDocument не можна змінити | freeze неглибокий (`Section` не frozen, поля-списки) |
| CLAUDE.md (тип чанка) | `body` | у корпусі: `sentence` 1 290 238, `figure` 35 438, `formula` 17 989, `table` 8 031, `abstract` 2 462 |
| пам'ять/документи Paper 3 | 24 тези | 36 |

---

## 9. Що з відкритого блокує API (див. [06_ROADMAP.md](06_ROADMAP.md))

- **Фаза 0** (до будь-якого коду API): О-1, О-2, О-3, О-4, О-5, О-10, **О-24, О-25, О-26, О-30, О-37, О-43, О-44, О-45**. Без них API віддаватиме:
  - граф без зв'язків;
  - пошук без абстрактів половини корпусу;
  - неправильні назви й роки;
  - і не зможе відповісти «чи є стаття в корпусі».
- **Фаза 1** (read-only API): О-12, О-15, О-19, **О-27, О-33, О-34, О-39** і документація §8.
- **Фаза 2** (інжест): О-7, О-8, О-9, О-11, О-16, **О-31, О-32, О-35, О-38**. Без них новий інжест повторює старі помилки.
- **Фаза 4** (LLM): **О-36**.
- **Датасет LoRA** ([08_LORA_DATASET.md](08_LORA_DATASET.md)): О-1…О-3, О-6, О-7, О-8, **О-24, О-28, О-29, О-40, О-41, О-42**. Інакше помилки, дублікати й чужі документи потраплять у ваги моделі.
- **Очищення й міграція** ([10](10_DATA_CLEANUP.md), [09](09_MIGRATION.md)): О-4, О-43, О-44, О-45.
