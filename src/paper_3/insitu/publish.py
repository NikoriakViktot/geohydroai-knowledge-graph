"""INSITU_README.md — what each CSV is, the datum statement, the flag legend,
the 2023 gaps verbatim, and where the tables feed the manuscript."""
from __future__ import annotations

from pathlib import Path

from src.paper_3.insitu.tables import Extracted
from src.paper_3.insitu.values import FLAG_MEANING

README = "INSITU_README.md"

WHAT = {
    "stations": "Station registry: post number, water body, type, distances, zero of post "
                "and height system, opening date, 2023 status, agency (№230 p146; №229 p10).",
    "datum_corrections": "«Поправка для приведення до єдиного нуля моря, см» per coastal "
                         "station (№229 p10) — the offset that puts a station's zero on the "
                         "unified sea zero.",
    "station_descriptions": "Verbatim post descriptions with the lifted code, address, "
                            "«Відмітка нуля поста» line, observation times and level "
                            "instruments (№230 pp149–158; №229 pp12–27).",
    "data_gaps": "Verbatim sentences stating why and when a post stopped or degraded in "
                 "2023, tagged by cause keyword; from the observation notes, every table "
                 "footnote, and the station list's status column.",
    "daily_levels_2023": "№230 Таблиця 2.1.1 — daily mean water level, cm above the zero of "
                         "post, one row per post-day, flags kept verbatim; `height_m` = zero "
                         "of post + level/100 (height system as stated per post).",
    "level_monthly_2023": "The «Середн. / Вищ. / Нижч.» rows at the foot of each 2.1.1 "
                          "table — monthly mean, highest and lowest daily level.",
    "level_summary_2023": "The «Період» block under each 2.1.1 table: annual mean, highest / "
                          "lowest with dates and counts, and the long-term extremes.",
    "sea_levels_2023": "№229 Табл. 1.1.1 — daily sea level at Очаків (hourly-based) and "
                       "Парутине (two-term), same columns as daily_levels_2023 plus `basis`.",
    "sea_level_summary_2023": "Monthly «сер. міс. / макс. / мінім.» rows and the annual "
                              "figures under each 1.1.1 table.",
    "level_frequency_2023": "№230 Таблиця 2.1.2 — count of days per 20-cm level interval per "
                            "month, with % and exceedance %.",
    "sea_level_stats_2023": "№229 Табл. 1.1.2 — level gradations and the statistics row "
                            "(mean, range, s.d., skewness, kurtosis with standard errors).",
    "hazard_events_2023": "№230 Додаток Табл. 1.6.1 — hazardous events in prose, incl. the "
                          "6–16 June 2023 breach wave; level / exceedance / duration are "
                          "regex-lifted from the description and blank when absent.",
    "hazard_thresholds_2023": "№230 Таблиця 2.1.3 — critical marks per post and each flood / "
                              "low-water / surge / set-down / ice-jam event with date, "
                              "relative magnitude and duration.",
    "surges_2023": "№229 Табл. 1.1.3 — surge / set-down events at the coastal stations "
                   "against their critical marks.",
    "overview_2023": "№230 «Огляд гідрометеорологічних умов» (pp184–195) paragraph by "
                     "paragraph, verbatim; `subsection = dam_breach` from «Вплив підриву "
                     "Каховської ГЕС на рівні води» (pp193–195).",
    "narrative_facts_2023": "Every numeric statement in the dam-breach subsection (cm, m БС, "
                            "km³, %, hh:mm) with its sentence — the hourly chronology "
                            "(04:20 06.06 → 08.06 1068 cm = 5.68 m БС → 16.06 20:00 649 cm) "
                            "that the tables do not carry.",
}

FEEDS = (
    ("stations, station_descriptions, datum_corrections",
     "3.3 Gauge records; 4.1 Vertical harmonisation; 5.1 The gauge network in one geodetic frame",
     "V1.1, V1.3"),
    ("daily_levels_2023, level_summary_2023",
     "5.4 Kherson as a local anchor; 4.2 Gauge branch", "V1.1, V4.1"),
    ("sea_levels_2023, sea_level_summary_2023, sea_level_stats_2023",
     "4.6 Spatial and temporal matchup (liman boundary)", "—"),
    ("hazard_events_2023, hazard_thresholds_2023, surges_2023",
     "2.2 The lower Dnipro and Kherson; 7.3 Seiches, wind setup", "—"),
    ("data_gaps", "3.3 Gauge records (data availability); Limitations", "V1.1"),
    ("overview_2023, narrative_facts_2023",
     "2.2 The lower Dnipro and Kherson; 5.4 Kherson as a local anchor (breach chronology, "
     "Kherson 1068 cm = 5.68 m БС; reservoir 16.79 m БС → −7.73 m in 6 days)", "V1.3, V10.2"),
)


