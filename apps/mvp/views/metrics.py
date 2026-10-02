"""Metrics: deterministic extraction, the corpus fact table, the metric ontology."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from common import api, call, provenance

st.title("📊 Метрики")
st.caption("Значення береться лише тоді, коли назва метрики стоїть перед ним у тому ж реченні; нерівності "
           "(«NSE > 0.5») позначено; NSE 1.7 відхиляється, а не ділиться на 100.")

tab1, tab2, tab3 = st.tabs(["Витягти", "Факти корпусу", "Онтологія"])

with tab1:
    mode = st.radio("Джерело", ["Текст", "Стаття корпусу (paper_id)"], horizontal=True)
    if mode == "Текст":
        text = st.text_area("Текст", "The calibrated model reached NSE = −0.27 at Kherson and an overall accuracy of 94.2 %. "
                                     "KGE values between 0.61 and 0.74 were obtained; NSE of 1.7 is implausible.")
        args = {"text": text}
    else:
        args = {"paper_id": st.text_input("paper_id", "1-s2.0-S0022169424001562-main")}
    if st.button("Витягти", type="primary"):
        body, secs = call(api().metrics.extract, **args)
        if body:
            facts = pd.DataFrame([{"метрика": f["metric"], "значення": f["value"], "до": f.get("value_hi"),
                                   "одиниця": f.get("unit"), "кваліфікатор": f.get("qualifier"), "джерело": f["source"],
                                   "стор.": f["evidence"].get("page"),
                                   "доказ": f["evidence"].get("text") or f"{f['evidence'].get('table_label')} · "
                                                                          f"{f['evidence'].get('col_header')}"}
                                  for f in body["facts"]])
            st.metric("Фактів", len(facts))
            st.dataframe(facts, hide_index=True, width="stretch")
            if body["rejected"]:
                st.warning("Відхилено: " + "; ".join(f"{r['raw']} — {r['reason']}" for r in body["rejected"]))
            provenance(body, secs)

with tab2:
    onto, _ = call(api().metrics.ontology)
    ids = sorted(m["canonical_id"] for m in (onto or []) if m["canonical_id"].startswith("metric."))
    c = st.columns(4)
    metric = c[0].selectbox("Метрика", ids, index=ids.index("metric.nse") if "metric.nse" in ids else 0)
    lo = c[1].number_input("Мін.", value=None)
    hi = c[2].number_input("Макс.", value=None)
    method = c[3].text_input("Метод (canonical_id, напр. method.swat)", "")
    if st.button("Показати факти", type="primary"):
        filters = {"metric": metric}
        if lo is not None:
            filters["min"] = lo
        if hi is not None:
            filters["max"] = hi
        if method:
            filters["method"] = method
        with st.spinner("Звертаюся до API…"):
            try:
                rows = list(api().metrics.facts(**filters))
            except Exception as exc:
                st.error(str(exc))
                rows = None
        if rows is not None:
            df = pd.DataFrame([{"значення": r["value"], "стаття": (r.get("paper") or {}).get("paper_id"),
                                "рік": (r.get("paper") or {}).get("year"), "таблиця": r["evidence"].get("table_label"),
                                "стовпець": r["evidence"].get("col_header"),
                                "рядок": " / ".join(r["evidence"].get("row_context") or []), "стор.": r["evidence"].get("page")}
                               for r in rows])
            c = st.columns(3)
            c[0].metric("Фактів", len(df))
            c[1].metric("Статей", df["стаття"].nunique() if not df.empty else 0)
            c[2].metric("Медіана", round(float(df["значення"].median()), 3) if not df.empty else "—")
            if not df.empty:
                hist = pd.cut(df["значення"], bins=20).value_counts().sort_index()
                st.bar_chart(pd.Series(hist.values, index=[f"{i.left:.2f}" for i in hist.index], name="фактів"))
                st.dataframe(df, hide_index=True, width="stretch")
            st.caption("Лише таблиці GROBID (990 статей). Відсутність тут — не відсутність у корпусі.")

with tab3:
    if onto:
        df = pd.DataFrame(onto)
        df["aliases"] = df["aliases"].map(lambda a: ", ".join(a[:6]))
        df["range"] = df["range"].map(lambda r: "" if not r else f"[{r.get('lo', '−∞') if r.get('lo') is not None else '−∞'}, "
                                                                 f"{r.get('hi') if r.get('hi') is not None else '+∞'}]")
        st.dataframe(df[["canonical_id", "name", "range", "group", "extracted_from_text", "aliases"]],
                     hide_index=True, width="stretch")
