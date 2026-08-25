"""
Text chunking layer.
Groups pages by document, then splits into overlapping character windows.
Preserves file/page metadata on every chunk.
"""
import logging
from dataclasses import dataclass
from itertools import groupby

from src.config import CHUNK_MIN, CHUNK_OVERLAP, CHUNK_SIZE
from src.ingestion.pdf_reader import PageDocument

logger = logging.getLogger(__name__)


@dataclass
class TextChunk:
    """A fixed-size text window ready for embedding."""
    text: str
    chunk_id: str          # globally unique: "<filename>::c<N>"
    filename: str
    page_start: int        # first page this chunk spans
    page_end: int          # last page this chunk spans
    chunk_index: int       # sequential index within the document
    char_start: int        # byte offset in the concatenated document text
    char_end: int


def chunk_documents(pages: list[PageDocument]) -> list[TextChunk]:
    """
    Chunk all pages.
    Pages belonging to the same file are concatenated before chunking
    so cross-page context is preserved.
    """
    chunks: list[TextChunk] = []

    # group by filename, preserving file order
    sorted_pages = sorted(pages, key=lambda p: (p.filename, p.page_num))
    for filename, group in groupby(sorted_pages, key=lambda p: p.filename):
        page_list = list(group)
        doc_chunks = _chunk_doc(filename, page_list)
        chunks.extend(doc_chunks)
        logger.debug("  %s → %d chunks", filename, len(doc_chunks))

    logger.info("Chunking complete: %d chunks from %d unique files",
                len(chunks), len({c.filename for c in chunks}))
    return chunks


def _chunk_doc(filename: str, pages: list[PageDocument]) -> list[TextChunk]:
    """Split one document's pages into overlapping chunks."""
    # build full doc text and track page boundaries
    full_text = ""
    boundaries: list[tuple[int, int]] = []   # (char_start, page_num)
    for page in pages:
        start = len(full_text)
        full_text += page.text + "\n\n"
        boundaries.append((start, page.page_num))

    def _page_at(char_pos: int) -> int:
        page = 1
        for bstart, bpage in boundaries:
            if char_pos >= bstart:
                page = bpage
        return page

    chunks: list[TextChunk] = []
    start = 0
    doc_len = len(full_text)
    idx = 0

    while start < doc_len:
        end = min(start + CHUNK_SIZE, doc_len)
        text = full_text[start:end].strip()

        if len(text) >= CHUNK_MIN:
            chunks.append(TextChunk(
                text=text,
                chunk_id=f"{filename}::c{idx}",
                filename=filename,
                page_start=_page_at(start),
                page_end=_page_at(end - 1),
                chunk_index=idx,
                char_start=start,
                char_end=end,
            ))
            idx += 1

        if end >= doc_len:
            break
        start = end - CHUNK_OVERLAP   # slide back by overlap

    return chunks
