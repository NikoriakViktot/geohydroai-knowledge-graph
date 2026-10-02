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
- **Grounding on the paper's text** (`src/graph/entity_grounding.py` → `data/graph_inputs/entity_grounding.parquet`): does the surface form or the entity's display name occur **as a word** in the GROBID text (abstract, body, captions)?
  - `grounded`: `true`, `false`, or `null` (not checked: no TEI);
  - `tei_mentions`: the number of matching sentences;
  - `tei_evidence`: up to 3 of those sentences, verbatim;
  - `tei_page`, `tei_section`: where the first one is.
  - Matching rule: case-insensitive; `_`, `-` and space are interchangeable. A short acronym (≤ 4 letters) does not match an all-lowercase word, so "on the other hand" is not HAND.

| Relationship | Edges | Grounded | Not grounded |
|---|---|---|---|
| `USES_METHOD` | 13,748 | 10,274 (75 %) | 3,474 |
| `USES_SENSOR` | 4,757 | 4,585 (96 %) | 172 |
| `REPORTS_METRIC` | 619 | 613 | 6 |

- The extractor's false positives are mostly words that contain or equal an alias:
  - `method.iric`: 8 of 800 edges grounded; the rest come from "empirical";
  - `method.hand`: 73 of 640 grounded; the rest are mostly "on the other hand";
  - GOES from "goes", SWAT from "swath".
- `grounded = false` means "the term does not occur as written". It does **not** prove the edge wrong: paraphrases are missed. For example, "2D hydraulic model" does not ground `method.two_dimensional_hydrodynamic_model`: 20 of its 563 edges are grounded.
- Grounding does not resolve ambiguous acronyms: `method.ml` (Maximum Likelihood) is grounded by every "ML", including machine learning.
- The API also flags `mention_in_evidence`: whether the extractor's own snippet contains the term. It holds for 30 % of `USES_METHOD`, 38 % of `USES_SENSOR` and 89 % of `REPORTS_METRIC` edges. Prefer `tei_evidence`.

All endpoints: **Scope** `read` (except `/graph/cypher`: `admin`) · **Mode** S. Every query runs in a managed **read** transaction (the server refuses writes) with a 10 s timeout and a row limit. Cypher text is static; your values are bound as parameters.

---

## `GET /graph/papers/{doi_or_paper_id}`
- **Status**: implemented (2026-10-02)
- **Purpose**: one paper's neighbourhood: authors, institutions, topics, methods, sensors, metrics with evidence, countries, flood events, numeric facts and citation counts.
- **Path**: a DOI (slashes need no encoding) or a corpus `paper_id`. A corpus paper wins over a reference stub with the same DOI.
- **Query**: `include=authors,methods,sensors,metrics,topics,countries,facts`. The default is all but `facts`. Parts left out are `null` in the response.
- **Response 200**: `{"paper": GraphPaper + {"study_type", "primary_country"}, "authors": [{"name", "orcid", "position", "corresponding", "institutions"}], "methods": [EntityEdge], "sensors": [...], "metrics": [...], "topics": [{"topic_id", "name", "score"}], "countries": [...], "flood_events": [{"name", "year", "country"}], "facts": [GraphFact]?, "counts": {"cites_out", "cites_corpus", "cited_in_corpus"}, "provenance"}`.
  - `EntityEdge` = `{canonical_id, display_name, family, confidence, role, surface_form, evidence, mention_in_evidence, page, section, grounded, tei_mentions, tei_evidence, tei_page, tei_section}`. Sorted grounded first, then `used` first. All edges are returned, grounded or not.
  - `GraphPaper` = `{paper_id?, doi?, title?, year?, venue?, identity_status?, is_reference_stub, cited_by_count?, openalex_id?}`.
