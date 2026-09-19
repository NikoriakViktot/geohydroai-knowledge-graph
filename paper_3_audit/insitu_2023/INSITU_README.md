# In-situ gauge data, Dnipro–Buh estuary, 2023

Extracted 2026-09-17T08:23:06+00:00 · commit `c3ec671` · `python -m src.paper_3.insitu`

Source: two volumes of «Щорічні дані про режим та ресурси вод морів і морських гирл річок», 2023 (ГМЦ ЧАМ, ДСНС України). Every row carries `source_volume` and `page` (PDF page index, 1-based) and the raw cell text, so any number can be traced to a printed page.

| volume | file | sha256 |
|---|---|---|
| Інв. №229 — Том 2 (моря) | `Інв_№229_Щорічні_дані_про_режим_та_ресурси_вод_морів_і_морських.pdf` | `05ffef8454abc93a…` |
| Інв. №230 — Том 2(2) (морські гирла річок) | `Інв_№230_Щорічні_дані_про_режим_та_ресурси_вод_морів_і_морських.pdf` | `bf74c22a69bb0f08…` |

## Datum statement

* Levels are **centimetres above the zero of post**. For every estuary post the zero is **−5.000 m БС-77** (Baltic height system 1977); Олександрівка (Південний Буг) is −3.02 m БС-77 (re-levelled to the state network in 2009).
* The seas volume states that its levels are «приведені до „Єдиний нуль поста“ (−5.000 м у БС)», and lists a per-station **correction to a unified sea zero** (`datum_corrections.csv`: Очаків 321 cm, Парутине 440 cm).
* `height_m` = zero of post + level/100, in the post's stated system. It is **not** an EVRF2019 or ellipsoidal height; the BS-77 → EVRF2019 step is the manuscript's EPSG:9902 operation, not this table.
* The yearbook's «м. Нова Каховка, 80802, ГП-ІІ» is the **tailwater** post 2.5 km below the dam (zero −5.000 m). It is not the manuscript's reservoir station 80977 Nova Kakhovka (zero +12.000 m); confusing them is a 17 m blunder.

## Value flags

Kept verbatim in `flags` (legend-documented) and `undocumented_flags` (everything else). Meanings are assigned **only** to what the volumes document:

| flag | meaning | documented where |
|---|---|---|
| `?` | doubtful | legend, №229 p8 / №230 p11 |
| `!` | from_frequent_observations | legend, №229 p8 |
| `/` | dam_breach_distortion | footnote under 2.1.1 Херсон, №230 p162 |
| `*` | footnote_marker | per-page footnote marker |

Suffixes such as `Ш`, `Z`, `І` and a trailing `-` on winter values are ice-condition marks that neither volume explains; they are preserved and not interpreted. The seas volume's «ОПИС ПОСТІВ … 2020 РОЦІ» heading is a stale label inside a 2023 volume.

## Files

| file | rows | what |
|---|---:|---|
| `stations.csv` | 12 | Station registry: post number, water body, type, distances, zero of post and height system, opening date, 2023 status, agency (№230 p146; №229 p10). |
| `datum_corrections.csv` | 2 | «Поправка для приведення до єдиного нуля моря, см» per coastal station (№229 p10) — the offset that puts a station's zero on the unified sea zero. |
| `station_descriptions.csv` | 12 | Verbatim post descriptions with the lifted code, address, «Відмітка нуля поста» line, observation times and level instruments (№230 pp149–158; №229 pp12–27). |
| `daily_levels_2023.csv` | 2120 | №230 Таблиця 2.1.1 — daily mean water level, cm above the zero of post, one row per post-day, flags kept verbatim; `height_m` = zero of post + level/100 (height system as stated per post). |
| `level_monthly_2023.csv` | 211 | The «Середн. / Вищ. / Нижч.» rows at the foot of each 2.1.1 table — monthly mean, highest and lowest daily level. |
| `level_summary_2023.csv` | 14 | The «Період» block under each 2.1.1 table: annual mean, highest / lowest with dates and counts, and the long-term extremes. |
| `sea_levels_2023.csv` | 730 | №229 Табл. 1.1.1 — daily sea level at Очаків (hourly-based) and Парутине (two-term), same columns as daily_levels_2023 plus `basis`. |
| `sea_level_summary_2023.csv` | 78 | Monthly «сер. міс. / макс. / мінім.» rows and the annual figures under each 1.1.1 table. |
| `level_frequency_2023.csv` | 71 | №230 Таблиця 2.1.2 — count of days per 20-cm level interval per month, with % and exceedance %. |
| `sea_level_stats_2023.csv` | 23 | №229 Табл. 1.1.2 — level gradations and the statistics row (mean, range, s.d., skewness, kurtosis with standard errors). |
| `hazard_events_2023.csv` | 25 | №230 Додаток Табл. 1.6.1 — hazardous events in prose, incl. the 6–16 June 2023 breach wave; level / exceedance / duration are regex-lifted from the description and blank when absent. |
| `hazard_thresholds_2023.csv` | 26 | №230 Таблиця 2.1.3 — critical marks per post and each flood / low-water / surge / set-down / ice-jam event with date, relative magnitude and duration. |
| `surges_2023.csv` | 9 | №229 Табл. 1.1.3 — surge / set-down events at the coastal stations against their critical marks. |
| `overview_2023.csv` | 163 | №230 «Огляд гідрометеорологічних умов» (pp184–195) paragraph by paragraph, verbatim; `subsection = dam_breach` from «Вплив підриву Каховської ГЕС на рівні води» (pp193–195). |
| `narrative_facts_2023.csv` | 18 | Every numeric statement in the dam-breach subsection (cm, m БС, km³, %, hh:mm) with its sentence — the hourly chronology (04:20 06.06 → 08.06 1068 cm = 5.68 m БС → 16.06 20:00 649 cm) that the tables do not carry. |
| `data_gaps.csv` | 24 | Verbatim sentences stating why and when a post stopped or degraded in 2023, tagged by cause keyword; from the observation notes, every table footnote, and the station list's status column. |

