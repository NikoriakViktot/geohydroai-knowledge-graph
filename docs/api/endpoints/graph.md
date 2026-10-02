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

**Paper properties**: `paper_id`, `doi` (canonical lower case), `title`, `year`, `cited_by_count`, `openalex_id`, `journal`, `study_type`, `primary_country`, `identity_status`, `is_reference_stub`.

All endpoints: **Scope** `read` (except `/graph/cypher`: `admin`) · **Mode** S. Every query runs in a read transaction with a 10 s timeout and a row limit.

---

## `GET /graph/papers/{doi_or_paper_id}`
- **Status**: planned (phase 1, WP 1.7)
- **Purpose**: one paper's neighbourhood: authors, institutions, topics, methods, sensors, metrics with evidence, countries, flood events and counts.
- **Path**: a DOI (URL-encode the `/`) or a corpus `paper_id`.
- **Query**: `include=authors,topics,methods,sensors,metrics,facts,countries` (default: all but `facts`).
- **Response 200**: `{"paper": PaperRef + {"cited_by_count", "is_reference_stub"}, "authors": [{"name", "orcid", "institutions": [...]}], "methods": [{"canonical_id", "confidence", "surface_form", "evidence"}], "sensors": [...], "metrics": [...], "topics": [...], "countries": [...], "counts": {"cites_out", "cited_in_corpus"}, "provenance"}`.
- **Errors**: `404 NOT_FOUND`.
- **Agent notes**:
  - A reference stub (`is_reference_stub: true`) is a cited work, not a corpus paper: no text and no entities.
  - Title-only stubs come from GROBID's parse of a bibliography line and may carry a wrong title or year.

---

## `GET /graph/papers/{doi_or_paper_id}/citations`
- **Status**: planned (phase 1)
- **Query**: `direction=out|in|both` (default `out`), `in_corpus_only=false`, `limit`, `cursor`.
- **Response 200**: `{"items": [{"paper": PaperRef, "is_reference_stub": bool, "direction": "out"}], "next_cursor", "counts": {"out", "in", "in_corpus"}, "provenance"}`.
- **Agent notes**:
  - `in` counts only citations **from corpus papers** (≈ 4.8 k). For global citation counts use `cited_by_count` (OpenAlex).
  - Citation snowballing beyond the corpus is `POST /discovery/snowball`.

---

## `GET /graph/entities/{label}/{canonical_id}/papers`
- **Status**: planned (phase 1)
- **Purpose**: corpus papers that use a method or sensor, or report a metric. Example: `GET /graph/entities/Method/method.hand/papers`.
- **Path**: `label` ∈ `Method | Sensor | Metric | Topic | Country | FloodEvent`; `canonical_id` from `POST /ontology/normalize`.
- **Query**: `year_from`, `year_to`, `min_confidence` (default 0.6), `limit`, `cursor`.
- **Response 200**: `{"items": [{"paper": PaperRef, "confidence": 0.9, "evidence": "…"}], "count": int, "next_cursor", "coverage", "provenance"}`.
- **Agent notes**:
  - Entity edges come from rule-based extraction without a human gold set.
  - A count from here is a lower bound with unknown precision. Report it as "papers in which the extractor found X", not "papers that use X".

---

## `GET /graph/queries`
- **Status**: planned (phase 1)
- **Purpose**: the catalogue of named, parameterised read queries.
- **Response 200**: `{"queries": [{"name": "method_sensor_pairs", "description": "…", "params": {"min_papers": "int"}, "returns": ["method", "sensor", "papers"]}], "provenance"}`.
- **Initial catalogue** (from `src/graph/graph_queries.py`, rewritten without string interpolation): `top_methods`, `top_sensors`, `method_sensor_pairs`, `papers_by_country`, `citation_lineage` (hops 1–3), `coauthor_network`, `metric_ranges_by_method`, `flood_event_papers`, `institution_output`, `numeric_facts_by_metric`.

---

## `POST /graph/queries/{name}`
- **Status**: planned (phase 1)
- **Request**: `{"params": {…}, "limit": 100}`. Parameters are validated against the catalogue entry.
- **Response 200**: `{"columns": string[], "rows": any[][], "truncated": bool, "provenance"}`.
- **Errors**: `404 NOT_FOUND` (unknown name), `422` (bad params), `504` (query exceeded 10 s).

---

## `POST /graph/cypher`
- **Status**: planned (phase 1) · **Scope** `admin`
- **Purpose**: ad-hoc read-only Cypher for operators.
- **Request**: `{"query": "MATCH … RETURN …", "params": {}, "limit": 1000}`.
- **Behaviour**:
  - The driver session uses `READ_ACCESS`; any write clause fails.
  - Timeout 10 s; at most 1,000 rows.
  - Params are bound and never interpolated.
- **Agent notes**: agents do not get the `admin` scope. If a question needs a new query, propose it for the named catalogue.
