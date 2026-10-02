# 09 — Міграція на інший сервер (бази даних і файли)

**Дата**: 2026-10-02 · **Стан**: план; нічого не переносилося

**Звідки**: WSL2-дистрибутив `Ubuntu` на робочій станції (23 GB RAM, 32 ядра, RTX A4000 16 GB). Сусідній дистрибутив `Ubuntu-24.04` — це споживачі.

**Куди**: виділений Linux-сервер. Рекомендовано нативну Ubuntu 24.04 LTS без WSL: кращий дисковий I/O, systemd, немає файлів `:Zone.Identifier`.

---

## 1. Що переноситься (інвентар на 2026-10-02)

| Компонент | Де зараз | Розмір / файлів | Формат, версія | Спосіб | Можна перебудувати? |
|---|---|---|---|---|---|
| Код | git `NikoriakViktot/geohydroai-knowledge-graph` | — | Python 3.12.3 | `git clone` | — **але `tools/`, `scripts/paper1_*`, частина тестів не закомічені (О-5)** |
| PDF корпусу | `data/literature/pdf` | 24 GB / 4 849 | PDF | rsync | ні (частина PDF отримана не з OA-джерел; повторно не завантажиться) |
| TEI XML | `data/literature/grobid_xml` | 973 MB / 5 039 (+2 322 `:Zone.Identifier`) | TEI | rsync, без Zone.Identifier | так, GROBID ≈ години на 5 тис. PDF |
| paper.json | `data/literature/paper_json` | 540 MB / 5 038 | JSON | rsync | так, але ≈ дні Ray + Ollama |
| normalized / enriched | `data/normalized`, `data/enriched` | 532 + 578 MB / 5 027 + 5 013 | JSON | rsync | normalized — швидко; enriched — з кешу OpenAlex |
| SODB | `data/sodb` | 171 MB / 20 974 | Parquet | rsync | так |
| Nougat: `regions.json` | `data/nougat_regions/*/regions.json` | 0,13 GB | JSON | rsync | результати інференсу Nougat (GPU) → **копіювати**; `region_kg_loader` читає лише їх |
| Nougat: PNG-кропи | `data/nougat_regions/*/crops/*.png` | **43 GiB / 84 833** | PNG | **не переносити** (`--exclude='crops/'`) | лише записуються, ніким не читаються; за потреби рендеряться з PDF на CPU ([10 §2](10_DATA_CLEANUP.md)) |
| Аналітика | `data/analytics` (+ `.bak_20260923`), `data/parquet` | 42 + 42 + 1,4 MB | Parquet/CSV | rsync | так |
| **Кеші HTTP** | `data/cache/openalex_doi.db`, `author_cache.db` | 707 MB | SQLite | `sqlite3 .backup` | так, але ≈ 60 тис. запитів до OpenAlex → копіювати |
| **Реєстр** | `data/registry/pipeline_registry.duckdb` + `parquet/`, `audit/` | 13 MB | DuckDB 1.5.2 | `EXPORT DATABASE` + копія файлу | частково |
| **ChromaDB** | `.chromadb/` | **8,4 GB / 21 файл**; 1 354 158 векторів | chromadb **1.5.9** (SQLite + HNSW) | зупинка → tar → копія → перевірка | так: реіндекс 1,35 млн чанків на GPU (години) |
| **Neo4j** | docker-том `neo4jdata` (`/var/lib/docker/volumes/neo4jdata/_data`) — його використовує контейнер `course-neo4j`, запущений **через `docker run`**. ⚠ `docker compose` монтує **інший, порожній** том `knoweledg_graf_neo4jdata` (О-43) | 117 MB баз + 515 MB журналів транзакцій | **Neo4j 5.26.19 Community**; образ `neo4j:5` (digest `sha256:0e5d68ed…`) | `neo4j-admin database dump` (лише офлайн у Community) | **так, і це рекомендовано**: поточний граф без семантичних ребер (О-1) |
| Закріплені входи досліджень | `data/analytics/papers.parquet` (sha `0f7a45a2…`) і решта `data/analytics` → `data/frozen/paper3_retrieval_20260916/` | 42 MB | Parquet | rsync **як read-only архів** | **ні**: перебудова не дасть того ж хешу ([10 §4](10_DATA_CLEANUP.md)) |
| Аудити статей | `data/paper_1_audit`, `data/paper_3_audit`, `paper_*/` | 946 + 450 MB | CSV/MD/Parquet | rsync | ні (результати досліджень) |
| Архіви | `data/v-1` (2,1 GB / 22 629), `.chromadb_backup_20260520` (249 MB) | 2,4 GB | — | у холодний архів, не на робочий сервер | — |
| Docker-образи | GROBID `0.9.0-full` **16,8 GB**, `neo4j:5` 524 MB | — | — | `docker pull` за digest на цільовому сервері | так |
| Ollama | `/usr/share/ollama/.ollama` | 6,6 GB | `mistral-nemo:12b` (`e7e06d107c6c`) | `ollama pull` + звірка digest | так |
| HF-моделі | **`.hf_cache/`** (2,2 GiB) — це `HF_HOME` пайплайну (`src/ingestion/pipeline.py:85-86`): specter2 `3447645e` (+ `0044d716`), bge-large `d4aa6901`, MiniLM `c9745ed1`, Nougat. `~/.cache/huggingface/hub` (64 GB) — переважно чужі моделі | 2,2 GiB | — | **копіювати `.hf_cache` як є** (це закріплені ревізії); з `~/.cache` — нічого | так, але **ревізії мають збігатися** (§4.6) |
| Секрети | `.env` | — | — | переписати вручну, **з ротацією** | — |
| (майбутнє) API | `data/api/{jobs,identity,keys}.sqlite` | малі | SQLite | `.backup` | частково |

