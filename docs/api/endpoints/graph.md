# Graph — Neo4j projection (read-only)

**Backing data**: Neo4j 5.26.19, rebuilt 2026-10-02 from the layer of truth plus enriched files (`build_graph --wipe --identity postgres`):
- **nodes**: 4,806 corpus papers, 63,798 cited works with DOI, 107,698 title-only reference stubs;
- **semantic relations**: 13,748 `USES_METHOD`, 4,757 `USES_SENSOR`, 619 `REPORTS_METRIC`;
- **citations**: 270,251 `CITES`, of which 25,009 link two corpus papers;
- **facts**: 25,049 `NumericFact`.

Node labels: `Paper`, `Author`, `Institution`, `Topic`, `Method`, `Sensor`, `Metric`, `Country`, `FloodEvent`, `NumericFact`.

Relationships:
- `CITES`, `AUTHORED`, `AFFILIATED_WITH`, `HAS_TOPIC`, `FROM_COUNTRY`, `LOCATED_IN`;
- `USES_METHOD`, `USES_SENSOR`, `REPORTS_METRIC`;
- `CO_OCCURS_WITH`, `COMMONLY_USED_WITH`, `INVESTIGATES`;
- `HAS_NUMERIC_FACT`, `MEASURES`.

**Paper properties**: `paper_id`, `doi` (canonical lower case), `title`, `year`, `cited_by_count`, `openalex_id`, `journal`, `study_type`, `primary_country`, `identity_status`, `is_reference_stub`. 791 of the 4,806 corpus papers have no `year` in the graph.

**Entity edge properties** (`USES_METHOD`, `USES_SENSOR`, `REPORTS_METRIC`; refreshed 2026-10-02 with `build_graph --only entity-edges`):
- `confidence`, `role` (`used` | `mentioned`, from the extractor), `surface_form`;
- `evidence`: up to 3 snippets from the extractor;
- `page`, `section`.
- The API adds **`mention_in_evidence`**: does a snippet contain the surface form as a word? Only **30 % of `USES_METHOD`, 38 % of `USES_SENSOR` and 89 % of `REPORTS_METRIC`** edges pass. A failing edge is not necessarily wrong: often the snippet window just misses the mention.
- Known false-positive class: the extractor matched aliases inside words. `method.iric` (iRIC) has 800 edges; 793 of them come from the word "empirical". Re-grounding the edges on the TEI sentences is planned (data fix 0.4.5).

All endpoints: **Scope** `read` (except `/graph/cypher`: `admin`) · **Mode** S. Every query runs in a managed **read** transaction (the server refuses writes) with a 10 s timeout and a row limit. Cypher text is static; your values are bound as parameters.

---

## `GET /graph/papers/{doi_or_paper_id}`
- **Status**: implemented (2026-10-02)
- **Purpose**: one paper's neighbourhood: authors, institutions, topics, methods, sensors, metrics with evidence, countries, flood events, numeric facts and citation counts.
- **Path**: a DOI (slashes need no encoding) or a corpus `paper_id`. A corpus paper wins over a reference stub with the same DOI.
- **Query**: `include=authors,methods,sensors,metrics,topics,countries,facts`. The default is all but `facts`. Parts left out are `null` in the response.
- **Response 200**: `{"paper": GraphPaper + {"study_type", "primary_country"}, "authors": [{"name", "orcid", "position", "corresponding", "institutions"}], "methods": [EntityEdge], "sensors": [...], "metrics": [...], "topics": [{"topic_id", "name", "score"}], "countries": [...], "flood_events": [{"name", "year", "country"}], "facts": [GraphFact]?, "counts": {"cites_out", "cites_corpus", "cited_in_corpus"}, "provenance"}`.
  - `EntityEdge` = `{canonical_id, display_name, family, confidence, role, surface_form, evidence, mention_in_evidence, page, section}`, sorted `used` first.
  - `GraphPaper` = `{paper_id?, doi?, title?, year?, venue?, identity_status?, is_reference_stub, cited_by_count?, openalex_id?}`.
- **Errors**: `404 NOT_FOUND`; `400` for an unknown `include` value; `503` (Neo4j unavailable).
- **Agent notes**:
  - A reference stub (`is_reference_stub: true`) is a cited work, not a corpus paper: no text and no entities.
  - Title-only stubs come from GROBID's parse of a bibliography line and may carry a wrong title or year.
  - Treat an entity edge with `mention_in_evidence = false` as unsupported until you find the mention in `/papers/{paper_id}/text?q=…`.
  - `role = mentioned` means the paper names the method, not that it applies it.
  - `institutions` are all affiliations known for the author, not those on this paper.

---

## `GET /graph/papers/{doi_or_paper_id}/citations`
- **Status**: implemented (2026-10-02)
- **Query**:
  - `direction=out|in|both` (default `out`); `both` lists outgoing citations, then incoming ones;
  - `in_corpus_only=false` (applies to `out`);
  - `limit` (1–1,000, default 100) and `cursor` (from `next_cursor`).
