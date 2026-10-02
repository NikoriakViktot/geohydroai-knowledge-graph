# 11 — PostgreSQL як шар правди, контракти, кінець тек «під статтю»

**Дата**: 2026-10-02 · **Стан**: інфраструктура піднята (`ghai-postgres`, PostgreSQL 17.11 + pgvector 0.8.7, `127.0.0.1:5433`); схема `core` / `ops` — перша міграція; решта схем — за цим документом.

---

## 1. Що змінюється

**Було**: кожна стаття (Paper 1, 2, 3, Article 1) отримувала в цьому репозиторії власні теки:
- `paper_unet-case-kakhovka/`, `paper_terrain-case-kakhovka/`, `paper_3_audit/`;
- `data/paper_*_audit/`, `paper_my/`;
- скрипти `scripts/paper1_*`, `src/paper_3/*`, `tools/paper3_audit/*`.

Дані жили у YAML/CSV/Parquet/JSON без схем, тягнулися з іншого дистрибутива через `wsl.exe` і копіювались назад вручну.

**Стає**: репозиторій — це **сервіс**:
- **PostgreSQL — єдине джерело правди** для всього, що вирішено, перевірено або розмічено.
- Neo4j, Chroma, Parquet і файли для споживачів — **похідні проєкції**, які перебудовуються з Postgres + файлового сховища.
- Репозиторії статей звертаються через API. Їхні тези, рукописи та бібліографії приходять як **валідовані за контрактом** запити, а не як дзеркала тек.
- Нових тек під статті в цьому репозиторії не з'являється.

```text
            репозиторії статей (floodstate-eo, SWOT-DNIPRO, kakhovka-terrain) / Claude Code (MCP)
                                   │  JSON за контрактами (src/contracts → contracts/schemas/*.json)
                                   ▼
                              FastAPI (src/api) ── src/services
                                   │ читає/пише (лише worker пише)
                                   ▼
   ┌──────────────── PostgreSQL: ШАР ПРАВДИ ───────────────────────────────────────────┐
   │ core     — статті, псевдоніми, файли, отримання, ліцензії, когорти, стан проєкцій   │
   │ biblio   — бібліографічні записи, перевірки DOI, ключі цитування, не-DOI джерела    │
   │ project  — простори імен споживачів, рукописи, тези, атомарні твердження, цитування │
   │ evidence — докази, мітки скринінгу, перевірки цитат/тверджень, ворота, вердикти     │
   │ ops      — прогони/провенанс, імпортовані файли, задачі, ключі API, журнал LLM      │
   └───────────────────────────────────────────────────────────────────────────────────┘
          │ проєкції (перебудовуються, ніколи не є джерелом правди)
          ├──► Neo4j (граф)   ├──► Chroma (вектори)   ├──► data/parquet (аналітика)   └──► експорти для споживачів
   файлове сховище (blob): PDF / TEI / SODB — посилання з core.paper_file за sha256
```

---

## 2. Принципи

1. **Одне джерело.** Якщо факт є в Postgres, будь-яка інша копія (YAML у `src/paper_3`, CSV у теці статті, вузол у Neo4j) — похідна. Розбіжність означає, що проєкцію треба перебудувати.
2. **Контракт перед даними.** Кожен тип запису має pydantic-модель у `src/contracts/` і експортовану JSON Schema у `contracts/schemas/<name>.v<major>.json`. Імпорт **валідує й відмовляє** (`422`), а не приводить типи мовчки. Урок О-13: CSV-дамп тез з kakhovka-terrain мовчки перетворювався на 107 односимвольних рядків.
3. **Провенанс у кожному рядку**: `run_id` (→ `ops.run`: коміт, параметри, час), `source` (файл + sha256 або API-запит), а для міток — `labeler_kind` ∈ {`human`, `model`, `rule`, `import`} + `labeler` (назва моделі або роль людини). Це закриває плутанину на кшталт `human_verified=yes` з `verified_by=Claude`.
4. **Простори імен** (рішення автора 2026-10-02). Усе, що належить статті чи звіту, ключується `project_id`:
   - `floodstate-eo:paper3` — U-Net/затоплення;
   - `kakhovka-terrain:paper2` — дно, рельєф, шорсткість; сюди ж виходи рукопису про рослинність/шорсткість (Paper 4);
   - `swot-dnipro:paper1` — геометрія водної поверхні;
   - `kakhovka-report:v1` — ранній науковий звіт по Каховці: `src/paper_3`, `data/paper_3_audit`, `paper_3_audit/`. Це **не** Paper 3 з floodstate-eo;
   - `article1` — новий окремий репозиторій для Article 1 (`paper_my/`, `src/paper_audit/`, `data/paper_audit/`).

   Тези `TH-INT-01` різних статей більше не зіштовхуються.
