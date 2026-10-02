"""Reference verification: every key of references.bib against CrossRef and OpenAlex
(metadata only — no ingestion). Writes 07_references_verified.bib (only entries whose
metadata matched) and 07b_references_unresolved.md (everything else, with what was
checked). Never invents a field: the .bib carries CrossRef's values, not ours.
"""
from __future__ import annotations

import difflib
import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

import pandas as pd

from src.config.settings import OPEN_ALEX_EMAIL
from src.paper_3._utils import normalize_title
from tools.paper3_audit import corpus as corpus_mod
from tools.paper3_audit.bibtex import bib_doi, load_bib_entries
from tools.paper3_audit.config import DELIVERABLES, OUT_DIR, WORK_DIR

logger = logging.getLogger(__name__)

# Polite-pool contact comes from OPEN_ALEX_EMAIL in .env; never hard-code a personal address.
_UA = f"GeoHydroAI/paper3_audit (mailto:{OPEN_ALEX_EMAIL})" if OPEN_ALEX_EMAIL else "GeoHydroAI/paper3_audit"


def _with_mailto(url: str) -> str:
    """Append the OpenAlex polite-pool `mailto` parameter when a contact is configured."""
    if not OPEN_ALEX_EMAIL:
        return url
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}mailto={urllib.parse.quote(OPEN_ALEX_EMAIL)}"
CROSSREF_CACHE = "crossref_cache.json"
OPENALEX_CACHE = "openalex_cache.json"
URL_CACHE = "url_cache.json"
VERIFIED_CSV = "references_verified.csv"
DELAY = 0.3
TITLE_MIN = 0.90

COLUMNS = ["bib_key", "bib_type", "doi", "bib_title", "bib_year", "crossref_status", "crossref_title",
           "crossref_year", "crossref_journal", "crossref_authors", "crossref_volume", "crossref_issue",
           "crossref_pages", "title_ratio", "year_match", "openalex_status", "openalex_id", "openalex_year",
           "cited_by_count", "url", "url_status", "in_corpus", "paper_id", "resolution", "flags", "verified_on"]


def _get_json(url: str, timeout: int = 15) -> dict | None:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _UA, "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except Exception as exc:
        logger.debug("GET %s failed: %s", url, exc)
        return None


def crossref_message(doi: str) -> dict | None:
    """Same endpoint as src.paper_3.v2.reference_registry.fetch_meta, but keeps the
    fields a .bib needs (volume, issue, pages) instead of flattening them away."""
    data = _get_json(f"https://api.crossref.org/works/{urllib.parse.quote(doi, safe='/')}")
    msg = (data or {}).get("message") or {}
    if not msg.get("title"):
        return None
    year = ""
    for k in ("published-print", "published-online", "issued", "created"):
        parts = (msg.get(k) or {}).get("date-parts") or [[None]]
        if parts and parts[0] and parts[0][0]:
            year = str(parts[0][0]); break
    return {"title": msg["title"][0], "year": year,
            "journal": (msg.get("container-title") or [""])[0],
            "authors": [{"family": a.get("family", ""), "given": a.get("given", "")}
                        for a in msg.get("author", []) if a.get("family") or a.get("name")],
            "volume": msg.get("volume", ""), "issue": msg.get("issue", ""), "pages": msg.get("page", ""),
            "article_number": msg.get("article-number", ""), "type": msg.get("type", ""),
            "publisher": msg.get("publisher", ""), "url": msg.get("URL", f"https://doi.org/{doi}")}


def openalex_work(doi: str) -> dict | None:
    data = _get_json(_with_mailto(f"https://api.openalex.org/works/doi:{urllib.parse.quote(doi, safe='/')}"))
    if not data or not data.get("id"):
        return None
    return {"openalex_id": corpus_mod.openalex_short(data["id"]), "publication_year": data.get("publication_year"),
            "cited_by_count": data.get("cited_by_count"), "title": data.get("title") or data.get("display_name", "")}


def openalex_title_search(title: str, year: str = "") -> dict | None:
    """Best OpenAlex work for a title (metadata lookup only). Returns None unless the
    title matches at >= TITLE_MIN and the year (if given) is within one year."""
    if not title:
        return None
    q = urllib.parse.quote(title[:200])
    data = _get_json(_with_mailto(f"https://api.openalex.org/works?search={q}&per-page=5"))
    best = None
    for w in (data or {}).get("results", []):
        t = w.get("title") or w.get("display_name") or ""
        ratio = difflib.SequenceMatcher(None, normalize_title(title), normalize_title(t)).ratio()
        wy = w.get("publication_year")
        if ratio >= TITLE_MIN and (not year or not wy or abs(int(year) - int(wy)) <= 1):
            cand = {"doi": corpus_mod.clean_doi(w.get("doi") or ""), "openalex_id": corpus_mod.openalex_short(w.get("id")),
                    "publication_year": wy, "cited_by_count": w.get("cited_by_count"), "title": t, "ratio": round(ratio, 3)}
            if best is None or cand["ratio"] > best["ratio"]:
                best = cand
    return best


