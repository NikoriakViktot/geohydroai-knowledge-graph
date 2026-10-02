# 07 — Споживачі: як інші репозиторії звертаються до API

**Дата**: 2026-10-02 · **Джерело**: read-only огляд дистрибутива `Ubuntu-24.04` (`/home/niko/repo/*`) + мережевий тест

---

## 1. Хто споживач

| Репозиторій (Ubuntu-24.04) | Що це | Літературна робота зараз | Python-середовище |
|---|---|---|---|
| `floodstate-eo` | Paper 3 (щоденне затоплення, Kakhovka), Streamlit-дашборд; `main`, 190067b, 2026-10-02 | `p100_literature_theses.py` (51 теза, `graph.json`/`graphml`), `p100b_open_citations.py`, `docs/references.bib` (147 записів), `literature_audit/` (скопійовано з knoweledg_graf) | `.venv` 3.12.3: `httpx 0.28.1`, `requests`, без pydantic |
| `SWOT-DNIPRO` | Paper 1 (геометрія водної поверхні, вертикальна система); гілка `perf/phase20-bbox`, 2026-10-01 | `tools/phase7_literature.py` — ручний журнал WebSearch/Crossref; список літератури в рукописі v6 рукописний, з мітками `[EGG2015]` | `.venv` 3.12.3: `httpx`, `aiohttp`, `pydantic 2.13` |
| `kakhovka-terrain` | Paper 2 (DEM дна, рельєф, шорсткість); код не закомічений | `literature/theses.csv`→`theses.json`, `atomic_claims.yaml`, `kg/paper2_graph.json` (`p71_kg_export.py`), `kg_extraction_p74.md` («додати 9 PDF») | venv немає; ні `requests`, ні `httpx` |
| `icesat2-atl13-kakhovka` | ICESat-2 ATL13 → EGG2015 → EVRS | літератури немає | `.venv` зламаний (немає python) |

---

## 2. Як дані ходять зараз і як ходитимуть

```text
ЗАРАЗ                                                   ПІСЛЯ
knoweledg_graf ──wsl.exe cat references.bib──▶ (pull)   consumer ──HTTP 127.0.0.1:8090──▶ API
knoweledg_graf ──wsl.exe (snapshot.py)───────▶ (pull)   consumer ◀──JSON + provenance──── API
бандли дзеркаляться вручну в paper_*/publication/        consumer ──MCP (Claude Code)────▶ API
результати копіюються вручну назад:                     consumer ──POST /bundles/import──▶ Neo4j (MERGE, ns)
  literature_audit/ (~9 MB), 07c, 07d, citation_verification.md
```

Факти огляду:

- **Жоден споживач не імпортує** neo4j, chromadb, LLM-SDK і не має HTTP-клієнта до knoweledg_graf.
- **knoweledg_graf тягне файли сам через `wsl.exe`**: `tools/paper3_audit/bibtex.py` читає `docs/references.bib`, `src/paper_3/snapshot.py` знімає файли SWOT-DNIPRO та icesat.
- **У Ubuntu-24.04 knoweledg_graf не змонтований**: `/mnt/wsl` містить лише `resolv.conf`.
- **З усіх бандлів код читає лише `theses.json`** (`tools/paper3_audit/claims.load_theses`). `graph.json`, `graph.graphml`, `open_citations.json` і `paper2_graph.json` записують «для графа знань», але ніхто їх не завантажує.

---

## 3. Мережа (перевірено 2026-10-02)

Тимчасовий сервер слухав `127.0.0.1:8765` у дистрибутиві `Ubuntu`. Запити з `Ubuntu-24.04`:

| Адреса | Результат |
|---|---|
| `http://127.0.0.1:8765/` (curl) | **200** |
| `http://localhost:8765/` | **200** |
| httpx з `.venv` floodstate-eo | **200** |
| `http://172.21.103.154:8765/` (eth0) | 000 — очікувано, bind лише на loopback |

**Висновок**: обидва дистрибутиви ділять один мережевий простір. API слухає `127.0.0.1:8090`, споживачі ходять на `http://127.0.0.1:8090/v1`. Назовні порт не відкривається. Тестовий сервер зупинено, порт 8765 вільний.

---

## 4. Підключення споживача

```bash
# у .env репозиторію-споживача (не комітиться)
GHAI_API_URL=http://127.0.0.1:8090/v1
GHAI_API_KEY=<ключ споживача floodstate-eo>
```

**Клієнт** — `clients/python/ghai_client/`:

