"""DOI metadata from the registries, and field-by-field verification of bibliography entries.

Crossref is the primary source of bibliographic fields; DataCite answers for DOIs Crossref
does not know (datasets); OpenAlex fills gaps and supplies the open-access fields. Every
field of the merged record names its source.

Verification rules (docs/api/endpoints/bibliography.md):
  title    similarity ≥ 0.90 after normalisation (subtitle optional), else MISMATCH;
  year     equal to the online, print or issued year ('2024a' → 2024), else MISMATCH;
           online ≠ print is reported as a note;
  authors  first family name must agree (MISMATCH otherwise); other names, counts and
           initials are minor differences;
  journal  abbreviations are fine; a different name is reported, not failed;
  volume, issue, pages
           differences are minor; a registry page range like '50-50' is reported as an
           artefact and not trusted.
"""

from __future__ import annotations

import difflib
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from urllib.parse import quote

from src.contracts.api import BibInput, DoiAuthor, DoiMetadata, DoiVerifyResult, FieldDiff, PaperRef
from src.etl.identity import norm_title
from src.services import http
from src.services.bibtex import fold, parse_authors, parse_bib, strip_braces
from src.services.identity import normalize_doi

TITLE_MIN = 0.90
TITLE_NOTE = 0.98
VENUE_MIN = 0.85
MAX_WORKERS = 4


@dataclass
class Registries:
    crossref: dict | None = None        # Crossref "message"
    openalex: dict | None = None        # OpenAlex work
    datacite: dict | None = None        # DataCite "attributes"
    fetched: dict[str, str] = field(default_factory=dict)

    @property
    def any(self) -> bool:
        return bool(self.crossref or self.openalex or self.datacite)


def _openalex_secret() -> dict:
    from src.config import settings
    return {"api_key": settings.OPEN_ALEX_API} if settings.OPEN_ALEX_API else {}


def lookup(doi: str, refresh: bool = False) -> Registries:
    """Ask Crossref and OpenAlex (and DataCite when Crossref does not know the DOI)."""
    reg = Registries()

    def one(service: str, url: str, unwrap, secret: dict | None = None):
        try:
            got = http.get_json(service, doi, url, secret=secret, refresh=refresh)
        except http.Upstream:
            reg.fetched[service] = "unavailable"
            return None
        if got.status != 200 or not got.body:
            reg.fetched[service] = "not_found"
            return None
        reg.fetched[service] = got.source
        return unwrap(got.body)

    q = quote(doi, safe="/")
    reg.crossref = one("crossref", f"https://api.crossref.org/works/{q}", lambda b: b.get("message"))
    reg.openalex = one("openalex", f"https://api.openalex.org/works/doi:{q}", lambda b: b, _openalex_secret())
    if reg.crossref is None:
        reg.datacite = one("datacite", f"https://api.datacite.org/dois/{q}",
                           lambda b: (b.get("data") or {}).get("attributes"))
    return reg


# ── merging ────────────────────────────────────────────────────────────────────

def _clean(text: str | None) -> str | None:
    """Registry text without JATS/HTML markup: '<scp>block</scp>\n<scp>CV</scp>' → 'block CV'."""
    import html
    if not text:
        return text
    out = " ".join(html.unescape(re.sub(r"<[^>]+>", "", text)).split())
    return re.sub(r"\s+([:;,.?])", r"\1", out) or None


def _first(v):
    return v[0] if isinstance(v, list) and v else (v if isinstance(v, str) and v else None)


def _date(obj) -> tuple[int | None, str | None]:
    parts = ((obj or {}).get("date-parts") or [[None]])[0]
    if not parts or parts[0] is None:
        return None, None
    return int(parts[0]), "-".join(str(p) if i == 0 else f"{int(p):02d}" for i, p in enumerate(parts))


def _orcid(v: str | None) -> str | None:
    m = re.search(r"(\d{4}-\d{4}-\d{4}-\d{3}[\dX])", v or "")
    return m.group(1) if m else None


def _split_name(display: str) -> tuple[str, str | None]:
    bits = (display or "").split()
    return (bits[-1], " ".join(bits[:-1]) or None) if bits else (display, None)


