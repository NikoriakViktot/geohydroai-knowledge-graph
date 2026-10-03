"""
nougat_gate.py — region-level acceptance gate for Nougat output.

Nougat is an image-to-sequence generator trained on full arXiv/PMC pages, not an
OCR engine. On region crops (an out-of-distribution input) it fails in three ways
that the audit of 2026-10-03 found in our regions.parquet:

  * decoder collapse — a phrase or a near-identical pair of phrases repeated for
    thousands of characters ("image-based image-based ...", 146 of 4,674 regions);
  * language-prior completion — words that are not on the crop, typically where
    the crop cuts a text line ("as closely or possible ... identifiability");
  * figures — the graphic is not read at all; the output is the caption and the
    surrounding page text, which GROBID already has.

A word-overlap check against the paper's full text does not catch the second case
(the invented words are ordinary vocabulary), so the gate checks the output
against the PDF text layer *inside the crop* — words and, separately, numbers,
because a table value that is not printed on the crop is the worst outcome for
NumericFact and metric extraction.

Statuses (stored per region in regions.parquet, column ``nougat_status``):
    accepted        usable as auxiliary evidence (tables, formulas only)
    rejected        must not reach metrics, facts, Chroma or the graph
    unverified      no text layer in the crop (scanned PDF): cannot be checked,
                    not used for facts
    not_applicable  figure-family region: Nougat text is never evidence
    failed          no output (render or actor failure)

Only ``accepted`` text may be consumed downstream (see ``usable_text``).
"""
from __future__ import annotations

import re
import unicodedata
import zlib
from collections import Counter
from dataclasses import dataclass, field

from src.document.nougat_quality import score_nougat_output

# ── policy ────────────────────────────────────────────────────────────────────
FIGURE_FAMILY = frozenset({
    "FIGURE_REGION", "MULTI_PANEL_FIGURE", "SCIENTIFIC_DIAGRAM", "CHART_REGION",
})
CHECKED_TYPES = frozenset({"TABLE_REGION", "FORMULA_REGION"})

# ── thresholds (calibrated on the OA batch of 2026-10-03, see docs_v2) ────────
COMPRESSION_MIN      = 0.20   # zlib ratio; prose ≈ 0.35–0.45, loops < 0.15
NEAR_DUP_WINDOW      = 20     # tokens per window
NEAR_DUP_JACCARD     = 0.80   # two windows this similar count as a repeat
NEAR_DUP_SHARE_MAX   = 0.30   # share of windows that repeat elsewhere
UNIQUE_TOKEN_MIN     = 0.20   # distinct/total tokens for outputs ≥ 150 tokens
DENSITY_MAX          = 0.06   # output chars per pt² of crop (body text ≈ 0.015)
MIN_CHECK_WORDS      = 5
MIN_CHECK_NUMBERS    = 3
MIN_LAYER_WORDS      = 3      # fewer words in the crop → no usable text layer

GROUNDING_RULES = {           # (min word share, min number share)
    "TABLE_REGION":   (0.80, 0.90),
    "FORMULA_REGION": (0.60, 0.80),   # math fonts garble the text layer
}

_LATEX_CMD = re.compile(r"\\[A-Za-z]+")
_TABLE_SHAPE = re.compile(r"\\begin\{tabular\}")
# a formula region must yield display math; inline \( \) inside prose means the crop
# (or GROBID's box) held a paragraph, not the equation
_MATH_SHAPE  = re.compile(r"\\\[|\\begin\{(?:equation|align|eqnarray|gather|multline)")
_WORD      = re.compile(r"[A-Za-z]{3,}")
_NUMBER    = re.compile(r"(?<![\w.])-?\d+(?:[.,]\d+)?(?![\w])")
_MINUS     = str.maketrans({"−": "-", "–": "-", "﹣": "-", "－": "-"})


Word = tuple[float, float, float, float, str]          # pdf_io.extract_page_words


