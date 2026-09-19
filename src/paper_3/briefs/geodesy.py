"""The geodesy mini-corpus — a separate harvest for topic B, kept apart until calibrated.

Why separate: the flood-oriented knowledge graph returned 14 full-text candidates
for vertical-reference questions, of which three or four were substantive and
one was an acronym list. That is a domain-coverage gap of the corpus, not a gap
in the literature, and the right response is to change the corpus rather than
turn the thresholds again.

Three rules, from the design review, enforced here rather than remembered:

1. **Not merged.** Everything lands under `briefs/geodesy/` with its own manifest.
   Nothing is written to the main corpus, ChromaDB or Neo4j. Relevant papers can
   be promoted later, once extraction has been calibrated on them.
2. **Not only papers.** `source_type` distinguishes `paper` from
   `technical_standard`, `mission_document` and `registry`, because the sentence
   "ATL03 heights are tide-free" comes from the ATBD, not from someone citing it.
3. **Frozen queries.** The query file is hashed into the manifest before the
   first request goes out, and the manifest refuses to move without --force.
   Nudging a term until a known paper appears is tuning the search to its answer.

Steps: discover → download → grobid → text → screen. Each guards on its own
output, so the chain resumes rather than restarts.
"""
from __future__ import annotations

import hashlib
import json
import logging
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests
import yaml

from src.paper_3._utils import OUT_DIR, PROJECT_ROOT, doi_to_slug, normalize_doi

logger = logging.getLogger(__name__)

QUERIES_PATH = Path(__file__).resolve().parent / "geodesy_queries.yaml"
TECHNICAL_PATH = Path(__file__).resolve().parent / "technical_sources.yaml"

#: `curated_paper` is a peer-reviewed article the harvest cannot reach (no DOI
#: or not in OpenAlex), listed by hand in technical_sources.yaml. It is a source,
#: never a positive control: retrieval could not recover it, so injecting it
#: would measure nothing.
SOURCE_TYPES = ("paper", "curated_paper", "technical_standard", "mission_document",
                "registry")

MANIFEST = "HARVEST_MANIFEST.json"
CANDIDATES = "discovery_candidates.parquet"
FULLTEXT_MANIFEST = "fulltext_manifest.parquet"
SCREENING_SHEET = "SCREENING_SHEET.csv"

#: Target for full-text candidates. After manual screening this should leave
#: 10–20 genuinely usable papers for calibrating the extractor. Above the upper
#: bound the harvest is sampled rather than downloaded wholesale.
TARGET_FULLTEXT_MIN = 30
TARGET_FULLTEXT_MAX = 60

CHUNK_CHARS = 2_000
MIN_GEODETIC_OCCURRENCES = 3
MIN_BEST_CHUNK_PER_10K = 8.0

#: Hosts that refused every scripted request in the first round — 403 from
#: Wiley/AGU/Elsevier/T&F/OUP, and HTTP 202 (a JavaScript interstitial) from
#: De Gruyter and Curtin's repository. They are not excluded, only queued last:
#: a paper there is still worth trying once the friendly hosts are exhausted.
BLOCKING_HOSTS = (
    "onlinelibrary.wiley.com", "agupubs.onlinelibrary.wiley.com",
    "sciencedirect.com", "tandfonline.com", "academic.oup.com",
    "degruyter.com", "espace.curtin.edu.au",
)


def geodesy_dir(out_dir: Path | None = None) -> Path:
    return (Path(out_dir) if out_dir else OUT_DIR) / "briefs" / "geodesy"


# ── query families ────────────────────────────────────────────────────────────

def load_families(path: Path | None = None) -> dict:
    return yaml.safe_load(Path(path or QUERIES_PATH).read_text(encoding="utf-8"))


def all_queries(families: dict) -> list[dict]:
    """Flatten to [{family, query}], preserving file order."""
    return [{"family": fam["id"], "query": q}
            for fam in families["families"] for q in fam["queries"]]


def geodetic_terms(families: dict) -> list[str]:
    """Every family term, minus the generic ones that need co-occurrence."""
    generic = {t.lower() for t in families.get("generic_terms", [])}
    out: list[str] = []
    for fam in families["families"]:
        out.extend(t for t in fam["terms"] if t.lower() not in generic)
    return out