- **Order**: newest first (no year last), then title.
- **Response 200**: `{"items": [GraphPaper + {"direction": "out" | "in"}], "next_cursor", "counts": {"out", "out_in_corpus", "in"}, "provenance"}`.
- **Agent notes**:
  - `in` counts only citations **from corpus papers** (≈ 4.8 k). For global citation counts use `cited_by_count` (OpenAlex).
  - Outgoing citations come from GROBID's reference parsing: a missing link is not evidence that the paper does not cite a work.
  - Citation snowballing beyond the corpus is `POST /discovery/snowball`.

---

## `GET /graph/entities/{label}/{canonical_id}/papers`
- **Status**: implemented (2026-10-02)
- **Purpose**: corpus papers in which the extractor found a method or sensor, or which report a metric, a topic, a country or a flood event. Example: `GET /graph/entities/Method/method.hand/papers?role=used`.
- **Path**:
  - `label` ∈ `Method | Sensor | Metric | Topic | Country | FloodEvent`;
  - the id is `canonical_id` for Method, Sensor and Metric; `topic_id` for Topic; `name` for Country and FloodEvent.
- **Query**: `year_from`, `year_to`, `min_confidence` (default 0.6), `role=used|mentioned`, `limit` (≤ 1,000), `cursor`.
- **Response 200**: `{"items": [GraphPaper + {"confidence", "role", "surface_form", "evidence", "mention_in_evidence", "page", "score"}], "count": int, "next_cursor", "coverage": {"corpus_papers_in_graph": 4806}, "provenance"}`. `count` covers all matches, not just this page.
- **Agent notes**:
  - Entity edges come from rule-based extraction without a human gold set.
  - A count from here is an **upper bound of unknown precision** until re-grounding (see the iRIC case above). Report it as "papers in which the extractor found X", never as "papers that use X".
  - Count only items with `mention_in_evidence = true` if you need a conservative figure, and say that you did.

---

## `GET /graph/queries`
- **Status**: implemented (2026-10-02)
- **Purpose**: the catalogue of named, parameterised read queries: name, description, parameters (type, default, range, choices), returned columns.
- **Catalogue**: `src/services/graph_read.py`, rewritten from `src/graph/graph_queries.py`. That code used a `REFERENCES` relationship the graph does not have, and interpolated the hop count into the Cypher text.

| Name | Parameters | Returns |
|---|---|---|
| `top_methods` / `top_sensors` | `year_from`, `year_to`, `role` | `canonical_id, display_name, family, papers` |
| `method_sensor_pairs` | `min_papers` (5), years, `role` | `method, sensor, papers` |
| `papers_by_country` | `country`*, years | `paper_id, doi, title, year` |
| `citation_lineage` | `canonical_id`*, `hops` 1–3, `direction` out/in, `role` | `seed, paper_id, doi, title, year, is_reference_stub, hops` |
| `coauthor_network` | `orcid` or `name` | `coauthor, orcid, shared_papers` |
| `metric_ranges_by_method` | `metric`*, `min_facts` (3), `role` | `method, papers, facts, min, median, max` |
| `flood_event_papers` | `event`* | `paper_id, doi, title, year` |
| `institution_output` | `country_code` | `institution, country_code, papers` |
| `numeric_facts_by_metric` | `metric`*, `min_value`, `max_value` | `paper_id, value, raw_cell, table_label, row_context, col_header, page` |
| `method_cooccurrence` | `min_count` (3) | `method_a, method_b, count` |

\* required.

---

## `POST /graph/queries/{name}`
- **Status**: implemented (2026-10-02)
- **Request**: `{"params": {…}, "limit": 100}` (limit ≤ 1,000). Parameters are validated against the catalogue entry. Unknown parameters, wrong types, out-of-range values and missing required ones fail with `422` and `errors[].loc = ["body", "params", name]`.
- **Response 200**: `{"columns": string[], "rows": any[][], "truncated": bool, "provenance"}`. `truncated` means more rows existed than `limit`.
- **Errors**: `404 NOT_FOUND` (unknown name), `422` (bad params), `504` (query exceeded 10 s), `503` (Neo4j unavailable).
- **Agent notes**:
  - `metric_ranges_by_method` reports raw table values, e.g. NSE down to −9.72, without range checks. Read `numeric_facts_by_metric` before you quote an extreme.
  - Method counts carry the extractor's false positives (see above).

---

## `POST /graph/cypher`
- **Status**: implemented (2026-10-02) · **Scope** `admin`
- **Purpose**: ad-hoc read-only Cypher for operators.
- **Request**: `{"query": "MATCH … RETURN …", "params": {}, "limit": 1000}`.
- **Behaviour**:
  - The query is first `EXPLAIN`ed. Unless the planner classifies it as read-only (`r`), it is refused with `422`, e.g. "the planner classifies this query as 'rw'".
  - It then runs in a managed read transaction, which the server also refuses to write in.
  - Timeout 10 s; at most 1,000 rows.
  - Params are bound and never interpolated.
- **Agent notes**: agents do not get the `admin` scope. If a question needs a new query, propose it for the named catalogue.