## Checks

| check | result | detail |
|---|---|---|
| stations.v230_station_count | PASS | 10 estuary posts listed on p146 (expected 10) |
| stations.zero_of_post_minus_5 | PASS | zeros: {'Каховська ГЕС': -5.0, 'Нова Каховка': -5.0, 'Херсон': -5.0, 'Касперівка': -5.0, 'Олександрівка': -3.02, 'Миколаїв': -5.0, 'Парутине': -5.0, 'Станіслав': -5.0, 'Геройське': -5.0, 'Очаків': -5.0} |
| stations.oleksandrivka_zero | PASS | Олександрівка zero -3.02 |
| datum_corrections.unified_zero_corrections | PASS | {'Очаків': 321.0, 'Парутине': 440.0} |
| station_descriptions.manuscript_kherson_80805 | PASS | Херсон code 80805, zero -5.0 m БС-77 — manuscript [V1.1, V1.3] state 80805 at −5.000 m BS-77 |
| station_descriptions.nova_kakhovka_is_tailwater_post_80802 | PASS | yearbook Нова Каховка code 80802 (ГП-ІІ, 2.5 km below the dam) — NOT the manuscript's reservoir station 80977 (+12.000 m) |
| daily_levels_2023.monthly_consistency_kherson | PASS | 11 months compared; all within 1 cm of «Середн.» |
| daily_levels_2023.extremes_kherson | PASS | undistorted daily range 448–552 cm within table 432–566 cm |
| daily_levels_2023.monthly_consistency_kasperivka | PASS | 6 months compared; all within 1 cm of «Середн.» |
| daily_levels_2023.monthly_consistency_oleksandrivka | PASS | 12 months compared; all within 1 cm of «Середн.» |
| daily_levels_2023.extremes_oleksandrivka | PASS | undistorted daily range 322–444 cm within table 320–515 cm |
| daily_levels_2023.monthly_consistency_mykolaiv | PASS | 11 months compared; all within 1 cm of «Середн.» |
| daily_levels_2023.extremes_mykolaiv | PASS | undistorted daily range 407–542 cm within table 378–580 cm |
| daily_levels_2023.monthly_consistency_ochakiv | PASS | 11 months compared; all within 1 cm of «Середн.» |
| daily_levels_2023.extremes_ochakiv | PASS | undistorted daily range 452–514 cm within table 444–525 cm |
| daily_levels_2023.monthly_consistency_stanislav | PASS | 3 months compared; all within 1 cm of «Середн.» |
| daily_levels_2023.monthly_consistency_parutyne | PASS | 11 months compared; all within 1 cm of «Середн.» |
| daily_levels_2023.extremes_parutyne | PASS | undistorted daily range 441–530 cm within table 414–546 cm |
| daily_levels_2023.kherson_june_dam_breach_flagged | PASS | 7 of 12 June values carry «/» |
| daily_levels_2023.kherson_june_exceeds_may | PASS | June max 1059 cm vs May max 542 cm |
| daily_levels_2023.nova_kakhovka_absent | PASS | 0 daily rows (yearbook: data absent — occupation, dam destroyed) |
| sea_levels_2023.monthly_consistency_ochakiv | PASS | all 12 monthly means within 1 cm of «сер. міс.» |
| sea_levels_2023.monthly_consistency_parutyne | PASS | all 12 monthly means within 1 cm of «сер. міс.» |
| level_frequency_2023.frequency_posts | PASS | 5 posts with frequency rows: Миколаїв, Олександрівка, Очаків, Парутине, Херсон |
| level_frequency_2023.frequency_pct_sums_Миколаїв | PASS | percentages sum to 100.00 |
| level_frequency_2023.frequency_pct_sums_Олександрівка | PASS | percentages sum to 100.00 |
| level_frequency_2023.frequency_pct_sums_Очаків | PASS | percentages sum to 100.00 |
| level_frequency_2023.frequency_pct_sums_Парутине | PASS | percentages sum to 100.00 |
| level_frequency_2023.frequency_pct_sums_Херсон | PASS | percentages sum to 100.00 |
| sea_level_stats_2023.sea_stats_posts | PASS | posts: ['Очаків', 'Парутине'] |
| hazard_events_2023.june_breach_events | PASS | 8 June-2023 event rows across 7 posts |
| hazard_thresholds_2023.threshold_posts | PASS | posts: ['Касперівка', 'Миколаїв', 'Очаків', 'Парутине', 'Станіслав', 'Херсон'] |
| surges_2023.surge_posts | PASS | posts: ['Очаків', 'Парутине'] |
| overview_2023.breach_subsection_found | PASS | «ВПЛИВ ПІДРИВУ…» on p193 |
| narrative_facts_2023.narrative_datum_consistent | PASS | 3 «см (м БС)» pairs; all satisfy −5.000 + cm/100 |
| narrative_facts_2023.narrative_max_kherson | PASS | narrative max 1068 cm found; June daily-mean max 1059 cm ≤ it |
| narrative_facts_2023.narrative_max_mykolaiv | PASS | narrative max 602 cm found; June daily-mean max 596 cm ≤ it |
| narrative_facts_2023.narrative_max_parutyne | PASS | narrative max 599 cm found; June daily-mean max 595 cm ≤ it |
| narrative_facts_2023.narrative_max_ochakiv | PASS | narrative max 573 cm found; June daily-mean max 569 cm ≤ it |
| data_gaps.gap_records | PASS | 24 verbatim gap records for 8 posts |
| data_gaps.gap_mentions_Нова Каховка | PASS | Нова Каховка named in a gap record |
| data_gaps.gap_mentions_Геройське | PASS | Геройське named in a gap record |
| data_gaps.gap_mentions_Станіслав | PASS | Станіслав named in a gap record |
| data_gaps.gap_mentions_Касперівка | PASS | Касперівка named in a gap record |
| data_gaps.gap_mentions_Херсон | PASS | Херсон named in a gap record |

