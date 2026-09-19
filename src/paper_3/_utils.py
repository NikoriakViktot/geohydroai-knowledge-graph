"""Shared utilities for paper_3 module.

Mirrors src/paper_audit/_utils.py, with three deliberate differences:
  * resolve_gemini_key() accepts GEMINI_API_KEY *or* GOOGLE_API_KEY — paper_audit
    reads only the latter, which is not what .env or src/config/settings.py define.
  * get_con() additionally exposes paper_reference_edges (citation expansion).
  * directory constants point at data/paper_3_audit/.
"""
from __future__ import annotations

import json
import logging
import os
import re
import unicodedata
from pathlib import Path

import duckdb

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]

ANALYTICS_DIR = PROJECT_ROOT / "data" / "analytics"
PAPER_JSON_DIR = PROJECT_ROOT / "data" / "literature" / "paper_json"
NORMALIZED_DIR = PROJECT_ROOT / "data" / "normalized"
ENRICHED_DIR = PROJECT_ROOT / "data" / "enriched"
XML_DIR = PROJECT_ROOT / "data" / "literature" / "grobid_xml"
PDF_MISSING_DIR = PROJECT_ROOT / "data" / "literature" / "pdf_missing"

OUT_DIR = PROJECT_ROOT / "data" / "paper_3_audit"
HARVEST_DIR = OUT_DIR / "harvest"
PUBLISH_DIR = PROJECT_ROOT / "paper_3_audit"

THESES_PATH = Path(__file__).resolve().parent / "theses.yaml"
DRAFT_PATH = PUBLISH_DIR / "Kakhovka_scientific_report_article_draft_v1.md"

COHORT = "paper_3"

#: Exact string the ai_gateway synthesis rules require when evidence is absent.
#: Reused verbatim so a refusal is recognisable across both pipelines.
REFUSAL = "The available evidence is insufficient to support this claim."


# ── DOI / slug ────────────────────────────────────────────────────────────────

def normalize_doi(raw: str) -> str:
    """Strip resolver prefixes and lowercase. Same contract as paper_audit."""
    doi = (raw or "").strip().lower()
    for prefix in ("https://doi.org/", "http://doi.org/",
                   "http://dx.doi.org/", "https://dx.doi.org/", "doi:"):
        if doi.startswith(prefix):
            doi = doi[len(prefix):]
    return doi.strip()


def doi_to_slug(doi: str) -> str:
    """DOI → corpus stem. Must stay identical to recover_missing.doi_to_slug."""
    return normalize_doi(doi).replace("/", "_")


_PUNCT = re.compile(r"[^a-z0-9 ]+")
_WS = re.compile(r"\s+")


def normalize_title(raw: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace — for dedup only."""
    t = unicodedata.normalize("NFKD", (raw or "")).casefold()
    t = _PUNCT.sub(" ", t)
    return _WS.sub(" ", t).strip()


# ── DuckDB connection (parquet views) ─────────────────────────────────────────

_con: duckdb.DuckDBPyConnection | None = None

_VIEWS = [
    ("papers", "papers.parquet"),
    ("numeric_facts", "numeric_facts.parquet"),
    ("sensors", "sensors.parquet"),
    ("methods", "methods.parquet"),
    ("paper_reference_edges", "paper_reference_edges.parquet"),
    ("references_tbl", "references.parquet"),
]


def get_con() -> duckdb.DuckDBPyConnection:
    """Cached in-memory DuckDB with views over data/analytics/*.parquet.

    Views whose parquet file is missing are skipped rather than fatal — the
    analytics layer is rebuilt mid-pipeline and callers check for themselves.
    """
    global _con
    if _con is None:
        _con = duckdb.connect()
        for name, fname in _VIEWS:
            path = ANALYTICS_DIR / fname
            if not path.exists():
                logger.warning("analytics view %s skipped — %s missing", name, fname)
                continue
            p = str(path).replace("\\", "/")
            _con.execute(
                f"CREATE OR REPLACE VIEW {name} AS SELECT * FROM read_parquet('{p}')"
            )
    return _con


def reset_con() -> None:
    """Drop the cached connection so rebuilt parquet files are picked up."""
    global _con
    if _con is not None:
        _con.close()
    _con = None


# ── Gemini ────────────────────────────────────────────────────────────────────

def resolve_gemini_key() -> str:
    """API key from either env name, then settings. Empty string if unset.

    src/paper_audit/_utils.py:67 reads only GOOGLE_API_KEY while .env and
    src/config/settings.py:116 define GEMINI_API_KEY, so that path fails silently.
    """
    key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not key:
        try:
            from src.config import settings
            key = getattr(settings, "GEMINI_API_KEY", "")
        except Exception:
            key = ""
    return key or ""


def get_gemini_model_name() -> str:
    return os.getenv("GEMINI_MODEL", "gemini-2.5-flash")


# ── paper loaders ─────────────────────────────────────────────────────────────

def load_paper_json(paper_id: str) -> dict | None:
    """Normalized → enriched → paper_json, in that order.

    Normalized comes first here (paper_audit prefers enriched) because the
    retrieval cascade grounds evidence in normalised section text.
    """
    candidates = [
        (NORMALIZED_DIR, ".json"),
        (ENRICHED_DIR, ".json"),
        (PAPER_JSON_DIR, ".tei.paper.json"),
        (PAPER_JSON_DIR, ".paper.json"),
    ]
    for d, suffix in candidates:
        p = d / f"{paper_id}{suffix}"
        if not p.exists():
            continue
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.debug("unreadable %s: %s", p.name, exc)
            continue
        if isinstance(raw, dict) and "paper" in raw and "openalex" in raw:
            return raw["paper"]
        return raw
    return None


def paper_sections(paper: dict) -> dict[str, str]:
    """Section name → text, from a loaded paper dict. Empty dict when absent."""
    sections = paper.get("sections") or {}
    if not isinstance(sections, dict):
        return {}
    return {k: v for k, v in sections.items() if isinstance(v, str) and v.strip()}


# ── directories ───────────────────────────────────────────────────────────────

def ensure_dirs() -> None:
    for d in (OUT_DIR, HARVEST_DIR, PUBLISH_DIR):
        d.mkdir(parents=True, exist_ok=True)
