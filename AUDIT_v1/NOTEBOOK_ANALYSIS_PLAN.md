# NOTEBOOK_ANALYSIS_PLAN.md
# GeoHydroAI Scientific Intelligence Platform

**Purpose**: Publication-quality Jupyter notebooks for scientific article writing, dissertation analysis, and knowledge graph analytics. All notebooks use REAL data — no synthetic examples.

---

## 1. Scientific Goals

These notebooks operationalize five core scientific claims:

| Claim | Supporting Notebooks |
|-------|---------------------|
| Scientific information degrades across AI pipelines | **09** (core), 02 |
| Multimodal parsing preserves more quantitative evidence | **09**, 08 |
| Hybrid architectures outperform isolated retrieval | **08**, 09 |
| Provenance-aware evidence improves traceability | **08**, 02 |
| Hydrological literature exhibits geographic and methodological patterns | **01, 03, 04, 05, 10** |

---

## 2. Data Architecture

| Layer | Location | Contents |
|-------|----------|----------|
| DuckDB / Parquet (analytics) | `data/analytics/*.parquet` | 3,065 papers, 713 numeric facts, 7,448 authors, 328 topics, 175 methods, 84 sensors |
| DuckDB / Parquet (raw) | `data/parquet/*.parquet` | 9,466 authors with full scientometrics (h-index, i10-index) |
| Neo4j Graph | bolt://localhost:7687 | 13 node types, 25 relationship types, NumericFact nodes, FloodEvent nodes |
| ChromaDB | `.chromadb/flood_papers_768d` | 986,832 chunks, 768-dim SPECTER2 |
| Shared utilities | `notebooks/shared/` | `db.py` (DuckDB), `neo4j_conn.py`, `style.py` |

### Key Parquet Schemas

| File | Rows | Key Columns |
|------|------|-------------|
| `analytics/papers.parquet` | 3,065 | paper_id, doi, title, year (str), journal, primary_country, cited_by_count, study_type, metrics_count, methods_count |
| `analytics/numeric_facts.parquet` | 713 | fact_id, paper_id, canonical_id, value, confidence (float32), period_type, source, page |
| `parquet/authors.parquet` | 9,466 | author_id, display_name, h_index, cited_by_count, works_count, i10_index |
| `analytics/sodb_tables.parquet` | 463 | table_id, paper_id, page, has_numeric_data, row_count, col_count |
| `analytics/formulas.parquet` | 1,512 | formula_id, paper_id, formula_class, confidence, source_parser |
| `analytics/regions.parquet` | 2,922 | region_id, paper_id, region_type, source_parser, confidence |
| `analytics/retrieval_audit.parquet` | 100 | query, rank, distance, semantic_score, section, filename |

### Critical DuckDB Note
`year` in `analytics/papers.parquet` is stored as **VARCHAR**. Always use:
```sql
WHERE year SIMILAR TO '[0-9]{4}' AND CAST(year AS INT) BETWEEN 2000 AND 2024
```

---

## 3. Notebook Descriptions

### `01_corpus_overview.ipynb`
**Goal**: Establish corpus baseline statistics for all downstream analyses.

| Aspect | Detail |
|--------|--------|
| Research question | What is the structure, scope, and geographic/temporal distribution of the corpus? |
| Data | papers.parquet, paper_topic_edges, topics, methods, sensors, institutions |
| Figures | Publication timeline (study_type stacked), country choropleth, topic bar, citation histogram, sensor donut, method type bar |
| Tables | Top 20 journals, Top 10 topics, Corpus coverage summary |
| Key query | `SELECT CAST(year AS INT) AS yr, study_type, COUNT(*) AS n FROM papers GROUP BY yr, study_type` |
| Expected finding | Post-2014 publication surge (Sentinel-1); Asia dominates; SAR growing |
| Publication use | Introduction, corpus description section of any paper using this platform |

---

### `02_metric_analysis.ipynb`
**Goal**: Quantify model performance across the corpus using 713 extracted numeric facts.