def render(manifest: dict, results: dict[str, Extracted]) -> str:
    L: list[str] = []
    L += ["# In-situ gauge data, Dnipro–Buh estuary, 2023", "",
          f"Extracted {manifest['extracted_at']} · commit `{manifest.get('git_commit') or 'unset'}` "
          f"· `python -m src.paper_3.insitu`", "",
          "Source: two volumes of «Щорічні дані про режим та ресурси вод морів і морських "
          "гирл річок», 2023 (ГМЦ ЧАМ, ДСНС України). Every row carries `source_volume` "
          "and `page` (PDF page index, 1-based) and the raw cell text, so any number can "
          "be traced to a printed page.", ""]
    L += ["| volume | file | sha256 |", "|---|---|---|"]
    for vid, s in manifest["sources"].items():
        L.append(f"| {s['label']} | `{s['file']}` | `{s['sha256'][:16]}…` |")
    L += ["", "## Datum statement", "",
          "* Levels are **centimetres above the zero of post**. For every estuary post "
          "the zero is **−5.000 m БС-77** (Baltic height system 1977); Олександрівка "
          "(Південний Буг) is −3.02 m БС-77 (re-levelled to the state network in 2009).",
          "* The seas volume states that its levels are «приведені до „Єдиний нуль поста“ "
          "(−5.000 м у БС)», and lists a per-station **correction to a unified sea zero** "
          "(`datum_corrections.csv`: Очаків 321 cm, Парутине 440 cm).",
          "* `height_m` = zero of post + level/100, in the post's stated system. It is "
          "**not** an EVRF2019 or ellipsoidal height; the BS-77 → EVRF2019 step is the "
          "manuscript's EPSG:9902 operation, not this table.",
          "* The yearbook's «м. Нова Каховка, 80802, ГП-ІІ» is the **tailwater** post 2.5 km "
          "below the dam (zero −5.000 m). It is not the manuscript's reservoir station "
          "80977 Nova Kakhovka (zero +12.000 m); confusing them is a 17 m blunder.", ""]
    L += ["## Value flags", "",
          "Kept verbatim in `flags` (legend-documented) and `undocumented_flags` (everything "
          "else). Meanings are assigned **only** to what the volumes document:", "",
          "| flag | meaning | documented where |", "|---|---|---|"]
    where = {"?": "legend, №229 p8 / №230 p11", "!": "legend, №229 p8",
             "/": "footnote under 2.1.1 Херсон, №230 p162",
             "*": "per-page footnote marker"}
    for f, m in FLAG_MEANING.items():
        L.append(f"| `{f}` | {m} | {where[f]} |")
    L += ["", "Suffixes such as `Ш`, `Z`, `І` and a trailing `-` on winter values are ice-"
          "condition marks that neither volume explains; they are preserved and not "
          "interpreted. The seas volume's «ОПИС ПОСТІВ … 2020 РОЦІ» heading is a stale "
          "label inside a 2023 volume.", ""]

    L += ["## Files", "", "| file | rows | what |", "|---|---:|---|"]
    for name, t in manifest["tables"].items():
        L.append(f"| `{t['csv']}` | {t['rows']} | {WHAT.get(name, '')} |")
    L += ["", "## Checks", "", "| check | result | detail |", "|---|---|---|"]
    for name, c in manifest["checks"].items():
        L.append(f"| {name} | {'PASS' if c['passed'] else 'FAIL'} | {c['detail']} |")
    if manifest.get("anomalies"):
        L += ["", "### Parser anomalies (nothing dropped silently)", ""]
        for name, items in manifest["anomalies"].items():
            L.append(f"- **{name}** ({len(items)}):")
            for it in items[:25]:
                L.append(f"  - {it}")
            if len(items) > 25:
                L.append(f"  - … {len(items) - 25} more in the manifest")

    gaps = results.get("data_gaps")
    if gaps and gaps.rows:
        L += ["", "## 2023 observation gaps — verbatim", "",
              "Each line is a sentence from the yearbook, unchanged; the tag is a keyword "
              "category, not an interpretation.", ""]
        for r in gaps.rows:
            L.append(f"- **{r['post_names']}** [{r['cause_categories']}] "
                     f"({r['source_volume']} p{r['page']}): «{r['verbatim_quote']}»")

    L += ["", "## Where this feeds the manuscript", "",
          "| tables | draft sections | claim ids |", "|---|---|---|"]
    for t, sec, ids in FEEDS:
        L.append(f"| {t} | {sec} | {ids} |")
    L += ["", "Claim ids are still `UNKNOWN` in `src/paper_3/v2/scientific_claim_status.yaml`; "
          "this extraction is evidence *for* the audit and changes no status.", ""]
    return "\n".join(L)


def write(out_dir: Path, manifest: dict, results: dict[str, Extracted]) -> Path:
    path = Path(out_dir) / README
    path.write_text(render(manifest, results), encoding="utf-8")
    return path
