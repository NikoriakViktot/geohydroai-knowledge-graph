"""Author–year citations in a manuscript, mapped to the keys of its BibTeX file.

Recognised forms: "(Surname et al. YYYY)", "(Surname et al., YYYY)", "(A and B YYYY)",
"(A & B, YYYY)", "(A 2019; B et al. 2020a)", "Surname et al. (YYYY)", "A and B (YYYY)",
"Surname (2019, 2020)" and suffixed years. Prefixes such as "e.g." and locators such as
"p. 12" are ignored. Quoted words in the sentence ("…" or “…”) come back as `quoted`, ready
for POST /quotes/verify. Markdown headings give the section; fenced code is skipped;
table placeholders such as {{T3}} are left alone.

A citation resolves when exactly one entry has the same year and first-author family name
(accents and case ignored) and the right author count: one author, two (A and B), or
three or more for "et al." (two are accepted when no three-author entry exists).
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

from src.services.bibtex import fold, parse_authors, parse_bib, strip_braces
from src.services.identity import normalize_doi

_PARTICLE = r"(?:(?:van|von|de|der|den|da|di|du|la|le|del|dos)\s+)*"
_NAME = _PARTICLE + r"[A-Z\u00C0-\u024F\u0400-\u04FF][\w'’\-]*[\w]"
_YEAR = r"(?:19|20)\d{2}[a-z]?"
_YEARS = rf"{_YEAR}(?:\s*,\s*(?:{_YEAR}|[a-z]))*"
_WHO = rf"(?P<a>{_NAME})(?:(?P<etal>\s+et\s+al\.?)|\s+(?:and|&)\s+(?P<b>{_NAME}))?"
NARRATIVE = re.compile(rf"{_WHO}\s*\((?P<years>{_YEARS})(?:\s*,\s*[^()]*)?\)")
PAREN = re.compile(r"\((?P<inner>[^()]*?\b(?:19|20)\d{2}[a-z]?\b[^()]*)\)")
PART = re.compile(rf"^(?:e\.g\.,?\s*|i\.e\.,?\s*|see\s+(?:also\s+)?|cf\.\s*|also\s+)?{_WHO},?\s+(?P<years>{_YEARS})\b")
QUOTED = re.compile(r"[“\"]([^”\"]{3,400})[”\"]")
HEADING = re.compile(r"^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$")
_ABBREV = ("et al.", "e.g.", "i.e.", "Fig.", "Figs.", "Eq.", "Eqs.", "cf.", "vs.", "approx.", "ca.", "No.",
           "Tab.", "Sect.", "Ref.", "Refs.", "St.", "Dr.", "Mt.")
_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9“\"(\[*_])")


@dataclass(frozen=True)
class Entry:
    key: str
    year: int | None
    families: tuple[str, ...]
    truncated: bool
    doi: str | None


def entries_of(bibtex: str) -> list[Entry]:
    out = []
    for e in parse_bib(bibtex):
        f = e["fields"]
        authors, truncated = parse_authors(f.get("author") or f.get("editor"))
        m = re.match(r"\s*(\d{4})", strip_braces(f.get("year") or ""))
        out.append(Entry(e["key"], int(m.group(1)) if m else None, tuple(fold(a[0]) for a in authors), truncated,
                         normalize_doi(f.get("doi")) if f.get("doi") else None))
    return out


def sentences(text: str) -> list[str]:
    """Split a paragraph into sentences without breaking after abbreviations or initials."""
    protected = text
    for i, abbr in enumerate(_ABBREV):
        protected = protected.replace(abbr, abbr.replace(".", f"\x00{i}\x00"))
    protected = re.sub(r"\b([A-Z])\.(?=\s+[A-Z])", "\\1\x01", protected)          # initials "J. Smith"
    parts = _SPLIT.split(protected)
    restore = []
    for p in parts:
        for i, abbr in enumerate(_ABBREV):
            p = p.replace(abbr.replace(".", f"\x00{i}\x00"), abbr)
        restore.append(p.replace("\x01", ".").strip())
    return [p for p in restore if p]


def _resolve(index: list[Entry], a: str, b: str | None, et_al: bool, year: str) -> tuple[str, list[str]]:
    y = int(year[:4])
    suffix = year[4:]
    cands = [e for e in index if e.year == y and e.families and e.families[0] == fold(a)]
    if b:
        cands = [e for e in cands if len(e.families) >= 2 and e.families[1] == fold(b)
                 and (len(e.families) == 2 or e.truncated)]
    elif et_al:
        three = [e for e in cands if len(e.families) >= 3 or e.truncated]
        cands = three or [e for e in cands if len(e.families) == 2]
    else:
        single = [e for e in cands if len(e.families) == 1]
        cands = single or cands
    if len(cands) > 1 and suffix:
        by_suffix = [e for e in cands if e.key.lower().endswith(suffix)]
        cands = by_suffix or cands
    keys = sorted(e.key for e in cands)
    if not keys:
        # house key convention (Surname_YYYY, A_B_YYYY): corporate authors are cited by acronym,
        # e.g. "(CEOBS 2023)" for author {Conflict and Environment Observatory}, key CEOBS_2023
        stem = "_".join(x for x in (a, b) if x)
        keys = sorted(e.key for e in index if fold(e.key) in {fold(f"{stem}_{year}"), fold(f"{a}_{year}")})
    return ("resolved" if len(keys) == 1 else "ambiguous" if keys else "missing"), keys


def _expand_years(years: str) -> list[str]:
    out, last = [], None
    for part in [p.strip() for p in years.split(",") if p.strip()]:
        if re.fullmatch(_YEAR, part):
            out.append(part)
            last = part[:4]
        elif re.fullmatch(r"[a-z]", part) and last:
            out.append(last + part)
    return out


def find(manuscript: str, bibtex: str) -> dict:
    index = entries_of(bibtex)
    doi_of = {e.key: e.doi for e in index}
    occurrences: list[dict] = []
    section, in_code = None, False
    for line_no, line in enumerate(manuscript.splitlines(), start=1):
        if line.strip().startswith("```"):
            in_code = not in_code
            continue
        if in_code or not line.strip():
            continue
        h = HEADING.match(line)
        if h:
            section = re.sub(r"[*_`]", "", h.group(2)).strip()
            continue
        for sentence in sentences(line):
            quoted = [q.strip() for q in QUOTED.findall(sentence)]
            spans: list[tuple[int, int]] = []
            found: list[tuple[int, str, str, str | None, bool, str]] = []   # (pos, cite_text, a, b, et_al, years)
            for m in NARRATIVE.finditer(sentence):
                spans.append(m.span())
                found.append((m.start(), m.group(0), m.group("a"), m.group("b"), bool(m.group("etal")), m.group("years")))
            for m in PAREN.finditer(sentence):
                if any(s <= m.start() < e for s, e in spans):
                    continue
                for part in m.group("inner").split(";"):
                    pm = PART.match(part.strip())
                    if pm:
                        found.append((m.start(), f"({part.strip()})", pm.group("a"), pm.group("b"),
                                      bool(pm.group("etal")), pm.group("years")))
            for pos, text, a, b, et_al, years in sorted(found, key=lambda x: x[0]):
                who = f"{a} et al." if et_al else (f"{a} and {b}" if b else a)
                for year in _expand_years(years):
                    status, keys = _resolve(index, a, b, et_al, year)
                    key = keys[0] if status == "resolved" else None
                    occurrences.append({"cite_text": text, "authors": who, "year": year, "status": status,
                                        "cite_key": key, "candidates": keys if status == "ambiguous" else [],
                                        "doi": doi_of.get(key) if key else None, "section": section,
                                        "sentence": sentence, "line": line_no, "quoted": quoted})
    cited = {o["cite_key"] for o in occurrences if o["cite_key"]} | {k for o in occurrences for k in o["candidates"]}
    missing = [{"cite_text": o["cite_text"], "section": o["section"], "sentence": o["sentence"], "line": o["line"]}
               for o in occurrences if o["status"] == "missing"]
    return {"occurrences": occurrences, "missing_keys": missing,
            "uncited_entries": sorted(e.key for e in index if e.key not in cited),
            "summary": dict(Counter(o["status"] for o in occurrences)) | {"entries": len(index),
                                                                         "cited_entries": len(cited)}}
