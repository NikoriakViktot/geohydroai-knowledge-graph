# Аудит paper_my — Науковий та технічний аналіз

**Версія**: 2.0 | **Дата**: 2026-06-09  
**Аналізовані файли**: `paper_my/generated_paper_v4_V2_final.md`, `paper_my/evidence_report_v4.md`, `paper_my/v1_augmented_diff_report.md`, `paper_my/sections_v4/`, `paper_my/references_v4_V2_final.bib`  
**Поточна стаття**: "Satellite-Based Flood Mapping Under Operational and Data Constraints: Implications for Eastern Europe and Ukraine"

---

## 1. Як формується стаття (технічний процес)

### 1.1 Pipeline генерації

```
Corpus (3,875 PDFs)
    │
    ▼
GeoHydroAI pipeline (paper.json + entities + metrics)
    │
    ▼
evidence_augmentation_v4.py
    ├── RAG query per section (ChromaDB semantic search)
    ├── DuckDB query (papers, metrics, entities)
    ├── Neo4j query (graph evidence — DISABLED в v4)
    └── generate_paper_v4.py
         ├── theses_v4.json (102 тези T1–T102)
         ├── section_evidence_pack_v4.json (5.7MB)
         └── Gemini-2.5-flash synthesis
              │
              ▼
         sections_v4/{section_name}.md (27 секцій)
              │
              ▼
         generated_paper_v4_V2_final.md (151KB)
```

### 1.2 Версії статті

| Версія | Файл | Розмір | Дата |
|--------|------|--------|------|
| V1 baseline | `generated_paper.md` | 122KB | 2026-05-23 |
| V3 | `generated_paper_v3.md` | 89KB | 2026-05-25 |
| V4 initial | `generated_paper_v4.md` | 102KB | 2026-05-28 |
| V4 + V2 | `generated_paper_v4_V2.md` | 151KB | 2026-06-02 |
| V4 + V2 clean | `generated_paper_v4_V2_clean.md` | 149KB | 2026-06-02 |
| **V4 + V2 final** | `generated_paper_v4_V2_final.md` | **151KB** | 2026-06-02 |
| V4 + V3 full | `generated_paper_v4_V3_full.md` | 156KB | 2026-06-02 |

### 1.3 Augmentation statistics (v1_augmented_diff_report.md)

- Original V1 word count: **21,825**
- Augmented V2 word count: **23,204** (+6.3%)
- Theses total: **102** (T1–T102)
- Present and cited: 44 (43%)
- **Present but needs citation: 54 (53%)** ← критична проблема
- Generated paragraphs: 3 / subsections: 2 / sentence expansions: 4
- New references added: **46** (до початкового BIB)

---

## 2. Структура статті

### 2.1 Поточна структура (V4 final)

```
Title: Satellite-Based Flood Mapping Under Operational and Data Constraints

Abstract
1. Introduction
[UNNUMBERED] Methodology section  ← БАГ: немає заголовку розділу
[Section about optical flood]     ← немає номера
4.1 SAR Sensor Capabilities       ← підрозділ без батьківського розділу
[DEM/hydraulic section]           ← без номера
[ML/DL section]                   ← без номера
[Accuracy/Validation section]
6.1 Methodological Considerations ← підрозділ Section 6?
7. Operational Timeliness         ← перестрибує до 7
...
References [1]–[127]
```

**Проблема**: структура нумерації зламана. Abstract посилається на секції 2–6 що не відповідають реальним номерам.

### 2.2 Зміст (27 секцій з sections_v4/)

abstract, introduction, sar_flood, optical_flood, ml_dl, dem_hydraulic, multi_sensor, accuracy_metrics, timeliness, tradeoff, transferability, uncertainty, physics_ai, physical_plausibility, validation, eastern_europe, methodology, rag_model, limitations, future_directions, conclusions, та _raw варіанти кожного.

---

## 3. Наукові проблеми

### 3.1 КРИТИЧНА: Методологічний розділ описує GeoHydroAI, а не PRISMA

**Підстава**: `generated_paper_v4_V2_final.md`, lines 48-65 (unnumbered section after Introduction)