@dataclass
class RegionVerdict:
    status:            str
    score:             float = 0.0
    flags:             list[str] = field(default_factory=list)
    grounding_words:   float | None = None
    grounding_numbers: float | None = None
    edge_cut_lines:    int = 0

    def as_row(self) -> dict:
        return {
            "nougat_status":     self.status,
            "nougat_flags":      ",".join(self.flags) or None,
            "grounding_words":   self.grounding_words,
            "grounding_numbers": self.grounding_numbers,
            "confidence":        round(self.score, 3),
        }


# ── text signals ──────────────────────────────────────────────────────────────

def compression_ratio(text: str) -> float:
    raw = text.encode("utf-8", "ignore")
    return len(zlib.compress(raw, 9)) / len(raw) if raw else 1.0


def near_duplicate_share(tokens: list[str], window: int = NEAR_DUP_WINDOW,
                         jaccard: float = NEAR_DUP_JACCARD) -> float:
    """Share of non-overlapping token windows that have a near-identical twin.

    Catches the alternating-phrase collapse that an exact 5-gram count misses
    ("A B C D E ..." / "A B C D X ..." / "A B C D E F Y ...")."""
    wins = [frozenset(tokens[i:i + window]) for i in range(0, len(tokens) - window + 1, window)]
    if len(wins) < 3:
        return 0.0
    rep = 0
    for i, a in enumerate(wins):
        for j, b in enumerate(wins):
            if i != j and a and b and len(a & b) / len(a | b) >= jaccard:
                rep += 1
                break
    return rep / len(wins)


def loop_flags(text: str) -> list[str]:
    flags: list[str] = []
    tokens = text.split()
    if len(text) >= 300 and compression_ratio(text) < COMPRESSION_MIN:
        flags.append("LOW_COMPRESSION")
    if len(tokens) >= 3 * NEAR_DUP_WINDOW and near_duplicate_share(tokens) > NEAR_DUP_SHARE_MAX:
        flags.append("NEAR_DUPLICATE_WINDOWS")
    if len(tokens) >= 150 and len(set(tokens)) / len(tokens) < UNIQUE_TOKEN_MIN:
        flags.append("LOW_UNIQUE_TOKENS")
    return flags


# ── grounding against the PDF text layer ──────────────────────────────────────

def _norm_number(s: str) -> str:
    s = s.translate(_MINUS).replace(",", ".")
    try:
        return repr(float(s))
    except ValueError:
        return s


_LATEX_ENV = re.compile(r"\\(?:begin|end)\{[^}]*\}")
_MARKUP_WORDS = frozenset({
    "tabular", "table", "array", "hline", "cline", "multicolumn", "multirow", "align",
    "aligned", "equation", "cases", "matrix", "pmatrix", "bmatrix", "left", "right",
    "text", "textbf", "textit", "frac", "mathrm", "mathbf", "mathit", "operatorname",
})


def text_tokens(text: str) -> tuple[list[str], list[str]]:
    """(words, numbers) of a Nougat output or a PDF text layer, LaTeX commands and
    markup removed. NFKC folds ligatures ("ﬁle" → "file") that PDFs store as one glyph."""
    clean = _LATEX_ENV.sub(" ", unicodedata.normalize("NFKC", text).translate(_MINUS))
    clean = _LATEX_CMD.sub(" ", clean)
    clean = re.sub(r"[{}_^$&|\\]", " ", clean)
    words = [w.lower() for w in _WORD.findall(clean) if w.lower() not in _MARKUP_WORDS]
    nums  = [_norm_number(n) for n in _NUMBER.findall(clean)]
    return words, nums


