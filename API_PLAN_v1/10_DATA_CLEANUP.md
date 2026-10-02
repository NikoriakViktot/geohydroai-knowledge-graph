# 10 — Застарілі й невикористовувані дані: що безпечно видалити

**Дата**: 2026-10-02 · **Стан**: аналіз; **нічого не видалено й не змінено**.

**Метод**: для кожного кандидата перевірено:
- хто його читає в коді (grep по `src/`, `tools/`, `scripts/`, `tests/`, ноутбуках, конфігах; окремо читачі й писачі);
- чи закріплений він у маніфестах результатів досліджень (`RETRIEVAL_MANIFEST`, `RUN_MANIFEST`, `run_manifest.json`, `*_manifest.json`, `CALIBRATION_PROVENANCE`);
- чи він дублікат (`sha256`/`cmp`);
- чи можна його відтворити, і чи результат буде ідентичним.

Найризикованіші твердження перевірено додатково: PNG-кропи Nougat лише записуються (`nougat_region_pipeline.py:828-842`). Зображення передається в Nougat з пам'яті, повторний прогін рендерить заново з PDF, а в `regions.parquet` лишається тільки рядок шляху (:731-769).

**Вердикти**:
- `SAFE_DELETE` — не використовується, дублікат або тривіально відтворюється; у жодному маніфесті не закріплений;
- `ARCHIVE_THEN_DELETE` — живий код не читає, але має історичну чи доказову цінність: спершу в стиснений архів;
- `KEEP`;
- `FIX_FIRST` — застаріле, але його читає живий код або воно закріплене; спершу виправити код;
- `OWNER` — не належить цьому проєкту, вирішує власник.

---

## 1. Підсумок

| Клас | Скільки звільнить | Головне |
|---|---|---|
| **SAFE_DELETE** у репозиторії | **≈ 46 GiB** | 43,0 GiB PNG-кропів Nougat (84 833 файли); 2,9 GiB PDF у `pdf_missing/`, побайтово ідентичних файлам у `pdf/` (977/977 за `cmp`); дрібниці |
| SAFE_DELETE у Docker | ≈ 0,4 GiB | 4 зупинені GROBID-контейнери, образ `nvidia/cuda:13.0.0`, 12 анонімних томів (див. OWNER) |
| **ARCHIVE_THEN_DELETE** | ≈ 2,4 GiB (архів ≈ 0,7 GiB) | `data/v-1`, `.chromadb_backup_20260520`, `data/ingestion_logs`, `data/analytics.bak_20260923`* |
| FIX_FIRST | ≈ 2,2 GiB + пастка з томом | `.hf_cache`; порожній том `knoweledg_graf_neo4jdata`; `data/analytics` (закріплений, видаляти не можна) |
| **OWNER** | ≈ 56 GiB | 10 моделей HF у `~/.cache/huggingface/hub` (55,5 GiB), томи `epitaphs-llm-v2_*` (590 MB), анонімні томи з грудня 2025 |
| KEEP | — | PDF, TEI, JSON, Chroma, кеші, реєстр, аудити статей, `.venv`, образ GROBID, Ollama |

\* `analytics.bak_20260923` побайтово дорівнює `data/analytics` і стає **єдиною копією закріпленого входу Paper 3**, щойно `data/analytics` перебудують. Див. §4.

---

## 2. Таблиця (за розміром)

