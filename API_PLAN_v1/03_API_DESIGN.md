# 03 — Дизайн API v1 (GeoHydroAI Knowledge API)

**Дата**: 2026-10-02 · **Статус**: проєкт, не реалізовано · **Base URL**: `http://127.0.0.1:8090/v1`

Цей документ описує **контракт**: принципи, конвенції, каталог ендпоінтів, ключові схеми, модель задач.

**Нормативний довідник кожного ендпоінта** (запит, відповідь, помилки, приклади, нотатки для агентів) — [`docs/api/`](../docs/api/README.md). **Правила для ІІ-агентів** — [`docs/api/AGENT_RULES.md`](../docs/api/AGENT_RULES.md). Цей файл лишається обґрунтуванням дизайну.
Де саме лежить код, який кожен ендпоінт обгортає, описано в [02_CAPABILITIES.md](02_CAPABILITIES.md). Процеси й сховища описано в [04_ARCHITECTURE.md](04_ARCHITECTURE.md).

---

## 1. Принципи (не обговорюються під час реалізації)

1. **Спочатку сервісний шар, потім API.** Уся логіка живе в `src/services/`: чистий Python і pydantic-моделі, без імпортів `fastapi`/`dash`/`ray`. Роутери FastAPI лише валідують запит і викликають сервіс. Ті самі сервіси викликають CLI, Dash і MCP. Інакше з'явиться четверта копія вже наявних трьох реалізацій Crossref/bib.
2. **Читання синхронне, будь-який запис іде через задачу.** Операція, що змінює Neo4j, ChromaDB, файли корпусу або реєстр, а також будь-яка операція довша за ~20 с, повертає `202 Accepted` + `job_id`. API-процес нічого не пише в сховища: пише лише worker (див. 04 §3).
3. **Провенанс у кожній відповіді.** Блок `provenance` = `{api_version, git_commit, corpus_manifest_id, collection, embedding_model, kb_version, llm?, generated_at}`. Споживач кладе його у свій run-manifest. Без цього результат пошуку не можна відтворити.
4. **Відсутність результату не означає прогалину в літературі.** Будь-яка відповідь пошуку містить `coverage` (розмір зрізу, знаменники) і `retrieval_validity` ∈ {`VALIDATED`, `UNVALIDATED`, `NOT_MEASURED`}. Статус `CANDIDATE_GAP` API **не видає**, поки ворота прийняття (positive controls, recall ≥ 90 %) не пройдені. Це правила L-серії §7.8, перенесені в код.
5. **LLM працює лише поверх доказів.** Генерація отримує або сама формує набір evidence (`chunk_id`, `doi`, `page`). Кожне речення відповіді посилається на evidence з цього набору. Речення без опори відкидаються або повертаються з позначкою `unsupported`. Метрики не можна виводити з тематичних слів.
6. **Одна ідентичність статті.** `paper_id` = doi-slug (`10.1029_2025gl120832`) або `sha256:<12 hex>`, якщо DOI немає. Таблиця ідентичності (04 §5) зв'язує DOI ↔ PDF/TEI/paper.json ↔ чанки Chroma ↔ вузол Neo4j. Зараз ці імена різнорідні: `Water Resources Research - 2025 - Yi - ….tei.xml`.
7. **Версіонування.** Шлях `/v1/...`. Ломкі зміни йдуть у `/v2`. Згенерована OpenAPI-схема є контрактом, і клієнт генерується з неї.
8. **Тільки localhost і ключі зі скоупами.** Зовнішній доступ поза машиною не передбачено (04 §8).

---

## 2. Загальні конвенції

