"""One paper as parsed: GROBID sections and tables, Nougat regions (formulas, tables, figures with
their PNG crops), entities; every item can be marked by a person (verify.human_check)."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from common import GHAIError, api, check_badge, current_checks, links, pdf_at, show_formula, verify_widget

st.title("🔬 Стаття: парсинг, формули, таблиці")
st.caption("Порівняйте розбір зі сторінкою PDF (відкривається в новій вкладці) і позначте, що правильно, а що ні. "
           "Позначки — це шар правди, перевірений вами; агенти їх не пишуть.")

default = st.query_params.get("paper", "1-s2.0-S030147972500948X-main")
pid = st.text_input("paper_id", default).strip()
if not pid:
    st.stop()
st.query_params["paper"] = pid

try:
    paper = api().papers.get(pid)
except GHAIError as exc:
    if exc.status != 404:
        st.error(f"{exc.status} {exc.code}: {exc.detail}")
        st.stop()
    paper = {"paper_id": pid}
    st.info("Статті ще немає в реєстрі ідентичностей (нова, ETL ідентичностей ще не запускався) — показую "
            "те, що вже розібрано.")
lk = links((pid,)).get(pid) or {}
c = st.columns([5, 1, 1])
c[0].subheader(paper.get("title") or pid)
c[0].caption(" · ".join(str(x) for x in (paper.get("year"), paper.get("venue"), paper.get("doi"),
                                         paper.get("identity_status")) if x))
if lk.get("pdf_url"):
    c[1].link_button("📄 PDF", lk["pdf_url"])
if lk.get("doi_url"):
    c[2].link_button("🔗 DOI", lk["doi_url"])
verify_widget("paper", pid, paper_id=pid, field="metadata",
              shown={k: paper.get(k) for k in ("title", "year", "venue", "doi")}, key="meta")

try:
    reg = api().papers.regions(pid)
except GHAIError as exc:
    reg = None
    st.info(f"Регіонів Nougat немає: {exc.detail}")
regions = (reg or {}).get("regions", [])
checks = current_checks("region", tuple(r["region_id"] for r in regions)) if regions else {}
formula_checks = current_checks("formula", tuple(r["region_id"] for r in regions if r["region_type"] == "FORMULA_REGION"))

pages = sorted({r["page"] for r in regions if r.get("page") is not None})
page = st.selectbox("Сторінка", ["усі"] + pages) if pages else "усі"


def quiet(fn, *args, what: str = ""):
    """An API call whose failure is a note, not an error (new papers have no TEI record yet)."""
    try:
        with st.spinner(what or "Звертаюся до API…"):
            return fn(*args)
    except GHAIError as exc:
        st.caption(f"{what}: {exc.code} — {exc.detail}")
        return None


def unique_columns(header: list) -> list[str]:
    seen: dict[str, int] = {}
    out = []
    for h in header:
        h = str(h) or "—"
        seen[h] = seen.get(h, 0) + 1
        out.append(h if seen[h] == 1 else f"{h} ({seen[h]})")
    return out


def first_text(x) -> str:
    while isinstance(x, (list, tuple)):
        x = x[0] if x else ""
    return "" if x is None else str(x)


def on_page(r: dict) -> bool:
    return page == "усі" or r.get("page") == page


tab_f, tab_t, tab_g, tab_s, tab_e = st.tabs(["∑ Формули", "▦ Таблиці", "🖼️ Рисунки", "§ Секції (GROBID)", "🏷️ Сутності"])

with tab_f:
    fs = [r for r in regions if r["region_type"] == "FORMULA_REGION" and on_page(r)]
    st.caption(f"{len(fs)} формульних регіонів. Регіон Nougat ширший за формулу: поруч може бути текст.")
    for r in fs:
        with st.container(border=True):
            h = st.columns([3, 2])
            h[0].markdown(f"**стор. {r.get('page')}** · `{r['region_id']}` · "
                          + check_badge(formula_checks.get((r["region_id"], "latex"))))
            if lk.get("pdf_url"):
                h[1].link_button("Сторінка в PDF", pdf_at(lk, r.get("page")))
            col = st.columns(2)
            if r.get("crop_url"):
                col[0].image(r["crop_url"], caption="кроп зі сторінки")
            with col[1]:
                show_formula(r.get("latex") or r.get("text"))
            verify_widget("formula", r["region_id"], paper_id=pid, field="latex",
                          shown={"latex": r.get("latex"), "page": r.get("page")})

with tab_t:
    ts = [r for r in regions if r["region_type"] == "TABLE_REGION" and on_page(r)]
    st.caption(f"{len(ts)} таблиць (Nougat). Нижче — таблиці GROBID для порівняння.")
    for r in ts:
        with st.container(border=True):
            st.markdown(f"**стор. {r.get('page')}** · `{r['region_id']}` · " + check_badge(checks.get((r["region_id"], "text"))))
            col = st.columns(2)
            if r.get("crop_url"):
                col[0].image(r["crop_url"])
            with col[1]:
                st.markdown(r.get("text") or "_Nougat не прочитав таблицю_")
            verify_widget("table", r["region_id"], paper_id=pid, field="text",
                          shown={"text": (r.get("text") or "")[:4000], "page": r.get("page")})
    tables = quiet(api().papers.tables, pid, what="Таблиці GROBID")
    for t in (tables or {}).get("tables", []):
        if page != "усі" and t.get("page") != page:
            continue
        with st.expander(f"GROBID {t.get('label') or t['table_id']} · стор. {t.get('page')} — {(t.get('caption') or '')[:90]}"):
            rows = t.get("rows") or []
            if rows:
                width_ = max(len(r) for r in rows)
                rows = [list(r) + [""] * (width_ - len(r)) for r in rows]
                st.dataframe(pd.DataFrame(rows[1:], columns=unique_columns(rows[0])) if len(rows) > 1
                             else pd.DataFrame(rows), hide_index=True)
            verify_widget("table", f"{pid}#{t['table_id']}", paper_id=pid, field="grobid",
                          shown={"caption": t.get("caption"), "page": t.get("page")}, key=f"g{t['table_id']}")

with tab_g:
    gs = [r for r in regions if r["region_type"] in ("FIGURE_REGION", "SCIENTIFIC_DIAGRAM", "MULTI_PANEL_FIGURE")
          and on_page(r)]
    st.caption(f"{len(gs)} рисунків і діаграм")
    cols = st.columns(3)
    for i, r in enumerate(gs):
        with cols[i % 3].container(border=True):
            if r.get("crop_url"):
                st.image(r["crop_url"])
            st.caption(f"стор. {r.get('page')} · {r['region_type']} · {check_badge(checks.get((r['region_id'], 'crop')))}")
            if r.get("text"):
                with st.expander("Текст Nougat"):
                    st.markdown(r["text"][:3000])
            verify_widget("region", r["region_id"], paper_id=pid, field="crop",
                          shown={"type": r["region_type"], "page": r.get("page")})

with tab_s:
    sec = quiet(api().papers.sections, pid, what="Секції")
    if sec:
        st.dataframe(pd.DataFrame(sec["sections"]), hide_index=True, width="stretch")
        st.caption(f"рисунків {sec.get('figures')} · таблиць {sec.get('tables')} · формул {sec.get('formulas')} · "
                   f"анотація: {'є' if sec.get('has_abstract') else 'немає'}")
        verify_widget("paper", pid, paper_id=pid, field="sections", shown={"n": len(sec["sections"])}, key="secs")

with tab_e:
    ent = quiet(api().papers.entities, pid, what="Сутності")
    if ent:
        st.caption("Сутності витягнуто правилами й словником, без людської перевірки; виберіть рядок, щоб позначити його.")
        for part, label in (("methods", "Методи"), ("sensors", "Сенсори"), ("metrics", "Метрики")):
            rows = ent.get(part) or []
            if not rows:
                continue
            st.markdown(f"**{label}**")
            edge_checks = current_checks("entity_edge", tuple(f"{pid}|{r['canonical_id']}" for r in rows))
            df = pd.DataFrame([{"моя перевірка": check_badge(edge_checks.get((f"{pid}|{r['canonical_id']}", None))),
                                "id": r["canonical_id"], "форма": r.get("surface_form"), "роль": r.get("role"),
                                "grounded": r.get("grounded"), "згадок": r.get("tei_mentions"), "стор.": r.get("page"),
                                "доказ": first_text(r.get("tei_evidence") or r.get("evidence"))} for r in rows])
            sel = st.dataframe(df, hide_index=True, width="stretch", on_select="rerun", selection_mode="single-row",
                               key=f"ent_{part}")
            for i in sel.selection.rows:
                r = rows[i]
                verify_widget("entity_edge", f"{pid}|{r['canonical_id']}", paper_id=pid,
                              shown={"canonical_id": r["canonical_id"], "surface_form": r.get("surface_form"),
                                     "role": r.get("role"), "evidence": first_text(r.get("tei_evidence"))},
                              key=f"e_{part}_{i}")
        sa = ent.get("study_area") or {}
        if sa:
            st.markdown(f"**Район дослідження:** головна країна — {sa.get('primary_country') or '—'}")
            st.dataframe(pd.DataFrame(sa.get("countries") or []), hide_index=True)
            verify_widget("location", pid, paper_id=pid, field="study_area",
                          shown={"primary_country": sa.get("primary_country")}, key="loc")
        st.caption(f"Задача: {(ent.get('task') or {}).get('label')} · тип дослідження: "
                   f"{(ent.get('study_type') or {}).get('label')}")
