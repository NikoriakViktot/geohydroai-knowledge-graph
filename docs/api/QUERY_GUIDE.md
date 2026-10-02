# Query guide — how to ask the corpus and which data to choose

> **Українською коротко.**
> - Це практичний посібник для ІІ-агента: як формулювати запити до корпусу і яке джерело даних обрати під питання.
> - Розділ 0 — готовий промт для агента (його ж повертає MCP-промт `research`).
> - Запити — англійською, як речення зі статті, 3–5 формулювань. Оцінка схожості — лише для ранжування, не доказ релевантності.
> - Граф сутностей неповний: нуль у графі ≠ нуль у літературі. Перевіряй повнотекстовим пошуком.
> - Числа з літератури — лише через `metric_facts` + таблицю + `verify_quotes`.

**Applies to**: every agent that uses the `ghai` MCP tools or the REST API. The rules of [AGENT_RULES.md](AGENT_RULES.md) still apply; this page says *how* to search well, AGENT_RULES says what you may conclude.

**Measured on**: corpus manifest `23ab6cfc…` (2026-10-02), collection `flood_papers_768d_v2`, SPECTER2. Counts below are snapshots: take the live ones from `corpus_manifest` and from each answer's `coverage`.

---

## 0. Agent prompt

Copy this into an agent's instructions, or get it from the MCP prompt `research` (arguments `question`, `project_id`).

```text
You answer questions from the GeoHydroAI literature corpus (~4.8k flood, hydrology and remote-sensing
papers) only through the `ghai` tools. Your memory of papers is not evidence.

1. Frame. Restate the question as (a) what kind of answer it needs — a passage, a list of papers, a
   count, a number, a citation link — and (b) the slice: years, sensors, methods, regions, chunk types.
   Call corpus_manifest once and keep corpus_manifest_id.
2. Pick the source by answer kind (QUERY_GUIDE §2):
   passages → search_literature; papers → search_papers / similar_papers; a known paper →
   resolve_paper then get_paper_sections / get_paper_text; numbers → metric_facts then
   get_paper_tables + verify_quotes; counts and co-occurrence → normalize_terms then
   papers_with_entity / run_graph_query, cross-checked with search; citations → graph_citations.
3. Write queries in English, as a sentence a paper would contain ("Sentinel-1 backscatter
   thresholding was used to delineate flooded areas"), one concept per query, 3–5 phrasings
   (acronym and expansion, synonyms, method + data + outcome). Translate non-English questions.
   Never use boolean syntax. Exact names (a dam, an event, a dataset) → get_paper_text(q=...) or
   the named graph queries, because embeddings blur proper names.
4. Read scores as a ranking only. Similarity is compressed (on-topic ≈ 0.94–0.96, unrelated
   ≈ 0.87–0.89); a high score is not relevance. Open the passage and judge it by its words.
5. Filter by fields that pre-filter (years, paper_ids, dois, chunk_types, exclude_cohorts). The
   `sections` filter is applied after 50 candidates and often empties the result: find papers first,
   then read the section with get_paper_text.
6. Verify before you use: quotes and numbers through verify_quotes (expected_numbers for numbers);
   attribution.cites_other_sources = true → trace the original paper.
7. Absence: report "this search (queries, k, slice of N papers, manifest id) found nothing", never
   "no study exists". A zero in the graph is not a zero in the literature.
8. Stop when two new phrasings bring no new relevant paper, or after ~12 search calls; say what
   you did not cover.
9. Answer with: the claim, the verbatim span (DOI, page), and a provenance block —
   corpus_manifest_id, collection, embedding_model, the queries and their queries_sha256, span ids,
   and which statements are model-assessed.
```

---

## 1. Where the data live

