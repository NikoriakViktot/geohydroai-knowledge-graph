# Ontology Forensic Audit v4
**Scope**: All 11 ontology JSON files in `data/`  
**Date**: 2026-05-15  
**Question**: Which files are loaded, which are dead, which have gaps, and which definitions are inconsistent?

---

## 1. File Inventory and Load Status

| File | Keys/Entries | Loaded by KB | Used by |
|---|---|---|---|
| `glossary_acronyms.json` | 961 entries | YES (`_load_glossary`) | `KnowledgeBase.entities` |
| `ontology_methods.json` | 5 top-level keys | YES (`_load_ontology_methods`) | `KnowledgeBase.methods` |
| `bfe_methods.json` | 4 top-level keys | YES (`_load_bfe_methods`) | `KnowledgeBase.bfe_methods` |
| `floods_satelite.json` | 6 top-level keys | YES (`_load_floods_satelite`) | `KnowledgeBase.floods` |
| `remote_sensing_water_resources.json` | 2 top-level keys | YES (`_load_rs_water_resources`) | `KnowledgeBase` entities |
| `flood_modeling_ontology_v2.json` | 9 top-level keys | YES (`_load_v2_ontology`) | `KnowledgeBase.v2_models` (46) |
| `node_methods.json` | 3 top-level keys (schema, nodes, edges) | **NO** | Unused |
| `ontology_disambiguation_rules.json` | 4 top-level keys | **NO** | Unused |
| `ontology_relations_v2.json` | 3 top-level keys | **NO** | Unused |
| `ontology_schema_v2.json` | 5 top-level keys | **NO** | Unused |
| `ontology_normalization_report.json` | 7 top-level keys | **NO** | Unused |

**5 of 11 ontology files are dead** — present in `data/` but never read by `load_knowledge_base()`.

---

## 2. Loaded File Analysis

### 2.1 `glossary_acronyms.json` (961 entities)

Primary source for satellite and method entity patterns. This file is the backbone of KB — 96% of all KB entities originate here.

**Key findings**:
- Entity `SENTINEL` pattern: `\bsentinel[\s\-]?[123][abc]?\b` — matches Sentinel-1, 2, 3 but collapses all to one entity key `SENTINEL`. No sub-entries for individual missions.
- Entity `SAR`: correctly represents Synthetic Aperture Radar as generic sensor
- Entity `HEC-RAS` aliased via `PATTERN_OVERRIDES` in `knowledge_loader.py`: `\bhec[\s\-]?ras\b` — works correctly
- Entity `NSE` appears in this file with `source_kb='glossary'` — KB has it, but metric extraction is handled separately by `METRIC_PATTERNS` in `entity_extractor.py`, not the KB's `entities` dict
- `_ENTITY_BLOCKLIST` (30 terms) in `pipeline.py:377` prevents false positives: includes "REMOTE", "SENSING", "ANALYSIS", "PROCESS", "PROCESSING", "DATA", "MAP", "MAPPING", "STUDY", "AREA" — some of these (e.g., "DATA") could legitimately block compound entity names

### 2.2 `flood_modeling_ontology_v2.json` (46 v2 models)

Contains the v2 taxonomy with task_types, input_data_types, output_types, and normalization mappings.

**Key findings**:
- 46 v2 model records loaded into `KnowledgeBase.v2_models`
- These are used by `ontology_matcher.py` for normalized entity resolution
- `flood_modeling_ontology_v2.json` defines 9 top-level categories (ontology_metadata, taxonomy, task_types, input_data_types, output_types, …)
- Taxonomy maps to normalization IDs used in `normalized_entities` output

### 2.3 `ontology_methods.json`

Contains method definitions across 5 categories. Key issues:

**Naming inconsistencies** (from `ontology_normalization_report.json`):
- `hec_ras` vs `HEC-RAS` vs `one_dimensional_hydrodynamic_model` — same entity appears under different IDs in `bfe_methods.json`, `node_methods.json`, and `ontology_methods.json`
- `type` field values: `'ml'` vs `'machine_learning'` vs `'hydraulic'` vs `'hydrodynamic'` — no canonical type vocabulary enforced
- `lisflood_fp` vs `LISFLOOD` vs `LISFLOOD-FP` — same model, three representations

