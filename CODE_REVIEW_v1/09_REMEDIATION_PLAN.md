# План виправлень (Remediation Plan)

**Версія**: 1.0 | **Дата**: 2026-06-11
**Підстава**: знахідки документів 01–08 цього ревю
**Мета**: чиста архітектура + наукова вірність + придатність кодової бази як еталонної "бібліотеки для навчання"
**Загальна оцінка обсягу**: ~4–6 тижнів роботи одного інженера; фази незалежні й можуть виконуватись частково паралельно

---

## Принципи

1. **Спочатку наука, потім косметика** — помилки, що спотворюють корпус (NSE, одиниці, Evidence-дублікати), виправляються раніше за рефакторинг.
2. **Кожна фаза закінчується тестом, що фіксує виправлення** — без тесту виправлення вважається незавершеним.
3. **Жодних нових залежностей без потреби** (виняток: `pydantic-settings` — вже є Pydantic v2).

---

## Фаза 0 — Безпека та контракти (2–3 дні) 🔴 — ✅ ВИКОНАНО 2026-06-11 (крім 0.5-ротації)

| Крок | Дія | Файли | Знахідка |
|------|-----|-------|----------|
| 0.1 | Прибрати дефолт `python2024` з усіх 6 місць → єдине джерело `settings.NEO4J_PASSWORD` без дефолту (fail-fast із зрозумілим повідомленням) | `graph/neo4j_writer.py:40`, `graph/graph_statistics.py:39`, `graph/build_graph.py:53`, `graphstore/neo4j_writer.py:38`, `dashboard_dash/data_neo4j.py:27` | F-GR-2 |
| 0.2 | Прибрати особисті ідентифікатори: GeoNames username (3 місця), email OpenAlex → обов'язкові env vars + створити `.env.example` | `config/settings.py:34,53`, `ingestion/stages/geo_stage.py:329`, `actors/geonames_actor.py:28` | F-GR-2, F-EXT-6 |
| 0.3 | Виправити `Gemini_API_Key` → `GEMINI_API_KEY` | `config/settings.py:97` | F-CFG-1 |
| 0.4 | **Рішення по TEIDocument**: зробити `frozen=True` + `dataclasses.replace()` у роутері (+ `object.__setattr__` у `_rebuild_indexes`); прогнати 606 тестів | `document/models.py:235`, `document/parser_router.py:179-181,238-240` | F-DOC-1 |
| 0.5 | Ротувати пароль Neo4j після централізації (літерал лишається в git-історії) | `docker-compose.yml` | F-GR-2 |

**Критерій завершення**: повний тест-сьют зелений; grep на `python2024|viktornikoriak` у `src/` порожній.

**Статус виконання (2026-06-11)**:
- ✅ 0.1–0.3 — централізовано в `settings.py` (`NEO4J_URI/USER` + `require_env()` fail-fast); 6 Neo4j-споживачів, 2 GeoNames-сайти, OpenAlex email/api_key (тепер опційні, mailto не шлеться порожнім), `GEMINI_API_KEY` виправлено; створено `.env.example`; локальний `.env` мігровано.
- ✅ 0.4 — `TEIDocument` тепер `@dataclass(frozen=True)`; `_rebuild_indexes` через `object.__setattr__`; мутації в `parser_router.py` (2 місця) і `hybrid_parser.py:_annotate_grobid_only` замінені на `dataclasses.replace()`; мутація реального об'єкта в `test_parser_routing.py` виправлена (mock-мутації в test_10/test_12 не потребували змін — MagicMock).
- ✅ Критерії: **647 passed**; grep `python2024|viktornikoriak|Gemini_API_Key` у `src/` порожній; всі 12 змінених модулів імпортуються.
- ⏳ 0.5 (ротація пароля) — **ручний крок** (потребує живої БД і вибору нового пароля):
  ```bash
  docker compose up -d neo4j
  docker exec -it <neo4j-container> cypher-shell -u neo4j -p python2024 \
      "ALTER CURRENT USER SET PASSWORD FROM 'python2024' TO '<новий-пароль>'"
  # потім оновити NEO4J_PASSWORD у .env та NEO4J_AUTH у docker-compose.yml,
  # і прибрати згадки python2024 з CLAUDE.md / docs_v2
  ```

