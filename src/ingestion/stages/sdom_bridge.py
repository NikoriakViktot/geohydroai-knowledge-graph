"""
sdom_bridge.py — Convert TEIDocument objects to the pipeline dict shapes.

These functions replace the raw lxml parse + parse_sections() + parse_authors()
calls when a pre-parsed TEIDocument is available.  No lxml types are used here.

They are extracted from pipeline.py so that other consumers (e.g., the future
PaperAssembler) can use them without importing all of pipeline.py.

Circular-import note: section_tags / split_inline_sections / _reclassify_from_other
live in pipeline.py and are imported lazily inside functions to avoid circular
import at module load time.
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from src.ingestion.utils import doi_to_url, scholar_url

if TYPE_CHECKING:
    from src.document.models import TEIDocument


def metadata_from_doc(doc: "TEIDocument", xml_path: Path) -> dict:
    """TEIDocument header → the metadata dict shape produced by parse_metadata()."""
    from src.ingestion.pipeline import normalize_country_name

    aff_map = {a.key: a for a in doc.affiliations}
    authors = []
    for author in doc.authors:
        affs      = [aff_map[k] for k in author.affiliation_keys if k in aff_map]
        aff_text  = affs[0].raw if affs else None
        countries = [normalize_country_name(a.country) for a in affs if a.country]
        countries = [c for c in countries if c]
        authors.append({
            "first_name":  author.first or None,
            "last_name":   author.last  or None,
            "full_name":   author.full_name or None,
            "email":       author.email,
            "orcid":       author.orcid,
            "affiliation": aff_text,
            "affiliation_countries": countries,
        })
    return {
        "paper_id":   doc.paper_id,
        "source_xml": str(xml_path),
        "title":      doc.title or None,
        "doi":        doc.doi,
        "url":        doi_to_url(doc.doi) or scholar_url(doc.title),
        "year":       str(doc.year) if doc.year else None,
        "journal":    doc.journal,
        "publisher":  None,
        "authors":    authors,
    }


def sections_from_doc(doc: "TEIDocument") -> dict:
    """
    Reproduce parse_sections(root) output from a TEIDocument.

    Each section (including nested subsections) is tagged by its own title
    so that a '2.2 Methods' subsection under an untitled parent lands in
    'methods', not 'other'.
    """
    from src.ingestion.pipeline import (
        section_tags,
        split_inline_sections,
        _reclassify_from_other,
    )

    secs = {
        "abstract": "", "introduction": "", "study_area": "",
        "data_sources": "", "methods": "", "results": "",
        "discussion": "", "conclusion": "", "other": "",
    }
    if doc.abstract:
        secs["abstract"] = doc.abstract + "\n"

    def _add_section(sec) -> None:
        para_text = "\n\n".join(p.text for p in sec.paragraphs if p.text)
        if para_text:
            tags = section_tags(sec.title, sec.n, para_text)
            if tags == {"other"}:
                for tag, block in split_inline_sections(para_text).items():
                    if tag in secs:
                        secs[tag] += block + "\n"
            else:
                for tag in tags:
                    if tag in secs:
                        secs[tag] += para_text + "\n"
        for sub in sec.subsections:
            _add_section(sub)

    for sec in doc.sections:
        _add_section(sec)
    return _reclassify_from_other(secs)


def ref_to_pipeline_dict(ref) -> dict:
    """TEIDocument Reference → the dict shape produced by extract_references()."""
    return {
        "title":    ref.title or None,
        "authors":  [a.full_name for a in ref.authors],
        "year":     ref.year,
        "doi":      ref.doi,
        "journal":  ref.journal or None,
        "raw_text": ref.raw or None,
    }
