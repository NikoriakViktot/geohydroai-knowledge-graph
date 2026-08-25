"""
Missing Reference Recovery & Citation-Impact Analysis Pipeline

Finds highly-cited papers that are referenced in the corpus but absent from
the local papers.parquet / Neo4j graph.  Queries OpenAlex for metadata and
produces ranked CSV + JSON + Markdown outputs.

Usage:
    .venv/bin/python3 -m src.analytics.missing_reference_recovery [--limit N] [--min-citations N]

Optional flags:
    --limit N           only process the top-N most-frequently referenced missing DOIs
    --min-citations N   minimum cited_by_count threshold (default 10)
    --dry-run           skip API calls, only show statistics
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sqlite3
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

import pandas as pd
import requests

# ─── paths ────────────────────────────────────────────────────────────────────

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_PARQUET  = PROJECT_ROOT / "data" / "parquet"
ANALYTICS_DIR = PROJECT_ROOT / "data" / "analytics"
CACHE_DB      = PROJECT_ROOT / "data" / "cache" / "openalex_doi.db"
OUTPUT_CSV    = ANALYTICS_DIR / "missing_high_impact_references.csv"
OUTPUT_JSON   = ANALYTICS_DIR / "missing_reference_summary.json"
OUTPUT_MD     = PROJECT_ROOT / "MISSING_REFERENCE_ANALYSIS.md"

ANALYTICS_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

OPENALEX_EMAIL = "nikoriakviktor@gmail.com"
OPENALEX_BASE  = "https://api.openalex.org"
BATCH_SIZE     = 50   # OpenAlex supports up to ~50 DOIs per filter request

# ─── category keywords ────────────────────────────────────────────────────────

CATEGORY_RULES: list[tuple[str, list[str]]] = [
    ("Remote sensing",      ["sentinel", "modis", "landsat", "sar", "radar", "satellite",
                             "remote sens", "multispectral", "optical", "backscatter",
                             "mndwi", "ndwi", "ndvi", "image classif"]),
    ("Hydraulic modeling",  ["hydraulic", "flood routing", "hec-ras", "lisflood", "2d model",
                             "inundation model", "shallow water", "saint-venant"]),
    ("Hydrological modeling",["hydrological", "rainfall-runoff", "runoff model", "swat",
                              "nash-sutcliffe", "curve number", "evapotranspir", "streamflow",
                              "discharge", "catchment"]),
    ("AI/ML",               ["machine learning", "deep learning", "neural network", "random forest",
                             "convolutional", "cnn", "lstm", "xgboost", "classification",
                             "segmentation", "detection"]),
    ("DEM/topography",      ["dem", "digital elevation", "hand", "height above nearest drainage",
                             "fabdem", "copernicus dem", "srtm", "lidar", "terrain"]),
    ("Evaluation metrics",  ["accuracy", "precision", "recall", "f1", "iou", "kappa",
                             "critical success", "pod", "far", "roc", "auc"]),
    ("General EO",          ["earth observation", "copernicus", "eo", "geospatial", "gis",
                             "mapping", "flood map", "inundation map", "change detection"]),
]

LANDMARK_HINTS: list[tuple[str, str]] = [
    ("10.1080/01431160600589179",    "Xu 2006 MNDWI"),
    ("10.1080/01431161.2016.1192304","Twele et al. 2016 Sentinel-1"),
    ("10.1029/2018ef001026",         "FABDEM"),
    ("10.1029/2010wr010017",         "HAND"),
    ("10.1016/j.rse.2017.06.031",    "Sentinel-1 flood mapping"),
    ("10.1080/01431169608948714",     "Gao 1996 NDWI"),
    ("10.1016/0022-1694(70)90255-6", "Nash-Sutcliffe 1970"),
    ("10.1038/nclimate1911",         "Flood risk climate"),
    ("10.1038/nature20584",          "Global flood exposure"),
    ("10.1029/2005rg000183",         "Biancamaria global rivers"),
]

LANDMARK_DOIS = {doi.lower(): label for doi, label in LANDMARK_HINTS}

# ─── cache helpers ────────────────────────────────────────────────────────────

def _open_cache() -> sqlite3.Connection:
    conn = sqlite3.connect(CACHE_DB)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS openalex_extended (
            doi       TEXT PRIMARY KEY,
            response  TEXT NOT NULL,
            fetched_at TEXT NOT NULL
        )
    """)
    conn.commit()
    return conn