| Аспект | Рішення |
|---|---|
| Транспорт | HTTP/1.1 JSON; upload — `multipart/form-data`; прогрес задач — SSE `text/event-stream` |
| Автентифікація | заголовок `X-API-Key`; ключ прив'язаний до споживача (`floodstate-eo`, `swot-dnipro`, `claude-mcp`, `dashboard`) |
| Скоупи | `read` (усі GET/пошук) · `llm` (витрачає квоту Gemini/Ollama) · `write` (задачі, що змінюють сховища) · `admin` (rebuild, сирий Cypher, заморожування маніфесту) |
| Пагінація | `limit` (≤ 200, дефолт 20) + непрозорий `cursor` |
| Помилки | RFC 9457 `application/problem+json`: `{type, title, status, detail, code, instance}` |
| Коди помилок | `NOT_IN_CORPUS` 404 · `SOURCE_UNAVAILABLE` 424 · `QUOTA_EXHAUSTED` 429 + `Retry-After` · `STORE_UNAVAILABLE` 503 · `GATE_NOT_PASSED` 409 · `INVALID_DOI` 422 |
| Ідемпотентність | заголовок `Idempotency-Key` для POST-задач + природні ключі (нормалізований DOI, SHA-256 PDF). Повтор повертає наявну задачу (`200`, `"duplicate_of"`) |
| Ліміти | на ключ: 20 rps для `read`; LLM-ендпоінти обмежує спільний quota ledger (RPM/RPD на модель, 04 §6) |
| Таймаути | синхронні ендпоінти ≤ 30 с; довші операції — задачі |
| Мова даних | поля англійською; повідомлення `detail` англійською (споживачі — скрипти й агенти) |

---

## 3. Каталог ендпоінтів

Позначки: **S** — синхронний; **J** — повертає задачу (`202` + `job_id`).

### 3.1 Система

| Метод | Шлях | Скоуп | Тип | Призначення |
|---|---|---|---|---|
| GET | `/health` | — | S | liveness + стан залежностей (`neo4j`, `chroma`, `grobid`, `ollama`, `gemini_quota`) |
| GET | `/stats` | read | S | лічильники корпусу (PDF/TEI/paper.json/normalized/enriched/chunks/nodes), дата останнього інжесту |
| GET | `/manifest` | read | S | поточний corpus manifest: id, git commit, правила ретривалу, модель ембедингів, колекція, кількість статей, hash |
| POST | `/admin/manifest/freeze` | admin | S | заморозити маніфест (для відтворюваного прогону статті) |
| GET | `/capabilities` | — | S | які групи ендпоінтів увімкнені (напр. `llm=false`, якщо немає ключа Gemini) |
| GET | `/llms.txt` | — | S | компактний машинний індекс API для мовних моделей ([docs/api/llms.txt](../docs/api/llms.txt)) |

### 3.2 Корпус і статті

| Метод | Шлях | Скоуп | Тип | Призначення |
|---|---|---|---|---|
| GET | `/papers/resolve?doi=…\|title=…\|file=…` | read | S | знайти статтю в корпусі → `PaperIdentity` (прапорці: pdf/tei/json/normalized/enriched/chroma/neo4j) |
| GET | `/papers/{paper_id}` | read | S | метадані + збагачення OpenAlex (цитування, автори, концепти) |
| GET | `/papers/{paper_id}/sections` | read | S | структура TEI: розділи, сторінки |
| GET | `/papers/{paper_id}/text?section=&page=&q=` | read | S | текст розділу/сторінки зі зміщеннями й координатами GROBID |
| GET | `/papers/{paper_id}/references` | read | S | список літератури статті + розв'язані DOI + `in_corpus` |
| GET | `/papers/{paper_id}/entities` | read | S | методи/сенсори/метрики/гео з normalized JSON (canonical_id) |
| GET | `/papers/{paper_id}/tables` | read | S | таблиці TEI + NumericFact з цієї статті |

### 3.3 Пошук (ChromaDB, SPECTER2 768-d)

| Метод | Шлях | Скоуп | Тип | Призначення |
|---|---|---|---|---|
| POST | `/search/chunks` | read | S | семантичний пошук чанків з фільтрами |
| POST | `/search/papers` | read | S | те саме, агреговане по статтях (max/mean скору, кількість влучань) |
| POST | `/search/similar` | read | S | статті, близькі до заданої DOI/paper_id |
| POST | `/search/hybrid` | read | S | вектор + лексичний (BM25/FTS) + розширення за цитуваннями (фаза 3) |

### 3.4 Граф (Neo4j, тільки читання)

