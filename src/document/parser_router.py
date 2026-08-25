"""
parser_router.py — Route PDFs to the appropriate parser based on strategy
                   and triage result.

Strategies
----------
auto                       triage-driven: scanned → Nougat; text → GROBID fallback Nougat
grobid_only                GROBID always; Nougat never
nougat_only                Nougat always; GROBID never
hybrid                     Always run both and merge
grobid_with_nougat_fallback GROBID first; Nougat only when GROBID fails or quality is low

Default (PARSER_STRATEGY env var, default "grobid_with_nougat_fallback").

Architecture note
-----------------
ParserRouter owns the GROBID HTTP call when it needs to run GROBID without
a pre-fetched XML string.  It imports GROBIDClient but only instantiates it
when the strategy requires GROBID.  This keeps NougatParser independent of
the GROBID HTTP layer.

Usage::

    from src.document import ParserRouter

    router = ParserRouter(strategy="auto")
    doc = router.parse_pdf(pdf_path, paper_id=paper_id)

    print(doc.parser_kind)
    print(doc.parser_capabilities)
    print(doc.title)
    print(len(doc.formulas))
    print(doc.body_text()[:500])
"""

from __future__ import annotations

import dataclasses
import logging
import os
import time
from pathlib import Path

from src.document.hybrid_parser import HybridParser
from src.document.models import TEIDocument
from src.document.nougat_parser import NougatParser, NougatParseError
from src.document.parser import TEIParser
from src.document.parser_quality import assess_quality, is_grobid_output_usable
from src.document.provenance import (
    GROBID_CAPABILITIES,
    NOUGAT_CAPABILITIES,
    ParserKind,
    ParserProvenance,
)

log = logging.getLogger(__name__)

# ── Valid strategy names ───────────────────────────────────────────────────────

STRATEGIES = frozenset({
    "auto",
    "grobid_only",
    "nougat_only",
    "hybrid",
    "grobid_with_nougat_fallback",
})

_DEFAULT_STRATEGY = os.getenv("PARSER_STRATEGY", "grobid_with_nougat_fallback")


class RouterConfig:
    """Configuration snapshot consumed by ParserRouter."""
    def __init__(
        self,
        strategy:          str   = _DEFAULT_STRATEGY,
        augment_formulas:  bool  = os.getenv("HYBRID_AUGMENT_FORMULAS",    "true").lower() != "false",
        augment_tables:    bool  = os.getenv("HYBRID_AUGMENT_TABLES",      "true").lower() != "false",
        augment_visual:    bool  = os.getenv("HYBRID_AUGMENT_VISUAL_TEXT", "true").lower() != "false",
        nougat_enabled:    bool  = os.getenv("NOUGAT_ENABLED",             "true").lower() not in {"0","false","no"},
    ) -> None:
        if strategy not in STRATEGIES:
            raise ValueError(f"Unknown strategy {strategy!r}. Choose from {sorted(STRATEGIES)}")
        self.strategy         = strategy
        self.augment_formulas = augment_formulas
        self.augment_tables   = augment_tables
        self.augment_visual   = augment_visual
        self.nougat_enabled   = nougat_enabled


