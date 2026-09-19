"""Second-chance PDF resolution for open-access works. HTTP only, no scraping.

OpenAlex reports `is_oa` for a work but often no `pdf_url`, and the URL it does
report is frequently a landing page that answers a script with HTML or 403. This
module asks the other places an open-access PDF is legitimately published:

    1. Unpaywall           — the canonical OA-location index (needs a contact email)
    2. Europe PMC          — rendered PDFs for anything with a PMCID
    3. OpenAlex locations  — every location flagged `is_oa`, not just the "best"
    4. publisher patterns  — fully-OA publishers whose PDF URL follows from the DOI
                             (MDPI, Copernicus, Frontiers, arXiv) and Wiley's
                             pdfdirect route for works OpenAlex marks open

Only works with `is_oa == True` are attempted, and a location is used only when the
index that supplied it says it is open. Paywalled works are never fetched from
anywhere; they stay on the manual list. Every attempt records the route that
succeeded or the fact that none did, so the download set remains explainable.
"""
from __future__ import annotations

import logging
import re
import time
from datetime import date
from pathlib import Path

import pandas as pd
import requests

from src.config import settings
from src.paper_3._utils import HARVEST_DIR, PDF_MISSING_DIR, normalize_doi

logger = logging.getLogger(__name__)

_UNPAYWALL = "https://api.unpaywall.org/v2/{doi}"
_EUROPEPMC = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
_EUROPEPMC_PDF = "https://europepmc.org/articles/{pmcid}?pdf=render"
_OPENALEX = "https://api.openalex.org/works/doi:{doi}"

_HTTP_TIMEOUT = 60
_DELAY = 1.5
RESULT_FILE = "oa_fallback.csv"

ROUTES = ("unpaywall", "europepmc", "openalex_location", "publisher_pattern")


def browser_session() -> requests.Session:
    """Same browser UA as harvest_ingest: MDPI and IOP refuse anything else."""
    s = requests.Session()
    s.headers.update({
        "User-Agent": ("Mozilla/5.0 (X11; Linux x86_64; rv:128.0) "
                       "Gecko/20100101 Firefox/128.0"),
        "Accept": "application/pdf,*/*",
    })
    return s


# ── candidate URLs per route ──────────────────────────────────────────────────

def unpaywall_urls(doi: str, session: requests.Session, email: str) -> list[str]:
    if not email:
        return []
    try:
        r = session.get(_UNPAYWALL.format(doi=doi), params={"email": email},
                        timeout=_HTTP_TIMEOUT)
    except requests.RequestException:
        return []
    if r.status_code != 200:
        return []
    try:
        payload = r.json()
    except ValueError:
        return []
    if not payload.get("is_oa"):
        return []
    urls: list[str] = []
    best = payload.get("best_oa_location") or {}
    for loc in [best, *(payload.get("oa_locations") or [])]:
        u = (loc or {}).get("url_for_pdf")
        if u and u not in urls:
            urls.append(u)
    return urls


def europepmc_urls(doi: str, session: requests.Session) -> list[str]:
    try:
        r = session.get(_EUROPEPMC, params={
            "query": f'DOI:"{doi}"', "format": "json", "resultType": "core"},
            timeout=_HTTP_TIMEOUT)
    except requests.RequestException:
        return []
    if r.status_code != 200:
        return []
    try:
        results = (r.json().get("resultList") or {}).get("result") or []
    except ValueError:
        return []
    urls: list[str] = []
    for res in results:
        if normalize_doi(res.get("doi") or "") != doi:
            continue
        for ft in ((res.get("fullTextUrlList") or {}).get("fullTextUrl") or []):
            if (ft.get("documentStyle") == "pdf"
                    and str(ft.get("availability", "")).lower().startswith("open")
                    and ft.get("url")):
                urls.append(ft["url"])
        pmcid = res.get("pmcid")
        if pmcid and str(res.get("isOpenAccess", "N")).upper() == "Y":
            urls.append(_EUROPEPMC_PDF.format(pmcid=pmcid))
    return list(dict.fromkeys(urls))


def openalex_location_urls(doi: str, session: requests.Session) -> list[str]:
    params = {}
    if settings.OPEN_ALEX_EMAIL:
        params["mailto"] = settings.OPEN_ALEX_EMAIL
    if settings.OPEN_ALEX_API:
        params["api_key"] = settings.OPEN_ALEX_API
    try:
        r = session.get(_OPENALEX.format(doi=doi), params=params, timeout=_HTTP_TIMEOUT)
    except requests.RequestException:
        return []
    if r.status_code != 200:
        return []
    try:
        work = r.json()
    except ValueError:
        return []
    urls: list[str] = []
    for loc in work.get("locations") or []:
        if not loc.get("is_oa"):
            continue
        if loc.get("pdf_url"):
            urls.append(loc["pdf_url"])
        # MDPI's PDF is the landing page plus /pdf; the harvest frame does not
        # keep landing pages, so derive it here where the work is in hand.
        landing = loc.get("landing_page_url") or ""
        if landing and _MDPI.match(doi):
            urls.append(landing.rstrip("/") + "/pdf")
    return list(dict.fromkeys(urls))


