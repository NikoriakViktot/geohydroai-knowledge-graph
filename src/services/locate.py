"""Where can I read this paper? Corpus files plus legal open-access copies.

    python -m src.services.locate 10.1016/j.isprsjprs.2019.10.017
    python -m src.services.locate https://www.sciencedirect.com/science/article/pii/S0924271619302485 --open
    scripts/pdf <doi|url|arXiv id|paper_id> [...] [--json] [--open | --browser]

The query may be a DOI (any form), a publisher URL with the DOI in its path, a
ScienceDirect PII URL (DOI via Crossref's alternative-id), an arXiv id or URL, or a
corpus paper_id / file stem. Only from the command line, a publisher page without a DOI
in its URL is fetched once to read its citation_doi meta tag (the API never fetches
arbitrary URLs: that would let callers make the server request internal addresses).

Copies come from open-access sources only: OpenAlex locations, Unpaywall, arXiv
(docs/api/endpoints/acquisition.md, legal rule). No shadow libraries.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import quote, unquote, urlparse

from src.services import http
from src.services.identity import normalize_doi

ROOT = Path(__file__).resolve().parents[2]
_DOI_IN_TEXT = re.compile(r"(10\.\d{4,9}/[^\s?#&\"'<>]+)")
_URL_SUFFIXES = re.compile(r"(?:/(?:full|abstract|pdf|epdf|pdfdirect|meta|fulltext|html|summary|references|"
                           r"citedby|figures|supplemental|suppl)(?:/.*)?|\.pdf|\.html?)$", re.IGNORECASE)
_PII = re.compile(r"/pii/(S?[0-9X]{15,17})", re.IGNORECASE)
_ARXIV = re.compile(r"(?:arxiv\.org/(?:abs|pdf)/|arxiv:\s*|^)(\d{4}\.\d{4,5})(v\d+)?(?:\.pdf)?$", re.IGNORECASE)
_META_DOI = re.compile(r"""<meta[^>]+(?:name|property)=["'](?:citation_doi|dc\.identifier|prism\.doi|DC\.Identifier)["']"""
                       r"""[^>]*content=["'](?:doi:)?\s*([^"']+)["']""", re.IGNORECASE)
_VERSION_RANK = {"publishedVersion": 0, "acceptedVersion": 1, "submittedVersion": 2, None: 3}


# ── identifiers ────────────────────────────────────────────────────────────────

def doi_from_url(url: str) -> str | None:
    path = unquote(urlparse(url).path or "")
    m = _DOI_IN_TEXT.search(path)
    if not m:
        return None
    candidate = _URL_SUFFIXES.sub("", m.group(1).rstrip("/"))
    return normalize_doi(candidate)


def doi_from_pii(pii: str) -> str | None:
    pii = pii.upper()
    try:
        got = http.get_json("crossref", f"pii:{pii}", "https://api.crossref.org/works",
                            params={"filter": f"alternative-id:{pii}", "rows": 2})
    except http.Upstream:
        return None
    items = ((got.body or {}).get("message") or {}).get("items") or []
    return normalize_doi(items[0].get("DOI")) if len(items) == 1 else None


def doi_from_landing_page(url: str) -> str | None:
    """CLI only: read the citation_doi meta tag of a public publisher page."""
    try:
        r = http.client().get(url, headers={"User-Agent": http.user_agent(), "Accept": "text/html"}, timeout=15)
    except Exception:
        return None
    if r.status_code != 200:
        return None
    m = _META_DOI.search(r.text[:400_000])
    if m:
        return normalize_doi(m.group(1))
    m = re.search(r"doi\.org/(10\.\d{4,9}/[^\s\"'<>&]+)", r.text[:400_000])
    return normalize_doi(m.group(1)) if m else None