- Залежить лише від `httpx` (він є і в floodstate-eo, і в SWOT-DNIPRO). Pydantic — опційний: без нього відповіді повертаються як `TypedDict`.
- Встановлення: `uv pip install "git+ssh://git@github.com/NikoriakViktot/geohydroai-knowledge-graph.git#subdirectory=clients/python"`.
- Альтернатива для kakhovka-terrain без venv — один файл `ghai_client.py`, що віддається з `GET /v1/client.py`.

```python
from ghai_client import GHAI
api = GHAI.from_env()
job = api.ingest.submit(dois=["10.1029/2025gl120832"])
for ev in api.jobs.events(job.job_id):        # SSE
    print(ev.step, ev.status)
```

**MCP для Claude Code-сесій у репозиторіях-споживачах** (`.mcp.json` у корені репозиторію):

```json
{
  "mcpServers": {
    "ghai": {
      "type": "http",
      "url": "http://127.0.0.1:8090/mcp",
      "headers": { "X-API-Key": "${GHAI_API_KEY}" }
    }
  }
}
```

---

## 5. Що саме викликатимуть споживачі і що це замінює

| # | Виклик | Ендпоінт | Що замінює |
|---|---|---|---|
| 1 | key / DOI / paper_id → чи є в корпусі + канонічні метадані | `GET /papers/resolve`, `POST /papers/resolve-batch` | `citation_keys.yaml` (ручне зіставлення), `corpus_status` у `references_p74_status.csv`, `in_corpus` у `03_source_ledger.csv` |
| 2 | пакетна перевірка DOI | `POST /doi/verify` | нотатки «Crossref-verified …» в bib, `references_to_verify.md`, журнали `phase7_literature.py`, крок `references` аудиту (07/07b) |
| 3 | DOI → BibTeX у домашній конвенції (+ `verifiedon`) | `POST /bib/format` | `bib_delta` → 07d → `cat >> references.bib`, ручні VERIFY-записи |
| 4 | аудит `references.bib` | `POST /bib/audit` | `p100.bib_keys`, VERIFY-фільтр p100b, лічильник VERIFY у дашборді |
| 5 | «цитата Q / число N є в джерелі D?» | `POST /quotes/verify` | `citation_verification.md`, `quote_verified` у `02_thesis_evidence.csv` |
| 6 | речення рукопису ↔ цитовані ключі → VERIFIED / FIX / WORDING / OPEN | `POST /claims/check` (приймає елементи `open_citations.json` як є) | цикл p100b → ручна сесія |
| 7 | доказова база для тез (асинхронно) | `POST /theses/evidence-run` → `GET /jobs/{id}` | дзеркало → `paper3_literature_audit.py prepare…export` → копіювання назад |
| 8 | семантичний пошук із фільтрами | `POST /search/chunks` | 112 рядків `SEARCH_QUERY` з `graph.json`, таблиця «що витягти» з `kg_extraction_p74.md` |
| 9 | граф: цитування, сусіди, тема/когорта | `GET /graph/papers/{doi}…` | citation route аудиту; «бібліографія за темою» в дашборді |
| 10 | кандидати поза корпусом | `POST /discovery/search`, `/discovery/snowball` | 47 заглушок `?Placeholder` у `graph.json`, журнали WebSearch у SWOT-DNIPRO |
| 11 | додати в корпус (DOI або PDF) | `POST /ingest`, `/ingest/upload` | «add 9 PDFs» у `kg_extraction_p74.md`, git-ignored PDF у `literature_audit/_work/` |
| 12 | метрики й числа з одиницями та цитатою | `POST /metrics/extract`, `GET /metrics/facts` | `kakhovka_numbers.csv`, крок «перевірити числа» |
| 13 | завантажити граф бандла в Neo4j (MERGE, простір імен статті) | `POST /bundles/import` (задача) | невикористані `graph.json`, `open_citations.json`, `paper2_graph.json` |
| 14 | відрендерити список літератури з ключів або тексту рукопису | `POST /bib/render` | заглушка References (L1217), `final_article.run()`, рукописний список Paper 1, `fmt_ref` |
| 15 | витягти цитування з рукопису: входження → ключ → речення → слова в лапках; відсутні ключі, нецитовані записи | `POST /manuscripts/citations` | `p100b.citing_sentences`, `revisions_for_template.md` |
| 16 | текст лише з перевірених доказів | `POST /generate/synthesis`, `/generate/related-work` | кроки 08/09 (`revise`) аудиту |

Ендпоінти 1 (batch), 13, 14 і 15 додано до каталогу в [03_API_DESIGN.md §3.11](03_API_DESIGN.md).

---

## 6. Єдині контракти даних (API приймає лише їх і валідує)

