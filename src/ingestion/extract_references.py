"""
extract_references.py  —  GeoHydroAI Stage 1 bibliography extraction
=====================================================================

Parses GROBID TEI XML bibliography sections into structured reference dicts.

Architecture position:
    Stage 1 (this) — pure local extraction, no external APIs.
    Stage 2        — OpenAlex DOI enrichment reads the extracted DOIs.

GROBID TEI structure targeted:
    <listBibl>                    ← only these, not sourceDesc/biblStruct
      <biblStruct xml:id="b0">
        <analytic>
          <title level="a">...</title>
          <author><persName>...</persName></author>
          <idno type="DOI">10.xxx/xxx</idno>
        </analytic>
        <monogr>
          <title level="j">...</title>   ← journal name
          <imprint>
            <date type="published" when="2019">2019</date>
          </imprint>
        </monogr>
      </biblStruct>
    </listBibl>

Output per reference:
    {
        "title":    str | None,
        "authors":  list[str],      # ["David C Mason", "Sarah Dance"]
        "year":     int | None,
        "doi":      str | None,     # normalised, no https://doi.org/ prefix
        "journal":  str | None,
        "raw_text": str | None,     # capped at 1000 chars
    }

Guarantees:
    - Never crashes ingestion; malformed biblStructs are skipped with a warning.
    - Does not retain any lxml element after parsing (only primitive values).
    - Deduplicates by DOI (primary) or normalised title (fallback).
"""

from __future__ import annotations

import logging
import re
from typing import Optional

log = logging.getLogger(__name__)

_NS  = "http://www.tei-c.org/ns/1.0"
_NSM = {"tei": _NS}

# XPath to reach only bibliography entries (excludes sourceDesc/biblStruct)
_BIBL_XPATH = ".//tei:listBibl/tei:biblStruct"

_DOI_PREFIXES = ("https://doi.org/", "http://doi.org/", "doi:")
_DOI_REGEX    = re.compile(r'\b(10\.\d{4,}/\S+)', re.IGNORECASE)
_YEAR_REGEX   = re.compile(r'\b(19|20)\d{2}\b')
_YEAR_WHEN    = re.compile(r'^(\d{4})')
_WHITESPACE   = re.compile(r'\s+')

_RAW_TEXT_LIMIT = 1000   # chars; enough for a full reference string


# ─────────────────────────────────────────────────────────────────────────────
# private helpers
# ─────────────────────────────────────────────────────────────────────────────

def _t(node) -> str:
    """Return stripped text content of a single element, or ''."""
    return (node.text or "").strip()


def _clean(text: str) -> str:
    return _WHITESPACE.sub(" ", text).strip()


def _node_text(node) -> Optional[str]:
    """Concatenate all text nodes under an element without retaining it."""
    parts = [t for t in node.itertext() if t and t.strip()]
    result = _clean(" ".join(parts))
    return result if result else None


def _raw_text(struct) -> Optional[str]:
    """Full text of a biblStruct, capped for memory safety."""
    parts = [t for t in struct.itertext() if t and t.strip()]
    raw = _clean(" ".join(parts))
    return raw[:_RAW_TEXT_LIMIT] if raw else None


def _extract_authors(struct) -> list[str]:
    """
    Return ["Forename Surname", ...] from all <author> elements.

    Combines all <forename> nodes (first + middle) with <surname>.
    Authors without a <persName> are skipped.
    """
    authors: list[str] = []
    for author in struct.iter("{%s}author" % _NS):
        pname = author.find("{%s}persName" % _NS)
        if pname is None:
            continue
        forenames = [
            _t(f) for f in pname.findall("{%s}forename" % _NS)
            if _t(f)
        ]
        surname = _t(pname.find("{%s}surname" % _NS)) if pname.find("{%s}surname" % _NS) is not None else ""
        parts = forenames + ([surname] if surname else [])
        full  = " ".join(parts).strip()
        if full:
            authors.append(full)
    return authors