def parse(query: str, allow_fetch: bool = False) -> tuple[str | None, str | None, str | None, list[str]]:
    """→ (doi, resolved_from, arxiv_id, notes)."""
    q = (query or "").strip()
    notes: list[str] = []
    a = _ARXIV.search(q)
    if a and ("arxiv" in q.lower() or re.fullmatch(r"\d{4}\.\d{4,5}(v\d+)?", q)):
        return f"10.48550/arxiv.{a.group(1)}", "arxiv", a.group(1), notes
    doi = normalize_doi(q)
    if doi:
        return doi, "doi", None, notes
    if q.lower().startswith(("http://", "https://")):
        doi = doi_from_url(q)
        if doi:
            return doi, "url", None, notes
        pii = _PII.search(q)
        if pii:
            doi = doi_from_pii(pii.group(1))
            if doi:
                return doi, "pii", None, notes
            notes.append(f"Crossref knows no single DOI for PII {pii.group(1)}")
        if allow_fetch:
            doi = doi_from_landing_page(q)
            if doi:
                return doi, "landing_page", None, notes
            notes.append("the page has no citation_doi meta tag (or blocks automated readers)")
        else:
            notes.append("no DOI in the URL; give the DOI (the API does not fetch arbitrary pages)")
        return None, None, None, notes
    return None, "paper_id", None, notes


# ── corpus files ───────────────────────────────────────────────────────────────

def windows_path(path: Path) -> str | None:
    distro = os.environ.get("WSL_DISTRO_NAME")
    if not distro:
        return None
    return "\\\\wsl.localhost\\" + distro + str(path).replace("/", "\\")


def corpus_files(paper_ids: list[str]) -> list[dict]:
    from sqlalchemy import select

    from src.db.engine import session_scope
    from src.db.models import PaperFile
    with session_scope() as s:
        rows = s.execute(select(PaperFile.paper_id, PaperFile.kind, PaperFile.path, PaperFile.status)
                         .where(PaperFile.paper_id.in_(paper_ids), PaperFile.kind.in_(["pdf", "tei"]))
                         .order_by(PaperFile.kind, PaperFile.status, PaperFile.path)).all()
    out = []
    for pid, kind, rel, status in rows:
        p = ROOT / rel
        out.append({"paper_id": pid, "kind": kind, "path": str(p), "windows_path": windows_path(p),
                    "exists": p.is_file(), "status": status})
    return out


# ── open-access copies ─────────────────────────────────────────────────────────

def _add(found: dict, url: str | None, kind: str, version, license_, host, source):
    if not url:
        return
    if url in found:
        known = found[url]
        if source not in known["source"].split("+"):
            known["source"] += f"+{source}"
        for k, v in (("version", version), ("license", license_), ("host", host)):
            known[k] = known[k] or v
        return
    found[url] = {"url": url, "kind": kind, "version": version, "license": license_, "host": host, "source": source}


def unpaywall(doi: str) -> dict | None:
    email = os.environ.get("OPEN_ALEX_EMAIL") or os.environ.get("UNPAYWALL_EMAIL")
    if not email:
        return None
    try:
        got = http.get_json("unpaywall", doi, f"https://api.unpaywall.org/v2/{quote(doi, safe='/')}",
                            secret={"email": email})
    except http.Upstream:
        return None
    return got.body if got.status == 200 else None


def open_access(doi: str, openalex: dict | None, arxiv_id: str | None) -> tuple[list[dict], dict]:
    found: dict[str, dict] = {}
    if arxiv_id:
        _add(found, f"https://arxiv.org/pdf/{arxiv_id}", "pdf", "submittedVersion", None, "repository", "arxiv")
    oa = openalex or {}
    for loc in [oa.get("best_oa_location")] + list(oa.get("locations") or []):
        if not loc or not loc.get("is_oa"):
            continue
        host = ((loc.get("source") or {}).get("type") or "").replace("journal", "publisher") or None
        _add(found, loc.get("pdf_url"), "pdf", loc.get("version"), loc.get("license"), host, "openalex")
        _add(found, loc.get("landing_page_url"), "landing", loc.get("version"), loc.get("license"), host, "openalex")
    upw = unpaywall(doi) or {}
    for loc in [upw.get("best_oa_location")] + list(upw.get("oa_locations") or []):
        if not loc:
            continue
        _add(found, loc.get("url_for_pdf"), "pdf", loc.get("version"), loc.get("license"), loc.get("host_type"),
             "unpaywall")
        _add(found, loc.get("url_for_landing_page"), "landing", loc.get("version"), loc.get("license"),
             loc.get("host_type"), "unpaywall")
    status = {"is_oa": upw.get("is_oa", (oa.get("open_access") or {}).get("is_oa")),
              "oa_status": upw.get("oa_status") or (oa.get("open_access") or {}).get("oa_status"),
              "unpaywall": bool(upw)}
    ranked = sorted(found.values(), key=lambda x: (x["kind"] != "pdf", _VERSION_RANK.get(x["version"], 3),
                                                   x["host"] != "publisher"))
    return ranked, status


