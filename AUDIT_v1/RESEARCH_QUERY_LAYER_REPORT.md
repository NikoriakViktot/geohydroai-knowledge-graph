# Research Query Layer — Implementation Report

## Overview

The Research Intelligence page (`/research`) unifies four backends into a single
evidence-first query layer. Gemini is invoked only **after** the evidence pack is
assembled; it cannot hallucinate beyond the provided data.

---

## Architecture

```
User filter state
       │
       ├─► DuckDB / Parquet  → corpus stats, metric distribution,
       │                       author influence, regional patterns
       │
       ├─► Neo4j             → domain NSE performance, institution ranking,
       │                       graph evidence (top NSE papers, metric counts)
       │
       ├─► ChromaDB + SPECTER2 → semantic passage retrieval (768-dim embeddings)
       │
       └─► Gemini            ← receives pre-built evidence pack only
                               synthesises with "Use ONLY evidence above" constraint
```

---

## Filter State Keys

| Key | Backend | Description |
|-----|---------|-------------|
| `year_min` / `year_max` | DuckDB + Neo4j | Year range filter (1990–2025) |
| `countries` | DuckDB | `primary_country` values from `papers.parquet` |
| `study_types` | DuckDB | Paper study type classification |
| `min_citations` | DuckDB | Minimum `cited_by_count` threshold |
| `has_doi` | DuckDB | Boolean — restrict to DOI-linked papers |
| `has_openalex` | DuckDB | Boolean — restrict to OpenAlex-enriched papers |
| `topics` | DuckDB | `topic_name` values from `topics.parquet` |
| `domains` | Neo4j | One of: AI / ML Forecasting, Hydraulic Modeling, Hydrological Modeling, Remote Sensing |
| `metrics` | DuckDB | `canonical_id` values from `numeric_facts.parquet` (e.g. `metric.nse`) |
| `metric_min` / `metric_max` | DuckDB | Value range filter on NumericFact.value |
| `institutions` | Neo4j | Institution `display_name` values |
| `authors` | DuckDB | Author `display_name` values |
| `semantic_query` | ChromaDB | Free-text; encoded by SPECTER2 to 768-dim vector |

---

## Service Functions

### `get_filtered_corpus(fs)` → dict
Backend: DuckDB + data_loader helpers  
Returns: `papers`, `authors`, `topics` KPI counts; `top_countries` DataFrame (n=10);
`year_trend` DataFrame; `top_topics` DataFrame (n=10).

### `get_metric_distribution(fs)` → DataFrame
Backend: DuckDB (`data/analytics/numeric_facts.parquet` ⋈ `papers.parquet`)  
Returns: `[metric, n, mean, std, min_v, max_v, pct_excellent]`  
Filters: year/country on papers; optional `metrics` list + `metric_min`/`metric_max` on value.  
Only returns rows where `node_label = 'Metric'` and `n >= 3`.

### `get_domain_performance(fs)` → DataFrame
Backend: Neo4j (live query — not cached, respects filter)  
Returns: `[domain, n, avg_nse, std_nse, pct_excellent]`  
Domains derived from Paper→Topic keywords (see Limitations).  
Filters: `year_min`/`year_max`, optional `countries`, optional `domains`.

### `get_method_by_region(fs)` → DataFrame
Backend: DuckDB  
Returns: `[country, dominant_topic, topic_papers, papers]`  
Dominant topic per country, restricted to countries with ≥ 5 papers.

### `get_institution_performance(fs)` → DataFrame
Backend: Neo4j  
Returns: `[institution, country, papers, avg_nse, pct_excellent]`  
NSE range: 0.0–1.0. Minimum 2 papers per institution. Top 20 by avg NSE.

### `get_author_influence(fs)` → DataFrame
Backend: DuckDB  
Returns: `[display_name, paper_count, cited_by_count, h_index, works_count, orcid]`  
Joins `paper_author_edges` + `authors` + `data/parquet/authors.parquet` (OpenAlex author universe).

### `get_graph_evidence(fs)` → list[dict]
Backend: Neo4j  
Returns structured evidence items of two types:
- `type=top_nse_paper` — top 10 papers by avg NSE (n_facts ≥ 2) within year filter
- `type=metric_count` — top 8 metric canonical IDs by occurrence count

### `get_semantic_evidence(query, fs, top_k=10)` → list[dict]
Backend: ChromaDB (`flood_papers_768d`, 986,832 chunks) + SPECTER2 (768-dim)  
Encodes query with `allenai/specter2_base` (first call: ~5–10 s warm-up, cached).  
Narrows ChromaDB search via `{"paper_id": {"$in": paper_ids}}` where `paper_ids` comes from
DuckDB (max 500 IDs to stay within ChromaDB `$in` limit).  
Returns: `[paper_id, text, section_title, chunk_type, page, distance, score]`