def families_in_text(families: dict, text: str) -> list[str]:
    low = (text or "").lower()
    return [fam["id"] for fam in families["families"]
            if any(t.lower() in low for t in fam["terms"])]


# ── the frozen manifest ───────────────────────────────────────────────────────

def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git_short() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                              cwd=str(PROJECT_ROOT), capture_output=True,
                              text=True, timeout=10).stdout.strip()
    except Exception:
        return ""


def freeze_manifest(out_dir: Path | None = None, force: bool = False) -> dict:
    """Record the query set before any request is sent.

    Refuses to re-freeze over a different query file unless forced, and keeps
    the earlier freeze in `history` when it is.
    """
    target = geodesy_dir(out_dir)
    target.mkdir(parents=True, exist_ok=True)
    path = target / MANIFEST

    families = load_families()
    current = {
        "frozen_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "git_commit": _git_short(),
        "queries_file": str(QUERIES_PATH),
        "queries_sha256": _sha256(QUERIES_PATH),
        "technical_sources_sha256": _sha256(TECHNICAL_PATH),
        "n_families": len(families["families"]),
        "n_queries": len(all_queries(families)),
        "generic_terms": list(families.get("generic_terms", [])),
        "filters": families.get("filters", {}),
        "corpus_policy": "separate — not merged into the main KG until calibrated",
        "target_fulltext": [TARGET_FULLTEXT_MIN, TARGET_FULLTEXT_MAX],
    }

    history: list[dict] = []
    if path.exists():
        existing = json.loads(path.read_text(encoding="utf-8"))
        history = existing.get("history", [])
        previous = {k: v for k, v in existing.items() if k != "history"}
        if previous.get("queries_sha256") != current["queries_sha256"]:
            if not force:
                raise RuntimeError(
                    "geodesy_queries.yaml has changed since the manifest was "
                    "frozen. Changing the query set after seeing results is "
                    "tuning the search to its answer. Pass --force to re-freeze "
                    "deliberately; the previous set is kept in `history`.")
            history.append(previous)
        else:
            # Same queries: keep the original freeze time, refresh nothing.
            current["frozen_at"] = previous.get("frozen_at", current["frozen_at"])

    path.write_text(json.dumps({**current, "history": history}, indent=2,
                               ensure_ascii=False), encoding="utf-8")
    logger.info("Geodesy manifest frozen: %d families, %d queries, sha %s",
                current["n_families"], current["n_queries"],
                current["queries_sha256"][:12])
    return current


def assert_manifest_matches(out_dir: Path | None = None) -> None:
    path = geodesy_dir(out_dir) / MANIFEST
    if not path.exists():
        raise FileNotFoundError(
            f"{path} missing — run `freeze_manifest()` (step `freeze`) before "
            f"discovery, so the query set is on record before results exist.")
    frozen = json.loads(path.read_text(encoding="utf-8"))
    if frozen["queries_sha256"] != _sha256(QUERIES_PATH):
        raise RuntimeError(
            "geodesy_queries.yaml differs from the frozen manifest. Re-freeze "
            "with --force if the change is deliberate.")


# ── step: discover ────────────────────────────────────────────────────────────

