"""
parser.py — TEI XML → TEIDocument.

This is the ONLY file in the entire codebase permitted to import lxml or
touch raw TEI XML strings.  Every other module receives a TEIDocument.

GROBID TEI structure (abbreviated):
  <TEI>
    <teiHeader>
      <fileDesc>
        <titleStmt><title level="a">…</title></titleStmt>
        <sourceDesc><biblStruct>  ← journal/volume/DOI of the paper itself
          <analytic><author><persName>…</persName><affiliation key="aff0">…
          <monogr><title level="j">…</title><imprint>…
        </biblStruct></sourceDesc>
      </fileDesc>
      <profileDesc>
        <abstract><div><p>…</p></div></abstract>
        <textClass><keywords><term>…
      </profileDesc>
    </teiHeader>
    <text>
      <body>
        <div><head n="1">Introduction</head>
          <p><s coords="1,72,100,400,10">…<ref type="bibr" target="#b0">[1]</ref>…</s></p>
          <figure xml:id="fig_0" coords="…"><head>Figure 1.</head>
            <label>1</label><figDesc>Caption</figDesc><graphic coords="…"/>
          </figure>
          <figure xml:id="tab_0" type="table" coords="…">…</figure>
        </div>
      </body>
      <back>
        <div type="references"><listBibl>
          <biblStruct xml:id="b0">
            <analytic><title level="a">…</title><author>…
            <monogr><title level="j">…</title><imprint>…
            <idno type="DOI">…
            <note type="raw_reference">…
          </biblStruct>
        </listBibl></div>
      </back>
    </text>
  </TEI>
"""

from __future__ import annotations

import logging
from pathlib import Path

from lxml import etree

from src.document.coordinates import Coordinates, parse_coords
from src.document.models import (
    Affiliation,
    Author,
    CitationMarker,
    Figure,
    Formula,
    Paragraph,
    Reference,
    Section,
    Sentence,
    Table,
    TEIDocument,
)

log = logging.getLogger(__name__)

_NS  = {"tei": "http://www.tei-c.org/ns/1.0"}
_TEI = "http://www.tei-c.org/ns/1.0"
_XML = "http://www.w3.org/XML/1998/namespace"

_T = f"{{{_TEI}}}"   # prefix shorthand for Clark notation: {ns}localname


def _tag(local: str) -> str:
    return f"{_T}{local}"


#: Elements whose boundaries are word boundaries. GROBID writes its <s> sentences (and
#: <p>, <head>, <div>) with no whitespace between them, so a plain itertext() join glued
#: "planning.The Height" in ~92 % of abstracts and in most figure captions.
_BLOCK = frozenset(_tag(x) for x in ("s", "p", "div", "head", "note", "figDesc", "label", "item", "row", "cell", "lb"))


def _text(el) -> str:
    """Text of an element; '' when el is None.

    Inline markup (<ref>, <hi>, <formula>) is joined as written; a sentence or block
    boundary becomes one space unless whitespace is already there. Comment text is skipped.
    """
    if el is None:
        return ""
    out: list[str] = []

    def gap() -> None:
        if out and not out[-1][-1:].isspace():
            out.append(" ")

    def walk(e) -> None:
        block = e.tag in _BLOCK
        if block:
            gap()
        if e.text:
            out.append(e.text)
        for child in e:
            if isinstance(child.tag, str):
                walk(child)
            if child.tail:
                out.append(child.tail)
        if block:
            gap()

    walk(el)
    return "".join(out).strip()


def _attr(el, name: str, default: str = "") -> str:
    if el is None:
        return default
    return el.get(name, default) or default


def _xml_id(el) -> str:
    return el.get(f"{{{_XML}}}id", "")


