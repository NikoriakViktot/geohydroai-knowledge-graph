"""
nougat_actor.py — Ray actor wrapping NougatParser.

Mirrors the pattern used by EmbeddingActor and SpacyActor:
  - One actor instance per Ray cluster.
  - Model loaded once on __init__; reused for all remote calls.
  - Returns serialisable dicts rather than TEIDocument instances
    (dataclasses with frozenset fields are not always Ray-serialisable).

Usage::

    nougat_actor = NougatActor.remote()
    result = ray.get(nougat_actor.parse_pdf.remote(str(pdf_path), paper_id))
    # result is a dict produced by TEIDocument.summary() + key content fields
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import ray

log = logging.getLogger(__name__)


@ray.remote(
    num_gpus=float(os.getenv("NOUGAT_GPU_FRACTION", "0.5")),
    max_concurrency=2,  # serialises inference; NougatParser is not thread-safe under concurrent calls
    max_restarts=1,     # Фаза 3.2: автоматичний рестарт після падіння актора
)
class NougatActor:
    """
    Ray actor: one NougatParser instance, shared across all task calls.

    GPU fraction is configurable via NOUGAT_GPU_FRACTION (default 0.5).
    Set to 0 to run on CPU only.
    """

    def __init__(self) -> None:
        from src.document.nougat_parser import NougatParser
        self._parser = NougatParser()
        # Pre-warm model to surface load errors at startup, not first parse
        try:
            self._parser._ensure_model_loaded()
            log.info("[NougatActor] model ready")
        except Exception as exc:
            log.warning("[NougatActor] model warm-up failed (non-fatal): %s", exc)

    def parse_pdf(self, pdf_path: str, paper_id: str) -> dict[str, Any]:
        """
        Parse one PDF and return a serialisable dict.

        Returns a dict with:
          summary  — TEIDocument.summary()
          sections — list of {title, text} dicts
          formulas — list of {xml_id, text}
          tables   — list of {xml_id, label, caption, rows}
          markdown_text — raw Nougat markdown or None
          visual_text   — plain body text or None
          parser_kind   — "nougat"
          error         — error string if parse failed, else None
        """
        try:
            doc = self._parser.parse_pdf(Path(pdf_path), paper_id)
            return _doc_to_dict(doc)
        except Exception as exc:
            log.error("[NougatActor] parse_pdf failed for %s: %s", paper_id, exc)
            return {"error": str(exc), "paper_id": paper_id, "parser_kind": "nougat"}

    def parse_markdown(self, markdown_text: str, paper_id: str) -> dict[str, Any]:
        """Parse Nougat markdown text directly (no model inference)."""
        try:
            doc = self._parser.parse_text(markdown_text, paper_id)
            return _doc_to_dict(doc)
        except Exception as exc:
            log.error("[NougatActor] parse_markdown failed for %s: %s", paper_id, exc)
            return {"error": str(exc), "paper_id": paper_id}

    def model_info(self) -> dict[str, Any]:
        return self._parser.model_info()

    def parse_image(self, image, paper_id: str) -> dict[str, Any]:
        try:
            doc = self._parser.parse_image(image=image, paper_id=paper_id)
            return _doc_to_dict(doc)
        except Exception as exc:
            log.error("[NougatActor] parse_image failed for %s: %s", paper_id, exc)
            return {
                "error": str(exc),
                "paper_id": paper_id,
                "parser_kind": "nougat",
            }


# ── Serialisation helper ───────────────────────────────────────────────────────

def _doc_to_dict(doc) -> dict[str, Any]:
    """Convert TEIDocument to a Ray-serialisable dict."""
    return {
        "summary":       doc.summary(),
        "sections": [
            {"title": s.title, "level": s.level, "text": s.text}
            for s in doc.sections
        ],
        "formulas": [
            {"xml_id": f.xml_id, "text": f.text}
            for f in doc.formulas
        ],
        "tables": [
            {
                "xml_id":  t.xml_id,
                "label":   t.label,
                "caption": t.caption,
                "rows":    [list(r) for r in t.rows],
            }
            for t in doc.tables
        ],
        "markdown_text": doc.markdown_text,
        "visual_text":   doc.visual_text,
        "parser_kind":   doc.parser_kind.value,
        "error":         None,
    }