| Store (tools) | What it holds | Good for | Known limits (2026-10-02) |
|---|---|---|---|
| Chroma `flood_papers_768d_v2` (`search_literature`, `search_papers`, `similar_papers`) | 1.36 M chunks of 4,810 papers: `sentence` 1.26 M, `figure` captions 59 k, `formula` 27 k, `table` captions 13 k, `abstract` 4.6 k | finding passages and papers by meaning | 202 papers in the slice have no chunks; no `paragraph`/`section` chunks; sentence chunks include boilerplate ("summarized in Table 1"); the same sentence can appear twice |
| GROBID TEI full text (`get_paper_sections`, `get_paper_text`, `get_paper_tables`, `get_paper_references`) | verbatim text, sections, pages, tables, parsed bibliography | quoting, reading a section, exact-word lookup (`q=`) | only papers with TEI; scanned PDFs may be thin |
| Postgres identity (`resolve_paper`, `resolve_papers`) | one identity per work: DOI, title, year, venue, `identity_status`, cohorts, duplicates | "is it in the corpus?", deduplication, reference lists | some records have `doi: null` or `year: null` (`identity_status: no_doi`) |
| Neo4j graph (`graph_paper`, `graph_citations`, `papers_with_entity`, `run_graph_query`) | papers, authors, institutions, countries, flood events, methods, sensors, metrics, citations, NumericFact | counts, co-occurrence, citation lineage, events, countries | entity extraction is incomplete and of unmeasured precision: 224 methods, 15 sensors. `method.u_net`, `method.otsu`, `sensor.sentinel_1` have **0** papers although the corpus discusses them at length; Sentinel-1 is folded into `sensor.sentinel` and SAR into `sensor.radar`; `flood_event_papers` returns 0 for "Kakhovka" |
| Metric facts (`metric_facts`, `extract_metrics`, `metric_ontology`) | values from TEI tables and text, with range checks | literature numbers for comparison tables | `unit` is mostly null; table facts carry `table_label`/`row_context`/`col_header`, not a sentence; example: overall accuracy 526 facts from 75 papers, NSE 3,701 facts from 262 papers |
| Ontology (`normalize_terms`, `ontology_entities`) | canonical ids and aliases for methods, sensors, metrics | mapping words to ids before graph or metric queries | ids that exist in the ontology may be missing from the graph (see above) |

## 2. Choose the source by the answer you need

| The question asks for… | Start with | Then | Do not |
|---|---|---|---|
| what the literature says about X (passages) | `search_literature`, 3–5 phrasings, k 10–20 | open the best hits, `verify_quotes` on what you cite | cite a hit you have not read |
| which papers address X | `search_papers` (all phrasings in one call, `aggregate: "max"`; `"count"` ranks by the number of matching chunks, i.e. how much of a paper is about it) | `get_paper_sections` / `get_paper_text` of the top ones | rank papers from sentence hits by hand |
| papers like a known paper | `resolve_paper` → `similar_papers` | `graph_citations` both directions | treat similarity as topical identity |
| what a specific paper says | `resolve_paper` → `get_paper_sections` → `get_paper_text(section=…)` or `get_paper_text(q="exact words")` | quote `span.text` | search the whole corpus for one paper's content |
| a reported number (OA, NSE, RMSE, IoU…) | `metric_ontology` (id and valid range) → `metric_facts(metric=…, method=…, sensor=…)` | `get_paper_tables` + `verify_quotes(expected_numbers=…)`; match unit, period, validation set | use `range_verdict: suspect` values, or infer a metric from topic words |
| how many papers use X / trends | `normalize_terms` → `papers_with_entity` or `run_graph_query` (`top_methods`, `method_sensor_pairs`) | cross-check with `search_papers` for the same concept; state the denominator (`coverage`) and exclude cohort `paper_3` | report a graph zero as "no papers" |
| a named event, place or structure (Kakhovka, Pakistan 2022, Sen1Floods11) | `search_literature` with the name in a full sentence (years bounded); `run_graph_query` `papers_by_country` (Ukraine: 276 papers) and `flood_event_papers` (sparse: 0 for Kakhovka) | `get_paper_text(q="Kakhovka")` on candidates to confirm the name is in the text | trust semantic hits for proper names: "Kakhovka dam breach" also returns the Koga dam |
| who cites whom, lineage of a method | `graph_citations`, `run_graph_query` `citation_lineage` | `resolve_papers` for out-of-corpus references | count reference stubs as corpus papers |
| bibliography metadata | `resolve_doi`, `verify_bib_entries`, `audit_bib` | `format_bib`, `render_bibliography` | write years, pages or authors from memory |

