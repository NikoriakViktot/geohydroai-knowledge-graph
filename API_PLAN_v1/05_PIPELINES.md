# 05 — Пошук нових статей, завантаження і повний пайплайн інжесту

**Дата**: 2026-10-02 · **Стан**: план. Кожен крок прив'язано до наявного коду (file:line) і до потрібного рефакторингу.

Ціль: одна асинхронна задача, яка за запитом або seed-DOI:
1. знаходить нові статті;
2. відсіює вже наявні;
3. оцінює релевантність;
4. легально завантажує PDF;
5. проганяє повний пайплайн до **нових вузлів у Neo4j** і **нових векторів у ChromaDB**;
6. звітує про кожну статтю.

---

## 1. Загальна схема

```text
JobSpec (заморожений, хешований)
   │
   ▼
[1] discover ──► [2] dedup ──► [3] screen ──► [4] review ──► [5] acquire ──► [6] ingest ──► [7] post-ingest
 OpenAlex/       identity.     SPECTER2 +      auto-accept    OA-каскад        14 кроків       identity,
 Crossref/       resolve()     (LLM-лейни)     ≥ поріг або    або upload       per paper       кеші, звіт,
 snowball        in_corpus /   ролі, скор      PATCH людини   (paywall →       (§5)            аналітика
                 stub / new                                    needs_manual)                    (батч)
```

Кожна стрілка — крок задачі з `input_hash`. Повторний запуск продовжує з першого незавершеного кроку. Кандидати живуть у таблиці `candidates` (jobs.sqlite) зі статусом `new → screened → accepted|rejected|needs_manual → acquired → ingested|failed`.

---

## 2. JobSpec (узагальнення `briefs/geodesy.py:773` і `theses.yaml`)

```yaml
name: swot-dam-break-2026q4
queries:                     # заморожуються; sha256 від нормалізованого списку → provenance (правило L: hash the query set)
  - "SWOT water surface elevation dam break flood"
  - "outburst flood hydraulic model satellite altimetry validation"
filters: {from_date: 2018-01-01, types: [article, review, preprint], languages: [en]}
seeds: ["10.1029/2025gl120832"]          # опційно: snowball
snowball: {direction: both, hops: 1, max_seeds_per_hop: 40, min_citations: 0}
screening:
  mode: embedding+llm        # embedding | embedding+llm | none
  embedding_threshold: 0.55  # cosine(SPECTER2(title+abstract), SPECTER2(query))
  llm_band: [0.45, 0.65]     # LLM лише для пограничних — економія квоти
  theses_ref: null           # або посилання на набір тез → ролі SUPPORTS/CONTRASTS/METHOD…
limits: {max_candidates: 2000, max_accept: 50, max_llm_calls: 300, max_gpu_minutes: 240}
acquire: {sources: [unpaywall, europepmc, openalex, publisher_patterns, arxiv], allow_wayback_for_oa: true}
ingest: {steps: all, nougat: false, judge: true}
cohort: null                 # напр. "paper_3": позначка, щоб виключати з recount
```

---

## 3. Кроки 1–4: пошук, дедуплікація, скринінг, рецензія