Розділ методології статті починається з:
> "The initial corpus was constructed by identifying relevant publications through a targeted search strategy... Each retrieved PDF document underwent structural parsing using **GROBID** to convert them into **TEI XML** format... a Retrieval-Augmented Generation (RAG) system was utilized... This process generated `ExtractionResult` objects... A total of **1,118 distinct entities**, including methods, sensors, and geographical locations, were subjected to ontology normalization..."

**Проблема**: це опис системи GeoHydroAI, а не методологія наукового огляду. У рецензованому журналі методологічний розділ систематичного огляду має описувати:
- Бази даних пошуку (Scopus, WoS, Google Scholar)
- Пошукові запити (keywords, MeSH terms)
- Критерії включення/виключення (PRISMA flow diagram)
- Оцінка якості джерел
- Стратегія data extraction

**Замість цього стаття описує**: GROBID parsing, TEI XML, ExtractionResult objects, ontology normalization, CCS scores — це технічна реалізація системи, не наукова методологія огляду.

**Вплив**: при рецензуванні у будь-якому Q1 журналі (RSE, ISPRS, NHESS) це стане причиною reject без розгляду.

---

### 3.2 КРИТИЧНА: Посилання на внутрішні файли у тексті

**Підстава**: `generated_paper_v4_V2_final.md`, численні місця

Текст містить посилання вигляду:
- `(Figure F1)`, `(Figure F2/F10)`, `(Figure F4)`, `(Figure F6)`, `(Figure F7)`
- `Figure F4_temporal_method_evolution.png`
- `Figure F_CCS_claim_confidence_summary.png`
- `Figure F1 and Figure F6`
- `as illustrated in Figure F7`

**Проблема**: ці посилання — internal pipeline file names, не figure references журнальної статті. У рукописі мають бути `Fig. 1`, `Fig. 2` і т.д. Посилання "Figure F_CCS_claim_confidence_summary.png" є повна внутрішня назва PNG файлу.

**Вплив**: стаття не може бути відправлена у журнал з такими посиланнями.

---

### 3.3 КРИТИЧНА: Внутрішній концепт CCS у тексті статті

**Підстава**: lines 53-54 у методологічному розділі

> "To manage the complexity and potential variability of automatically extracted information, each synthesized claim is assigned a **Claim Confidence Score (CCS)**..."
> "The distribution of these scores across all claims is summarized in Figure F_CCS_claim_confidence_summary.png..."

**Проблема**: CCS — внутрішній артефакт pipeline, не науковий концепт. Читач журналу не має уявлення що це таке, і посилання на PNG файл з CCS є абсолютно недопустимим у публікації.

---

### 3.4 ВИСОКА: 54/102 тез без цитат (52%)

**Підстава**: `v1_augmented_diff_report.md` — "present_but_needs_citation: 54"

**Що це означає**: понад половина стверджень статті не мають підтверджуючих посилань. Це порушує базовий принцип наукової публікації — кожне нетривіальне твердження має бути підтверджено посиланням.

**Приклад**: стаття стверджує статистику (медіанний OA 89.7%, n=458) але в кількох місцях без посилань або з одиничними посиланнями.

---

### 3.5 ВИСОКА: Статистика з пошкодженого pipeline

**Підстава**: cross-reference між paper.json pipeline issues та claimed statistics

Стаття кількаразово стверджує:
- "corpus-wide median Overall Accuracy (OA) of 89.7%"
- "median F1-score is 80.0% (n=473)"
- "median IoU is 77.7% (P75=87.4%)"

Але extraction pipeline мав:
- `metric_text` без `methods` секції → NSE/RMSE ~0% coverage
- Section routing failure для 24% papers → entities/metrics missing
- PATCH 1 (accepted=True) застосовано заднім числом, але статистика могла бути розрахована до патчу

**Вплив**: наведена статистика може бути неповною або систематично зміщеною через відомі баги. Без чіткого опису провенансу (з якого підмножини papers, яка версія pipeline) ці числа не можна верифікувати.

---

### 3.6 ВИСОКА: Пошкоджені посилання

**Підстава**: `references_v4_V2_final.bib` / `generated_paper_v4_V2_final.md`