---

## Фаза 1 — Архітектурні інваріанти (4–5 днів) 🟠 — ✅ ВИКОНАНО 2026-06-11

| Крок | Дія | Знахідка |
|------|-----|----------|
| 1.1 | Створити `src/document/pdf_io.py` (рендеринг сторінок, text-probe, region crop) і `src/document/tei_io.py`; мігрувати 5 порушників (`tei_validator`, `nougat_region_pipeline`, `pdf_triage`, `pdf_reader`) | F-ING-1 |
| 1.2 | Влити Evidence-запис із `graphstore/neo4j_writer.py` у `graph/neo4j_writer.py` через `MERGE` з детермінованим ключем (hash від `paper_id+chunk_id+claim_id`); видалити `src/graphstore/`; оновити `pipeline/rag_pipeline.py:310`. **Перед цим** — разовий скрипт дедуплікації вже створених Evidence-вузлів у Neo4j | F-GR-1 |
| 1.3 | Додати `tests/test_import_invariants.py`: заборонені імпорти lxml/fitz; всі write-statements `src/graph/` починаються з MERGE | F-ING-1, F-GR-1 |
| 1.4 | Видалити stub-пакети: `src/tasks/` (застарілий дублікат), `src/embedding/`, `src/assembly/`; `src/analysis/method_clustering.py` → `analytics/`; `src/utils/logging_config.py` зробити єдиною точкою конфігурації логів | F-ORCH-4, F-DASH-стаби |
| 1.5 | Узгодити термінологію `canonical_id` (замість `ontology_id`) по всьому коду + глосарій у docs_v2/DATA_MODEL.md | F-NORM-4 |

**Критерій завершення**: `test_import_invariants.py` зелений; кількість top-level пакетів ≤ 28; в Neo4j немає дубльованих Evidence.

**Статус виконання (2026-06-11)**:
- ✅ 1.1 — `src/document/pdf_io.py` (probe_pdf/extract_pages_text/render_*_image) і `tei_io.py` (parse_string/parse_file/localname); мігровані всі 4 порушники; CLAUDE.md-інваріанти оновлені на фасадні.
- ✅ 1.2 — `src/graphstore/` видалено; fact-centric writer тепер `src/graph/fact_writer.py`: Evidence через `MERGE` з ключем `sha1(fact_id|chunk_id|field|text)` + unique constraint; `rag_pipeline.py` оновлено. Жива БД: Evidence-вузлів 0 — дедуплікація не знадобилась.
- ✅ 1.3 — `tests/test_import_invariants.py`: 5 тестів (lxml/fitz AST-скан, CREATE-вузли, DELETE поза wipe, graphstore не повертається).
- ✅ 1.4 — видалено `src/tasks/` (зламаний дублікат) і мертвий затінений `src/config.py` (друга поверхня конфігурації!); `analysis/method_clustering.py` → `analytics/`. **Корекції ревю**: `src/embedding/` НЕ stub (споживачі: retriever, rag_pipeline), `src/assembly/` НЕ stub (реалізує інваріант paper.json-із-SODB) — обидва лишаються. Пакетів: 27.
- ✅ 1.5 — **F-NORM-4 retracted**: `ontology_id` у коді не існує, `canonical_id` уніфікований (40 файлів). Додано глосарій ідентифікаторів у docs_v2/DATA_MODEL.md §10.
- Критерії: 652 passed (647 + 5 інваріантних); пакетів 27 ≤ 28; Evidence-дублікатів немає.

---

## Фаза 2 — Наукова вірність (5–7 днів) 🔴 (найвищий науковий пріоритет) — ✅ ВИКОНАНО 2026-06-11 (2.4-розмітка — за людиною)

