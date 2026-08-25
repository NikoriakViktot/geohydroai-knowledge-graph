# Код-ревю GeoHydroAI: Підсумковий звіт

**Версія**: 1.0 | **Дата**: 2026-06-11
**Підстава**: повний аудит `src/` (34 пакети, 226 Python-файлів, ~55 216 рядків), `tests/` (22 файли, 606 тестів), верифікація кожної критичної знахідки прямим читанням коду
**Рев'юер**: Claude (архітектурне + наукове ревю)
**Документи**: див. [INDEX.md](INDEX.md)

---

## 1. Загальна оцінка

**Зрілість кодової бази: 6.5 / 10** — функціональна, продумана в ядрі, але з суттєвим технічним боргом на периферії та критичними прогалинами в тестуванні й науковій валідації.

| Вимір | Оцінка | Коментар |
|-------|--------|----------|
| Архітектура ядра (SDOM, document/) | 8/10 | Чистий дизайн, чіткі контракти, але порушений інваріант immutability |
| Дотримання власних інваріантів | 5/10 | 5 порушень lxml/fitz, дубльований Neo4j writer з `CREATE`, TEIDocument не frozen |
| Наукова валідність екстракції | 5.5/10 | NSE/Kappa з неправильними діапазонами, наївні одиниці вимірювання, LLM judge без ground truth |
| Тестове покриття | 3/10 | 22 тест-файли на 226 модулів; orchestration/graph/extraction/enrichment — 0 тестів |
| Безпека / конфігурація | 4/10 | Hardcoded пароль Neo4j у 6 місцях, особисті credentials у коді |
| Спостережуваність (observability) | 4/10 | 55 мовчазних `except Exception` лише в callbacks.py; print/logging змішані |
| Готовність як "навчальна бібліотека" | 5/10 | Сильна модель даних, але дублікати пайплайнів і stub-пакети заплутають читача |

---

## 2. Що зроблено добре (чесно — це сильні сторони)

1. **SDOM (`src/document/`)** — справді гарна абстракція: `TEIDocument` як єдиний доменний об'єкт, ієрархія парсерів (TEI/Nougat/Hybrid/Router), merge-policy задокументована. Це рівень дизайну, який рідко зустрічається в дослідницькому коді.
2. **Оркестрація** — дворівнева ідемпотентність (фільтр перед створенням Ray-задачі + guard всередині задачі), sliding-window concurrency, явне керування пам'яттю (`del` + `gc.collect()`).
3. **Тестова інфраструктура** — conftest.py з мокованими акторами: 606 тестів проходять без GPU/Ollama/Ray. Те, що покрито — покрито якісно.
4. **Граф** — основний `GraphWriter` справді MERGE-only; деструктивний wipe ізольований за явним прапорцем `--wipe` (`graph_constraints.py:72-74`, `build_graph.py:94-97`).
5. **Pydantic-схема нормалізації** — `normalized_paper.py` з закритими enum і інваріантом `canonical_id`, версіонування схеми.
6. **Evidence-grounding** — MetricFact-шар та принцип "не вигадувати метрики з topic keywords" вбудовані в дизайн.

---

## 3. Топ-10 проблем (за пріоритетом)

| # | Проблема | Severity | Де | Деталі |
|---|----------|----------|-----|--------|
| 1 | `TEIDocument` — **не** frozen, хоча CLAUDE.md та docstring декларують immutability; роутер мутує об'єкт напряму | **CRITICAL** (контракт) | `models.py:235`, `parser_router.py:179-181, 238-240` | [01](01_REVIEW_document_sdom.md#f-doc-1) |
| 2 | NSE приєднано до R²-патернів як "same 0–1 scale" — **науково невірно**: NSE ∈ (−∞, 1] | **CRITICAL** (наука) | `regex_extractor.py` (R2_PATTERNS) | [02](02_REVIEW_ingestion_extraction.md#f-ext-1) |
| 3 | Hardcoded пароль `python2024` (6 файлів) і особистий username/email (4 файли) як дефолти в коді | **HIGH** (безпека) | `graph/`, `graphstore/`, `dashboard_dash/`, `config/`, `actors/` | [04](04_REVIEW_graph_layer.md#f-gr-2) |
| 4 | Дубльований Neo4j writer `src/graphstore/` використовує `CREATE` — порушує інваріант MERGE-only | **HIGH** | `graphstore/neo4j_writer.py:431` | [04](04_REVIEW_graph_layer.md#f-gr-1) |
| 5 | 5 порушень інваріанту ізоляції lxml/fitz поза document-шаром | **HIGH** | `tei_validator.py:15`, `nougat_region_pipeline.py:50,54`, `pdf_triage.py`, `pdf_reader.py` | [02](02_REVIEW_ingestion_extraction.md#f-ing-1) |
| 6 | ~90% коду без тестів: orchestration, graph, extraction, enrichment, dashboard — 0 тестів | **HIGH** | `tests/` | [08](08_TESTING_AUDIT.md) |
| 7 | LLM judge без ground-truth валідації; `normalize_judge_verdict` мовчки "лагодить" невалідний вивід — немає сигналу про деградацію | **HIGH** (наука) | `judge_stage.py`, `validation/judge_normalizer.py` | [02](02_REVIEW_ingestion_extraction.md#f-ext-3) |
| 8 | `callbacks.py`: 1 963 рядки, 55 `except Exception` із мовчазними дефолтами | **MEDIUM** | `dashboard_dash/callbacks.py` | [07](07_REVIEW_dashboard_misc.md) |
| 9 | Немає retry/health-check для Ray-акторів; падіння актора = вічний hang на `ray.get()` | **MEDIUM** | `orchestration/pipeline_runner.py` | [03](03_REVIEW_orchestration.md) |
| 10 | 5 stub-пакетів (`embedding`, `analysis`, `assembly`, `tasks`, `utils`) + дубльована логіка legacy vs SDOM пайплайнів | **MEDIUM** | `src/` | [07](07_REVIEW_dashboard_misc.md) |

**Виправлена помилкова гіпотеза**: попередній автоматичний аналіз стверджував наявність bare `except:` у `geo_stage.py` та `ontology_matcher.py` — перевірка grep'ом показала, що **bare except у кодовій базі відсутні**. Всі обробники — `except Exception` (проблема не в синтаксисі, а в мовчазному поглинанні).

---

## 4. Головні ризики для наукової вірності

1. **Метрики з обрізаними діапазонами**: NSE та Kappa можуть бути від'ємними; нормалізований екстрактор [0–1] відкидає валідні від'ємні значення → системний bias у бік "хороших" результатів у корпусі.
2. **Одиниці без розмірного аналізу**: `_UNIT` regex покриває лише m/cm/mm/m³/s; RMSE у km, ft, % або безрозмірний не розрізняються → NumericFact-вузли з непорівнянними величинами.
3. **Judge без еталону**: невідома точність LLM-судді на жодній розміченій вибірці; defensive-нормалізація приховує деградацію моделі.
4. **Keyword-банки замість онтології**: study-type/methods визначаються статичними списками рядків, а не KB з 1 118 сутностей — два джерела істини, що розходяться.

---

## 5. Структура звіту

Повний план виправлень: [09_REMEDIATION_PLAN.md](09_REMEDIATION_PLAN.md) (5 фаз, ~4–6 тижнів).
План датасету для навчання моделі: [10_TRAINING_DATASET_PLAN.md](10_TRAINING_DATASET_PLAN.md).
Технічні ревю по групах модулів: документи 01–08 (англійською, за конвенцією AUDIT_v1).