| Aspect | Detail |
|--------|--------|
| Research question | What is the NSE/KGE/RMSE distribution, and which methods meet excellence thresholds? |
| Data | numeric_facts.parquet JOIN papers.parquet |
| Figures | Violin plots (all metrics), NSE histogram with threshold, cal/val scatter, confidence scatter, top papers bar |
| Tables | Metric statistics (n, mean, std, Q1, Q3, pct_excellent), Top 10 NSE papers, Cal vs Val comparison |
| Key query | `PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY value)` grouped by canonical_id |
| Expected finding | Mean NSE ≈ 0.58; ~50% exceed excellence threshold; cal NSE > val NSE (overfitting signal) |
| Publication use | Results section, methodology validation, performance benchmarking tables |

---

### `03_temporal_trends.ipynb`
**Goal**: Detect temporal inflection points in flood research methodology adoption.

| Aspect | Detail |
|--------|--------|
| Research question | How has AI/ML adoption and SAR usage evolved over time? |
| Data | papers.parquet, paper_topic_edges, topics, numeric_facts |
| Statistical methods | Mann-Kendall trend test (scipy.stats.kendalltau), pre/post breakpoint comparison |
| Figures | Stacked area (study_type by year), AI/ML dual-axis, SAR adoption with Sentinel-1 marker, NSE trend ±std, top-5 topics line chart |
| Key finding | SAR: >3× increase post-2014; AI: >5× increase post-2018; NSE trend not significant |
| Publication use | Background section, literature evolution, motivation for multi-modal approach |

---

### `04_geographic_patterns.ipynb`
**Goal**: Map geographic distribution and regional methodological preferences.

| Aspect | Detail |
|--------|--------|
| Research question | Which regions dominate flood research, and do they differ methodologically? |
| Data | papers.parquet, institutions.parquet, numeric_facts, paper_topic_edges |
| Figures | World choropleth (paper count), country bar (avg cit overlay), NSE choropleth, institution bar, Eastern Europe timeline |
| Tables | Top 20 countries with DOI coverage, Top 15 institutions, FloodEvent linkage |
| Neo4j | `MATCH (p:Paper)-[:INVESTIGATES]->(fe:FloodEvent) RETURN fe.name, count(p)` |
| Key finding | India/China/USA dominate volume; Asian institutions lead NSE performance; EE is underrepresented |
| Publication use | Geographic analysis section, regional comparison figures |

---

### `05_method_dominance.ipynb`
**Goal**: Characterize method frequency, performance, and co-occurrence structure.

| Aspect | Detail |
|--------|--------|
| Research question | Which methods dominate, and what are their performance profiles and co-occurrence patterns? |
| Data | methods.parquet, sensors.parquet, numeric_facts; Neo4j: CO_OCCURS_WITH, USES_METHOD |
| Figures | Method bar (by type_group), NSE by method (error bars), co-occurrence network (networkx), sensor bar |
| Neo4j | `MATCH (m1:Method)-[co:CO_OCCURS_WITH]->(m2:Method) RETURN m1.display_name, m2.display_name, co.count` |
| Key finding | SWAT dominant (classical hydrology); Random Forest leads AI; SAR+U-Net co-occurrence growing |
| Publication use | Methodology section, method taxonomy, comparative analysis |

---

### `06_scientometric_analysis.ipynb`
**Goal**: Characterize the citation and influence structure of the flood research community.

| Aspect | Detail |
|--------|--------|
| Research question | Who are the most influential authors/papers, and how concentrated is citation influence? |
| Data | parquet/authors.parquet (h_index, i10_index), paper_author_edges, references, paper_reference_edges |
| Figures | h-index histogram, Lorenz curve (Gini coefficient), top-author bubble chart, reference age histogram, position violin |
| Statistical methods | Gini coefficient (np.trapz on Lorenz curve), reference age distribution |
| Key finding | Gini > 0.7; top 10% of papers hold ~70% of citations; median ref age ≈ 8–12 years |
| Publication use | Scientometric characterization, community structure analysis |

---

### `07_graph_analysis.ipynb`
**Goal**: Network topology analysis of the knowledge graph.

| Aspect | Detail |
|--------|--------|
| Research question | Which papers are structurally central, and what community structure emerges? |
| Data | paper_reference_edges.parquet (155,665 edges); Neo4j: USES_METHOD, HAS_TOPIC, CO_OCCURS_WITH |
| Algorithms | PageRank (nx.pagerank), Louvain communities (nx.community.louvain_communities), betweenness (sampled k=500) |
| Figures | Citation network subgraph (top 150 nodes, force-directed), degree distribution log-log, PageRank vs in-degree scatter, community treemap |
| Key finding | Power-law degree distribution; 3–5 major communities; SWAT/Sentinel-1 papers are structural bridges |
| Publication use | Knowledge graph analysis chapter, community structure figures |