| Крок | Дія | Знахідка |
|------|-----|----------|
| 2.1 | **Модель діапазонів метрик** у `metric_ontology`: `NSE/KGE: (−∞,1]`, `kappa: [−1,1]`, `R²: (−∞,1]`, `IoU/F1/OA: [0,1]`; sign-aware `_NUM`; out-of-range → `suspect`, а не drop | F-EXT-1 |
| 2.2 | **Ре-аудит корпусу**: повторна екстракція метрик по 3,5K paper.json; звіт скільки від'ємних NSE/Kappa було втрачено; оновити NumericFacts | F-EXT-1 |
| 2.3 | Розширити словник одиниць (km, ft, %, mm/day, cms, юнікодні `³`); політика `unit=unknown` → виключення з крос-paper порівнянь | F-EXT-2 |
| 2.4 | **Gold set**: 50–100 розмічених статей (study_type, country, rivers, task) + 200–300 пар mention→canonical_id; стратифікація за study_type. Звіт precision/recall judge та semantic-matcher | F-EXT-3, F-NORM-сем |
| 2.5 | Телеметрія: `repair_count` (judge_normalizer), `judge_used/failed` у registry, tier-downgrade у embedding_matcher | F-EXT-3, F-ORCH-3, F-NORM-2 |
| 2.6 | Keyword-банки генерувати з KB (aliases → компільовані патерни з word boundaries); regex лишити тільки для числових патернів | F-EXT-4 |

**Критерій завершення**: задокументовані precision/recall judge ≥ узгодженого порогу; звіт ре-аудиту метрик у `AUDIT_v1/`; gold set збережений (він же — ядро тренувального датасету, див. [10](10_TRAINING_DATASET_PLAN.md)).

