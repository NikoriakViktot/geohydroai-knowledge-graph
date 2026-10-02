"""Overview: dependency health, corpus counts, projections, the endpoint inventory, agent rules."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from common import api, call, provenance, status_badge

st.title("🌊 GeoHydroAI Knowledge API — MVP")
st.caption("Корпус наукових статей про повені, гідрологію й дистанційне зондування: Postgres — шар правди; "
           "Neo4j, Chroma і Parquet — проєкції. Усе нижче отримано з API.")

health, t_health = call(api().health)
if health:
    deps = health.get("dependencies", {})
    cols = st.columns(len(deps) or 1)
    for col, (name, d) in zip(cols, deps.items()):
        col.metric(name, f"{status_badge(d.get('status'))} {d.get('status')}",
                   f"{d.get('latency_ms', '?')} мс", delta_color="off")
        if d.get("detail"):
            col.caption(d["detail"])

stats, _ = call(api().stats)
manifest, _ = call(api().manifest)
if stats:
    st.subheader("Корпус")
    papers = stats.get("papers", {})
    c = st.columns(4)
    c[0].metric("Статей (ідентичність)", f"{papers.get('total', 0):,}".replace(",", " "))
    c[1].metric("TEI (GROBID)", f"{stats.get('files', {}).get('tei', 0):,}".replace(",", " "))
    c[2].metric("PDF-файлів", f"{stats.get('files', {}).get('pdf', 0):,}".replace(",", " "))
    if manifest:
        c[3].metric("Колекція векторів", manifest.get("collection"), manifest.get("embedding_model"), delta_color="off")
    left, right = st.columns(2)
    with left:
        st.caption("Статуси ідентичності")
        st.bar_chart(pd.Series(papers.get("by_identity_status", {}), name="статей"), horizontal=True)
    with right:
        st.caption("Проєкції (core.projection_state)")
        proj = pd.DataFrame(stats.get("projections", []))
        if not proj.empty:
            st.dataframe(proj[[c for c in ("store", "name", "item_count", "built_at") if c in proj]],
                         hide_index=True, width="stretch")

st.subheader("Ендпоінти")
index, _ = call(api().get, "docs/index")
if index:
    df = pd.DataFrame(index["endpoints"])
    df = df[df["variant"].isna()] if "variant" in df else df
    counts = df.groupby(["group", "status"]).size().unstack(fill_value=0)
    c1, c2 = st.columns([2, 3])
    with c1:
        n_impl = int((df["status"] == "implemented").sum())
        st.metric("Реалізовано", f"{n_impl} з {len(df)}")
        st.bar_chart(counts, horizontal=True)
    with c2:
        groups = st.multiselect("Групи", sorted(df["group"].unique()), placeholder="усі групи")
        only = st.radio("Статус", ["усі", "implemented", "planned"], horizontal=True)
        view = df if not groups else df[df["group"].isin(groups)]
        view = view if only == "усі" else view[view["status"] == only]
        show = [c for c in ("status", "group", "method", "path", "summary") if c in view]
        st.dataframe(view[show].sort_values(["group", "path"]), hide_index=True, width="stretch",
                     height=380)

st.subheader("Правила для агентів")
rules, _ = call(api().agent_rules)
if rules:
    st.markdown(rules.get("top_rules", ""))
    with st.expander(f"Усі правила ({len(rules.get('rules', []))})"):
        rdf = pd.DataFrame(rules.get("rules", []))
        show = [c for c in ("id", "category", "level", "text") if c in rdf]
        st.dataframe(rdf[show], hide_index=True, width="stretch")
provenance(health, t_health)
