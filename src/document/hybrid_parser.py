"""
hybrid_parser.py — HybridParser: GROBID + Nougat → merged TEIDocument.

Merge policy (V1 — conservative):
  GROBID wins for:  metadata, title, authors, affiliations, abstract,
                    section hierarchy, references, citation markers, coordinates.
  Nougat wins for:  formulas, markdown_text, visual_text.
  Augmented by Nougat when GROBID is weak (score < 0.4):
                    tables (if GROBID found none), additional sections.

The merged document's provenance lists both parsers' records so any audit
can trace every field back to its source.

Scanned PDF path:
  When GROBID returns no usable output, the Nougat document is returned as-is
  with parser_kind=NOUGAT.  References and citations will be absent.
"""

from __future__ import annotations

import dataclasses
import logging
import time
from pathlib import Path

from src.document.models import Formula, TEIDocument
from src.document.nougat_parser import NougatParser, NougatParseError
from src.document.parser import TEIParser
from src.document.parser_quality import (
    assess_quality,
    is_grobid_output_usable,
    needs_nougat_augmentation,
)
from src.document.provenance import (
    GROBID_CAPABILITIES,
    NOUGAT_CAPABILITIES,
    ParserCapability,
    ParserKind,
    ParserProvenance,
)

log = logging.getLogger(__name__)