def crop_layer(words: list[Word], bbox: tuple[float, float, float, float]
               ) -> tuple[set[str], set[str], int, int, str]:
    """Words/numbers of the PDF text layer whose centre lies inside bbox, the word
    count, the number of text lines cut by the top or bottom crop edge, and the
    digit stream of the crop (for numbers the text layer glues together, e.g. the
    coordinate 39°45′04″ is stored as "3984500400N")."""
    x0, y0, x1, y1 = bbox
    wset: set[str] = set()
    nset: set[str] = set()
    n = 0
    cut_rows: set[int] = set()
    raw: list[str] = []
    for wx0, wy0, wx1, wy1, t in words:
        cx, cy = (wx0 + wx1) / 2, (wy0 + wy1) / 2
        inside_x = x0 <= cx <= x1
        if inside_x and ((wy0 < y0 < wy1) or (wy0 < y1 < wy1)):
            cut_rows.add(round(cy))
        if inside_x and y0 <= cy <= y1:
            n += 1
            tt = unicodedata.normalize("NFKC", t).translate(_MINUS)
            wset.update(w.lower() for w in _WORD.findall(tt))
            nset.update(_norm_number(m) for m in _NUMBER.findall(tt))
            raw.append(tt)
    return wset, nset, n, len(cut_rows), " ".join(raw)


def _share(items: list[str], pool: set[str]) -> float | None:
    return sum(i in pool for i in items) / len(items) if items else None


def _digits(n: str) -> str:
    n = n.lstrip("-")
    return n[:-2] if n.endswith(".0") else n


def _number_share(nums: list[str], pool: set[str], layer_text: str = "") -> float | None:
    """Share of numbers printed on the crop. A sign lost in the text layer
    ("10−3" read as "3") still counts, and so does a number found inside a token
    the text layer glued together ("3984500400N" holds 39, 45, 04)."""
    if not nums:
        return None
    absolute = {x.lstrip("-") for x in pool}
    ok = 0
    for n in nums:
        if n in pool or n.lstrip("-") in absolute or (_digits(n) and _digits(n) in layer_text):
            ok += 1
    return ok / len(nums)


# ── verdict ───────────────────────────────────────────────────────────────────

def assess_region(text: str | None, region_type: str,
                  bbox: tuple[float, float, float, float] | None = None,
                  page_words: list[Word] | None = None) -> RegionVerdict:
    """Decide whether one region's Nougat output may be used downstream."""
    if region_type in FIGURE_FAMILY:
        return RegionVerdict("not_applicable", 0.0, ["FIGURE_POLICY"])
    if not text or not text.strip():
        return RegionVerdict("failed", 0.0, ["EMPTY"])

    q = score_nougat_output(text, region_type)
    # nougat_quality's single-5-gram and entropy tests fire on short LaTeX
    # ("A_{i,j} = A_{i,0} + \\delta A_{i,0}" has a 5-gram ratio of 0.5); a decoder
    # loop is long, so for short outputs only this module's own checks apply.
    short = len(text.split()) < 60
    flags = [f for f in q.flags if not (short and f in ("REPETITION_LOOP", "LOW_ENTROPY"))]
    flags += loop_flags(text)
    score = q.score

    v = RegionVerdict("accepted", score, flags)
    if region_type not in CHECKED_TYPES:
        v.status = "rejected"
        v.flags.append("UNKNOWN_REGION_TYPE")
        return v

    # the output must be the kind of object the region is: a crop of a table that
    # yields the paragraph under it is grounded text, but not the table
    if region_type == "TABLE_REGION" and not _TABLE_SHAPE.search(text) and text.count("&") < 4:
        v.flags.append("NOT_A_TABLE")
    if region_type == "FORMULA_REGION" and not _MATH_SHAPE.search(text):
        v.flags.append("NOT_A_FORMULA")

    if bbox is not None:
        area = max((bbox[2] - bbox[0]) * (bbox[3] - bbox[1]), 1.0)
        if len(text) / area > DENSITY_MAX:
            v.flags.append("TEXT_DENSITY")

    if page_words is not None and bbox is not None:
        wset, nset, n_layer, cut, layer_text = crop_layer(page_words, bbox)
        v.edge_cut_lines = cut
        if cut:
            v.flags.append("EDGE_CUT")
        if n_layer < MIN_LAYER_WORDS:
            v.status = "unverified"
            v.flags.append("NO_TEXT_LAYER")
        else:
            words, nums = text_tokens(text)
            v.grounding_words = _share(words, wset)
            v.grounding_numbers = _number_share(nums, nset, layer_text)
            wmin, nmin = GROUNDING_RULES[region_type]
            if len(words) >= MIN_CHECK_WORDS and v.grounding_words is not None \
                    and v.grounding_words < wmin:
                v.flags.append("UNGROUNDED_WORDS")
            if len(nums) >= MIN_CHECK_NUMBERS and v.grounding_numbers is not None \
                    and v.grounding_numbers < nmin:
                v.flags.append("UNGROUNDED_NUMBERS")
    else:
        v.status = "unverified"
        v.flags.append("NO_TEXT_LAYER")

    hard = {"LOW_COMPRESSION", "NEAR_DUPLICATE_WINDOWS", "LOW_UNIQUE_TOKENS",
            "TEXT_DENSITY", "UNGROUNDED_WORDS", "UNGROUNDED_NUMBERS",
            "NOT_A_TABLE", "NOT_A_FORMULA",
            "REPETITION_LOOP", "LOW_ENTROPY", "HALLUCINATION", "EMPTY", "TOO_SHORT"}
    if any(f in hard for f in v.flags):
        v.status = "rejected"
        v.score = min(v.score, 0.2)
    return v


