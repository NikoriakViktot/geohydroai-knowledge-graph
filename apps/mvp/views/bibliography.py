"""Bibliography: DOI verification, .bib audit, BibTeX formatting, reference lists, manuscript citations."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from common import EXAMPLE_DOI, api, call, provenance

st.title("📚 Бібліографія")
PROJECTS = ["", "floodstate-eo:paper3", "kakhovka-terrain:paper2", "swot-dnipro:paper1", "kakhovka-report:v1", "article1"]
VERDICT = {"VERIFIED": "✅", "VERIFIED_WITH_NOTES": "🟡", "MISMATCH": "❌", "UNRESOLVED": "❔", "NOT_A_DOI": "⛔"}


def bib_input(key: str) -> str:
    up = st.file_uploader(".bib-файл", type=["bib", "txt"], key=f"{key}u")
    text = st.text_area("…або вставте BibTeX", height=160, key=f"{key}t")
    return up.getvalue().decode("utf-8") if up else text


tabs = st.tabs(["Перевірка DOI", "Аудит .bib", "DOI → BibTeX", "Список літератури", "Цитування в рукописі"])

with tabs[0]:
    st.caption("Поле за полем проти Crossref / DataCite / OpenAlex: назва ≥ 0.90, рік online/print, автори, том, сторінки.")
    lines = st.text_area("DOI по одному в рядку", "10.1007/s10712-015-9346-y\n10.24425/agg.2023.146162\n" + EXAMPLE_DOI)
    if st.button("Перевірити DOI", type="primary"):
        entries = [{"doi": x.strip()} for x in lines.splitlines() if x.strip()]
        body, secs = call(api().doi.verify, entries)
        if body:
            st.write(body["summary"])
            rows = [{"": VERDICT.get(r["verdict"], ""), "DOI": r.get("doi"), "вердикт": r["verdict"],
                     "назва": (r.get("registry") or {}).get("title"), "рік (print/online)":
                     f"{(r.get('registry') or {}).get('year_print')}/{(r.get('registry') or {}).get('year_online')}",
                     "у корпусі": (r.get("in_corpus") or {}).get("paper_id"), "нотатки": "; ".join(r.get("notes", []))}
                    for r in body["results"]]
            st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
            provenance(body, secs)

with tabs[1]:
    st.caption("DOI проти реєстрів, пропущені DOI (пошук Crossref), дублікати, рік у ключі, нотатки VERIFY.")
    text = bib_input("audit")
    proj = st.selectbox("Проєкт", PROJECTS, key="auditp")
    if text and st.button("Аудит", type="primary"):
        body, secs = call(api().bib.audit, text, project_id=proj or None, spinner="Перевіряю записи (до хвилини)…")
        if body:
            s = body["summary"]
            c = st.columns(4)
            c[0].metric("Записів", s.get("entries", 0))
            c[1].metric("OK", s.get("ok", 0))
            c[2].metric("Виправити", s.get("fix", 0))
            c[3].metric("Не розв'язано", s.get("unresolved", 0))
            df = pd.DataFrame(body["entries"])
            order = {"fix": 0, "unresolved": 1, "ok": 2}
            df = df.sort_values("status", key=lambda s_: s_.map(order))
            st.dataframe(df.assign(problems=df["problems"].map("; ".join))[
                ["status", "key", "type", "verdict", "doi", "suggested_doi", "problems"]],
                hide_index=True, width="stretch")
            for e in body["entries"]:
                if e.get("suggested_bibtex"):
                    with st.expander(f"Запропонований запис: {e['key']}"):
                        st.code(e["suggested_bibtex"], language="bibtex")
            provenance(body, secs)

with tabs[2]:
    dois = st.text_area("DOI по одному в рядку", EXAMPLE_DOI, key="fmt")
    proj = st.selectbox("Проєкт (уникати зайнятих ключів)", PROJECTS, key="fmtp")
    if st.button("Сформувати BibTeX", type="primary"):
        entries, secs = call(api().bib.format, [x.strip() for x in dois.splitlines() if x.strip()], project_id=proj or None)
        if entries:
            st.code("\n\n".join(e["bibtex"] for e in entries if e.get("bibtex")), language="bibtex")
            for e in entries:
                for n in e.get("notes", []):
                    st.caption(f"{e.get('key') or e['doi']}: {n}")

with tabs[3]:
    text = bib_input("render")
    c = st.columns(2)
    style = c[0].selectbox("Стиль", ["apa", "agu", "copernicus", "elsevier-harvard"])
    manuscript = c[1].text_area("Рукопис (необов'язково: ключі беруться з його цитувань)", height=100)
    if text and st.button("Оформити список", type="primary"):
        body, secs = call(api().bib.render, text, manuscript=manuscript or None, style=style)
        if body:
            st.markdown("\n".join(f"{i}. {r['text']}" for i, r in enumerate(body["references"], start=1)))
            if body.get("unresolved_keys"):
                st.warning("Не розв'язано: " + ", ".join(body["unresolved_keys"]))
            if body.get("uncited_entries"):
                st.caption("Не цитуються: " + ", ".join(body["uncited_entries"]))

with tabs[4]:
    ms_up = st.file_uploader("Рукопис (.md)", type=["md", "txt"], key="msu")
    text = bib_input("cit")
    if ms_up and text and st.button("Знайти цитування", type="primary"):
        body, secs = call(api().bib.citations, ms_up.getvalue().decode("utf-8"), text)
        if body:
            st.write(body["summary"])
            df = pd.DataFrame(body["occurrences"])
            if not df.empty:
                st.dataframe(df.assign(quoted=df["quoted"].map(lambda q: "; ".join(q)))[
                    ["status", "cite_text", "cite_key", "section", "line", "quoted", "sentence"]],
                    hide_index=True, width="stretch")
            if body.get("uncited_entries"):
                st.caption("Записи bib без цитувань: " + ", ".join(body["uncited_entries"]))
            provenance(body, secs)