**Проблема 1**: Посилання [72] — порожній запис:
```
[72] Rummanmowla Mowla Chowdhury; Muhammad Mizanur Rahman; Muhammad Mizanur Rahaman (None). . https://doi.org/10.13140/RG.2.2.17111.14244
```
Рік: `None`, назва: `. ` (порожня) — це пошкоджений GROBID extraction артефакт.

**Проблема 2**: Посилання з `Null-` prefix у іменах авторів:
```
David C Mason; Rainer Speck; Bernard Devereux; Null- P Schumann; J C Neal; P D Bates (2010)
```
`Null-` — GROBID parsing артефакт для `<null>` або пропущеного поля.

**Проблема 3**: Надзвичайно довге посилання [69] (Bajracharya et al.) з 50+ авторами — очевидно, зборником чи звітом організації, де всі colaborators записані як автори.

---

### 3.7 СЕРЕДНЯ: Повторення статистики verbatim

**Підстава**: читання тексту

Наступний текст з'являється 3+ рази практично verbatim:
> "corpus-wide analysis shows a median Overall Accuracy (OA) of 89.7%"
> "Cohen's kappa values between 0.75 and 0.80 [Abinash Silwal et al. 2026; Mrinal Singha et al. 2020; El-Alaouy Nafia et al. 2022]"
> "Mann-Whitney U test (p=0.39) reveals no significant difference in OA between Eastern European and global studies"

**Вплив**: видає автоматичну генерацію окремих секцій без cross-section consistency check.

---

### 3.8 СЕРЕДНЯ: Невідповідність нумерації секцій у Abstract

**Підстава**: перший рядок Abstract

Abstract: "...Section 2 provides an overview... Section 3 details the various flood mapping methodologies... Section 4 discusses operational timeliness... Section 5 addresses key challenges... Section 6 concludes..."

Реальні секції: "7. Operational Timeliness" (не Section 4), структура секцій не відповідає опису в Abstract.

---

### 3.9 СЕРЕДНЯ: Несумісні формати цитування

**Підстава**: текст статті

Два формати використовуються:
- В тексті: `[Abinash Silwal et al. 2026; Mrinal Singha et al. 2020]` (author-year)
- В references: `[1]`, `[2]`, ... `[127]` (numbered)

Журнали зазвичай вимагають один стиль. Крім того, `Silwal et al. 2026` — 2026 рік, що є поточним роком публікації огляду (потенційна проблема: це preprint або recently published?)

---

### 3.10 СЕРЕДНЯ: Graph source disabled у evidence v4

**Підстава**: `evidence_report_v4.md` рядок 6: "Graph source: disabled"

Neo4j граф не використовувався при генерації v4 секцій (validation, transferability, multi_sensor, uncertainty, physics_ai, physical_plausibility, rag_model).

**Вплив**: evidence pack формувався тільки з DuckDB Parquet + ChromaDB. Це означає що якісний граф знань (57,603 вузлів, 92,003 CITES) не використовувався при генерації найновіших секцій.

---

### 3.11 СЕРЕДНЯ: Стаття описує власну методологію як перевагу

**Підстава**: methodology section

> "This approach systematically extracts, normalizes, and analyzes information, **moving beyond traditional manual literature reviews** to provide a quantitative and verifiable assessment of the field."

**Проблема**: система GeoHydroAI описується в тексті огляду як перевага автоматизованого огляду над ручним. Але система має задокументовані обмеження (24% papers без methods, NSE ~0%, graph disabled). Представляти систему з такими відомими обмеженнями як "verifiable" без їх розкриття є науково некоректним.

---

## 4. Технічні проблеми

### 4.1 Sections coverage в evidence_report_v4

```
Sections processed: 7 (з 21+)
Graph source: disabled
```

**Що значить**: тільки 7 секцій мали повний evidence pack у v4. Решта секцій (introduction, sar_flood, optical_flood, ml_dl, dem_hydraulic, eastern_europe, conclusions, etc.) генерувались у попередніх версіях з different evidence.

**Результат**: стаття — мозаїка секцій з різних pipeline версій (v3 та v4) без уніфікованого перегляду.

### 4.2 Claim Coverage Matrix

**Файл**: `thesis_v1_coverage_matrix.csv` (41KB), `claim_evidence_matrix_v4.json` (200KB)

