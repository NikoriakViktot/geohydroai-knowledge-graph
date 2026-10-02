"""Shared helpers of the MVP app: the API client, errors, provenance, small widgets.

The app is a client of the Knowledge API (http://127.0.0.1:8090/v1): every number on the
pages comes from an endpoint, through clients/python/ghai_client. The API key is read on
the server side (GHAI_API_KEY, else ~/.config/ghai/env) and never sent to the browser.
"""

from __future__ import annotations

import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Callable

import streamlit as st

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "clients" / "python"))

from ghai_client import GHAI, GHAIError  # noqa: E402

EXAMPLE_DOI = "10.1016/j.rse.2017.09.032"


def _env() -> tuple[str, str | None]:
    url, key = os.environ.get("GHAI_API_URL"), os.environ.get("GHAI_API_KEY")
    conf = Path.home() / ".config" / "ghai" / "env"
    if (not url or not key) and conf.is_file():
        for line in conf.read_text().splitlines():
            m = re.match(r"\s*(?:export\s+)?(GHAI_API_URL|GHAI_API_KEY)=(.*)", line)
            if m:
                value = m.group(2).strip().strip("'\"")
                if m.group(1) == "GHAI_API_URL":
                    url = url or value
                else:
                    key = key or value
    return (url or "http://127.0.0.1:8090/v1").rstrip("/"), key


@st.cache_resource
def api() -> GHAI:
    url, key = _env()
    return GHAI(url, key, timeout=300, retries=1)


def has_key() -> bool:
    return bool(_env()[1])


def call(fn: Callable, *args, spinner: str = "Звертаюся до API…", **kwargs) -> tuple[Any, float]:
    """Run an API call; show problem+json errors; return (result or None, seconds)."""
    t0 = time.time()
    try:
        with st.spinner(spinner):
            result = fn(*args, **kwargs)
        return result, time.time() - t0
    except GHAIError as exc:
        if exc.status == 0:
            st.error(f"API недоступне ({api().base_url}). Запустіть: `scripts/ghai_api.sh`")
        else:
            st.error(f"{exc.status} {exc.code}: {exc.detail}")
            if exc.errors:
                with st.expander("Деталі помилки"):
                    st.json(exc.errors)
        return None, time.time() - t0


def provenance(body: dict | None, seconds: float | None = None) -> None:
    if not body:
        return
    p = body.get("provenance") or {}
    bits = [f"API {p.get('api_version')}", f"commit {str(p.get('git_commit') or '')[:7]}"]
    if p.get("collection"):
        bits.append(f"колекція {p['collection']}")
    if p.get("corpus_manifest_id"):
        bits.append(f"маніфест {p['corpus_manifest_id'][:12]}")
    if seconds is not None:
        bits.append(f"{seconds:.2f} с")
    st.caption(" · ".join(bits))


def sidebar() -> None:
    url, key = _env()
    with st.sidebar:
        st.caption(f"API: `{url}`")
        st.caption("Ключ: " + ("✅ знайдено" if key else "❌ немає — `python -m src.api.keys create …`"))
        st.caption("[Swagger](http://127.0.0.1:8090/docs) · [Сторінка /ui](http://127.0.0.1:8090/ui)")


def status_badge(status: str | None) -> str:
    return {"ok": "✅", "down": "⛔", "degraded": "⚠️"}.get((status or "").lower(), "❔")