- **Errors**: `404 NOT_FOUND`; `400` for an unknown `include` value; `503` (Neo4j unavailable).
- **Agent notes**:
  - A reference stub (`is_reference_stub: true`) is a cited work, not a corpus paper: no text and no entities.
  - Title-only stubs come from GROBID's parse of a bibliography line and may carry a wrong title or year.
  - Treat an entity edge with `grounded = false` as unsupported until you find the concept in `/papers/{paper_id}/text?q=…`. Quote `tei_evidence`, not `evidence`.
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
- **Query**: `year_from`, `year_to`, `min_confidence` (default 0.6), `role=used|mentioned`, `grounded=true|false|any`, `limit` (≤ 1,000), `cursor`.
  - `grounded` applies to Method, Sensor and Metric. The default `true` keeps only edges grounded on the TEI text.
- **Response 200**: `{"items": [GraphPaper + {"confidence", "role", "surface_form", "evidence", "mention_in_evidence", "page", "score", "grounded", "tei_mentions", "tei_evidence", "tei_page"}], "count": int, "next_cursor", "coverage": {"corpus_papers_in_graph": 4806}, "provenance"}`. `count` covers all matches, not just this page.
- **Agent notes**:
  - Entity edges come from rule-based extraction without a human gold set.
  - Even grounded, a count is "papers whose text names X", not "papers that use X": `role` comes from the extractor and has no gold set.
  - Say which filter you used (`grounded`, `role`) whenever you report a count.

---

## `GET /graph/queries`
- **Status**: implemented (2026-10-02)
- **Purpose**: the catalogue of named, parameterised read queries: name, description, parameters (type, default, range, choices), returned columns.
- **Catalogue**: `src/services/graph_read.py`, rewritten from `src/graph/graph_queries.py`. That code used a `REFERENCES` relationship the graph does not have, and interpolated the hop count into the Cypher text.

| Name | Parameters | Returns |
|---|---|---|
| `top_methods` / `top_sensors` | `year_from`, `year_to`, `role`, `grounded` | `canonical_id, display_name, family, papers` |
| `method_sensor_pairs` | `min_papers` (5), years, `role`, `grounded` | `method, sensor, papers` |
| `papers_by_country` | `country`*, years | `paper_id, doi, title, year` |
| `citation_lineage` | `canonical_id`*, `hops` 1–3, `direction` out/in, `role`, `grounded` | `seed, paper_id, doi, title, year, is_reference_stub, hops` |
| `coauthor_network` | `orcid` or `name` | `coauthor, orcid, shared_papers` |
| `metric_ranges_by_method` | `metric`*, `min_facts` (3), `role`, `grounded` | `method, papers, facts, min, median, max` |
| `flood_event_papers` | `event`* | `paper_id, doi, title, year` |
| `institution_output` | `country_code` | `institution, country_code, papers` |
| `numeric_facts_by_metric` | `metric`*, `min_value`, `max_value` | `paper_id, value, raw_cell, table_label, row_context, col_header, page` |
| `method_cooccurrence` | `min_count` (3) | `method_a, method_b, count` |

\* required. `grounded` defaults to `true` (only edges grounded on the TEI text); use `any` to see the extractor's raw output. With `grounded=true`, `top_methods` no longer lists iRIC (800 → 8 papers), and HAND falls from 640 to 73.

---

## `POST /graph/queries/{name}`
- **Status**: implemented (2026-10-02)
- **Request**: `{"params": {…}, "limit": 100}` (limit ≤ 1,000). Parameters are validated against the catalogue entry. Unknown parameters, wrong types, out-of-range values and missing required ones fail with `422` and `errors[].loc = ["body", "params", name]`.
- **Response 200**: `{"columns": string[], "rows": any[][], "truncated": bool, "provenance"}`. `truncated` means more rows existed than `limit`.
- **Errors**: `404 NOT_FOUND` (unknown name), `422` (bad params), `504` (query exceeded 10 s), `503` (Neo4j unavailable).
- **Agent notes**:
  - `metric_ranges_by_method` reports raw table values, e.g. NSE down to −9.72, without range checks. Read `numeric_facts_by_metric` before you quote an extreme.
  - Method counts with `grounded=any` carry the extractor's false positives (see above).

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
