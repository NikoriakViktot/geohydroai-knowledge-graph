"""
utils.py — Pure-Python utilities shared across ingestion stages.

No external dependencies (stdlib only + numpy for json_safe).
Importable from any ingestion stage without circular-dependency risk.
"""
from __future__ import annotations

import hashlib
import re
import urllib.parse
from pathlib import Path
from typing import Optional

import numpy as np


def clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def snippet(text: str, start: int, end: int, window: int = 120) -> str:
    return clean_text(text[max(0, start - window): min(len(text), end + window)])


def make_hash(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def doi_to_url(doi: Optional[str]) -> Optional[str]:
    return f"https://doi.org/{doi}" if doi else None


def scholar_url(title: Optional[str]) -> Optional[str]:
    if not title:
        return None
    return "https://scholar.google.com/scholar?q=" + urllib.parse.quote(title)


def ensure_dict(value, default=None):
    if isinstance(value, dict):
        return value
    return default if default is not None else {}


def json_safe(obj):
    import datetime as _dt
    if isinstance(obj, dict):
        return {str(k): json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [json_safe(v) for v in obj]
    if isinstance(obj, (np.float32, np.float64)):
        return float(obj)
    if isinstance(obj, (np.int32, np.int64)):
        return int(obj)
    if isinstance(obj, Path):
        return str(obj)
    if isinstance(obj, (_dt.datetime, _dt.date)):
        return obj.isoformat()
    return obj


def build_full_text(sections: dict) -> str:
    return " ".join([
        sections.get("abstract", ""),
        sections.get("introduction", ""),
        sections.get("study_area", ""),
        sections.get("data_sources", ""),
        sections.get("methods", ""),
        sections.get("results", ""),
        sections.get("other", ""),
    ])
