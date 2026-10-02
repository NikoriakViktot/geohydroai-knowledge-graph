"""Diagnostics: one sample call per implemented endpoint — status, latency, what came back."""

from __future__ import annotations

import time

import pandas as pd
import streamlit as st

from common import EXAMPLE_DOI, GHAIError, api

st.title("🩺 Діагностика ендпоінтів")
st.caption("Кожен реалізований ендпоінт викликається зі зразковими даними. Перший семантичний пошук після запуску "
           "API довгий (~16 с): сервер завантажує індекс Chroma (5 ГБ).")

BIB = ("@article{Biancamaria_2016, author={Biancamaria, Sylvain and Lettenmaier, Dennis P. and Pavelsky, Tamlin M.},\n"
       " title={The SWOT Mission and Its Capabilities for Land Hydrology}, journal={Surveys in Geophysics}, year={2016},\n"
       " volume={37}, pages={307--337}, doi={10.1007/s10712-015-9346-y}}")
MANUSCRIPT = "# Intro\nSWOT observes rivers (Biancamaria et al. 2016; Missing 2020)."


def checks(ctx: dict) -> list[tuple]:
    pid = lambda: ctx.get("paper_id", "10.1016_j.rse.2017.09.032")  # noqa: E731
    return [
        ("system", "GET", "health", {}, None, lambda b: ", ".join(f"{k}={v['status']}" for k, v in b["dependencies"].items())),
        ("system", "GET", "capabilities", {}, None, lambda b: f"{len(b['implemented_endpoints'])} реалізовано"),
        ("system", "GET", "stats", {}, None, lambda b: f"{b['papers']['total']} статей"),
        ("system", "GET", "manifest", {}, None, lambda b: b["collection"]),
        ("system", "GET", "agent-rules", {}, None, lambda b: f"{len(b['rules'])} правил"),
        ("system", "GET", "docs/index", {}, None, lambda b: f"{b['counts']}"),
        ("system", "GET", "schemas/PaperIdentity.v1", {}, None, lambda b: b.get("title", "?")),
        ("papers", "GET", "papers/resolve", {"doi": EXAMPLE_DOI}, None,
         lambda b: ctx.setdefault("paper_id", b["paper"]["paper_id"])),
        ("papers", "POST", "papers/resolve-batch", {}, {"items": [{"doi": EXAMPLE_DOI}, {"doi": "10.9999/none"}]},
         lambda b: str(b["summary"])),
        ("papers", "GET", lambda: f"papers/{pid()}", {}, None, lambda b: b["identity_status"]),
        ("papers", "GET", lambda: f"papers/{pid()}/sections", {}, None, lambda b: f"{len(b['sections'])} розділів"),
        ("papers", "GET", lambda: f"papers/{pid()}/text", {"q": "VIIRS flood"}, None,
         lambda b: f"{len(b['spans'])} уривків"),
        ("papers", "GET", lambda: f"papers/{pid()}/references", {}, None, lambda b: str(b["counts"])),
        ("papers", "GET", lambda: f"papers/{pid()}/tables", {}, None, lambda b: f"{len(b['tables'])} таблиць"),
        ("papers", "GET", lambda: f"papers/{pid()}/entities", {}, None,
         lambda b: f"{len(b['methods'])} методів, {len(b['sensors'])} сенсорів"),
        ("acquisition", "GET", "locate", {"q": EXAMPLE_DOI}, None,
         lambda b: (ctx.__setitem__("open_url", next((f["open_url"] for f in b["files"] if f.get("open_url")), None))
                    or f"в корпусі: {bool(b['in_corpus'])}, OA: {b['oa_status']}")),
        ("acquisition", "GET", "files/{token}", None, None, None),
        ("evidence", "POST", "quotes/verify", {}, {"items": [{"source": "10.5194/nhess-19-2405-2019",
                                                               "quote": "does not accurately capture inundated cells"}]},
         lambda b: b["items"][0]["status"]),
        ("evidence", "POST", "theses/validate", {}, {"kind": "theses", "project_id": "floodstate-eo:paper3",
                                                      "document": [{"id": "T1", "thesis": "SAR misses water under canopy."}]},
         lambda b: f"valid={b['valid']}"),
        ("bibliography", "GET", f"doi/{EXAMPLE_DOI}", {}, None, lambda b: (b.get("title") or "")[:60]),
        ("bibliography", "POST", "doi/verify", {}, {"entries": [{"bibtex": BIB}]}, lambda b: b["results"][0]["verdict"]),
        ("bibliography", "POST", "bib/format", {}, {"dois": [EXAMPLE_DOI]}, lambda b: b["entries"][0]["key"]),
        ("bibliography", "POST", "bib/render", {}, {"bibtex": BIB, "style": "apa"},
         lambda b: b["references"][0]["text"][:60]),
        ("bibliography", "POST", "bib/audit", {}, {"bibtex": BIB}, lambda b: str(b["summary"])),
        ("bibliography", "POST", "manuscripts/citations", {}, {"manuscript": MANUSCRIPT, "bibtex": BIB},
         lambda b: str(b["summary"])),
        ("graph", "GET", f"graph/papers/{EXAMPLE_DOI}", {}, None,
         lambda b: f"{len(b.get('methods') or [])} методів, цитує {b['counts']['cites_out']}"),
        ("graph", "GET", f"graph/papers/{EXAMPLE_DOI}/citations", {"limit": 5}, None, lambda b: str(b["counts"])),
        ("graph", "GET", "graph/entities/Method/method.hand/papers", {"limit": 3}, None,
         lambda b: f"{b['count']} статей (grounded)"),
        ("graph", "GET", "graph/queries", {}, None, lambda b: f"{len(b['queries'])} запитів"),
        ("graph", "POST", "graph/queries/top_methods", {}, {"params": {}, "limit": 3},
         lambda b: ", ".join(str(r[0]) for r in b["rows"])),
        ("metrics", "POST", "metrics/extract", {}, {"text": "The model reached NSE = −0.27 and an overall accuracy of 94.2 %."},
         lambda b: ", ".join(f"{f['metric']}={f['value']}" for f in b["facts"])),
        ("metrics", "GET", "metrics/facts", {"metric": "NSE", "min": 0.8, "limit": 5}, None,
         lambda b: f"{b['summary']['n']} фактів, {b['summary']['papers']} статей"),
        ("metrics", "GET", "metrics/ontology", {}, None, lambda b: f"{len(b['metrics'])} метрик"),
        ("metrics", "POST", "ontology/normalize", {}, {"terms": [{"text": "Sentinel-1"}, {"text": "HEC RAS"}]},
         lambda b: ", ".join(str(r["canonical_id"]) for r in b["results"])),
        ("metrics", "GET", "ontology/entities", {"type": "metric", "q": "nash"}, None, lambda b: f"{b['count']} збігів"),
        ("search", "POST", "search/chunks", {}, {"query": "near real-time flood detection with VIIRS", "k": 3},
         lambda b: f"{(b['hits'][0]['paper'] or {}).get('paper_id')} ({b['hits'][0]['score']})" if b["hits"] else "0"),
        ("search", "POST", "search/papers", {}, {"queries": ["HAND flood inundation mapping"], "k": 3},
         lambda b: ", ".join(p["paper_id"][:28] for p in b["papers"])),
        ("search", "POST", "search/similar", {}, {"doi": EXAMPLE_DOI, "k": 3},
         lambda b: ", ".join(p["paper_id"][:28] for p in b["papers"])),
    ]


