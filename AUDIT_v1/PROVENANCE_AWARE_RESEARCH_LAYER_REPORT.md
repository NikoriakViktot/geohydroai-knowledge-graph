# Provenance-Aware Research Layer — Implementation Report

## 1. What Changed

### `src/dashboard_dash/research_query_service.py`

| Addition | Description |
|----------|-------------|
| `_batch_paper_metadata(paper_ids)` | DuckDB batch lookup: paper_id → {doi, title, year, journal, openalex_id} |
| `_batch_paper_authors(paper_ids)` | DuckDB batch lookup: paper_id → [author display_names] (max 3) |
| `enrich_with_paper_metadata(rows, ...)` | Canonical enrichment function — adds doi, title, year, journal, authors, source_layer, provenance_path to any list of dicts |
| `_batch_paper_abstracts(paper_ids)` | Neo4j batch fetch of abstract text (max 1200 chars per paper) |
| `_select_abstract_papers(pack, max_n=12)` | Hybrid abstract selection: 4 semantic + 4 quantitative + 4 scientometric, deduplicated |
| `_build_limitations(fs)` | Returns list of active limitation strings based on filter state |
| `get_numeric_fact_evidence(fs)` | **NEW**: individual NumericFact rows with doi, title, confidence, extraction_source |
| `get_paper_detail(paper_id)` | **NEW**: full paper detail — metadata + authors + abstract + metrics + semantic chunks |
| `get_semantic_evidence()` | Modified: enriched with doi, title, year, authors via `enrich_with_paper_metadata` |
| `get_graph_evidence()` | Modified: doi + provenance_path added to top_nse_paper items |
| `build_evidence_pack()` | Restructured: new named sections + abstract_context + limitations |

### `src/dashboard_dash/ai_gateway.py`

- `build_research_prompt()` rewritten with:
  - Hard anti-hallucination instruction block at the top
  - DOI serialized in numeric fact evidence, graph evidence, semantic evidence sections
  - Abstract context section (up to 12 papers, 1200 chars each)
  - Known limitations section
  - PROVENANCE and LIMITATIONS required output sections in task instructions

### `src/dashboard_dash/callbacks.py`

| Addition | Description |
|----------|-------------|
| `_doi_cell(doi)` | Renders clickable `doi.org` link or "—" |
| `_source_badge(layer)` | Color-coded source layer badge (parquet/neo4j/chromadb/numeric_fact/openalex) |
| `_th()`, `_td()` | Table header/cell helpers |
| `_papers_table()` | **+DOI column** (clickable link) + source_layer badge |
| `_graph_table()` | **+DOI link** + source_layer badge per item |
| `_semantic_table()` | **+DOI link** + title + source_layer badge per hit |
| `_numeric_facts_table()` | **NEW**: individual facts table with metric, value, confidence, doi, title, year, source |
| `run_research_query` | +Output for `research-numeric-facts`; papers query now selects `doi`; uses new evidence pack keys |
| `_paper_detail_panel()` | **NEW**: renders full paper detail (title, DOI, authors, abstract, metrics table, passages) |
| `show_paper_detail` callback | **NEW**: triggered by `research-selected-paper-id` store; calls `rqs.get_paper_detail()` |

### `src/dashboard_dash/pages/research.py`

- Added `dcc.Store(id="research-selected-paper-id")`
- Added Row 4b: Numeric Fact Evidence section (`research-numeric-facts`)
- Added Row 5: Paper Detail Panel (`research-paper-detail-panel`, `research-paper-detail-content`)
- Updated section headers to mention "doi" in descriptions

---

## 2. Evidence Object Schema

```json
{
  "paper_id":         "0030",
  "doi":              "10.1007/s12040-020-01532-8",
  "title":            "Simulation of rainfall–runoff process...",
  "year":             "2020",
  "journal":          "Journal of Earth System Science",
  "openalex_id":      "W3045...",
  "authors":          ["Surendar Natarajan", "Nisha Radhakrishnan"],
  "metric":           "metric.nse",
  "value":            0.82,
  "unit":             null,
  "confidence":       0.85,
  "extraction_source": "table_extractor",
  "page":             5,
  "period_type":      "CALIBRATION",
  "source_layer":     "numeric_fact",
  "provenance_path":  ["numeric_facts.parquet", "papers.parquet"]
}
```

### Source Layer Values

