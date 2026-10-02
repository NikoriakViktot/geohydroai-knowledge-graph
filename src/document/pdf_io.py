"""
pdf_io.py — єдиний PDF-фасад поза parser.py / nougat_parser.py.

Архітектурний інваріант: fitz (PyMuPDF) можна імпортувати лише в
src/document/{parser.py, nougat_parser.py, pdf_io.py}. Усі інші модулі
(triage, reader, region pipeline) працюють через цей фасад — байтова
обробка PDF ізольована в document-шарі (enforced у
tests/test_import_invariants.py).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import fitz  # PyMuPDF
from PIL import Image


class PdfIOError(Exception):
    """PDF неможливо відкрити або прочитати (корупція, формат, I/O)."""


# ── Triage probe ──────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class PdfProbe:
    """Сирі факти про PDF для тріажу. Політика (пороги SKIP) — у викликача."""
    page_count:       int
    is_locked:        bool   # зашифрований і не відкрився порожнім паролем
    sample_pages:     int    # скільки сторінок реально проінспектовано
    sampled_chars:    int    # сумарно символів тексту в семплі
    image_only_pages: int    # сторінок у семплі: текст < min_chars, але є зображення
    estimated_chars:  int    # екстрапольовано на весь документ


def probe_pdf(
    pdf_path: Path,
    sample_pages: int = 20,
    min_chars_per_page: int = 50,
    full_sample_pages: int = 50,
) -> PdfProbe:
    """Швидка інспекція PDF (мілісекунди, без ML). Raises PdfIOError."""
    try:
        doc = fitz.open(str(pdf_path))
    except Exception as exc:
        raise PdfIOError(f"cannot open {pdf_path.name}: {exc}") from exc

    try:
        if doc.is_encrypted and not doc.authenticate(""):
            return PdfProbe(
                page_count=doc.page_count, is_locked=True,
                sample_pages=0, sampled_chars=0,
                image_only_pages=0, estimated_chars=0,
            )

        page_count = doc.page_count
        sample_n   = min(page_count, sample_pages)
        total_chars  = 0
        image_only_n = 0
        for i in range(sample_n):
            page = doc[i]
            text = page.get_text().strip()
            total_chars += len(text)
            if len(text) < min_chars_per_page and page.get_images():
                image_only_n += 1

        full_n     = min(page_count, full_sample_pages)
        full_chars = sum(len(doc[i].get_text()) for i in range(full_n))
        if full_n and full_n < page_count:
            full_chars = int(full_chars * page_count / full_n)

        return PdfProbe(
            page_count=page_count, is_locked=False,
            sample_pages=sample_n, sampled_chars=total_chars,
            image_only_pages=image_only_n, estimated_chars=full_chars,
        )
    except Exception as exc:
        raise PdfIOError(f"probe failed for {pdf_path.name}: {exc}") from exc
    finally:
        doc.close()


# ── Page text extraction ──────────────────────────────────────────────────────

def extract_pages_text(pdf_path: Path) -> tuple[int, list[tuple[int, str]]]:
    """(total_pages, [(page_num 1-based, raw text), ...]). Raises PdfIOError."""
    try:
        with fitz.open(str(pdf_path)) as pdf:
            total = pdf.page_count
            pages = [
                (idx + 1, pdf.load_page(idx).get_text("text"))
                for idx in range(total)
            ]
            return total, pages
    except Exception as exc:
        raise PdfIOError(f"cannot read {pdf_path.name}: {exc}") from exc


def extract_page_text(pdf_path: Path, page_num: int) -> str:
    """Сирий текст однієї сторінки (1-indexed). Raises PdfIOError."""
    try:
        with fitz.open(str(pdf_path)) as pdf:
            return pdf.load_page(page_num - 1).get_text("text")
    except Exception as exc:
        raise PdfIOError(f"cannot read page {page_num} of {pdf_path.name}: {exc}") from exc


# ── Page geometry: words and ruled tables ─────────────────────────────────────
#
# Потрібно для таблиць без ліній (гідрометеорологічні щорічники): find_tables
# повертає лише шапку, а тіло відновлюється зі слів за x-межами колонок шапки.
# Обидві функції повертають лише прості кортежі/словники — жодного fitz-об'єкта
# не виходить за межі фасаду.

Word = tuple[float, float, float, float, str]   # x0, y0, x1, y1, text


def extract_page_words(pdf_path: Path, page_num: int) -> list[Word]:
    """Слова сторінки (1-indexed) з bbox у pt, у порядку читання. Raises PdfIOError."""
    try:
        with fitz.open(str(pdf_path)) as pdf:
            page = pdf.load_page(page_num - 1)
            return [(float(w[0]), float(w[1]), float(w[2]), float(w[3]), str(w[4]))
                    for w in page.get_text("words")]
    except Exception as exc:
        raise PdfIOError(f"cannot read words on page {page_num} of "
                         f"{pdf_path.name}: {exc}") from exc


def extract_page_tables(pdf_path: Path, page_num: int,
                        strategy: str = "lines") -> list[dict]:
    """Таблиці, знайдені PyMuPDF на сторінці (1-indexed).

    Кожна: {"bbox": (x0,y0,x1,y1), "rows": [[str|None, ...], ...],
            "cells": [(x0,y0,x1,y1) | None, ...]  (row-major, як у fitz),
            "row_count": int, "col_count": int}.
    `strategy` — "lines" (лише лінійовані) або "text" (за вирівнюванням тексту).
    Raises PdfIOError.
    """
    try:
        with fitz.open(str(pdf_path)) as pdf:
            page = pdf.load_page(page_num - 1)
            found = page.find_tables(strategy=strategy)
            out: list[dict] = []
            for t in found.tables:
                out.append({
                    "bbox": tuple(float(v) for v in t.bbox),
                    "rows": t.extract(),
                    "cells": [tuple(float(v) for v in c) if c is not None else None
                              for c in t.cells],
                    "row_count": int(t.row_count),
                    "col_count": int(t.col_count),
                })
            return out
    except Exception as exc:
        raise PdfIOError(f"cannot find tables on page {page_num} of "
                         f"{pdf_path.name}: {exc}") from exc


# ── Region / page rendering (Nougat crops) ────────────────────────────────────

def render_region_image(
    pdf_path: Path,
    page_num: int,                                  # 1-indexed (як у GROBID)
    bbox: tuple[float, float, float, float],        # (x0, y0, x1, y1) у pt
    dpi: int = 150,
) -> Image.Image:
    """Кроп bbox зі сторінки як PIL RGB Image. Raises PdfIOError."""
    try:
        doc  = fitz.open(str(pdf_path))
        page = doc[page_num - 1]
        rect = fitz.Rect(*bbox)
        mat  = fitz.Matrix(dpi / 72, dpi / 72)
        pix  = page.get_pixmap(matrix=mat, clip=rect, alpha=False)
        img  = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
        doc.close()
        return img
    except Exception as exc:
        raise PdfIOError(f"render region failed for {pdf_path.name}: {exc}") from exc


def render_page_image(pdf_path: Path, page_num: int, dpi: int = 150) -> Image.Image:
    """Повна сторінка (1-indexed) як PIL RGB Image. Raises PdfIOError."""
    try:
        doc  = fitz.open(str(pdf_path))
        page = doc[page_num - 1]
        mat  = fitz.Matrix(dpi / 72, dpi / 72)
        pix  = page.get_pixmap(matrix=mat, alpha=False)
        img  = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
        doc.close()
        return img
    except Exception as exc:
        raise PdfIOError(f"render page failed for {pdf_path.name}: {exc}") from exc
