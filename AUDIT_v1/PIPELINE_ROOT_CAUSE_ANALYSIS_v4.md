# Pipeline Root Cause Analysis v4
**Date**: 2026-05-15  
**Scope**: Scientific information loss in the GeoHydroAI ingestion pipeline  
**Purpose**: Ranked root causes with evidence, code locations, and estimated impact

---

## Root Cause Ranking

| # | Cause | Severity | Papers affected | Code location |
|---|---|---|---|---|
| 1 | metric_text excludes methods section | CRITICAL | ~100% | `pipeline.py:~472` |
| 2 | METRIC_PATTERNS require numeric value | CRITICAL | ~100% | `entity_extractor.py:28-42` |
| 3 | Disambiguation rules never applied | HIGH | ~20-30% | `knowledge_loader.py` (not loaded) |
| 4 | Usage filter blocks acquisition verbs | HIGH | ~73% for satellites | `entity_extractor.py:50-68` |
| 5 | Sentinel-1/2/3 version collapse | MEDIUM | ~100% (all sentinel papers) | `knowledge_loader.py:SENTINEL` |
| 6 | Other section (16KB avg) partially inaccessible | MEDIUM | ~40% | `pipeline.py:_reclassify_from_other` |
| 7 | study_type stored at wrong JSON path | MEDIUM | 100% (all consumers) | `pipeline.py` serialization |
| 8 | LLM judge score never applied to entities | LOW-MEDIUM | 100% | `pipeline.py:2376` |
| 9 | Judge prompt template leakage | LOW | ~8% (4 papers) | `pipeline.py:2450` |
| 10 | BFE methods loaded but never queried | INFO | 0% (dead code) | `knowledge_loader.py:bfe_methods` |

---

## RC-1: metric_text Excludes Methods Section

**Severity**: CRITICAL  
**File**: `src/ingestion/pipeline.py:~472`

```python
# Current (broken)
metric_text = " ".join([self.abstract, self.results, self.conclusion])

# Methods section is completely excluded from metric search
```

**Why this fails**:
- NSE appears in methods section of 77% of hydrology papers (performance values reported alongside calibration/validation procedure)
- Results section is empty in 48% of papers
- Conclusion section rarely contains numeric values

**Evidence**:
- Paper `032003_1.tei`: NSE mentioned in methods, results section = 0 chars, metric_text = 4 115 chars (abstract + empty results + conclusion). `extract_metrics(metric_text)` returns `[]`. `extract_metrics(methods)` returns metrics.
- Across 52 papers: NSE extracted in 0 of 52 papers.

**Impact**: NSE and RMSE — the two primary model performance metrics in hydrology — are systematically invisible to the pipeline. A graph query "show papers where NSE > 0.80" would return zero results regardless of the actual data.

**Fix direction**: Add `self.methods` to `metric_text`:
```python
metric_text = " ".join([self.abstract, self.methods, self.results, self.conclusion])
```

---

## RC-2: METRIC_PATTERNS Require Numeric Value

**Severity**: CRITICAL  
**File**: `src/ingestion/knowledge/entity_extractor.py:28-42`

```python
"NSE":  r"\bnse\b\s*(?:=|:|of)?\s*(\d+(?:\.\d+)?)",
"RMSE": r"\brmse\b\s*(?:=|:|of)?\s*(\d+(?:\.\d+)?)",
```

The capturing group `(\d+(?:\.\d+)?)` is not optional. If NSE appears without an immediately following numeric value, no match occurs.

**Why this matters**:
- Many papers discuss NSE/RMSE in results narrative without a directly adjacent value: "The NSE values for all stations were above 0.75, indicating…" — this would match because "above" is followed by 0.75
- But "NSE performance was satisfactory" → no match
- "Average NSE across catchments: see Table 3" → no match (value in table, not text)
- "The model showed strong NSE" → no match

**Evidence**: `extract_metrics("The NSE was high and RMSE was low.")` returns `[]`. `extract_metrics("NSE of 0.82")` returns `['NSE', 'RMSE']`.

**Compounding interaction with RC-1**: Even if methods section were added to metric_text, metric mentions without adjacent values (common in methods/discussion sections) would still be missed.

**Fix direction**: Add a secondary "metric mentioned" extraction that captures bare mentions (NSE without value) as a separate list, and require value capture only for the primary `metrics` list.

---

## RC-3: Disambiguation Rules Never Applied

**Severity**: HIGH  
**File**: `data/ontology_disambiguation_rules.json` (not loaded)  
**KB file**: `src/ingestion/knowledge/knowledge_loader.py:load_knowledge_base()`

15 disambiguation rules are defined in JSON but the file is never read by `load_knowledge_base()`. The `files` dict in `load_knowledge_base()` lists only 6 files and does not include `ontology_disambiguation_rules.json`.