class ParserRouter:
    """
    Route a PDF through GROBID, Nougat, or HybridParser based on strategy.

    The router is stateful: lazy-initialised parser instances are reused
    across multiple parse_pdf() calls so the Nougat model loads only once.
    """

    def __init__(
        self,
        strategy: str | None = None,
        config:   RouterConfig | None = None,
    ) -> None:
        if config is None:
            config = RouterConfig(strategy=strategy or _DEFAULT_STRATEGY)
        self._cfg = config

        # Parsers — lazy initialised
        self._tei_parser:    TEIParser    | None = None
        self._nougat_parser: NougatParser | None = None
        self._hybrid_parser: HybridParser | None = None

    # ── Main entry point ──────────────────────────────────────────────────────

    def parse_pdf(
        self,
        pdf_path:     Path,
        paper_id:     str,
        grobid_xml:   str | None = None,
        triage_result = None,
    ) -> TEIDocument:
        """
        Route and parse one PDF.

        Args:
            pdf_path:     Path to the PDF file.
            paper_id:     Canonical paper identifier.
            grobid_xml:   Pre-fetched GROBID TEI XML (avoids an HTTP round-trip).
                          When None, some strategies will call GROBID internally.
            triage_result: TriageResult from pdf_triage.triage_pdf(); None is safe.

        Returns:
            TEIDocument with parser_kind, parser_capabilities, parser_provenance,
            markdown_text, visual_text populated.
        """
        strategy  = self._cfg.strategy
        is_scanned= getattr(triage_result, "is_scanned", False)
        has_text  = getattr(triage_result, "has_text",   True)

        log.info(
            "[router] %s | strategy=%s scanned=%s has_text=%s",
            paper_id, strategy, is_scanned, has_text,
        )

        if strategy == "nougat_only":
            return self._run_nougat(pdf_path, paper_id)

        if strategy == "grobid_only":
            return self._run_grobid(grobid_xml, pdf_path, paper_id)

        if strategy == "hybrid":
            return self._run_hybrid(pdf_path, paper_id, grobid_xml, triage_result,
                                    force_nougat=True)

        if strategy == "auto":
            return self._run_auto(pdf_path, paper_id, grobid_xml, triage_result,
                                  is_scanned, has_text)

        # "grobid_with_nougat_fallback" (default)
        return self._run_grobid_with_fallback(pdf_path, paper_id, grobid_xml, triage_result)

    # ── Strategy implementations ──────────────────────────────────────────────

    def _run_nougat(self, pdf_path: Path, paper_id: str) -> TEIDocument:
        log.info("[router] strategy=nougat_only | %s", paper_id)
        try:
            return self._get_nougat().parse_pdf(pdf_path, paper_id)
        except NougatParseError as exc:
            log.error("[router] nougat_only failed: %s", exc)
            return _empty_doc(paper_id, "nougat_only")

    def _run_grobid(
        self, grobid_xml: str | None, pdf_path: Path, paper_id: str
    ) -> TEIDocument:
        log.info("[router] strategy=grobid_only | %s", paper_id)
        if grobid_xml is None:
            log.warning("[router] grobid_only but no pre-fetched XML for %s", paper_id)
            return _empty_doc(paper_id, "grobid_only")
        try:
            doc = self._get_tei().parse_text(grobid_xml, paper_id)
            return dataclasses.replace(
                doc,
                parser_kind=ParserKind.GROBID,
                parser_capabilities=GROBID_CAPABILITIES,
                parser_provenance=[ParserProvenance(
                    parser_name="grobid", parser_version=doc.parser_version,
                    source_format="tei_xml", capabilities=GROBID_CAPABILITIES,
                )],
            )
        except Exception as exc:
            log.error("[router] grobid parse failed for %s: %s", paper_id, exc)
            return _empty_doc(paper_id, "grobid_only")

    def _run_hybrid(
        self,
        pdf_path:     Path,
        paper_id:     str,
        grobid_xml:   str | None,
        triage_result,
        force_nougat: bool = False,
    ) -> TEIDocument:
        log.info("[router] strategy=hybrid | %s", paper_id)
        return self._get_hybrid().parse_pdf(
            pdf_path, paper_id,
            grobid_xml=grobid_xml,
            triage_result=triage_result,
            force_nougat=force_nougat,
        )

    def _run_auto(
        self,
        pdf_path:     Path,
        paper_id:     str,
        grobid_xml:   str | None,
        triage_result,
        is_scanned:   bool,
        has_text:     bool,
    ) -> TEIDocument:
        if is_scanned or not has_text:
            log.info("[router] auto → nougat (scanned/no-text) | %s", paper_id)
            return self._run_nougat(pdf_path, paper_id)
        # text PDF → GROBID with Nougat fallback
        log.info("[router] auto → grobid_with_nougat_fallback | %s", paper_id)
        return self._run_grobid_with_fallback(pdf_path, paper_id, grobid_xml, triage_result)

    def _run_grobid_with_fallback(
        self,
        pdf_path:     Path,
        paper_id:     str,
        grobid_xml:   str | None,
        triage_result,
    ) -> TEIDocument:
        """GROBID primary; Nougat used if GROBID quality < threshold or fails."""
        if grobid_xml is not None:
            try:
                tei = self._get_tei()
                doc = tei.parse_text(grobid_xml, paper_id)
                quality = assess_quality(doc, "grobid_with_nougat_fallback")

                if is_grobid_output_usable(quality) and not self._cfg.augment_formulas:
                    # Pure GROBID, quality is fine
                    return dataclasses.replace(
                        doc,
                        parser_kind=ParserKind.GROBID,
                        parser_capabilities=GROBID_CAPABILITIES,
                        parser_provenance=[ParserProvenance(
                            parser_name="grobid", parser_version=doc.parser_version,
                            source_format="tei_xml", capabilities=GROBID_CAPABILITIES,
                        )],
                    )

                # Augment with Nougat
                log.info(
                    "[router] grobid quality=%.2f → adding Nougat for %s",
                    quality.quality_score, paper_id,
                )
            except Exception as exc:
                log.warning("[router] grobid failed for %s: %s", paper_id, exc)

        return self._run_hybrid(pdf_path, paper_id, grobid_xml, triage_result)

    # ── Lazy parser accessors ─────────────────────────────────────────────────

    def _get_tei(self) -> TEIParser:
        if self._tei_parser is None:
            self._tei_parser = TEIParser()
        return self._tei_parser

    def _get_nougat(self) -> NougatParser:
        if self._nougat_parser is None:
            self._nougat_parser = NougatParser()
        return self._nougat_parser

    def _get_hybrid(self) -> HybridParser:
        if self._hybrid_parser is None:
            self._hybrid_parser = HybridParser(
                tei_parser           = self._get_tei(),
                nougat_parser        = self._get_nougat(),
                augment_formulas     = self._cfg.augment_formulas,
                augment_tables       = self._cfg.augment_tables,
                augment_visual_text  = self._cfg.augment_visual,
            )
        return self._hybrid_parser


# ── Helpers ───────────────────────────────────────────────────────────────────

def _empty_doc(paper_id: str, strategy: str) -> TEIDocument:
    return TEIDocument(
        paper_id=paper_id, title="", abstract="",
        authors=[], affiliations=[], keywords=[],
        doi=None, year=None, journal=None, volume=None, issue=None,
        sections=[], figures=[], tables=[], formulas=[], references=[],
        parser=strategy, parser_kind=ParserKind.UNKNOWN,
    )
