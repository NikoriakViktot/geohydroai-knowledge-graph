"""
tei_io.py — єдиний XML-фасад поза parser.py.

Архітектурний інваріант: lxml можна імпортувати лише в
src/document/{parser.py, tei_io.py}. Валідатори та region-пайплайни
отримують розпарсений Element звідси — XML-парсинг (attack surface)
ізольований у document-шарі (enforced у tests/test_import_invariants.py).
"""
from __future__ import annotations

from pathlib import Path

from lxml import etree

# Публічний alias, щоб споживачі могли типізувати без імпорту lxml
Element = etree._Element

TEI_NS = {"tei": "http://www.tei-c.org/ns/1.0"}


class TEIXMLError(Exception):
    """XML синтаксично невалідний або файл не читається."""


def parse_string(xml_text: str) -> Element:
    """Розпарсити XML-рядок (строго). Raises TEIXMLError на синтаксис."""
    try:
        return etree.fromstring(xml_text.encode("utf-8"))
    except etree.XMLSyntaxError as exc:
        raise TEIXMLError(str(exc)) from exc


def parse_file(path: Path, recover: bool = True) -> Element:
    """Розпарсити XML-файл; recover=True — толерантно до зламаного TEI."""
    try:
        parser = etree.XMLParser(recover=recover)
        return etree.parse(str(path), parser).getroot()
    except (etree.XMLSyntaxError, OSError) as exc:
        raise TEIXMLError(f"{path}: {exc}") from exc


def localname(elem: Element) -> str:
    """Ім'я тега без namespace (etree.QName(...).localname)."""
    return etree.QName(elem).localname