**Affected ambiguities**:

| Token | Ambiguity | In corpus |
|---|---|---|
| SCS | SCS-CN (Curve Number hydrology) vs Spectral Classification System | Common |
| ANN | General ML model vs time-series forecasting model | Common |
| MLP | General perceptron vs water discharge MLP | Common |
| HEC-RAS | 1D vs 2D hydrodynamic model | Common |
| NSE | Nash-Sutcliffe Efficiency vs noise/other | Common |
| LISFLOOD | LISFLOOD-FP (2D inundation) vs LISFLOOD (hydrological) | Occasional |
| Prophet | Facebook Prophet (time-series) vs hydrological use | Occasional |

**Impact**: Papers using SCS for hydrology and papers using SCS for remote sensing classification are indistinguishable in the output. Graph queries on SCS conflate two different scientific domains. ANN ambiguity affects task classification.

**Fix direction**: Load the disambiguation rules file and apply rules in `EntityExtractor` or `ontology_matcher.py` when an ambiguous token is found.

---

## RC-4: Usage Filter Blocks Satellite Acquisition Verbs

**Severity**: HIGH  
**File**: `src/ingestion/knowledge/entity_extractor.py:50-56`

```python
_USAGE_POSITIVE = [
    "used", "applied", "we used", "this study uses", "data used",
    "method used", "we applied", "this paper uses", "using",
    "was applied", "is applied", "are used", "were used",
    "we employ", "employed", "we adopt", "adopted",
    "we utilize", "utilized",
]
```

Satellite data acquisition in scientific papers is predominantly described with verbs NOT in this list:
- "obtained from" — Sentinel-1 data was **obtained from** ESA
- "acquired from" — imagery **acquired from** Copernicus Open Access Hub
- "downloaded from" — data **downloaded from** USGS EarthExplorer
- "derived from" — backscatter **derived from** Sentinel-1 SAR
- "provided by" — SAR data **provided by** ESA
- "sourced from" — imagery **sourced from**

**Evidence**: `extract_satellites("SAR data was obtained from the ESA Sentinel-1 archive.", strict=True)` would return no match because "obtained" is not in `_USAGE_POSITIVE`. `strict=False` returns the match.

The `strict=True` default is the conservative setting used in production.

**Impact**: Papers that correctly cite data sources using acquisition language instead of usage language lose satellite entity extraction. This directly explains the 73% papers-with-no-satellites finding.

**Compounding factors**: 
- Satellite mentions in passive voice without subject ("Sentinel-1 imagery was processed") would pass if "was" + method verb in context — but only if `_USAGE_POSITIVE` has the right passive forms
- The 200-char context window may miss a usage verb that appears earlier in the sentence

**Fix direction**: Expand `_USAGE_POSITIVE` to include acquisition verbs: "obtained", "acquired", "downloaded", "derived from", "provided by", "sourced from", "collected from", "retrieved from".

---

## RC-5: Sentinel Mission Version Collapse

**Severity**: MEDIUM  
**File**: `src/ingestion/knowledge/knowledge_loader.py` and `data/glossary_acronyms.json`

KB entity `SENTINEL` has pattern `\bsentinel[\s\-]?[123][abc]?\b` which matches Sentinel-1, Sentinel-2, and Sentinel-3. All matches are stored as entity name `"SENTINEL"`. The specific mission — which determines whether the paper is using SAR (Sentinel-1) or optical (Sentinel-2) data — is lost.

**Scientific significance**: Sentinel-1 and Sentinel-2 are fundamentally different instruments:
- Sentinel-1: C-band SAR, all-weather, active sensor, flood detection
- Sentinel-2: Multispectral optical, 10m resolution, land cover, NDWI

Conflating them in a graph makes it impossible to distinguish SAR-based flood mapping studies from optical NDWI studies.

**Current behavior**: Paper mentioning "Sentinel-1C" → entity `{name: "SENTINEL", type: "satellite"}`. The "-1C" version is discarded.

**Fix direction**: Maintain separate KB entries for SENTINEL-1, SENTINEL-2, SENTINEL-3 with distinct IDs and store the matched mission number.

---

## RC-6: Other Section Content Partially Inaccessible

**Severity**: MEDIUM  
**File**: `src/ingestion/pipeline.py:_reclassify_from_other()`

The `other` section averages 16 276 chars across 52 papers — larger than any named section except introduction. Content reaches `other` when:
- Section title doesn't match any router keyword
- Paper has non-standard structure (no explicit "Methods" section)
- "Data and Methodology" style compound titles (partially handled)

`_reclassify_from_other()` rescues content from `other` → `methods` only if:
1. `methods` is completely empty
2. `other` >= 3 000 chars
3. Method-phrase density >= 5 per 1 000 chars