### 2.4 `floods_satelite.json`

Used for cross-populating satellite entity names into `KnowledgeBase.entities` (the satellite cross-populate loop at `knowledge_loader.py:767-786`).

**Key findings**:
- Satellite names from `floods.data_sources.satellites[sat_type].examples` are added as `EntityRecord` objects with `is_satellite=True`
- These satellite records are built via `_build_pattern()` which generates patterns from acronym and full_name
- Since satellite names from this file are added only if NOT already in `kb.entities`, glossary entities take precedence

### 2.5 `bfe_methods.json`

Contains BFE (Basic Flood Event) method taxonomy. 

**Key findings**:
- `bfe_methods` dict loaded into `KnowledgeBase.bfe_methods` but never queried by `EntityExtractor`
- No `extract_bfe()` method exists — this data is structurally dead from extraction standpoint
- Content is used downstream by normalization pipeline only

---

## 3. Dead Files Analysis

### 3.1 `ontology_disambiguation_rules.json` (NOT LOADED)

**Content**: 15 disambiguation rules + 5 false positive suppressions + acronym collision index.

**Rules defined** (never applied):

| Rule ID | Ambiguous token | Resolution strategy |
|---|---|---|
| `dis_scs_hydrology_vs_remote_sensing` | SCS | context_keyword |
| `dis_rri_model_vs_statistic` | RRI | context_keyword |
| `dis_ann_time_series_vs_spatial` | ANN | context_keyword |
| `dis_mlp_time_series_vs_spatial` | MLP | context_keyword |
| `dis_hec_hms_vs_hec_ras` | HEC-HMS/HEC-RAS | context_keyword |
| `dis_hec_ras_1d_vs_2d` | HEC-RAS | context_keyword |
| `dis_mike_variants` | MIKE | context_keyword |
| `dis_2d_hydrodynamic_vs_hydrological` | (2D) | context_keyword |
| `dis_sarima_vs_arima` | SARIMA | context_keyword |
| `dis_wann_one_vs_multi` | WANN | context_keyword |
| `dis_fuzzy_mamdani_vs_sugeno` | Fuzzy | context_keyword |
| `dis_lisflood_fp_vs_lisflood` | LISFLOOD | context_keyword |
| `dis_prophet_facebook_vs_hydro` | Prophet | context_keyword |
| `dis_nse_vs_noise` | NSE | context_keyword |
| `dis_r2_coefficient_vs_other` | R2 | context_keyword |

**Acronym collisions** tracked but never resolved:
- `ANN`: Artificial Neural Network vs feedforward time-series NN
- `MLP`: Multi-Layer Perceptron (general) vs MLP for water discharge
- `SCS`: SCS Curve Number (hydrology) vs Spectral Classification System (remote sensing)

**False positive suppressions** defined but never applied:
- `J`: suppress unless context includes "cost function" or "objective function"
- 4 additional tokens with suppression conditions

**Impact**: These 15 rules address the most common ambiguities in the domain. SCS and ANN are highly ambiguous — without disambiguation, papers using SCS for hydrology vs remote sensing cannot be distinguished.

### 3.2 `node_methods.json` (NOT LOADED)

**Content**: Graph schema for Neo4j — `schema`, `nodes`, `edges` definitions.

**Purpose**: Defines Neo4j node types and edge relationships. Not part of text extraction pipeline. Structurally appropriate to be separate — this is a Neo4j configuration file, not an extraction knowledge source.

### 3.3 `ontology_relations_v2.json` (NOT LOADED)

**Content**: `relations_metadata`, `relation_definitions`, `semantic_inference_rules`.

**Purpose**: Defines RDF-style semantic relations between entities (e.g., `HEC-RAS uses_dem SRTM`). Used for graph construction, not text extraction. Appropriate as a separate file.

### 3.4 `ontology_schema_v2.json` (NOT LOADED)

**Content**: `schema_metadata`, `node_types`, `edge_types`, `graph_constraints`, `cypher_templates`.