| Метод | Шлях | Скоуп | Тип | Призначення |
|---|---|---|---|---|
| GET | `/graph/papers/{doi}` | read | S | вузол Paper + сусіди (автори, установи, теми, методи, сенсори, метрики, країни) |
| GET | `/graph/papers/{doi}/citations?direction=in\|out` | read | S | цитування; `in_corpus` для кожної |
| GET | `/graph/entities/{label}/{canonical_id}/papers` | read | S | статті, що використовують метод/сенсор/метрику |
| GET | `/graph/queries` | read | S | каталог іменованих запитів |
| POST | `/graph/queries/{name}` | read | S | виконати іменований параметризований запит (read-only транзакція, таймаут, LIMIT) |
| POST | `/graph/cypher` | admin | S | сирий Cypher лише в READ-режимі драйвера, таймаут 10 с, ≤ 1000 рядків |

### 3.5 Метрики й онтологія

| Метод | Шлях | Скоуп | Тип | Призначення |
|---|---|---|---|---|
| GET | `/metrics/facts?metric=&min=&max=&sensor=&task=&paper_id=` | read | S | NumericFact/MetricFact з evidence-фрагментами |
| POST | `/metrics/extract` | read | S | regex + таблиці з поданого тексту/TEI/paper_id; діапазони онтології, `suspect`-прапорці; **без LLM** |
| POST | `/metrics/extract?mode=llm` | llm | J | дворівнева LLM-екстракція (PaperStore + `extract_from_paper`) |
| GET | `/metrics/ontology` | read | S | визначення метрик і допустимі діапазони (NSE/KGE ≤ 1, kappa −1..1, OA/F1/IoU 0..1) |
| POST | `/ontology/normalize` | read | S | терміни → `canonical_id` + тип + тір збігу (exact/alias/embedding) |
| GET | `/ontology/entities?type=&q=` | read | S | пошук сутностей онтології (1 118 сутностей, 3 161 аліас) |

### 3.6 DOI і бібліографія

| Метод | Шлях | Скоуп | Тип | Призначення |
|---|---|---|---|---|
| GET | `/doi/{doi}` | read | S | метадані з Crossref + OpenAlex (кеш), джерело кожного поля, OA-статус |
| POST | `/doi/verify` | read | S/J | перевірка bib-записів поле за полем (≤ 50 синхронно, більше — задача) |
| POST | `/bib/format` | read | S | DOI → BibTeX з конвенцією ключів `Surname_Year` (`Lehnigk_2026`) |
| POST | `/bib/audit` | read | J | повний аудит `.bib`: DOI, дублікати, колізії ключів, невирішені записи |

### 3.7 Цитати, твердження, тези

| Метод | Шлях | Скоуп | Тип | Призначення |
|---|---|---|---|---|
| POST | `/quotes/verify` | read | S/J | знайти цитату (і числа) в повному тексті джерела; без LLM |
| POST | `/claims/check` | llm | S/J | чи підтримує джерело твердження рукопису (SUPPORTED / OVERSTATED / CONTRADICTED …) |
| POST | `/theses/extract` | llm | J | рукопис → атомарні твердження (схема `atomic_claims.yaml`) |
| POST | `/theses/evidence` | read | S | для набору тез: доказові фрагменти з корпусу + цитовані DOI |
| POST | `/theses/novelty` | llm | J | оцінка новизни з воротами: без пройдених воріт статус не вище `RETRIEVAL_UNVALIDATED` |
| GET | `/theses/sets/{set_id}` | read | S | стан набору тез і вердикти |

### 3.8 Генерація тексту (поверх доказів)

| Метод | Шлях | Скоуп | Тип | Призначення |
|---|---|---|---|---|
| POST | `/generate/synthesis` | llm | S/J | відповідь на питання з посиланнями на речення-evidence (Dash ai_gateway як сервіс) |
| POST | `/generate/related-work` | llm | J | чернетка абзацу related work для набору тез + таблиця доказів + BibTeX |
| POST | `/generate/rewrite-check` | llm | S | перевірити, чи переписаний абзац не посилив твердження джерела |

### 3.9 Пошук нових статей, завантаження, інжест

