"""Header-anchored grid reconstruction for borderless table bodies.

PyMuPDF's `find_tables` recovers the ruled header of the yearbook tables but
not their unruled bodies. The header's leaf cells give the column x-bounds;
the body is rebuilt from word bboxes: cluster words into rows by baseline,
then drop each word into the column whose x-range contains its centre.

Everything here is a pure function over plain tuples, so it is testable
without a PDF.
"""
from __future__ import annotations

from src.paper_3.insitu.pages import Word

Bounds = list[tuple[float, float]]


def leaf_cells(table: dict, tol: float = 1.0) -> list[tuple[float, float, float, float]]:
    """Cells on the bottom row of a (possibly merged) header, left to right."""
    bottom = table["bbox"][3]
    cells = [c for c in table["cells"] if c is not None and abs(c[3] - bottom) <= tol]
    return sorted(cells, key=lambda c: c[0])


def column_bounds(table: dict) -> Bounds:
    return [(c[0], c[2]) for c in leaf_cells(table)]


def words_in(words: list[Word], y_top: float | None = None,
             y_bottom: float | None = None, x_left: float | None = None,
             x_right: float | None = None) -> list[Word]:
    """Words whose box lies inside the given (open) limits."""
    out = []
    for w in words:
        x0, y0, x1, y1, _ = w
        if y_top is not None and y0 < y_top:
            continue
        if y_bottom is not None and y1 > y_bottom:
            continue
        if x_left is not None and x0 < x_left:
            continue
        if x_right is not None and x1 > x_right:
            continue
        out.append(w)
    return out


def cluster_rows(words: list[Word], tol: float = 2.5) -> list[list[Word]]:
    """Group words into rows by baseline (y1); each row sorted left to right."""
    rows: list[list[Word]] = []
    for w in sorted(words, key=lambda w: (w[3], w[0])):
        if rows and abs(w[3] - rows[-1][-1][3]) <= tol:
            rows[-1].append(w)
        else:
            rows.append([w])
    return [sorted(r, key=lambda w: w[0]) for r in rows]


def column_of(word: Word, bounds: Bounds, slack: float = 2.0) -> int | None:
    xc = (word[0] + word[2]) / 2
    for i, (left, right) in enumerate(bounds):
        if left - slack <= xc <= right + slack:
            return i
    return None


def assign_columns(row: list[Word], bounds: Bounds, slack: float = 2.0
                   ) -> tuple[list[str], list[str]]:
    """(cells, orphans): words per column joined by a space; orphans fell between columns."""
    cells = [""] * len(bounds)
    orphans: list[str] = []
    for w in row:
        i = column_of(w, bounds, slack)
        if i is None:
            orphans.append(w[4])
            continue
        cells[i] = (cells[i] + " " + w[4]).strip() if cells[i] else w[4]
    return cells, orphans


def body_rows(words: list[Word], y_top: float, y_bottom: float, bounds: Bounds,
              tol: float = 2.5, slack: float = 2.0) -> list[dict]:
    """Rows between two y-limits as [{"y": baseline, "cells": [...], "orphans": [...]}]."""
    zone = words_in(words, y_top=y_top, y_bottom=y_bottom)
    out = []
    for row in cluster_rows(zone, tol):
        cells, orphans = assign_columns(row, bounds, slack)
        out.append({"y": row[0][3], "cells": cells, "orphans": orphans})
    return out


def text_of(words: list[Word], tol: float = 2.5) -> str:
    """Reading-order text: rows top to bottom, words left to right, one line per row."""
    return "\n".join(" ".join(w[4] for w in row) for row in cluster_rows(words, tol))


def first_y_of(words: list[Word], needle: str, y_after: float = 0.0) -> float | None:
    """Top of the first word containing `needle` below `y_after`."""
    hits = [w[1] for w in words if needle in w[4] and w[1] >= y_after]
    return min(hits) if hits else None
