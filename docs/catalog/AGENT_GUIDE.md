# Paper catalog API — guide for AI agents

> **Українською.**
> - Це інструкція для будь-якого ІІ-агента (Claude Code, MCP-клієнт, скрипт з LLM), який читає картки праць із сервера-каталогу.
> - Картка — це посилання на працю (DOI, OpenAlex, відкритий PDF, якщо є) плюс короткий аналіз: методи, сенсори, дані, країни дослідження, числові результати з таблиць.
> - Кожне значення має статус: `human_verified` (перевірено людиною), `model` (витягнуто автоматично й пройшло фільтр якості), `source` (метадані від OpenAlex / видавця).
> - Головні правила: завжди давати посилання на працю; ніколи не видавати `model` за перевірене; відсутність у картці ≠ відсутність у статті.

**Applies to**: any agent or script that reads the catalog over HTTP.
**Keywords**: **MUST** / **MUST NOT** are hard rules; **SHOULD** may be broken only with a stated reason.
**Version**: API 1.0, rules `catalog-rules-v1` (the served version is in `GET /v1/release`).

---

## 1. What the catalog is — and is not

The catalog serves one **card** per paper of the GeoHydroAI corpus (flood mapping, hydrology, remote sensing, ≈ 4 700 cards). The cards come from a snapshot built on the research workstation and copied to the server. The server has:

- **no PDFs**;
- **no abstracts or full text**;
- **no vector search, graph or LLM**.

**What a card holds:**

- bibliographic metadata and links;
- extracted entities: methods, sensors, data, study countries;
- up to 12 numeric results taken from the paper's tables.