def _cache_get(conn: sqlite3.Connection, doi: str) -> dict | None:
    row = conn.execute(
        "SELECT response FROM openalex_extended WHERE doi = ?", (doi.lower(),)
    ).fetchone()
    if row:
        return json.loads(row[0])
    return None


def _cache_put(conn: sqlite3.Connection, doi: str, data: dict | None) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO openalex_extended (doi, response, fetched_at) VALUES (?,?,datetime('now'))",
        (doi.lower(), json.dumps(data)),
    )
    conn.commit()


# ─── OpenAlex batch query ─────────────────────────────────────────────────────

_SELECT_FIELDS = (
    "id,doi,title,publication_year,cited_by_count,"
    "primary_location,authorships,concepts,abstract_inverted_index"
)


def _fetch_batch(dois: list[str], session: requests.Session) -> dict[str, dict]:
    """Query OpenAlex for up to BATCH_SIZE DOIs; returns {doi_lower: work_dict}."""
    filter_val = "|".join(f"https://doi.org/{d}" for d in dois)
    params = {
        "filter": f"doi:{filter_val}",
        "select": _SELECT_FIELDS,
        "per-page": len(dois),
        "mailto": OPENALEX_EMAIL,
    }
    try:
        r = session.get(f"{OPENALEX_BASE}/works", params=params, timeout=30)
        r.raise_for_status()
        results = r.json().get("results", [])
        out: dict[str, dict] = {}
        for work in results:
            raw_doi = work.get("doi", "")
            if raw_doi:
                doi_clean = raw_doi.replace("https://doi.org/", "").lower().strip()
                out[doi_clean] = work
        return out
    except Exception as exc:
        log.warning("OpenAlex batch error: %s", exc)
        return {}


# ─── metadata extraction ──────────────────────────────────────────────────────

def _extract_journal(work: dict) -> str:
    loc = work.get("primary_location") or {}
    src = loc.get("source") or {}
    return src.get("display_name") or ""


def _extract_authors(work: dict) -> str:
    auths = work.get("authorships") or []
    names = []
    for a in auths[:5]:
        author = a.get("author") or {}
        n = author.get("display_name", "")
        if n:
            names.append(n)
    suffix = " et al." if len(auths) > 5 else ""
    return "; ".join(names) + suffix


def _extract_concepts(work: dict) -> str:
    concepts = work.get("concepts") or []
    top = [c.get("display_name", "") for c in concepts[:6] if c.get("display_name")]
    return "; ".join(top)


def _reconstruct_abstract(work: dict) -> str:
    inv = work.get("abstract_inverted_index")
    if not inv:
        return ""
    index: dict[str, list[int]] = {}
    for word, positions in inv.items():
        for pos in positions:
            index[pos] = word
    words = [index[k] for k in sorted(index)]
    return " ".join(words)[:400]


def _categorize(title: str | None, concepts: str) -> str:
    text = ((title or "") + " " + concepts).lower()
    for cat, keywords in CATEGORY_RULES:
        if any(kw in text for kw in keywords):
            return cat
    return "General EO"


# ─── sci-hub downloader ───────────────────────────────────────────────────────

