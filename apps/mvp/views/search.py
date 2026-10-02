"""Semantic search over chunks, paper-level search and similar papers."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from common import EXAMPLE_DOI, api, call, provenance

st.title("🔎 Семантичний пошук")
st.warning("Схожість — не підтвердження. Перед цитуванням перевірте уривок через «Перевірка цитат». "
           "Порожній результат означає лише «цей пошук нічого не знайшов» (retrieval_validity = NOT_MEASURED). "
           "Якість поки помірна: 4 з 8 контрольних статей у топ-10 (SPECTER2 без адаптерів).")

CHUNK_TYPES = ["abstract", "sentence", "paragraph", "section", "figure", "table", "formula"]


def filters_form(prefix: str) -> dict:
    c = st.columns(4)
    y0 = c[0].number_input("Рік від", 1950, 2030, value=None, step=1, key=f"{prefix}y0")
    y1 = c[1].number_input("Рік до", 1950, 2030, value=None, step=1, key=f"{prefix}y1")
    types = c[2].multiselect("Типи чанків", CHUNK_TYPES, key=f"{prefix}t", placeholder="усі")
    excl = c[3].multiselect("Виключити когорти", ["paper_3"], key=f"{prefix}x")
    f = {"exclude_cohorts": excl}
    if y0:
        f["year_from"] = int(y0)
    if y1:
        f["year_to"] = int(y1)
    if types:
        f["chunk_types"] = types
    return f


def coverage(body: dict) -> None:
    cov = body.get("coverage") or {}
    st.caption(f"Пошук по {cov.get('papers_in_slice', 0)} статтях, {cov.get('chunks_in_slice', 0):,} чанках"
               .replace(",", " ") + (f"; без чанків: {cov.get('papers_without_chunks')}" if cov.get("papers_without_chunks")
                                     else "") + (f" — {cov['note']}" if cov.get("note") else ""))


tab1, tab2, tab3 = st.tabs(["Уривки", "Статті", "Схожі статті"])
with tab1:
    with st.form("chunks"):
        query = st.text_input("Запит", "HAND methods do not preserve hydraulic connectivity")
        k = st.slider("Скільки уривків", 1, 50, 10)
        f = filters_form("c")
        go = st.form_submit_button("Шукати", type="primary")
    if go:
        body, secs = call(api().search.chunks, query, k=k, filters=f)
        if body:
            coverage(body)
            rows = [{"score": h["score"], "paper": (h["paper"] or {}).get("paper_id"), "year": (h["paper"] or {}).get("year"),
                     "тип": h.get("chunk_type"), "розділ": h.get("section"), "стор.": h.get("page"), "текст": h["text"]}
                    for h in body["hits"]]
            st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch",
                         column_config={"текст": st.column_config.TextColumn(width="large")})
            provenance(body, secs)
with tab2:
    with st.form("papers"):
        queries = st.text_area("Формулювання (по одному в рядку)",
                               "HAND height above nearest drainage flood maps\nterrain-based inundation mapping")
        c = st.columns(2)
        k2 = c[0].slider("Скільки статей", 1, 50, 10)
        agg = c[1].selectbox("Агрегування", ["max", "mean", "count"])
        f2 = filters_form("p")
        go2 = st.form_submit_button("Шукати статті", type="primary")
    if go2:
        qs = [x.strip() for x in queries.splitlines() if x.strip()]
        body, secs = call(api().search.papers, qs, k=k2, filters=f2, aggregate=agg)
        if body:
            coverage(body)
            rows = [{"score": p["score"], "paper_id": p["paper_id"], "назва": (p.get("paper") or {}).get("title"),
                     "рік": (p.get("paper") or {}).get("year"), "збігів": p["hits"], "формулювань": p["queries_matched"],
                     "найкращий уривок": p["best_chunks"][0]["text"][:200] if p["best_chunks"] else ""}
                    for p in body["papers"]]
            st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
            provenance(body, secs)
with tab3:
    seed = st.text_input("DOI або paper_id статті-зразка", EXAMPLE_DOI)
    if st.button("Знайти схожі"):
        arg = {"doi": seed} if seed.startswith("10.") or "doi.org" in seed else {"paper_id": seed}
        body, secs = call(api().search.similar, k=15, **arg)
        if body:
            coverage(body)
            st.dataframe(pd.DataFrame([{"score": p["score"], "paper_id": p["paper_id"],
                                        "назва": (p.get("paper") or {}).get("title"), "рік": (p.get("paper") or {}).get("year")}
                                       for p in body["papers"]]), hide_index=True, width="stretch")
            provenance(body, secs)