def metadata(doi: str, reg: Registries) -> DoiMetadata | None:
    if not reg.any:
        return None
    m: dict = {}
    src: dict = {}

    def put(name, value, source):
        if value in (None, "", []) or name in m:
            return
        m[name] = value
        src[name] = source

    cr = reg.crossref
    if cr:
        title, sub = _clean(_first(cr.get("title"))), _clean(_first(cr.get("subtitle")))
        put("title", f"{title}: {sub}" if title and sub else title, "crossref")
        authors = [DoiAuthor(family=a.get("family") or a.get("name") or "?", given=a.get("given"),
                             orcid=_orcid(a.get("ORCID"))) for a in cr.get("author") or []]
        put("authors", authors, "crossref")
        y_on, d_on = _date(cr.get("published-online"))
        y_pr, d_pr = _date(cr.get("published-print"))
        y_is, _ = _date(cr.get("issued"))
        put("year_online", y_on, "crossref")
        put("date_online", d_on, "crossref")
        put("year_print", y_pr, "crossref")
        put("date_print", d_pr, "crossref")
        put("year_issued", y_is, "crossref")
        put("venue", _clean(_first(cr.get("container-title"))), "crossref")
        for name, key in (("volume", "volume"), ("issue", "issue"), ("pages", "page"),
                          ("article_number", "article-number"), ("type", "type"), ("publisher", "publisher"),
                          ("url", "URL")):
            put(name, cr.get(key), "crossref")
        lic = sorted(cr.get("license") or [], key=lambda x: x.get("content-version") != "vor")
        put("licence", lic[0].get("URL") if lic else None, "crossref")
    dc = reg.datacite
    if dc:
        put("title", _clean(_first([t.get("title") for t in dc.get("titles") or [] if t.get("title")])), "datacite")
        put("authors", [DoiAuthor(family=c.get("familyName") or c.get("name") or "?", given=c.get("givenName"))
                        for c in dc.get("creators") or []], "datacite")
        put("year_issued", dc.get("publicationYear"), "datacite")
        put("publisher", dc.get("publisher") if isinstance(dc.get("publisher"), str)
            else (dc.get("publisher") or {}).get("name"), "datacite")
        put("type", (dc.get("types") or {}).get("resourceTypeGeneral"), "datacite")
        put("url", dc.get("url"), "datacite")
        put("licence", _first([r.get("rightsUri") for r in dc.get("rightsList") or [] if r.get("rightsUri")]),
            "datacite")
    oa = reg.openalex
    if oa:
        put("title", _clean(oa.get("title") or oa.get("display_name")), "openalex")
        put("authors", [DoiAuthor(family=_split_name(a["author"].get("display_name") or "")[0],
                                  given=_split_name(a["author"].get("display_name") or "")[1],
                                  orcid=_orcid(a["author"].get("orcid")))
                        for a in oa.get("authorships") or [] if a.get("author")], "openalex")
        put("year_issued", oa.get("publication_year"), "openalex")
        pl = oa.get("primary_location") or {}
        put("venue", (pl.get("source") or {}).get("display_name"), "openalex")
        bib = oa.get("biblio") or {}
        put("volume", bib.get("volume"), "openalex")
        put("issue", bib.get("issue"), "openalex")
        fp, lp = bib.get("first_page"), bib.get("last_page")
        put("pages", f"{fp}-{lp}" if fp and lp and fp != lp else fp, "openalex")
        put("type", oa.get("type"), "openalex")
        put("licence", pl.get("license"), "openalex")
        acc = oa.get("open_access") or {}
        put("is_oa", acc.get("is_oa"), "openalex")
        put("oa_status", acc.get("oa_status"), "openalex")
        put("oa_url", acc.get("oa_url"), "openalex")
    return DoiMetadata(doi=doi, **m, sources=src, fetched=reg.fetched)


# ── verification ───────────────────────────────────────────────────────────────

def _year(value) -> int | None:
    m = re.match(r"\s*(\d{4})", str(value)) if value not in (None, "") else None
    return int(m.group(1)) if m else None


def _pages(value) -> str:
    return re.sub(r"\s*[-–—]+\s*", "-", strip_braces(str(value))).strip()