def _extract_doi(struct) -> Optional[str]:
    """
    Return a normalised DOI string (no prefix, lowercase).

    Priority:
    1. <idno type="DOI"> — canonical GROBID location
    2. Regex over raw text — fallback for non-standard markup
    """
    for idno in struct.iter("{%s}idno" % _NS):
        if idno.get("type", "").upper() == "DOI":
            doi = _t(idno).lower()
            if doi:
                for prefix in _DOI_PREFIXES:
                    if doi.startswith(prefix):
                        doi = doi[len(prefix):]
                        break
                return doi if doi else None

    # regex fallback on raw text (avoids retaining the element)
    raw = _raw_text(struct) or ""
    m = _DOI_REGEX.search(raw)
    if m:
        doi = m.group(1).lower().rstrip(".,)>")
        return doi
    return None


def _extract_year(struct) -> Optional[int]:
    """
    Return publication year as int.

    Priority:
    1. date/@when attribute (format: "2019" or "2019-01-15")
    2. date element text
    3. Regex over raw text
    """
    for date in struct.iter("{%s}date" % _NS):
        when = (date.get("when") or "").strip()
        if when:
            m = _YEAR_WHEN.match(when)
            if m:
                return int(m.group(1))
        text = _t(date)
        if text:
            m = _YEAR_REGEX.search(text)
            if m:
                return int(m.group(0))

    raw = _raw_text(struct) or ""
    m = _YEAR_REGEX.search(raw)
    if m:
        return int(m.group(0))
    return None


def _extract_title(struct) -> Optional[str]:
    """
    Return the reference title.

    Priority:
    1. analytic/title (journal article title, level="a")
    2. analytic/title (any level — catch-all)
    3. monogr/title[@level="m"] (book / report / thesis)
    4. monogr/title (any — last resort, avoids returning a journal name)
    """
    analytic = struct.find("{%s}analytic" % _NS)
    if analytic is not None:
        # prefer level="a" (article title), then any title
        for title in analytic.findall("{%s}title" % _NS):
            txt = _node_text(title)
            if txt:
                return txt

    monogr = struct.find("{%s}monogr" % _NS)
    if monogr is not None:
        # level="m" → book/monograph title (not the journal name)
        for title in monogr.findall("{%s}title" % _NS):
            if title.get("level") == "m":
                txt = _node_text(title)
                if txt:
                    return txt
        # fall back to any monogr title (may be journal name for article-only refs)
        for title in monogr.findall("{%s}title" % _NS):
            txt = _node_text(title)
            if txt:
                return txt

    return None


def _extract_journal(struct) -> Optional[str]:
    """Return journal name from monogr/title[@level='j']."""
    monogr = struct.find("{%s}monogr" % _NS)
    if monogr is None:
        return None
    for title in monogr.findall("{%s}title" % _NS):
        if title.get("level") == "j":
            txt = _node_text(title)
            if txt:
                return txt
    return None


def _dedup_key(ref: dict) -> Optional[str]:
    """DOI if present, otherwise normalised title (no punctuation, lowercase)."""
    if ref.get("doi"):
        return ref["doi"]
    title = ref.get("title") or ""
    key = re.sub(r"[^\w]", "", title.lower())
    return key if key else None


# ─────────────────────────────────────────────────────────────────────────────
# public API
# ─────────────────────────────────────────────────────────────────────────────

def extract_references(root) -> list[dict]:
    """
    Parse bibliography from a GROBID TEI XML root element.

    Targets only <listBibl>/<biblStruct> nodes — excludes the paper's own
    <sourceDesc>/<biblStruct> which has no xml:id and is not a reference.

    Args:
        root: lxml root element of a parsed .tei.xml file.

    Returns:
        List of reference dicts, deduplicated by DOI or normalised title.
        Each dict: {"title", "authors", "year", "doi", "journal", "raw_text"}.
        Empty list if no bibliography section is found.
    """
    refs:  list[dict] = []
    seen:  set[str]   = set()

    for struct in root.xpath(_BIBL_XPATH, namespaces=_NSM):
        try:
            ref = {
                "title":    _extract_title(struct),
                "authors":  _extract_authors(struct),
                "year":     _extract_year(struct),
                "doi":      _extract_doi(struct),
                "journal":  _extract_journal(struct),
                "raw_text": _raw_text(struct),
            }

            key = _dedup_key(ref)
            if key:
                if key in seen:
                    continue
                seen.add(key)

            refs.append(ref)

        except Exception as exc:
            log.warning(
                "extract_references: skipping malformed biblStruct — %s: %s",
                type(exc).__name__, exc,
            )

    return refs