5. **Історія без видалень.** Перевірки й вердикти лише додаються: кожен новий результат — новий рядок, а поточний стан — це view `*_current`. Ворота й рукописи прив'язуються до знімка, тож старий результат не перезаписується тихо.
6. **Ідентичність статті одна** (`core.paper.paper_id`). Решта (DOI, slug, імена файлів, ID Chroma й OpenAlex, sha256) — це `core.paper_alias`. Рішення v1: `paper_id` = **наявний stem файлу**, тобто стабільний ключ, на який уже спираються 5 тис. файлів, Chroma й Neo4j. Перейменування файлів не потрібне.
7. **Проєкції знають, з чого їх збудовано.** `core.projection_state` (сховище, `run_id`, кількість, час) для Neo4j, Chroma, Parquet. API показує це в `provenance`.

---

## 3. Схеми й таблиці

Позначка ✅ — у міграції `0001` (створено). Решта йде наступними міграціями в порядку §5.

### core — статті та файли

| Таблиця | Ключ | Головні колонки | Звідки дані |
|---|---|---|---|
| ✅ `core.paper` | `paper_id` | `doi` (нормалізований, унікальний серед канонічних), `title`, `year`, `venue`, `language`, `identity_status` (`ok` · `no_doi` · `title_doi_mismatch` · `duplicate` · `not_a_paper` · `truncated_json`), `duplicate_of`, `openalex_id` | paper_json, enriched (OpenAlex), normalized |
| ✅ `core.paper_alias` | (`alias_type`, `alias`) | `paper_id`, `source` | DOI, slug-конвенції (`/`→`_`; `/`,`:`→`_`), імена файлів, ID OpenAlex/Chroma, sha256 PDF |
| ✅ `core.paper_file` | (`paper_id`, `kind`, `path`) | `kind` (`pdf`, `tei`, `paper_json`, `normalized`, `enriched`, `sodb`, `nougat_regions`), `sha256`, `size_bytes`, `status` (`ok`, `truncated`, `duplicate_copy`) | файлова система + маніфест sha256 від 2026-10-02 |
| ✅ `core.cohort_member` | (`cohort`, `paper_id`) | `source` | `cohort_paper_3.csv` (225 DOI) |
| ✅ `core.projection_state` | (`store`, `name`) | `run_id`, `item_count`, `built_at`, `notes` | Neo4j, Chroma v1/v2, Parquet |
| `core.acquisition` | `id` | `paper_id`/`doi`, `route` (unpaywall · europepmc · openalex · publisher · arxiv · wayback_oa · manual_upload · legacy_unknown), `url`, `oa_status`, `licence`, `version`, `sha256`, `failure_reason` | `MANUAL_DOWNLOAD_LIST.csv`, логи `recover_missing`, нові задачі |
| `core.chunk` (фаза 2) | `chunk_id` | `paper_id`, `chunk_type`, `section`, `page`, `bbox`, `text`, `text_sha1` | чанкер. Chroma зберігає лише вектор + `chunk_id`. Фільтри за DOI/роком — join у Postgres (О-27) |

### biblio — бібліографія