def run_one(ctx: dict, group, method, path, params, body, summarise) -> dict:
    path = path() if callable(path) else path
    t0 = time.time()
    if path == "files/{token}":
        url = ctx.get("open_url")
        if not url:
            return {"група": group, "ендпоінт": f"{method} /{path}", "статус": "—", "мс": 0, "результат": "немає посилання"}
        r = api()._http.get(url)
        ok = r.status_code == 200 and r.headers.get("content-type") == "application/pdf"
        return {"група": group, "ендпоінт": f"{method} /{path}", "статус": r.status_code,
                "мс": round((time.time() - t0) * 1000), "результат": f"{r.headers.get('content-type')}, "
                f"{int(r.headers.get('content-length', 0)) // 1024} КБ", "ok": ok}
    try:
        b = api().request(method, path, params=params, json=body)
        summary = summarise(b) if summarise else ""
        return {"група": group, "ендпоінт": f"{method} /{path}", "статус": 200, "мс": round((time.time() - t0) * 1000),
                "результат": str(summary)[:120], "ok": True}
    except GHAIError as exc:
        return {"група": group, "ендпоінт": f"{method} /{path}", "статус": exc.status or "—",
                "мс": round((time.time() - t0) * 1000), "результат": f"{exc.code}: {exc.detail[:100]}", "ok": False}
    except Exception as exc:  # a summary that did not fit the answer
        return {"група": group, "ендпоінт": f"{method} /{path}", "статус": "?", "мс": round((time.time() - t0) * 1000),
                "результат": f"{type(exc).__name__}: {exc}"[:120], "ok": False}


if st.button("▶ Запустити перевірку", type="primary"):
    ctx: dict = {}
    rows = []
    suite = checks(ctx)
    bar = st.progress(0.0, text="…")
    for i, check in enumerate(suite, start=1):
        bar.progress(i / len(suite), text=f"{check[1]} /{check[2]() if callable(check[2]) else check[2]}")
        rows.append(run_one(ctx, *check))
    bar.empty()
    st.session_state["diag"] = pd.DataFrame(rows)
    st.session_state["diag_at"] = time.strftime("%Y-%m-%d %H:%M:%S")

df = st.session_state.get("diag")
if df is not None:
    ok = int(df["ok"].fillna(False).sum())
    c = st.columns(4)
    c[0].metric("Пройшло", f"{ok} / {len(df)}")
    c[1].metric("Медіана, мс", int(df["мс"].median()))
    c[2].metric("Найдовший виклик, мс", int(df["мс"].max()))
    c[3].metric("Перевірено", st.session_state.get("diag_at", ""))
    df_view = df.assign(**{"": df["ok"].map({True: "✅", False: "❌"}).fillna("—")})
    st.dataframe(df_view[["", "група", "ендпоінт", "статус", "мс", "результат"]], hide_index=True,
                 width="stretch", height=min(38 * (len(df) + 1), 900))
    st.bar_chart(df.groupby("група")["мс"].median(), horizontal=True)
    st.download_button("Завантажити CSV", df.to_csv(index=False).encode("utf-8"), "ghai_endpoint_check.csv",
                       "text/csv")
else:
    st.info("Натисніть «Запустити перевірку».")
