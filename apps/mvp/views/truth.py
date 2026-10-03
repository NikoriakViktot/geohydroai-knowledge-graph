"""The layer of truth verified by the person: what has been checked, where the parsing fails, and
the full history of judgements (verify.human_check, append-only)."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from common import PROBLEMS, VERDICTS, api, call, provenance, verify_api, with_links

st.title("🛡️ Шар правди (перевірено мною)")
st.caption("Єдині людські позначки в системі. Записи лише додаються: виправлення — нова позначка, історія "
           "зберігається. Агенти не мають права `verify` (правило R-ACC-7).")
if verify_api() is None:
    st.warning("Ключа з правом `verify` не знайдено: позначки можна лише переглядати.")

project = st.selectbox("Проєкт", ["усі", "floodstate-eo:paper3", "kakhovka-terrain:paper2", "swot-dnipro:paper1",
                                  "kakhovka-report:v1", "article1"])
pid = None if project == "усі" else project
summary, secs = call(api().verify.summary, pid, spinner="Підсумок…")
if summary:
    c = st.columns(4)
    c[0].metric("Перевірено", summary["total"])
    for i, v in enumerate(("correct", "incorrect", "partial")):
        c[i + 1].metric(VERDICTS[v], summary["by_verdict"].get(v, 0))
    if summary["total"]:
        cc = st.columns(2)
        kinds = pd.DataFrame(summary["by_target_kind"]).T.fillna(0).astype(int)
        cc[0].markdown("**За типом об'єкта**")
        cc[0].bar_chart(kinds, horizontal=True)
        if summary["by_problem"]:
            cc[1].markdown("**Проблеми**")
            cc[1].bar_chart(pd.Series({PROBLEMS.get(k, k): v for k, v in summary["by_problem"].items()}), horizontal=True)
        if summary["parse_problem_papers"]:
            st.markdown("**Статті з найбільшою кількістю проблем парсингу** (GROBID / Nougat)")
            bad = pd.DataFrame([{"paper_id": p["paper_id"], "проблем": p["total"],
                                 "які": ", ".join(f"{PROBLEMS.get(k, k)} ×{n}" for k, n in p["problems"].items())}
                                for p in summary["parse_problem_papers"]])
            bad, cfg = with_links(bad, "paper_id")
            st.dataframe(bad, hide_index=True, width="stretch", column_config=cfg)
    provenance(summary, secs)

st.subheader("Усі позначки")
c = st.columns(4)
kind = c[0].selectbox("Тип", ["усі", "formula", "table", "region", "metric_fact", "claim_evidence", "thesis",
                              "entity_edge", "location", "paper"])
verdict = c[1].selectbox("Вердикт", ["усі", *VERDICTS], format_func=lambda v: VERDICTS.get(v, v))
problem = c[2].selectbox("Проблема", ["усі", *[p for p in PROBLEMS if p]], format_func=lambda v: PROBLEMS.get(v, v))
history = c[3].toggle("Уся історія", help="кожна позначка, а не лише остання для об'єкта")
filters = {k: v for k, v in (("target_kind", kind), ("verdict", verdict), ("problem", problem)) if v != "усі"}
if pid:
    filters["project_id"] = pid
items, _ = call(api().verify.list, history=history, limit=5000, **filters, spinner="Позначки…")
if items is not None:
    if not items:
        st.info("Позначок ще немає. Почніть на сторінках «Стаття: парсинг», «Тези» або «Метрики».")
    else:
        df = pd.DataFrame([{"#": c["check_id"], "коли": str(c.get("created_at"))[:16], "тип": c["target_kind"],
                            "об'єкт": c["target_id"], "поле": c.get("field"), "вердикт": VERDICTS.get(c["verdict"]),
                            "проблема": PROBLEMS.get(c.get("problem") or "", c.get("problem")),
                            "правильне": (c.get("corrected") or {}).get("value"), "нотатка": c.get("note"),
                            "paper_id": c.get("paper_id"), "хто": c.get("labeler"), "замінює": c.get("supersedes")}
                           for c in items])
        df, cfg = with_links(df, "paper_id")
        st.dataframe(df, hide_index=True, width="stretch", column_config=cfg)
        st.download_button("CSV", df.to_csv(index=False).encode("utf-8"), "human_checks.csv", "text/csv")