| Таблиця | Ключ | Головні колонки | Звідки |
|---|---|---|---|
| `biblio.work` | `work_id` | `doi`, `title`, `authors` (jsonb), `year_online`, `year_print`, `venue`, `volume`, `issue`, `pages`, `type`, `paper_id` (якщо в корпусі) | Crossref/OpenAlex-кеші, `07_references_verified.bib`, `REFERENCES.csv` |
| `biblio.verification` | `id` | `work_id`, `checked_against` (crossref · openalex · datacite · fulltext · human), `verdict` (`verified` · `verified_with_notes` · `mismatch` · `unresolved`), `diffs` (jsonb), `checked_at`, `labeler_kind`, `labeler`, `run_id` | bib-нотатки «Crossref-verified», 07/07b, `citation_verification.md` |
| `biblio.cite_key` | (`project_id`, `key`) | `work_id`, `canonical_key`, `aliases` | `citation_keys.yaml`, `references.bib` споживачів (`Roberts_2017` ↔ `Roberts_2017_blockCV`) |
| `biblio.technical_source` | `source_id` | `cite_as`, `source_type` (стандарт, місія, реєстр, датасет), `url`, `url_sha256`, `status` | `src/paper_3/briefs/technical_sources.yaml` |
| `biblio.http_cache` ✅ (0004) | (`service`, `key`) | `response` (jsonb), `status` (200/404/410), `url` без секретів, `fetched_at`, `expires_at` | `_work/{crossref,openalex,url}_cache.json`, `data/cache/*.db`. Таймаути **не** кешуються як «не знайдено» |

### project — простори імен статей

| Таблиця | Ключ | Головні колонки |
|---|---|---|
| `project.project` | `project_id` | `repo`, `paper_label`, `title`, `status` |
| `project.manuscript` | (`project_id`, `version`) | `sha256`, `submitted_at`, `source` (API upload) — **текст лишається в репозиторії статті**; тут лише версія та її хеш |
| `project.thesis` | (`project_id`, `thesis_id`) | `section`, `category`, `priority`, `text`, `quantitative`, `needs`, `search_queries` (text[]), `status` |
| `project.atomic_claim` | (`project_id`, `atomic_id`) | `thesis_id`, `statement`, `required_roles` (text[]), `manuscript_relevance`, `key_terms_primary`, `key_terms_support` (jsonb), `negative_terms`, `extra_queries`, `counterevidence_queries` |
| `project.citation_occurrence` | `id` | `project_id`, `manuscript_version`, `section`, `sentence`, `cite_key`, `quoted_text`, `work_id` |
| `project.positive_control` | (`project_id`, `thesis_id`, `paper_id`) | `role` (dev/holdout), `relevance`, `expected_stage`, `difficulty`, `checked_by`, `evidence` |
| `project.own_claim` | (`project_id`, `claim_id`) | **лише статус і посилання**. Числа власних результатів живуть у репозиторії статті (це її дані, а не дані корпусу) |

### evidence — докази, мітки, вердикти

| Таблиця | Ключ | Головні колонки |
|---|---|---|
| `evidence.span` | `span_id` | `paper_id`, `chunk_id`, `section`, `page`, `char_start`/`char_end`, `text`, `text_sha1` |
| `evidence.screening_label` | `id` | `project_id`, `atomic_id`/`thesis_id`, `paper_id`, `role` (SUPPORTS · CONTRASTS · METHOD · ANALOGUE · BACKGROUND · NOT_RELEVANT), `quote`, `quote_verified`, `rationale`, `labeler_kind`, `labeler`, `prompt_sha256`, `run_id` |
| `evidence.quote_check` | `id` | `work_id`/`paper_id`, `quote`, `status` (FOUND_EXACT · FOUND_NORMALIZED · FOUND_FUZZY · NOT_FOUND · SOURCE_UNAVAILABLE), `score`, `span_id`, `attribution` (jsonb), `checked_at`, `run_id` |
| `evidence.claim_check` | `id` | `project_id`, `occurrence_id`, `work_id`, `verdict` (SUPPORTED · PARTIALLY_SUPPORTED · OVERSTATED · CONTRADICTED · NOT_FOUND_IN_SOURCE · SOURCE_UNAVAILABLE), `evidence_span_ids`, `explanation`, `suggested_rewrite`, `labeler_kind`, `labeler`, `run_id` |
| `evidence.gate_state` | (`project_id`, `snapshot_id`) | `criteria` (jsonb), `passed`, `recall`, `computed_at` |
| `evidence.novelty_verdict` | (`project_id`, `question_id`, `snapshot_id`) | `verdict`, `denominator`, `closest_papers`, `statement` |

### ops — провенанс і експлуатація

