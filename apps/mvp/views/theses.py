"""Theses with their literature evidence (PDF at the page, crops, the person's checks), and the
contract check of a theses.json / atomic_claims.yaml before any import."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from common import (
    GHAIError, api, call, check_badge, current_checks, verify_widget, with_links,
)

st.title("🧾 Тези")
PROJECTS = ["floodstate-eo:paper3", "kakhovka-terrain:paper2", "swot-dnipro:paper1", "kakhovka-report:v1", "article1"]
tab_ev, tab_val = st.tabs(["Тези і докази", "Перевірка контракту"])

with tab_ev:
    st.caption("Статуси доказів (VERIFIED_SUPPORTED, …) поставили моделі під час літературного прогону — це "
               "model-assessed. Ваша позначка в колонці «моя перевірка» — єдина людська.")
    project = st.selectbox("Проєкт", PROJECTS, key="ev_project")
    body, _ = call(api().theses.set, project, spinner="Тези й докази…")
    theses = (body or {}).get("theses", [])
    if theses:
        th_checks = current_checks("thesis", tuple(f"{project}|{t['thesis_id']}" for t in theses))
        overview = pd.DataFrame([{"теза": t["thesis_id"], "моя перевірка": check_badge(th_checks.get((f"{project}|{t['thesis_id']}", None))),
                                  "доказів": len(t["evidence"]),
                                  "підтримано (модель)": sum(e["status"] == "VERIFIED_SUPPORTED" for e in t["evidence"]),
                                  "твердження": t["statement"]} for t in theses])
        st.dataframe(overview, hide_index=True, width="stretch", height=240)
        tid = st.selectbox("Теза", [t["thesis_id"] for t in theses],
                           format_func=lambda i: f"{i} — {next(t['statement'] for t in theses if t['thesis_id'] == i)[:110]}")
        th = next(t for t in theses if t["thesis_id"] == tid)
        st.markdown(f"> {th['statement']}")
        verify_widget("thesis", f"{project}|{tid}", project_id=project, shown={"statement": th["statement"]}, key="th")
        if th["atomic_claims"]:
            with st.expander(f"Атомарні твердження ({len(th['atomic_claims'])})"):
                st.dataframe(pd.DataFrame(th["atomic_claims"]), hide_index=True, width="stretch")
        ev = th["evidence"]
        if not ev:
            st.info("Для цієї тези немає рядків доказів.")
        else:
            ev_checks = current_checks("claim_evidence", tuple(e["evidence_id"] for e in ev))
            df = pd.DataFrame([{"моя перевірка": check_badge(ev_checks.get((e["evidence_id"], None))),
                                "статус (модель)": e["status"], "роль": e.get("role"), "джерело": e.get("cite_key"),
                                "paper_id": e.get("paper_id"), "стор.": e.get("page"), "секція": e.get("section"),
                                "цитата": e.get("evidence_quote"), "атом": e.get("atomic_id")} for e in ev])
            df, cfg = with_links(df, "paper_id", "стор.")
            sel = st.dataframe(df, hide_index=True, width="stretch", column_config=cfg, on_select="rerun",
                               selection_mode="single-row", key="ev_table")
            st.caption("Посилання PDF відкривають статтю в новій вкладці на потрібній сторінці. "
                       "Виберіть рядок, щоб побачити кропи сторінки й поставити позначку.")
            for i in sel.selection.rows:
                e = ev[i]
                with st.container(border=True):
                    st.markdown(f"**{e.get('cite_key') or e.get('paper_id')}** · стор. {e.get('page')} · "
                                f"{e['status']} (модель)")
                    if e.get("evidence_quote"):
                        st.markdown(f"> {e['evidence_quote']}")
                    if e.get("paper_id"):
                        st.page_link("views/paper.py", label="Відкрити парсинг статті", icon="🔬",
                                     query_params={"paper": e["paper_id"]})
                        try:
                            reg = api().papers.regions(e["paper_id"])["regions"]
                        except GHAIError:
                            reg = []
                        page_no = str(e.get("page") or "").split("-")[0].strip()
                        same = [r for r in reg if page_no.isdigit() and r.get("page") == int(page_no) and r.get("crop_url")]
                        if same:
                            st.caption(f"Кропи зі сторінки {page_no}:")
                            cols = st.columns(min(3, len(same)))
                            for j, r in enumerate(same[:6]):
                                cols[j % len(cols)].image(r["crop_url"], caption=r["region_type"])
                    verify_widget("claim_evidence", e["evidence_id"], paper_id=e.get("paper_id"), project_id=project,
                                  shown={"thesis_id": tid, "status": e["status"], "quote": e.get("evidence_quote"),
                                         "page": e.get("page")}, key=f"ev{i}")

with tab_val:
    st.caption("Строга перевірка за контрактом v1: нічого не «виправляється» мовчки. Помилки вказують місце в документі.")

    c = st.columns(2)
    kind = c[0].selectbox("Документ", ["theses", "atomic_claims"])
    project = c[1].selectbox("Проєкт", PROJECTS)
    up = st.file_uploader("theses.json або atomic_claims.yaml", type=["json", "yaml", "yml"])
    text = up.getvalue().decode("utf-8") if up else st.text_area(
        "…або вставте документ", '[{"id": "T1", "thesis": "SAR misses water under dense vegetation.", '
                                  '"refs": [{"key": "Pulvirenti_2021", "relation": "SUPPORTED_BY", "status": "VERIFY"}], '
                                  '"search_queries": ["flooded vegetation SAR"]}]', height=180)
    if text and st.button("Перевірити", type="primary"):
        try:
            with st.spinner("Перевіряю…"):
                body = api().theses.validate(kind, project, text)
            st.success("Документ відповідає контракту v1.")
            st.write(body["counts"])
            for w in body.get("warnings", []):
                st.info(w)
        except GHAIError as exc:
            st.error(f"{exc.status} {exc.code}: {exc.detail}")
            if exc.errors:
                st.dataframe(pd.DataFrame([{"місце": " → ".join(str(x) for x in e["loc"]), "помилка": e["msg"], "тип": e["type"]}
                                           for e in exc.errors]), hide_index=True, width="stretch")
            extra = exc.problem
            if extra.get("counts"):
                st.write(extra["counts"])
