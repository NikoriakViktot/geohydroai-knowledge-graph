"""Is a quotation (and its numbers) in the cited source? One quote, or a whole open_citations.json."""

from __future__ import annotations

import json

import pandas as pd
import streamlit as st

from common import api, call, provenance

st.title("✅ Перевірка цитат")
st.caption("Детерміновано, без LLM: точний збіг, збіг після нормалізації (NFKC, лапки, тире) або нечіткий ≥ 0.92. "
           "Атрибуція показує, чи джерело саме цитує інших (вторинне цитування).")

ICON = {"FOUND_EXACT": "✅", "FOUND_NORMALIZED": "✅", "FOUND_FUZZY": "⚠️", "NOT_FOUND": "❌", "SOURCE_UNAVAILABLE": "❔"}
tab1, tab2 = st.tabs(["Одна цитата", "Файл open_citations.json"])

with tab1:
    with st.form("one"):
        source = st.text_input("Джерело (DOI, paper_id або ключ проєкту)", "AshikIqbal_EffectivenessofDEMsforFloodModeling_jfr3.12937")
        quote = st.text_area("Цитата", "A mean absolute vertical error of 1.12-1.61 m was found for the FABDEM in built-up areas")
        numbers = st.text_input("Числа, які мають бути поруч (через кому)", "1.12, 1.61")
        project = st.selectbox("Проєкт (для ключів bib)", ["", "floodstate-eo:paper3", "kakhovka-terrain:paper2",
                                                          "swot-dnipro:paper1", "kakhovka-report:v1", "article1"])
        go = st.form_submit_button("Перевірити", type="primary")
    if go:
        item = {"source": source.strip(), "quote": quote.strip(),
                "expected_numbers": [x.strip() for x in numbers.split(",") if x.strip()]}
        body, secs = call(api().quotes.verify, [item], project_id=project or None)
        if body:
            r = body["items"][0]
            st.subheader(f"{ICON.get(r['status'], '')} {r['status']}" + (f" (схожість {r['score']})" if r.get("score") else ""))
            if r.get("span"):
                sp = r["span"]
                st.markdown(f"**{sp.get('section') or ''}**, стор. {sp.get('page') or '—'} · `{sp['passage_id']}`")
                st.info(sp["text"])
            att = r.get("attribution") or {}
            if att.get("cites_other_sources"):
                st.warning("Джерело саме цитує інших — вторинне цитування: " + ", ".join(att.get("in_text_refs", []))
                           + (" → " + ", ".join(att.get("resolved_dois", [])) if att.get("resolved_dois") else ""))
            if r.get("numbers"):
                st.dataframe(pd.DataFrame(r["numbers"]), hide_index=True, width="stretch")
            if r.get("detail"):
                st.caption(r["detail"])
            provenance(body, secs)

with tab2:
    up = st.file_uploader("open_citations.json (з floodstate-eo, p100b)", type=["json"])
    project2 = st.selectbox("Проєкт", ["floodstate-eo:paper3", "kakhovka-terrain:paper2", "swot-dnipro:paper1",
                                       "kakhovka-report:v1", "article1"], key="p2")
    if up and st.button("Перевірити всі цитати", type="primary"):
        doc = json.loads(up.getvalue().decode("utf-8"))
        body, secs = call(api().quotes.verify_open_citations, doc, project_id=project2)
        if body:
            st.write({k: v for k, v in body["summary"].items()})
            rows = [{"": ICON.get(r["status"], ""), "ключ": r.get("key"), "статус": r["status"],
                     "знайдено в": ", ".join(r.get("found_in") or []), "розділ": (r.get("span") or {}).get("section"),
                     "стор.": (r.get("span") or {}).get("page"),
                     "вторинне": (r.get("attribution") or {}).get("cites_other_sources"), "цитата": r["quote"]}
                    for r in body["items"]]
            st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
            if body.get("not_checked"):
                st.caption("Не перевірено: " + "; ".join(f"{x['key']}: {x['reason']}" for x in body["not_checked"]))
            provenance(body, secs)