| Таблиця | Ключ | Головні колонки |
|---|---|---|
| ✅ `ops.run` | `run_id` (uuid) | `kind` (etl · ingest · reindex · graph_build · audit · api_job), `started_at`, `finished_at`, `status`, `git_commit`, `git_dirty`, `params` (jsonb), `counts` (jsonb) |
| ✅ `ops.source_file` | (`sha256`, `path`) | `run_id`, `size_bytes`, `imported_at`. Кожен імпортований файл з тек статей (провенанс ETL) |
| `ops.job`, `ops.job_step`, `ops.job_event` | — | задачі API ([03 §5](03_API_DESIGN.md)); замість `jobs.sqlite` з першого варіанту 04 |
| `ops.api_key` | `key_id` | `consumer`, `scopes`, `key_hash`, `created_at`, `revoked_at` |
| `ops.llm_call` | `id` | `provider`, `model`, `prompt_sha256`, `tokens`, `cached`, `status`, `called_at`, `run_id`. Замінює `quota_ledger.*.json`; квота = агрегат за вікно |

---

## 4. Контракти

- `src/contracts/*.py` — pydantic v2 моделі. Це **контракт API та імпорту**. Таблиця Postgres може мати більше колонок (провенанс), але не менше.
- `python -m src.contracts.export` → `contracts/schemas/<Name>.v1.json` (комітиться). Репозиторій статті валідує свій файл ще до відправки, навіть без Python-залежностей.
- Версії: зміна, що ламає контракт, означає `.v2.json` і нову версію API (`/v2`). Сумісне розширення лишається в межах `.v1`.
- Перші контракти: `PaperIdentity`, `PaperFile`, `CohortMember` (готові разом з міграцією 0001). Далі `Thesis`, `AtomicClaim`, `BibEntry`, `CitationOccurrence`, `QuoteCheck`, `ClaimCheck`, `ScreeningLabel`, `GraphBundle` — разом з міграціями 0002–0004.

---

## 5. Порядок робіт

| Крок | Що | Готово, коли |
|---|---|---|
| P0 ✅ | контейнер `ghai-postgres` (PG 17 + pgvector), `GHAI_PG_*` у `.env`, compose з виправленим томом Neo4j | `pg_isready`; `docker compose config` OK |
| P1 ✅ | міграція `0001` (`core`, `ops`) + контракти ідентичності + ETL ідентичності з файлів | кожна з ~5 038 статей має рядок; статуси дублікатів, `not_a_paper`, обрізаних JSON; звіт |
| P2 ✅ (без HTTP-кешів) | `biblio` у міграції `0003` (`0002` — ключі API) + імпорт `references_verified.csv`, `REFERENCES.csv`, `citation_keys.yaml`, `technical_sources.yaml`, `method_references`, результатів `citation_verification.md` | перевірки DOI з'являються як рядки `biblio.verification` |
| P3 ✅ (без воріт і журналу LLM) | `0003` `project` + `evidence` + імпорт тек статей (`src/etl/paper_folders.py`, 26 джерел): тези, атомарні твердження, контролі, мітки скринінгу з `labeler_kind`, `02_thesis_evidence`, вердикти новизни, overrides, числа, черга ручного завантаження | кожен файл тек статей записаний в `ops.source_file` з sha256, а кожен рядок має провенанс |
| P4 | `0004` `ops.job` / `api_key` / `llm_call` — основа для API (фази 1–2 [06](06_ROADMAP.md)) | API пише задачі в Postgres |
| P5 | проєкції з Postgres: граф (`build_graph` читає `core.paper` замість застарілого `data/analytics`), Chroma (ID статей з `core.paper`), Parquet-експорти | `core.projection_state` заповнений |
| P6 | **вивести з репозиторію теки статей**: виходи статей → у репозиторії споживачів; дані правди вже в Postgres; решта → архів `data/frozen/` | нових тек `paper_*` немає; `src/paper_3/*.yaml` і `tools/paper3_audit` читають з API або з Postgres |

---

## 6. Що лишається у файлах (і чому)

- **PDF, TEI, SODB** — великі бінарні й напівструктуровані артефакти. Вони лишаються у файловому сховищі, адресованому через sha256. Postgres зберігає їхні метадані й шляхи, а не вміст.
- **Вектори** — у Chroma (v1). pgvector уже встановлений: перенести вектори в Postgres (`core.chunk.embedding vector(768)`) можна пізніше, якщо захочеться одного сховища. Ціна — ≈ 8–10 GB і години побудови HNSW.
- **Числа власних результатів статей** (`OWN_EVIDENCE`, таблиці Paper 1) — дані відповідного дослідження. Їх джерело правди — репозиторій статті. API отримує лише статус тверджень і посилання на них.