def discover(out_dir: Path | None = None, per_query: int = 50,
             max_pages: int = 2, session: requests.Session | None = None
             ) -> pd.DataFrame:
    """Run every frozen query; dedupe by DOI; record which families matched."""
    from src.paper_3.harvest_openalex import (
        _DELAY, make_session, openalex_search, to_record)

    assert_manifest_matches(out_dir)
    target = geodesy_dir(out_dir)
    families = load_families()
    session = session or make_session()
    filters = families.get("filters", {})

    by_doi: dict[str, dict] = {}
    worldwide: dict[str, int] = {}

    queries = all_queries(families)
    for i, item in enumerate(queries, 1):
        logger.info("[%d/%d] %s :: %r", i, len(queries), item["family"], item["query"])
        works, count = openalex_search(item["query"], session, per_page=per_query,
                                       max_pages=max_pages, filters=filters)
        worldwide[item["query"]] = count
        for work in works:
            rec = to_record(work)
            if not rec:
                continue
            row = by_doi.get(rec["doi"])
            if row is None:
                row = {**rec, "matched_families": {item["family"]},
                       "matched_queries": {item["query"]}, "n_query_hits": 0}
                by_doi[rec["doi"]] = row
            row["matched_families"].add(item["family"])
            row["matched_queries"].add(item["query"])
            row["n_query_hits"] += 1
        time.sleep(_DELAY)

    rows = []
    for row in by_doi.values():
        text = f"{row.get('title', '')} {row.get('abstract', '')}"
        rows.append({
            **{k: v for k, v in row.items()
               if k not in ("matched_families", "matched_queries")},
            "source_type": "paper",
            "matched_families": sorted(row["matched_families"]),
            "n_families_matched": len(row["matched_families"]),
            "families_in_abstract": families_in_text(families, text),
            "n_query_hits": row["n_query_hits"],
            "harvested_at": datetime.now(timezone.utc).date().isoformat(),
            "provenance": "openalex_search",
        })

    frame = pd.DataFrame(rows)
    if frame.empty:
        logger.error("geodesy discovery returned nothing")
        return frame

    # Rank for download: more families is a stronger signal than more hits of
    # one family, because it means the paper relates the concepts.
    frame["download_priority"] = (
        3.0 * frame["n_families_matched"]
        + 1.0 * frame["n_query_hits"]
        + 2.0 * frame["families_in_abstract"].map(len)
        + frame["cited_by_count"].clip(upper=200) / 50.0
    ).round(3)
    frame = frame.sort_values("download_priority", ascending=False)

    target.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(target / CANDIDATES, index=False)
    (target / "worldwide_counts.json").write_text(
        json.dumps(worldwide, indent=2, ensure_ascii=False), encoding="utf-8")

    oa = int((frame["is_oa"] & frame["pdf_url"].ne("")).sum())
    logger.info("Geodesy discovery: %d unique works, %d with an OA PDF, "
                "%d matching >=2 families",
                len(frame), oa, int((frame["n_families_matched"] >= 2).sum()))
    return frame


# ── technical sources ─────────────────────────────────────────────────────────

def technical_sources() -> pd.DataFrame:
    """The non-paper authorities, as rows of the same shape as papers."""
    raw = yaml.safe_load(TECHNICAL_PATH.read_text(encoding="utf-8")) or []
    rows = []
    for entry in raw:
        url = entry.get("url", "") or ""
        verified = entry.get("status", "") == "url_verified"
        rows.append({
            "doi": "", "slug": entry["id"], "openalex_id": "",
            "title": entry.get("title", ""), "abstract": "",
            "year": entry.get("year"), "journal": entry.get("issuer", ""),
            "cited_by_count": 0, "type": entry.get("source_type", ""),
            # Only a URL that was actually fetched by hand counts as open access;
            # an unverified URL is not downloaded (an invented URL is worse than none).
            "is_oa": bool(url) and verified, "pdf_url": url if verified else "",
            "referenced_works_count": 0,
            "source_type": entry.get("source_type", ""),
            "matched_families": list(entry.get("bears_on", [])),
            "n_families_matched": len(entry.get("bears_on", [])),
            "families_in_abstract": [],
            "n_query_hits": 0,
            "harvested_at": "",
            "provenance": "technical_sources.yaml",
            "identifier": entry.get("identifier", ""),
            "status": entry.get("status", ""),
            "mandatory": bool(entry.get("mandatory", False)),
            "formal_citation": entry.get("formal_citation", "") or "",
            # str(): an all-digit hash would otherwise load from YAML as an int.
            "url_sha256": str(entry.get("url_sha256") or ""),
            "download_priority": 99.0,   # always first: they are the authorities
        })
    return pd.DataFrame(rows)


def expected_sha256(slug: str) -> str:
    """The sha256 recorded for a hand-verified technical source, or ''."""
    raw = yaml.safe_load(TECHNICAL_PATH.read_text(encoding="utf-8")) or []
    for entry in raw:
        if entry.get("id") == slug:
            return str(entry.get("url_sha256") or "")
    return ""