| Що | Розмір / файлів | Хто читає (код / маніфести) | Дублікат | Відтворюється? | Вердикт | Ризик |
|---|---|---|---|---|---|---|
| Моделі HF: Qwen2.5-7B-Instruct, Qwen1.5-7B-Chat, OpenHermes-2.5-Mistral-7B, Qwen2.5-7B-GPTQ, deberta-large, gpt2-large, roberta-large, deberta-base-mnli, stsb-distilroberta, deberta-v3-base | 55,5 GiB | жоден код у `/home/niko/projects/*` | — | завантажити з HF | **OWNER** | з'явились у грудні 2025, ймовірно для іншого проєкту (epitaphs-llm-v2). **Qwen2.5-7B-Instruct може знадобитися як локальний вчитель для LoRA** ([08 §6](08_LORA_DATASET.md)) |
| `data/nougat_regions/*/crops/*.png` | **43,0 GiB / 84 833** | лише запис (`nougat_region_pipeline.py:806-842`); `region_kg_loader.py:481` читає лише `regions.json`; споживачі беруть `sodb/*/regions.parquet` | — | так, на CPU: рендер bbox з PDF (`pdf_io.py:162`) | **SAFE_DELETE** (лишити `regions.json`, 0,13 GiB) | рядки `crop_path` у parquet вказуватимуть на відсутні файли (косметика) |
| `data/literature/pdf` | 24 GiB / 4 849 | `settings.py:84`, `grobid_ingest.py:50`, `nougat_region_pipeline.py:60` | 143 дублікати всередині (127 груп, 1,13 GB) — див. §5 | **ні** | KEEP | — |
| Образ GROBID `0.9.0-full` | 16,8 GB | `docker-compose.yml:26-27` | — | pull | KEEP | TEI залежить від цієї версії |
| `.venv` | 11 GiB | середовище виконання | — | частково | KEEP | — |
| `.chromadb` | 8,4 GiB | `config/__init__.py:17`; `RETRIEVAL_MANIFEST.json`; U-Net `run_manifest.json` (n_chroma 1 354 158) | — | години GPU, не ідентично | KEEP | — |
| Ollama `mistral-nemo:12b` | 6,6 GiB | `settings.py:8` | — | pull | KEEP | — |
| `pdf_missing/`: 977 файлів, які є і в `pdf/` | **2,9 GiB** | писачі: `harvest_ingest.py:103-115`, `recover_missing.py:51`; `export_reading_lists.py:212` спершу дивиться в `pdf/` | **побайтово = `pdf/`** (`cmp` 977/977) | — | **SAFE_DELETE** | 249 `regions.json` записують ці шляхи як `pdf_path` (код їх не читає); гарвест Paper 3 перезавантажить файл, якщо DOI повернеться в чергу |
| `pdf_missing/`: 375 файлів, яких немає в `pdf/` | 1,4 GiB | те саме | 3 дублікати за вмістом | ні | KEEP | єдина копія |
| `data/v-1` | 2,1 GiB / 22 629 | коду немає (`normalization_runner.py:35` — лише docstring з неправильним шляхом); у маніфестах немає | старі версії, **не** копії живих даних; `becap_json` = `paper_json_V1` (300/300) | дні GROBID/Ray, не ідентично | **ARCHIVE_THEN_DELETE** (стиснення ~4,3× → ~0,45 GiB) | втрата знімків до виправлень |
| `.hf_cache` | 2,2 GiB | **`src/ingestion/pipeline.py:85-86` робить його `HF_HOME`** (`settings.py:45`) | ті самі ревізії, що в `~/.cache` (specter2 `3447645e` + `0044d716`, bge-large `d4aa6901`, MiniLM `c9745ed1`) | download | **FIX_FIRST** | повторне завантаження може взяти **новішу ревізію SPECTER2**, і ембединги розійдуться з індексом |
| `data/paper_1_audit` | 946 MiB | `scripts/paper1_*.py`; sha закріплені в `vc_manifest.json`, `swot_icesat_zone_manifest.json:17` | — | ні (EGG2015 обмежений) | KEEP | — |
| `grobid_xml`, `paper_json`, `enriched`, `normalized` | 2,6 GiB разом | `settings.py:51-53,76`; кількість normalized закріплена (4 841 і 5 027) | — | дні, не ідентично | KEEP | — |
| `data/cache` | 707 MiB | `enrichment_runner.py:96`, `build_author_universe.py:49`, `missing_reference_recovery.py:39` | — | ~65 тис. запитів, дані дрейфують | KEEP | — |
| Docker-том `neo4jdata` (+ логи) | 661 MiB | контейнер `course-neo4j` (запущений через `docker run`, а не compose) | — | `build_graph` | KEEP | — |
| `data/paper_3_audit` | 450 MiB | `RETRIEVAL_MANIFEST`, `RUN_MANIFEST`, `CALIBRATION_PROVENANCE`, `SNAPSHOT_MANIFEST` | `briefs/geodesy/pdf` — 11 PDF-дублікатів (76 MB), окремо навмисно (`briefs/geodesy.py:11`) | ні | KEEP | — |
| Образ `nvidia/cuda:13.0.0` | 411 MB | ніде | — | pull | SAFE_DELETE | — |
| `.chromadb_backup_20260520` | 249 MiB | лише документи (`docs_v2/DATA_MODEL.md:223`, `PIPELINE.md:267`, CLAUDE.md:195) | стара 384-d колекція `flood_papers`, 13 970 векторів | не ідентично | ARCHIVE_THEN_DELETE | — |
| `data/ingestion_logs` | 42 MiB / 14 | лише запис (`grobid_ingest.py:51,339`, `ingest_missing_pdfs.py:47,80`) | — | ні | ARCHIVE_THEN_DELETE (стиснення ~19×) | — |
| `data/analytics` | 42 MiB / 32 | дашборд (`callbacks.py:1262,1271,1690`), `duckdb_manager.py:14`, RQS `:48-55`, `notebooks/shared/db.py`, `paper_3/_utils.py:94-97`, `paper_audit/_utils.py:16`. **Закріплений**: `papers_parquet_sha256 0f7a45a2…` = `analytics/papers.parquet` (3 680 рядків) | — | перебудова не дасть того ж хешу | **KEEP / FIX_FIRST** | застарілий (3 680 проти 5 013), але закріплений |
| `data/analytics.bak_20260923` | 42 MiB | пише `harvest_ingest.py:370-387`, не читає ніхто | **побайтово = `analytics`** (32/32) | — | ARCHIVE_THEN_DELETE | стане єдиною закріпленою копією після перебудови `analytics` |
| `data/paper_3_audit/missing_paper_23-09-2026` | 33 MiB / 16 | ніде | усі 16 побайтово є в `literature/` | — | SAFE_DELETE | — |
| 4 зупинені GROBID-контейнери (`admiring_murdock`, `happy_mestorf`, `sweet_raman`, `cranky_ramanujan`) | 15 MB | запускались вручну; без томів; порт 8070 як у compose | — | — | SAFE_DELETE | — |
| `data/registry` | 13 MiB | `grobid_ingest.py:306`; порожні `audit/`, `parquet/` створює `registry_db.py:236-237` | — | частково | KEEP | — |
| `__pycache__` (47), `.pytest_cache`, `src/ingestion/grobid_pipeline.log`, `debug/` | 15 MiB | генеруються / не читаються | — | так | SAFE_DELETE | — |
| `tiff.bak_20260814` (`src/paper_audit/…`), кореневі `flood_mapping_eda.ipynb`, `flood_rag_analysis.ipynb` | 4 MiB | ніде (крім `create_notebook.py:655`) | — | частково | ARCHIVE_THEN_DELETE | — |
| `data/raw` | 2,8 MiB | `raw_store.py:27`, `sdom_runner.py:83-90` | — | секунди | KEEP | — |
| 12 анонімних Docker-томів | 0,7 MB | жоден контейнер | — | — | **OWNER** | створені в грудні 2025, до цього репозиторію |
| `data/chromadb` | 188 KiB | ніде (`vector_synchronizer.py:42` вказує на `data/chroma`, клас без викликів) | порожня Chroma-БД, 0 колекцій | — | SAFE_DELETE | — |
| Осиротілі HNSW-каталоги в `.chromadb` (`96c449fc`, `ea0e3c2b`, `5bd974d6`) | 30 MiB | немає в таблиці `segments` (живий лише `c5e5d1c0`) | `96c449fc` = копія в бекапі | — | SAFE_DELETE, **останнім** | лише після злиття WAL (§3, крок 6) |
| 3 957 `:Zone.Identifier`, `=23.0`, `=4.0`, `.pipeline_stop`, `rag_pipeline.log`, `docker` (0 B) | < 1 MiB | ніде; `docker` і 2 файли Zone.Identifier — **у git** | — | — | SAFE_DELETE (`git rm` для 3 трекованих) | — |
| Том `knoweledg_graf_neo4jdata` | 0 B | **`docker compose` монтує саме його** (`docker-compose.yml:20,38`; проєкт `knoweledg_graf`), а дані лежать у `neo4jdata` контейнера `course-neo4j` (перевірено) | — | — | **FIX_FIRST** | `docker compose up` підняв би **порожній граф** або конфлікт портів з `course-neo4j` |
| Знімки статей: `publication_audited_*`, `candidates_parts`, `references_38e3375.bib`, `revisions_before_*`, `paper_audit/frozen*`, `backup*` | < 15 MiB | `README_BUILD.md:4`, `tools/paper3_audit/retrieve.py:309,361`, `07d_corpus_references.bib:2` | — | ні | KEEP | — |