def scihub_download(doi: str, output_dir: Path, session: requests.Session | None = None) -> Path | None:
    """
    Attempt to fetch a PDF from sci-hub.ru/{doi} and save it locally.

    Returns the saved Path on success, None on failure.

    Note: Sci-Hub availability varies by region and network.  Only use this
    function for papers you are legally entitled to access in your jurisdiction.
    """
    if session is None:
        session = requests.Session()
        session.headers.update({"User-Agent": "Mozilla/5.0 (X11; Linux x86_64)"})

    output_dir.mkdir(parents=True, exist_ok=True)

    # sanitise DOI for filename
    safe_name = re.sub(r"[^\w\-.]", "_", doi) + ".pdf"
    out_path = output_dir / safe_name
    if out_path.exists():
        log.debug("Already downloaded: %s", out_path)
        return out_path

    scihub_url = f"https://sci-hub.ru/{doi}"
    try:
        page_resp = session.get(scihub_url, timeout=30)
        page_resp.raise_for_status()
    except Exception as exc:
        log.warning("Sci-Hub page fetch failed for %s: %s", doi, exc)
        return None

    # find the PDF link in the returned HTML
    html = page_resp.text
    pdf_url: str | None = None

    # Sci-hub embeds the path in several places; try in priority order
    for pattern in [
        # <meta name="citation_pdf_url" content="/storage/...">
        r'<meta\s+name=["\']citation_pdf_url["\']\s+content=["\']([^"\']+)["\']',
        r'content=["\']([^"\']+\.pdf[^"\']*)["\'][^>]+name=["\']citation_pdf_url["\']',
        # <div class="download"><a href="...">
        r'class=["\']download["\'][^>]*>\s*<a\s+href=["\']([^"\']+)["\']',
        # <object data="...">
        r'<object[^>]+data=["\']([^"\'#]+\.pdf)',
        r'<iframe[^>]+src=["\']([^"\']+\.pdf[^"\']*)["\']',
        r'<embed[^>]+src=["\']([^"\']+\.pdf[^"\']*)["\']',
        r'"(https?://[^"\']+\.pdf[^"\']*)"',
        r"'(https?://[^'\"]+\.pdf[^'\"]*)'",
    ]:
        m = re.search(pattern, html, re.IGNORECASE)
        if m:
            pdf_url = m.group(1).split("#")[0]
            if pdf_url.startswith("//"):
                pdf_url = "https:" + pdf_url
            elif pdf_url.startswith("/"):
                pdf_url = "https://sci-hub.ru" + pdf_url
            break

    if not pdf_url:
        log.warning("No PDF link found on sci-hub page for %s", doi)
        return None

    try:
        pdf_resp = session.get(pdf_url, timeout=60, stream=True)
        pdf_resp.raise_for_status()
        with open(out_path, "wb") as f:
            for chunk in pdf_resp.iter_content(chunk_size=65536):
                f.write(chunk)
        log.info("Downloaded: %s → %s", doi, out_path.name)
        return out_path
    except Exception as exc:
        log.warning("PDF download failed for %s: %s", doi, exc)
        return None


# ─── main pipeline ────────────────────────────────────────────────────────────