# ── step: download ────────────────────────────────────────────────────────────

def _host(url: str) -> str:
    from urllib.parse import urlparse
    return urlparse(url or "").netloc.replace("www.", "")


def _rewrite_pdf_url(url: str) -> str:
    """Point at the PDF binary where OpenAlex recorded the article page instead.

    Only patterns observed to fail in the first round, each verified against the
    publisher's actual layout. Anything unrecognised is returned unchanged.
    """
    host = _host(url)
    if host == "link.springer.com" and "/article/" in url:
        # /article/10.1007/x  →  /content/pdf/10.1007/x.pdf
        doi_part = url.split("/article/", 1)[1].split("?")[0]
        return f"https://link.springer.com/content/pdf/{doi_part}.pdf"
    if host.endswith("springeropen.com") and "/articles/" in url:
        doi_part = url.split("/articles/", 1)[1].split("?")[0]
        return f"https://{host}/track/pdf/{doi_part}"
    if host == "mdpi.com":
        base = url.split("?")[0].rstrip("/")
        if base.endswith("/htm"):
            return base[:-4] + "/pdf"
        if not base.endswith("/pdf"):
            return base + "/pdf"
    return url


def select_for_download(frame: pd.DataFrame,
                        target_max: int = TARGET_FULLTEXT_MAX,
                        exclude_dois: set[str] | None = None) -> pd.DataFrame:
    """Top-ranked OA candidates, capped so the screening stays manual-sized.

    Known-blocking hosts are queued last rather than dropped: the first round
    lost 46 of 60 to them, so a batch that leads with the friendly hosts is the
    one that actually reaches GROBID.
    """
    eligible = frame[frame["is_oa"] & frame["pdf_url"].ne("")].copy()
    if exclude_dois:
        eligible = eligible[~eligible["doi"].isin(exclude_dois)]
    eligible["_blocked"] = eligible["pdf_url"].map(_host).isin(BLOCKING_HOSTS)
    eligible["_rank"] = eligible["download_priority"] - 100.0 * eligible["_blocked"]

    strong = eligible[eligible["n_families_matched"] >= 2]
    if len(strong) >= target_max:
        chosen = strong.nlargest(target_max, "_rank")
    else:
        rest = eligible.drop(strong.index).nlargest(target_max - len(strong), "_rank")
        chosen = pd.concat([strong, rest])
    return chosen.drop(columns=["_blocked", "_rank"])


#: Repositories that serve green-OA copies reliably. Tried before publisher
#: hosts when several OA locations exist for one work.
REPOSITORY_HINTS = ("arxiv.org", "hal.science", "hal.archives-ouvertes.fr",
                    "zenodo.org", "europepmc.org", "ncbi.nlm.nih.gov",
                    "eprints.", "repository.", "dspace.", "orbit.", "pure.",
                    "research-collection.", "researchgate.net", "osf.io",
                    "copernicus.org", "mdpi.com", "pubs.usgs.gov")


def _host_rank(url: str) -> int:
    host = _host(url)
    if any(h in host for h in REPOSITORY_HINTS):
        return 0
    if host in BLOCKING_HOSTS:
        return 2
    return 1


def alternate_pdf_urls(doi: str, session: requests.Session | None = None
                       ) -> list[str]:
    """Every open-access PDF URL OpenAlex knows for a work, friendliest first.

    `harvest_openalex.to_record` keeps only `best_oa_location`, which for many
    Springer and Wiley papers is the publisher's own link — and that link serves
    an HTML paywall page with HTTP 200 even when a green copy sits in a
    repository. This re-queries the work and returns all of them.
    """
    from src.paper_3.harvest_openalex import fetch_by_doi, make_session

    work = fetch_by_doi(doi, session or make_session())
    if not work:
        return []
    urls: list[str] = []
    for loc in work.get("locations") or []:
        if loc.get("is_oa") and loc.get("pdf_url"):
            urls.append(loc["pdf_url"])
    best = (work.get("best_oa_location") or {}).get("pdf_url")
    if best and best not in urls:
        urls.insert(0, best)
    seen: set[str] = set()
    ordered = []
    for u in sorted(urls, key=_host_rank):
        if u not in seen:
            seen.add(u)
            ordered.append(u)
    return ordered