| Метод | Шлях | Скоуп | Тип | Призначення |
|---|---|---|---|---|
| POST | `/discovery/search` | read | S/J | OpenAlex/Crossref/arXiv за запитом і фільтрами → кандидати з `in_corpus`, OA-статусом, релевантністю |
| POST | `/discovery/snowball` | read | J | цитування назад і вперед від seed-DOI (глибина 1–2, поріг цитувань) |
| POST | `/discovery/screen` | llm | J | скринінг кандидатів: SPECTER2-схожість + опційно LLM (лейни Gemini з квотою) |
| GET | `/discovery/candidates?status=` | read | S | черга рецензування кандидатів |
| PATCH | `/discovery/candidates/{id}` | write | S | accept / reject / needs_manual |
| POST | `/acquire` | write | J | знайти й завантажити OA-PDF (OpenAlex `best_oa_location`, Unpaywall, arXiv, PMC) |
| POST | `/ingest/upload` | write | J | завантажити власний PDF (multipart, + опційно DOI) і запустити інжест |
| POST | `/ingest` | write | J | повний пайплайн для DOI/paper_id: кроки з [05_PIPELINES.md](05_PIPELINES.md) |
| POST | `/pipelines/discover-and-ingest` | write+llm | J | складена задача: пошук → скринінг → завантаження → інжест (з лімітами) |

### 3.10 Задачі

| Метод | Шлях | Скоуп | Тип | Призначення |
|---|---|---|---|---|
| GET | `/jobs/{job_id}` | read | S | стан, кроки, артефакти, помилки |
| GET | `/jobs?type=&status=&owner=` | read | S | список задач |
| GET | `/jobs/{job_id}/events` | read | SSE | потік подій прогресу |
| DELETE | `/jobs/{job_id}` | write | S | скасувати (кооперативно, між кроками) |
| POST | `/jobs/{job_id}/retry` | write | S | перезапустити невдалі кроки |

### 3.11 Рукописи, бандли, контракти (з потреб споживачів, див. [07_CONSUMERS.md](07_CONSUMERS.md))

| Метод | Шлях | Скоуп | Тип | Призначення |
|---|---|---|---|---|
| POST | `/papers/resolve-batch` | read | S | до 500 ключів / DOI / paper_id за раз → `PaperIdentity` + bib-ключ |
| POST | `/manuscripts/citations` | read | S | текст рукопису + `.bib` → входження цитувань (ключ, речення, розділ, слова в лапках), відсутні ключі, нецитовані записи |
| POST | `/bib/render` | read | S | ключі або текст рукопису + `.bib` → відформатований список літератури + невирішені ключі |
| POST | `/theses/validate` | read | S | валідація `theses.json` / `atomic_claims.yaml` за JSON Schema; `422` з переліком полів замість мовчазного приведення типів |
| POST | `/theses/evidence-run` | llm | J | повний прогін доказів для бандла тез (заміна `paper3_literature_audit.py prepare…export`) |
| POST | `/bundles/import` | write | J | `graph.json` / `open_citations.json` / `paper2_graph.json` → Neo4j під простором імен `ns`, лише MERGE |
| GET | `/schemas/{name}` | — | S | JSON Schema контрактів: `Thesis`, `AtomicClaim`, `BibEntry`, `GraphBundle`, `CitationOccurrence` |
| GET | `/client.py` | — | S | однофайловий клієнт для репозиторіїв без менеджера пакетів |

### 3.12 Адміністрування

| Метод | Шлях | Скоуп | Тип | Призначення |
|---|---|---|---|---|
| POST | `/admin/rebuild/graph` | admin | J | повна перебудова графа (`build_graph`), лише MERGE |
| POST | `/admin/rebuild/vectors` | admin | J | повний реіндекс Chroma в нову колекцію + атомарне перемикання |
| POST | `/admin/rebuild/analytics` | admin | J | перебудова Parquet-шару |
| POST | `/admin/integrity/check` | admin | J | звірка ідентичності: DOI ↔ назва ↔ рік у Neo4j / normalized / enriched (01 §5.2) |

---

## 4. Ключові схеми (pydantic v2, скорочено)