## 2023 observation gaps — verbatim

Each line is a sentence from the yearbook, unchanged; the tag is a keyword category, not an interpretation.

- **Касперівка** [access_ban;relocated] (v230_mouths p147): «на посту Касперівка з 03.08.2023 заборонено ЗСУ доступ до основного місця спостережень, розпочато морські спостереження на тимчасовому місці (оз»
- **Геройське** [equipment_destroyed;suspended;closed] (v230_mouths p147): «в Геройському з 18.09.2022 морські спостереження припинено через руйнування причалу з водомірною рейкою та установки СРВ, до 01.05.2023 проводились тільки метеорологічні спостереження, з 01.05.2023 спостереження були тимчасово призупинені, а 31.12.2023 МГП-І Геройське закрито згідно Наказу УкрГМЦ від 13.10.2023 року №НС-83/99»
- **Станіслав** [access_ban;equipment_destroyed;evacuation;mining;suspended] (v230_mouths p147): «на посту Станіслав морські спостереження тимчасово призупинено з 11.05.2023 у зв'язку з мінуванням берегової лінії, забороною ЗСУ наближатись до поста, пошкодженням житла та евакуацією гідрометеоспостерігача в безпечне місце»
- **Нова Каховка** [dam_breach;occupation] (v230_mouths p147): «Дані про середньодобові рівні води по ГП-II Нова Каховка, про середньодобові витрати води та коротка характеристика режиму роботи Каховської ГЕС відсутні у зв'язку з окупацією території та підривом греблі Каховської ГЕС. Метеорологічні дані у щорічнику також представлені не в повному обсязі»
- **Миколаїв;Очаків** [shelling;suspended] (v230_mouths p147): «З 24.02.2022 було призупинено проведення спостережень на АМСЦ Миколаїв, метеомайданчик було розгорнуто на території Миколаївського ЦГМ. На станції МГ-ІІ Очаків внаслідок постійних обстрілів метеорологічні спостереження виконувались за скороченою програмою в світлу частину доби»
- **Миколаїв;Очаків** [suspended;data_absent] (v230_mouths p147): «Через відсутність даних в повному обсязі розрахунок середньодобових та середньомісячних значень температури повітря та швидкості вітру не проводився»
- **Херсон** [data_absent] (v230_mouths p147): «З 24.02.2022 дані по АМСЦ Херсон відсутні, використовувались метеорологічні дані по автоматичному метеорологічному комплексу американської компанії Campbell Scientific, що встановлено на агрометеорологічній станції Херсон»
- **Очаків** [relocated] (v230_mouths p148): «МСЧ. Перенесення спостережень за льодовими явищами на більш пізній час проводиться при поганій видимості»
- **Геройське** [equipment_destroyed] (v230_mouths p148): «На МГП-І Геройське у звʼязку з поломкою вказівника швидкості вітру на флюгері Вільда, швидкість вітру вимірювалась ручним анемометром АРІ – 49»
- **Геройське** [suspended] (v230_mouths p168): «На МГП-І Геройське з 18.09.2022 року спостереження за рівнем води призупинено»
- **Геройське** [closed] (v230_mouths p168): «з 31.12.2023 року пост закритий.»
- **Станіслав** [suspended] (v230_mouths p171): «На МГП-І Станіслав з 11.05 у звʹязку з військовими діями спостереження тимчасово припинені»
- **Нова Каховка** [occupation;suspended] (v230_mouths p171): «Дані про середньодобові рівні по ГП-ІІ Нова Каховка (р.Дніпро) за 2023 рік відсутні у звʹязку з окупацією території та знищенням Каховської ГЕС. Роботу поста призупинено»
- **Геройське** [occupation;suspended] (v230_mouths p171): «На МГП-І Геройське з 18.09.2022 спостереження за рівнем води припинено у звʹязку з військовими діями та окупацією території»
- **Геройське** [suspended] (v230_mouths p171): «З 01.05.2023 року роботу поста призупинено»
- **Геройське** [occupation;data_absent] (v230_mouths p160): «ПРИМІТКА: Дані про рівні води на МГП-I Геройське відсутні через окупацію лівобережної частини Херсонської області»
- **Геройське** [shelling;equipment_destroyed;suspended;closed] (v230_mouths p160): «На МГП-I Геройське з 18.09.2022 спостереження за рівнем води припинені - під час артилерійського обстрілу зруйновано причал з футштоком та установку СРВ. З 31.12.2023 пост закритий.»
- **Геройське** [shelling;equipment_destroyed;suspended] (v230_mouths p172): «Примітка : На МГП-I Геройське з 18.09.2022 спостереження за рівнем води припинені - під час артилерійського обстрілу зруйновано причал з футштоком та установку СРВ»
- **Геройське** [equipment_destroyed;suspended] (v230_mouths p172): «з 01.05.2023 тимчасово призупинено роботу поста через руйнування»
- **Станіслав** [evacuation;suspended] (v230_mouths p172): «Спостереження на МГП-l Станіслав з 11.05.2023 тимчасово призупинено в зв'язку з евакуацією гідрометеоспостерігача.»
- **Каховська ГЕС** [status_column] (v230_mouths p146): «Період дії: діючий до 10.2022»
- **Нова Каховка** [status_column] (v230_mouths p146): «Період дії: діючий до 07.2022»
- **Станіслав** [status_column] (v230_mouths p146): «Період дії: діючий до 10.05.2023»
- **Геройське** [status_column] (v230_mouths p146): «Період дії: закритий 31.12.2023»

## Where this feeds the manuscript

| tables | draft sections | claim ids |
|---|---|---|
| stations, station_descriptions, datum_corrections | 3.3 Gauge records; 4.1 Vertical harmonisation; 5.1 The gauge network in one geodetic frame | V1.1, V1.3 |
| daily_levels_2023, level_summary_2023 | 5.4 Kherson as a local anchor; 4.2 Gauge branch | V1.1, V4.1 |
| sea_levels_2023, sea_level_summary_2023, sea_level_stats_2023 | 4.6 Spatial and temporal matchup (liman boundary) | — |
| hazard_events_2023, hazard_thresholds_2023, surges_2023 | 2.2 The lower Dnipro and Kherson; 7.3 Seiches, wind setup | — |
| data_gaps | 3.3 Gauge records (data availability); Limitations | V1.1 |
| overview_2023, narrative_facts_2023 | 2.2 The lower Dnipro and Kherson; 5.4 Kherson as a local anchor (breach chronology, Kherson 1068 cm = 5.68 m БС; reservoir 16.79 m БС → −7.73 m in 6 days) | V1.3, V10.2 |

Claim ids are still `UNKNOWN` in `src/paper_3/v2/scientific_claim_status.yaml`; this extraction is evidence *for* the audit and changes no status.