def retry_alternates(out_dir: Path | None = None, max_per_doi: int = 4) -> pd.DataFrame:
    """For every row still failed, try each alternate OA location once.

    A separate step from `download` so it runs only after the main chain has
    finished writing the manifest, and so its cost — one OpenAlex lookup per
    failed DOI — is visible rather than folded into every download.
    """
    from src.paper_3.harvest_ingest import _pdf_session
    from src.paper_3.harvest_openalex import _DELAY, make_session

    target = geodesy_dir(out_dir)
    manifest = pd.read_parquet(target / FULLTEXT_MANIFEST)
    failed = manifest[~manifest["download_status"].isin(
        ["downloaded", "already_present"])]
    if failed.empty:
        logger.info("retry_alternates: nothing to retry")
        return manifest

    api = make_session()
    pdf_session = _pdf_session()
    recovered = 0
    for i in failed.index:
        doi = manifest.at[i, "doi"]
        pdf = Path(manifest.at[i, "pdf_path"])
        alternates = alternate_pdf_urls(doi, api)[:max_per_doi]
        time.sleep(_DELAY)
        status = manifest.at[i, "download_status"]
        for url in alternates:
            status = _fetch_pdf(pdf_session, url, pdf)
            time.sleep(1.5)
            if status == "downloaded":
                recovered += 1
                manifest.at[i, "download_status"] = "downloaded"
                logger.info("  recovered via %-28s %s", _host(url),
                            str(manifest.at[i, "title"])[:50])
                break
        else:
            manifest.at[i, "download_status"] = (
                f"{status}|alternates_tried={len(alternates)}")

    manifest.to_parquet(target / FULLTEXT_MANIFEST, index=False)
    logger.info("retry_alternates: %d of %d recovered", recovered, len(failed))
    return manifest


def _fetch_pdf(session: requests.Session, url: str, target: Path) -> str:
    """One attempt. Returns a status string; writes the file only on a real PDF."""
    try:
        r = session.get(url, timeout=60, allow_redirects=True)
    except requests.RequestException as exc:
        return f"error:{type(exc).__name__}"
    if r.status_code == 200 and r.content[:5] == b"%PDF-":
        target.write_bytes(r.content)
        return "downloaded"
    return f"not_pdf_http_{r.status_code}"


def download(out_dir: Path | None = None,
             target_max: int = TARGET_FULLTEXT_MAX,
             extend: bool = False) -> pd.DataFrame:
    """Fetch OA PDFs for the selected batch.

    With `extend=True`, rows already in the manifest are kept, previously
    failed rows are retried once through `_rewrite_pdf_url`, and a fresh batch
    of `target_max` untried candidates is appended — friendly hosts first.
    """
    from src.paper_3.harvest_ingest import _pdf_session

    target = geodesy_dir(out_dir)
    frame = pd.read_parquet(target / CANDIDATES)
    pdf_dir = target / "pdf"
    pdf_dir.mkdir(parents=True, exist_ok=True)
    session = _pdf_session()
    url_by_doi = dict(zip(frame["doi"], frame["pdf_url"]))

    existing = pd.DataFrame()
    manifest_path = target / FULLTEXT_MANIFEST
    if extend and manifest_path.exists():
        existing = pd.read_parquet(manifest_path)

    rows: list[dict] = []

    # Retry earlier failures with a corrected URL — one attempt, no loop.
    if not existing.empty:
        for i in existing.index:
            row = existing.loc[i]
            if row["download_status"] in ("downloaded", "already_present"):
                rows.append(row.to_dict())
                continue
            pdf = Path(row["pdf_path"])
            new_url = _rewrite_pdf_url(url_by_doi.get(row["doi"], ""))
            if new_url != url_by_doi.get(row["doi"], ""):
                status = _fetch_pdf(session, new_url, pdf)
                logger.info("  retry %-16s %s", status, str(row["title"])[:56])
                time.sleep(1.5)
            else:
                status = row["download_status"]
            rows.append({**row.to_dict(), "download_status": status})

    tried = {r["doi"] for r in rows}
    chosen = select_for_download(frame, target_max, exclude_dois=tried)
    for row in chosen.itertuples():
        pdf = pdf_dir / f"{row.slug}.pdf"
        if pdf.exists():
            status = "already_present"
        else:
            status = _fetch_pdf(session, _rewrite_pdf_url(row.pdf_url), pdf)
            time.sleep(1.5)
        rows.append({"doi": row.doi, "slug": row.slug, "title": row.title,
                     "source_type": "paper", "pdf_path": str(pdf),
                     "download_status": status, "xml_path": "",
                     "grobid_status": "", "text_path": "", "n_chars": 0})
        logger.info("  %-22s %s", status, row.title[:60])

    manifest = pd.DataFrame(rows).drop_duplicates("doi", keep="last")
    manifest.to_parquet(manifest_path, index=False)
    logger.info("Download: %d in manifest, %d on disk",
                len(manifest),
                int(manifest["download_status"].isin(
                    ["downloaded", "already_present"]).sum()))
    return manifest