| Контракт | Поля (скорочено) | Проблема, яку закриває |
|---|---|---|
| `Thesis` | `{ns, id, section, category, thesis, quantitative, tables[], needs, refs:[{key, relation, status}], search_queries[], priority}` | **У kakhovka-terrain `theses.json` — сирий CSV-дамп** (`refs`, `search_queries` як рядки). `load_theses` мовчки дає 0 refs, а запити розбирає на 107 односимвольних рядків (`('K','a','k')`) — **баг у `tools/paper3_audit/claims.py`**. API повертає `422` з переліком полів |
| `ns` (простір імен) | `floodstate-eo:paper3`, `kakhovka-terrain:paper2`, `swot-dnipro:paper1` | ID тез збігаються між статтями (`TH-INT-01` в обох) |
| `AtomicClaim` | схема `atomic_claims.yaml` v1.1.0: `{thesis_id, atomic_id, statement, required_roles, key_terms_primary, key_terms_support[[…]], negative_terms, extra_queries, counterevidence_queries}` | уже є; формально описати JSON Schema |
| `BibEntry` | ключ `Surname_YYYY` (`Wilson_Sader_2002`, `UNOSAT_3616_2023`, `Paper1_Nikoriak_2026`), `doi` (нормалізований), `verification: {status, method, verified_on, by}` | зараз статус перевірки записаний вільним текстом у `note`, регістр полів змішаний (`DOI=` / `doi=`) |
| `KeyAlias` | `{canonical_key, aliases[]}` | дрейф ключів між статтями: `Roberts_2017` ↔ `Roberts_2017_blockCV`, `Zanaga_2022` ↔ `Zanaga_2022_WorldCover` |
| `CitationOccurrence` | `{ns, key, section, sentence, quoted[], doi, source_paper_id}` | цитування в рукописах — вільний текст «(Surname et al. YYYY)», без `[@key]`; у Paper 1 з комою «(Kadam et al., 2024)» |
| `GraphBundle` | `{producer, ns, nodes[{id, type, …}], edges[{source, target, relation, …}]}` з префіксами ID | три несумісні формати (p100, p100b, `p71`); у Neo4j пишеться під міткою `:Bundle*` з `ns`, лише MERGE |

JSON Schema кожного контракту віддає `GET /v1/schemas/{name}`. Споживач може валідувати файл ще до відправки.

---

## 7. Міграція по репозиторіях (порядок)

1. **floodstate-eo** (найбільше ручної роботи):
   - `p100b_open_citations.py` → після генерації `open_citations.json` викликати `POST /quotes/verify` і `/claims/check` та записати статуси у файл.
   - `p100_literature_theses.py` → `POST /theses/validate` + `/bundles/import`.
   - `apps/dashboard/lib.py::_parse_bib` → `POST /bib/audit`.
   - Три копії bib-парсера → один клієнтський виклик.
2. **kakhovka-terrain**:
   - Створити venv із `httpx`.
   - `theses.csv` → `POST /theses/validate`: схема ловить CSV-дамп ще до аудиту.
   - «Додати 9 PDF» → `POST /ingest` (DOI) або `/ingest/upload`.
3. **SWOT-DNIPRO**:
   - `tools/phase7_literature.py` → `POST /doi/verify` + `/discovery/search` з provenance замість ручних записів.
   - Рукописний список літератури → `POST /bib/render` з `.bib`, який ще треба створити (зараз .bib немає).
4. **knoweledg_graf**: прибрати `wsl.exe`-витягування з `bibtex.py`/`snapshot.py`. Бандли приходять через `POST /bundles/import` або `POST /bib/audit` з тілом файлу. Ручне дзеркалення в `paper_*/publication/` скасовується.

---

## 8. Обмеження, про які споживач має знати

- **Gemini-квота спільна**: близько 480 запитів на день на лейн. Усе, що використовує LLM (`/claims/check` пакетом, `/theses/*`, `/generate/*`, `/discovery/screen`), стає задачею з чергою. `429 QUOTA_EXHAUSTED` повертається з `Retry-After`.
- **`retrieval_validity: UNVALIDATED`** означає, що «0 знайдено» не можна писати в статтю як прогалину. Ворота Paper 3 зараз **NOT READY**: recall контролів 7/23 = 30 %.
- **Когорта `paper_3`** — це 225 DOI, які спеціально зібрали під тези Paper 3 (139 у корпусі); це **не** власні статті авторів. Звичайний пошук її включає. Ендпоінти, що рахують поширеність або знаменники, виключають її за замовчуванням, як `recount.py`, бо цільовий гарвест змістив би частки.
