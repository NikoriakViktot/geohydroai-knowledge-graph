"""Audit of a BibTeX file (docs/api/endpoints/bibliography.md, POST /bib/audit).

Per entry:
  * the DOI checked field by field against the registries (as POST /doi/verify);
  * a missing DOI the registry has: Crossref bibliographic search on title, first author
    and year, accepted only at title similarity ≥ 0.95 with the same year;
  * duplicates: two keys with one DOI, or one key twice;
  * key/year disagreement (Smith_2019 with year = 2020);
  * mixed field-name case in the file (DOI= next to doi=);
  * free-text status notes (VERIFY, TODO, FIXME, "to check").
Status: ok | fix | unresolved; entries with a problem and a usable DOI get a suggested
entry in the house format, under their own key.
"""

from __future__ import annotations

import difflib
import hashlib
import re
from collections import Counter, defaultdict
from datetime import date

from src.contracts.api import BibInput
from src.etl.identity import norm_title
from src.services import bibformat, http
from src.services import doi as doi_service
from src.services.bibtex import parse_authors, parse_bib, strip_braces
from src.services.identity import normalize_doi

MAX_ENTRIES = 300
TITLE_MIN = 0.95
_STATUS_NOTE = re.compile(r"\b(VERIFY|TODO|FIXME|XXX)\b|\bto\s+check\b", re.IGNORECASE)
_FIELD_NAME = re.compile(r"(?m)^\s*([A-Za-z][\w-]*)\s*=")
_KEY_YEAR = re.compile(r"(?:^|_)(\d{4})[a-z]?(?=_|$)")


def key_year(key: str) -> str | None:
    """The last _YYYY part of a key that is a plausible year (UNOSAT_3616_2023 → 2023)."""
    years = [y for y in _KEY_YEAR.findall(key) if 1800 <= int(y) <= 2099]
    return years[-1] if years else None


def _raw_entries(text: str) -> dict[str, str]:
    """key → the entry's raw text (the last one when a key repeats)."""
    out = {}
    starts = [(m.start(), m.group(2)) for m in re.finditer(r"@(\w+)\s*\{\s*([^,\s]+)\s*,", text)]
    for i, (pos, key) in enumerate(starts):
        end = starts[i + 1][0] if i + 1 < len(starts) else len(text)
        out[key] = text[pos:end]
    return out


def find_doi(title: str, family: str | None, year: int | None) -> str | None:
    if not title or len(norm_title(title)) < 15:
        return None
    query = " ".join(x for x in (title, family or "", str(year or "")) if x)
    key = "q:" + hashlib.sha256(query.encode()).hexdigest()[:24]
    try:
        got = http.get_json("crossref", key, "https://api.crossref.org/works",
                            params={"query.bibliographic": query, "rows": 3})
    except http.Upstream:
        return None
    for item in ((got.body or {}).get("message") or {}).get("items") or []:
        cand = (item.get("title") or [""])[0]
        score = difflib.SequenceMatcher(None, norm_title(title), norm_title(cand)).ratio()
        years = {(item.get(k) or {}).get("date-parts", [[None]])[0][0]
                 for k in ("published-print", "published-online", "issued")}
        if score >= TITLE_MIN and (year is None or year in years):
            return normalize_doi(item.get("DOI"))
    return None


