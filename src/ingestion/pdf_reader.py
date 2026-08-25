"""
PDF ingestion layer.
Reads every PDF in a folder and returns page-level documents.
"""
import logging
from dataclasses import dataclass, field
from pathlib import Path

from src.document.pdf_io import PdfIOError, extract_pages_text

logger = logging.getLogger(__name__)


@dataclass
class PageDocument:
    """One page of text extracted from a PDF."""
    text: str
    filename: str
    page_num: int           # 1-based
    total_pages: int
    filepath: str = field(default="", repr=False)

    @property
    def doc_id(self) -> str:
        return f"{self.filename}::p{self.page_num}"


def read_pdf(path: Path) -> list[PageDocument]:
    """
    Extract text from all pages of *path*.
    Returns an empty list (with a logged error) on any failure.
    """
    docs: list[PageDocument] = []
    try:
        total, pages = extract_pages_text(path)
        for page_num, raw_text in pages:
            text = _clean_text(raw_text)
            if len(text) >= 50:          # skip nearly-empty pages
                docs.append(PageDocument(
                    text=text,
                    filename=path.name,
                    page_num=page_num,
                    total_pages=total,
                    filepath=str(path),
                ))
    except PdfIOError as exc:
        logger.error("PDF read error for %s: %s", path.name, exc)
    except Exception as exc:
        logger.error("Unexpected error reading %s: %s", path.name, exc)

    if not docs:
        logger.warning("No usable text extracted from %s", path.name)

    return docs


def read_all_pdfs(folder: Path) -> list[PageDocument]:
    """
    Walk *folder* and read every *.pdf file.
    Returns all page documents sorted by (file, page).
    """
    folder = Path(folder)
    if not folder.exists():
        raise FileNotFoundError(f"Literature folder not found: {folder}")

    paths = sorted(folder.glob("*.pdf"))
    if not paths:
        logger.warning("No PDF files found in %s", folder)
        return []

    logger.info("Found %d PDF files in %s", len(paths), folder)

    all_docs: list[PageDocument] = []
    for path in paths:
        logger.info("  Reading %-50s", path.name)
        pages = read_pdf(path)
        all_docs.extend(pages)
        logger.debug("    → %d pages", len(pages))

    logger.info(
        "Ingestion complete: %d pages from %d PDFs",
        len(all_docs), len(paths),
    )
    return all_docs


# ── helpers ──────────────────────────────────────────────────────────────────

def _clean_text(text: str) -> str:
    """Remove common PDF artefacts (ligatures, soft hyphens, excess whitespace)."""
    replacements = {
        "ﬁ": "fi",
        "ﬂ": "fl",
        "­": "",    # soft hyphen
        "–": "-",
        "—": "-",
        "‘": "'",
        "’": "'",
        "“": '"',
        "”": '"',
    }
    for orig, rep in replacements.items():
        text = text.replace(orig, rep)

    # collapse runs of whitespace but preserve paragraph breaks
    lines = [" ".join(line.split()) for line in text.splitlines()]
    return "\n".join(lines)
