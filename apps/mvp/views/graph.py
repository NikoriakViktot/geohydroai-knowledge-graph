"""Knowledge graph: one paper's neighbourhood, named queries, entity → papers."""

from __future__ import annotations

import pandas as pd
import streamlit as st

import streamlit.components.v1 as components

from common import EXAMPLE_DOI, api, call, provenance, with_links

st.title("🕸️ Граф знань (Neo4j, лише читання)")
st.caption("Ребра методів/сенсорів/метрик прив'язано до тексту TEI: `grounded = true`, якщо термін є в тексті як "
           "слово. Без цього фільтра в топі були iRIC (зі слова «empirical») і HAND (з «on the other hand»).")

tab0, tab1, tab2, tab3 = st.tabs(["Мережа зв'язків", "Стаття", "Іменовані запити", "Сутність → статті"])

COLORS = {"paper": "#1f6feb", "cites": "#8b949e", "cited_by": "#a371f7", "method": "#2da44e", "sensor": "#d4a72c",
          "metric": "#cf222e", "country": "#0598bc", "topic": "#bf8700", "author": "#6e7781"}


def network_html(nodes: dict[str, tuple[str, str]], edges: list[tuple[str, str, str]], height: int = 640) -> str:
    """nodes: id → (label, kind); edges: (source, target, label). Self-contained HTML (no CDN)."""
    from pyvis.network import Network
    net = Network(height=f"{height}px", width="100%", directed=True, cdn_resources="in_line", bgcolor="#ffffff")
    net.barnes_hut(gravity=-4000, spring_length=140)
    for nid, (label, kind) in nodes.items():
        net.add_node(nid, label=label[:40], title=label, color=COLORS.get(kind, "#999"),
                     size=28 if kind == "paper" else 14, shape="dot")
    for s, d, lab in edges:
        if s in nodes and d in nodes:
            net.add_edge(s, d, title=lab, color="#c9d1d9", arrows="to")
    return net.generate_html(notebook=False)


with tab0:
    mode = st.radio("Що показати", ["Сусідство статті", "Метод — сенсор (корпус)"], horizontal=True)
    if mode == "Сусідство статті":
        c = st.columns([3, 1, 1])
        pid0 = c[0].text_input("DOI або paper_id ", EXAMPLE_DOI, key="net_pid")
        with_cites = c[1].toggle("Цитування в корпусі", True)
        grounded_only = c[2].toggle("Лише grounded", True)
        if st.button("Побудувати граф", type="primary"):
            body, secs = call(api().graph.paper, pid0.strip(), include="authors,methods,sensors,metrics,topics,countries")
            if body:
                p = body["paper"]
                center = p.get("paper_id") or pid0
                nodes = {center: (p.get("title") or center, "paper")}
                edges = []
                for part, kind in (("methods", "method"), ("sensors", "sensor"), ("metrics", "metric")):
                    for r in body.get(part) or []:
                        if grounded_only and not r.get("grounded"):
                            continue
                        nodes[r["canonical_id"]] = (r.get("display_name") or r["canonical_id"], kind)
                        edges.append((center, r["canonical_id"], r.get("role") or part))
                for cn in body.get("countries") or []:
                    nodes[f"country:{cn}"] = (cn, "country")
                    edges.append((center, f"country:{cn}", "study area"))
                for tp in (body.get("topics") or [])[:5]:
                    nodes[f"topic:{tp['name']}"] = (tp["name"] or "?", "topic")
                    edges.append((center, f"topic:{tp['name']}", "topic"))
                cited = []
                if with_cites:
                    for direction, kind in (("out", "cites"), ("in", "cited_by")):
                        try:
                            rows = list(api().graph.citations(center, direction=direction, in_corpus_only=True))[:40]
                        except Exception:
                            rows = []
                        for r in rows:
                            oid = r.get("paper_id") or r.get("doi")
                            if not oid:
                                continue
                            nodes[oid] = (f"{r.get('year') or ''} {r.get('title') or oid}", kind)
                            edges.append((center, oid, "cites") if direction == "out" else (oid, center, "cites"))
                            cited.append({"paper_id": r.get("paper_id"), "напрям": "цитує" if direction == "out" else "цитує її",
                                          "рік": r.get("year"), "назва": r.get("title")})
                components.html(network_html(nodes, edges), height=660, scrolling=False)
                st.caption("🔵 стаття · 🟢 метод · 🟡 сенсор · 🔴 метрика · 🩵 країна · 🟤 тема · ⚪ цитує · 🟣 цитують її")
                if cited:
                    df, cfg = with_links(pd.DataFrame(cited), "paper_id")
                    st.dataframe(df, hide_index=True, width="stretch", column_config=cfg)
                provenance(body, secs)
    else:
        c = st.columns(2)
        min_papers = c[0].slider("Мінімум статей на пару", 3, 100, 15)
        top = c[1].slider("Пар на графі", 10, 200, 60)
        if st.button("Побудувати граф", type="primary", key="ms_go"):
            body, secs = call(api().graph.run, "method_sensor_pairs", {"min_papers": min_papers}, limit=top)
            if body:
                nodes, edges = {}, []
                for m, s, n in body["rows"]:
                    nodes[m] = (m.removeprefix("method."), "method")
                    nodes[s] = (s.removeprefix("sensor."), "sensor")
                    edges.append((m, s, f"{n} статей"))
                components.html(network_html(nodes, edges), height=660)
                st.caption("🟢 метод · 🟡 сенсор; підпис ребра — кількість статей, де трапляються обидва (grounded).")
                provenance(body, secs)

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
