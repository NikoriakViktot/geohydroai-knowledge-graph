"""Reference registry: every DOI the manuscript may cite, resolved before use.

Sources, in order of authority:

1. ``briefs/technical_sources.yaml`` — mission documents, standards, registries
   (no DOI; cite key = entry id; they render only when ``status: url_verified``).
2. Thesis positive controls (``theses.yaml``) — the papers retrieval must find.
3. SWOT-DNIPRO snapshot registers — ``literature_sources.csv`` (SRC-nn),
   ``p34_references.csv`` and ``p38_references.csv`` (S1 classifier study).
4. ``relations.parquet`` — papers adjudicated SUPPORTS / CONTRADICTS /
   METHOD_RELEVANT / ANALOGUE (never NOT_RELEVANT).

Every DOI goes through ``fetch_meta`` (CrossRef ``/works/{doi}``) and is marked
``resolved`` only when CrossRef returns a title. The assembler refuses to cite
an unresolved key. Fetching is injectable so tests never touch the network.
"""
from __future__ import annotations

import json
import logging
import re
import time
import unicodedata
import urllib.request
from pathlib import Path
from typing import Callable

import pandas as pd
import yaml

from src.paper_3 import snapshot
from src.paper_3._utils import OUT_DIR, normalize_doi
from src.paper_3.theses import load_theses
from src.paper_3.v2.references import TECHNICAL_PATH

logger = logging.getLogger(__name__)

REGISTRY_CSV = "REFERENCES.csv"
REGISTRY_MD = "REFERENCES.md"
CACHE_FILE = "crossref_cache.json"
COLUMNS = ["cite_key", "doi", "title", "authors", "year", "journal", "source",
           "resolved", "resolution_method", "url", "note"]
_UA = "GeoHydroAI/paper_3 (mailto:nikoriakviktor@gmail.com)"