# ── crop geometry ─────────────────────────────────────────────────────────────

TOKENS_PER_PT2 = 0.04          # ≈ 2.5× the token density of a dense LaTeX table
MIN_TOKENS, MAX_TOKENS = 256, 4096


def token_budget(bbox: tuple[float, float, float, float]) -> int:
    """max_new_tokens for a crop: a small formula cannot need 4,096 tokens, and a
    budget near the content size bounds how far a loop can run."""
    area = max((bbox[2] - bbox[0]) * (bbox[3] - bbox[1]), 0.0)
    return int(min(MAX_TOKENS, max(MIN_TOKENS, area * TOKENS_PER_PT2)))


def snap_bbox(bbox: tuple[float, float, float, float], words: list[Word],
              max_shift: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    """Move the top and bottom crop edges inward so they do not cut a text line.

    A line cut in half is the typical place where Nougat completes text from its
    language prior. Edges move only within the padding added around the region
    (max_shift = top, bottom, left, right), so the region itself is never cropped."""
    x0, y0, x1, y1 = bbox
    st, sb, _, _ = max_shift
    top, bottom = y0, y1
    for wx0, wy0, wx1, wy1, _t in words:
        cx = (wx0 + wx1) / 2
        if not (x0 <= cx <= x1):
            continue
        if wy0 < y0 < wy1 and wy1 - y0 <= st:
            top = max(top, wy1 + 0.5)
        if wy0 < y1 < wy1 and y1 - wy0 <= sb:
            bottom = min(bottom, wy0 - 0.5)
    if bottom - top < 10:                 # never collapse a crop
        return bbox
    return (x0, top, x1, bottom)


def region_text(row: dict) -> str:
    """The Nougat output of a regions.parquet row: LaTeX for formulas, else text.
    pandas hands NaN for empty cells, and NaN is truthy, so `a or b` is not enough."""
    for key in ("nougat_latex", "nougat_text"):
        v = row.get(key)
        if isinstance(v, str) and v.strip():
            return v
    return ""


def usable_text(row: dict) -> str | None:
    """Nougat text of a regions.parquet row if — and only if — the gate accepted it.
    Rows written before the gate existed have no status and are not usable."""
    if row.get("nougat_status") != "accepted":
        return None
    return region_text(row) or None