# ── step: curated ─────────────────────────────────────────────────────────────

def download_curated(out_dir: Path | None = None,
                     session: requests.Session | None = None) -> pd.DataFrame:
    """Fetch the hand-verified technical sources and append them to the manifest.

    Separate from `download()` on purpose: that step samples the OpenAlex
    harvest and would overwrite or extend the batch, while a curated source is
    one named file. Only `status: url_verified` entries are fetched, and a PDF
    whose sha256 differs from the recorded `url_sha256` is marked
    `sha_mismatch` rather than admitted — the bytes the user vetted are the
    bytes that enter the corpus. Rows already in the manifest are left alone.
    """
    from src.paper_3.harvest_ingest import _pdf_session

    target = geodesy_dir(out_dir)
    pdf_dir = target / "pdf"
    pdf_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = target / FULLTEXT_MANIFEST
    existing = pd.read_parquet(manifest_path) if manifest_path.exists() else pd.DataFrame()
    have = set(existing["slug"]) if not existing.empty else set()
    session = session or _pdf_session()

    frame = technical_sources()
    # Only a vetted PDF is fetched: `url_sha256` is what says the URL points at
    # a file rather than a registry web page (EPSG, CRS-EU), which is cited,
    # not ingested.
    frame = frame[frame["is_oa"] & frame["pdf_url"].ne("") & frame["url_sha256"].ne("")]
    rows: list[dict] = []
    for row in frame.itertuples():
        if row.slug in have:
            logger.info("  curated %-18s %s (already in manifest)", "skip", row.slug)
            continue
        pdf = pdf_dir / f"{row.slug}.pdf"
        if pdf.exists():
            status = "already_present"
        else:
            status = _fetch_pdf(session, row.pdf_url, pdf)
            time.sleep(1.5)
        if pdf.exists() and row.url_sha256 and _sha256(pdf) != row.url_sha256:
            status = "sha_mismatch"
            logger.error("  curated %s: sha256 %s != recorded %s — not admitted",
                         row.slug, _sha256(pdf)[:12], row.url_sha256[:12])
        rows.append({"doi": row.doi, "slug": row.slug, "title": row.title,
                     "source_type": row.source_type, "pdf_path": str(pdf),
                     "download_status": status, "xml_path": "",
                     "grobid_status": "", "text_path": "", "n_chars": 0})
        logger.info("  curated %-18s %s", status, row.slug)

    manifest = pd.concat([existing, pd.DataFrame(rows)], ignore_index=True) \
        if rows else existing
    if rows or existing.empty:
        manifest.to_parquet(manifest_path, index=False)
    logger.info("Curated: %d fetched, %d already listed, %d in manifest",
                len(rows), len(have & set(frame["slug"])), len(manifest))
    return manifest


# ── step: grobid ──────────────────────────────────────────────────────────────

