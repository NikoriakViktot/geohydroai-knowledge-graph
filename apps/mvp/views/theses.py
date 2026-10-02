"""Validate a theses.json or atomic_claims.yaml against contract v1 before any import."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from common import GHAIError, api

st.title("🧾 Тези")
st.caption("Строга перевірка за контрактом v1: нічого не «виправляється» мовчки. Помилки вказують місце в документі.")

c = st.columns(2)
kind = c[0].selectbox("Документ", ["theses", "atomic_claims"])
project = c[1].selectbox("Проєкт", ["floodstate-eo:paper3", "kakhovka-terrain:paper2", "swot-dnipro:paper1",
                                    "kakhovka-report:v1", "article1"])
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
