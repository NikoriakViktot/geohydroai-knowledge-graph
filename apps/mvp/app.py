"""GeoHydroAI Knowledge API — MVP app.

    scripts/mvp.sh            # http://127.0.0.1:8502 (needs the API: scripts/ghai_api.sh)
"""

from __future__ import annotations

import streamlit as st

st.set_page_config(page_title="GeoHydroAI — MVP", page_icon="🌊", layout="wide")

from common import sidebar  # noqa: E402

pages = {
    "Огляд": [
        st.Page("views/overview.py", title="Стан і ендпоінти", icon="🏠", default=True),
        st.Page("views/diagnostics.py", title="Діагностика ендпоінтів", icon="🩺"),
    ],
    "Можливості": [
        st.Page("views/locate.py", title="Пошук статті та PDF", icon="📄"),
        st.Page("views/search.py", title="Семантичний пошук", icon="🔎"),
        st.Page("views/quotes.py", title="Перевірка цитат", icon="✅"),
        st.Page("views/bibliography.py", title="Бібліографія", icon="📚"),
        st.Page("views/graph.py", title="Граф знань", icon="🕸️"),
        st.Page("views/metrics.py", title="Метрики", icon="📊"),
        st.Page("views/theses.py", title="Тези", icon="🧾"),
        st.Page("views/map.py", title="Карта досліджень", icon="🗺️"),
    ],
    "Перевірка": [
        st.Page("views/paper.py", title="Стаття: парсинг", icon="🔬"),
        st.Page("views/truth.py", title="Шар правди", icon="🛡️"),
    ],
}
sidebar()
st.navigation(pages).run()