def fetch_meta(doi: str, timeout: int = 10) -> dict | None:
    """CrossRef work metadata for one DOI, or None when it does not resolve."""
    try:
        req = urllib.request.Request(f"https://api.crossref.org/works/{doi}",
                                     headers={"User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            msg = json.loads(r.read()).get("message", {})
    except Exception as exc:  # 404, timeout, no network — all mean "unresolved"
        logger.debug("CrossRef failed for %s: %s", doi, exc)
        return None
    title = (msg.get("title") or [""])[0]
    if not title:
        return None
    authors = "; ".join(
        f"{a.get('family', '')}, {a.get('given', '')}".strip(", ")
        for a in msg.get("author", []) if a.get("family"))
    year = ""
    for k in ("published-print", "published-online", "issued", "created"):
        parts = (msg.get(k) or {}).get("date-parts") or [[None]]
        if parts and parts[0] and parts[0][0]:
            year = str(parts[0][0]); break
    return {"title": title, "authors": authors, "year": year,
            "journal": (msg.get("container-title") or [""])[0],
            "url": msg.get("URL", f"https://doi.org/{doi}")}


_CYR = str.maketrans({
    "а": "a", "б": "b", "в": "v", "г": "h", "ґ": "g", "д": "d", "е": "e", "є": "ie",
    "ж": "zh", "з": "z", "и": "y", "і": "i", "ї": "i", "й": "i", "к": "k", "л": "l",
    "м": "m", "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "shch", "ь": "",
    "ю": "iu", "я": "ia", "ы": "y", "э": "e", "ё": "e", "ъ": "",
})


_AUTHOR_SPLIT = re.compile(r",\s*(?=[A-ZА-ЯІЇЄҐ][^\s,;]+\s+[A-ZА-ЯІЇЄҐ]\.)")


def split_authors(raw: str) -> list[str]:
    """'Arcement G.J., Schneider V.R.' / 'A & B' / 'Family, Given; …' → one string per author."""
    if not raw:
        return []
    text = raw.replace(" & ", "; ").replace(" and ", "; ")
    parts: list[str] = []
    for chunk in text.split(";"):
        parts.extend(p.strip() for p in _AUTHOR_SPLIT.split(chunk) if p.strip())
    return parts


def first_family(authors: str) -> str:
    """Family name of the first author, whatever the list format."""
    parts = split_authors(authors)
    if not parts:
        return ""
    first = parts[0]
    return (first.split(",")[0] if "," in first else first).split()[0].strip()


def cite_key(authors: str, year: str, doi: str) -> str:
    """``family2024`` (ASCII, lower-case, Cyrillic transliterated); else a DOI slug."""
    family = first_family(authors)
    family = family.lower().translate(_CYR)
    family = unicodedata.normalize("NFKD", family).encode("ascii", "ignore").decode()
    family = re.sub(r"[^a-z]", "", family.lower())
    if family and year:
        return f"{family}{year}"
    return "doi_" + re.sub(r"[^a-z0-9]", "_", doi)


# ── collectors (pure: they read files, never the network) ────────────────────

def from_technical_sources(path: Path = TECHNICAL_PATH) -> list[dict]:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or []
    out = []
    for e in raw:
        out.append({"cite_key": e["id"], "doi": "", "title": e.get("title", ""),
                    "authors": e.get("issuer", ""), "year": "", "journal": e.get("identifier", ""),
                    "source": "technical", "resolved": e.get("status") == "url_verified",
                    "resolution_method": e.get("status", "url_needed"),
                    "url": e.get("url", "") or "", "note": e.get("formal_citation", "")})
    return out


def from_theses() -> list[tuple[str, str]]:
    """(doi, note) for every positive control."""
    out = []
    for t in load_theses():
        for c in t.positive_controls:
            out.append((normalize_doi(c.doi), f"control for {t.id} ({c.role}, {c.relevance}): {c.note}"))
    return out


def from_snapshot(snapshot_dir: Path = snapshot.SNAPSHOT_DIR) -> list[tuple[str, str]]:
    out = []
    try:
        src = pd.read_csv(snapshot.resolve("literature_sources.csv", snapshot_dir), dtype=str, keep_default_na=False)
        for r in src.itertuples():
            doi = normalize_doi(r.doi_or_url)
            if doi.startswith("10."):
                out.append((doi, f"{r.source_id}: {r.title[:80]}"))
    except FileNotFoundError:
        pass
    for name in ("p34_references.csv", "p38_references.csv"):
        try:
            ref = pd.read_csv(snapshot.resolve(name, snapshot_dir), dtype=str, keep_default_na=False)
        except FileNotFoundError:
            continue
        for r in ref.itertuples():
            doi = normalize_doi(r.doi)
            if doi.startswith("10."):
                out.append((doi, f"{name}: {r.key}"))
    return out


def hints_from_snapshot(snapshot_dir: Path = snapshot.SNAPSHOT_DIR) -> dict[str, dict]:
    """authors/year the audit register recorded, for DOIs CrossRef returns bare
    (USGS reports, Ukrainian journals with Cyrillic-only metadata)."""
    out: dict[str, dict] = {}
    try:
        src = pd.read_csv(snapshot.resolve("literature_sources.csv", snapshot_dir), dtype=str, keep_default_na=False)
    except FileNotFoundError:
        return out
    for r in src.itertuples():
        doi = normalize_doi(r.doi_or_url)
        if doi.startswith("10.") and r.authors_or_org:
            out[doi] = {"authors": "; ".join(split_authors(r.authors_or_org)), "year": r.year}
    return out


def from_relations(out_dir: Path = OUT_DIR) -> list[tuple[str, str]]:
    p = out_dir / "relations.parquet"
    if not p.exists():
        return []
    rel = pd.read_parquet(p)
    rel = rel[rel.relation != "NOT_RELEVANT"]
    out = []
    for doi, g in rel.groupby("doi"):
        doi = normalize_doi(doi)
        if doi.startswith("10."):
            out.append((doi, "relations: " + ",".join(sorted({f"{r.thesis_id}:{r.relation}" for r in g.itertuples()}))))
    return out


# ── registry ─────────────────────────────────────────────────────────────────

def build(dois: list[tuple[str, str]], technical: list[dict],
          fetch: Callable[[str], dict | None] = fetch_meta,
          cache: dict | None = None, delay: float = 0.2,
          hints: dict[str, dict] | None = None) -> pd.DataFrame:
    """Resolve every DOI once (cache keyed by DOI); technical sources pass through.
    ``hints`` fill authors/year when CrossRef returns none."""
    cache = cache if cache is not None else {}
    hints = hints or {}
    rows: list[dict] = list(technical)
    seen: dict[str, dict] = {}
    for doi, note in dois:
        if doi in seen:
            if note and note not in seen[doi]["note"]:
                seen[doi]["note"] += " | " + note
            continue
        meta = cache.get(doi)
        if meta is None and doi not in cache:
            meta = fetch(doi)
            cache[doi] = meta
            if delay:
                time.sleep(delay)
        row = {"cite_key": "", "doi": doi, "title": "", "authors": "", "year": "",
               "journal": "", "source": "doi", "resolved": False,
               "resolution_method": "crossref_unresolved", "url": f"https://doi.org/{doi}",
               "note": note}
        if meta:
            row.update(meta)
            row["resolved"] = True
            row["resolution_method"] = "crossref"
        h = hints.get(doi, {})
        if not row["authors"] and h.get("authors"):
            row["authors"] = h["authors"]
            row["resolution_method"] += "+register_authors"
        if not row["year"] and h.get("year"):
            row["year"] = h["year"]
        row["cite_key"] = cite_key(row["authors"], row["year"], doi)
        seen[doi] = row
        rows.append(row)
    frame = pd.DataFrame(rows, columns=COLUMNS)
    # disambiguate colliding keys (smith2024, smith2024b, …)
    counts: dict[str, int] = {}
    keys = []
    for k in frame.cite_key:
        n = counts.get(k, 0)
        keys.append(k if n == 0 else f"{k}{'abcdefghij'[min(n, 9)]}")
        counts[k] = n + 1
    frame["cite_key"] = keys
    return frame


def render_md(frame: pd.DataFrame) -> str:
    lines = ["# References — resolved registry", "",
             f"{int(frame.resolved.sum())} of {len(frame)} entries resolved. "
             "Unresolved entries may not be cited; they render as [PENDING REF…] markers.", ""]
    for r in frame.sort_values(["source", "cite_key"]).itertuples():
        flag = "" if r.resolved else " **[UNRESOLVED]**"
        if r.source == "technical":
            lines.append(f"- `{r.cite_key}`{flag} — {r.note}")
        else:
            lines.append(f"- `{r.cite_key}`{flag} — {r.authors[:60]} ({r.year}). {r.title}. *{r.journal}*. "
                         f"[doi:{r.doi}](https://doi.org/{r.doi})")
    return "\n".join(lines) + "\n"


def load_registry(out_dir: Path = OUT_DIR) -> pd.DataFrame:
    return pd.read_csv(out_dir / REGISTRY_CSV, dtype=str, keep_default_na=False)


def run(out_dir: Path = OUT_DIR, snapshot_dir: Path = snapshot.SNAPSHOT_DIR,
        fetch: Callable[[str], dict | None] = fetch_meta) -> Path:
    cache_path = out_dir / CACHE_FILE
    cache = json.loads(cache_path.read_text()) if cache_path.exists() else {}
    dois = from_theses() + from_snapshot(snapshot_dir) + from_relations(out_dir)
    frame = build(dois, from_technical_sources(), fetch=fetch, cache=cache,
                  hints=hints_from_snapshot(snapshot_dir))
    cache_path.write_text(json.dumps(cache, indent=1, ensure_ascii=False), encoding="utf-8")
    frame.to_csv(out_dir / REGISTRY_CSV, index=False)
    (out_dir / REGISTRY_MD).write_text(render_md(frame), encoding="utf-8")
    logger.info("references: %d entries, %d resolved, %d unresolved",
                len(frame), int(frame.resolved.sum()), int((~frame.resolved).sum()))
    return out_dir / REGISTRY_CSV