def run(limit: int = 0, min_citations: int = 10, dry_run: bool = False) -> None:
    log.info("Loading parquet files…")
    refs   = pd.read_parquet(DATA_PARQUET / "references.parquet")
    papers = pd.read_parquet(DATA_PARQUET / "papers.parquet")

    known_dois: set[str] = set(papers["doi"].dropna().str.lower().str.strip())
    log.info("Known DOIs in corpus: %d", len(known_dois))

    # referenced DOI frequency
    ref_dois_series = (
        refs["referenced_doi"].dropna().str.lower().str.strip()
    )
    ref_dois_series = ref_dois_series[ref_dois_series != ""]

    freq = ref_dois_series.value_counts().reset_index()
    freq.columns = ["doi", "referenced_count"]

    # first-seen paper for each referenced DOI
    first_seen: dict[str, str] = {}
    for row in refs[refs["referenced_doi"].notna()].itertuples():
        doi_low = str(row.referenced_doi).lower().strip()
        if doi_low not in first_seen:
            first_seen[doi_low] = str(row.source_paper_id)

    missing_freq = freq[~freq["doi"].isin(known_dois)].copy()
    log.info("Unique missing DOIs: %d", len(missing_freq))

    if limit:
        missing_freq = missing_freq.head(limit)
        log.info("Limiting to top %d by frequency", limit)

    if dry_run:
        log.info("[dry-run] Skipping API calls.")
        print(missing_freq.head(30).to_string())
        return

    # ── OpenAlex lookup ──────────────────────────────────────────────────────

    conn    = _open_cache()
    session = requests.Session()
    session.headers.update({"User-Agent": f"KnowledgGraf/1.0 ({OPENALEX_EMAIL})"})

    all_dois = missing_freq["doi"].tolist()
    uncached = [d for d in all_dois if _cache_get(conn, d) is None]
    log.info("DOIs to fetch from OpenAlex: %d  (cache hits: %d)", len(uncached), len(all_dois) - len(uncached))

    batches = [uncached[i : i + BATCH_SIZE] for i in range(0, len(uncached), BATCH_SIZE)]
    for batch_idx, batch in enumerate(batches):
        results = _fetch_batch(batch, session)
        for doi in batch:
            _cache_put(conn, doi, results.get(doi.lower()))
        if (batch_idx + 1) % 10 == 0:
            log.info("  Fetched batch %d/%d", batch_idx + 1, len(batches))
        time.sleep(0.12)   # ~8 req/s — polite to OpenAlex

    log.info("OpenAlex fetch complete. Building result table…")

    # ── assemble result rows ─────────────────────────────────────────────────

    rows: list[dict] = []
    for doi in all_dois:
        work = _cache_get(conn, doi)
        freq_count = int(missing_freq.loc[missing_freq["doi"] == doi, "referenced_count"].iloc[0])

        if work:
            cited = work.get("cited_by_count") or 0
            if cited < min_citations:
                continue
            journal   = _extract_journal(work)
            authors   = _extract_authors(work)
            concepts  = _extract_concepts(work)
            abstract  = _reconstruct_abstract(work)
            category  = _categorize(work.get("title", ""), concepts)
            landmark  = LANDMARK_DOIS.get(doi.lower(), "")
            rows.append({
                "doi":                   doi,
                "title":                 work.get("title", ""),
                "cited_by_count":        cited,
                "referenced_count":      freq_count,
                "year":                  work.get("publication_year"),
                "journal":               journal,
                "authors":               authors,
                "in_openalex":           True,
                "missing_from_local_corpus": True,
                "likely_landmark":       landmark,
                "category":              category,
                "concepts":              concepts,
                "abstract_snippet":      abstract,
                "first_seen_reference":  first_seen.get(doi, ""),
            })
        else:
            # not found in OpenAlex — still log if highly freq referenced
            if freq_count >= 5:
                rows.append({
                    "doi":                   doi,
                    "title":                 "",
                    "cited_by_count":        0,
                    "referenced_count":      freq_count,
                    "year":                  None,
                    "journal":               "",
                    "authors":               "",
                    "in_openalex":           False,
                    "missing_from_local_corpus": True,
                    "likely_landmark":       LANDMARK_DOIS.get(doi.lower(), ""),
                    "category":              "Unknown",
                    "concepts":              "",
                    "abstract_snippet":      "",
                    "first_seen_reference":  first_seen.get(doi, ""),
                })

    conn.close()
    log.info("Result rows after citation filter (>=%d): %d", min_citations, len(rows))

    df = pd.DataFrame(rows)
    if df.empty:
        log.warning("No results — try lowering --min-citations")
        return

    df.sort_values("cited_by_count", ascending=False, inplace=True)
    df.reset_index(drop=True, inplace=True)

    # ── save CSV ─────────────────────────────────────────────────────────────
    df.to_csv(OUTPUT_CSV, index=False)
    log.info("Saved CSV → %s  (%d rows)", OUTPUT_CSV, len(df))

    # ── save JSON summary ─────────────────────────────────────────────────────
    top_by_citations  = df.head(50)[["doi","title","cited_by_count","year","category","likely_landmark"]].to_dict("records")
    top_by_freq       = df.sort_values("referenced_count", ascending=False).head(50)[
        ["doi","title","referenced_count","cited_by_count","year","category"]
    ].to_dict("records")
    landmark_rows     = df[df["likely_landmark"] != ""].to_dict("records")
    category_counts   = df.groupby("category")["doi"].count().to_dict()

    summary = {
        "total_missing_dois":           int(missing_freq.shape[0]),
        "missing_dois_with_openalex":   int(df["in_openalex"].sum()),
        "highly_cited_missing":         int((df["cited_by_count"] >= min_citations).sum()),
        "min_citations_threshold":      min_citations,
        "top_by_citations":             top_by_citations,
        "top_by_corpus_frequency":      top_by_freq,
        "landmark_papers_missing":      landmark_rows,
        "category_distribution":        category_counts,
    }
    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False, default=str)
    log.info("Saved JSON → %s", OUTPUT_JSON)

    # ── generate Markdown report ──────────────────────────────────────────────
    _write_markdown(df, summary, min_citations)
    log.info("Saved Markdown → %s", OUTPUT_MD)
    log.info("Pipeline complete.")