_VENUE_STOP = {"in", "of", "the", "and", "for", "on", "de", "des", "la", "le", "et", "und", "der", "di"}


def _venue_words(name: str) -> list[str]:
    import unicodedata
    text = unicodedata.normalize("NFKD", strip_braces(name))
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).casefold()
    return [w for w in re.findall(r"[a-z]+", text) if w not in _VENUE_STOP]


def same_venue(given: str, registry: str) -> bool:
    """Equal, contained, close, or an abbreviation: 'Surv. Geophys.' = 'Surveys in Geophysics'."""
    a, b = fold(given), fold(registry)
    if a in b or b in a or _ratio(a, b) >= VENUE_MIN:
        return True
    wa, wb = _venue_words(given), _venue_words(registry)
    return bool(wa) and len(wa) == len(wb) and all(x.startswith(y) or y.startswith(x) for x, y in zip(wa, wb))


def _ratio(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a, b).ratio() if a and b else 0.0


def from_bibtex(entry: BibInput) -> BibInput:
    parsed = parse_bib(entry.bibtex or "")
    if not parsed:
        raise ValueError("not a BibTeX entry")
    f = parsed[0]["fields"]
    return BibInput(key=entry.key or parsed[0]["key"], doi=f.get("doi"), title=strip_braces(f.get("title")) or None,
                    authors=f.get("author"), year=f.get("year"), journal=strip_braces(f.get("journal") or f.get("booktitle")) or None,
                    volume=f.get("volume"), issue=f.get("number"), pages=f.get("pages"))


def cite_key_doi(project_id: str, key: str) -> str | None:
    from sqlalchemy import select

    from src.db.engine import session_scope
    from src.db.models import CiteKey
    with session_scope() as s:
        return s.scalar(select(CiteKey.doi).where(CiteKey.project_id == project_id, CiteKey.key == key))


def compare(e: BibInput, meta: DoiMetadata) -> tuple[list[FieldDiff], list[str]]:
    diffs: list[FieldDiff] = []
    notes: list[str] = []
    src = meta.sources

    def diff(name, given, registry, severity, source_field=None):
        diffs.append(FieldDiff(field=name, given=None if given is None else str(given),
                               registry=None if registry is None else str(registry),
                               source=src.get(source_field or name), severity=severity))

    # title
    if e.title and meta.title:
        given = norm_title(strip_braces(e.title))
        main = meta.title.split(": ")[0]
        score = max(_ratio(given, norm_title(meta.title)), _ratio(given, norm_title(main)))
        if score < TITLE_MIN:
            diff("title", e.title, meta.title, "major")
        elif score < TITLE_NOTE:
            diff("title", e.title, meta.title, "info")
    # year
    given_year = _year(e.year)
    years = {y for y in (meta.year_print, meta.year_online, meta.year_issued) if y}
    if given_year and years:
        if given_year not in years:
            diff("year", e.year, "/".join(str(y) for y in sorted(years)), "major", "year_issued")
        if meta.year_online and meta.year_print and meta.year_online != meta.year_print:
            vol = f" (vol. {meta.volume})" if meta.volume else ""
            notes.append(f"online {meta.date_online}, print {meta.date_print}{vol}: cite the print year with its "
                         "volume unless the journal style says otherwise")
    # authors (corporate names such as "{the ICESat-2 Science Team}" are not compared)
    given_all, truncated = parse_authors(e.authors)
    people = [(fam, gv) for fam, gv, corporate in given_all if not corporate]
    if people and meta.authors:
        reg = [(fold(a.family), a.given) for a in meta.authors]
        if given_all[0][2] is False and fold(people[0][0]) != reg[0][0]:
            diff("authors[0]", people[0][0], meta.authors[0].family, "major", "authors")
        missing = [fam for fam, _ in people if fold(fam) not in {f for f, _ in reg}]
        if missing:
            diff("authors", "; ".join(missing), "not in the registry's author list", "minor")
        if not truncated and len(people) != len(meta.authors) and len(people) == len(given_all):
            diff("authors.count", len(people), len(meta.authors), "minor", "authors")
        initials = []
        for fam, gv in people:
            cands = [rg for rf, rg in reg if rf == fold(fam) and rg and fold(rg)]
            if gv and fold(gv) and cands and not any(fold(gv)[:1] == fold(rg)[:1] for rg in cands):
                initials.append(f"{fam}: {gv} vs {' / '.join(c.strip() for c in cands)}")
        if initials:
            diff("authors.initials", "; ".join(initials), None, "minor", "authors")
    # journal
    if e.journal and meta.venue and not same_venue(e.journal, meta.venue):
        diff("journal", e.journal, meta.venue, "info", "venue")
    # volume, issue
    for name, given, reg in (("volume", e.volume, meta.volume), ("issue", e.issue, meta.issue)):
        g = strip_braces(str(given)) if given not in (None, "") else None
        if g and reg and g != str(reg).strip():
            diff(name, g, reg, "minor")
        elif not g and reg:
            diff(name, None, reg, "info")
    # pages
    reg_pages = _pages(meta.pages) if meta.pages else None
    artefact = bool(reg_pages and re.fullmatch(r"(\w+)-\1", reg_pages))
    if artefact:
        notes.append(f"{src.get('pages', 'registry')} pages '{meta.pages}' look like an article-number artefact; "
                     "take the pages from the journal page")
    elif e.pages and (reg_pages or meta.article_number):
        g = _pages(e.pages)
        if g not in {reg_pages, meta.article_number}:
            diff("pages", e.pages, meta.pages or meta.article_number, "minor", "pages")
    elif not e.pages and (reg_pages or meta.article_number):
        diff("pages", None, meta.pages or meta.article_number, "info", "pages")
    return diffs, notes


