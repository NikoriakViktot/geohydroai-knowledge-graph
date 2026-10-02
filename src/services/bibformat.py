"""BibTeX in the house convention, and formatted reference lists.

House key convention (docs/api/endpoints/bibliography.md): `Surname_YYYY`; with two
authors `Wilson_Sader_2002`; organisations by their acronym (`UNOSAT_3616_2023`), which a
registry rarely gives, so organisation keys are flagged for review. The key year is the
registry's print year (online year when there is no print issue). Cyrillic family names
are transliterated for keys only (Ukrainian national standard, KMU 2010); the `author`
field keeps the original spelling.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date

from src.contracts.api import DoiMetadata

# ── transliteration (KMU 2010; Russian-only letters by the common BGN-like forms) ──

_UK = {"а": "a", "б": "b", "в": "v", "г": "h", "ґ": "g", "д": "d", "е": "e", "є": "ie", "ж": "zh", "з": "z",
       "и": "y", "і": "i", "ї": "i", "й": "i", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p",
       "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh",
       "щ": "shch", "ь": "", "ю": "iu", "я": "ia", "ы": "y", "э": "e", "ё": "e", "ъ": "", "'": "", "’": ""}
_UK_INITIAL = {"є": "ye", "ї": "yi", "й": "y", "ю": "yu", "я": "ya"}


def transliterate(word: str) -> str:
    out = []
    low = word.lower().replace("зг", "zgh")
    for i, ch in enumerate(low):
        initial = i == 0 or not low[i - 1].isalpha()
        out.append(_UK_INITIAL.get(ch) if initial and ch in _UK_INITIAL else _UK.get(ch, ch))
    text = "".join(out)
    return text[:1].upper() + text[1:] if word[:1].isupper() else text


def key_part(name: str) -> str:
    """An ASCII key fragment: 'Höhle' → 'Hohle', 'Нікорак' → 'Nikorak', "D'Odorico" → 'DOdorico'."""
    text = transliterate(name) if re.search(r"[Ѐ-ӿ]", name) else name
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^A-Za-z]", "", text)
    return text[:1].upper() + text[1:]


# ── keys ───────────────────────────────────────────────────────────────────────

def key_year(meta: DoiMetadata) -> int | None:
    return meta.year_print or meta.year_issued or meta.year_online


def house_key(meta: DoiMetadata) -> tuple[str, list[str]]:
    """(key, notes) for a registry record."""
    notes = []
    authors = meta.authors
    year = key_year(meta)
    if not authors:
        stem = "Anon"
        notes.append("no authors in the registry: choose the key by hand")
    elif len(authors) == 1 and not authors[0].given and len(authors[0].family.split()) > 1:
        org = authors[0].family
        stem = "".join(w[0] for w in org.split() if w[:1].isupper()) or key_part(org)
        notes.append(f"organisation author '{org}': check the house acronym for the key")
    elif len(authors) == 2:
        stem = f"{key_part(authors[0].family)}_{key_part(authors[1].family)}"
    else:
        stem = key_part(authors[0].family)
    if year is None:
        notes.append("no year in the registry")
    if (meta.type or "").lower() == "dataset":
        notes.append("dataset: the house convention may key it by product and version (e.g. ATL13_v6)")
    return f"{stem}_{year if year else 'nd'}", notes


def disambiguate(keys: list[str], taken: dict[str, str | None], dois: list[str]) -> list[tuple[str, bool]]:
    """Suffix a, b, … where a key is already used for another DOI (in `taken`, key → DOI) or twice in the
    batch. Returns (key, collision) per input."""
    out = []
    used = dict(taken)
    for key, doi in zip(keys, dois):
        if key not in used or used[key] == doi:
            used[key] = doi
            out.append((key, False))
            continue
        for suffix in "abcdefghijklmnopqrstuvwxyz":
            candidate = f"{key}{suffix}"
            if candidate not in used or used[candidate] == doi:
                used[candidate] = doi
                out.append((candidate, True))
                break
    return out


# ── BibTeX ─────────────────────────────────────────────────────────────────────

_TYPES = {"journal-article": "article", "article": "article", "proceedings-article": "inproceedings",
          "book-chapter": "incollection", "book": "book", "monograph": "book", "edited-book": "book",
          "report": "techreport", "dissertation": "phdthesis", "posted-content": "misc", "preprint": "misc",
          "dataset": "misc", "Dataset": "misc"}
_ESCAPE = str.maketrans({"&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#", "_": r"\_"})


def _value(text: str) -> str:
    return " ".join(str(text).split()).translate(_ESCAPE)


def bibtex(meta: DoiMetadata, key: str, verified_on: date | None = None) -> str:
    kind = _TYPES.get(meta.type or "", "misc")
    fields: list[tuple[str, str]] = []
    if meta.authors:
        fields.append(("author", " and ".join(
            f"{{{_value(a.family)}}}" if not a.given and len(a.family.split()) > 1
            else (f"{_value(a.family)}, {_value(a.given)}" if a.given else _value(a.family)) for a in meta.authors)))
    if meta.title:
        fields.append(("title", f"{{{_value(meta.title)}}}"))
    venue_field = {"article": "journal", "inproceedings": "booktitle", "incollection": "booktitle"}.get(kind)
    if meta.venue and venue_field:
        fields.append((venue_field, _value(meta.venue)))
    elif meta.venue or meta.publisher:
        fields.append(("publisher", _value(meta.publisher or meta.venue)))
    year = key_year(meta)
    if year:
        fields.append(("year", str(year)))
    for name, value in (("volume", meta.volume), ("number", meta.issue)):
        if value:
            fields.append((name, _value(value)))
    pages = meta.pages if meta.pages and not re.fullmatch(r"(\w+)-\1", meta.pages) else None
    if pages:
        fields.append(("pages", pages.replace("-", "--")))
    elif meta.article_number:
        fields.append(("eid", _value(meta.article_number)))
    fields.append(("doi", meta.doi))
    if kind == "misc" and meta.url:
        fields.append(("url", meta.url))
    note = []
    if verified_on:
        names = {"crossref": "Crossref", "openalex": "OpenAlex", "datacite": "DataCite"}
        sources = sorted({names.get(s, s) for s in meta.sources.values()})
        note.append(f"{'/'.join(sources) or 'Registry'}-verified {verified_on.isoformat()}")
    if meta.year_online and meta.year_print and meta.year_online != meta.year_print:
        note.append(f"online {meta.date_online}, print {meta.date_print}")
    if note:
        fields.append(("note", "; ".join(note)))
    width = max(len(n) for n, _ in fields)
    body = ",\n".join(f"  {n.ljust(width)} = {{{v}}}" for n, v in fields)
    return f"@{kind}{{{key},\n{body}\n}}"


# ── formatted reference lists ──────────────────────────────────────────────────

def _initials(given: str) -> str:
    parts = re.split(r"[\s.]+", (given or "").strip())
    out = []
    for p in parts:
        if not p:
            continue
        if "-" in p:
            out.append("-".join(f"{x[0]}." for x in p.split("-") if x))
        else:
            out.append(f"{p[0]}.")
    return " ".join(out)


def _authors(authors: list[tuple[str, str, bool]], style: str) -> str:
    names = []
    for family, given, corporate in authors:
        if corporate or not given:
            names.append(family)
        elif style == "elsevier-harvard":
            names.append(f"{family}, {_initials(given).replace(' ', '')}")
        else:
            names.append(f"{family}, {_initials(given)}")
    if not names:
        return ""
    if style in ("apa", "agu"):
        if len(names) > 20:
            names = names[:19] + ["…", names[-1]]
            return ", ".join(names)
        return names[0] if len(names) == 1 else ", ".join(names[:-1]) + ", & " + names[-1]
    if style == "copernicus":
        return names[0] if len(names) == 1 else ", ".join(names[:-1]) + ", and " + names[-1]
    return ", ".join(names)


_UNLATEX = [(r"\&", "&"), (r"\%", "%"), (r"\$", "$"), (r"\#", "#"), (r"\_", "_"), ("--", "–"), ("~", " ")]


def _plain(text: str | None) -> str:
    from src.services.bibtex import strip_braces
    out = strip_braces(text or "")
    for latex, plain in _UNLATEX:
        out = out.replace(latex, plain)
    return " ".join(out.split())


def render_entry(fields: dict, style: str) -> str:
    """One BibTeX entry's fields → a formatted reference."""
    from src.services.bibtex import parse_authors
    strip_braces = _plain
    authors, truncated = parse_authors(fields.get("author") or fields.get("editor"))
    who = _authors(authors, style) + (" et al." if truncated else "")
    year = strip_braces(fields.get("year") or "n.d.")
    title = strip_braces(fields.get("title") or "").rstrip(".")
    venue = strip_braces(fields.get("journal") or fields.get("booktitle") or fields.get("publisher")
                         or fields.get("howpublished") or "")
    volume, number = strip_braces(fields.get("volume") or ""), strip_braces(fields.get("number") or "")
    pages = strip_braces(fields.get("pages") or fields.get("eid") or "").replace("--", "–")
    doi = strip_braces(fields.get("doi") or "")
    link = f"https://doi.org/{doi}" if doi else strip_braces(fields.get("url") or "")
    if style in ("apa", "agu"):
        src = venue + (f", {volume}" if volume else "") + (f"({number})" if number else "") + (f", {pages}" if pages else "")
        who_ = who if who.endswith(".") or not who else who + "."      # group authors: "Team. (2023)."
        return " ".join(x for x in (f"{who_} ({year}).", f"{title}.", f"{src}." if src else "", link) if x).strip()
    if style == "copernicus":
        parts = [f"{who}: {title}", venue, volume, pages, link, f"{year}."]
        return ", ".join(p for p in parts if p)
    # elsevier-harvard
    src = venue + (f" {volume}" if volume else "") + (f", {pages}" if pages else "")
    return " ".join(x for x in (f"{who}, {year}.", f"{title}.", f"{src}." if src else "", f"{link}." if link else "") if x)


def sort_key(fields: dict) -> tuple:
    from src.services.bibtex import fold, parse_authors, strip_braces
    authors, _ = parse_authors(fields.get("author") or fields.get("editor"))
    return (fold(authors[0][0]) if authors else "", strip_braces(fields.get("year") or ""),
            fold(strip_braces(fields.get("title") or "")))