```python
class Provenance(BaseModel):
    api_version: str            # "1.0.0"
    git_commit: str             # HEAD сервісу
    corpus_manifest_id: str     # sha256 від (список paper_id, правила, модель, колекція)
    collection: str | None      # "flood_papers_768d"
    embedding_model: str | None # "allenai/specter2_base"
    kb_version: str | None
    llm: LLMUsage | None        # {provider, model, prompt_hash, tokens, cached}
    generated_at: datetime

class SearchFilters(BaseModel):
    # DOI, рік, назва й когорта в Chroma НЕ зберігаються: сервіс спершу розв'язує їх
    # через таблицю ідентичності / DuckDB у список paper_id, а потім передає `$in`
    # (саме тут RQS зараз обрізає до довільних 500 статей — О-27).
    year_from: int | None = None
    year_to: int | None = None               # без жорсткої стелі 2025 (О-34)
    paper_ids: list[str] | None = None
    dois: list[str] | None = None
    sections: list[str] | None = None        # section_title у метаданих чанка
    chunk_types: list[Literal["abstract","sentence","paragraph","section",
                              "figure","table","formula"]] | None = None  # фактичні значення в колекції
    exclude_cohorts: list[str] = []          # звичайний пошук — по всьому корпусу; ендпоінти
                                             # поширеності/знаменників виключають когорту "paper_3"
                                             # (225 DOI, зібраних під тези Paper 3; 139 у корпусі), як recount.py

class ChunkSearchRequest(BaseModel):
    query: str
    k: int = Field(20, le=200)
    filters: SearchFilters = SearchFilters()
    min_score: float | None = None

class ChunkHit(BaseModel):
    chunk_id: str; paper_id: str; doi: str | None; title: str; year: int | None
    section: str | None; page: int | None; chunk_type: str
    text: str; score: float

class SearchResponse(BaseModel):
    hits: list[ChunkHit]
    coverage: Coverage                 # {papers_in_slice, chunks_in_slice, filters_applied}
    retrieval_validity: Literal["VALIDATED","UNVALIDATED","NOT_MEASURED"]
    provenance: Provenance

class QuoteItem(BaseModel):
    source: str                        # DOI або paper_id
    quote: str                         # "…" означає пропуск: фрагменти шукаються по порядку
    expected_numbers: list[str] = []   # ["5.7", "0.8", "20.4"]
    manuscript_sentence: str | None = None

class QuoteResult(BaseModel):
    status: Literal["FOUND_EXACT","FOUND_NORMALIZED","FOUND_FUZZY",
                    "NOT_FOUND","SOURCE_UNAVAILABLE"]
    score: float | None                # для FUZZY (rapidfuzz partial_ratio)
    context: str | None                # ±250 символів
    section: str | None; page: int | None; coords: list[BBox] | None
    numbers: list[NumberCheck]         # кожне число: found / context
    attribution: Attribution | None    # in-text посилання в знайденому реченні
    text_source: Literal["corpus_tei","oa_fetch","none"]

class Attribution(BaseModel):
    cites_other_sources: bool          # True → можливе вторинне цитування
    in_text_refs: list[str]            # ["Hawker et al., 2022"]
    resolved_dois: list[str]

class DoiVerifyResult(BaseModel):
    input_key: str | None
    verdict: Literal["VERIFIED","VERIFIED_WITH_NOTES","MISMATCH","UNRESOLVED","NOT_A_DOI"]
    diffs: list[FieldDiff]             # {field, given, registry, source, severity}
    notes: list[str]                   # "online 2015-10-27, print 2016-03 (vol. 37)"
    registry: DoiMetadata | None

class ClaimCheckResult(BaseModel):
    verdict: Literal["SUPPORTED","PARTIALLY_SUPPORTED","OVERSTATED",
                     "CONTRADICTED","NOT_FOUND_IN_SOURCE","SOURCE_UNAVAILABLE"]
    evidence: list[EvidenceSpan]       # дослівні фрагменти джерела з location
    explanation: str                   # чому саме цей вердикт, з посиланнями на evidence
    suggested_rewrite: str | None
    provenance: Provenance
```

