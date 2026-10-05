"""Gold set of the equation-centric KG (docs_v2/EQUATION_KG_PLAN.md): the person labels frozen
samples; every label is an append-only row in verify.human_check (rule R-ACC-7: agents never label)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import streamlit as st

from common import PROBLEMS, VERDICTS, GHAIError, api, check_badge, current_checks, links, pdf_at, show_formula, verify_api

ROOT = Path(__file__).resolve().parents[3]
GOLD = ROOT / "data" / "gold"
ROLES = {"computes_quantity": "обчислює величину", "defines_metric": "визначає метрику",
         "estimates_parameter": "оцінює параметр", "converts_quantity": "перетворює величину",
         "relates_quantities": "пов'язує величини", "none": "нічого з переліченого"}
RELATIONS = {"EXACT": "той самий запис (EXACT)", "ALGEBRAIC": "алгебраїчно еквівалентні (ALGEBRAIC)",
             "SAME_LAW": "той самий фізичний закон (SAME_LAW)", "RELATED": "пов'язані, але різні (RELATED)",
             "DIFFERENT": "різні (DIFFERENT)", "UNCERTAIN": "не можу визначити (UNCERTAIN)"}
SETS = {"Рівняння": ("equations.jsonl", "equation"), "Параметри": ("parameters.jsonl", "parameter"),
        "Назви величин": ("quantity_names.jsonl", "quantity_name"), "Пари рівнянь": ("equation_pairs.jsonl", "equation_pair"),
        "Факти метрик": ("metric_facts.jsonl", "metric_fact"), "Питання (QA)": ("qa_items.jsonl", "qa_item")}

st.title("🥇 Еталонний набір рівнянь")
st.caption("Розмічає лише людина. Кожна позначка — новий запис у шарі правди; виправлення — нова позначка. "
           "Набір заморожено: те, що показано, і є те, що оцінюється.")

versions = sorted(p.name for p in GOLD.glob("equation_kg_*") if (p / "MANIFEST.json").exists())
if not versions:
    st.info("Еталонний набір ще не заморожено: `python -m src.evaluation.gold_equation_set --version v1`.")
    st.stop()
ver = st.selectbox("Версія набору", versions, index=len(versions) - 1)
vapi = verify_api()
if vapi is None:
    st.warning("Немає ключа з правом `verify` (~/.config/ghai/verify_env): розмітка лише для перегляду.")


@st.cache_data(show_spinner=False)
def load(version: str, name: str) -> list[dict]:
    path = GOLD / version / name
    return [json.loads(line) for line in path.open()] if path.exists() else []


def save(kind: str, target_id: str, field: str, verdict: str, *, corrected: dict | None = None,
         problem: str | None = None, note: str | None = None, paper_id: str | None = None,
         shown: dict | None = None, prev: dict | None = None) -> None:
    if vapi is None:
        st.error("Немає ключа з правом `verify`.")
        return
    check = {"target_kind": kind, "target_id": target_id, "field": field, "paper_id": paper_id,
             "verdict": verdict, "problem": problem or None, "corrected": corrected or None,
             "note": note or None, "shown": {"gold_version": ver, **(shown or {})},
             "supersedes": prev["check_id"] if prev else None}
    try:
        body = vapi.verify.add([check])
        current_checks.clear()
        st.toast(f"Збережено #{body['items'][0]['check_id']}")
        st.rerun()
    except GHAIError as exc:
        st.error(f"{exc.status} {exc.code}: {exc.detail}")


def show_equation(eq: dict, *, compact: bool = False) -> None:
    img = eq.get("image_path")
    if img and (ROOT / img).exists():
        st.image(str(ROOT / img), caption="рівняння в PDF (рамка GROBID)")
    lk = links((eq["paper_id"],)).get(eq["paper_id"])
    url = pdf_at(lk, eq.get("page"))
    st.caption(f"`{eq['paper_id']}` · рівняння {eq.get('equation_number') or '—'} · стор. {eq.get('page') or '—'}"
               + (f" · {eq['section']}" if eq.get("section") else ""))
    if url:
        st.link_button("Сторінка в PDF", url)
    if eq.get("latex"):
        st.markdown("**LaTeX (Nougat)**")
        show_formula(eq["latex"])
    else:
        st.caption("LaTeX немає (Nougat не дав) — лише текст GROBID.")
    st.markdown(f"**Текст GROBID:** `{(eq.get('text_grobid') or '')[:300]}`")
    if not compact:
        with st.expander("Контекст: речення перед і пояснення після"):
            st.write(eq.get("lead_in") or "—")
            st.write(eq.get("clause") or "—")


tab = st.radio("Набір", list(SETS), horizontal=True)
fname, kind = SETS[tab]
items = load(ver, fname)

# ── questions: written by the person ──────────────────────────────────────────
if kind == "qa_item":
    st.subheader("Питання для наскрізної оцінки")
    st.caption("30–50 наукових питань з правильною відповіддю і доказами (стаття, сторінка, таблиця/рівняння). "
               "Вони порівнюють vector RAG, лише граф і граф + RAG.")
    with st.form("qa", clear_on_submit=True):
        q = st.text_area("Питання", height=80)
        a = st.text_area("Правильна відповідь", height=100)
        ev = st.text_area("Докази: paper_id, сторінка, таблиця/рівняння (по рядку)", height=80)
        if st.form_submit_button("Додати питання", type="primary") and q.strip():
            tid = hashlib.sha1(q.strip().encode()).hexdigest()[:16]
            save("qa_item", tid, "question", "correct",
                 corrected={"question": q.strip(), "answer": a.strip(), "evidence": [x for x in ev.splitlines() if x.strip()]})
    try:
        rows = api().verify.list(target_kind="qa_item", limit=500)
        st.metric("Питань", len(rows))
        for r in rows:
            c = r.get("corrected") or {}
            with st.expander(c.get("question", r["target_id"])[:120]):
                st.write(c.get("answer"))
                st.caption("; ".join(c.get("evidence") or []))
    except GHAIError as exc:
        st.error(f"{exc.status} {exc.code}: {exc.detail}")
    st.stop()

if not items:
    st.info("У цій версії набір порожній.")
    st.stop()

ids = tuple(i["target_id"] for i in items)
checks = current_checks(kind, ids)
done = {t for (t, _f) in checks}
st.progress(len(done) / len(items), text=f"розмічено {len(done)} з {len(items)}")
c1, c2 = st.columns([1, 3])
only_open = c1.toggle("Лише нерозмічені", True)
pool = [i for i, it in enumerate(items) if not only_open or it["target_id"] not in done] or list(range(len(items)))
pos = c2.number_input("Елемент", 1, len(pool), 1) - 1
it = items[pool[pos]]
tid, pid = it["target_id"], it.get("paper_id")
st.divider()

if kind == "equation":
    show_equation(it["equation"])
    sysv = it.get("system") or {}
    st.markdown("### 1. Чи відповідає LaTeX зображенню?")
    prev = checks.get((tid, "latex"))
    st.caption(check_badge(prev))
    with st.form(f"lx:{tid}"):
        v = st.radio("Вердикт", list(VERDICTS), format_func=VERDICTS.get, horizontal=True)
        p = st.selectbox("Проблема", list(PROBLEMS), format_func=PROBLEMS.get)
        if st.form_submit_button("Зберегти", type="primary"):
            save(kind, tid, "latex", v, problem=p, paper_id=pid, shown={"latex": it["equation"].get("latex")}, prev=prev)
    st.markdown("### 2. Що обчислює рівняння?")
    st.markdown(f"Система: символ **{sysv.get('lhs_symbol') or '—'}**, призначення: _{sysv.get('purpose') or '—'}_")
    prev = checks.get((tid, "computes"))
    st.caption(check_badge(prev))
    with st.form(f"cp:{tid}"):
        v = st.radio("Відповідь системи", list(VERDICTS), format_func=VERDICTS.get, horizontal=True)
        sym = st.text_input("Правильний символ лівої частини", sysv.get("lhs_symbol") or "")
        qty = st.text_input("Правильна величина (назва)", "")
        role = st.selectbox("Роль", list(ROLES), format_func=ROLES.get)
        note = st.text_input("Нотатка", "")
        if st.form_submit_button("Зберегти", type="primary"):
            save(kind, tid, "computes", v, corrected={"lhs_symbol": sym, "quantity": qty, "computation_role": role},
                 note=note, paper_id=pid, shown={"system": sysv}, prev=prev)

elif kind == "parameter":
    show_equation(it["equation"], compact=True)
    with st.expander("Пояснення після рівняння", expanded=True):
        st.write(it["equation"].get("clause") or "—")
    sysv = it.get("system") or {}
    st.markdown(f"### Параметр **{it['symbol']}**")
    st.markdown(f"Система: _{sysv.get('description') or '—'}_ · одиниця **{sysv.get('unit') or '—'}** · "
                f"значення **{sysv.get('value') or '—'}** · джерело `{sysv.get('source')}`")
    for field, label in (("definition", "Визначення"), ("unit", "Одиниця")):
        prev = checks.get((tid, field))
        with st.form(f"{field}:{tid}"):
            st.markdown(f"**{label}** · {check_badge(prev)}")
            v = st.radio("Вердикт", list(VERDICTS), format_func=VERDICTS.get, horizontal=True, key=f"v{field}{tid}")
            p = st.selectbox("Проблема", list(PROBLEMS), format_func=PROBLEMS.get, key=f"p{field}{tid}")
            corr = st.text_input("Правильне значення (якщо інше)", "", key=f"c{field}{tid}")
            if st.form_submit_button("Зберегти", type="primary"):
                save(kind, tid, field, v, corrected={field: corr} if corr else None, problem=p, paper_id=pid,
                     shown={"system": sysv, "symbol": it["symbol"]}, prev=prev)

elif kind == "quantity_name":
    st.markdown(f"### «{tid}»")
    st.caption(f"зустрічається в {it.get('frequency', '?')} параметрах")
    prev = checks.get((tid, "canonical"))
    st.caption(check_badge(prev))
    with st.form(f"q:{tid}"):
        v = st.radio("Це назва фізичної/наукової величини?", list(VERDICTS), format_func=VERDICTS.get, horizontal=True)
        canon = st.text_input("Канонічна назва величини (англ., як в онтології)", tid)
        dim = st.text_input("Розмірність, напр. L3 T-1 (або 1 для безрозмірної)", "")
        unit = st.text_input("Типова одиниця, напр. m3 s-1", "")
        if st.form_submit_button("Зберегти", type="primary"):
            save(kind, tid, "canonical", v, corrected={"quantity": canon, "dimension": dim, "unit": unit},
                 problem=None if v == "correct" else "quantity_wrong", prev=prev)

elif kind == "equation_pair":
    a, b = st.columns(2)
    with a:
        st.markdown("#### A")
        show_equation(it["a"], compact=True)
    with b:
        st.markdown("#### B")
        show_equation(it["b"], compact=True)
    prev = checks.get((tid, "relation"))
    st.caption(check_badge(prev) + (f" · {(prev.get('corrected') or {}).get('relation')}" if prev else ""))
    with st.form(f"pair:{tid}"):
        rel = st.radio("Відношення", list(RELATIONS), format_func=RELATIONS.get)
        sure = st.toggle("Впевнений", True)
        note = st.text_input("Нотатка (напр. «modified Manning», «інша параметризація»)", "")
        if st.form_submit_button("Зберегти", type="primary"):
            save(kind, tid, "relation", "correct" if sure and rel != "UNCERTAIN" else "unsure",
                 corrected={"relation": rel}, note=note, shown={"a": it["a"]["equation_id"], "b": it["b"]["equation_id"]},
                 prev=prev)

elif kind == "metric_fact":
    sysv = it.get("system") or {}
    st.markdown(f"### {sysv.get('metric')} = **{sysv.get('value')}**")
    st.caption(f"клітинка `{sysv.get('raw_cell')}` · заголовок `{sysv.get('col_header')}` · таблиця `{sysv.get('table_id')}` · "
               f"стор. {sysv.get('page')}")
    url = pdf_at(links((pid,)).get(pid), sysv.get("page"))
    if url:
        st.link_button("Сторінка в PDF", url)
    prev = checks.get((tid, "value"))
    st.caption(check_badge(prev))
    with st.form(f"mf:{tid}"):
        v = st.radio("Метрика і значення правильні?", list(VERDICTS), format_func=VERDICTS.get, horizontal=True)
        p = st.selectbox("Проблема", list(PROBLEMS), format_func=PROBLEMS.get)
        corr = st.text_input("Правильне значення / метрика (якщо інше)", "")
        if st.form_submit_button("Зберегти", type="primary"):
            save(kind, tid, "value", v, corrected={"value": corr} if corr else None, problem=p, paper_id=pid,
                 shown={"system": sysv}, prev=prev)