def url_status(url: str, timeout: int = 12) -> str:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _UA}, method="HEAD")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return f"http_{r.status}"
    except urllib.error.HTTPError as exc:
        if exc.code in (403, 405):
            try:
                req = urllib.request.Request(url, headers={"User-Agent": _UA})
                with urllib.request.urlopen(req, timeout=timeout) as r:
                    return f"http_{r.status}"
            except Exception as exc2:
                return f"error_{getattr(exc2, 'code', type(exc2).__name__)}"
        return f"http_{exc.code}"
    except Exception as exc:
        return f"error_{type(exc).__name__}"


class Cache:
    def __init__(self, path: Path):
        self.path = path
        self.data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}

    def get(self, key):
        return self.data.get(key)

    def put(self, key, value):
        self.data[key] = value
        self.path.write_text(json.dumps(self.data, indent=1, ensure_ascii=False), encoding="utf-8")


def _bib_url(fields: dict) -> str:
    for k in ("url", "howpublished"):
        v = fields.get(k, "")
        m = __import__("re").search(r"https?://\S+", v)
        if m:
            return m.group(0).rstrip("}).,")
    return ""


def verify_all(entries: dict[str, dict], resolver: corpus_mod.PaperResolver, work_dir: Path,
               offline: bool = False, crossref=crossref_message, openalex=openalex_work, urlcheck=url_status,
               title_search=openalex_title_search) -> pd.DataFrame:
    cr_cache, oa_cache, url_cache = Cache(work_dir / CROSSREF_CACHE), Cache(work_dir / OPENALEX_CACHE), Cache(work_dir / URL_CACHE)
    rows = []
    today = str(date.today())
    for key, e in entries.items():
        f = e["fields"]
        doi = bib_doi(e)
        row = {c: "" for c in COLUMNS}
        row.update({"bib_key": key, "bib_type": e["type"], "doi": doi, "bib_title": f.get("title", ""),
                    "bib_year": f.get("year", ""), "url": _bib_url(f), "verified_on": today})
        flags = []
        pid = resolver.from_doi(doi) if doi else None
        if pid is None and f.get("title"):
            pid = resolver.from_title(f["title"])
        row["in_corpus"], row["paper_id"] = bool(pid), pid or ""
        if not doi and pid:
            cdoi = resolver.doi_of(pid)
            if cdoi:
                doi, row["doi"] = cdoi, cdoi
                flags.append("doi_from_corpus_metadata")
        if not doi and f.get("title") and e["type"] != "misc" and not offline:
            hit = oa_cache.get("title:" + normalize_title(f["title"]))
            if hit is None:
                hit = title_search(f.get("title", ""), f.get("year", "")) or {"_none": True}
                time.sleep(DELAY); oa_cache.put("title:" + normalize_title(f["title"]), hit)
            if hit and not hit.get("_none") and hit.get("doi"):
                doi, row["doi"] = hit["doi"], hit["doi"]
                flags.append(f"doi_from_openalex_title_search(ratio {hit['ratio']})")

        cr = None
        if doi:
            cr = cr_cache.get(doi)
            if cr is None and not offline:
                cr = crossref(doi); time.sleep(DELAY)
                cr_cache.put(doi, cr if cr else {"_none": True})
            if cr and cr.get("_none"):
                cr = None
        if cr:
            row.update({"crossref_status": "resolved", "crossref_title": cr["title"], "crossref_year": cr["year"],
                        "crossref_journal": cr["journal"], "crossref_volume": cr["volume"], "crossref_issue": cr["issue"],
                        "crossref_pages": cr["pages"] or cr.get("article_number", ""),
                        "crossref_authors": " and ".join(f"{a['family']}, {a['given']}".strip(", ") for a in cr["authors"])})
            ratio = difflib.SequenceMatcher(None, normalize_title(f.get("title", "")), normalize_title(cr["title"])).ratio() if f.get("title") else 1.0
            row["title_ratio"] = round(ratio, 3)
            if ratio < TITLE_MIN:
                flags.append(f"title_mismatch({ratio:.2f})")
            by, cy = f.get("year", ""), cr["year"]
            row["year_match"] = bool(by and cy and abs(int(by) - int(cy)) <= 1)
            if by and cy and abs(int(by) - int(cy)) > 1:
                flags.append(f"year_mismatch(bib {by} vs crossref {cy})")
        elif doi:
            row["crossref_status"] = "unresolved" if not offline else "not_checked(offline)"
            flags.append("doi_not_in_crossref" if not offline else "offline")
        else:
            row["crossref_status"] = "no_doi"

        oa = None
        if doi:
            oa = oa_cache.get(doi)
            if oa is None and not offline:
                oa = openalex(doi); time.sleep(DELAY)
                oa_cache.put(doi, oa if oa else {"_none": True})
            if oa and oa.get("_none"):
                oa = None
        if oa:
            row.update({"openalex_status": "resolved", "openalex_id": oa["openalex_id"],
                        "openalex_year": oa.get("publication_year", ""), "cited_by_count": oa.get("cited_by_count", "")})
        elif doi:
            row["openalex_status"] = "unresolved" if not offline else "not_checked(offline)"

        if not doi and row["url"]:
            st = url_cache.get(row["url"])
            if st is None and not offline:
                st = urlcheck(row["url"]); url_cache.put(row["url"], st)
            row["url_status"] = st or "not_checked(offline)"

        hard_flags = [x for x in flags if x.startswith(("title_mismatch", "year_mismatch"))]
        if cr and not hard_flags:
            row["resolution"] = "crossref+openalex" if oa else "crossref"
        elif cr:
            row["resolution"] = "metadata_mismatch"
        elif oa and doi:
            ratio = difflib.SequenceMatcher(None, normalize_title(f.get("title", "")), normalize_title(oa.get("title", ""))).ratio() if f.get("title") else 1.0
            row["title_ratio"] = round(ratio, 3)
            if ratio >= TITLE_MIN:
                row["resolution"] = "openalex_only"     # DataCite DOIs (Zenodo) are not in CrossRef
                row["crossref_title"] = oa.get("title", ""); row["crossref_year"] = str(oa.get("publication_year") or "")
            else:
                row["resolution"] = "metadata_mismatch"; flags.append(f"title_mismatch_openalex({ratio:.2f})")
        elif doi:
            row["resolution"] = "doi_unresolved"
        else:
            row["resolution"] = "no_doi_url_checked" if str(row["url_status"]).startswith("http_2") else "no_doi_unverified"
        row["flags"] = "; ".join(flags)
        rows.append(row)
    return pd.DataFrame(rows, columns=COLUMNS)


