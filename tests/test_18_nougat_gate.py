"""Nougat acceptance gate (src/document/nougat_gate.py) — 2026-10-03 audit cases."""
from __future__ import annotations

import math

import pytest

from src.document.nougat_gate import (
    assess_region, compression_ratio, loop_flags, near_duplicate_share, region_text,
    snap_bbox, text_tokens, token_budget, usable_text,
)
from src.document.nougat_quality import score_nougat_output

BOX = (100.0, 100.0, 400.0, 300.0)


def layer(*lines: str, x0: float = 110.0, y0: float = 120.0, step: float = 14.0):
    """Fake PDF text layer: one word tuple per token, one line per string."""
    words = []
    for i, line in enumerate(lines):
        x = x0
        for tok in line.split():
            words.append((x, y0 + i * step, x + 8 * len(tok), y0 + i * step + 10, tok))
            x += 8 * len(tok) + 4
    return words


TABLE_LAYER = layer("Station NSE RMSE", "Nova Kakhovka 0.82 1.35", "Kherson 0.77 2.10")
TABLE_OK = ("\\begin{tabular}{l c c} Station & NSE & RMSE \\\\ \\hline "
            "Nova Kakhovka & 0.82 & 1.35 \\\\ Kherson & 0.77 & 2.10 \\\\ \\end{tabular}")


def test_figures_are_never_evidence():
    v = assess_region("Figure 3: Flood map of the study area.", "FIGURE_REGION", BOX, TABLE_LAYER)
    assert v.status == "not_applicable"
    assert usable_text({"nougat_status": v.status, "nougat_text": "x"}) is None


def test_grounded_table_is_accepted():
    v = assess_region(TABLE_OK, "TABLE_REGION", BOX, TABLE_LAYER)
    assert v.status == "accepted", v.flags
    assert v.grounding_numbers == 1.0


def test_invented_numbers_are_rejected():
    bad = TABLE_OK.replace("0.82", "0.93").replace("1.35", "4.71").replace("2.10", "3.33")
    v = assess_region(bad, "TABLE_REGION", BOX, TABLE_LAYER)
    assert v.status == "rejected" and "UNGROUNDED_NUMBERS" in v.flags


def test_invented_caption_is_rejected():
    # "Cumulative level game summary" was printed nowhere on the crop
    bad = "**Cumulative level game summary of the uniform prior distribution.**"
    v = assess_region(bad, "TABLE_REGION", BOX, TABLE_LAYER)
    assert v.status == "rejected" and "UNGROUNDED_WORDS" in v.flags


def test_scanned_crop_is_unverified_not_accepted():
    v = assess_region(TABLE_OK, "TABLE_REGION", BOX, [])
    assert v.status == "unverified"
    assert usable_text({"nougat_status": v.status, "nougat_text": TABLE_OK}) is None


@pytest.mark.parametrize("text", [
    "In this paper, we have proposed a novel approach to the " + "image-based " * 300,
    ("the activities/e a extensive agricultural operations and flood in " * 40),
    " ".join(("They have also used the Hubble Space Telescope data to determine the "
              "position of the source in the sky." if i % 2 else
              "They have used the Hubble Space Telescope (HST) data to determine the "
              "position of the source in the sky.") for i in range(30)),
])
def test_loops_from_the_audit_are_flagged(text):
    assert loop_flags(text), text[:60]
    assert assess_region(text, "TABLE_REGION", BOX, TABLE_LAYER).status == "rejected"


def test_normal_prose_is_not_a_loop():
    prose = ("The canopy storage is calculated from precipitation, throughfall and canopy "
             "evaporation. Throughfall is the fraction of precipitation that reaches the soil, "
             "and evaporation from the canopy depends on potential evapotranspiration and the "
             "leaf area index, which varies by land cover class and month of the year.") * 1
    assert loop_flags(prose) == []
    assert compression_ratio(prose) > 0.3
    assert near_duplicate_share(prose.split()) == 0.0


def test_markdown_heading_is_not_a_hallucination_marker():
    q = score_nougat_output("#### 3.2.1 Modelling runoff and ET\n\nFirst, we compute runoff.",
                            "FORMULA_REGION")
    assert "HALLUCINATION" not in q.flags


def test_text_tokens_drop_markup_and_keep_numbers():
    words, nums = text_tokens("\\begin{tabular}{l} \\(\\mathrm{NSE}\\) & 0.82 & 10^{-3} \\end{tabular}")
    assert "tabular" not in words and "mathrm" not in words and "nse" in words
    assert "0.82" in nums and "-3.0" in nums


def test_glued_coordinates_count_as_grounded():
    lay = layer("39.03 3984500400N Highway bridge")
    v = assess_region("39.03 & 39^{\\circ}45^{\\prime}04 & Highway bridge", "TABLE_REGION", BOX, lay)
    assert v.grounding_numbers == 1.0


def test_snap_bbox_does_not_cut_a_line():
    # a body-text line straddles the top edge (y 95–105) and the bottom edge (295–305)
    words = [(150, 95, 200, 105, "cut"), (150, 295, 200, 305, "cut"), (150, 200, 200, 210, "core")]
    top, bottom = snap_bbox(BOX, words, (20, 30, 10, 10))[1::2]
    assert top > 105 and bottom < 295


def test_snap_bbox_never_eats_into_the_region():
    words = [(150, 60, 200, 140, "tall")]          # straddles by more than the padding
    assert snap_bbox(BOX, words, (20, 30, 10, 10)) == BOX


def test_token_budget_scales_with_area():
    assert token_budget((0, 0, 400, 60)) < 2000
    assert token_budget((0, 0, 600, 800)) == 4096
    assert token_budget((0, 0, 10, 10)) == 256


def test_region_text_handles_pandas_nan():
    assert region_text({"nougat_latex": math.nan, "nougat_text": "0.82"}) == "0.82"
    assert usable_text({"nougat_status": "accepted", "nougat_latex": math.nan,
                        "nougat_text": "0.82"}) == "0.82"
    assert usable_text({"nougat_text": "0.82"}) is None     # ungated row