**Разом**:
- **до очищення** ≈ 80 GB робочих даних і ≈ 160 тис. файлів;
- **після [10_DATA_CLEANUP.md](10_DATA_CLEANUP.md)** (без PNG-кропів, дублікатів `pdf_missing/`, архівів) ≈ **32 GB і ≈ 70 тис. файлів**;
- плюс ≈ 25 GB образів і моделей.

Очищення перед міграцією скорочує вікно rsync приблизно вдвічі.

---

## 2. Передумови (до першого копіювання)

| # | Що зробити | Навіщо |
|---|---|---|
| P1 | **Закомітити** `tools/`, `scripts/paper1_*`, тести й незакомічені правки `nougat_parser.py`/`pdf_io.py`/`grobid_client.py` (О-5) | інакше код на новий сервер не потрапить |
| P2 | **Повний lock-файл**: `uv pip compile` / `pip freeze > requirements.lock` з основного `.venv` (у `requirements.txt` бракує 23 пакетів) | відтворюване середовище |
| P3 | **Закріпити версії**: `chromadb==1.5.9`, `duckdb==1.5.2`, Python-драйвер `neo4j 6.2.0`, сервер `neo4j:5.26.19-community`, `grobid/grobid:0.9.0-full@sha256:…`, `transformers==5.8.1` (виправлення Nougat), ревізії моделей HF | формати сховищ і простір ембедингів прив'язані до версій |
| P4 | **Прибрати абсолютні шляхи й `wsl.exe`** з коду: `src/dashboard_dash/app.py`, `src/paper_3/snapshot.py`, `src/paper_3/v2/scientific_status.py`, `tools/paper3_audit/{config,bibtex,cli}.py`, `scripts/paper1_swot_icesat_*.py`, 2 тести → змінні `GHAI_DATA_ROOT`, `GHAI_CHROMA_URL`, `GHAI_BUNDLES_*` | на сервері немає ні `/home/niko`, ні другого дистрибутива |
| P5 | **Злити WAL ChromaDB** (О-4): у вікні обслуговування відкрити колекцію одним процесом, переконатися, що `count() == 1 354 158` і пошук працює, коректно закрити. Потім `embeddings_queue` має бути порожньою, а `max_seq_id` сегментів — однаковим | інакше копія містить пошкоджену ситуацію, описану в CLAUDE.md |
| P6 | **Прибрати сміття**: 3 957 файлів `:Zone.Identifier`, `__pycache__`; архіви (`data/v-1`, `analytics.bak_*`, `.chromadb_backup_*`) перенести в холодне сховище | менше копіювати; на Linux ці файли зайві |
| P7 | **Маніфест даних**: `sha256 + size + mtime` для кожного файлу `data/` і `.chromadb/` → `migration/manifest_source.parquet` | єдиний спосіб довести, що нічого не загубилось |
| P8 | **Рішення щодо Neo4j**: переносимо дамп як архів, але робочий граф **перебудовуємо** на новому сервері після виправлення О-1/О-2 | поточний граф неповний; переносити його як джерело істини безглуздо |
| P9 | **Виправити `docker-compose.yml`**: том `neo4jdata` як `external: true`, пароль через `${NEO4J_PASSWORD}`, закріплені версії образів; на сервері — bind mount `/srv/ghai/stores/neo4j` | зараз `docker compose up` піднімає **порожній** граф у `knoweledg_graf_neo4jdata`, а дані живуть у `neo4jdata` контейнера, запущеного вручну |
| P10 | **Заморозити закріплені входи** (`data/analytics` → `data/frozen/paper3_retrieval_20260916/`) і виконати очищення з [10 §3](10_DATA_CLEANUP.md), кроки 1–6 | не перенести на сервер 46 GiB сміття і не втратити закріплене |