# ── one query ──────────────────────────────────────────────────────────────────

def locate(query: str, allow_fetch: bool = False) -> dict:
    from src.services import doi as doi_service
    from src.services import identity_store
    doi, how, arxiv_id, notes = parse(query, allow_fetch)
    found = None
    if how == "paper_id":
        found = identity_store.resolve(paper_id=query.strip()) or identity_store.resolve(file=query.strip())
        if found is None:
            notes.append("not a DOI, URL, arXiv id or corpus paper_id")
        else:
            doi = (found.canonical or found.paper).doi
    elif doi:
        found = identity_store.resolve(doi=doi)
    meta, files, oa_list, oa_status = None, [], [], {"is_oa": None, "oa_status": None, "unpaywall": False}
    if found is not None:
        ids = [p.paper_id for p in (found.canonical, found.paper) if p]
        files = corpus_files(ids)
    if doi:
        reg = doi_service.lookup(doi)
        meta = doi_service.metadata(doi, reg)
        oa_list, oa_status = open_access(doi, reg.openalex, arxiv_id)
        if meta is None and not found:
            notes.append("no registry knows this DOI")
    paper = (found.canonical or found.paper) if found else None
    pdf_here = [f for f in files if f["kind"] == "pdf" and f["exists"]]
    best = next((x["url"] for x in oa_list if x["kind"] == "pdf"), None)
    if not pdf_here and not oa_list and doi:
        notes.append("no open-access copy is known: use your institution's access or ask the authors; "
                     "a copy you are entitled to use can be added with POST /ingest/upload")
    return {"query": query, "doi": doi, "resolved_from": how if (doi or found) else None,
            "title": (meta.title if meta else None) or (paper.title if paper else None),
            "year": (meta.year_print or meta.year_issued if meta else None) or (paper.year if paper else None),
            "venue": (meta.venue if meta else None) or (paper.venue if paper else None),
            "in_corpus": None if paper is None else {"paper_id": paper.paper_id, "doi": paper.doi, "title": paper.title,
                                                     "year": paper.year, "venue": paper.venue,
                                                     "identity_status": paper.identity_status},
            "files": files, "is_oa": oa_status["is_oa"], "oa_status": oa_status["oa_status"],
            "best_pdf_url": best, "open_access": oa_list,
            "doi_url": f"https://doi.org/{doi}" if doi else None, "notes": notes}


# ── command line ───────────────────────────────────────────────────────────────

def _print(r: dict) -> None:
    print(r["query"])
    if r["doi"] and r["doi"] != r["query"]:
        print(f"  DOI      {r['doi']}  ({r['resolved_from']})")
    if r["title"]:
        print(f"  TITLE    {r['title']} ({r['year'] or '?'}; {r['venue'] or '?'})")
    if r["in_corpus"]:
        print(f"  CORPUS   {r['in_corpus']['paper_id']}  [{r['in_corpus']['identity_status']}]")
        for f in r["files"]:
            if f["exists"]:
                print(f"    {f['kind'].upper():4} {f['windows_path'] or f['path']}"
                      + ("" if f["status"] == "ok" else f"  ({f['status']})"))
    else:
        print("  CORPUS   not in the corpus")
    if r["open_access"]:
        print(f"  OPEN     {r['oa_status'] or 'yes'}")
        for x in r["open_access"][:6]:
            print(f"    {x['kind'].upper():7} {x['url']}  ({x['version'] or '?'}, {x['host'] or '?'}, {x['source']})")
    elif r["doi"]:
        print(f"  OPEN     no open-access copy known ({r['oa_status'] or 'closed'})")
    if r["doi_url"]:
        print(f"  DOI URL  {r['doi_url']}")
    for n in r["notes"]:
        print(f"  NOTE     {n}")