---

### `08_semantic_retrieval_validation.ipynb`
**Goal**: Validate ChromaDB retrieval consistency and DOI provenance coverage.

| Aspect | Detail |
|--------|--------|
| Research question | How consistent is semantic retrieval, and what fraction of chunks have DOI traceability? |
| Data | retrieval_audit.parquet (100 records), ChromaDB flood_papers_768d (986,832 chunks) |
| Live queries | `get_semantic_evidence(query, fs)` for 4 standardized test queries |
| Figures | Score distribution per query, score vs rank scatter, section distribution donut, live retrieval bar |
| Key finding | Scores cluster 0.6–0.85; Results/Methods sections dominate; ~15–25% chunks lack DOI |
| Publication use | Retrieval system validation, hybrid evidence architecture evaluation |

---

### `09_information_loss_analysis.ipynb` ⭐ Core Scientific Novelty
**Goal**: Quantify information degradation across pipeline stages.

| Aspect | Detail |
|--------|--------|
| Research question | How much quantitative information is lost at each pipeline stage? |
| Data | papers.parquet (metrics_count), numeric_facts, sodb_tables, formulas, regions |
| Figures | Pipeline Sankey (plotly), metrics_count histogram, table quality analysis, region confidence violin, formula confidence bar, retention curve |
| Key finding | 77% information loss at quantitative level; only ~23% of papers yield ≥1 numeric fact |
| Scientific claim | Multimodal parsing preserves more evidence; information degradation is measurable and significant |
| Publication use | Core results chapter; pipeline evaluation; novelty claim support |

---

### `10_case_studies_ukraine.ipynb`
**Goal**: Focused analysis of Eastern European / Ukrainian flood research patterns.

| Aspect | Detail |
|--------|--------|
| Research question | How is Eastern Europe (especially Ukraine) represented in flood research? |
| Data | papers.parquet (filter by primary_country), numeric_facts, topics; Neo4j: FloodEvent; ChromaDB semantic queries |
| Figures | EE country bar, Ukraine timeline (with Kakhovka annotation), SAR adoption by EE country, NSE comparison violin, semantic retrieval bar |
| Key finding | Ukraine underrepresented; Kakhovka 2023 barely covered (publication lag); EE favors SAR methods |
| Publication use | Case study chapter, regional analysis, motivating example for Kakhovka/Prut research |

---

## 4. Cross-Notebook Scientific Narrative

```
NB01 (Corpus) → establishes scale and geographic/temporal baseline
  ↓
NB03 (Temporal) → proves methodology evolution (AI/SAR inflection points)
  ↓
NB05 (Methods) → identifies dominant method clusters and co-occurrences
  ↓
NB07 (Graph) → reveals community structure and structural bridges
  ↓
NB02 (Metrics) → quantifies performance across methods
  ↓
NB09 (Info Loss) ← KEY: shows why performance varies (extraction degradation)
  ↓
NB08 (Retrieval) → validates semantic layer complements quantitative layer
  ↓
NB10 (Ukraine) → applies all analyses to a focused geographic case study
  ↓
NB04, 06 (Geography, Scientometrics) → supporting characterization chapters
```

---

## 5. Visualization Conventions

```python
# Color palette (from notebooks/shared/style.py)
PALETTE = [
    "#00B4D8", "#0077B6", "#48CAE4", "#90E0EF", "#ADE8F4",
    "#023E8A", "#03045E", "#06D6A0", "#FFB703", "#FB8500",
    "#E63946", "#2A9D8F", "#E9C46A", "#F4A261", "#264653",
]

# Figure export
save(fig, "fig_X_Y_name")  # → figures/fig_X_Y_name.{png,svg}
# PNG: 300 DPI | SVG: vector (for journal submission)

# Style settings
figure.dpi     = 150  (screen)
savefig.dpi    = 300  (export)
axes.spines    = top=False, right=False
axes.grid      = True, alpha=0.3
```

### Domain Color Conventions
| Domain | Color |
|--------|-------|
| AI / ML Forecasting | `#06D6A0` |
| Hydraulic Modeling | `#00B4D8` |
| Hydrological Modeling | `#0077B6` |
| Remote Sensing | `#FFB703` |
| Calibration | `#00B4D8` |
| Validation | `#48CAE4` |