| Крок | Що робить | Наявний код | Що змінити |
|---|---|---|---|
| 1a. Пошук | OpenAlex `/works?search=` + фільтри; Crossref `query.bibliographic` (опційно); arXiv API (опційно) | `harvest_openalex.discover` (:312; пагінація сторінками, ≤ 500 робіт на запит, пауза 2 с); `harvest_queries.build_query_set` (:132); `openalex_actor.search_works` | **cursor-пагінація**, `mailto` з конфігу (polite pool), обробка 429/`Retry-After`, кеш з TTL (зараз невдачі кешуються назавжди); відв'язати від `theses.yaml`; версіонувати набір запитів (зараз у коді 7 зрізів, а заморожений прогін записав 4) |
| 1b. Snowball | `cited_by:` / `cites:` OpenAlex, фолбек Crossref за назвою | `p2_literature.expand(seed_dois, hops=2, max_seeds_per_hop=40)` (:227); fuzzy ≥ 88 (:148) | виправити баг дедуплікації назв (:253); прибрати прив'язку до T25–T34; тести |
| 1c. Відсутні посилання | ранжування DOI, на які корпус посилається, але яких немає | `missing_reference_recovery.run` (:283), кеш `openalex_doi.db:openalex_extended` (61 580 рядків), `missing_rs_priority.csv` (3 648) | **спершу О-25** (таблиця посилань затерта до 883 рядків); вирізати Sci-Hub (:207) |
| 2. Дедуп | кожен кандидат → `in_corpus` / `stub_in_graph` (вузол-заглушка з `CITES`) / `new` | ~10 реалізацій; найкраща `PaperResolver` (`tools/paper3_audit/corpus.py:185`) | **один** `identity.resolve` (02 §3): DOI без урахування регістру, slug-псевдоніми, назва ≥ 0.95 + рік ±1; 144 групи дублікатів DOI — політика злиття |
| 3. Скринінг | SPECTER2-схожість назви й анотації до запиту/тези; LLM лише в пограничній смузі | `harvest_openalex` score (:253-264: 2×hits + ln(1+cit) + 3 seed + 2 recent); `tools/paper3_audit/screen.py` (8 ролей, SUPPORTS/CONTRASTS лише з перевіреною цитатою); `abstract_mode` | score = скор пошуку + косинус SPECTER2; LLM через спільний `llm.py` з квотою; модель — аргумент, а не перезапис `MODEL_POOL` (`screen.py:289`) |
| 4. Рецензія | auto-accept ≥ поріг, решта в черзі `GET /discovery/candidates?status=screened`, рішення `PATCH` | — | новий UI-мінімум: таблиця в Dash або просто API + CSV |

**Якщо пошук слугує доказом «прогалини в літературі»** (Paper 3, §7.8), задача прив'язується до воріт прийняття:
- позитивні контролі ≥ 90 % recall;
- заморожений набір контролів.

Інакше результат має статус `RETRIEVAL_UNVALIDATED` і не публікується як прогалина.

---

## 4. Крок 5: завантаження PDF (лише легальні джерела)

**Каскад** — узагальнений `src/paper_3/oa_resolver` (:177 `fetch_pdf`, :210):
1. Unpaywall (потрібен `OPEN_ALEX_EMAIL`; зараз за порожнього значення крок **мовчки пропускається**);
2. Europe PMC;
3. OpenAlex `best_oa_location` + `locations`;
4. шаблони видавців (MDPI, Copernicus, Frontiers, відкритий Wiley);
5. arXiv;
6. Wayback-копія — **лише для OA-сторінок**, які блокують ботів (так 2026-10-01 отримано Lefebvre 2019 і Pulvirenti 2021, обидва CC-BY).

