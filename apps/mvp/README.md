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
| Граф знань | interactive network (a paper's neighbourhood with citations, or method–sensor pairs), a paper's grounded entity edges, named queries, entity → papers | `/graph/*` |
| Метрики | extraction from text or a paper, the corpus fact table with a histogram, the ontology | `/metrics/*`, `/ontology/*` |
| Тези | theses → evidence rows with PDF links at the page (new tab), crops of that page, the person's checks; contract check of `theses.json` / `atomic_claims.yaml` | `/theses/sets/{project_id}`, `/papers/links`, `/papers/{id}/regions`, `/verifications`, `/theses/validate` |
| Карта досліджень | papers per study-area country on a map; one country's papers with PDF links | `/graph/queries/papers_per_country`, `papers_by_country` |
| Стаття: парсинг | one paper as parsed: formulas rendered next to their PNG crops, Nougat and GROBID tables, figures, sections, entities; every item can be marked | `/papers/{id}/regions`, `/tables`, `/sections`, `/entities`, `/verifications` |
| Шар правди | the person's checks: counts, problems by kind, papers with the most parse problems, full history, CSV | `/verifications`, `/verifications/summary` |

Links (PDF, crops) are signed `/v1/files/<token>` URLs: they open in a new browser tab and live 12 h.

**Human checks.** Marks are written with a separate key that has the `verify` scope, read on the server
side from `~/.config/ghai/verify_env` (`GHAI_VERIFY_KEY`). It belongs to the person; agents never get
it (AGENT_RULES R-ACC-7). Every mark is appended to `verify.human_check`; a correction is a new mark.

As a service: `scripts/systemd/ghai-mvp.service` (installed in `~/.config/systemd/user`).

The first semantic search after the API starts takes up to a minute and a half: the server
loads the 5 GB Chroma index.