class TEIParser:
    """
    GROBID TEI XML → TEIDocument.

    Stateless: every call to parse_text / parse_file is independent.
    Thread-safe to use from multiple threads simultaneously.
    """

    parser_name:    str = "grobid"
    parser_version: str = ""

    # ── Public entry points ───────────────────────────────────────────────────

    def parse_text(self, xml_text: str, paper_id: str) -> TEIDocument:
        """Parse from an in-memory TEI XML string (e.g. straight from GROBID)."""
        try:
            root = etree.fromstring(xml_text.encode("utf-8"))
        except etree.XMLSyntaxError as exc:
            raise ValueError(f"Invalid TEI XML for {paper_id}: {exc}") from exc
        return self._build(root, paper_id)

    def parse_file(self, path: Path, paper_id: str) -> TEIDocument:
        """Parse from a TEI XML file on disk."""
        return self.parse_text(path.read_text(encoding="utf-8"), paper_id)

    # ── Top-level builder ─────────────────────────────────────────────────────

    def _build(self, root: etree._Element, paper_id: str) -> TEIDocument:
        header             = self._parse_header(root)
        figures, tables, formulas = self._parse_floats(root)
        sections           = self._parse_body(root)
        references         = self._parse_references(root)

        return TEIDocument(
            paper_id    = paper_id,
            title       = header["title"],
            abstract    = header["abstract"],
            authors     = header["authors"],
            affiliations= header["affiliations"],
            keywords    = header["keywords"],
            doi         = header["doi"],
            year        = header["year"],
            journal     = header["journal"],
            volume      = header["volume"],
            issue       = header["issue"],
            sections    = sections,
            figures     = figures,
            tables      = tables,
            formulas    = formulas,
            references  = references,
            parser      = self.parser_name,
            parser_version = self.parser_version,
        )

    # ── Header ────────────────────────────────────────────────────────────────

    def _parse_header(self, root: etree._Element) -> dict:
        title = _text(self._first(root, "//tei:titleStmt/tei:title[@level='a' or @level='m']"))

        # abstract — may be flat or wrapped in divs
        abstract_parts = root.xpath("//tei:abstract//tei:p", namespaces=_NS)
        if not abstract_parts:
            abstract_parts = root.xpath("//tei:abstract", namespaces=_NS)
        abstract = " ".join(_text(el) for el in abstract_parts).strip()

        # keywords
        kw_els   = root.xpath("//tei:keywords/tei:term", namespaces=_NS)
        keywords = [_text(el) for el in kw_els if _text(el)]

        # source biblStruct (the paper's own bibliographic identity)
        affiliations = self._parse_affiliations(root)
        aff_index    = {a.key: a for a in affiliations}
        authors      = self._parse_authors(root, aff_index)

        journal = _text(self._first(root, "//tei:sourceDesc//tei:title[@level='j']"))
        doi_el  = self._first(root, "//tei:sourceDesc//tei:idno[@type='DOI']")
        doi     = doi_el.text.strip() if doi_el is not None and doi_el.text else None

        year    = self._parse_year(root, "//tei:sourceDesc//tei:date[@type='published']/@when")
        vol     = _text(self._first(root, "//tei:sourceDesc//tei:biblScope[@unit='volume']"))
        issue   = _text(self._first(root, "//tei:sourceDesc//tei:biblScope[@unit='issue']"))

        return {
            "title":        title,
            "abstract":     abstract,
            "authors":      authors,
            "affiliations": affiliations,
            "keywords":     keywords,
            "doi":          doi,
            "year":         year,
            "journal":      journal or None,
            "volume":       vol or None,
            "issue":        issue or None,
        }

    def _parse_affiliations(self, root: etree._Element) -> list[Affiliation]:
        aff_els    = root.xpath("//tei:sourceDesc//tei:affiliation", namespaces=_NS)
        affiliations: list[Affiliation] = []
        seen: set[str] = set()

        for el in aff_els:
            key = _attr(el, "key")
            if key in seen:
                continue
            seen.add(key)

            institution = _text(el.find(f".//{_tag('orgName')}[@type='institution']"))
            department  = _text(el.find(f".//{_tag('orgName')}[@type='department']"))
            laboratory  = _text(el.find(f".//{_tag('orgName')}[@type='laboratory']"))

            addr        = el.find(_tag("address"))
            country_el  = addr.find(_tag("country")) if addr is not None else None
            country     = _text(country_el)
            country_code= _attr(country_el, "key")
            settlement  = _text(addr.find(_tag("settlement")) if addr is not None else None)
            postcode    = _text(addr.find(_tag("postCode"))   if addr is not None else None)
            address     = ", ".join(p for p in [settlement, postcode, country] if p)

            affiliations.append(Affiliation(
                key          = key,
                institution  = institution,
                department   = department,
                laboratory   = laboratory,
                address      = address,
                country      = country,
                country_code = country_code,
                raw          = "".join(el.itertext()).strip(),
            ))
        return affiliations

    def _parse_authors(
        self,
        root: etree._Element,
        aff_index: dict[str, Affiliation],
    ) -> list[Author]:
        author_els = root.xpath("//tei:sourceDesc//tei:author", namespaces=_NS)
        authors: list[Author] = []

        for el in author_els:
            pn = el.find(_tag("persName"))
            if pn is None:
                continue

            first  = _text(pn.find(f"{_tag('forename')}[@type='first']"))
            mid_els= pn.findall(f"{_tag('forename')}[@type='middle']")
            middle = " ".join(_text(m) for m in mid_els)
            last   = _text(pn.find(_tag("surname")))
            if not last:
                continue

            aff_keys = tuple(
                _attr(a, "key")
                for a in el.findall(_tag("affiliation"))
                if _attr(a, "key")
            )
            email_el = el.find(f".//{_tag('email')}")
            email    = email_el.text.strip() if email_el is not None and email_el.text else None
            orcid_el = el.find(f"{_tag('idno')}[@type='ORCID']")
            orcid    = orcid_el.text.strip() if orcid_el is not None and orcid_el.text else None

            authors.append(Author(
                first           = first,
                middle          = middle,
                last            = last,
                affiliation_keys= aff_keys,
                email           = email,
                orcid           = orcid,
            ))
        return authors

    # ── Float elements (figures, tables, formulas) ────────────────────────────

    def _parse_floats(
        self,
        root: etree._Element,
    ) -> tuple[list[Figure], list[Table], list[Formula]]:
        figures:  list[Figure]  = []
        tables:   list[Table]   = []
        formulas: list[Formula] = []

        for fig_el in root.xpath("//tei:figure[not(@type='table')]", namespaces=_NS):
            xml_id     = _xml_id(fig_el)
            label      = _text(fig_el.find(_tag("label")))
            head       = _text(fig_el.find(_tag("head")))
            caption    = _text(fig_el.find(_tag("figDesc")))
            coords     = parse_coords(_attr(fig_el, "coords"))
            graphic_el = fig_el.find(_tag("graphic"))
            graphic_c  = parse_coords(_attr(graphic_el, "coords")) if graphic_el is not None else None

            figures.append(Figure(
                xml_id        = xml_id,
                label         = label or head,
                caption       = caption,
                coords        = coords,
                graphic_coords= graphic_c,
            ))

        for tab_el in root.xpath("//tei:figure[@type='table']", namespaces=_NS):
            xml_id  = _xml_id(tab_el)
            label   = _text(tab_el.find(_tag("label")))
            head    = _text(tab_el.find(_tag("head")))
            caption = _text(tab_el.find(_tag("figDesc")))
            coords  = parse_coords(_attr(tab_el, "coords"))
            rows    = self._parse_table_rows(tab_el)

            tables.append(Table(
                xml_id  = xml_id,
                label   = label or head,
                caption = caption,
                coords  = coords,
                rows    = rows,
            ))

        for form_el in root.xpath("//tei:formula", namespaces=_NS):
            formulas.append(Formula(
                xml_id = _xml_id(form_el),
                text   = "".join(form_el.itertext()).strip(),
                coords = parse_coords(_attr(form_el, "coords")),
            ))

        return figures, tables, formulas

    def _parse_table_rows(
        self,
        tab_el: etree._Element,
    ) -> tuple[tuple[str, ...], ...]:
        table_el = tab_el.find(f".//{_tag('table')}")
        if table_el is None:
            return ()
        rows = []
        for row_el in table_el.findall(_tag("row")):
            cells = tuple(
                "".join(cell_el.itertext()).strip()
                for cell_el in row_el.findall(_tag("cell"))
            )
            if cells:
                rows.append(cells)
        return tuple(rows)

    # ── Body ──────────────────────────────────────────────────────────────────

    def _parse_body(self, root: etree._Element) -> list[Section]:
        body = root.find(f".//{_tag('body')}")
        if body is None:
            return []
        return [
            sec
            for div in body.findall(_tag("div"))
            if (sec := self._parse_div(div, level=1)) is not None
        ]

    def _parse_div(self, div_el: etree._Element, level: int) -> Section | None:
        head_el = div_el.find(_tag("head"))
        title   = _text(head_el)
        n       = _attr(head_el, "n")
        coords  = parse_coords(_attr(div_el, "coords"))

        paragraphs = [
            self._parse_paragraph(p_el)
            for p_el in div_el.findall(_tag("p"))
        ]
        subsections = [
            sub
            for sub_el in div_el.findall(_tag("div"))
            if (sub := self._parse_div(sub_el, level + 1)) is not None
        ]

        if not paragraphs and not subsections and not title:
            return None

        return Section(
            title       = title,
            level       = level,
            n           = n,
            paragraphs  = paragraphs,
            subsections = subsections,
            coords      = coords,
        )

    def _parse_paragraph(self, p_el: etree._Element) -> Paragraph:
        coords  = parse_coords(_attr(p_el, "coords"))
        s_els   = p_el.findall(_tag("s"))

        if s_els:
            sentences = tuple(self._parse_sentence(s_el) for s_el in s_els)
        else:
            # GROBID did not segment sentences — treat whole <p> as one unit
            text = "".join(p_el.itertext()).strip()
            sentences = (Sentence(
                xml_id    = "",
                text      = text,
                coords    = coords,
                citations = tuple(self._collect_citation_markers(p_el)),
            ),) if text else ()

        return Paragraph(sentences=sentences, coords=coords)

    def _parse_sentence(self, s_el: etree._Element) -> Sentence:
        return Sentence(
            xml_id    = _xml_id(s_el),
            text      = "".join(s_el.itertext()).strip(),
            coords    = parse_coords(_attr(s_el, "coords")),
            citations = tuple(self._collect_citation_markers(s_el)),
        )

    def _collect_citation_markers(
        self,
        el: etree._Element,
    ) -> list[CitationMarker]:
        markers: list[CitationMarker] = []
        for ref_el in el.findall(f".//{_tag('ref')}[@type='bibr']"):
            target = _attr(ref_el, "target").lstrip("#")
            label  = "".join(ref_el.itertext()).strip()
            if target:
                markers.append(CitationMarker(
                    ref_id = target,
                    label  = label,
                    coords = parse_coords(_attr(ref_el, "coords")),
                ))
        return markers

    # ── References ────────────────────────────────────────────────────────────

    def _parse_references(self, root: etree._Element) -> list[Reference]:
        refs: list[Reference] = []
        for bib_el in root.xpath("//tei:listBibl/tei:biblStruct", namespaces=_NS):
            ref = self._parse_biblstruct(bib_el)
            if ref is not None:
                refs.append(ref)
        return refs

    def _parse_biblstruct(self, bib_el: etree._Element) -> Reference | None:
        xml_id   = _xml_id(bib_el)
        coords   = parse_coords(_attr(bib_el, "coords"))
        analytic = bib_el.find(_tag("analytic"))
        monogr   = bib_el.find(_tag("monogr"))

        # title: prefer analytic-level, fall back to monogr
        title = ""
        if analytic is not None:
            title = _text(analytic.find(f"{_tag('title')}[@level='a']"))
            if not title:
                title = _text(analytic.find(_tag("title")))
        if not title and monogr is not None:
            title = _text(monogr.find(_tag("title")))

        # authors (from analytic, or bib_el directly)
        source  = analytic if analytic is not None else bib_el
        authors = self._parse_ref_authors(source)

        # journal / imprint
        journal = page_from = page_to = volume = issue = ""
        year: int | None = None

        if monogr is not None:
            jt = monogr.find(f"{_tag('title')}[@level='j']")
            if jt is None:
                jt = monogr.find(_tag("title"))
            journal = _text(jt)

            imprint = monogr.find(_tag("imprint"))
            if imprint is not None:
                volume  = _text(imprint.find(f"{_tag('biblScope')}[@unit='volume']"))
                issue   = _text(imprint.find(f"{_tag('biblScope')}[@unit='issue']"))
                pg_el   = imprint.find(f"{_tag('biblScope')}[@unit='page']")
                page_from = _attr(pg_el, "from")
                page_to   = _attr(pg_el, "to")
                dt_el   = imprint.find(f"{_tag('date')}[@type='published']")
                if dt_el is not None:
                    year = self._year_from_when(_attr(dt_el, "when"))

        # DOI
        doi_el = bib_el.find(f".//{_tag('idno')}[@type='DOI']")
        doi    = doi_el.text.strip() if doi_el is not None and doi_el.text else None

        # raw reference string (present when includeRawCitations=1)
        note_el = bib_el.find(f".//{_tag('note')}[@type='raw_reference']")
        raw     = _text(note_el)

        if not xml_id and not title:
            return None

        return Reference(
            xml_id    = xml_id,
            title     = title,
            authors   = tuple(authors),
            journal   = journal,
            volume    = volume,
            issue     = issue,
            page_from = page_from,
            page_to   = page_to,
            year      = year,
            doi       = doi,
            raw       = raw,
            coords    = coords,
        )

    def _parse_ref_authors(self, source: etree._Element) -> list[Author]:
        authors: list[Author] = []
        for auth_el in source.findall(_tag("author")):
            pn = auth_el.find(_tag("persName"))
            if pn is None:
                continue
            first   = _text(pn.find(f"{_tag('forename')}[@type='first']"))
            mid_els = pn.findall(f"{_tag('forename')}[@type='middle']")
            middle  = " ".join(_text(m) for m in mid_els)
            last    = _text(pn.find(_tag("surname")))
            if not last:
                continue
            authors.append(Author(
                first=first, middle=middle, last=last, affiliation_keys=(),
            ))
        return authors

    # ── Utilities ─────────────────────────────────────────────────────────────

    def _first(self, root: etree._Element, xpath: str):
        results = root.xpath(xpath, namespaces=_NS)
        return results[0] if results else None

    def _parse_year(self, root: etree._Element, xpath: str) -> int | None:
        results = root.xpath(xpath, namespaces=_NS)
        if not results:
            return None
        return self._year_from_when(str(results[0]))

    @staticmethod
    def _year_from_when(when: str) -> int | None:
        try:
            return int(when[:4])
        except (ValueError, IndexError, TypeError):
            return None