## 3. Writing search queries

The embedding model is SPECTER2, trained on English scientific text. Measured behaviour:

| Query | Top scores | What came back |
|---|---|---|
| `Sentinel-1 SAR backscatter thresholding was used to delineate flooded areas` | 0.961–0.950 | on-topic method sentences |
| `SAR flood mapping Sentinel-1` (keywords) | 0.964–0.948 | on-topic, more generic sentences and captions |
| `картографування затоплень Sentinel-1` (Ukrainian) | 0.930–0.921 | noise: "Supplementary Table 1", "(Figure 8)" |
| `protein folding with transformer neural networks` (off-topic) | 0.888–0.869 | loosely related ANN sentences |
| `Kakhovka dam breach 2023 flood extent` | 0.958–0.939 | Kakhovka papers mixed with the Koga dam |

Rules that follow:
- **English only.** Translate the question; keep sensor, method and place names in their English form.
- **Write like the paper.** A methods sentence ("X was used to…"), a result sentence ("X achieved an overall accuracy of…") or a definition finds the passages that state it. Keywords find generic mentions.
- **One concept per query, several phrasings.** Vary acronym and expansion (SAR / synthetic aperture radar), synonyms (inundation / flood extent / water mask), and the angle (method, data, outcome, limitation). Pass the phrasings together to `search_papers`.
- **No boolean syntax**, quotes or field prefixes: they are embedded as text.
- **Proper names are weak in embeddings.** Put the name in a full sentence, then confirm with `get_paper_text(q=…)`.
- **Use chunk types for the answer kind**: `abstract` for "is this paper about X" (one chunk per paper); `table` / `figure` for captions that announce results; `formula` for equations; default (all) for statements.

## 4. Reading scores

- `score` is 1 − cosine distance. In this collection almost everything scores above 0.85, so the scale is compressed: on-topic top hits ≈ 0.94–0.96, unrelated ≈ 0.87–0.89.
- Use the score **only to rank** within one query. Do not compare scores across queries or chunk types (abstract hits score lower than sentence hits for the same query).
- `min_score` is not a relevance gate. If you set one, calibrate it first with an off-topic query on the same slice and say so.
- Relevance is decided by reading the passage: does it state the thing, about the right object, period and place?
- `retrieval_validity` is `NOT_MEASURED`: no recall has been measured for this collection, so an empty or short result never supports absence (AGENT_RULES R-SCI-3).

## 5. Filters and slices

| Filter | Behaviour |
|---|---|
| `year_from`, `year_to` | pre-filter; papers with `year: null` drop out of any year-bounded slice; say so when it matters |
| `paper_ids`, `dois` | pre-filter: search inside chosen papers (follow-up questions on a shortlist) |
| `chunk_types` | pre-filter; see §1 for counts |
| `exclude_cohorts: ["paper_3"]` | required for prevalence counts (harvested cohort, R-SCI-7) |
| `identity_status` | duplicates and non-papers are never searched; default keeps `ok`, `no_doi`, `title_doi_mismatch` |
| `sections` | **post-filter over the top 50 candidates**: usually returns nothing (`coverage.note` says so). Find papers first, then `get_paper_text(section=…)` |

Always copy `coverage.papers_in_slice` and `filters_applied` into your notes: they are the denominator of any statement you make from the result.

## 6. Graph, entities and counts