class HybridParser:
    """
    Two-stage parser: GROBID for structure/metadata, Nougat for visual content.

    Usage::

        hp = HybridParser()
        doc = hp.parse_pdf(pdf_path, paper_id="abc123",
                           triage_result=triage,
                           grobid_xml=xml_text)  # xml may come pre-fetched
    """

    parser_name:    str = "hybrid"
    parser_version: str = "1.0"

    def __init__(
        self,
        tei_parser:    TEIParser    | None = None,
        nougat_parser: NougatParser | None = None,
        augment_formulas:    bool = True,
        augment_tables:      bool = True,
        augment_visual_text: bool = True,
    ) -> None:
        self._tei     = tei_parser    or TEIParser()
        self._nougat  = nougat_parser or NougatParser()
        self._aug_formulas    = augment_formulas
        self._aug_tables      = augment_tables
        self._aug_visual_text = augment_visual_text

    # ── Main entry point ──────────────────────────────────────────────────────

    def parse_pdf(
        self,
        pdf_path:      Path,
        paper_id:      str,
        grobid_xml:    str | None = None,
        triage_result  = None,
        force_nougat:  bool = False,
    ) -> TEIDocument:
        """
        Args:
            pdf_path:     Path to the PDF file (required for Nougat).
            paper_id:     Canonical paper identifier.
            grobid_xml:   Pre-fetched GROBID TEI XML string.  When None,
                          TEIParser.parse_file() is called on any matching .xml.
            triage_result: TriageResult from pdf_triage.py — used to detect
                          scanned PDFs; may be None.
            force_nougat: Run Nougat even when GROBID quality is acceptable.
        """
        t0 = time.perf_counter()
        is_scanned = (
            getattr(triage_result, "is_scanned", False)
            if triage_result is not None else False
        )

        # ── 1. GROBID path ────────────────────────────────────────────────────
        grobid_doc: TEIDocument | None = None
        grobid_elapsed = 0.0

        if not is_scanned and grobid_xml is not None:
            try:
                gt0        = time.perf_counter()
                grobid_doc = self._tei.parse_text(grobid_xml, paper_id)
                grobid_elapsed = time.perf_counter() - gt0
                log.info(
                    "[hybrid] grobid OK for %s | sections=%d refs=%d  %.2fs",
                    paper_id, len(grobid_doc.sections), len(grobid_doc.references),
                    grobid_elapsed,
                )
            except Exception as exc:
                log.warning("[hybrid] grobid parse failed for %s: %s", paper_id, exc)

        # ── 2. Quality assessment of GROBID output ────────────────────────────
        run_nougat = force_nougat or is_scanned
        if grobid_doc is not None:
            quality = assess_quality(grobid_doc, "hybrid")
            if needs_nougat_augmentation(quality) or not is_grobid_output_usable(quality):
                run_nougat = True
                log.info(
                    "[hybrid] weak GROBID (score=%.2f) → will run Nougat | %s",
                    quality.quality_score, paper_id,
                )
        else:
            run_nougat = True   # no GROBID output → must use Nougat

        # ── 3. Nougat path ────────────────────────────────────────────────────
        nougat_doc:    TEIDocument | None = None
        nougat_elapsed = 0.0

        if run_nougat:
            try:
                nt0        = time.perf_counter()
                nougat_doc = self._nougat.parse_pdf(pdf_path, paper_id)
                nougat_elapsed = time.perf_counter() - nt0
                log.info(
                    "[hybrid] nougat OK for %s | sections=%d formulas=%d tables=%d  %.2fs",
                    paper_id,
                    len(nougat_doc.sections),
                    len(nougat_doc.formulas),
                    len(nougat_doc.tables),
                    nougat_elapsed,
                )
            except NougatParseError as exc:
                log.warning("[hybrid] nougat failed for %s: %s", paper_id, exc)
            except Exception as exc:
                log.warning("[hybrid] nougat unexpected error for %s: %s", paper_id, exc)

        # ── 4. Merge or fallback ──────────────────────────────────────────────
        total_elapsed = time.perf_counter() - t0

        if grobid_doc is not None and nougat_doc is not None:
            merged = self._merge(
                grobid_doc, nougat_doc,
                grobid_elapsed=grobid_elapsed,
                nougat_elapsed=nougat_elapsed,
                strategy="hybrid",
            )
            log.info("[hybrid] merged | %s  %.2fs total", paper_id, total_elapsed)
            return merged

        if grobid_doc is not None:
            log.info("[hybrid] grobid-only | %s  %.2fs total", paper_id, total_elapsed)
            return self._annotate_grobid_only(grobid_doc, grobid_elapsed)

        if nougat_doc is not None:
            log.info("[hybrid] nougat-only | %s  %.2fs total", paper_id, total_elapsed)
            return nougat_doc

        # Both failed — return empty GROBID doc (non-null, but empty)
        log.error("[hybrid] both parsers failed for %s", paper_id)
        return self._empty_doc(paper_id)

    # ── Merge logic ───────────────────────────────────────────────────────────

    def _merge(
        self,
        grobid_doc:     TEIDocument,
        nougat_doc:     TEIDocument,
        grobid_elapsed: float,
        nougat_elapsed: float,
        strategy:       str,
    ) -> TEIDocument:
        """
        Merge GROBID (structure authority) with Nougat (visual authority).

        GROBID fields are authoritative unless clearly absent.
        Nougat fields augment where GROBID is weak.
        """
        # Formulas: merge both lists, dedup by text content
        merged_formulas = list(grobid_doc.formulas)
        if self._aug_formulas:
            existing_texts = {f.text for f in merged_formulas}
            for f in nougat_doc.formulas:
                if f.text not in existing_texts:
                    merged_formulas.append(dataclasses.replace(
                        f, xml_id=f"nougat-{f.xml_id}"
                    ))
                    existing_texts.add(f.text)

        # Tables: use GROBID tables; augment from Nougat if GROBID found none
        merged_tables = list(grobid_doc.tables)
        if self._aug_tables and not merged_tables:
            merged_tables = list(nougat_doc.tables)

        # Build merged provenance
        grobid_prov = ParserProvenance(
            parser_name    = "grobid",
            parser_version = grobid_doc.parser_version,
            source_format  = "tei_xml",
            confidence     = 1.0,
            capabilities   = GROBID_CAPABILITIES,
            elapsed_sec    = grobid_elapsed,
        )
        nougat_prov = ParserProvenance(
            parser_name    = "nougat",
            parser_version = nougat_doc.parser_version,
            source_format  = "pdf_image",
            confidence     = 0.80,
            capabilities   = NOUGAT_CAPABILITIES,
            elapsed_sec    = nougat_elapsed,
        )

        merged_capabilities = GROBID_CAPABILITIES | NOUGAT_CAPABILITIES
        if not merged_tables:
            merged_capabilities -= {ParserCapability.TABLES}
        if not merged_formulas:
            merged_capabilities -= {ParserCapability.FORMULAS}

        merged_doc = TEIDocument(
            paper_id   = grobid_doc.paper_id,
            # GROBID wins for all metadata
            title      = grobid_doc.title or nougat_doc.title,
            abstract   = grobid_doc.abstract or nougat_doc.abstract,
            authors    = grobid_doc.authors,
            affiliations= grobid_doc.affiliations,
            keywords   = grobid_doc.keywords,
            doi        = grobid_doc.doi,
            year       = grobid_doc.year,
            journal    = grobid_doc.journal,
            volume     = grobid_doc.volume,
            issue      = grobid_doc.issue,
            # GROBID wins for structure (sections, references, citations)
            sections   = grobid_doc.sections,
            figures    = grobid_doc.figures,
            references = grobid_doc.references,
            # Merged float elements
            tables     = merged_tables,
            formulas   = merged_formulas,
            # Legacy fields
            parser         = "hybrid",
            parser_version = f"grobid+nougat",
            # New provenance fields
            parser_kind         = ParserKind.HYBRID,
            parser_capabilities = merged_capabilities,
            parser_provenance   = [grobid_prov, nougat_prov],
            # Nougat supplementary text
            markdown_text = nougat_doc.markdown_text if self._aug_visual_text else None,
            visual_text   = nougat_doc.visual_text   if self._aug_visual_text else None,
            source_paths  = grobid_doc.source_paths + nougat_doc.source_paths,
        )

        return merged_doc

    def _annotate_grobid_only(
        self, doc: TEIDocument, elapsed: float
    ) -> TEIDocument:
        """Attach provenance to a GROBID-only document."""
        prov = ParserProvenance(
            parser_name    = "grobid",
            parser_version = doc.parser_version,
            source_format  = "tei_xml",
            confidence     = 1.0,
            capabilities   = GROBID_CAPABILITIES,
            elapsed_sec    = elapsed,
        )
        return dataclasses.replace(
            doc,
            parser_kind=ParserKind.GROBID,
            parser_capabilities=GROBID_CAPABILITIES,
            parser_provenance=[prov],
        )

    @staticmethod
    def _empty_doc(paper_id: str) -> TEIDocument:
        return TEIDocument(
            paper_id=paper_id, title="", abstract="",
            authors=[], affiliations=[], keywords=[],
            doi=None, year=None, journal=None, volume=None, issue=None,
            sections=[], figures=[], tables=[], formulas=[], references=[],
            parser="hybrid", parser_kind=ParserKind.HYBRID,
        )
