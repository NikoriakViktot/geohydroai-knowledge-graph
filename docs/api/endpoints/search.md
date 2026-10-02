# Search — semantic, paper-level, similar, hybrid

**Backing data**:
- ChromaDB collection `flood_papers_768d_v2`. It is being rebuilt on 2026-10-02 with unique chunk ids; v1 `flood_papers_768d` had abstract chunks for only 2,462 of 4,955 papers.
- Model `allenai/specter2_base@3447645e` (full revision `3447645e1def…`): 768-d, mean pooling, no adapter. The same encoder embeds queries and documents. The API loads it on the first search (CPU, a few seconds).
- Filters on DOI, year, cohort and identity resolve through `core.paper` in Postgres. Chroma stores only chunk metadata, so the resulting paper set goes into Chroma as `paper_id $in`, or as the shorter `$nin` of the papers left out.
- The collection is opened in-process for reading. Chroma server mode (one owner of the index) is planned (WP 1.2).

**Chunk types** in the collection: `abstract`, `sentence` (95 %), `paragraph`, `section`, `figure`, `table`, `formula`.

All endpoints: **Scope** `read` · **Mode** S · p95 latency target 1.5 s (warm).

---

## `POST /search/chunks`
- **Status**: implemented (2026-10-02)
- **Purpose**: find passages that say something. This is the basic evidence search.

**Request**:

| Field | Type | Default | Notes |
|---|---|---|---|
| `query` | string | — | natural language; 3–1,000 chars |
| `k` | int | 20 | ≤ 200 |
| `filters.year_from` / `year_to` | int? | — | no hidden upper cap (the old dashboard capped at 2025) |
| `filters.paper_ids` / `filters.dois` | string[]? | — | restrict to these works |
| `filters.chunk_types` | string[]? | all | e.g. `["abstract", "paragraph"]` |
| `filters.sections` | string[]? | — | prefix match on the section title, case-insensitive. Applied after retrieving `5 × k` candidates, so fewer than `k` hits may come back |
| `filters.exclude_cohorts` | string[] | `[]` | e.g. `["paper_3"]` to drop the harvested Kakhovka-report cohort |
| `filters.identity_status` | string[] | `["ok", "no_doi", "title_doi_mismatch"]` | `truncated_json` may be added; `duplicate` and `not_a_paper` are refused (`422`): they are never searched |
| `min_score` | float? | — | drop hits below this similarity |
| `project_id` | string? | — | ties `retrieval_validity` to that project's acceptance gate |

**Response 200**:

| Field | Type | Notes |
|---|---|---|
| `hits` | `ChunkHit[]` | best first |
| `coverage` | `Coverage` | `{papers_in_slice, chunks_in_slice, papers_without_chunks, filters_applied, excluded_cohorts, note?}`: what was actually searched, counted from the index's own catalogue |
| `retrieval_validity` | `NOT_MEASURED \| MEASURED_BELOW_GATE \| VALIDATED` | `NOT_MEASURED` until a recall measurement exists for (collection, manifest), with the reason in `validity_detail` |
| `provenance` | `Provenance` | includes `collection`, `embedding_model` |

```json
{"query": "HAND methods do not preserve hydraulic connectivity", "k": 5,
 "filters": {"year_from": 2015, "chunk_types": ["sentence", "paragraph"]}}
```
```json
{"hits": [{"chunk_id": "a41c…", "score": 0.83, "chunk_type": "sentence",
           "paper": {"paper_id": "annurev-fluid-030121-113138", "doi": "10.1146/annurev-fluid-030121-113138",
                     "title": "Flood Inundation Prediction", "year": 2022, "identity_status": "ok"},
           "section": "HAND AND BATHTUB APPROACHES", "page": 7,
           "text": "Like 1D models, HAND and similar methods do not preserve hydraulic connectivity (i.e., floodplain cells lower than the channel water height are denoted as flooded whether or not there is a physical flow path to them) …"}],
 "coverage": {"papers_in_slice": 4310, "chunks_in_slice": 1180311,
              "filters_applied": {"year_from": 2015, "chunk_types": ["sentence", "paragraph"]}, "excluded_cohorts": []},
 "retrieval_validity": "NOT_MEASURED", "validity_detail": {},
 "provenance": {"collection": "flood_papers_768d_v2", "embedding_model": "allenai/specter2_base@3447645e", "…": "…"}}
```

**Errors**: `422 VALIDATION_FAILED`; `503 STORE_UNAVAILABLE` (Chroma down).

**Agent notes**:
- Semantic similarity is not support. Before citing a hit for a claim, run `POST /quotes/verify` or `POST /claims/check` on it.
- An empty `hits` list with `retrieval_validity ≠ VALIDATED` means only "this search did not find it". Say exactly that.
- Rephrase and retry with the method name, the measured quantity and the place before concluding absence. Record all queries you tried.

---

## `POST /search/papers`
- **Status**: implemented (2026-10-02)
- **Purpose**: which **papers** are most relevant to one or more queries (screening, reading lists).
- **Request**: `{"queries": string[1..20], "k": 20, "max_candidates": 500, "filters": SearchFilters, "aggregate": "max" | "mean" | "count"}`.
- **Response 200**: `{"papers": [{"paper_id", "paper": PaperRef, "score": float, "hits": int, "queries_matched": int, "best_chunks": ChunkHit[≤3]}], "coverage", "retrieval_validity", "provenance"}`.
- **Scoring**:
  - Each query retrieves `max_candidates / len(queries)` chunks.
  - A paper's score per query is its best chunk score.
  - `aggregate` combines the queries: `max` (default), `mean` over the queries that matched, or `count` of matching chunks.
- **Agent notes**: use several phrasings of one concept as separate `queries`. Aggregation over queries is more robust than one long query.

---

## `POST /search/similar`
- **Status**: implemented (2026-10-02)
- **Purpose**: papers similar to a given paper. The seed vector is the paper's abstract chunk; without one, it is the mean of up to 200 of its chunks. A duplicate seed resolves to its canonical paper.
- **Request**: `{"paper_id" | "doi": string, "k": 20, "filters": SearchFilters}`.
- **Response 200**: same shape as `/search/papers`; the seed paper is excluded.
- **Errors**: `404 NOT_IN_CORPUS`; `424 SOURCE_UNAVAILABLE` (seed paper has no chunks yet).

---

## `POST /search/hybrid`
- **Status**: planned (phase 3)
- **Purpose**: vector search, plus lexical matching (exact terms, acronyms, numbers), plus one citation hop from the top hits. Use it for recall-critical tasks such as positive controls and literature-gap checks.
- **Request**: `/search/chunks` fields plus `{"lexical_terms": string[], "citation_expansion": {"enabled": true, "direction": "both", "max_per_seed": 20}}`.
- **Response 200**: `hits` with `route: semantic|lexical|citation`, plus `coverage`, `retrieval_validity`, `provenance`.
- **Agent notes**: this is the only search that may back a `VALIDATED` absence statement, and only when the project gate has passed (see [AGENT_RULES R-SCI-3](../AGENT_RULES.md)).