Claim evidence matrix існує, але 54% тез без цитат (з v1_augmented_diff_report) — тобто навіть після augmentation pipeline не зміг знайти або вставити цитати для більшості тверджень.

### 4.3 Missing references pipeline

**Файл**: `missing_references_for_v1.csv` — 22KB

Є окремий pipeline для пошуку відсутніх посилань. Це показує що проблема з посиланнями відома і розробник намагається виправити, але не завершено.

### 4.4 Article 1 — паралельна версія статті

**Файл**: `Flood Inundation Mapping Using Satellite Remote Sensing_V4.md` (165KB)

Існує паралельна версія статті ("Article 1") що обробляється окремим пайплайном (`augment_pipeline.py`). Відношення між "generated paper" і "Article 1" неясне — можливо дві різні статті або дві версії однієї.

---

## 5. Що зроблено правильно

| Аспект | Оцінка |
|--------|--------|
| Обсяг огляду (3,875 papers) | ✅ Амбіційно і цінно |
| Evidence-grounded цитати (44/102 тез) | ✅ Краще ніж нуль |
| Corpus-level statistics (OA, F1, IoU) | ✅ Правильний підхід |
| SAR та optical sections якість тексту | ✅ Технічно грамотно |
| Mann-Whitney test для Eastern Europe | ✅ Статистично коректний підхід |
| DEM/hydraulic section деталізованість | ✅ Добре структуровано |
| NRT timeliness analysis (CEMS: 6.4h) | ✅ Конкретна evidence |
| 127 references загалом | ✅ Широке покриття |
| Multiple figure types (decision framework, Pareto) | ✅ Цінні візуалізації |

---

## 6. Пріоритетний список виправлень для публікації

### Рівень 1 — ОБОВ'ЯЗКОВО до відправки у журнал

| # | Проблема | Дія |
|---|----------|-----|
| **1** | Методологічний розділ описує GeoHydroAI pipeline | Переписати як PRISMA-compliant methodology: databases, search queries, inclusion criteria, quality assessment |
| **2** | Internal figure references (F1, F4_temporal..., F_CCS...) | Замінити на Fig. 1, Fig. 2, Fig. 3 etc. з proper captions |
| **3** | CCS (Claim Confidence Score) у тексті | Видалити, або переформулювати без internal pipeline terminology |
| **4** | 54 тези без цитат | Для кожного твердження знайти підтвердження у corpus або видалити твердження |
| **5** | Пошкоджені references ([72], Null-) | Виправити вручну або видалити |
| **6** | Несумісні citation formats (author-year vs numbered) | Уніфікувати відповідно до журнальних вимог |
| **7** | Section numbering inconsistency | Провести manual renumbering всіх секцій |

### Рівень 2 — РЕКОМЕНДОВАНО перед submission

| # | Проблема | Дія |
|---|----------|-----|
| **8** | Повторення statistics verbatim | Уніфікувати в одне місце (methodology або results table) |
| **9** | Abstract посилається на невірні section numbers | Оновити після renumbering |
| **10** | Pipeline limitations не розкриті | Додати limitations subsection про автоматичну extraction та її обмеження |
| **11** | Graph evidence disabled для v4 секцій | Або ввімкнути Neo4j для генерації, або документувати обмеження |
| **12** | Author надзвичайно довгих references [69] | Скоротити до "et al." |

### Рівень 3 — ДЛЯ SCIENTIFIC RIGOR

| # | Проблема | Дія |
|---|----------|-----|
| **13** | Статистика (OA 89.7%) з pipeline з відомими багами | Документувати версію pipeline, coverage, confidence intervals |
| **14** | "verifiable" claim для системи з відомими обмеженнями | Додати explicit statement про обмеження автоматичної extraction |
| **15** | Немає explicit PRISMA flow diagram | Створити Figure показуючи screening/inclusion/exclusion |
| **16** | Нема порівняння з існуючими ручними оглядами | Додати comparison з Bentivoglio 2022, Munawar 2022 review papers |

---

## 7. Порівняльна оцінка версій

### Прогрес від V1 до V4

