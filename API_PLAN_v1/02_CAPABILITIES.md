# 02 — Карта можливостей: що вже є в коді для кожної групи API

**Дата**: 2026-10-02 · **Метод**: читання коду (file:line) трьома незалежними оглядами з вибірковою перевіркою; живі сховища не відкривалися, крім read-only SQLite Chroma

**Готовність**:
- ✅ — чиста функція з тестами, можна обгортати;
- 🟡 — логіка є, але зав'язана на Dash, CLI, Ray або конфіг, треба винести;
- 🟠 — прошита під Paper 3 / Kakhovka або дубльована, потрібен рефакторинг;
- 🔴 — немає, писати;
- ⛔ — блокер у даних (номери О-* з [01_PROJECT_INDEX.md](01_PROJECT_INDEX.md)).

---

## 1. Зведена таблиця

| Група API | Найкраща наявна точка входу | Готовність | Блокери | Сервіс-модуль |
|---|---|---|---|---|
| Ідентичність / «чи є в корпусі» | `tools/paper3_audit/corpus.PaperResolver` (:185) — DOI, назва ≥ 0.95, Chroma-id; `build_runtime_index` (:84): 5 027 статей, 212 927 ребер цитування OpenAlex, 144 групи дублікатів DOI | 🟠 (не закомічено, ~10 конкуруючих реалізацій) | О-3, О-37 | `identity.py` |
| Текст статті | `src/document` TEIParser → `TEIDocument` (розділи, сторінки, координати GROBID); `paper_3/_utils.load_paper_json` (:155); `PaperStore.load` (`paper_store.py:150`) | ✅ / 🟡 | О-3 | `corpus.py` |
| Векторний пошук | `Embedder.embed_query` (`embedding/embedder.py:20-69`) + `VectorStore.query(embedding, top_k, where)` (`vectorstore/chroma_store.py:152-188`); `Retriever.retrieve_many_ranked` (`retrieval/retriever.py:140`); RQS `get_semantic_evidence` (:653-708) — єдиний, що приєднує DOI/назву | 🟡 | ⛔ О-24, О-27, О-39 | `search.py` |
| Граф (читання) | `graph/graph_queries.py`: 10 запитів + `run_query` (:262), 6 з них залежать від порожніх зв'язків; `GraphWriter.reasoning_chain_score` (:595); `data_neo4j` (кешує назавжди, на помилку повертає `[]`) | 🟡 | ⛔ О-1, О-2; О-33 | `graph_read.py` |
| Аналітика (DuckDB/Parquet) | `dashboard_dash/duckdb_manager.query` (:16-63), `data_loader.py` (~30 запитів), RQS `get_metric_distribution` (:334), `get_numeric_fact_evidence` (:711) | 🟡 | ⛔ О-25, О-26; О-33, О-34 | `analytics.py` |
| Метрики з тексту | `EntityExtractor(kb).extract_metrics(text)` (`ingestion/knowledge/entity_extractor.py:323`) → 15 метрик з evidence | ✅ (test_15) | О-7, О-8, О-28, О-29 | `metrics.py` |
| Метрики з таблиць | `extraction/table_extractor.extract_numeric_facts(tei_path, paper_id)` (:566) → `NumericFact` | ✅ (test_16) | О-7, О-8 | `metrics.py` |
| Метрики через LLM | `SectionExtractor.extract_from_paper` (`section_extractor.py:261`), 3 виклики Ollama + перевірка «число є в джерелі» (:143) | 🟡 (без тестів) | О-6 (немає еталону) | `metrics.py` |
| Онтологія | `normalization/ontology_matcher.normalize_entity(raw, expected_type, context)` (:65; батч :196) → `NormalizedEntity` (`schemas/normalized_paper.py:64`) | ✅ | семантичний фолбек BGE-large не потокобезпечний (`embedding_matcher.py:56-67,177-256`) → за замовчуванням `allow_semantic=false` | `ontology.py` |
| Синтез (Gemini) | `ai_gateway.call_ai_with_gateway` (:105-174), `build_research_prompt` (:179-387, правила grounding :200-215), RQS `build_evidence_pack` (:864-918); оркестрація — ~20 рядків у `callbacks.py:1741-1761` | 🟡 | О-27, О-36; немає лімітера, вихідні DOI не перевіряються | `generate.py`, `llm.py` |
| Перевірка цитат | `src/paper_3/evidence.verify_quote(quote, passages)` (:131), `normalize_text` (:55), `build_passages` (:203): NFKC, лігатури, дефіси; exact → ковзне вікно ≥ 0.92 | ✅ (25 тестів) | бракує: DOI+цитата → повний текст; атрибуція | `quotes.py` |
| Адюдикація тверджень | `classify_relation.run` (:257) → `finalize_relations.run` (:182) → `relations.parquet`; `abstract_mode.run` (:285); `tools/paper3_audit/screen.py` (8 ролей; SUPPORTS/CONTRASTS лише з перевіреною цитатою :222-230) | 🟠 | О-36; 4 відношення проти 8 ролей; 9 вердиктів проти 7 статусів | `claims.py` |
| Статуси й ворота | `gap_matrix._assign_verdict_core` (:372), `acceptance.run` (recall ≥ 0.90, :27), `export.derive_status` (R0–R6, :76), `markers` (:135-211) | ✅ (детерміновані, з тестами) | — | `claims.py`, `theses.py` |
| Тези | `src/paper_3/theses.load_theses / validate_theses` (:264/:333) — 36 тез прошито (:25, :68, :75); `tools/paper3_audit/claims.py` (`atomic_claims.yaml`) | 🟠 | О-13 (мовчазне приведення типів) | `theses.py` |
| Витяг тез LLM-ом | **немає**: тези пишуться вручну; найближче — regex-парсери (`draft_anchors.parse_*`, `v2/claim_map.extract_claim_refs` :42) | 🔴 | — | `theses.py` |
| Рукописи | `v2/assemble.assemble` (:491; `{{claim:…}}`, `[PENDING]`), `v2/translate.translate_text` (:117, токени ⟦i⟧ для чисел і DOI), `docx_build.build` (:71), `revise.apply` (:40), `rebase` (:28), `checks.check_C` | 🟠 (`TABLE_SPECS` під Paper 1, `--paper 1\|2`) | у floodstate-eo інша граматика плейсхолдерів (`{{T|…}}`, `[[MISSING]]`) | (фаза 4, опційно) |
| DOI і бібліографія | `tools/paper3_audit/references.verify_all` (:140): назва ≥ 0.90, рік ±1, автори **не** порівнюються, падає на «2024a»; `bib_delta.build` (:26); `v2/reference_registry.run` (:287; транслітерація кириличних ключів); схема `briefs/technical_sources.yaml` для джерел без DOI | 🟠 | О-38; невдачі кешуються назавжди | `doi.py` |
| Пошук нових статей | `harvest_queries.build_query_set` (:132) + `harvest_openalex.discover` (:312); `p2_literature.expand` (:227) — snowball; **`briefs/geodesy.py:773`** — найкращий шаблон: заморожений хешований запит → discover → download → GROBID → text → screen; `openalex_actor.search_works/verify_doi/verify_title` | 🟠 (прив'язано до `theses.yaml`) | О-25 (список відсутніх посилань), О-37 | `discovery.py` |
| Завантаження PDF | **`src/paper_3/oa_resolver`** (:177 `fetch_pdf`, :210 каскад Unpaywall → Europe PMC → OpenAlex → шаблони видавців: MDPI, Copernicus, Frontiers, arXiv, відкритий Wiley); `briefs/geodesy` (sha256-контроль); `MANUAL_DOWNLOAD_LIST.csv` для paywall | 🟡 | О-35 (Sci-Hub — **не** загортати), ліцензія не записується | `acquisition.py` |
| Інжест однієї статті | ланцюг описано в [05_PIPELINES.md](05_PIPELINES.md); шаблон — `src/paper_3/harvest_ingest.py` (subprocess-кроки, :69-80); ядро `build_paper_json` (`ingestion/pipeline.py:169`) | 🟡/🔴 (`process_paper` лише як Ray-задача; граф — лише повна перебудова) | О-1, О-2, О-9, О-24, О-31 | `ingestion.py` |
| Свої дані (own evidence) | `own_evidence.run` (:276), `snapshot.pull` (через `wsl.exe`), `derived.build` (:404), `figures.build` (:572) | 🟠 | О-14 | поза v1; лише `/bundles/import` |
| Реєстр пайплайну | `registry/registry_db.PipelineRegistry` (:196): `register_paper` :311, `start_processing` :363, `mark_success`/`mark_failure` :441/:498, `get_status` :1154 — пише лише стадія GROBID (3 724 рядки, останні 2026-05-14) | 🟡 | без тестів | `jobs/` + реєстр |

---

## 2. Що вже правильно і варто зберегти дослівно

1. **Квота і лейни.** `screen.Quota` / `quota_ledger.<lane>.json` (≤ 480 запитів на день на лейн). Переноситься в SQLite-ledger LLM-шлюзу.
2. **Перевірка цитат.** `verify_quote`: нормалізація й поріг 0.92 вже відкалібровані на реальних статтях. Сервіс `quotes.py` додає лише завантаження повного тексту (TEI → розділи) та атрибуцію (`<ref type="bibr">` усередині знайденого речення).
3. **Детерміновані вердикти й ворота.** `_assign_verdict_core`, `acceptance.run`, `derive_status`. Це код, який перетворює правила L-серії на поведінку API (відповідь «не вище `RETRIEVAL_UNVALIDATED`»).
4. **Перекладач з блокуванням токенів.** `translate.translate_text`: числа, DOI і ключі не проходять через модель. Той самий прийом потрібен у `generate.py`.
5. **OA-каскад** `oa_resolver` і sha256-контроль з `briefs/geodesy`.
6. **Схема `technical_sources`** для джерел без DOI: стандарти, місійні документи, реєстри.
7. **Grounding-правила промпта** `build_research_prompt` (:200-215): лише докази, фіксована фраза «доказів недостатньо», `[doi:…]`, розділи PROVENANCE і LIMITATIONS.

---

## 3. Дублікати, які API має звести в один модуль

| Що дублюється | Скільки копій (приклади) | Куди зводимо |
|---|---|---|
| Нормалізація DOI | 6: `paper_3/_utils.py:55`, `paper_audit/_utils.py:23`, `openalex_enrichment.py:53`, `openalex_actor.py:307`, `tools/paper3_audit/corpus.py:39`, `build_candidates.py:29` | `identity.normalize_doi` |
| DOI → ім'я файлу (slug) | 4 конвенції, що не збігаються: `/` → `_` лише (`paper_3/_utils.py:71`); також `:` (`recover_missing.py:61`); усі небуквені (Sci-Hub-шлях); `@` (файли, покладені вручну) | `identity.doi_slug` + таблиця псевдонімів |
| «Чи є в корпусі» | ~10 (`harvest_openalex.py:219`, `PaperResolver`, `paper_audit/doi_resolver`, `abstract_mode.py:250`, `missing_reference_recovery.py:288`, `scripts/revise_paper.py:572` з урахуванням регістру …) | `identity.resolve` |
| Клієнти CrossRef / OpenAlex | 5 / 8+; 5 копій OA-завантаження PDF; 3 декодери інвертованих анотацій OpenAlex | `doi.py` + `discovery.py` + `acquisition.py` на спільному HTTP-шарі (кеш з TTL, backoff, 429, `mailto` з конфігу) |
| Виклики Gemini | 4: шлюз напряму (`classify_relation.py:193`, `translate.py:94`); лейн через перезапис `MODEL_POOL` (`screen.py:289`); власний клієнт на `GOOGLE_API_KEY` (`paper_audit/_utils.py:59`); `paper_my` (+ claude-haiku-4-5) | `llm.py` (один шлюз, модель — аргумент, не глобальна змінна) |
| Схеми тез | 3: `theses.yaml` (Paper 3), `theses.json` + `atomic_claims.yaml` (бандли), `theses_v4.json` (Article 1) | контракт `Thesis` + `AtomicClaim` (07 §6) |
| Відношення / ролі / вердикти | 4 відношення проти 8 ролей; 9 вердиктів проти 7 статусів; два шляхи overrides (`finalize_relations.py:139`, `export.py:144`) | один enum ролей + таблиця відповідності |
| BibTeX | 2 парсери тут + 3 у floodstate-eo; 3+ райтери (`bibtex.py`, `reference_registry`, `paper_my/augment_pipeline.py:170`, `paper_my/app/export_utils.py:43`) | `doi.py` (`/bib/*`) |
| Пороги збігу назв | 0.85 / 0.90 / 0.95 і fuzzy 80 / 88 | один поріг у конфігу + звіт про калібрування |
| Інфраструктура прогонів | 3 хелпери «пропусти, якщо вихід є» (`paper_3/cli.py:45`, `paper_audit/cli.py:34`, `tools/paper3_audit/cli.py:27`); 5 райтерів run-manifest | `jobs/` (кроки з `input_hash`) + один `Provenance` |
| Docx | 2: `build_docx.convert_markdown` (:159), `docx_build.build` (:71) | один, у фазі 4 |
| Чанкери, Neo4j-доступ, нормалізатори метрик | 2 / 4 / 3 | `document/chunker.py`; `src/graph` + `graph_read.py`; `metric_ontology` |

---

## 4. Що треба написати з нуля

| Що | Навіщо | Де |
|---|---|---|
| Таблиця ідентичності + міграція `paper_id` | основа всього (03 §1.6, 04 §5) | `identity.py`, задача `integrity_check` |
| Функція фільтрованого пошуку з join на метадані | у Chroma немає DOI, року, назви: префільтр через ідентичність/DuckDB → `$in` | `search.py` |
| `process_paper_local(xml, ner_fn, encode_fn, judge_fn, vector_sink)` | інжест без Ray; Ray-задача лишається адаптером | `ingestion.py` |
| `write_paper_subgraph(paper_id, doc, tei)` | інкрементальний граф замість повної перебудови; «підвищення» вузла-заглушки до повної статті | `src/graph` |
| `VectorStore.delete(paper_id)` і детерміновані ID чанків | повторний інжест без чанків-сиріт | `chroma_store.py`, `chunker.py` |
| Перевірка цитати за DOI у повному тексті + атрибуція | закриває ручну роботу з `open_citations` | `quotes.py` |
| LLM-витяг тез / атомарних тверджень | зараз тези пишуться вручну | `theses.py` |
| Перевірка DOI у згенерованому тексті проти evidence pack | `ai_gateway` цього не робить | `generate.py` |
| Job store, worker, API-ключі, ліміти, quota ledger | нічого з цього немає | `src/jobs/`, `src/api/` |
| Pydantic-контракти відповідей | є лише `schemas/normalized_paper.py` | `services/models.py` |