**Статус виконання (2026-06-11)**:
- ✅ 2.1 — `METRIC_RANGES` модель: NSE/KGE/R² ∈ (−∞,1], Kappa/MCC/correlation ∈ [−1,1], bias/PBIAS знакові; `RATIO_METRICS` очищено від знакових; `validate_metric_value()` → ok/suspect (не дроп); sign-aware патерни у ВСІХ трьох шляхах (regex_extractor, scientific_extractor, KB entity_extractor); NSE/KGE — окремі pattern-families (видалені з R2_PATTERNS); свідома відмова "рятувати" неможливі значення (NSE=1.7 ≠ 1.7%). 34+18 регресійних тестів у `tests/test_15_metric_ranges.py`.
- ✅ 2.2 — ре-аудит 4,852 paper.json (`scripts/reaudit_negative_metrics.py` + CSV): **36 від'ємних значень у 25 статтях втрачались** (Bias 20, PBIAS 13, KGE 2, NSE 1; реальні приклади: NSE −1.22/−1.33). Чесний висновок: масштаб менший за гіпотезу ревю — головна цінність fix'а forward-looking. Звіт: `AUDIT_v1/NEGATIVE_METRICS_REAUDIT.md`.
- ✅ 2.3 — словник одиниць розширено (km/ft/%/mm-day/cms/юнікод ³ → канонізація `normalize_unit`); політика `unit=unknown` + `units_comparable()` (unknown ніколи не порівнюється).
- ✅ 2.5 — телеметрія: `normalizer_repairs` у кожному вердикті (через `_warn`-хелпер, 22 repair-сайти) → provenance → агрегація і деградаційний поріг у pipeline_runner; tier-downgrade Counter в embedding_matcher → снапшот у normalization_runner.
- ✅ 2.6 — `src/extraction/kb_keywords.py`: KB-аугментація `_detect_satellites` (152 сенсори, \b-патерни, denylist технологічних acronym'ів) + `audit_static_banks_vs_kb()` (29 static-сутностей відсутні в KB — план збагачення). **Чесне обмеження**: KB не кодує SAR/optical і місії Sentinel-1/-2 — curated-банки лишаються ядром типізації.
- ⏳ 2.4 — інфраструктура готова (`src/evaluation/goldset_sampler.py`, seed=42): `goldset_judge_template.csv` (107 статей, стратифіковано: case_study 82/review 10/решта по 5) + `goldset_linking_template.csv` (217 унікальних пар mention→canonical_id). **Розмітка true_*-колонок — ручний крок**; після неї `evaluator.py` рахує precision/recall.
- Критерії: 704 passed (652 + 52 нових); звіт у AUDIT_v1 ✅; gold set шаблони збережені ✅.

---

## Фаза 3 — Стійкість і тести (5–6 днів) 🟠

| Крок | Дія | Знахідка |
|------|-----|----------|
| 3.1 | Тести `regex_extractor`/`table_extractor` (параметризовані, включно з "NSE = −0.27") | F-EXT-1, 08 |
| 3.2 | Retry-хелпер (exponential backoff) + `ray.get(timeout=...)` + `max_restarts=1` на акторах + `atexit ray.shutdown()` | F-ORCH-1, 2, 6 |
| 3.3 | Тести ідемпотентності оркестрації (mock Ray); тести graph writer (mock driver, MERGE-ключі, wipe недосяжний без прапорця) | F-GR-3, 08 |
| 3.4 | Тести enrichment (mock OpenAlex: кеш, 429, DOI-нормалізація); unit-тести `merge_ontology` (ідемпотентність, без злиття між типами) | F-NORM-1, 3 |
| 3.5 | Fixtures зіпсованих входів: обрізаний JSON (реальний кейс 12 Elsevier-файлів), битий parquet, порожній TEI | 08 |
| 3.6 | `pytest-cov` gate 35% (ratchet), `mypy --strict` для `document/`, `schemas/`, `extraction/` | 08 |

---

## Фаза 4 — Спостережуваність і dashboard (4–5 днів) 🟡

| Крок | Дія | Знахідка |
|------|-----|----------|
| 4.1 | Розбити `callbacks.py` (1,963 LOC) на пакет по сторінках; декоратор `@safe_callback` — лог + видима помилка на графіку замість `return {}` | F-DASH-1 |
| 4.2 | `skipped_inputs.parquet` в analytics; validators ніколи не ковтають помилки | F-ANA-1 |
| 4.3 | Logging замість print по всій базі (print тільки в `__main__`); єдиний `logging_config` | F-ANA-2, F-DOC-4 |
| 4.4 | Dashboard → Neo4j тільки через API `src/graph` | F-DASH-2 |
| 4.5 | Звіт розбіжностей у `kg_synchronizer` | F-GR-4 |

---

## Фаза 5 — Документація та "навчальна бібліотека" (3–4 дні) 🟢

| Крок | Дія |
|------|-----|
| 5.1 | Оновити CLAUDE.md/docs_v2: інваріанти мають відповідати коду (frozen TEIDocument — після 0.4 це стане правдою) |
| 5.2 | `pydantic-settings` міграція конфігурації з валідацією діапазонів |
| 5.3 | Розбити `nougat_region_pipeline.py` (1,083 LOC) на 3 модулі; за бажанням — `registry_db.py` |
| 5.4 | ADR-файли (Architecture Decision Records) для ключових рішень: чому два пайплайни, чому DuckDB, merge-policy Hybrid — це і є "навчальний" шар бібліотеки |
| 5.5 | Тип-хінти до 95% у `scientific_extractor.py`, `section_extractor.py` |

---

## Що свідомо НЕ входить у план

- Переписування legacy Ray-пайплайну на SDOM-пайплайн — це окремий проєкт; план лише прибирає дублікати та фіксує спільні точки.
- Заміна Dash на інший фреймворк — невиправдано.
- 100% тестове покриття — нереалістично; цільові пороги задані по-пакетно.

## Підсумкова послідовність

```
Фаза 0 (безпека/контракти) ──► Фаза 1 (інваріанти) ──► Фаза 2 (наука) ──► Фаза 3 (тести/стійкість)
                                                            │
                                                            └──► живить 10_TRAINING_DATASET_PLAN (gold set)
Фази 4–5 — паралельно з 2–3 за наявності ресурсу
```
