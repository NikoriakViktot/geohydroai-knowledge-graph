"""Find a paper: corpus files (PDF opens in the browser) and legal open-access copies."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from common import EXAMPLE_DOI, api, call, provenance

st.title("📄 Пошук статті та PDF")
st.caption("DOI, посилання видавця (Wiley, Springer, ScienceDirect PII, IOP…), arXiv або paper_id корпусу. "
           "Відкриті копії — лише легальні: OpenAlex, Unpaywall, arXiv.")

q = st.text_input("Запит", value=st.query_params.get("q", EXAMPLE_DOI))
if q:
    st.query_params["q"] = q
    body, secs = call(api().locate, q.strip())
    if body:
        st.subheader(body.get("title") or q)
        st.caption(" · ".join(str(x) for x in (body.get("year"), body.get("venue"), body.get("doi")) if x))
        if body.get("doi_url"):
            st.link_button("Сторінка DOI", body["doi_url"])
        if body.get("in_corpus"):
            st.success(f"У корпусі: `{body['in_corpus']['paper_id']}`")
            for f in body.get("files", []):
                if not f.get("exists"):
                    continue
                c1, c2 = st.columns([1, 4])
                if f["kind"] == "pdf" and f.get("open_url"):
                    c1.link_button("📕 Відкрити PDF", f["open_url"], type="primary")
                else:
                    c1.write(f["kind"].upper())
                c2.code(f.get("windows_path") or f["path"], language=None)
        else:
            st.warning("Статті немає в корпусі.")
        oa = body.get("open_access") or []
        st.markdown(f"**Відкритий доступ:** {body.get('oa_status') or ('так' if oa else 'немає')}")
        if oa:
            st.dataframe(pd.DataFrame(oa)[["kind", "url", "version", "host", "source"]], hide_index=True,
                         width="stretch", column_config={"url": st.column_config.LinkColumn("url")})
        for n in body.get("notes", []):
            st.info(n)
        with st.expander("Відповідь API (JSON)"):
            st.json(body)
        provenance(body, secs)