---

## 3. Цільовий сервер

| Ресурс | Мінімум | Рекомендовано | Обґрунтування |
|---|---|---|---|
| CPU | 16 ядер | 32 | Ray-воркери, GROBID, паралельне rsync/хешування |
| RAM | 32 GB | **64 GB** | Chroma-сервер ≈ 4,5 GB + Neo4j (heap + page cache) 4–8 GB + GROBID 5,3 GB + Ray 3×2–3 GB + API 1,5 GB + Ollama |
| GPU | 16 GB | **24 GB** (якщо там же тренуватиметься LoRA, див. 08) | Nougat, SPECTER2, Ollama, QLoRA 12B |
| Диск | 300 GB NVMe | **1 TB NVMe** + окремий диск/бакет для бекапів | дані 80 GB + образи/моделі 25 GB + приріст (≈ 15 GB на кожну 1 000 нових статей: PDF + Nougat-кропи + чанки) |
| ОС | Ubuntu 24.04 LTS | + NVIDIA driver, `nvidia-container-toolkit`, Docker, systemd | GPU для GROBID-контейнера (`deploy.resources.reservations.devices`) |

**Розкладка каталогів** (bind mounts замість іменованих томів, щоб бекап був просто копією каталогу):

```text
/srv/ghai/
  code/            # git clone
  data/            # = поточний data/ (GHAI_DATA_ROOT)
  stores/neo4j/    # bind mount → /data контейнера neo4j
  stores/chroma/   # = поточний .chromadb/ (chroma run --path)
  models/hf/       # HF_HOME; лише потрібні моделі з закріпленими ревізіями
  models/ollama/   # OLLAMA_MODELS
  backups/         # локальні знімки перед вивантаженням назовні
  logs/
```

---

## 4. Як переносити кожну базу

### 4.1 Файли (`data/`, ≈ 75 GB)

1. **Попередня синхронізація**, поки система працює. Дані майже тільки доповнюються, тож це безпечно:
   ```bash
   rsync -aH --info=progress2 --partial --exclude='*:Zone.Identifier' --exclude='__pycache__' \
         --exclude='nougat_regions/*/crops/' \
         data/ ghai@server:/srv/ghai/data/
   ```
2. **Фінальна дельта** у вікні заморожування (пункт 5): той самий `rsync` з `--delete`. Він переносить лише змінене.
3. **Перевірка**: побудувати `manifest_target.parquet` на сервері і порівняти з P7. Кількість і контрольні суми мають збігтися повністю, з урахуванням свідомо виключених архівів.

### 4.2 Neo4j 5.26.19 Community

**Варіант А — перебудова (рекомендовано).** На сервері:
1. `docker compose up -d neo4j` з `neo4j:5.26.19-community` і новим паролем.
2. `python -m src.graph.build_graph …` після виправлення О-1/О-2.
3. `python -m src.graph.table_kg_loader`.

Перебудова з `data/enriched` + `normalized` займає хвилини–години, без GPU.

**Варіант Б — дамп/відновлення** (архів і відкат; також якщо граф уже містить ручні дані, яких немає у файлах, наприклад імпортовані бандли тез):

```bash
# джерело: Community вміє лише офлайн-дамп
docker compose stop neo4j
docker run --rm -v neo4jdata:/data -v $PWD/backups:/backups neo4j:5.26.19 \
  neo4j-admin database dump neo4j --to-path=/backups
# ціль: та сама версія 5.26.19
docker run --rm -v /srv/ghai/stores/neo4j:/data -v /srv/ghai/backups:/backups neo4j:5.26.19 \
  neo4j-admin database load neo4j --from-path=/backups --overwrite-destination=true
```

**Перевірка**: кількість вузлів за кожною міткою і зв'язків за кожним типом (включно з `CITES`, якого не рахує `rel_counts()`), обмеження й індекси (`SHOW CONSTRAINTS`, `SHOW INDEXES`), 10 контрольних запитів з `graph_statistics`.

### 4.3 ChromaDB 1.5.9 (8,4 GB, 1 354 158 векторів)

