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


# ── links, formulas and the human verification widget ───────────────────────────

VERDICTS = {"correct": "✅ правильно", "partial": "🟡 частково", "incorrect": "❌ неправильно", "unsure": "❔ не впевнений"}
PROBLEMS = {"": "—", "text_missing": "текст не розпізнано", "text_garbled": "текст спотворено (OCR/кодування)",
            "table_broken": "таблиця зламана", "formula_broken": "формула зламана", "crop_wrong": "неправильний кроп",
            "value_wrong": "неправильне значення", "unit_wrong": "неправильна одиниця",
            "entity_wrong": "неправильна сутність", "location_wrong": "неправильне місце",
            "metadata_wrong": "неправильні метадані", "evidence_not_supporting": "доказ не підтримує тезу",
            "definition_wrong": "неправильне визначення символу", "quantity_wrong": "неправильна величина",
            "other": "інше"}
BADGE = {"correct": "✅", "partial": "🟡", "incorrect": "❌", "unsure": "❔"}


def _verify_key() -> str | None:
    """The person-only key with the 'verify' scope (~/.config/ghai/verify_env); never shown in the page."""
    key = os.environ.get("GHAI_VERIFY_KEY")
    conf = Path.home() / ".config" / "ghai" / "verify_env"
    if not key and conf.is_file():
        m = re.search(r"GHAI_VERIFY_KEY=(\S+)", conf.read_text())
        key = m.group(1).strip("'\"") if m else None
    return key


@st.cache_resource
def verify_api() -> GHAI | None:
    key = _verify_key()
    return GHAI(_env()[0], key, timeout=60, retries=1) if key else None


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def links(paper_ids: tuple[str, ...]) -> dict[str, dict]:
    """paper_id → {pdf_url, doi_url}; signed links live 12 h, cached for 6."""
    ids = tuple(p for p in paper_ids if p)
    if not ids:
        return {}
    try:
        return api().papers.links(list(ids))
    except GHAIError:
        return {}


def pdf_at(link: dict | None, page) -> str | None:
    url = (link or {}).get("pdf_url")
    if not url:
        return None
    try:
        return f"{url}#page={int(page)}" if page not in (None, "") else url
    except (TypeError, ValueError):
        return url


def with_links(df, paper_col: str = "paper_id", page_col: str | None = None):
    """Add 'PDF' and 'DOI' link columns; returns (df, column_config) for st.dataframe (links open in a new tab)."""
    lk = links(tuple(sorted({str(p) for p in df[paper_col].dropna()}))) if len(df) else {}
    df = df.copy()
    df["PDF"] = [pdf_at(lk.get(str(p)), df[page_col].iloc[i] if page_col else None) for i, p in enumerate(df[paper_col])]
    df["DOI"] = [(lk.get(str(p)) or {}).get("doi_url") for p in df[paper_col]]
    cfg = {"PDF": st.column_config.LinkColumn("PDF", display_text="📄 відкрити"),
           "DOI": st.column_config.LinkColumn("DOI", display_text="🔗 doi")}
    return df, cfg


@st.cache_data(ttl=30, show_spinner=False)
def current_checks(target_kind: str, target_ids: tuple[str, ...]) -> dict[tuple[str, str | None], dict]:
    """(target_id, field) → the latest human judgement."""
    if not target_ids:
        return {}
    out = {}
    try:
        for i in range(0, len(target_ids), 100):
            for c in api().verify.list(target_kind=target_kind, target_id=list(target_ids[i:i + 100]), limit=5000):
                out[(c["target_id"], c.get("field"))] = c
    except GHAIError:
        pass
    return out


def check_badge(check: dict | None) -> str:
    if not check:
        return "· не перевірено"
    return f"{BADGE.get(check['verdict'], '')} {VERDICTS.get(check['verdict'], check['verdict'])}" + (
        f" — {PROBLEMS.get(check.get('problem') or '', check.get('problem'))}" if check.get("problem") else "")


def verify_widget(target_kind: str, target_id: str, *, paper_id: str | None = None, field: str | None = None,
                  shown: dict | None = None, project_id: str | None = None, key: str = "") -> None:
    """The person's judgement of one item: status, and a form that appends a new check."""
    k = key or f"{target_kind}:{target_id}:{field}"
    cur = current_checks(target_kind, (target_id,)).get((target_id, field))
    with st.popover(f"Моя перевірка {check_badge(cur)}", width="content"):
        vapi = verify_api()
        if vapi is None:
            st.warning("Немає ключа з правом `verify` (~/.config/ghai/verify_env).")
            return
        if cur:
            st.caption(f"Остання позначка #{cur['check_id']} ({cur.get('labeler')}, {str(cur.get('created_at'))[:16]})"
                       + (f": {cur['note']}" if cur.get("note") else ""))
        with st.form(f"vf:{k}", clear_on_submit=False, border=False):
            verdict = st.radio("Вердикт", list(VERDICTS), format_func=VERDICTS.get, horizontal=True,
                               index=list(VERDICTS).index(cur["verdict"]) if cur else 0)
            problem = st.selectbox("Проблема", list(PROBLEMS), format_func=PROBLEMS.get,
                                   index=list(PROBLEMS).index(cur.get("problem") or "") if cur else 0)
            corrected = st.text_input("Правильне значення (якщо є)", "")
            note = st.text_area("Нотатка", "", height=68)
            if st.form_submit_button("Зберегти позначку", type="primary"):
                check = {"target_kind": target_kind, "target_id": target_id, "field": field, "paper_id": paper_id,
                         "project_id": project_id, "verdict": verdict, "problem": problem or None,
                         "corrected": {"value": corrected} if corrected else None, "note": note or None,
                         "shown": shown, "supersedes": cur["check_id"] if cur else None}
                try:
                    body = vapi.verify.add([check])
                    current_checks.clear()
                    st.success(f"Збережено #{body['items'][0]['check_id']}")
                except GHAIError as exc:
                    st.error(f"{exc.status} {exc.code}: {exc.detail}")


_DISPLAY = re.compile(r"\\\[(.+?)\\\]", re.S)
_INLINE = re.compile(r"\\\((.+?)\\\)", re.S)


def nougat_markdown(text: str | None) -> str:
    """Nougat's Mathpix-style markdown → Streamlit markdown with KaTeX ($…$, $$…$$)."""
    if not text:
        return ""
    text = _DISPLAY.sub(lambda m: f"\n$$\n{m.group(1).strip()}\n$$\n", text)
    return _INLINE.sub(lambda m: f"${m.group(1).strip()}$", text)


def show_formula(latex: str | None) -> None:
    md = nougat_markdown(latex)
    try:
        st.markdown(md)
    except Exception:
        st.code(latex or "", language="latex")
    with st.expander("LaTeX як текст"):
        st.code(latex or "", language="latex")