`Attribution` і `OVERSTATED`/`CONTRADICTED` взято з ручної перевірки `open_citations` 2026-10-01.
Iqbal 2023 переказав Hawker 2022 і перетворив пару «1.61 → 1.12 m» на діапазон. Lehnigk 2026: рукопис сказав «після корекції батиметрії моделі відтворюють», а джерело каже «still fails». Обидва випадки API має ловити автоматично. Їх треба покласти в тести як регресійні фікстури.

---

## 5. Модель задачі (Job)

```text
queued → running → succeeded
                 ↘ failed      (крок упав після вичерпання retry)
                 ↘ partial     (частина статей/кроків успішна)
                 ↘ cancelled
```

```python
class Job(BaseModel):
    job_id: str                        # ulid
    type: Literal["ingest","acquire","discovery_search","snowball","screen",
                  "theses_extract","theses_novelty","claims_check_batch",
                  "bib_audit","metrics_llm","rebuild_graph","rebuild_vectors",
                  "rebuild_analytics","integrity_check","discover_and_ingest"]
    owner: str                         # споживач (з API-ключа)
    params: dict                       # валідований запит
    idempotency_key: str | None
    status: JobStatus
    steps: list[JobStep]               # {name, status, started_at, finished_at, attempts, error, outputs}
    progress: float                    # 0..1
    artifacts: dict                    # {"neo4j": {"merged_nodes": 41, ...}, "chroma": {"upserted": 312}, "files": [...]}
    resource_class: Literal["cpu","gpu","grobid","llm","network"]
    created_at: datetime; started_at: datetime | None; finished_at: datetime | None
    provenance: Provenance
```

- **Кроки ідемпотентні.** Кожен крок має `input_hash`, і якщо вихід із тим самим hash уже існує, крок пропускається (`skipped_up_to_date`). Повторний запуск після падіння продовжує з першого невиконаного кроку.
- **Retry.** Мережеві кроки (OpenAlex, Crossref, завантаження) мають 3 спроби з експоненційною паузою. GPU/LLM-кроки мають 1 повтор. Детерміновані помилки (бита TEI, PDF не є PDF) не повторюються.
- **Скасування.** Кооперативне, між кроками. Крок, який вже пише в Neo4j/Chroma, доводиться до кінця.
- **Ресурсні класи.** Семафори worker'а: `gpu=1` (Nougat, батч SPECTER2, Ollama), `grobid=1`, `llm` обмежений quota ledger, `network=4`.

---

## 6. Приклади викликів

```bash
# 1. Чи є стаття в корпусі
curl -s -H "X-API-Key: $GHAI_KEY" \
  "http://127.0.0.1:8090/v1/papers/resolve?doi=10.1029/2025gl120832"

# 2. Перевірити цитати з open_citations.json
curl -s -H "X-API-Key: $GHAI_KEY" -H "Content-Type: application/json" \
  -d '{"items":[{"source":"10.5194/nhess-19-2405-2019",
                 "quote":"does not accurately capture inundated cells"},
                {"source":"10.1029/2024WR038314",
                 "quote":"initial volumetric flow rate","expected_numbers":["5.7","0.8"]}]}' \
  http://127.0.0.1:8090/v1/quotes/verify

# 3. Знайти нові статті і поставити їх на інжест
curl -s -H "X-API-Key: $GHAI_KEY" -H "Content-Type: application/json" \
  -d '{"query":"SWOT water surface elevation dam break flood","year_from":2023,
       "screen":{"mode":"embedding","threshold":0.55},"max_papers":20,"ingest":true}' \
  http://127.0.0.1:8090/v1/pipelines/discover-and-ingest
# → 202 {"job_id":"01J…","status":"queued"}
curl -s -N -H "X-API-Key: $GHAI_KEY" http://127.0.0.1:8090/v1/jobs/01J…/events
```

```python
from ghai_client import GHAI          # clients/python, див. 07_CONSUMERS.md
api = GHAI.from_env()                 # GHAI_API_URL, GHAI_API_KEY
res = api.quotes.verify([{"source": "10.1016/j.rse.2014.02.015",
                          "quote": "based on a sample of higher quality"}])
assert res.items[0].status == "FOUND_EXACT"
hits = api.search.chunks("HAND does not preserve hydraulic connectivity", k=10,
                         filters={"year_from": 2015})
```
