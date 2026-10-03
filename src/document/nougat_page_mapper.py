"""
nougat_page_mapper.py — map page-level Nougat markdown onto GROBID regions.

Nougat is trained on full pages and must be run on full pages (the authors'
preprocessing crops margins and resizes a page to 896×672; on region crops it
loops and completes text from its language prior — see docs_v2/NOUGAT_AUDIT_20261003.md).
The region pipeline therefore infers each page that holds a table or formula once,
splits the page markdown into table and display-equation blocks, and assigns blocks
to the GROBID regions of that page.

Assignment is grounded in the PDF itself, not in GROBID captions (table regions
rarely carry one): the words and numbers printed inside a region's bbox are compared
with each candidate block, and the best match above a threshold wins. Display
equations are also matched by their number "(n)" when the region's text layer shows
it. A region with no convincing block gets no text — never a guess.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from src.document.nougat_gate import Word, text_tokens

_TABLE_ENV  = re.compile(r"\\begin\{table\}.*?\\end\{table\}", re.S)
_TABULAR    = re.compile(r"\\begin\{tabular\}.*?\\end\{tabular\}", re.S)
_CAPTION    = re.compile(r"^\s*(Table|TABLE|Tab\.)\s*[\dIVX]+[.:]?.*$", re.M)
_DISPLAY_EQ = re.compile(r"\\\[(.*?)\\\]\s*(?:\((\d+[a-z]?)\))?", re.S)
_EQ_NUMBER  = re.compile(r"\(\s*(\d+[a-z]?)\s*\)")
_NUMBER     = re.compile(r"(?<![\w.])-?\d+(?:[.,]\d+)?")

MIN_TABLE_COVER   = 0.60   # share of the region's printed tokens the block contains
MIN_SOLE_COVER    = 0.30   # the same when the page has exactly one table and one table region
MIN_FORMULA_PREC  = 0.60   # share of the block's tokens printed inside the region
MIN_LAYER_TOKENS  = 3


@dataclass
class Block:
    kind:   str                  # "table" | "equation"
    text:   str
    start:  int
    number: str | None = None    # equation number, if printed


@dataclass
class Assignment:
    text:   str | None
    score:  float
    method: str                  # "equation_number" | "layer_overlap" | "unmatched"
    blocks: list[int] = field(default_factory=list)


def split_blocks(markdown: str) -> list[Block]:
    """Table and display-equation blocks of one page, in reading order. A table
    block keeps the caption line Nougat writes right after \\end{table}."""
    blocks: list[Block] = []
    taken: list[tuple[int, int]] = []
    for m in _TABLE_ENV.finditer(markdown):
        end = m.end()
        cap = _CAPTION.match(markdown, end + 1) if end < len(markdown) else None
        if cap:
            end = cap.end()
        blocks.append(Block("table", markdown[m.start():end].strip(), m.start()))
        taken.append((m.start(), end))
    for m in _TABULAR.finditer(markdown):
        if any(a <= m.start() < b for a, b in taken):
            continue
        blocks.append(Block("table", m.group(0).strip(), m.start()))
        taken.append((m.start(), m.end()))
    for m in _DISPLAY_EQ.finditer(markdown):
        if any(a <= m.start() < b for a, b in taken):
            continue
        blocks.append(Block("equation", m.group(0).strip(), m.start(), m.group(2)))
    return sorted(blocks, key=lambda b: b.start)


def layer_tokens(words: list[Word], bbox: tuple[float, float, float, float]) -> tuple[set[str], set[str], str]:
    """Words, numbers and raw text of the PDF text layer inside bbox."""
    x0, y0, x1, y1 = bbox
    inside = [t for wx0, wy0, wx1, wy1, t in words
              if x0 <= (wx0 + wx1) / 2 <= x1 and y0 <= (wy0 + wy1) / 2 <= y1]
    raw = " ".join(inside)
    w, n = text_tokens(raw)
    return set(w), set(n), raw


def _tokens(text: str) -> set[str]:
    w, n = text_tokens(text)
    return set(w) | set(n)


def table_cover(block_text: str, layer: set[str]) -> float:
    """How much of what is printed inside a table region the block contains. GROBID
    table boxes often cover only part of a table (the first columns), so the block is
    expected to hold more than the region shows, never less."""
    if len(layer) < MIN_LAYER_TOKENS:
        return 0.0
    return len(layer & _tokens(block_text)) / len(layer)


def formula_precision(block_text: str, layer: set[str]) -> float:
    """How much of an equation block is printed inside the region. Formula boxes
    often include the surrounding sentence, so precision, not recall, is the test."""
    b = _tokens(block_text)
    if len(b) < MIN_LAYER_TOKENS:
        return 0.0
    return len(b & layer) / len(b)


def _v_overlap(a: tuple, b: tuple) -> float:
    inter = min(a[3], b[3]) - max(a[1], b[1])
    return max(0.0, inter) / max(1.0, min(a[3] - a[1], b[3] - b[1]))


def assign_regions(markdown: str, regions: list[dict], words: list[Word] | None
                   ) -> dict[str, Assignment]:
    """regions: dicts with region_id, region_type, bbox (x0, y0, x1, y1) and, for
    formulas, formula_text. Returns region_id → Assignment."""
    blocks = split_blocks(markdown)
    out: dict[str, Assignment] = {}
    if not words:
        return {r["region_id"]: Assignment(None, 0.0, "no_text_layer") for r in regions}

    # ── tables ────────────────────────────────────────────────────────────────
    tables = sorted((r for r in regions if r["region_type"] == "TABLE_REGION"),
                    key=lambda r: (r["bbox"][1], r["bbox"][0]))
    tblocks = [i for i, b in enumerate(blocks) if b.kind == "table"]
    owner: dict[int, list[dict]] = {}
    for rank, r in enumerate(tables):
        lw, ln, _ = layer_tokens(words, r["bbox"])
        layer = lw | ln
        scored = [(table_cover(blocks[i].text, layer), i) for i in tblocks]
        if not scored:
            continue
        best = max(sc for sc, _ in scored)
        sole = len(tables) == 1 and len(tblocks) == 1
        if best < (MIN_SOLE_COVER if sole else MIN_TABLE_COVER):
            continue
        cands = [i for sc, i in scored if sc >= best - 1e-9]
        # a block already owned by a region at another height belongs to another table
        free = [i for i in cands
                if all(_v_overlap(o["bbox"], r["bbox"]) > 0.5 for o in owner.get(i, []))]
        if not free:
            continue
        # tie-break: reading order of tables on the page
        i = min(free, key=lambda i: abs(tblocks.index(i) - min(rank, len(tblocks) - 1)))
        owner.setdefault(i, []).append(r)
        out[r["region_id"]] = Assignment(blocks[i].text, best, "layer_cover", [i])

    # ── formulas ──────────────────────────────────────────────────────────────
    eq = [i for i, b in enumerate(blocks) if b.kind == "equation"]
    formulas = sorted((r for r in regions if r["region_type"] == "FORMULA_REGION"),
                      key=lambda r: (r["bbox"][1], r["bbox"][0]))
    used: set[int] = set()
    pending: list[dict] = []
    for r in formulas:
        lw, ln, raw = layer_tokens(words, r["bbox"])
        layer = lw | ln
        numbers = set(_EQ_NUMBER.findall(raw)) | set(_EQ_NUMBER.findall(r.get("formula_text") or ""))
        idx = [i for i in eq if blocks[i].number and blocks[i].number in numbers and i not in used]
        if idx:
            out[r["region_id"]] = Assignment("\n\n".join(blocks[i].text for i in idx), 1.0,
                                             "equation_number", idx)
            used.update(idx)
            continue
        scored = [(formula_precision(blocks[i].text, layer), i) for i in eq if i not in used]
        best = max(scored, default=(0.0, -1))
        if best[0] >= MIN_FORMULA_PREC:
            out[r["region_id"]] = Assignment(blocks[best[1]].text, best[0], "layer_precision", [best[1]])
            used.add(best[1])
        else:
            pending.append(r)
    # unnumbered equations: reading order, only when the counts agree exactly
    free_eq = [i for i in eq if i not in used]
    if pending and len(pending) == len(free_eq):
        for r, i in zip(pending, free_eq):
            out[r["region_id"]] = Assignment(blocks[i].text, 0.5, "reading_order", [i])

    for r in regions:
        out.setdefault(r["region_id"], Assignment(None, 0.0, "unmatched"))
    return out
