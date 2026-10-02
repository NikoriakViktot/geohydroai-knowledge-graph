"""Knowledge graph: one paper's neighbourhood, named queries, entity → papers."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from common import EXAMPLE_DOI, api, call, provenance

st.title("🕸️ Граф знань (Neo4j, лише читання)")
st.caption("Ребра методів/сенсорів/метрик прив'язано до тексту TEI: `grounded = true`, якщо термін є в тексті як "
           "слово. Без цього фільтра в топі були iRIC (зі слова «empirical») і HAND (з «on the other hand»).")

tab1, tab2, tab3 = st.tabs(["Стаття", "Іменовані запити", "Сутність → статті"])

with tab1:
    pid = st.text_input("DOI або paper_id", EXAMPLE_DOI)
    if st.button("Показати", type="primary"):
        body, secs = call(api().graph.paper, pid.strip(), include="authors,methods,sensors,metrics,topics,countries")
        if body:
            p = body["paper"]
            st.subheader(p.get("title") or pid)
            st.caption(" · ".join(str(x) for x in (p.get("year"), p.get("venue"), p.get("doi"),
                                                   f"цитувань (OpenAlex): {p.get('cited_by_count')}") if x))
            c = st.columns(3)
            c[0].metric("Цитує", body["counts"].get("cites_out"))
            c[1].metric("…з них у корпусі", body["counts"].get("cites_corpus"))
            c[2].metric("Цитують у корпусі", body["counts"].get("cited_in_corpus"))
            for part, label in (("methods", "Методи"), ("sensors", "Сенсори"), ("metrics", "Метрики")):
                rows = body.get(part) or []
                if rows:
                    st.markdown(f"**{label}**")
                    st.dataframe(pd.DataFrame([{"grounded": r.get("grounded"), "id": r["canonical_id"], "роль": r.get("role"),
                                                "форма": r.get("surface_form"), "згадок у TEI": r.get("tei_mentions"),
                                                "доказ (TEI)": (r.get("tei_evidence") or [""])[0]} for r in rows]),
                                 hide_index=True, width="stretch")
            if body.get("authors"):
                st.markdown("**Автори:** " + ", ".join(a["name"] or "?" for a in body["authors"]))
            if body.get("topics"):
                st.markdown("**Теми:** " + ", ".join(t["name"] or "?" for t in body["topics"]))
            if body.get("countries"):
                st.markdown("**Країни:** " + ", ".join(body["countries"]))
            provenance(body, secs)

with tab2:
    catalogue, _ = call(api().graph.queries)
    if catalogue:
        names = [q["name"] for q in catalogue]
        name = st.selectbox("Запит", names, index=names.index("top_methods") if "top_methods" in names else 0)
        spec = next(q for q in catalogue if q["name"] == name)
        st.caption(spec["description"])
        params = {}
        with st.form("named"):
            for pname, p in spec["params"].items():
                label = f"{pname}" + (" *" if p.get("required") else "") + (f" — {p['description']}" if p.get("description") else "")
                if p.get("choices"):
                    opts = [""] + p["choices"]
                    default = opts.index(p["default"]) if p.get("default") in opts else 0
                    v = st.selectbox(label, opts, index=default)
                    params[pname] = v or None
                elif p["type"] == "int":
                    v = st.number_input(label, value=p.get("default"), step=1, min_value=p.get("min"), max_value=p.get("max"))
                    params[pname] = int(v) if v is not None else None
                elif p["type"] == "float":
                    v = st.number_input(label, value=p.get("default"))
                    params[pname] = float(v) if v is not None else None
                else:
                    params[pname] = st.text_input(label, value=p.get("default") or "") or None
            limit = st.slider("Рядків", 1, 1000, 50)
            go = st.form_submit_button("Виконати", type="primary")
        if go:
            body, secs = call(api().graph.run, name, {k: v for k, v in params.items() if v is not None}, limit=limit)
            if body:
                df = pd.DataFrame(body["rows"], columns=body["columns"])
                st.dataframe(df, hide_index=True, width="stretch")
                if body.get("truncated"):
                    st.caption("Показано перші рядки (truncated).")
                if {"papers"} <= set(df.columns) and len(df.columns) <= 5:
                    st.bar_chart(df.set_index(df.columns[0])["papers"].head(25), horizontal=True)
                provenance(body, secs)

with tab3:
    c = st.columns(3)
    label = c[0].selectbox("Тип", ["Method", "Sensor", "Metric", "Topic", "Country", "FloodEvent"])
    cid = c[1].text_input("Ідентифікатор", "method.hand")
    grounded = c[2].selectbox("grounded", ["true", "any", "false"])
    role = st.radio("Роль", ["будь-яка", "used", "mentioned"], horizontal=True)
    if st.button("Знайти статті"):
        filters = {"grounded": grounded}
        if role != "будь-яка":
            filters["role"] = role
        with st.spinner("Звертаюся до API…"):
            try:
                rows = list(api().graph.entity_papers(label, cid, **filters))
            except Exception as exc:  # GHAIError and friends
                st.error(str(exc))
                rows = None
        if rows is not None:
            st.metric("Статей", len(rows))
            st.dataframe(pd.DataFrame([{"paper_id": r["paper_id"], "рік": r.get("year"), "назва": r.get("title"),
                                        "роль": r.get("role"), "grounded": r.get("grounded"),
                                        "доказ (TEI)": (r.get("tei_evidence") or [""])[0]} for r in rows]),
                         hide_index=True, width="stretch")