### `build_evidence_pack(query, fs)` → dict
Assembles all 8 sources. Passed to `ai_gateway.build_research_prompt()` before Gemini call.

---

## AI Gateway (`ai_gateway.py`)

### Model pool (priority order)
1. `gemini-2.5-flash`
2. `gemini-2.5-flash-lite`
3. `gemini-2.0-flash`
4. `gemini-2.0-flash-lite`

### Circuit breaker
In-memory: 5 consecutive failures → 300 s cooldown. Resets on first success.

### Prompt contract
`build_research_prompt(evidence_pack)` serialises all evidence sections (corpus,
metrics, domains, institutions, authors, graph, semantic) into a structured text prompt.
Instruction block: "Use ONLY the evidence above. Do not hallucinate. If evidence is
insufficient, explicitly state the limitation."

Requested synthesis sections:
1. Executive Summary (3 sentences)
2. Geographic Insights
3. Methodological Trends
4. Model Quality Assessment
5. Research Gaps
6. Confidence Rating (HIGH / MEDIUM / LOW)

---

## Supported Analytical Questions

| Question | Primary backend |
|----------|----------------|
| How many papers match my filter? | DuckDB |
| Which countries produce the most research? | DuckDB |
| What metrics are reported and at what performance level? | DuckDB (NumericFacts) |
| Which domain (AI, Hydraulic, etc.) achieves the best NSE? | Neo4j |
| Which institutions produce highest-NSE flood models? | Neo4j |
| Which authors have the highest h-index / citation count? | DuckDB + OpenAlex |
| What do papers say about [specific method/concept]? | ChromaDB + SPECTER2 |
| What are the overall patterns and gaps in this corpus? | Gemini (evidence-grounded) |

---

## Known Limitations

| Limitation | Detail |
|-----------|--------|
| **USES_METHOD=0 in Neo4j** | Paper→Method edges were never populated. `get_method_by_region()` uses topic keywords as a proxy; accuracy depends on topic quality. |
| **Domain classification is keyword-based** | Domain is inferred from Paper→Topic names in Neo4j using keyword matching (e.g. "AI", "HEC", "SAR"). No formal ontology edge. |
| **metric_min/max applies only to Parquet** | NumericFact value range filter works in DuckDB but not in Neo4j graph evidence queries. |
| **ChromaDB paper_id filter ≤ 500 IDs** | The `$in` filter is capped at 500 paper IDs to avoid ChromaDB memory pressure. Broad filters may miss some chunks. |
| **SPECTER2 first-call latency** | Warm-up takes 5–10 s on first semantic query per process. Subsequent queries are fast (< 1 s). |
| **Gemini output size** | `max_output_tokens = 2048`. Very large evidence packs may be truncated. The gateway detects `MAX_TOKENS` finish reason and retries the next model. |
| **pct_excellent threshold** | Defined as `value ≥ 0.75` for all metrics. This is calibrated for NSE/KGE; may be inappropriate for RMSE or unbounded metrics. |
| **Year filter requires valid 4-digit year** | Queries use `CAST(year AS INT)` with `SIMILAR TO '[0-9]{4}'` guard because `papers.parquet` stores year as VARCHAR. |

---

## Example Queries

### Query 1: SWAT calibration NSE trends 2018–2023
```
Semantic query: "SWAT model calibration NSE"
Year range: 2018–2023
Metrics: NSE
```
Expected: 1,691 papers; NSE mean ≈ 0.584; domain rows with AI ML and Hydrological; semantic hits from calibration sections.

### Query 2: AI forecasting institutions since 2015
```
Year range: 2015–2025
Domains: AI / ML Forecasting
```
Expected: institution table with avg NSE > 0.8 for top performers; domain chart shows AI NSE vs Hydraulic.

### Query 3: Remote sensing RMSE 2010–2020
```
Year range: 2010–2020
Metrics: RMSE
```
Expected: RMSE metric row with n > 100; semantic hits from validation sections.

---

## Performance Notes

| Operation | Typical latency |
|-----------|----------------|
| DuckDB corpus query | < 100 ms |
| DuckDB metric distribution | < 200 ms |
| Neo4j domain performance | 200–600 ms |
| Neo4j institution performance | 200–500 ms |
| SPECTER2 encoding (warm) | < 500 ms |
| ChromaDB query | 100–400 ms |
| Full `build_evidence_pack` | 1–2 s |
| Gemini synthesis | 3–15 s |

---

## Files

| File | Purpose |
|------|---------|
| `src/dashboard_dash/research_query_service.py` | 9 public query functions |
| `src/dashboard_dash/ai_gateway.py` | Gemini wrapper, circuit breaker, prompt builder |
| `src/dashboard_dash/pages/research.py` | `/research` page layout |
| `src/dashboard_dash/callbacks.py` | Route + search + AI synthesis callbacks |
