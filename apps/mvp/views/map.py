"""Where the corpus papers' studies are: papers per study-area country on a map, and the papers of
one country with links to their PDFs."""

from __future__ import annotations

import pandas as pd
import plotly.express as px
import streamlit as st

from common import api, call, provenance, with_links

st.title("🗺️ Карта досліджень")
st.caption("Країна району дослідження з графа (OpenAlex і правила над текстом). Це рівень країни, а не точки "
           "дослідження; країна може бути помилковою — перевіряйте у вкладці «Сутності» сторінки статті.")

c = st.columns(3)
y0 = c[0].number_input("Рік від", 1950, 2030, value=None, step=1)
y1 = c[1].number_input("Рік до", 1950, 2030, value=None, step=1)
params = {k: int(v) for k, v in (("year_from", y0), ("year_to", y1)) if v}
body, secs = call(api().graph.run, "papers_per_country", params, limit=500, spinner="Країни…")
if not body:
    st.stop()
df = pd.DataFrame(body["rows"], columns=body["columns"]).dropna(subset=["lat", "lon"])
c[2].metric("Статей з країною", int(df["papers"].sum()))
fig = px.scatter_geo(df, lat="lat", lon="lon", size="papers", hover_name="country", hover_data={"papers": True,
                     "lat": False, "lon": False}, projection="natural earth", size_max=45,
                     color="papers", color_continuous_scale="Blues")
fig.update_layout(margin=dict(l=0, r=0, t=0, b=0), height=520, coloraxis_showscale=False)
st.plotly_chart(fig, width="stretch")
provenance(body, secs)

country = st.selectbox("Країна", df["country"].tolist(),
                       index=df["country"].tolist().index("Ukraine") if "Ukraine" in df["country"].tolist() else 0)
rows, _ = call(api().graph.run, "papers_by_country", {"country": country, **params}, limit=1000, spinner="Статті…")
if rows:
    papers = pd.DataFrame(rows["rows"], columns=rows["columns"])
    st.metric(f"Статей: {country}", len(papers))
    papers, cfg = with_links(papers, "paper_id")
    cfg["paper_id"] = st.column_config.TextColumn("paper_id", width="small")
    st.dataframe(papers, hide_index=True, width="stretch", column_config=cfg)
    st.caption("📄 відкриває PDF у новій вкладці. Розбір статті — сторінка «Стаття: парсинг» (?paper=<paper_id>).")