Інші дрібні KEEP: `data/parquet`, `evaluation`, `graph`, `ontology*`, `outputs/` (`scripts/paper1_*.py:53-55`), `assets/` (дашборд), `figures/`, `notebooks/`.
HF-моделі **цього** проєкту: `specter2_base`, `nougat-base`, `bge-large-en-v1.5`, `all-mpnet-base-v2`, `all-MiniLM-L6-v2`. Моделі `bge-m3`, `clip-ViT-B-32`, `dinov2-small` використовує проєкт media_kakhovka.

---

## 3. Безпечна послідовність (рекомендації; кожен крок виконується лише з вашої команди)

1. **Спершу маніфест.** sha256 + розмір для `data/` і `.chromadb` → `migration/manifest_source.parquet` (той самий крок P7, що в [09](09_MIGRATION.md)). Перевірити, що `data/analytics/papers.parquet` досі має хеш `0f7a45a2…`.
2. **Заморозити закріплені входи досліджень** (§4): `data/analytics` + `RETRIEVAL_MANIFEST` → `data/frozen/paper3_retrieval_20260916/` (копія, read-only права).
3. **Архівувати → перевірити → видалити**:
   - архівувати `data/v-1` (дублікат `becap_json` прибрати першим), `.chromadb_backup_20260520`, `data/ingestion_logs`, два кореневі ноутбуки, `tiff.bak_*`;
   - для кожного архіву: `tar --zstd`, порівняти список файлів і sha256 з маніфестом, і лише потім видаляти оригінал.
