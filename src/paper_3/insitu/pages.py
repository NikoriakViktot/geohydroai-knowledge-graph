"""Memoised page access over the `pdf_io` facade — each page is read once."""
from __future__ import annotations

from pathlib import Path

from src.document.pdf_io import (extract_page_tables, extract_page_text,
                                 extract_page_words)
from src.paper_3.insitu.sources import PageSpan, assert_caption

Word = tuple[float, float, float, float, str]


class PageCache:
    def __init__(self, paths: dict[str, Path]):
        self.paths = {k: Path(v) for k, v in paths.items()}
        self._text: dict[tuple[str, int], str] = {}
        self._words: dict[tuple[str, int], list[Word]] = {}
        self._tables: dict[tuple[str, int, str], list[dict]] = {}

    def text(self, volume: str, page: int) -> str:
        key = (volume, page)
        if key not in self._text:
            self._text[key] = extract_page_text(self.paths[volume], page)
        return self._text[key]

    def words(self, volume: str, page: int) -> list[Word]:
        key = (volume, page)
        if key not in self._words:
            self._words[key] = extract_page_words(self.paths[volume], page)
        return self._words[key]

    def tables(self, volume: str, page: int, strategy: str = "lines") -> list[dict]:
        key = (volume, page, strategy)
        if key not in self._tables:
            self._tables[key] = extract_page_tables(self.paths[volume], page, strategy)
        return self._tables[key]

    def check_span(self, span: PageSpan) -> None:
        """Raise PageMapError on the first page of the span missing its caption."""
        for page in span.pages:
            assert_caption(self.text(span.volume, page), span.caption,
                           span.volume, page)