def _in_corpus(doi: str) -> PaperRef | None:
    from src.services import identity_store
    found = identity_store.resolve(doi=doi)
    if found is None:
        return None
    p = found.canonical or found.paper
    return PaperRef(paper_id=p.paper_id, doi=p.doi, title=p.title, year=p.year, venue=p.venue,
                    identity_status=p.identity_status)


def verify(entry: BibInput, project_id: str | None = None, refresh: bool = False) -> DoiVerifyResult:
    notes: list[str] = []
    try:
        e = from_bibtex(entry) if entry.bibtex else entry
    except ValueError as exc:
        return DoiVerifyResult(input_key=entry.key, verdict="UNRESOLVED", notes=[str(exc)])
    raw = e.doi
    if not raw and e.key and project_id:
        raw = cite_key_doi(project_id, e.key)
        if raw:
            notes.append(f"DOI taken from the project's cite key {e.key}")
    if not raw:
        return DoiVerifyResult(input_key=e.key, verdict="UNRESOLVED",
                               notes=["no DOI given: nothing to resolve. Works without a DOI (e.g. JMLR) are "
                                      "cited by URL; check them on the publisher's page"])
    doi = normalize_doi(raw)
    if doi is None:
        return DoiVerifyResult(input_key=e.key, verdict="NOT_A_DOI", notes=[f"{raw!r} is not a DOI"])
    reg = lookup(doi, refresh)
    meta = metadata(doi, reg)
    if meta is None:
        if "unavailable" in reg.fetched.values():
            notes.append("a registry did not answer: not checked; retry later")
        else:
            notes.append("no registry knows this DOI (Crossref, DataCite, OpenAlex)")
        return DoiVerifyResult(input_key=e.key, doi=doi, verdict="UNRESOLVED", notes=notes)
    for service, how in reg.fetched.items():
        if how == "unavailable":
            notes.append(f"{service} did not answer; the fields it would supply were not compared")
        elif how == "stale_cache":
            notes.append(f"{service} did not answer; its cached answer was used")
    diffs, more = compare(e, meta)
    notes += more
    if any(d.severity == "major" for d in diffs):
        verdict = "MISMATCH"
    elif diffs or notes:
        verdict = "VERIFIED_WITH_NOTES"
    else:
        verdict = "VERIFIED"
    return DoiVerifyResult(input_key=e.key, doi=doi, verdict=verdict, diffs=diffs, notes=notes, registry=meta,
                           in_corpus=_in_corpus(doi))


def verify_many(entries: list[BibInput], project_id: str | None = None, refresh: bool = False) -> list[DoiVerifyResult]:
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        return list(pool.map(lambda e: verify(e, project_id, refresh), entries))