**Purpose**: Cypher query templates and schema definitions for Neo4j. Not part of KB extraction. Appropriate as a separate file.

### 3.5 `ontology_normalization_report.json` (NOT LOADED)

**Content**: Audit report generated by the v2 migration process.

**Missing concepts catalogued** (self-reported gaps):
- `missing_models`: 32 models not in any KB file
- `missing_metrics`: 9 metrics not in any KB file  
- `missing_ontology_concepts`: 11 concepts absent

**Naming inconsistencies catalogued**:
- 3 documented cases (HEC-RAS, type values, LISFLOOD) that remain unfixed

This file is **diagnostic documentation, not an active knowledge source**. Its findings indicate known gaps in the loaded KB files that have not been remediated.

---

## 4. KB Runtime Statistics

```
KB loaded: 966 entities total
  152 satellites
   20 DEMs
  320 methods
   46 v2 models
    ? BFE methods
```

Loaded via `load_knowledge_base()` → singleton via `get_global_kb()` or lazy `_get_kb()`.

### 4.1 PATTERN_OVERRIDES Coverage

40+ hand-crafted patterns in `knowledge_loader.py:PATTERN_OVERRIDES`. These override auto-generated patterns for key entities:

| Entity | Pattern | Covers |
|---|---|---|
| `Sentinel-1` | `\bsentinel[\s\-]?1[abc]?\b\|\bs[\-]?1[abc]?\b` | Sentinel-1A/B/C |
| `HEC-RAS` | `\bhec[\s\-]?ras\b` | HEC RAS, HEC-RAS |
| `SWAT` | `\bswat\+?\b\|soil and water assessment tool` | SWAT+ |
| `NSE` | `\bnse\b\|nash[\-\s]?sutcliffe` | NSE or full name |
| `RMSE` | `\brmse\b` | RMSE only |
| `RANDOM-FOREST` | `\brandom forest\b\|\brf\b(?=\s+classif)` | RF only before "classif" |
| `SVM` | `\bsvm\b\|support vector machine` | SVM, SVM |
| `CNN` | `\bcnn\b\|convolutional neural network` | CNN |

**Gap**: `SENTINEL` in KB has pattern `\bsentinel[\s\-]?[123][abc]?\b` — merges all missions. `Sentinel-1` has its own PATTERN_OVERRIDE but the KB key is shared. When the extractor finds a Sentinel-1 match, it resolves to the KB key `SENTINEL` not `SENTINEL-1`.

---

## 5. Structural Consistency Gaps

### 5.1 Cross-File Naming Conflicts

| Concept | bfe_methods.json | ontology_methods.json | node_methods.json | Canonical |
|---|---|---|---|---|
| HEC-RAS 1D | one_dimensional_hydrodynamic_model | hec_ras | HEC-RAS | Undefined |
| HEC-RAS 2D | two_dimensional_hydrodynamic_model | hec_ras_2d | HEC-RAS-2D | Undefined |
| LISFLOOD-FP | lisflood_fp | LISFLOOD | LISFLOOD-FP | Undefined |
| Method type | physical_based | hydraulic | – | Undefined |

### 5.2 Missing KB Entries (from normalization_report.json)

**32 missing models** — examples include models that appear in scientific literature but have no KB entry (specific list in `data/ontology_normalization_report.json:missing_concepts.missing_models`).

**9 missing metrics** — performance metrics without KB entries. Since `extract_metrics()` uses `METRIC_PATTERNS` (hardcoded in `entity_extractor.py`), this only matters for normalization of already-extracted metrics.

**11 missing ontology concepts** — taxonomic gaps in the v2 hierarchy.

---

## Summary

| Finding | Severity |
|---|---|
| 5/11 ontology files dead (disambiguation, relations, schema not loaded) | HIGH |
| 15 disambiguation rules defined but never applied | HIGH |
| Sentinel-1/2/3 version collapse in KB | MEDIUM |
| 32 missing models documented but not remediated | MEDIUM |
| HEC-RAS/LISFLOOD naming conflict across 3 files | MEDIUM |
| bfe_methods data loaded but never queried by extractor | LOW |
| ontology_normalization_report catalogues known gaps (read-only) | INFO |