1. Виконати P5: WAL злитий, `count()` перевірено.
2. Зупинити **всіх** клієнтів: дашборд, VectorStoreActor, реіндексатор. Переконатися, що ніхто не тримає `chroma.sqlite3` (`fuser`).
3. `tar -cf chroma_20261xxx.tar .chromadb/` → копія → розпакувати в `/srv/ghai/stores/chroma/`.
4. На сервері запустити `chroma run --path /srv/ghai/stores/chroma --host 127.0.0.1 --port 8001` з **тією ж** версією 1.5.9.
5. **Перевірка**:
   - `count() == 1 354 158`;
   - `PRAGMA integrity_check` для `chroma.sqlite3`;
   - **20 фіксованих запитів**: ID і скори top-10 на джерелі та цілі мають збігтися;
   - smoke-тест на 10 запитів із `REPAIR_EXECUTION_REPORT_v1`.
6. Якщо перевірка не пройшла, відкат такий: реіндекс у **нову** колекцію на сервері (`reindex_chromadb --collection-name flood_papers_768d_v2`), потім перемикання `COLLECTION_NAME`. Займає години GPU-часу, але це надійний запасний варіант.

### 4.4 DuckDB-реєстр (1.5.2)

```sql
-- джерело, коли жоден writer не працює
EXPORT DATABASE 'migration/registry_export' (FORMAT PARQUET);
-- ціль: якщо версія DuckDB та сама — достатньо скопіювати файл; інакше:
IMPORT DATABASE 'migration/registry_export';
```

Перевірка: кількість рядків у кожній таблиці і розподіл статусів (`python -m src.registry.cli stats`) на обох боках.

### 4.5 SQLite (кеші OpenAlex/авторів, майбутні jobs/identity/keys)

```bash
sqlite3 data/cache/openalex_doi.db ".backup 'migration/openalex_doi.db'"   # консистентна копія навіть при читачах
sqlite3 migration/openalex_doi.db "PRAGMA integrity_check;"                 # на цілі: ok
```

### 4.6 Моделі (критично для сумісності векторів)

- **SPECTER2**: на сервері мають бути ті самі ревізії `allenai/specter2_base` і адаптера, що й під час індексації. Інакше ембединги запитів потрапляють в інший простір, а пошук мовчки деградує, без жодних помилок.
  - Пайплайн бере моделі з **`.hf_cache/`** (`HF_HOME`, `src/ingestion/pipeline.py:85-86`). Там specter2 `3447645e` (+ `0044d716`). Ці ревізії записати в маніфест.
  - На сервер переносити саме `.hf_cache/`. Якщо завантажувати заново, то лише через `huggingface-cli download … --revision <sha>`.
- **Nougat і spaCy** `en_core_web_sm`: також з закріпленими версіями.
- **Ollama**: `ollama pull mistral-nemo:12b`, звірити digest `e7e06d107c6c`.
  - Майбутній LoRA-адаптер (08) переноситься як файл GGUF разом з `Modelfile`.
- **Не переносити**: інші моделі з 64 GB HF-кешу (Qwen, OpenHermes) — проєкту вони не потрібні.

---

## 5. Перемикання (cutover)

| Коли | Крок | Готово, коли |
|---|---|---|
| T−7 дн | P1–P8; сервер підготовлено (ОС, драйвери, Docker, користувач `ghai`) | `pytest` 1 413/1 413 на сервері з чистого clone; lock-файл встановлюється |
| T−3 дн | попередній `rsync` даних і tar Chroma (з P5); `docker pull` образів; моделі | маніфести збігаються для скопійованого |
| T−2 дн | **сухий прогін на сервері**: Chroma-сервер, перебудова Neo4j (варіант А), реєстр, API/worker (якщо вже є) | перевірки §4.2–4.5 зелені; інжест 1 нової статті від PDF до вузлів і векторів проходить |
| T0 | **Заморожування записів**: зупинити пайплайни/worker, дашборд у read-only; фінальний `rsync --delete`; дельта Chroma (якщо після T−3 були upsert-и, повторити tar); дамп Neo4j як архів | дельта-маніфест збігається |
| T0 + 1 год | перемкнути споживачів: `GHAI_API_URL` у `.env` floodstate-eo / SWOT-DNIPRO / kakhovka-terrain; MCP-конфіги | `/v1/health` зелений з машини споживача; 20 контрольних запитів ідентичні |
| T+1…14 дн | паралельна робота; старе середовище **тільки для читання** як відкат | жодних розбіжностей у щоденній звірці лічильників |
| T+14 дн | заархівувати старе середовище (дані → холодне сховище), видалити робочі копії | — |

**Відкат**: до T+14 повернути `GHAI_API_URL` на старе середовище. Записи, зроблені на новому сервері після T0, вивантажуються задачами інжесту за `job_id` і повторюються на старому, оскільки кожен крок ідемпотентний.