| Що | Правило |
|---|---|
| Перевірка файлу | сигнатура `%PDF`, розмір > 20 KB, `pdf_io`: кількість сторінок, текстова проба (сканований PDF → прапорець для Nougat). Слабка перевірка `recover_missing.py:176` (content-type **або** заголовок) пропускає HTML — не використовувати |
| Ім'я файлу | `pdf/<doi_slug>.pdf` через **одну** функцію `identity.doi_slug`; без DOI — `pdf/sha256_<12hex>.pdf` |
| Ідемпотентність | sha256 PDF; той самий хеш → `duplicate`, без повторного інжесту |
| Запис у реєстр | `acquisition(doi, route, url, oa_status, licence, version, sha256, fetched_at, failure_reason)`. Зараз ліцензія й версія **ніде не записуються** |
| Paywall | статус `needs_manual` + рядок у черзі (формат `MANUAL_DOWNLOAD_LIST.csv`: priority, doi, attempted_url, publisher_host, why_not_downloaded, save_as). Користувач кладе PDF через `POST /ingest/upload` (ім'я дає сервер) |
| Sci-Hub | **ні в якій формі**. `missing_reference_recovery.scihub_download` (:207) і `scripts/download_missing_papers.py` видалити (О-35) |

---

## 5. Крок 6: інжест однієї статті (14 кроків)

| # | Крок | Вхід → вихід | Наявна точка входу | Без Ray? | Що треба зробити |
|---|---|---|---|---|---|
| 1 | Тріаж + хеш | PDF → `{sha256, pages, has_text}` | `pdf_triage.triage_pdf` (:34), `sha256_pdf` (:130) | так | — |
| 2 | GROBID | PDF → `tei.xml` | `GROBIDClient().process_pdf` (`grobid_client.py:488`; `is_alive` :137; 3 retry, 180 с) | так | URL з конфігу (зараз прошитий, :39); `GROBID_CONSOLIDATE_HEADER=0` (консолідація зависає > 600 с; зміна не закомічена); атомарний запис |
| 3 | Валідація TEI | → ok / причина | `tei_validator.validate_tei` (:25) | так | — |
| 4 | Nougat (опційно, до 5) | PDF + TEI → `sodb/{id}/regions.parquet` | `NougatRegionPipeline().process_pdf` (:785/803) | **ні** (актор 0,5 GPU) | інжектувати локальний `NougatParser`; спершу закомітити виправлення transformers 5 (текст є лише в 424 з 5 008, О-32) |
| 5 | Парсинг + сутності + метрики + judge | TEI → paper.json (dict) | ядро `build_paper_json` (`ingestion/pipeline.py:169`); обгортка `process_paper` (`process_paper.py:289`, `max_calls=1`) | **ні** | **`process_paper_local(xml, ner_fn, encode_fn, judge_fn, vector_sink)`**; Ray-задача — лише адаптер. `max_calls=1` перезавантажує BGE-large на кожну статтю |
| 6 | Чанки + ембединги + Chroma | TEIDocument → чанки → SPECTER2 → upsert | `LayoutAwareChunker("sentence").chunk` → `VectorStore.upsert_document_chunks` (:107) | так | **спершу О-24** (повний `paper_id` у `_make_id`, унікальні ключі абстракту, рисунків, таблиць і формул); `delete(paper_id)` перед upsert; **жодного фолбеку на MiniLM** (О-12/О-31): нема SPECTER2 → крок `failed` |
| 7 | paper.json | dict → файл | `pipeline_runner.py:308-310` (не атомарно) | — | tmp + rename; перевірка JSON після запису |
| 8 | Нормалізація | paper.json → `normalized/{id}.json` | `normalization_runner.process_one` (:92); чисте `normalize_paper_entities` (`normalization_utils.py:84`) | так | **запускати після judge** (зараз до нього: `process_paper.py:473-477` проти :486); перевірка пропуску — за `paper_id`, а не за stem (:121-132) |
| 9 | OpenAlex | DOI → `enriched/{id}.json` | `build_graph_entity(doi)` (`openalex_actor.py:260`); `enrich_paper` (`openalex_enrichment.py:189`) | ядро — так | звичайний клієнт поверх SQLite-кешу; обробка 429; зараз крок пропускає наявний файл, тож дані ніколи не оновлюються |
| 10 | Neo4j: підграф статті | enriched + normalized → MERGE | частини: `parquet_builder._extract` (:117), `GraphWriter`, `graph_loader._edge_row` (:397), `build_cites_rows`/`write_cites_edges` (`neo4j_writer.py:286-348`), `load_paper_flood_event_edges` (:625) | так | **`write_paper_subgraph(paper_id)`**. Спершу О-1 (ключі під `paper`), О-2 (один ключ ідентичності; **підвищення вузла-заглушки**: нова стаття часто вже існує як заглушка з `CITES`) |
| 11 | NumericFact / регіони → Neo4j | TEI → факти | `extract_numeric_facts` + записувачі (`table_kg_loader.py:102-114`); `region_kg_loader.load_all_regions` (:487, підрядок) | частково | точний `paper_id` замість префікса/підрядка |
| 12 | Реєстр | → рядок на кожен крок | `PipelineRegistry` (`registry_db.py:196`; зараз пише лише GROBID) | так | рядки за стадіями; ключ `paper_id` + `sha256`; пише лише worker |
| 13 | Ідентичність + кеші | → `paper_identity`, інвалідація | — | — | оновити таблицю ідентичності; скинути `_pack_cache`, `lru_cache` у `data_neo4j`, індекс `PaperStore`, DuckDB-view |
| 14 | Аналітика | → Parquet-шар | `build_parquet_layer` (інкрементальний, **з багом О-25**); `parquet_builder`/`sodb_aggregator` (повна перебудова) | так | періодична батч-задача, не в кожному інжесті |

**Готовий шаблон ланцюга**: `src/paper_3/harvest_ingest.py` уже проходить download → GROBID → pipeline → normalize → Chroma (з перевіркою, що колекція виросла) → enrich → parquet → graph через `python -m` підпроцеси (:69-80). Його кроки перетворюються на кроки задачі. Пароль Neo4j при цьому **не** передається в командному рядку (:410).

---

## 6. Повторний інжест і «відкликання» фактів

Інваріант «лише `MERGE`» (`test_import_invariants.py:106`) означає, що повторний інжест статті **накопичує** застарілі зв'язки. Наприклад, judge виправив сутність, а старе ребро лишилося.

Пропоноване рішення не порушує інваріант для звичайних записів:
- кожне ребро й факт статті отримує `ingest_run_id`;
- запити читають лише останній `run_id` статті;
- окрема **admin-задача** `compact_paper(paper_id)` з явним підтвердженням видаляє ребра попередніх запусків, із журналом;
- це єдине місце з `DELETE` поза `--wipe`, і тест інваріантів отримує для нього виняток з поясненням.

---

## 7. Небезпеки довгоживучого сервісу і як їх закрито

| Небезпека | Контроль |
|---|---|
| Chroma: лише один процес може тримати `PersistentClient`; незалитий WAL при виході (відомий збій 1.5.9); маркер `.index_verified` пише лише `reindex_chromadb` (:157-164) | Chroma-сервер (04 A1); коректна зупинка; worker оновлює маркер після кожного інжесту |
| DuckDB-реєстр блокується на процес | стан задач — у `jobs.sqlite`; реєстр пише лише worker |
| GROBID: ~5 GB RAM, конфлікт із Ray (OOM) | ресурсний клас `grobid=1`; worker піднімає/зупиняє контейнер між батчами |
| Один GPU на Ollama 12B, GROBID-full і Nougat | ресурсний клас `gpu=1`, серійна черга |
| `TOKENIZERS_PARALLELISM`, `RAYON_NUM_THREADS`, `OMP_NUM_THREADS` | виставляються у worker **до** імпорту torch |
| Різні дефолти spaCy-моделі (sm/trf/md) | одна модель у конфігу, записується в provenance |
| Три простори ключів (sha256 / slug / DOI) | таблиця ідентичності; DOI у нижньому регістрі; унікальний констрейнт на `Paper.doi` |
| Неатомарні записи + «пропустити, якщо файл є» | tmp + rename; пропуск лише за `input_hash` |

---

## 8. Складена задача `discover-and-ingest`

- **Бюджети**: `max_accept`, `max_llm_calls`, `max_gpu_minutes`. Задача зупиняється на першому вичерпаному й позначає себе `partial` зі звітом.
- **Звіт**: `report.md` + `report.json`, на кожну статтю: шлях (in_corpus / new), скор, роль, маршрут завантаження, ліцензія, кроки інжесту, створені вузли й чанки, помилки. Плюс `provenance` (хеш запитів, коміт, модель ембедингів, колекція).
- **Оцінка пропускної здатності** (уточнити пілотом):
  - legacy-пайплайн при 2 воркерах ~26 с на статтю (заміряно 2026-09-18);
  - GROBID — секунди-десятки секунд на статтю;
  - OpenAlex — з кешу миттєво, інакше ~0,1 с;
  - Neo4j-підграф і Chroma upsert — секунди.
  - Отже, **~1–2 хв на статтю, 50 статей ≈ 1–2 год**.
  - Nougat (якщо ввімкнено) — ще хвилини на статтю на GPU.