4. **Без ризику**:
   - `:Zone.Identifier`, `__pycache__`, `.pytest_cache`, `debug/`, кореневі `=23.0`, `=4.0`, `docker` (через `git rm`), логи, `data/chromadb`, `missing_paper_23-09-2026`;
   - Docker: `docker rm` 4 GROBID-контейнерів, `docker rmi nvidia/cuda:13.0.0`.
5. **Дублікати в `pdf_missing/`**: безпосередньо перед видаленням ще раз `cmp` проти `pdf/` і видаляти лише ідентичні.
6. **Кропи Nougat**: видаляти лише `*/crops/*.png`, ніколи `regions.json`. За бажанням спершу перевірити скрипт повторного рендеру кропів на 2–3 статтях.
7. **Спершу виправити, потім чистити**:
   - `HF_HOME` у `.env`; переконатися, що SPECTER2 ревізії `3447645e` вантажиться; лише тоді прибрати `.hf_cache`, або залишити його як закріплений кеш.
   - `docker-compose.yml`: том `neo4jdata` як `external: true` (+ `container_name`). Лише тоді видалити порожній `knoweledg_graf_neo4jdata`.
   - Дашборд і ноутбуки перевести на `data/parquet` **до** будь-якої перебудови `data/analytics`.
   - Виправити баг злиття в `build_parquet_layer` (О-25) до наступного його запуску.
   - Осиротілі HNSW-каталоги — останніми. Спершу один Chroma-клієнт зливає 253 записи `embeddings_queue` і показує `count() == 1 354 158`, потім коректно закривається.
8. **OWNER-пункти** (моделі HF, томи `epitaphs-*`, анонімні томи) — лише після вашого підтвердження, що вони не потрібні іншому проєкту. Qwen2.5-7B-Instruct варто лишити, якщо його планується використовувати як вчителя для LoRA.

---

## 4. Провенанс досліджень: що вже втрачено і що не можна втратити

- **Заморожений ретривал Paper 3 від 2026-09-16 уже не відтворюється побайтово, хоч би що ми видалили:**
  - закріплений у маніфесті `references.parquet` (sha `d6124d0b…`) перезаписано 2026-09-23 (тепер 883 рядки, баг О-25), і в системі його більше немає;
  - Chroma з того часу зросла з 1 313 665 до 1 354 158 векторів.
  - Це варто записати в `RUN_MANIFEST` / `ACCEPTANCE_GATE` як відому неможливість відтворення.
- **Не можна втратити**: `data/analytics/papers.parquet` (sha `0f7a45a2…`, 3 680 рядків) — другий закріплений вхід. Тому `data/analytics` — це KEEP / FIX_FIRST, а не видалення. Перед будь-якою перебудовою його треба заморозити (§3, крок 2).
- **Правило на майбутнє** (у [06](06_ROADMAP.md), WP0.6): кожна задача, що перебудовує файл, закріплений у будь-якому маніфесті, спершу копіює його в `data/frozen/<run_id>/`. Перевірку додати в CI: grep хешів з маніфестів проти наявних файлів.

---

## 5. Не вирішено (потрібне ваше рішення)

| Що | Чому не вирішено |
|---|---|
| 143 дублікати PDF усередині `pdf/` (127 груп, 1,13 GB) | у кожного свої XML, JSON і чанки Chroma. Видалення змінює корпус і закріплені лічильники (`n_duplicate_groups` 144 у U-Net run manifest), тож це рішення про корпус, а не прибирання. Правильне місце — таблиця ідентичності (WP0.3, статус `DUPLICATE`) і політика злиття |
| Моделі HF (55,5 GiB), томи `epitaphs-llm-v2_*` (590 MB), 12 анонімних томів | коду в `/home/niko/projects` немає; дати збігаються з проєктом epitaphs-llm-v2, вихідного коду якого на цьому дистрибутиві немає |
| Контейнери й томи `kakhovka-*` (зокрема 21 GB тому Nominatim) | інший проєкт, що зараз працює; не оцінювався |
| `EXHIBIT A #3013EURIZON_Prof. Osypov` у корпусі | не стаття, адміністративний документ з іменами (О-40): прибрати з корпусу, Chroma й графа — рішення про корпус |
