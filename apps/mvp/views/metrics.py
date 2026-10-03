"""Metrics: deterministic extraction, the corpus fact table, the metric ontology."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from common import GHAIError, api, call, check_badge, current_checks, provenance, verify_widget, with_links

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
        st.session_state["facts_rows"] = rows
    rows = st.session_state.get("facts_rows")
    if rows is not None:
        fchecks = current_checks("metric_fact", tuple(r["fact_id"] for r in rows if r.get("fact_id")))
        df = pd.DataFrame([{"моя перевірка": check_badge(fchecks.get((r.get("fact_id"), "value"))),
                            "значення": r["value"], "paper_id": (r.get("paper") or {}).get("paper_id"),
                            "рік": (r.get("paper") or {}).get("year"), "таблиця": r["evidence"].get("table_label"),
                            "стовпець": r["evidence"].get("col_header"),
                            "рядок": " / ".join(r["evidence"].get("row_context") or []), "стор.": r["evidence"].get("page"),
                            "джерело": r.get("source"), "діапазон": r.get("range_verdict")}
                           for r in rows])
        c = st.columns(3)
        c[0].metric("Фактів", len(df))
        c[1].metric("Статей", df["paper_id"].nunique() if not df.empty else 0)
        c[2].metric("Медіана", round(float(df["значення"].median()), 3) if not df.empty else "—")
        if not df.empty:
            hist = pd.cut(df["значення"], bins=20).value_counts().sort_index()
            st.bar_chart(pd.Series(hist.values, index=[f"{i.left:.2f}" for i in hist.index], name="фактів"))
            df, cfg = with_links(df, "paper_id", "стор.")
            sel = st.dataframe(df, hide_index=True, width="stretch", column_config=cfg, on_select="rerun",
                               selection_mode="single-row", key="facts_table")
            st.caption("📄 відкриває PDF у новій вкладці на сторінці таблиці. Виберіть рядок — побачите кроп таблиці "
                       "(Nougat) і зможете позначити значення.")
            for i in sel.selection.rows:
                r = rows[i]
                pid = (r.get("paper") or {}).get("paper_id")
                with st.container(border=True):
                    st.markdown(f"**{r['value']}** · {r['evidence'].get('table_label')} · "
                                f"{r['evidence'].get('col_header')} · {' / '.join(r['evidence'].get('row_context') or [])}")
                    try:
                        reg = api().papers.regions(pid)["regions"] if pid else []
                    except GHAIError:
                        reg = []
                    pg = r["evidence"].get("page")
                    crops = [x for x in reg if x.get("page") == pg and x["region_type"] == "TABLE_REGION" and x.get("crop_url")]
                    if crops:
                        for x in crops[:3]:
                            st.image(x["crop_url"], caption=f"таблиця на стор. {pg} (Nougat)")
                    else:
                        st.caption("Кропу таблиці немає (Nougat не запускався або регіон не знайдено) — відкрийте PDF.")
                    if pid:
                        st.page_link("views/paper.py", label="Відкрити парсинг статті", icon="🔬",
                                     query_params={"paper": pid})
                    verify_widget("metric_fact", r["fact_id"], paper_id=pid, field="value",
                                  shown={"metric": r["metric"], "value": r["value"], "unit": r.get("unit"),
                                         "table": r["evidence"].get("table_label"), "page": pg}, key=f"mf{i}")
        st.caption("Факти з таблиць і тексту, витягнуті автоматично. Відсутність тут — не відсутність у корпусі.")

with tab3:
    if onto:
        df = pd.DataFrame(onto)
        df["aliases"] = df["aliases"].map(lambda a: ", ".join(a[:6]))
        df["range"] = df["range"].map(lambda r: "" if not r else f"[{r.get('lo', '−∞') if r.get('lo') is not None else '−∞'}, "
                                                                 f"{r.get('hi') if r.get('hi') is not None else '+∞'}]")
        st.dataframe(df[["canonical_id", "name", "range", "group", "extracted_from_text", "aliases"]],
                     hide_index=True, width="stretch")