**Every card went through a quality filter** ([§6](#6-quality-filter-what-was-hidden-and-why)). Values that failed a rule are **not in the card**; only their count and reason are. Hence:

- A card is a **pointer** to a paper with a few reliable facts, not a summary of it.
- **Absence of a method, sensor or result in a card does not mean the paper lacks it.**

---

## 2. Connecting

| Item | Value |
|---|---|
| Base URL | `https://geohydroai.org/catalog` (all paths below are relative to it: `https://geohydroai.org/catalog/v1/cards`) |
| Auth | header `X-API-Key: <key>` on every `/v1/*` request, if the server has keys enabled. `/health`, `/agent-guide`, `/llms.txt`, `/openapi.json` are open |
| Format | JSON, UTF-8. Read-only: there are only `GET` endpoints |
| Machine description | `GET /openapi.json` (OpenAPI 3), interactive docs at `/docs`; requests are `GET` only |
| Rate | no hard limit; keep ≤ 10 requests/s and page with `limit` ≤ 200 |

**Where the key lives:**

- Store it in an environment variable, e.g. `CATALOG_API_KEY`, and the base URL in `CATALOG_API_URL=https://geohydroai.org/catalog`.
- **MUST NOT** print the key, write it into files under version control, or include it in answers.

**First call** — check that the server is up and which snapshot it serves:

```bash
curl -s "$CATALOG_API_URL/health"
curl -s -H "X-API-Key: $CATALOG_API_KEY" "$CATALOG_API_URL/v1/release"
```

```json
{"rules_version": "catalog-rules-v1", "built_at": "2026-10-09T…Z", "git_commit": "…",
 "counts": {"papers": 5812, "cards": 4742, "excluded": 1070, "with_analysis": 4736,
            "with_open_access_link": 665, "human_verified_metadata": 0, "retracted": 0},
 "cards_sha256": "…"}
```

**Recording provenance:** when results are written down, record `rules_version` and `cards_sha256`. They identify exactly which data the statements came from.

---

## 3. Endpoints

### 3.1 `GET /v1/cards` — search and list

Returns **summaries**, not full cards. To read the analysis, follow up with [`/v1/cards/{paper_id}`](#32-get-v1cardspaper_id--one-full-card).

| Parameter | Type | Meaning |
|---|---|---|
| `q` | string | Words that must **all** occur in title, authors, venue, entity names, countries or topics. Case-insensitive, whole words, no stemming (`flood` ≠ `floods`) |
| `method` | string | Canonical id (`method.hec_ras`) or exact display name (`HEC-RAS`). A substring of the id also matches (`hec_ras` matches `method.hec_ras_2d`) |
| `sensor` | string | Same, for sensors (`sensor.sentinel`, `sensor.landsat`) |
| `data` | string | Same, for data sets (`data.dem`, `data.srtm`) |
| `country` | string | Study-area country, exact name, case-insensitive (`Ukraine`, `USA`, `UK`) |
| `metric` | string | Card has a numeric result of this metric: `metric.nse`, `nse` or the label `Nash-Sutcliffe Efficiency` |
| `year_from`, `year_to` | int | Publication year range, inclusive |
| `verified_only` | bool | Only cards with at least one `human_verified` value |
| `open_access_only` | bool | Only cards with an open-access link |
| `sort` | `cited` (default) \| `year` \| `title` | `cited` = most cited first |
| `limit` | 1–200, default 20 | Page size |
| `offset` | ≥ 0 | Page start |

**Response:**

```json
{"total": 37, "limit": 20, "offset": 0,
 "items": [{"paper_id": "10.1016_j.rse.2013.08.029",
            "title": "Automated Water Extraction Index: …", "year": 2014,
            "venue": "Remote Sensing of Environment", "authors": ["Gudina Legese Feyisa", "…"],
            "cited_by_count": 2003,
            "links": {"doi": "https://doi.org/10.1016/j.rse.2013.08.029",
                      "openalex": "https://openalex.org/W1995581599",
                      "open_access": null, "open_access_status": "closed"},
            "metadata_status": "source", "retracted": false,
            "methods": ["Maximum Likelihood"], "sensors": ["Landsat Satellite Program", "…"],
            "study_countries": ["Switzerland", "China"], "results": 3}]}
```

In a summary, `methods` and `sensors` hold at most 5 names each, and `results` is a **count**.

**Paging:** repeat with `offset += limit` while `offset < total`.

### 3.2 `GET /v1/cards/{paper_id}` — one full card

- `paper_id` is the corpus identifier from a summary, used **verbatim**.
- It is often a DOI slug, e.g. `10.1016_j.rse.2013.08.029`. **MUST NOT** turn it into a DOI by replacing `_` with `/`; use `links.doi` instead.
- An unknown `paper_id` returns `404`.

The full card is [§4](#4-the-card).

### 3.3 `GET /v1/facets?top=50`

The most frequent values, with card counts, for:

- `methods`, `sensors`, `data`;
- `study_countries`, `metrics`, `years`.

Use it to discover valid filter values before searching: canonical ids are not guessable.

```json
{"methods": [{"value": "method.hec_hms", "cards": 403}, …], "sensors": [{"value": "sensor.landsat", "cards": 392}, …],
 "data": [{"value": "data.dem", "cards": 1169}, …], "study_countries": [{"value": "China", "cards": 687}, …],
 "metrics": [{"value": "metric.nse", "cards": 132}, …], "years": [{"value": 2023, "cards": 433}, …]}
```

### 3.4 Open endpoints

| Endpoint | Returns |
|---|---|
| `GET /health` | `{"status": "ok", "cards": 4742, "rules_version": "…"}` |
| `GET /agent-guide` | this document (Markdown) |
| `GET /llms.txt` | short machine index |
| `GET /openapi.json` | OpenAPI 3 schema |

### 3.5 Errors

| Code | Meaning | What to do |
|---|---|---|
| `401` | Key missing or wrong | Check `CATALOG_API_KEY`; do not retry in a loop |
| `404` | No card with that `paper_id` | The paper is not in the catalog (it may have been excluded, see §6.1) — say so; do not invent a card |
| `422` | Invalid parameter (e.g. `limit=500`, `sort=foo`) | Fix the parameter; `detail` names it |
| `5xx` / timeout | Server problem | Retry once after a few seconds; then report that the catalog is unavailable |

---

## 4. The card

| Field | Type | Meaning |
|---|---|---|
| `paper_id` | string | Corpus id (stable within a release) |
| `title`, `year`, `venue` | string, int\|null, string\|null | Bibliographic data |
| `authors` | string[] | At most 10; `authors_truncated: true` when the paper has more |
| `cited_by_count` | int\|null | OpenAlex citation count at build time |
| `links.doi` | url\|null | `https://doi.org/…` — **the primary link** |
| `links.openalex` | url\|null | OpenAlex work page |
| `links.open_access` | url\|null | Legal open-access copy (OpenAlex/Unpaywall), when known |
| `links.open_access_status` | string\|null | `gold`, `green`, `hybrid`, `bronze`, `diamond`, `closed` or null (unknown) |
| `metadata_status` | `source` \| `human_verified` | Whether a person checked the bibliographic data |
| `retracted` | bool | Retracted per OpenAlex — **MUST** be mentioned whenever the paper is cited |
| `topics[]` | `{name, score, status:"source"}` | OpenAlex topics with score ≥ 0.9, at most 3. Coarse; do not treat as the paper's subject |
| `analysis` | object\|null | null when the paper was not parsed (`quality.analysis_available: false`) |
| `analysis.methods[]`, `sensors[]`, `data[]` | `{id, name, mentions, status}` | Ontology concept, display name, number of mentions in the paper text (null if unknown). Sorted: human-verified first, then by mentions |
| `analysis.study_countries[]` | `{name, status}` | Countries named as study area in the text, at most 8 |
| `analysis.author_countries[]` | `{name, status}` | Countries of author affiliations. **Not** the study area |
| `analysis.results[]` | see below | Numeric results from the paper's tables, at most 12 |
| `analysis.task`, `analysis.study_type` | object\|null | **Currently always null.** The labels exist but are withheld until validated (`label_not_validated`) |
| `quality.rules_version` | string | Rules the card was filtered with |
| `quality.human_checks` | int | Number of human judgements on this paper |
| `quality.hidden` | `{ "<field>:<reason>": count }` | What the filter removed from this card (§6) |

**A result** (`analysis.results[]`):

```json
{"metric": "metric.kappa", "metric_label": "Cohen's Kappa", "value": 0.93, "unit": null,
 "context": "AWEI", "table": "Table 4", "page": 8, "fact_id": "f1d1dd4e4c09ea65", "status": "model"}
```

| Field | Meaning |
|---|---|
| `metric` | Canonical metric id |
| `value` | As a number. Ratio metrics (OA, F1, IoU, kappa, NSE, KGE, R²) are on a 0–1 scale. A `%` in the table was converted, so 94.2 % becomes 0.942 |
| `unit` | Recognised unit (`m`, `mm`, `m³/s`, `%` …). null for dimensionless metrics |
| `context` | The table **row label**: which method, site, model or period the value belongs to (`"AWEI"`, `"Kratie · calibration"`) |
| `table`, `page` | Where to check it in the paper |

**What a result is not:**

- **Not** necessarily the paper's headline result. It is one cell of a table.
- Several rows of one table can appear. For example, the three kappa values above compare AWEI with MNDWI and ML in one test site; they are not three studies.

---

## 5. Statuses and how to use them

| Status | Means | How to present it |
|---|---|---|
| `human_verified` | A person checked this value against the paper | May be stated as fact, with the link |
| `model` | Extracted automatically and passed every quality rule | **MUST** be presented as automatically extracted ("the catalog lists…", "according to the automatic extraction…"). **SHOULD** advise checking the paper (give `table`/`page` for results) |
| `source` | Bibliographic data or topics from OpenAlex / the publisher | Normal bibliographic trust |

As of `catalog-rules-v1`, almost every analysis value is `model`. Human checks are being added over time.

---

## 6. Quality filter: what was hidden and why

### 6.1 Papers without a card (`excluded`)

| Reason | Meaning |
|---|---|
| `identity_duplicate`, `identity_not_a_paper` | Duplicate of another corpus record, or not a paper |
| `no_title` | No reliable title in any source |
| `no_link` | Neither a DOI nor an OpenAlex id: the catalog only publishes linkable papers |
| `human_rejected_metadata` | A person marked the metadata as wrong |

### 6.2 Values hidden inside a card (`quality.hidden`)

| Key (`field:reason`) | Meaning |
|---|---|
| `methods/sensors/data:not_in_ontology` | Extracted name has no ontology concept |
| `…:role_mentioned` | Named in passing (e.g. in related work), not used in the study |
| `…:low_score` | Extractor confidence < 0.5 |
| `…:not_in_text` | Concept not found in the paper text |
| `…:generic_term` | Common word matched as a concept (`gamma`, `transform`, `histogram`, `radar`) |
| `…:ambiguous_acronym` | Acronym with several senses (`ML`, `CV`, `SCS`, `ALI`) whose long form is absent from the evidence |
| `…:over_limit` | More than 12 items; the least mentioned were cut |
| `study_countries:name_not_in_evidence` | Country name not literally in its evidence sentence, or part of an agency name ("United States Geological Survey") |
| `results:out_of_range` | Value impossible for the metric (kappa 1.4, NSE 1.3) |
| `results:metric_without_range` | Metric without a known valid range (MSE, RSR…): cannot be checked |
| `results:unit_unknown` | Dimensional metric (RMSE, MAE) without a recognised unit |
| `results:no_row_label` | Table row has no readable label, so the value cannot be attributed |
| `results:low_confidence` | Table parse confidence < 0.7 |
| `task/study_type:label_not_validated` | Label withheld (see §4) |
| `…:human_rejected` | A person marked this value wrong |

**Consequence for agents:** **MUST NOT** conclude "paper X does not use method Y" or "no paper reports NSE for Z" from the catalog. The right formulation is: "the catalog card does not list it".

---

## 7. Rules for agents

1. **MUST** give the link for every paper mentioned: `links.doi`, else `links.openalex`. Prefer `links.open_access` when the reader needs the text.
2. **MUST** distinguish statuses (§5). Never write a `model` value as if a person had checked it.
3. **MUST NOT** invent values, papers, DOIs or links. A paper not returned by the API is not in the catalog. Say so.
4. **MUST NOT** fill fields the card does not have. For example, do not write an abstract from the title, or derive the study area from `author_countries`.
5. **MUST** report counts as catalog counts: "37 cards in the catalog list HEC-RAS". Do not phrase them as "37 papers use HEC-RAS": the corpus is a selection and the filter hides values.
6. **MUST** mention `retracted: true` whenever such a paper is cited.
7. **SHOULD** quote numeric results with metric, value, unit, row context and location:
   - Example: "Cohen's κ = 0.93 for AWEI (Table 4, p. 8)".
   - **MUST NOT** average or compare values across papers without saying they come from different tables, sites and set-ups.
8. **SHOULD** discover filter values with `/v1/facets` instead of guessing canonical ids.
9. **SHOULD** record `rules_version` and `cards_sha256` from `/v1/release` with any saved result.
10. **MUST NOT** send the API key anywhere except the `X-API-Key` header of this API.

---

## 8. Recipes

### 8.1 curl

```bash
H="X-API-Key: $CATALOG_API_KEY"
# HEC-RAS 2D studies in Ukraine, newest first
curl -s -H "$H" "$CATALOG_API_URL/v1/cards?method=method.hec_ras_2d&country=Ukraine&sort=year"
# papers with a Landsat sensor that report NSE in a table
curl -s -H "$H" "$CATALOG_API_URL/v1/cards?metric=nse&sensor=landsat&limit=50"
# free text + open access only
curl -s -H "$H" "$CATALOG_API_URL/v1/cards?q=kakhovka%20dam&open_access_only=true"
# one card
curl -s -H "$H" "$CATALOG_API_URL/v1/cards/10.1016_j.rse.2013.08.029"
```

### 8.2 Python (requests)

```python
import os, requests

BASE = os.environ["CATALOG_API_URL"].rstrip("/")
S = requests.Session()
S.headers["X-API-Key"] = os.environ.get("CATALOG_API_KEY", "")

def search(**params):
    """All matching summaries, following pages."""
    params = {"limit": 200, **params}
    offset, out = 0, []
    while True:
        r = S.get(f"{BASE}/v1/cards", params={**params, "offset": offset}, timeout=30)
        r.raise_for_status()
        page = r.json()
        out += page["items"]
        offset += page["limit"]
        if offset >= page["total"]:
            return out

def card(paper_id):
    r = S.get(f"{BASE}/v1/cards/{paper_id}", timeout=30)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.json()

release = S.get(f"{BASE}/v1/release", timeout=30).json()
for s in search(method="method.hec_ras", country="Ukraine"):
    c = card(s["paper_id"])
    link = c["links"]["doi"] or c["links"]["openalex"]
    for res in (c["analysis"] or {}).get("results", []):
        print(c["title"], link, res["metric_label"], res["value"], res["unit"], res["context"],
              res["table"], res["page"], res["status"])
print("catalog", release["rules_version"], release["cards_sha256"][:12])
```

### 8.3 As a tool for an LLM agent

Expose two tools to the model and let it call them:

```json
[
  {"name": "catalog_search",
   "description": "Search the GeoHydroAI paper catalog. Returns paper summaries with links. Filters: q (all words), method, sensor, data (canonical id or name), country, metric, year_from, year_to, open_access_only, verified_only, sort (cited|year|title), limit, offset. Values with status 'model' are automatic extractions, not verified facts. A missing value means 'not listed in the card', never 'not in the paper'.",
   "input_schema": {"type": "object", "properties": {
     "q": {"type": "string"}, "method": {"type": "string"}, "sensor": {"type": "string"},
     "data": {"type": "string"}, "country": {"type": "string"}, "metric": {"type": "string"},
     "year_from": {"type": "integer"}, "year_to": {"type": "integer"},
     "open_access_only": {"type": "boolean"}, "verified_only": {"type": "boolean"},
     "sort": {"type": "string", "enum": ["cited", "year", "title"]},
     "limit": {"type": "integer", "minimum": 1, "maximum": 200}, "offset": {"type": "integer", "minimum": 0}}}},
  {"name": "catalog_card",
   "description": "Full card of one paper by paper_id (from catalog_search): links, methods, sensors, data, study countries, numeric results from tables with table/page, each with a status (human_verified | model | source).",
   "input_schema": {"type": "object", "properties": {"paper_id": {"type": "string"}}, "required": ["paper_id"]}}
]
```

**Implementation:** `catalog_search` → `GET /v1/cards`, `catalog_card` → `GET /v1/cards/{paper_id}`. Return the JSON unchanged.

**System prompt:** put §5 and §7 of this guide into the agent's system prompt.

### 8.4 Claude Code

Add to the project's `CLAUDE.md`:

```markdown
## Paper catalog
Search literature through the catalog API ($CATALOG_API_URL, key in $CATALOG_API_KEY, header X-API-Key).
Read $CATALOG_API_URL/agent-guide before the first call and follow its §7 rules.
```

The key is set in the shell environment (e.g. `~/.config/ghai/catalog_env`, mode 600), never in the repository.

---

## 9. Freshness

The catalog is a snapshot. A new snapshot is published after the corpus changes. When one is published, `/v1/release` shows a new `built_at` and `cards_sha256`, and `paper_id`s stay stable. Within one release, results are fully reproducible.