def _open(r: dict) -> None:
    """Open the local PDF, else the best open copy, in Windows (explorer.exe) or with xdg-open."""
    import shutil
    import subprocess
    local = next((f for f in r["files"] if f["kind"] == "pdf" and f["exists"]), None)
    target = (local["windows_path"] or local["path"]) if local else (r["best_pdf_url"] or
                                                                     (r["open_access"][0]["url"] if r["open_access"] else r["doi_url"]))
    if not target:
        return
    opener = shutil.which("explorer.exe") or shutil.which("wslview") or shutil.which("xdg-open")
    if opener:
        subprocess.run([opener, target], check=False)


def _ghai_env() -> tuple[str, str | None]:
    """GHAI_API_URL and GHAI_API_KEY from the environment, else from ~/.config/ghai/env."""
    url, key = os.environ.get("GHAI_API_URL"), os.environ.get("GHAI_API_KEY")
    conf = Path.home() / ".config" / "ghai" / "env"
    if (not url or not key) and conf.is_file():
        for line in conf.read_text().splitlines():
            m = re.match(r"\s*(?:export\s+)?(GHAI_API_URL|GHAI_API_KEY)=(.*)", line)
            if m and m.group(1) == "GHAI_API_URL":
                url = url or m.group(2).strip().strip("'\"")
            elif m:
                key = key or m.group(2).strip().strip("'\"")
    return (url or "http://127.0.0.1:8090/v1").rstrip("/"), key


def _open_in_browser(query: str) -> bool:
    """Ask the running API for a signed link to the local PDF and open it in the default browser."""
    import shutil
    import subprocess

    import httpx
    url, key = _ghai_env()
    if not key:
        print("  BROWSER  no API key (GHAI_API_KEY or ~/.config/ghai/env)")
        return False
    try:
        r = httpx.get(f"{url}/locate", params={"q": query}, headers={"X-API-Key": key}, timeout=60)
    except httpx.HTTPError:
        print(f"  BROWSER  the API at {url} is not running (scripts/ghai_api.sh)")
        return False
    if r.status_code != 200:
        print(f"  BROWSER  {r.status_code}: {r.json().get('detail', '') if r.headers.get('content-type', '').startswith('application') else ''}")
        return False
    link = next((f.get("open_url") for f in r.json().get("files", []) if f.get("open_url")), None)
    if not link:
        print("  BROWSER  no local PDF to open")
        return False
    opener = shutil.which("explorer.exe") or shutil.which("wslview") or shutil.which("xdg-open")
    if not opener:
        print(f"  BROWSER  open this link: {link}")
        return True
    subprocess.run([opener, link], check=False)
    print("  BROWSER  opened the local PDF in the default browser")
    return True


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Find a paper's PDF: corpus files and legal open-access copies.")
    ap.add_argument("queries", nargs="+", help="DOI, doi.org or publisher URL, arXiv id, or corpus paper_id")
    ap.add_argument("--json", action="store_true", help="print JSON")
    ap.add_argument("--open", action="store_true", help="open the local PDF (default PDF app) or the best open copy")
    ap.add_argument("--browser", action="store_true",
                    help="open the local PDF in the browser through the running API (signed link)")
    ap.add_argument("--no-fetch", action="store_true", help="never fetch publisher pages to find a DOI")
    args = ap.parse_args(argv)
    results = [locate(q, allow_fetch=not args.no_fetch) for q in args.queries]
    if args.json:
        print(json.dumps(results, indent=1, ensure_ascii=False))
    else:
        for r in results:
            _print(r)
    if args.browser:
        for q, r in zip(args.queries, results):
            if not _open_in_browser(q):
                _open(r)
    elif args.open:
        for r in results:
            _open(r)
    return 0 if all(r["doi"] or r["in_corpus"] for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