1. Map words to ids with `normalize_terms` (give `expected_type` and `context` for short acronyms: "S1" with context "Sentinel-1 SAR imagery" → `sensor.sentinel_1`).
2. Labels are capitalised: `Method`, `Sensor`, `Metric`, `Topic`, `Country`, `FloodEvent`.
3. Ask the graph (`papers_with_entity`, `top_methods`, `top_sensors`, `method_sensor_pairs`, `metric_ranges_by_method`, `flood_event_papers`, `papers_by_country`, `citation_lineage`, `coauthor_network`, `institution_output`, `numeric_facts_by_metric`, `method_cooccurrence`; `graph_queries` lists parameters).
4. If the count is 0 or surprisingly low, the entity may simply not be extracted (§1). Try the broader id (`sensor.sentinel`, `sensor.radar`) and run `search_papers` for the concept. Report both numbers and their sources.
5. `grounded: "true"` (default) keeps only edges whose term occurs in the paper's text; `role: "used"` separates use from mention. State which you used.
6. Every count states its denominator (`coverage.corpus_papers_in_graph`), excludes duplicates and the `paper_3` cohort, and says that extraction precision is unmeasured.

## 7. Numbers from the literature

1. `metric_ontology` → canonical id, valid range, `percent_scale`.
2. `metric_facts(metric=…, method=…, sensor=…, range_verdict="ok")` → candidates; read `summary` (n, papers, median, p10, p90) as a description of the extracted facts, not of the literature.
3. For each value you will use: `get_paper_tables(paper_id)` to see the table, then `verify_quotes` with `expected_numbers`. Match unit (fraction vs %), period (calibration vs validation), area and dataset.
4. Table facts have `evidence.text: null`; the evidence is `table_label` + `row_context` + `col_header` + `page`. Cite the table, not a sentence.

## 8. Recipes

**Support for one claim.**
`search_literature` × 3–5 phrasings (k 15) → keep hits whose words state the claim → `resolve_paper` for each source → `verify_quotes` on the exact sentence → if `cites_other_sources`, trace the original → write the claim at the strength of the weakest span.

**Comparison table of a metric.**
§7, grouped by method and sensor. Report how many papers each cell rests on; drop rows without a verified table cell.

**Prevalence or trend.**
§6 with `year_from`/`year_to` windows, plus `search_papers` with `aggregate: "count"` for the same concept as a cross-check; report both, with denominators.

**Everything about one paper.**
`resolve_paper` → `get_paper_sections` → `get_paper_text` per section you need → `get_paper_tables`, `get_paper_entities`, `get_paper_references` → `graph_citations` both ways.

**An event or a site.**
`flood_event_papers` / `papers_by_country` → `search_literature` with the name in a sentence, years bounded → confirm each paper with `get_paper_text(q=<name>)`.

**Missing literature.**
`resolve_papers` on the reference list or DOIs you expect → `NOT_IN_CORPUS` items go to the person (discovery and acquisition are planned endpoints; check `api_capabilities`).

## 9. Budget and stopping

- k 10–20 per query; up to 5 phrasings per concept; batch `resolve_papers` (≤ 500), `verify_quotes` (≤ 50), `verify_bib_entries` (≤ 50).
- Stop a concept when two new phrasings add no new relevant paper; stop the task at ~12 search calls unless the person asked for exhaustive coverage, and list what was not covered.
- Do not page through hundreds of hits to "prove" absence: absence is not provable here (§4).

## 10. Report template

```text
Answer: <statement, at the strength the evidence allows>
Evidence:
  - "<span.text>" — <first author year>, DOI <doi>, p. <page> (span <span_id>)
Coverage: <papers_in_slice> papers; filters <filters_applied>; cohorts excluded <…>
Queries: <the phrasings> (queries_sha256 <…>), k <…>
Provenance: corpus_manifest_id <…>, collection <…>, embedding_model <…>
Model-assessed: <which judgements are yours, not verified by a tool>
Not covered: <phrasings, slices or sources not searched>
```