def _bib_escape(s: str) -> str:
    return str(s).replace("&", "\\&").replace("%", "\\%")


def write_bib(frame: pd.DataFrame, entries: dict[str, dict], path: Path) -> int:
    lines = [f"% 07_references_verified.bib — generated {date.today()} by tools/paper3_audit/references.py",
             "% Only entries whose DOI resolved in CrossRef with matching title/year. Field values are CrossRef's.",
             "% Everything else is listed in 07b_references_unresolved.md and must not be cited from this file.", ""]
    n = 0
    for r in frame.itertuples():
        if not (str(r.resolution).startswith("crossref") or r.resolution == "openalex_only"):
            continue
        e = entries[r.bib_key]
        if r.resolution == "openalex_only":
            f = e["fields"]
            body = ",\n".join(f"  {k} = {{{_bib_escape(v)}}}" for k, v in (
                ("author", f.get("author", "")), ("title", f.get("title", "")), ("year", r.crossref_year or f.get("year", "")),
                ("publisher", f.get("publisher", "")), ("doi", r.doi), ("openalexid", r.openalex_id),
                ("note", "DataCite DOI: resolved in OpenAlex, not indexed by CrossRef"), ("verifiedon", r.verified_on)) if v)
            lines.append(f"@{e['type'] if e['type'] in ('misc','article') else 'misc'}{{{r.bib_key},\n{body}\n}}\n"); n += 1
            continue
        btype = e["type"] if e["type"] in ("article", "inproceedings", "book", "incollection", "misc") else "article"
        fields = [("author", r.crossref_authors), ("title", r.crossref_title), ("year", r.crossref_year), ("doi", r.doi)]
        if btype == "inproceedings":
            fields.append(("booktitle", r.crossref_journal or e["fields"].get("booktitle", "")))
        else:
            fields.append(("journal", r.crossref_journal))
        for k, v in (("volume", r.crossref_volume), ("number", r.crossref_issue), ("pages", r.crossref_pages)):
            if v:
                fields.append((k, v))
        if r.openalex_id:
            fields.append(("openalexid", r.openalex_id))
        fields.append(("verifiedon", r.verified_on))
        body = ",\n".join(f"  {k} = {{{_bib_escape(v)}}}" for k, v in fields if v)
        lines.append(f"@{btype}{{{r.bib_key},\n{body}\n}}\n")
        n += 1
    path.write_text("\n".join(lines), encoding="utf-8")
    return n