| Аспект | V1 | V3 | V4 final |
|--------|----|----|---------|
| Word count | 21,825 | ~25,000 | ~23,200 |
| Reference count | ~80 | ~100 | 127 |
| Section count | ~10 | ~15 | 21+ |
| Evidence base | ChromaDB v1 | ChromaDB v1 | ChromaDB 768d |
| Graph integration | No | Partial | Disabled |
| Internal refs (F1, F4...) | Present | Present | **Still present** |
| Methodology section | GeoHydroAI | GeoHydroAI | **Still GeoHydroAI** |
| Uncited theses | Unknown | Unknown | 54/102 (53%) |
| Pipeline maturity | v3 | v4 | v5 patch |

**Висновок**: core scientific problems (methodology, internal refs, citation format) не виправлені між версіями.

---

## 8. Рекомендований план доопрацювання

### Фаза 1 — Структурне виправлення (1 тиждень)

```
1. Вибрати target journal (RSE / NHESS / Remote Sensing / IJDRR)
2. Скачати Author Guidelines
3. Реструктурувати секції відповідно до шаблону журналу
4. Перенумерувати секції
5. Уніфікувати citation format (IEEE або APA або numbered)
```

### Фаза 2 — Методологія (2-3 дні)

```
1. Написати справжній методологічний розділ:
   - Database: Scopus, Web of Science, Google Scholar
   - Search terms: "flood mapping" AND ("SAR" OR "remote sensing")
   - Date range: 2015-2026
   - Inclusion: peer-reviewed, flood mapping, quantitative results
   - Exclusion criteria
   - PRISMA flow diagram (не GeoHydroAI pipeline diagram)
2. Зберегти GeoHydroAI опис як Supplementary Material або Data Availability
```

### Фаза 3 — Посилання (1 тиждень)

```
1. Для кожної з 54 тез без цитат:
   a. Пошук у Scopus/GS
   b. Або видалення твердження
2. Виправити пошкоджені references (Null-, None year)
3. Перевірити рік 2026 papers (чи вони peer-reviewed або preprints)
4. Скоротити надзвичайно довгі author lists
```

### Фаза 4 — Figures (3-4 дні)

```
1. Замінити F1, F2, F4_temporal, F_CCS references
2. Призначити номери: Fig. 1–8
3. Написати proper captions
4. Видалити CCS figure з тексту або перетворити на supplementary
```

### Фаза 5 — Технічний рев'ю (1 тиждень)

```
1. Перевірити всі статистичні твердження на предмет posвилання
2. Перевірити Mann-Whitney test parameters та reporting
3. Перевірити наявність limitation section
4. Перевірити чи CCS/pipeline terminology видалено
```

---

## 9. Потенційні журнали для submission

| Журнал | IF | Відповідність |
|--------|-----|--------------|
| Remote Sensing of Environment | 13.5 | ⭐⭐⭐ (найкращий) |
| ISPRS Journal | 12.7 | ⭐⭐⭐ |
| International Journal of Disaster Risk Reduction | 5.4 | ⭐⭐ |
| Natural Hazards and Earth System Sciences | 4.0 | ⭐⭐ |
| Remote Sensing (MDPI) | 5.0 | ⭐⭐ (легший для review) |
| Hydrology and Earth System Sciences | 6.5 | ⭐⭐ |

**Рекомендація**: для першої публікації з автоматизованою методологією — Remote Sensing (MDPI) або NHESS, які більш відкриті до нових методів. RSE/ISPRS вимагатимуть дуже детального обґрунтування методу.

---

## 10. Резюме

| Категорія | Оцінка |
|-----------|--------|
| Наукова ідея (автоматизований огляд) | **9/10** — оригінально |
| Поточна готовність до submission | **2/10** — не готова |
| Технічна якість тексту (коли є) | **7/10** |
| Coverage corpus | **8/10** |
| Citation completeness | **4/10** |
| Методологічна прозорість | **2/10** |
| Reference quality | **5/10** |
| Figure/structure | **3/10** |

**Головне**: стаття має дуже цінне наукове ядро (автоматизований аналіз 3,875 статей, corpus statistics, Eastern Europe comparison) але **не готова до публікації**. Три критичні блокери: (1) методологія описує software pipeline замість PRISMA, (2) internal figure references, (3) 53% тез без цитат.