#: DOI prefix → PDF URL. Only publishers whose whole output is open access, plus
#: arXiv; the URL follows from the DOI without a landing page.
_MDPI = re.compile(r"^10\.3390/")
_COPERNICUS = re.compile(r"^10\.5194/(?P<name>[a-z]+)-(?P<rest>[\w.-]+)$")
_FRONTIERS = re.compile(r"^10\.3389/")
_ARXIV = re.compile(r"^10\.48550/arxiv\.(?P<id>.+)$", re.IGNORECASE)
_WILEY = re.compile(r"^10\.(1002|1111|1029)/")


def publisher_pattern_urls(doi: str, landing_page: str = "", is_oa: bool = False
                           ) -> list[str]:
    urls: list[str] = []
    if _MDPI.match(doi) and landing_page:
        urls.append(landing_page.rstrip("/") + "/pdf")
    m = _COPERNICUS.match(doi)
    if m:
        name, rest = m.group("name"), m.group("rest")
        parts = rest.split("-")
        if len(parts) >= 3:
            vol, first, year = parts[0], parts[1], parts[2]
            urls.append(f"https://{name}.copernicus.org/articles/{vol}/{first}/{year}/"
                        f"{name}-{rest}.pdf")
    if _FRONTIERS.match(doi):
        urls.append(f"https://www.frontiersin.org/articles/{doi}/pdf")
    m = _ARXIV.match(doi)
    if m:
        urls.append(f"https://arxiv.org/pdf/{m.group('id')}")
    if _WILEY.match(doi) and is_oa:
        urls.append(f"https://onlinelibrary.wiley.com/doi/pdfdirect/{doi}")
    return urls


# ── fetching ──────────────────────────────────────────────────────────────────

def fetch_pdf(url: str, session: requests.Session, target: Path) -> bool:
    """Save `url` as a PDF if — and only if — the bytes are a PDF."""
    try:
        r = session.get(url, timeout=_HTTP_TIMEOUT, allow_redirects=True)
    except requests.RequestException as exc:
        logger.debug("  %s → %s", url, exc)
        return False
    if r.status_code != 200 or r.content[:5] != b"%PDF-":
        logger.debug("  %s → HTTP %d, %s", url, r.status_code,
                     "PDF" if r.content[:5] == b"%PDF-" else "not a PDF")
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(r.content)
    return True


def resolve_one(doi: str, target: Path, session: requests.Session,
                landing_page: str = "", email: str = "") -> tuple[str, str]:
    """Try each route in order. Returns (route, url) or ("", "")."""
    routes = (
        ("unpaywall", lambda: unpaywall_urls(doi, session, email)),
        ("europepmc", lambda: europepmc_urls(doi, session)),
        ("openalex_location", lambda: openalex_location_urls(doi, session)),
        ("publisher_pattern", lambda: publisher_pattern_urls(doi, landing_page, True)),
    )
    for route, fn in routes:
        for url in fn():
            if fetch_pdf(url, session, target):
                return route, url
            time.sleep(_DELAY / 3)
    return "", ""


def run(candidates: pd.DataFrame, out_dir: Path | None = None,
        session: requests.Session | None = None, email: str | None = None,
        pdf_dir: Path | None = None) -> pd.DataFrame:
    """Attempt every open-access candidate that still has no PDF on disk.

    `candidates` is the harvest frame; only rows with `is_oa` are eligible.
    Writes harvest/oa_fallback.csv and returns it.
    """
    target_dir = Path(out_dir) if out_dir else HARVEST_DIR
    pdf_dir = Path(pdf_dir) if pdf_dir else PDF_MISSING_DIR
    session = session or browser_session()
    email = settings.OPEN_ALEX_EMAIL if email is None else email
    if not email:
        logger.warning("OPEN_ALEX_EMAIL is not set — Unpaywall will be skipped")

    todo = candidates[candidates["is_oa"].fillna(False).astype(bool)
                      & candidates["drop_reason"].fillna("").eq("")]
    rows: list[dict] = []
    fetched = 0
    for i, r in enumerate(todo.itertuples(), 1):
        target = pdf_dir / f"{r.slug}.pdf"
        if target.exists():
            continue
        landing = getattr(r, "landing_page_url", "") or ""
        route, url = resolve_one(r.doi, target, session, landing_page=landing, email=email)
        if route:
            fetched += 1
            logger.info("[%d/%d] ✓ %s via %s", i, len(todo), r.doi, route)
        else:
            logger.info("[%d/%d] ✗ %s — no open-access PDF found", i, len(todo), r.doi)
        rows.append({"doi": r.doi, "slug": r.slug, "route": route or "unresolved",
                     "url": url, "attempted_at": date.today().isoformat()})
        time.sleep(_DELAY)

    frame = pd.DataFrame(rows, columns=["doi", "slug", "route", "url", "attempted_at"])
    target_dir.mkdir(parents=True, exist_ok=True)
    frame.to_csv(target_dir / RESULT_FILE, index=False)
    logger.info("OA fallback: %d fetched of %d attempted (%s)", fetched, len(frame),
                frame["route"].value_counts().to_dict() if len(frame) else {})
    return frame