---

## 6. Що змінюється в мережі й безпеці, коли споживачі на іншій машині

Зараз безпека тримається на тому, що все слухає `127.0.0.1` і обидва WSL-дистрибутиви ділять цей loopback ([07_CONSUMERS.md §3](07_CONSUMERS.md)). На окремому сервері це припущення зникає.

| Що | Рішення |
|---|---|
| Доступ до API | **Варіант 1 (рекомендовано)**: приватна мережа Tailscale/WireGuard, API слухає лише інтерфейс VPN. **Варіант 2**: reverse proxy (Caddy) з TLS на 443 + API-ключі + allowlist IP. Ніколи не відкривати uvicorn напряму |
| Neo4j, Chroma, GROBID, Ollama | слухають **тільки `127.0.0.1` на сервері**; доступ лише через API. Порти 7474/7687/8001/8070/11434 закриті фаєрволом (`ufw default deny incoming`) |
| Секрети | `.env` з правами 600 під користувачем `ghai`. Пароль Neo4j **ротується під час міграції** (закриває О-10). Видалити `python2024` з `docker-compose.yml`, CLAUDE.md і скриптів |
| API-ключі | обов'язкові для всіх ендпоінтів, крім `/health`; окремий ключ на кожного споживача; ротація раз на квартал |
| Обмін файлами з репозиторіями | лише через API (`/bundles/import`, `/bib/audit`, `/ingest/upload`). `wsl.exe` і спільні шляхи зникають (P4) |
| PDF і ліцензії | корпус містить статті не з OA-джерел. На сервері з мережевим доступом PDF **ніколи не віддаються** через API, лише текстові фрагменти-докази й метадані. Каталог `pdf/` не публікується |

---

## 7. Бекапи на новому сервері (визначити одразу)

| Що | Як | Як часто | Зберігати |
|---|---|---|---|
| `data/` (файли) | `restic`/`borg` інкрементально в окреме сховище | щодня | 14 щоденних, 8 тижневих, 6 місячних |
| SQLite (кеші, jobs, identity, keys) | `.backup` → restic | щодня | так само |
| DuckDB-реєстр | `EXPORT DATABASE` (Parquet) → restic | щодня | так само |
| Neo4j | офлайн-дамп (Community) у вікні обслуговування **або** перебудова з файлів | щотижня + перед кожним `rebuild_graph` | 4 останні |
| Chroma | зупинка сервера → tar; перед кожним `rebuild_vectors` нова колекція поруч | щотижня | 2 останні |
| Конфіги, `.env`, Modelfile, lock-файли | git (без секретів) + зашифрована копія `.env` | при зміні | — |

**Перевірка відновлення раз на місяць**: розгорнути бекап на тестовому каталозі й прогнати §4 (маніфест, лічильники, 20 запитів). Бекап, який ніхто ніколи не відновлював, лише вважається бекапом.

---

## 8. Ризики

| Ризик | Ймовірність | Що робимо |
|---|---|---|
| HNSW Chroma не перечитується на цілі (відомий збій 1.5.9) | середня | P5 + перевірка `count()`/20 запитів; запасний варіант — реіндекс у нову колекцію |
| Інша ревізія SPECTER2 на сервері → тиха деградація пошуку | середня | закріплені ревізії + порівняння top-10 на 20 запитах |
| Плаваючий тег `neo4j:5` підтягне іншу мінорну версію, яка не відкриє дамп | середня | закріпити `5.26.19-community` |
| Незакомічений код не потрапить на сервер | висока, якщо пропустити P1 | P1 — перший крок; перевірка: `pytest` із чистого clone |
| Абсолютні шляхи / `wsl.exe` в коді | висока | P4 + `grep` у CI на `/home/niko` і `wsl.exe` |
| Новий сервер видно ззовні → витік PDF або паролів | залежить від мережі | §6: VPN, фаєрвол, бази лише на `127.0.0.1`, ротація секретів |
| Час копіювання | — | PNG-кропи (43 GiB, 85 тис. файлів) не переносяться; попередній rsync за кілька днів до T0; у вікні лише дельта |
| `docker compose up` на сервері або локально піднімає порожній граф (інший том) | висока, доки не виконано P9 | P9: `external: true` / bind mount; перевірка лічильників вузлів одразу після старту |
| Закріплений вхід Paper 3 (`analytics/papers.parquet`, `0f7a45a2…`) перезапишуть під час перебудови | середня | P10: заморозити в `data/frozen/` до будь-якої перебудови |
