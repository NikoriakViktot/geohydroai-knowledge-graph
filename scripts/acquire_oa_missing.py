#!/usr/bin/env python3
"""Most-cited works the corpus references but does not hold → legal open-access PDFs → GROBID.

Open access only (AGENT_RULES R-DATA-3): a PDF is fetched only from a location that OpenAlex or
Unpaywall lists as open access (src/services/locate.py). Works without such a copy go to
``no_oa.csv`` for a person (library access, the authors), never to a shadow library.

    .venv/bin/python3 scripts/acquire_oa_missing.py rank     [--top 600] [--min-citing 3]
    .venv/bin/python3 scripts/acquire_oa_missing.py discover --queries Q.txt --run DIR  # topical instead of rank
    .venv/bin/python3 scripts/acquire_oa_missing.py locate   # identity check + OA locations
    .venv/bin/python3 scripts/acquire_oa_missing.py more     # + Semantic Scholar, OA landing pages, arXiv
    .venv/bin/python3 scripts/acquire_oa_missing.py download # PDFs into data/literature/pdf_oa/
    .venv/bin/python3 scripts/acquire_oa_missing.py grobid   # TEI into grobid_xml/, links in xml_new/
    .venv/bin/python3 scripts/acquire_oa_missing.py all

Run directory: data/acquisition/oa_<date>/ (``--run``). Priority = number of distinct corpus papers
that cite the work, then its global citation count. Each stage resumes: rows already done are skipped.
After ``grobid``: pipeline_runner --xml-dir <run>/xml_new, then the usual post-pipeline steps
(scripts/process_oa_batch.sh).
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import logging
import os
import sys
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

PDF_DIR = ROOT / "data" / "literature" / "pdf_oa"
XML_DIR = ROOT / "data" / "literature" / "grobid_xml"
MAX_PDF = 100 * 1024 * 1024
MIN_PDF = 20 * 1024

log = logging.getLogger("acquire_oa")


def slug(doi: str) -> str:
    """The doi-slug convention of data/literature/pdf_missing."""
    return doi.lower().replace("/", "_").replace(":", "_")


def norm_doi(raw) -> str | None:
    if not isinstance(raw, str):
        return None
    d = raw.strip().lower()
    for p in ("https://doi.org/", "http://doi.org/", "http://dx.doi.org/", "https://dx.doi.org/", "doi:"):
        if d.startswith(p):
            d = d[len(p):]
    return d if d.startswith("10.") else None


def read_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    tmp.replace(path)


# ── rank ───────────────────────────────────────────────────────────────────────

def rank(run: Path, top: int, min_citing: int) -> None:
    import pandas as pd
    refs = pd.read_parquet(ROOT / "data/parquet/references.parquet")
    papers = pd.read_parquet(ROOT / "data/parquet/papers.parquet")
    refs["doi"] = refs["referenced_doi"].map(norm_doi)
    refs = refs.dropna(subset=["doi"])
    known = {d for d in papers["doi"].map(norm_doi) if d}
    g = (refs[~refs["doi"].isin(known)]
         .groupby("doi")
         .agg(citing_papers=("source_paper_id", "nunique"), cited_by_count=("cited_by_count", "max"))
         .reset_index())
    g = g[(g["citing_papers"] >= min_citing) & ~g["doi"].str.contains("(issn)", regex=False)]  # journal DOIs
    g["cited_by_count"] = g["cited_by_count"].fillna(0).astype(int)
    g = g.sort_values(["citing_papers", "cited_by_count"], ascending=False).head(top)
    g.insert(0, "rank", range(1, len(g) + 1))
    run.mkdir(parents=True, exist_ok=True)
    g.to_csv(run / "priority.csv", index=False)
    log.info("priority.csv: %d works cited by ≥ %d corpus papers (of %d missing DOIs referenced)",
             len(g), min_citing, refs.loc[~refs["doi"].isin(known), "doi"].nunique())


# ── discover: topical search in OpenAlex ──────────────────────────────────────

def discover(run: Path, queries: list[str], per_query: int, min_cited: int, top: int) -> None:
    """Works matching topic queries (OpenAlex search), not yet in the corpus, ranked by global citations.
    Writes priority.csv in the same shape as ``rank`` (citing_papers = number of queries that found it)."""
    import requests
    from src.config import settings
    params_key = {"api_key": settings.OPEN_ALEX_API} if settings.OPEN_ALEX_API else {}
    s = requests.Session()
    s.headers.update({"User-Agent": f"GeoHydroAI-OA/1.0 (mailto:{settings.OPEN_ALEX_EMAIL})"})
    found: dict[str, dict] = {}
    for q in queries:
        got, cursor = 0, "*"
        while got < per_query and cursor:
            resp = s.get("https://api.openalex.org/works",
                         params={"search": q, "per-page": 100, "cursor": cursor,
                                 "filter": f"cited_by_count:>{min_cited - 1},type:article|review|book-chapter",
                                 "select": "doi,title,publication_year,cited_by_count,open_access",
                                 **params_key}, timeout=60)
            resp.raise_for_status()
            j = resp.json()
            for w in j["results"]:
                d = norm_doi(w.get("doi"))
                if not d:
                    continue
                e = found.setdefault(d, {"doi": d, "citing_papers": 0, "cited_by_count": w.get("cited_by_count") or 0,
                                         "title": w.get("title"), "year": w.get("publication_year"),
                                         "is_oa": (w.get("open_access") or {}).get("is_oa")})
                e["citing_papers"] += 1
            got += len(j["results"])
            cursor = j["meta"].get("next_cursor") if j["results"] else None
            time.sleep(0.2)
        log.info("query %-60s → %d works so far", q[:60], len(found))
    import pandas as pd
    g = pd.DataFrame(found.values())
    papers = pd.read_parquet(ROOT / "data/parquet/papers.parquet")
    known = {d for d in papers["doi"].map(norm_doi) if d}
    g = g[~g["doi"].isin(known) & ~g["doi"].str.contains("(issn)", regex=False)]
    g = g.sort_values(["citing_papers", "cited_by_count"], ascending=False).head(top)
    g.insert(0, "rank", range(1, len(g) + 1))
    run.mkdir(parents=True, exist_ok=True)
    g.to_csv(run / "priority.csv", index=False)
    (run / "queries.json").write_text(json.dumps({"queries": queries, "per_query": per_query,
                                                  "min_cited": min_cited, "top": top}, indent=2))
    log.info("priority.csv: %d works (%d open access per OpenAlex)", len(g), int(g["is_oa"].fillna(False).sum()))


# ── locate ─────────────────────────────────────────────────────────────────────

LOC_FIELDS = ["rank", "doi", "citing_papers", "cited_by_count", "status", "title", "year", "venue",
              "in_corpus_paper_id", "oa_status", "pdf_urls", "landing_urls", "notes"]


def locate_all(run: Path) -> None:
    from src.services import locate as L
    pri = read_csv(run / "priority.csv")
    out_path = run / "located.csv"
    done = {r["doi"]: r for r in read_csv(out_path)}
    rows = []
    for i, p in enumerate(pri, 1):
        if p["doi"] in done:
            rows.append(done[p["doi"]])
            continue
        try:
            r = L.locate(p["doi"])
        except Exception as exc:                        # one bad registry answer must not stop the run
            r = {"notes": [f"locate failed: {exc}"], "open_access": [], "in_corpus": None}
        pdfs = [x for x in r.get("open_access") or [] if x["kind"] == "pdf"]
        lands = [x for x in r.get("open_access") or [] if x["kind"] == "landing"]
        status = ("in_corpus" if r.get("in_corpus") else "oa_pdf" if pdfs else "oa_landing" if lands else "no_oa")
        rows.append({**p, "status": status, "title": r.get("title"), "year": r.get("year"), "venue": r.get("venue"),
                     "in_corpus_paper_id": (r.get("in_corpus") or {}).get("paper_id"),
                     "oa_status": r.get("oa_status"),
                     "pdf_urls": json.dumps([{k: x[k] for k in ("url", "version", "license", "host", "source")}
                                             for x in pdfs]),
                     "landing_urls": json.dumps([x["url"] for x in lands]),
                     "notes": "; ".join(r.get("notes") or [])})
        if i % 25 == 0:
            write_csv(out_path, rows, LOC_FIELDS)
            log.info("located %d/%d", i, len(pri))
    write_csv(out_path, rows, LOC_FIELDS)
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    log.info("located.csv: %s", counts)
    write_csv(run / "no_oa.csv", [r for r in rows if r["status"] in ("no_oa", "oa_landing")], LOC_FIELDS)


# ── download ───────────────────────────────────────────────────────────────────

DL_FIELDS = ["doi", "status", "file", "sha256", "bytes", "url", "version", "license", "host", "source", "error"]


def download_all(run: Path, delay: float) -> None:
    import requests
    from src.config.settings import OPEN_ALEX_EMAIL
    PDF_DIR.mkdir(parents=True, exist_ok=True)
    located = [r for r in read_csv(run / "located.csv") if r["status"] == "oa_pdf" and "(issn)" not in r["doi"]]
    out_path = run / "downloads.csv"
    done = {r["doi"]: r for r in read_csv(out_path) if r["status"] == "ok"}
    s = requests.Session()
    s.headers.update({"User-Agent": f"GeoHydroAI-OA/1.0 (mailto:{OPEN_ALEX_EMAIL})",
                      "Accept": "application/pdf,*/*;q=0.5"})
    rows = list(done.values())
    for i, r in enumerate(located, 1):
        if r["doi"] in done:
            continue
        dest = PDF_DIR / f"{slug(r['doi'])}.pdf"
        row = {"doi": r["doi"], "status": "failed", "file": dest.name}
        errors = []
        cands = json.loads(r["pdf_urls"])
        cands.sort(key=lambda c: c.get("host") == "publisher")   # repositories first: publisher bot walls 403
        for cand in cands[:6]:
            try:
                resp = s.get(cand["url"], timeout=60, allow_redirects=True)
                body = resp.content
                if resp.status_code != 200:
                    errors.append(f"{resp.status_code} {cand['url']}")
                elif not body.startswith(b"%PDF"):
                    errors.append(f"not a PDF ({resp.headers.get('content-type')}) {cand['url']}")
                elif not MIN_PDF <= len(body) <= MAX_PDF:
                    errors.append(f"size {len(body)} {cand['url']}")
                else:
                    dest.write_bytes(body)
                    row.update(status="ok", sha256=hashlib.sha256(body).hexdigest(), bytes=len(body), **cand)
                    break
            except requests.RequestException as exc:
                errors.append(f"{type(exc).__name__} {cand['url']}")
            time.sleep(delay)
        row["error"] = " | ".join(errors) if row["status"] != "ok" else ""
        rows.append(row)
        if i % 10 == 0:
            write_csv(out_path, rows, DL_FIELDS)
            log.info("downloads %d/%d (ok %d)", i, len(located), sum(x["status"] == "ok" for x in rows))
        time.sleep(delay)
    write_csv(out_path, rows, DL_FIELDS)
    log.info("downloads.csv: ok %d, failed %d", sum(x["status"] == "ok" for x in rows),
             sum(x["status"] != "ok" for x in rows))


# ── more open-access sources for what locate did not find ───────────────────────

def _title_match(a: str | None, b: str | None) -> bool:
    import difflib
    norm = lambda s: " ".join("".join(ch.lower() if ch.isalnum() else " " for ch in s).split())
    return bool(a and b) and difflib.SequenceMatcher(None, norm(a), norm(b)).ratio() >= 0.92


def more_sources(run: Path, delay: float) -> None:
    """Semantic Scholar openAccessPdf / arXiv id, citation_pdf_url of OA landing pages, arXiv title search."""
    import re
    import xml.etree.ElementTree as ET
    import requests
    from src.config.settings import OPEN_ALEX_EMAIL
    path = run / "located.csv"
    rows = read_csv(path)
    s = requests.Session()
    s.headers.update({"User-Agent": f"GeoHydroAI-OA/1.0 (mailto:{OPEN_ALEX_EMAIL})"})
    todo = [r for r in rows if r["status"] in ("no_oa", "oa_landing") and "(issn)" not in r["doi"]
            and "more:" not in (r["notes"] or "")]
    found = 0
    for i, r in enumerate(todo, 1):
        cands, how = [], []
        try:                                               # Semantic Scholar (no key: ~1 request/s)
            for attempt in range(3):
                resp = s.get(f"https://api.semanticscholar.org/graph/v1/paper/DOI:{r['doi']}",
                             params={"fields": "title,openAccessPdf,externalIds"}, timeout=30)
                if resp.status_code != 429:
                    break
                time.sleep(5 * (attempt + 1))
            if resp.status_code == 200:
                j = resp.json()
                url = (j.get("openAccessPdf") or {}).get("url")
                if url:
                    cands.append({"url": url, "version": None, "license": None, "host": "repository",
                                  "source": "semanticscholar"})
                ax = (j.get("externalIds") or {}).get("ArXiv")
                if ax:
                    cands.append({"url": f"https://arxiv.org/pdf/{ax}", "version": "submittedVersion",
                                  "license": None, "host": "repository", "source": "arxiv"})
                how.append("s2")
        except requests.RequestException:
            pass
        time.sleep(delay)
        for land in json.loads(r["landing_urls"] or "[]")[:2]:   # landing pages OpenAlex/Unpaywall list as OA
            try:
                html = s.get(land, timeout=30).text
                m = re.search(r'<meta[^>]+name=["\']citation_pdf_url["\'][^>]+content=["\']([^"\']+)', html, re.I) \
                    or re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']citation_pdf_url', html, re.I)
                if m:
                    cands.append({"url": m.group(1), "version": None, "license": None, "host": "publisher",
                                  "source": "oa_landing"})
                how.append("landing")
            except requests.RequestException:
                pass
            time.sleep(delay)
        if not cands and r["title"]:                       # arXiv by title (preprints of journal papers)
            try:
                q = " ".join(re.findall(r"[A-Za-z0-9]+", r["title"])[:12])
                resp = s.get("https://export.arxiv.org/api/query",
                             params={"search_query": f'ti:"{q}"', "max_results": 3}, timeout=30)
                ns = {"a": "http://www.w3.org/2005/Atom"}
                for e in ET.fromstring(resp.text).findall("a:entry", ns):
                    if _title_match(e.findtext("a:title", "", ns), r["title"]):
                        aid = e.findtext("a:id", "", ns).rsplit("/abs/", 1)[-1]
                        cands.append({"url": f"https://arxiv.org/pdf/{aid}", "version": "submittedVersion",
                                      "license": None, "host": "repository", "source": "arxiv_title"})
                        break
                how.append("arxiv")
            except (requests.RequestException, ET.ParseError):
                pass
            time.sleep(3)                                  # arXiv asks for 3 s between calls
        r["notes"] = "; ".join(x for x in [r["notes"], "more:" + ",".join(how)] if x)
        if cands:
            r["pdf_urls"] = json.dumps(cands)
            r["status"] = "oa_pdf"
            found += 1
        if i % 20 == 0:
            write_csv(path, rows, LOC_FIELDS)
            log.info("more sources %d/%d (found %d)", i, len(todo), found)
    write_csv(path, rows, LOC_FIELDS)
    write_csv(run / "no_oa.csv", [r for r in rows if r["status"] in ("no_oa", "oa_landing")], LOC_FIELDS)
    log.info("more sources: %d of %d now have a PDF candidate", found, len(todo))


# ── GROBID ─────────────────────────────────────────────────────────────────────

def grobid_all(run: Path) -> None:
    # Header consolidation blocks on an unreachable CrossRef until GROBID's own timeout (180 s client
    # timeouts measured 2026-10-02); the metadata of acquired works come from OpenAlex anyway.
    os.environ.setdefault("GROBID_CONSOLIDATE_HEADER", "0")
    from src.ingestion.grobid_client import GROBIDClient
    got = [r for r in read_csv(run / "downloads.csv") if r["status"] == "ok"]
    links = run / "xml_new"
    links.mkdir(parents=True, exist_ok=True)
    ok = fail = 0
    with GROBIDClient(max_retries=3) as client:
        if not client.is_alive():
            sys.exit("GROBID is not running on :8070 (docker compose up -d grobid)")
        for i, r in enumerate(got, 1):
            pdf = PDF_DIR / r["file"]
            xml = XML_DIR / f"{pdf.stem}.tei.xml"
            if not xml.exists():
                res = client.process_pdf(pdf)
                if not (res.success and res.xml_text):
                    fail += 1
                    log.warning("[%d/%d] GROBID failed %s: %s", i, len(got), pdf.name, res.failure_type)
                    continue
                xml.write_text(res.xml_text, encoding="utf-8")
            ok += 1
            link = links / xml.name
            if not link.exists():
                link.symlink_to(xml)
            if i % 10 == 0:
                log.info("GROBID %d/%d", i, len(got))
    log.info("GROBID: %d TEI ready (linked in %s), %d failed", ok, links, fail)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("stage", choices=["rank", "discover", "locate", "more", "download", "grobid", "all"])
    ap.add_argument("--run", type=Path, default=ROOT / "data" / "acquisition" / f"oa_{date.today():%Y%m%d}")
    ap.add_argument("--top", type=int, default=600)
    ap.add_argument("--min-citing", type=int, default=3)
    ap.add_argument("--queries", type=Path, help="discover: text file, one topic query per line")
    ap.add_argument("--per-query", type=int, default=200)
    ap.add_argument("--min-cited", type=int, default=5)
    ap.add_argument("--delay", type=float, default=1.0, help="seconds between download requests")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    logging.getLogger("httpx").setLevel(logging.WARNING)      # its INFO lines carry registry keys in URLs
    stages = ["rank", "locate", "more", "download", "grobid"] if a.stage == "all" else [a.stage]
    for st in stages:
        log.info("── %s → %s", st, a.run)
        if st == "rank":
            rank(a.run, a.top, a.min_citing)
        elif st == "discover":
            qs = [q.strip() for q in a.queries.read_text().splitlines() if q.strip() and not q.startswith("#")]
            discover(a.run, qs, a.per_query, a.min_cited, a.top)
        elif st == "locate":
            locate_all(a.run)
        elif st == "more":
            more_sources(a.run, a.delay)
        elif st == "download":
            download_all(a.run, a.delay)
        else:
            grobid_all(a.run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