def audit(text: str, project_id: str | None = None, search_missing: bool = True) -> dict:
    entries = parse_bib(text)
    if len(entries) > MAX_ENTRIES:
        raise ValueError(f"{len(entries)} entries; at most {MAX_ENTRIES} per request until asynchronous jobs exist")
    raw = _raw_entries(text)
    names = Counter(m.group(1) for m in _FIELD_NAME.finditer(text))
    mixed = sorted({n.lower() for n in names} - {n for n in names if n == n.lower() and names[n] == sum(
        c for k, c in names.items() if k.lower() == n)})
    key_count = Counter(e["key"] for e in entries)
    dois = defaultdict(list)
    for e in entries:
        d = normalize_doi(e["fields"].get("doi")) if e["fields"].get("doi") else None
        if d:
            dois[d].append(e["key"])

    inputs = [BibInput(key=e["key"], doi=e["fields"].get("doi"), title=e["fields"].get("title"),
                       authors=e["fields"].get("author"), year=e["fields"].get("year"),
                       journal=e["fields"].get("journal") or e["fields"].get("booktitle"),
                       volume=e["fields"].get("volume"), issue=e["fields"].get("number"),
                       pages=e["fields"].get("pages")) for e in entries]
    with_doi = [i for i in inputs if i.doi]
    verified = {r.input_key: r for r in doi_service.verify_many(with_doi, project_id)} if with_doi else {}

    out = []
    today = date.today()
    for e, inp in zip(entries, inputs):
        f = e["fields"]
        problems: list[str] = []
        key, doi = e["key"], normalize_doi(f.get("doi")) if f.get("doi") else None
        if key_count[key] > 1:
            problems.append(f"key {key} occurs {key_count[key]} times")
        if doi and len(dois[doi]) > 1:
            problems.append(f"DOI shared with {', '.join(k for k in dois[doi] if k != key)}")
        warnings: list[str] = []
        y = re.match(r"\s*(\d{4})", strip_braces(f.get("year") or ""))
        ky = key_year(key)
        if y and ky and ky != y.group(1):
            problems.append(f"key year {ky} ≠ year field {y.group(1)}")
        for name in mixed:
            if re.search(rf"(?m)^\s*{name}\s*=", raw.get(key, ""), re.IGNORECASE) and \
                    not re.search(rf"(?m)^\s*{name}\s*=", raw.get(key, "")):
                warnings.append(f"field name '{name}' is not lower case here (BibTeX ignores case; keep one style)")
        for field, value in f.items():
            if field in ("note", "annote", "comment", "addendum") and _STATUS_NOTE.search(value or ""):
                problems.append(f"status note in {field}: {strip_braces(value)[:120]}")
        result = verified.get(key)
        verdict, suggested_doi = None, None
        if f.get("doi") and doi is None:
            problems.append(f"doi field {f['doi']!r} is not a DOI")
            verdict = "NOT_A_DOI"
        elif result is not None:
            verdict = result.verdict
            if verdict == "MISMATCH":
                problems += [f"{d.field}: '{d.given}' vs registry '{d.registry}'" for d in result.diffs
                             if d.severity == "major"]
            elif verdict == "UNRESOLVED":
                problems += result.notes
        elif search_missing and e["type"] in ("article", "inproceedings", "incollection", "book"):
            authors, _ = parse_authors(f.get("author"))
            suggested_doi = find_doi(strip_braces(f.get("title") or ""), authors[0][0] if authors else None,
                                     int(y.group(1)) if y else None)
            if suggested_doi:
                problems.append(f"no doi field; the registry has {suggested_doi}")
        target = doi if verdict not in ("UNRESOLVED", "NOT_A_DOI") else None
        target = target or suggested_doi
        suggested = None
        if problems and target:
            meta = doi_service.metadata(target, doi_service.lookup(target))
            if meta is not None:
                suggested = bibformat.bibtex(meta, key, today)
        if verdict is None and not doi and not suggested_doi and e["type"] in ("article", "inproceedings"):
            problems.append("no DOI, and none found in the registry")
            status = "unresolved"
        elif verdict == "UNRESOLVED":
            status = "unresolved"
        else:
            status = "fix" if problems else "ok"
        out.append({"key": key, "type": e["type"], "status": status, "doi": doi, "verdict": verdict,
                    "suggested_doi": suggested_doi, "problems": problems, "warnings": warnings,
                    "suggested_bibtex": suggested})
    summary = dict(Counter(x["status"] for x in out)) | {"entries": len(out), "with_doi": sum(bool(x["doi"]) for x in out),
                                                         "mixed_field_case": len(mixed)}
    return {"entries": out, "summary": summary, "mixed_field_names": mixed}