| source_layer | Origin |
|-------------|--------|
| `parquet` | papers.parquet (top papers table) |
| `numeric_fact` | numeric_facts.parquet + papers.parquet JOIN |
| `neo4j` | Neo4j graph: Paper-[:HAS_NUMERIC_FACT]->NumericFact |
| `chromadb` | ChromaDB flood_papers_768d + papers.parquet enrichment |
| `openalex` | data/parquet/authors.parquet (OpenAlex scientometrics) |

---

## 3. Which Query Functions Expose DOI

| Function | DOI available? | Source |
|----------|---------------|--------|
| `get_filtered_corpus()` | No (corpus-level aggregate) | — |
| `get_metric_distribution()` | No (aggregate, no per-paper DOI) | — |
| `get_numeric_fact_evidence()` | **YES** (per-fact row) | papers.parquet JOIN |
| `get_domain_performance()` | No (domain aggregate) | — |
| `get_method_by_region()` | No (regional aggregate) | — |
| `get_institution_performance()` | No (institution aggregate) | — |
| `get_author_influence()` | No (author-level aggregate) | — |
| `get_graph_evidence()` | **YES** (top_nse_paper items) | _batch_paper_metadata() |
| `get_semantic_evidence()` | **YES** (per-hit) | enrich_with_paper_metadata() |
| `get_paper_detail()` | **YES** (full metadata) | papers.parquet direct lookup |
| `build_evidence_pack()` | Via sub-functions above | — |

---

## 4. Source Layer Traceability

| Evidence | Backend | DOI | Title | Authors | Confidence | Page |
|---------|---------|-----|-------|---------|-----------|------|
| Corpus stats | DuckDB | — | — | — | — | — |
| Metric aggregate | DuckDB | — | — | — | pct_excellent | — |
| **Numeric facts** | DuckDB | ✓ | ✓ | — | ✓ (float) | ✓ |
| **Graph (NSE papers)** | Neo4j | ✓ | ✓ | — | avg_nse | — |
| Graph (metric counts) | Neo4j | — | — | — | — | — |
| **Semantic passages** | ChromaDB | ✓ | ✓ | ✓ | score | ✓ |
| **Paper detail** | DuckDB+Neo4j+ChromaDB | ✓ | ✓ | ✓ | per-fact | ✓ |

---

## 5. Example Evidence Pack (abridged)

```python
{
  "query": "SWAT NSE calibration",
  "filters": {"year_min": 2018, "year_max": 2023},

  "numeric_fact_evidence": [
    {"paper_id": "0030", "doi": "10.1007/s12040-020-01532-8",
     "metric": "metric.nse", "value": 0.82, "confidence": 0.85,
     "source_layer": "numeric_fact",
     "provenance_path": ["numeric_facts.parquet", "papers.parquet"]},
    ...  # 50 rows total
  ],

  "graph_evidence": [
    {"type": "top_nse_paper", "paper_id": "hydr-...", "avg_nse": 0.997,
     "doi": "10.1175/JHM-D-22-0011.s1.", "source_layer": "neo4j",
     "provenance_path": ["Neo4j:Paper-[:HAS_NUMERIC_FACT]->NumericFact", "papers.parquet"]},
    ...
  ],

  "semantic_evidence": [
    {"paper_id": "Comparison...", "doi": "10.1080/23249676.2022.2156401",
     "title": "Comparison of SWAT and HEC-HMS model performance",
     "score": 0.9521, "section_title": "Results",
     "chunk_text": "The NSE value for SWAT model calibration was 0.82...",
     "source_layer": "chromadb",
     "provenance_path": ["ChromaDB:flood_papers_768d", "papers.parquet"]},
    ...
  ],

  "abstract_context": [],   # empty: Neo4j Paper nodes have no `abstract` property
  "limitations": [
    "USES_METHOD edges not populated in Neo4j — domain classification uses topic keywords as proxy.",
    "Abstract text fetched from Neo4j; not all papers have abstract stored.",
    "ChromaDB paper_id filter capped at 500 IDs — very broad filters may miss chunks.",
    ...
  ]
}
```

---

## 6. Example Grounded Gemini Output (with DOI citations)

Prompt now includes 20+ DOI references. Example expected output (at synthesis time):