This means:
- If `methods` already has any content, `other` content is NOT reclassified (even if it contains additional methods)
- `study_area`, `results`, `data_sources` content in `other` is never recovered
- Threshold of 5/1000 chars may be too high for dense papers

**Impact**: 16 276 chars of scientific content per paper on average that is only partially searchable. Entities in this content are found by satellite/method search (their `text` scope includes `other`) but not by metric search.

---

## RC-7: study_type Stored at Wrong JSON Path

**Severity**: MEDIUM  
**File**: `src/ingestion/pipeline.py` — serialization

`paper["study_type"]` = `None` in all serialized paper JSON files.  
`paper["entities"]["geo"]["study_type"]["label"]` = "case_study" (correctly extracted).

`apply_judge_verdict()` writes to `paper["entities"]["geo"]["study_type"]` on correction only — the field is never promoted to the top level. Any code reading `paper.get("study_type")` gets `None`.

**Impact**: Neo4j ingestion, ChromaDB metadata, and any API consumer that expects `paper.study_type` at the standard location gets `None`. This is a silent data loss — extraction succeeded but the result is unreachable via the expected path.

**Fix direction**: At serialization time, promote `entities.geo.study_type` and `entities.task` to top-level fields.

---

## RC-8: LLM Score Never Applied to Entity Scoring

**Severity**: LOW-MEDIUM  
**File**: `src/ingestion/pipeline.py:2376`

Entity acceptance formula:
```python
final_score = pattern*0.3 + context*0.3 + embedding*0.2 + llm*0.2
```

The LLM component is always 0 because per-entity judge is not implemented. The formula effectively becomes:
```python
final_score = pattern*0.3 + context*0.3 + embedding*0.2
```
capped at 0.8 maximum, then section boost is added.

This means the 0.3 acceptance threshold is calibrated for a maximum of 0.8 (without section boost), but the documentation/design assumed an LLM component of up to 0.2 additional. Low-confidence entities that would be excluded with LLM validation are accepted.

**Impact**: No observed false negatives from this issue in the audit. However, the scoring model is misrepresented — the formula shows 4 components but only 3 function.

---

## RC-9: Judge Prompt Template Leakage

**Severity**: LOW  
**File**: `src/ingestion/pipeline.py:2470`

The judge prompt includes an example response with `paper_id: '12345'`. Some papers return `llm_judge.paper_id = '12345'` — the LLM is outputting the example from the prompt rather than the actual paper_id.

Affected: ~4 of 52 papers (8%).

**Impact**: When `paper_id: '12345'` appears in `llm_judge`, the judge verdict is stored but the verdict may not accurately reflect the actual paper (LLM may have generated a generic response). `apply_judge_verdict()` is called on all non-failed/non-skipped verdicts, so false verdicts may be applied.

**Mitigation already in place**: `is_valid_judge_study_type_verdict()` and `is_valid_judge_task_verdict()` validate verdict structure before applying. Most invalid responses are caught. However, a structurally valid but content-incorrect response (LLM output "case_study" for a review paper) would not be caught.

**Fix direction**: Remove the `paper_id: '12345'` example from the prompt, or use a fictional but clearly-non-real ID like `"paper_id": "EXAMPLE_PAPER"`.

---

## RC-10: BFE Methods Loaded but Never Queried

**Severity**: INFO  
**File**: `src/ingestion/knowledge/knowledge_loader.py`, `src/ingestion/knowledge/entity_extractor.py`

`KnowledgeBase.bfe_methods` is populated by `_load_bfe_methods()` but `EntityExtractor` has no `extract_bfe_methods()` or equivalent method. The data is loaded into memory and discarded.

**Impact**: Zero — the BFE data has no effect on extraction. It is dead load.

---

## Fix Priority Order

| Priority | Fix | Expected gain |
|---|---|---|
| P0 | Add `methods` to `metric_text` scope | NSE/RMSE extraction for ~77% of papers |
| P0 | Expand `_USAGE_POSITIVE` with acquisition verbs | Satellite extraction for ~40-50% more papers |
| P1 | Load and apply `ontology_disambiguation_rules.json` | SCS/ANN/MLP disambiguation for ~20-30% of papers |
| P1 | Split SENTINEL into SENTINEL-1/2/3 KB entries | Mission specificity for all Sentinel papers |
| P2 | Promote study_type/task to top-level JSON fields | Fix for all downstream consumers |
| P2 | Add secondary metric "mentioned" extraction (no value required) | Captures metric names without adjacent values |
| P3 | Reclassify from other for study_area and results (not just methods) | Recover ~40% of misrouted content |
| P3 | Remove `paper_id: '12345'` from judge prompt example | Eliminate template leakage |
| P4 | Add METRIC_PATTERNS bare-mention fallback | Edge cases where value is in next sentence |
| P5 | Remove or document BFE methods dead code | Code clarity |