def _write_markdown(df: pd.DataFrame, summary: dict, min_citations: int) -> None:
    lines: list[str] = []
    a = lines.append

    a("# Missing Reference Recovery — Citation-Impact Analysis")
    a("")
    a(f"*Generated: {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M')}*")
    a("")
    a("---")
    a("")
    a("## Executive Summary")
    a("")
    a(f"| Metric | Value |")
    a(f"|--------|-------|")
    a(f"| Total unique referenced DOIs | {summary['total_missing_dois']:,} |")
    a(f"| Found in OpenAlex | {summary['missing_dois_with_openalex']:,} |")
    a(f"| Highly cited (≥{min_citations}) | {summary['highly_cited_missing']:,} |")
    a(f"| Landmark papers identified | {len(summary['landmark_papers_missing'])} |")
    a("")
    a("---")
    a("")

    # ── top 20 by citations ───────────────────────────────────────────────────
    a("## Top 20 Missing Papers by Citations")
    a("")
    a("| # | DOI | Title | Cited | Year | Category |")
    a("|---|-----|-------|-------|------|----------|")
    top20 = df.head(20)
    for i, row in enumerate(top20.itertuples(), 1):
        title  = str(row.title)[:60].replace("|", "/")
        doi    = row.doi
        cited  = int(row.cited_by_count)
        year   = int(row.year) if pd.notna(row.year) else "?"
        cat    = row.category
        a(f"| {i} | `{doi}` | {title}… | {cited:,} | {year} | {cat} |")
    a("")

    # ── top 20 by corpus frequency ────────────────────────────────────────────
    a("## Top 20 by Reference Frequency in Corpus")
    a("")
    a("| # | DOI | Title | Refs | Cited | Year |")
    a("|---|-----|-------|------|-------|------|")
    by_freq = df.sort_values("referenced_count", ascending=False).head(20)
    for i, row in enumerate(by_freq.itertuples(), 1):
        title = str(row.title)[:55].replace("|", "/")
        doi   = row.doi
        refs  = int(row.referenced_count)
        cited = int(row.cited_by_count)
        year  = int(row.year) if pd.notna(row.year) else "?"
        a(f"| {i} | `{doi}` | {title}… | {refs} | {cited:,} | {year} |")
    a("")

    # ── landmark papers ───────────────────────────────────────────────────────
    a("## Flood-Science Landmark Papers (Missing from Corpus)")
    a("")
    landmarks = df[df["likely_landmark"] != ""]
    if landmarks.empty:
        a("*No confirmed landmark papers found in the missing set.*")
    else:
        a("| DOI | Label | Cited | Year | In OpenAlex |")
        a("|-----|-------|-------|------|-------------|")
        for row in landmarks.itertuples():
            cited = int(row.cited_by_count)
            year  = int(row.year) if pd.notna(row.year) else "?"
            a(f"| `{row.doi}` | {row.likely_landmark} | {cited:,} | {year} | {'✓' if row.in_openalex else '✗'} |")
    a("")

    # ── category distribution ─────────────────────────────────────────────────
    a("## Category Distribution")
    a("")
    a("| Category | Count |")
    a("|----------|-------|")
    for cat, cnt in sorted(summary["category_distribution"].items(), key=lambda x: -x[1]):
        a(f"| {cat} | {cnt} |")
    a("")

    # ── analysis ──────────────────────────────────────────────────────────────
    a("## Possible Causes of Absence")
    a("")
    a("1. **Corpus filtering** — search queries used narrow flood/water keywords; foundational")
    a("   methods papers (Nash-Sutcliffe, SCS curve number) were excluded even though every")
    a("   hydrological paper cites them.")
    a("")
    a("2. **Title-keyword recall limits** — journal-level exclusions (e.g. Nature Climate Change,")
    a("   JGR-Atmospheres) removed high-impact papers outside the direct flood-mapping domain.")
    a("")
    a("3. **Missing ingestion** — some papers have DOIs in references.parquet but their PDFs")
    a("   were never downloaded; GROBID therefore never processed them.")
    a("")
    a("4. **Topic ontology gaps** — the current ontology focuses on flood mapping / SAR /")
    a("   optical remote sensing; papers primarily about climate, hydrology theory, or general")
    a("   EO infrastructure are under-represented.")
    a("")
    a("5. **Paywalled or retracted papers** — a small fraction may be inaccessible via open")
    a("   channels; absent from GROBID output and therefore from the corpus.")
    a("")
    a("---")
    a("")
    a("## Recommended Next Steps")
    a("")
    a("- **Re-ingest top missing papers** via GROBID after downloading PDFs")
    a("- **Expand ontology** to cover foundational hydrology metrics")
    a("- **Add indirect citations** as `[:CITES]` edges in Neo4j even without full paper ingestion")
    a("- **Sci-Hub download utility** is available in `src/analytics/missing_reference_recovery.py`")
    a("  via `scihub_download(doi, output_dir)` — use for papers legally accessible to you")
    a("")
    a("---")
    a("")
    a("*Analysis produced by `src/analytics/missing_reference_recovery.py`*")

    with open(OUTPUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


# ─── CLI ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit",         type=int, default=0,  help="top-N missing DOIs by frequency (0 = all)")
    parser.add_argument("--min-citations", type=int, default=10, help="minimum cited_by_count (default 10)")
    parser.add_argument("--dry-run",       action="store_true",  help="skip API, print stats only")
    args = parser.parse_args()

    run(limit=args.limit, min_citations=args.min_citations, dry_run=args.dry_run)