```
EXECUTIVE SUMMARY
Per NUMERIC FACT EVIDENCE, 50 NSE measurements from 2018–2023 show a mean
of 0.584 with 50.9% exceeding the 0.75 excellence threshold (per METRIC
DISTRIBUTION). SWAT-based studies dominate calibration evidence
[doi:10.1080/23249676.2022.2156401], with KGE achieving a higher mean of
0.703 [doi:10.3390/w12092516].

...

PROVENANCE
| Claim | DOI | Source Layer | Confidence |
|-------|-----|-------------|-----------|
| Mean NSE = 0.584 | — | DuckDB / numeric_facts | MEDIUM |
| SWAT vs HEC-HMS comparison | doi:10.1080/23249676.2022.2156401 | ChromaDB | HIGH |
| Top NSE = 0.997 | doi:10.1175/JHM-D-22-0011.s1. | Neo4j | HIGH |

LIMITATIONS
- Abstract evidence absent (Neo4j Paper nodes lack abstract property).
- Domain classification uses topic keywords, not formal ontology edges.
- Metric value range filter applies to Parquet only, not Neo4j graph evidence.
```

---

## 7. Remaining Limitations

| Limitation | Severity | Mitigation |
|-----------|---------|-----------|
| **Abstract not in Neo4j** | Medium | `build_graph.py` did not store abstract field; requires graph rebuild with abstract included. Until then `abstract_context` section will always be empty. |
| **No DOI on aggregate functions** | Low | `get_metric_distribution`, `get_domain_performance`, `get_institution_performance` return aggregates without per-paper DOI. Use `get_numeric_fact_evidence` for DOI-level granularity. |
| **DOI quality varies** | Low | Some DOIs have trailing periods or are formatted as supplementary links (e.g. `JHM-D-22-0011.s1.`). Stored as-is from OpenAlex; no correction applied. |
| **Author edges partial coverage** | Medium | Not all papers in `papers.parquet` have matching rows in `paper_author_edges.parquet` (depends on OpenAlex enrichment coverage: 2,963/3,680 papers enriched). |
| **USES_METHOD=0 in Neo4j** | Medium | Paper→Method edges never populated; domain classification is keyword-based. |
| **ChromaDB $in cap at 500** | Low | Broad year filters return many papers; ChromaDB query narrows to 500 paper_ids maximum. Very broad queries may miss chunks from papers outside the 500. |
| **Gemini token limit** | Low | Full evidence pack prompt is ~11,700 chars. With `max_output_tokens=2048` and Gemini token budget, deep analysis may be truncated; gateway falls back to next model on `MAX_TOKENS`. |

---

## 8. Verification Commands

```bash
# A. Semantic search provenance
.venv/bin/python -c "
from src.dashboard_dash.research_query_service import get_semantic_evidence
hits = get_semantic_evidence('SWAT NSE calibration', {'year_min': 2018, 'year_max': 2023})
for h in hits[:3]:
    print(h.get('paper_id'), h.get('doi'), h.get('title'), h.get('score'))
"

# B. Numeric fact evidence with DOI
.venv/bin/python -c "
from src.dashboard_dash.research_query_service import get_numeric_fact_evidence
facts = get_numeric_fact_evidence({'year_min': 2018, 'year_max': 2023, 'metrics': ['metric.nse']})
print(len(facts), 'facts')
for f in facts[:3]: print(f.get('doi'), f.get('metric'), f.get('value'), f.get('confidence'))
"

# C. DOI references in Gemini prompt
.venv/bin/python -c "
from src.dashboard_dash.research_query_service import build_evidence_pack
from src.dashboard_dash.ai_gateway import build_research_prompt
pack = build_evidence_pack('SWAT NSE calibration', {'year_min': 2018, 'year_max': 2023})
prompt = build_research_prompt(pack)
print('DOI references:', prompt.count('doi:'))
assert prompt.count('doi:') >= 3
"

# D. Paper detail
.venv/bin/python -c "
from src.dashboard_dash.research_query_service import get_paper_detail
d = get_paper_detail('0030')
print('DOI:', d.get('doi'))
print('Authors:', d.get('authors'))
print('Metrics:', len(d.get('metrics', [])))
print('Semantic chunks:', len(d.get('semantic_chunks', [])))
"

# E. Start dashboard and navigate to /research
.venv/bin/python -m src.dashboard_dash.app
# Search → verify DOI column in Top Papers table
# Search → verify Numeric Fact Evidence table with doi, confidence columns
# Click Generate AI Synthesis → verify DOI citations + PROVENANCE section
```