def grobid(out_dir: Path | None = None) -> pd.DataFrame:
    from src.ingestion.grobid_client import GROBIDClient

    target = geodesy_dir(out_dir)
    manifest = pd.read_parquet(target / FULLTEXT_MANIFEST)
    xml_dir = target / "xml"
    xml_dir.mkdir(parents=True, exist_ok=True)

    with GROBIDClient(max_retries=3) as client:
        if not client.is_alive():
            raise RuntimeError("GROBID is not answering on :8070")
        for i in manifest.index:
            pdf = Path(manifest.at[i, "pdf_path"])
            xml = xml_dir / f"{manifest.at[i, 'slug']}.tei.xml"
            if xml.exists():
                manifest.at[i, "xml_path"] = str(xml)
                manifest.at[i, "grobid_status"] = "already_present"
                continue
            if not pdf.exists():
                manifest.at[i, "grobid_status"] = "no_pdf"
                continue
            result = client.process_pdf(pdf)
            if result.success and result.xml_text:
                xml.write_text(result.xml_text, encoding="utf-8")
                manifest.at[i, "xml_path"] = str(xml)
                manifest.at[i, "grobid_status"] = "ok"
            else:
                manifest.at[i, "grobid_status"] = f"failed:{result.failure_type}"
            logger.info("  grobid %-18s %s", manifest.at[i, "grobid_status"],
                        str(manifest.at[i, "title"])[:60])

    manifest.to_parquet(target / FULLTEXT_MANIFEST, index=False)
    return manifest


# ── step: text ────────────────────────────────────────────────────────────────

def extract_text(out_dir: Path | None = None) -> pd.DataFrame:
    """TEI → {title, abstract, sections} JSON, the shape `paper_sections` reads."""
    from src.document.parser import TEIParser

    target = geodesy_dir(out_dir)
    manifest = pd.read_parquet(target / FULLTEXT_MANIFEST)
    text_dir = target / "text"
    text_dir.mkdir(parents=True, exist_ok=True)
    parser = TEIParser()

    for i in manifest.index:
        xml_path = manifest.at[i, "xml_path"]
        if not xml_path or not Path(xml_path).exists():
            continue
        out = text_dir / f"{manifest.at[i, 'slug']}.json"
        try:
            doc = parser.parse_file(Path(xml_path), manifest.at[i, "slug"])
        except Exception as exc:
            logger.warning("  parse failed %s: %s", manifest.at[i, "slug"], exc)
            continue
        sections = {}
        for sec in doc.sections:
            name = (f"{sec.n} {sec.title}".strip() if sec.n else sec.title) or "body"
            if sec.text.strip():
                sections[name] = sec.text
        payload = {"paper_id": manifest.at[i, "slug"], "title": doc.title,
                   "abstract": doc.abstract, "sections": sections,
                   "source": "geodesy_mini_corpus"}
        out.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        manifest.at[i, "text_path"] = str(out)
        manifest.at[i, "n_chars"] = sum(len(v) for v in sections.values())

    manifest.to_parquet(target / FULLTEXT_MANIFEST, index=False)
    logger.info("Text: %d papers with extractable sections",
                int(manifest["text_path"].ne("").sum()))
    return manifest


# ── step: screen ──────────────────────────────────────────────────────────────