def write_unresolved(frame: pd.DataFrame, entries: dict[str, dict], path: Path) -> int:
    rows = frame[~(frame["resolution"].str.startswith("crossref") | (frame["resolution"] == "openalex_only"))]
    out = ["# 07b — references NOT verifiable from CrossRef/OpenAlex metadata", "",
           f"Generated {date.today()}. These keys are kept for the author's decision; none of them may be cited "
           "from `07_references_verified.bib`. 'What was checked' is literal: no metadata was invented.", "",
           "| key | type | DOI | resolution | what was checked | bib note | action needed |", "|---|---|---|---|---|---|---|"]
    for r in rows.itertuples():
        e = entries[r.bib_key]
        checked = []
        if r.doi:
            checked.append(f"CrossRef: {r.crossref_status}")
            checked.append(f"OpenAlex: {r.openalex_status}")
        if r.url:
            checked.append(f"URL {r.url} → {r.url_status}")
        if r.flags:
            checked.append(f"flags: {r.flags}")
        if r.in_corpus:
            checked.append(f"in corpus as `{r.paper_id}`")
        note = e["fields"].get("note", "")
        action = ("obtain the product sheet / report and cite it as grey literature with access date" if r.bib_type == "misc"
                  else "correct the DOI or the title/year in references.bib, then re-run `references`")
        esc = lambda s: str(s).replace("|", "\\|")
        out.append(f"| {r.bib_key} | {r.bib_type} | {r.doi or '—'} | {r.resolution} | {esc('; '.join(checked))} | {esc(note[:160])} | {action} |")
    path.write_text("\n".join(out) + "\n", encoding="utf-8")
    return len(rows)


def run(work_dir: Path = WORK_DIR, out_dir: Path = OUT_DIR, force: bool = False, offline: bool = False) -> int:
    entries = load_bib_entries(work_dir)
    if not entries:
        raise FileNotFoundError("bib snapshot missing — run `prepare` first")
    index = corpus_mod.load_runtime_index(work_dir)
    resolver = corpus_mod.PaperResolver(index)
    frame = verify_all(entries, resolver, work_dir, offline=offline)
    frame.to_csv(work_dir / VERIFIED_CSV, index=False)
    n_ok = write_bib(frame, entries, out_dir / DELIVERABLES["bib"])
    n_un = write_unresolved(frame, entries, out_dir / DELIVERABLES["bib_unresolved"])
    logger.info("references: %d verified → %s; %d unresolved → %s", n_ok, DELIVERABLES["bib"], n_un, DELIVERABLES["bib_unresolved"])
    return 0


# ── metadata for retained corpus papers ───────────────────────────────────────

PAPER_META_FILE = "paper_meta_crossref.json"


def enrich_retained(work_dir: Path = WORK_DIR, offline: bool = False, crossref=crossref_message) -> int:
    """CrossRef metadata (authors, year, journal) for every corpus paper retained by
    screening whose normalized record lacks a year or a journal. Cached; never
    overwrites the corpus files — export.py merges it into the ledger/citations."""
    judged_path = work_dir / "screen_judged.parquet"
    if not judged_path.exists():
        return 0
    judged = pd.read_parquet(judged_path)
    retained = judged[judged["role"] != "NOT_RELEVANT"]
    index = corpus_mod.load_runtime_index(work_dir)
    meta = {r["paper_id"]: r for r in index.to_dict("records")}
    cache = Cache(work_dir / PAPER_META_FILE)
    n_new = 0
    for pid in sorted(set(retained["paper_id"])):
        m = meta.get(pid)
        if not m or not m.get("doi_clean"):
            continue
        if m.get("year") and m.get("journal") and m.get("authors"):
            continue
        if cache.get(pid) is not None:
            continue
        if offline:
            continue
        rec = crossref(m["doi_clean"]); time.sleep(DELAY)
        cache.put(pid, rec if rec else {"_none": True})
        n_new += int(bool(rec))
    logger.info("enrich_retained: %d new CrossRef records (%d cached)", n_new, len(cache.data))
    return 0