---

## 6. Statistical Methods Index

| Method | Used In | Library |
|--------|---------|---------|
| Mann-Kendall trend test | NB03 (NSE trend, AI trend) | `scipy.stats.kendalltau` |
| Mann-Whitney U test | NB10 (EE vs Global NSE) | `scipy.stats.mannwhitneyu` |
| Lorenz curve + Gini coefficient | NB06 (citation inequality) | `numpy.trapz` |
| PageRank | NB07 (paper centrality) | `networkx.pagerank` |
| Louvain community detection | NB07 (community structure) | `networkx.community.louvain_communities` |
| Pearson correlation | NB02 (confidence vs NSE) | `pandas.DataFrame.corr` |
| Percentile statistics | NB02 (Q1, Q3, median) | DuckDB `PERCENTILE_CONT` |
| Log-log regression proxy | NB07 (power-law test) | visual inspection |

---

## 7. Publication Relevance Mapping

### For a Scientific Article on "AI Pipeline Information Loss"
- **Introduction**: NB01 (corpus scale), NB03 (temporal context)
- **Related Work**: NB05 (method taxonomy), NB07 (community structure)
- **Methodology**: NB08 (retrieval validation), NB09 (pipeline design)
- **Results**: NB09 (information loss quantification), NB02 (performance evidence)
- **Discussion**: NB04 (geographic patterns), NB10 (case study)

### For a Dissertation Chapter on "Scientific Evidence Provenance"
- **Ch. 1 Literature**: NB01, NB03, NB06
- **Ch. 2 Corpus Analysis**: NB04, NB05, NB07
- **Ch. 3 Quantitative Evidence**: NB02, NB09
- **Ch. 4 Retrieval System**: NB08
- **Ch. 5 Case Studies**: NB10

### For a Conference Paper on "Flood Research Scientometrics"
- Primary: NB01, NB03, NB04, NB06
- Supporting: NB05, NB07

---

## 8. Running the Notebooks

### Prerequisites
```bash
# Services required
docker compose up -d          # Neo4j (7687) must be running for NB05, 07, 10
# ChromaDB is file-based (.chromadb/) — no service needed

# Python environment
.venv/bin/pip install matplotlib seaborn pyvis geopandas nbformat jupyter nbconvert

# Recommended working directory when executing cells
cd /home/niko/projects/knoweledg_graf/notebooks
```

### Execution Order
```bash
# Run all notebooks headlessly (in order)
for nb in 01 02 03 04 05 06 07 08 09 10; do
    .venv/bin/jupyter nbconvert --to notebook --execute \
        notebooks/${nb}_*.ipynb \
        --output notebooks/${nb}_*_executed.ipynb \
        --ExecutePreprocessor.timeout=600
done
```

### Output
- `figures/fig_X_Y_name.png` — 300 DPI PNG for all figures
- `figures/fig_X_Y_name.svg` — vector SVG for journal submission
- Executed notebooks with outputs embedded

---

## 9. Known Data Limitations

| Limitation | Impact | Notebooks Affected |
|-----------|--------|-------------------|
| `abstract` not stored in Neo4j Paper nodes | Abstract context always empty | 08 |
| Only 180/3,692 papers have Nougat regions | SODB table/formula analysis covers ~5% of corpus | 09 |
| numeric_facts.parquet: 713 facts from 3,065 papers (23% coverage) | Metric analysis is a sample, not full corpus | 02, 05, 09 |
| `year` column is VARCHAR in analytics/papers.parquet | Must use `CAST(year AS INT)` + SIMILAR TO filter | 01, 03, 04, 10 |
| CO_OCCURS_WITH edges require threshold ≥3 | Emerging method pairs may be absent | 05, 07 |
| INVESTIGATES (Paper→FloodEvent) may not be populated | FloodEvent analysis falls back to title search | 10 |
| retrieval_audit.parquet: only 100 records | Small sample for retrieval statistics | 08 |
| OpenAlex enrichment: 2,963/3,680 papers (80.5%) | Author scientometrics cover ~80% of corpus | 06 |

---

*Generated by GeoHydroAI Scientific Intelligence Platform — 2026-05-21*