def _best_chunk(terms: list[str], text: str, chunk: int = CHUNK_CHARS
                ) -> tuple[float, int, str]:
    """(density per 10k, co-occurring windows, sample passage)."""
    low = text.lower()
    step = max(chunk // 2, 1)
    scale = 10_000.0 / chunk
    best, best_start, cooccur = 0.0, 0, 0
    for start in range(0, max(len(low) - chunk, 0) + 1, step):
        window = low[start:start + chunk]
        hits = sum(window.count(t.lower()) for t in terms)
        distinct = sum(1 for t in terms if t.lower() in window)
        if distinct >= 2:
            cooccur += 1
        if hits * scale > best:
            best, best_start = hits * scale, start

    # The sample must show the reviewer the geodetic content, not the start of
    # the window it happens to fall in — a dense Methods paragraph can sit at
    # the end of a window that opens with unrelated text. Anchor on the first
    # term hit inside the best window, with a little lead-in for context.
    window_low = low[best_start:best_start + chunk]
    positions = [window_low.find(t.lower()) for t in terms]
    positions = [p for p in positions if p >= 0]
    anchor = best_start + (min(positions) if positions else 0)
    sample_start = max(best_start, anchor - 80)
    sample = text[sample_start:sample_start + 400].replace("\n", " ")
    return round(best, 3), cooccur, sample


def screen(out_dir: Path | None = None) -> pd.DataFrame:
    """Rank the full texts and write the manual screening sheet.

    The sheet is the hand-off: a `manual_verdict` column to fill with
    `usable | marginal | irrelevant`, plus the best passage so the reviewer can
    decide from the sheet without opening every PDF.
    """
    target = geodesy_dir(out_dir)
    manifest = pd.read_parquet(target / FULLTEXT_MANIFEST)
    families = load_families()
    terms = geodetic_terms(families)
    generic = [t.lower() for t in families.get("generic_terms", [])]

    rows = []
    for row in manifest.itertuples():
        if not row.text_path or not Path(row.text_path).exists():
            continue
        payload = json.loads(Path(row.text_path).read_text(encoding="utf-8"))
        text = " ".join(payload.get("sections", {}).values())
        if not text:
            continue

        low = text.lower()
        occurrences = sum(low.count(t.lower()) for t in terms)
        doc_density = round(occurrences / max(len(text) / 10_000.0, 1.0), 3)
        chunk_density, cooccur, sample = _best_chunk(terms, text)
        fams = families_in_text(families, text)

        # Generic terms count only when a geodetic term shares the window.
        generic_only = (any(g in low for g in generic) and occurrences == 0)

        admitted = (occurrences >= MIN_GEODETIC_OCCURRENCES
                    and (doc_density >= 1.5 or chunk_density >= MIN_BEST_CHUNK_PER_10K)
                    and not generic_only)

        rows.append({
            "slug": row.slug, "doi": row.doi, "title": row.title,
            "source_type": row.source_type,
            "n_chars": len(text),
            "families_in_fulltext": ";".join(fams),
            "n_families": len(fams),
            "geodetic_occurrences": occurrences,
            "doc_density_per_10k": doc_density,
            "best_chunk_per_10k": chunk_density,
            "cooccurring_windows": cooccur,
            "generic_only": generic_only,
            "auto_admitted": admitted,
            "read_priority": round(3 * chunk_density + 2 * len(fams)
                                   + 1.5 * cooccur + occurrences / 10, 3),
            "best_passage": sample,
            "manual_verdict": "",
            "manual_note": "",
        })

    sheet = pd.DataFrame(rows).sort_values("read_priority", ascending=False)
    sheet.to_csv(target / SCREENING_SHEET, index=False)
    logger.info("Screening: %d full texts, %d auto-admitted → %s",
                len(sheet), int(sheet["auto_admitted"].sum()),
                target / SCREENING_SHEET)
    logger.info("Fill `manual_verdict` with usable | marginal | irrelevant; "
                "aim for 10–20 usable before calibrating extraction.")
    return sheet


# ── orchestration ─────────────────────────────────────────────────────────────

STEPS = ("freeze", "discover", "download", "alternates", "curated", "grobid", "text",
         "screen")


def run(steps: tuple[str, ...] = STEPS, out_dir: Path | None = None,
        force: bool = False, extend: bool = False) -> None:
    for step in steps:
        logger.info("=== geodesy: %s ===", step)
        if step == "freeze":
            freeze_manifest(out_dir, force=force)
        elif step == "discover":
            discover(out_dir)
        elif step == "download":
            download(out_dir, extend=extend)
        elif step == "alternates":
            retry_alternates(out_dir)
        elif step == "curated":
            download_curated(out_dir)
        elif step == "grobid":
            grobid(out_dir)
        elif step == "text":
            extract_text(out_dir)
        elif step == "screen":
            screen(out_dir)


if __name__ == "__main__":  # pragma: no cover
    import argparse

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s  %(levelname)-7s  %(message)s",
                        datefmt="%H:%M:%S")
    ap = argparse.ArgumentParser(description="Geodesy mini-corpus for topic B")
    ap.add_argument("--step", action="append", choices=STEPS)
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    run(tuple(args.step) if args.step else STEPS, force=args.force)
