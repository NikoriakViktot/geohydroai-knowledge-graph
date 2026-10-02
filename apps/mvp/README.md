# GeoHydroAI — MVP app (Streamlit)

A client of the Knowledge API: every number comes from an endpoint, through
`clients/python/ghai_client`. The API key is read on the server side (`GHAI_API_KEY`, else
`~/.config/ghai/env`) and never reaches the browser.

```bash
scripts/ghai_api.sh &        # the API on 127.0.0.1:8090
scripts/mvp.sh               # the app on http://127.0.0.1:8502
```

Pages:

| Page | What it shows | Endpoints |
|---|---|---|
| Стан і ендпоінти | dependency health, corpus counts, projections, every documented endpoint with its status, agent rules | `/health`, `/stats`, `/manifest`, `/docs/index`, `/agent-rules` |
| Діагностика ендпоінтів | one sample call per implemented endpoint: status, latency, what came back; CSV download | all implemented |
| Пошук статті та PDF | corpus copy (opens in the browser) and legal open copies | `/locate`, `/files/{token}` |
| Семантичний пошук | passages, papers (several phrasings), similar papers, with coverage | `/search/*` |
| Перевірка цитат | one quotation with numbers and attribution, or a whole `open_citations.json` | `/quotes/verify` |
| Бібліографія | DOI verification, `.bib` audit, DOI → BibTeX, reference lists, manuscript citations | `/doi/verify`, `/bib/*`, `/manuscripts/citations` |
| Граф знань | a paper's neighbourhood with grounded entity edges, named queries, entity → papers | `/graph/*` |
| Метрики | extraction from text or a paper, the corpus fact table with a histogram, the ontology | `/metrics/*`, `/ontology/*` |
| Тези | contract check of `theses.json` / `atomic_claims.yaml` | `/theses/validate` |

The first semantic search after the API starts takes up to a minute and a half: the server
loads the 5 GB Chroma index.
