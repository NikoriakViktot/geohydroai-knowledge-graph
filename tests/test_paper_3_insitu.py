"""In-situ yearbook extraction — the parsing logic, without a PDF.

Three rules pinned here: flags are kept verbatim and only legend-documented
ones get a meaning; a page missing its caption fails loudly; the grid
reconstruction is a pure function over word boxes.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.paper_3.insitu import grid, sources, values
from src.paper_3.insitu.tables import Extracted, descriptions, gaps, stations

SOURCE_PDFS = Path("/mnt/f/data_kakhovka_dem_swot/Sea_post_data")
HAVE_PDFS = SOURCE_PDFS.exists() and any(SOURCE_PDFS.glob("*№230*.pdf"))


# ── values ────────────────────────────────────────────────────────────────────

def test_dam_breach_and_doubtful_flags_are_named():
    c = values.parse_cell("799?/")
    assert c.value == 799
    assert c.flags == "?/"
    assert c.meanings == ("doubtful", "dam_breach_distortion")
    assert c.undocumented_flags == ""


def test_ice_suffixes_are_kept_but_not_interpreted():
    for raw, expected in (("511Ш", "Ш"), ("470 Z", "Z"), ("489  І", "І"), ("465-", "-")):
        c = values.parse_cell(raw)
        assert c.value in (511, 470, 489, 465)
        assert c.undocumented_flags == expected
        assert c.meanings == ()


def test_footnote_marker_and_frequent_observation_flag():
    assert values.parse_cell("511*").meanings == ("footnote_marker",)
    assert values.parse_cell("525!").meanings == ("from_frequent_observations",)


def test_not_observed_and_empty_cells():
    assert values.parse_cell("нб").not_observed
    assert values.parse_cell("").empty
    assert values.parse_cell(None).empty
    assert not values.parse_cell("нб").empty


def test_decimal_comma_is_accepted():
    assert values.parse_cell("0,69").value == pytest.approx(0.69)


def test_height_is_zero_plus_level():
    assert values.to_height_m(546, -5.0) == pytest.approx(0.46)
    assert values.to_height_m(None, -5.0) is None


# ── sources ───────────────────────────────────────────────────────────────────

def test_zero_of_post_parses_both_volumes_spellings():
    assert sources.parse_zero("Відмітка нуля поста - 5,00 м БС - 77")[:2] == (-5.0, "БС-77")
    assert sources.parse_zero("Відмітка нуля поста -5.000 м (БС)")[:2] == (-5.0, "БС")
    assert sources.parse_zero("-3,02 м БС-77")[0] == -3.02
    assert sources.parse_zero("no zero here")[0] is None


def test_detect_post_prefers_the_longest_alias_and_ignores_agencies():
    assert sources.detect_post("р. Дніпро - м. Херсон").name == "Херсон"
    assert sources.detect_post("ГП-ІІ Нова Каховка").name == "Нова Каховка"
    assert sources.detect_post("рук. Рвач с. Кізомис МГП-1 Касперівка").name == "Касперівка"
    assert sources.detect_post("Миколаївський ЦГМ") is None, "an agency is not the post"
    assert sources.detect_post("р. Південний Буг - м. Миколаїв!").name == "Миколаїв"


def test_missing_caption_is_a_page_map_error():
    with pytest.raises(sources.PageMapError, match="p162"):
        sources.assert_caption("Таблиця 2.1.2 something else", "2.1.1", "v230_mouths", 162)
    sources.assert_caption("Таблиця  2.1.1", "2.1.1", "v230_mouths", 162)   # no raise


def test_find_pdfs_names_what_is_missing(tmp_path):
    (tmp_path / "Інв_№229_x.pdf").write_bytes(b"%PDF")
    with pytest.raises(FileNotFoundError, match="№230"):
        sources.find_pdfs(tmp_path)


# ── grid ──────────────────────────────────────────────────────────────────────

def _w(x0, y1, text, width=12.0, height=9.0):
    return (x0, y1 - height, x0 + width, y1, text)


def test_body_rows_cluster_by_baseline_and_assign_by_column():
    bounds = [(85, 131), (131, 167), (167, 196), (196, 235)]
    words = [
        _w(106, 193.8, "1"), _w(143, 192.7, "519"), _w(176, 192.7, "504"),
        _w(210, 193.8, "509"),
        _w(106, 206.3, "2"), _w(143, 205.2, "470"), _w(157, 205.2, "Z"),   # two words, one cell
        _w(210, 206.3, "511"),
    ]
    rows = grid.body_rows(words, y_top=182, y_bottom=639, bounds=bounds)
    assert [r["cells"] for r in rows] == [["1", "519", "504", "509"],
                                          ["2", "470 Z", "", "511"]]
    assert all(not r["orphans"] for r in rows)


def test_words_between_columns_are_reported_not_dropped():
    bounds = [(85, 131), (167, 196)]
    rows = grid.body_rows([_w(106, 193, "1"), _w(145, 193, "lost")], 0, 300, bounds)
    assert rows[0]["cells"] == ["1", ""]
    assert rows[0]["orphans"] == ["lost"]


def test_column_bounds_use_only_the_bottom_row_of_a_merged_header():
    table = {"bbox": (85, 155, 542, 182),
             "cells": [(85, 155, 131, 182), (131, 155, 542, 169),
                       (131, 169, 167, 182), (167, 169, 196, 182)]}
    assert grid.column_bounds(table) == [(85, 131), (131, 167), (167, 196)]


# ── stations (stubbed page) ───────────────────────────────────────────────────

class _StubPages:
    def __init__(self, tables=None, words=None, text=""):
        self._t, self._w, self._x = tables or {}, words or {}, text
    def tables(self, volume, page, strategy="lines"):
        return self._t.get(page, [])
    def words(self, volume, page):
        return self._w.get(page, [])
    def text(self, volume, page):
        return self._x if isinstance(self._x, str) else self._x.get(page, "")
    def check_span(self, span):
        pass


def test_station_list_carries_water_body_forward_and_parses_zero():
    rows = [["№", "Назва", "Назва станції", "Відстань", "", "Відмітка", "Період", "", "Відомча", "Номери", "Форма"],
            ["", "", "", "вершини", "морського", "", "відкритий", "закритий", "", "", ""],
            ["4", "р. Дніпро", "м. Херсон", "65", "28", "-5,00 м БС-77", "13.10.1896", "діючий", "Миколаївський\nЦГМ", "2.1.1", "ЦГМ"],
            ["6", "", "рук. Рвач\nс. Кізомис\nМГП-1\nКасперівка", "91", "2", "-5,00 м БС-77", "01.01.1941\n01.09.1948", "07.1941\nдіючий", "- « -", "", ""],
            ["7", "р.Південний Буг", "с. Олександрівка", "-", "132", "-3,02 м БС-77", "14.05.1923", "діючий", "", "", ""]]
    table = {"bbox": (0, 0, 1, 1), "rows": rows, "cells": [], "row_count": 5, "col_count": 11}
    out, anomalies = stations.parse_v230(_StubPages(tables={146: [table]}), 146)
    assert anomalies == []
    assert [r["post_name"] for r in out] == ["Херсон", "Касперівка", "Олександрівка"]
    assert out[1]["water_body"] == "р. Дніпро", "blank water body carries forward"
    assert out[1]["post_type"] == "МГП-І"
    assert out[0]["zero_of_post_m"] == -5.0 and out[0]["height_system"] == "БС-77"
    assert out[2]["zero_of_post_m"] == -3.02
    assert out[1]["opened"] == "01.01.1941"


# ── descriptions (stubbed text) ───────────────────────────────────────────────

DESC = """ОПИС ПОСТІВ ТА СТАНЦІЙ
м. Херсон, р Дніпро, 80805, МГП-І.
Адреса: Одеська площа, 81, м. Херсон.
Гідрологічна рейка встановлена на пристані. Строки спостережень – 06 та 18 год. (UTC).
Відмітка нуля поста – 5,000 м БС – 77.
с. Касперівка, р.Дніпро, рук. Рвач, 80807, МГП-І.
Адреса: вул. Касперівська, 206, с. Кізомис.
У 1973 р. почалися регулярні спостереження за рівнем моря по СРВ.
Строки спостережень за рівнем і температурою води о 06 і 18 годині (UTC).
Відмітка нуля поста – 5,000 м БС – 77.
"""


def test_descriptions_split_on_station_code_headers(monkeypatch):
    pc = _StubPages(text={149: DESC})
    monkeypatch.setattr(descriptions, "span",
                        lambda v, t: sources.PageSpan(v, t, 149, 149, ""))
    rows, skipped, anomalies = descriptions.parse_volume(pc, "v230_mouths")
    assert [r["post_name"] for r in rows] == ["Херсон", "Касперівка"]
    assert rows[0]["code"] == "80805" and rows[0]["post_type"] == "МГП-І"
    assert rows[0]["zero_of_post_m"] == -5.0
    assert rows[0]["address"].startswith("Одеська площа")
    assert "СРВ" in rows[1]["level_instruments"]
    assert rows[1]["obs_times_utc"] == "06,18"
    assert rows[0]["text"].startswith("м. Херсон, р Дніпро, 80805")
    assert "Касперівка" not in rows[0]["text"]


# ── gaps ──────────────────────────────────────────────────────────────────────

NOTES = ("Протягом 2023 року через повномасштабне вторгнення рф на територію України та "
         "підрив греблі Каховської ГЕС виникали перебої у роботі постів та станцій. "
         "На посту Касперівка з 03.08.2023 заборонено ЗСУ доступ до основного місця "
         "спостережень, розпочато морські спостереження на тимчасовому місці. "
         "Дані про середньодобові рівні води по ГП-II Нова Каховка відсутні у зв'язку з "
         "окупацією території та підривом греблі Каховської ГЕС.")


def test_gap_records_are_verbatim_sentences_with_keyword_tags():
    recs = gaps.records_from(NOTES, "v230_mouths", 147, "observation_notes")
    by_post = {r["post_names"]: r for r in recs}
    kasp = by_post["Касперівка"]
    assert kasp["from_date"] == "2023-08-03"
    assert "access_ban" in kasp["cause_categories"]
    assert kasp["verbatim_quote"].startswith("На посту Касперівка з 03.08.2023")
    nk = by_post["Нова Каховка"]
    assert {"occupation", "dam_breach", "data_absent"} <= set(nk["cause_categories"].split(";"))
    assert all(r["verbatim_quote"] in NOTES for r in recs), "quotes are substrings of the source"


def test_gap_sentence_without_a_post_is_ignored_unless_a_default_is_given():
    text = "Внаслідок постійних обстрілів спостереження проводились один раз на добу."
    assert gaps.records_from(text, "v230_mouths", 147, "notes") == []
    got = gaps.records_from(text, "v230_mouths", 162, "table_footnote", default_post="Херсон")
    assert got and got[0]["post_names"] == "Херсон"


# ── manifest refusal ──────────────────────────────────────────────────────────

def test_changed_source_bytes_are_refused_without_force(tmp_path):
    from src.paper_3.insitu import manifest as m
    (tmp_path / m.MANIFEST).write_text(json.dumps(
        {"sources": {"v230_mouths": {"sha256": "old"}}}), encoding="utf-8")
    with pytest.raises(RuntimeError, match="changed"):
        m.assert_sources_unchanged(tmp_path, {"v230_mouths": {"sha256": "new"}})
    m.assert_sources_unchanged(tmp_path, {"v230_mouths": {"sha256": "new"}}, force=True)
    m.assert_sources_unchanged(tmp_path, {"v230_mouths": {"sha256": "old"}})


# ── integration (only when the yearbooks are mounted) ─────────────────────────

@pytest.mark.skipif(not HAVE_PDFS, reason="yearbook PDFs not mounted")
def test_full_extraction_against_the_real_yearbooks(tmp_path):
    import pandas as pd

    from src.paper_3.insitu.__main__ import run

    manifest = run(pdf_dir=SOURCE_PDFS, out_dir=tmp_path / "out",
                   source_dir=tmp_path / "src")
    failed = [k for k, c in manifest["checks"].items() if not c["passed"]]
    assert failed == [], failed

    daily = pd.read_csv(tmp_path / "out" / "daily_levels_2023.csv")
    counts = daily.groupby("post_name").size()
    # Херсон: 365 − 18 (13–30 June, recorder well flooded) − 8 (1–8 July) = 339,
    # exactly the blank cells on p162; the yearbook, not the calendar, is the truth.
    assert counts["Херсон"] == 339
    assert counts["Миколаїв"] == counts["Очаків"] == counts["Парутине"] == 365
    assert "Нова Каховка" not in counts.index
    june = daily[(daily.post_name == "Херсон") & daily.date.str.startswith("2023-06")]
    # `daily.flags` would hit the DataFrame's own `flags` attribute, not the column.
    assert june["flags"].fillna("").str.contains("/").sum() >= 5

    corr = pd.read_csv(tmp_path / "out" / "datum_corrections.csv").set_index("post_name")
    assert corr.loc["Очаків", "unified_sea_zero_correction_cm"] == 321
    assert corr.loc["Парутине", "unified_sea_zero_correction_cm"] == 440
    assert (tmp_path / "out" / "INSITU_README.md").exists()


# ── narrative (the overview prose) ────────────────────────────────────────────

from src.paper_3.insitu.tables import narrative  # noqa: E402

BREACH = ("Максимального значення рівень води досяг 8 червня 1068 см (5,68 м БС), що на 411 см "
          "перевищував максимум за період введення в експлуатацію Каховської ГЕС (657 см, "
          "1956 - 2022 р.р.). 16 червня о 20:00 рівень води знизився нижче небезпечної "
          "відмітки і досяг 649 см (1,49 м БС). Рівень води в Каховському водосховищі перед "
          "підривом станом на 20:00 5 червня 2023 року дорівнював 16,79 м БС (об’єм 19,9 км3), "
          "що на 0,79 м вище нормального підпірного рівня водосховища (НПР).")


def test_narrative_lifts_level_height_pairs_with_their_sentence():
    facts = [f for s in narrative.sentences(BREACH) for f in narrative.facts_from(s, 193, "dam_breach")]
    pairs = [f for f in facts if f["kind"] == "level_with_height"]
    assert [(p["value"], p["height_bs_m"]) for p in pairs] == [(1068.0, 5.68), (649.0, 1.49)]
    assert pairs[0]["date"] == "2023-06-08" and pairs[1]["time"] == "20:00"
    assert all(f["sentence"] in BREACH for f in facts), "every fact carries its verbatim sentence"
    assert all(-5.0 + p["value"] / 100 == pytest.approx(p["height_bs_m"], abs=0.006) for p in pairs), \
        "the yearbook's own cm↔m БС pairs confirm the −5.000 m zero"


def test_narrative_lifts_reservoir_height_volume_and_change():
    facts = [f for s in narrative.sentences(BREACH) for f in narrative.facts_from(s, 193, "dam_breach")]
    kinds = {(f["kind"], f["value"]) for f in facts}
    assert ("height", 16.79) in kinds
    assert ("volume", 19.9) in kinds
    assert ("level_change", 0.79) in kinds
    assert ("level", 657.0) in kinds and ("level", 411.0) in kinds


def test_paragraphs_reflow_lines_and_drop_page_numbers():
    text = ("перша частина\nтого ж абзацу.\n\n  Новий абзац починається з відступу\nі триває.\n"
            "194\n  Третій.")
    assert narrative.paragraphs(text) == ["перша частина того ж абзацу.",
                                          "Новий абзац починається з відступу і триває.",
                                          "Третій."]
