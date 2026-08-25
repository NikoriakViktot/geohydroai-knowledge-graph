# GeoHydroAI Scientific Object Database (SODB)
## Complete Architecture Design & Critical Analysis

**Classification: Production Architecture Whitepaper**
**Version: 1.0 | Date: 2026-05-18**
**Scope: GeoHydroAI — 3692-paper hydrology/flood/remote-sensing corpus**

---

## Preface: The Core Diagnosis

Before any design work, the diagnosis must be stated precisely.

The current `process_paper.py` task implements this flow:

```
TEI XML → TEIParser → TEIDocument → build_paper_json() → paper.json
```

The `NougatRegionPipeline` lives in a **completely separate module** (`ingestion/nougat_region_pipeline.py`) that is **not wired into** `process_paper.py`. It writes to `data/nougat_regions/` as JSON files but nothing downstream reads them systematically. The `table_extractor.py` runs as a separate script. The `ScientificFact` / `FactExtractionResult` objects in `extraction/models.py` are built and then **serialized directly into `paper.json`** without any intermediate persistent layer.

The system has all the right components. They are not connected through a shared evidential substrate.

The SODB is that substrate.

---

## Section 1 — Architectural Analysis: Why SODB Is Necessary

### 1.1 The Fundamental Error: Conflation of Parsing and Knowledge

The deepest architectural mistake in the current system is treating `paper.json` as both the **parsing output** and the **knowledge product**. These are two fundamentally different things:

```
Parsing output:   "This region on page 4 contains the token sequence
                   'NSE = 1 - Σ...'"

Knowledge product: "Paper XYZ claims that model HEC-HMS achieves NSE=0.82
                    under calibration conditions on watershed W280, evidenced
                    by Table 2, column 'Calibration', row 'W280'."
```

The journey from parsing output to knowledge product requires multiple independent transformation steps: coordinate-to-object reconstruction, Nougat inference, metric pattern matching, ontology grounding, calibration-vs-validation disambiguation, confidence scoring, and cross-reference validation. Collapsing all of this into `build_paper_json()` creates an opaque monolith where any single-step failure corrupts the entire output, and improving any single step requires rerunning the entire pipeline.

### 1.2 The Six Failure Modes of Direct paper.json Generation

**Failure 1: GPU Coupling**

In the current architecture, improving the `table_extractor.py` logic requires rerunning `process_paper.py`, which triggers GROBID re-parsing (60s/paper × 3692 papers = ~60 hours), re-embedding (20s/paper × 3692 = ~20 hours), and Ollama judge calls (~3 min/paper × 3692 = ~185 hours). None of this GPU/compute time is relevant to a table extraction improvement. The SODB decouples these: table extraction reads from `regions.parquet`, writes to `numeric_facts.parquet`. Zero GROBID. Zero Nougat. Zero embedding. Zero Ollama.

**Failure 2: Provenance Collapse**

When `build_paper_json()` writes `"nse": 0.82` into `paper.json`, the answer to "where did this number come from?" requires debugging the entire pipeline. With SODB, the answer is:

```
numeric_facts.parquet row → table_id → tables.parquet row → region_id → regions.parquet row
→ nougat_output field (the raw Nougat string that was parsed)
→ crop_path (the exact image that was sent to Nougat)
→ grobid_block_ids (the original GROBID coordinate blocks)
→ source_pdf + page + bbox (the exact pixels in the original PDF)
```

This is scientific evidence lineage. Without it, the system produces numbers without accountability.

**Failure 3: Non-Incremental Improvement**

Scientific information extraction is not a solved problem. The metric pattern matching in `table_extractor.py` will need refinement as new papers with unusual table formats arrive. The formula grounding will need extension. The figure classification will evolve. In a SODB-less architecture, every improvement to any extraction component requires full re-ingestion. In a SODB architecture, extraction components are **readers of `regions.parquet`** and **writers of their specific output parquet**. Improving formula extraction re-runs only formula extraction. This is the difference between a one-week improvement cycle and a one-day improvement cycle.

**Failure 4: Inability to Audit Scientific Claims**

The GeoHydroAI system makes scientific claims. "The mean NSE across 847 calibration results in this corpus is 0.74." This claim has no defensible basis if the pipeline cannot be audited. With SODB, every claim is traceable to specific `fact_id` rows, which trace to specific `region_id` rows, which trace to specific pixels in specific PDFs. Without SODB, the claim is a number in a JSON file.

**Failure 5: Disconnected Nougat Pipeline**

This is the most acute current problem. `NougatRegionPipeline` produces semantically rich output — LaTeX formulas, table markdown, figure regions — but this output is **not consumed by the main extraction pipeline**. It writes to `data/nougat_regions/*.json`. The `process_paper.py` task does not read these files. The Nougat infrastructure is producing output that feeds nothing.

The SODB resolves this by defining `regions.parquet` as the **handoff contract** between the Nougat pipeline and all downstream extraction components. Once Nougat inference writes to `regions.parquet`, every extraction component (formula extractor, table parser, figure classifier) reads from it.

**Failure 6: No Scientific Object Identity**

A formula is not a text string. A table is not a list of rows. A numeric fact is not a floating-point value. These are **scientific objects** with identity, provenance, semantic type, confidence, and relationships to other objects. The current system treats them as JSON values inside `paper.json`. The SODB promotes them to first-class persistent entities with stable identifiers, schemas, and version histories.

### 1.3 Scaling Risks in the Current Architecture

**Memory risk**: `build_paper_json()` holds the full `TEIDocument`, NER entities, embedding vectors, and ontology matches simultaneously in a single Ray task. For large papers (15+ pages, 50+ sections, 200+ references), this can exceed 2GB per task. With `--workers 3`, peak memory is 6GB just for task state.

**GPU risk**: Nougat inference is currently decoupled from the main pipeline. This means there is no mechanism to prioritize GPU work by downstream value (tables first, then formulas, then figures). The region pipeline processes in filesystem order, not value order.

**Consistency risk**: If `process_paper.py` completes for paper X but `nougat_region_pipeline.py` was never run for paper X, the `paper.json` for paper X has no formula LaTeX, no visual content, and no high-quality table reconstruction — but the registry marks it `SUCCESS`. The SODB stage flags make this inconsistency visible and actionable.

**Ontology risk**: The `_normalize_paper_entities()` call in `process_paper.py` runs ontology grounding in the same task as parsing, embedding, and Ollama validation. If the ontology changes (new aliases, new concepts), the entire paper must be reprocessed even though only the grounding step changed.

### 1.4 Why Layout-Aware Multimodal Parsing Changes Everything

The critical insight in `nougat_region_pipeline.py` is:

```
GROBID coordinates are semantic *anchors*, not final crop regions.
```

This is architecturally profound. GROBID produces bounding boxes that locate objects on the page. It does not produce the objects themselves. A table bbox from GROBID is a rectangle. The table is the semantic object with headers, body cells, row labels, column labels, caption, footnotes, and numeric content. Reconstructing the table from the bbox requires:

1. Merging adjacent GROBID blocks (caption + body + footnotes are separate blocks)
2. Context-expanding the merged region (adding whitespace for proper rendering)
3. Running Nougat inference on the expanded crop (producing markdown)
4. Parsing the markdown (extracting rows, headers, cell content)
5. Running metric pattern matching (identifying NSE, KGE, PBIAS columns)
6. Running row context analysis (identifying sub-basins, events, periods)
7. Running ontology grounding (normalizing "Nash-Sutcliffe" → `metric.nse`)

Steps 1–2 happen in `nougat_region_pipeline.py`. Steps 3–7 happen in `table_extractor.py` (steps 3–5) and normalization (steps 6–7). The SODB is the **shared persistent state** that connects these stages. `regions.parquet` is the output of steps 1–3. `tables.parquet` is the output of steps 4–5. `numeric_facts.parquet` is the output of steps 6–7.

Without SODB, these steps have no shared state. They must all run together in sequence, or the data flow breaks.

---

## Section 2 — Complete SODB Design

### 2.1 Design Principles

**Principle 1: Per-paper isolation.** Every paper's scientific objects live in a dedicated namespace. No paper can corrupt another paper's data. Reprocessing paper X does not touch paper Y.

**Principle 2: Stage-gated writes.** Each stage writes exactly one set of parquet files. A stage is complete when its parquet files exist with the correct `pipeline_version`. No partial writes are visible to downstream stages.

**Principle 3: Append-only indexes.** Global index files (`_index/*.parquet`) are rebuilt by DuckDB UNION queries over per-paper parquet files. They are derived views, not authoritative stores. The per-paper parquets are authoritative.

**Principle 4: Regions as the canonical handoff.** `regions.parquet` is the contract between the parsing layer (GROBID + Nougat) and the extraction layer (formula, table, figure extractors). Nothing downstream of the parsing layer should ever read raw TEI XML or raw Nougat JSON output files. It reads `regions.parquet`.

**Principle 5: Scientific objects have stable identity.** A `formula_id` computed as `SHA256(paper_id + page + latex_normalized[:64])` is stable across pipeline re-runs. The same formula extracted twice produces the same ID. This enables deduplication, versioning, and cross-paper formula matching.

### 2.2 Directory Structure

```
data/
├── sodb/
│   ├── {paper_id}/                          # per-paper namespace
│   │   ├── paper_metadata.parquet           # stage 0: identity + provenance
│   │   ├── regions.parquet                  # stage 1: parsing outputs (GROBID + Nougat)
│   │   ├── formulas.parquet                 # stage 2a: formula scientific objects
│   │   ├── tables.parquet                   # stage 2b: table scientific objects
│   │   ├── figures.parquet                  # stage 2c: figure scientific objects
│   │   ├── captions.parquet                 # stage 2d: all captions (cross-linked)
│   │   ├── numeric_facts.parquet            # stage 3: numeric evidence objects
│   │   ├── scientific_claims.parquet        # stage 4: natural-language claims
│   │   ├── provenance.parquet               # stage X: per-object provenance chain
│   │   ├── quality.parquet                  # stage X: per-paper quality assessment
│   │   ├── assets/
│   │   │   ├── figures/                     # extracted figure images
│   │   │   │   └── {figure_id}.png
│   │   │   ├── crops/                       # Nougat input crops (for audit)
│   │   │   │   └── {region_id}_{dpi}.png
│   │   │   └── tables/                      # CSV exports
│   │   │       └── {table_id}.csv
│   │   └── .sodb_manifest.json              # stage completion status + hashes
│   │
│   └── _index/                              # global derived views
│       ├── global_numeric_facts.parquet     # all numeric facts, all papers
│       ├── global_formulas.parquet          # all formulas, all papers
│       ├── global_figures.parquet           # all figures, all papers
│       ├── papers_summary.parquet           # per-paper quality + completion
│       └── pipeline_runs.parquet            # run history + stage timing
│
├── nougat_regions/                          # LEGACY — migrate to sodb/{id}/assets/crops/
├── literature/
│   ├── pdf/                                 # input PDFs (read-only)
│   └── grobid_xml/                          # GROBID TEI XML outputs
└── enriched/                                # paper.json outputs (derived from SODB)
```

### 2.3 The SODB Manifest

Each paper's `sodb/{paper_id}/.sodb_manifest.json` tracks stage completion:

```json
{
  "paper_id": "sha256_...",
  "sodb_version": "2.0",
  "stages": {
    "paper_metadata":    {"status": "complete", "pipeline_hash": "abc...", "written_at": "..."},
    "regions":           {"status": "complete", "pipeline_hash": "def...", "written_at": "..."},
    "formulas":          {"status": "complete", "pipeline_hash": "ghi...", "written_at": "..."},
    "tables":            {"status": "complete", "pipeline_hash": "jkl...", "written_at": "..."},
    "figures":           {"status": "pending",  "pipeline_hash": null,     "written_at": null},
    "numeric_facts":     {"status": "pending",  "pipeline_hash": null,     "written_at": null},
    "scientific_claims": {"status": "pending",  "pipeline_hash": null,     "written_at": null}
  },
  "region_count": 47,
  "nougat_success_rate": 0.94,
  "last_updated": "2026-05-18T14:23:11Z"
}
```

The `pipeline_hash` for each stage is `SHA256(code_version + config_hash)`. When a stage's code changes, its hash changes. The SODB writer checks: if the existing parquet has `pipeline_hash == current_hash`, the stage is skipped. This is the incremental reprocessing mechanism.

### 2.4 Global Index Rebuild Strategy

Global indexes are **never written incrementally**. They are rebuilt by DuckDB UNION queries:

```python
def rebuild_global_numeric_facts(sodb_root: Path) -> None:
    per_paper_paths = list(sodb_root.glob("*/numeric_facts.parquet"))
    if not per_paper_paths:
        return
    union_query = " UNION ALL ".join(
        f"SELECT * FROM read_parquet('{p}')" for p in per_paper_paths
    )
    duckdb.execute(f"""
        COPY ({union_query})
        TO '{sodb_root}/_index/global_numeric_facts.parquet'
        (FORMAT PARQUET, COMPRESSION ZSTD)
    """)
```

This is correct because global indexes are derived views. The per-paper parquets are authoritative. Rebuilding takes ~30 seconds for 3692 papers on modern hardware (DuckDB is extremely fast at UNION ALL over parquet files).

### 2.5 Caching Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                     Cache Layer Hierarchy                        │
├──────────────────────────────────────────────────────────────────┤
│                                                                  │
│  L1: Nougat Region Cache                                         │
│      Key:   SHA256(crop_image_bytes)                             │
│      Value: nougat_output_text + token_count + duration_ms       │
│      Store: data/sodb/_cache/nougat/{sha256[:2]}/{sha256}.json   │
│      TTL:   permanent (model version is in the key)              │
│      Hit rate on re-run: ~95%                                    │
│      Value: eliminates ALL GPU cost on pipeline re-runs          │
│                                                                  │
│  L2: SODB Stage Cache                                            │
│      Key:   (paper_id, stage, pipeline_hash)                     │
│      Value: stage parquet files exist + manifest entry           │
│      Store: .sodb_manifest.json per paper                        │
│      TTL:   permanent until pipeline_hash changes                │
│      Value: eliminates re-extraction on code changes             │
│                                                                  │
│  L3: OpenAlex API Cache                                          │
│      Key:   SHA256(doi or title_normalized)                      │
│      Value: OpenAlex JSON response                               │
│      Store: data/openalex_cache/{sha256[:2]}/{sha256}.json       │
│      TTL:   30 days                                              │
│      Value: eliminates all API calls on re-runs                  │
│                                                                  │
│  L4: Ontology Grounding Cache                                    │
│      Key:   SHA256(raw_text + entity_type)                       │
│      Value: (canonical_id, confidence, resolution_path)          │
│      Store: data/sodb/_cache/grounding/{sha256[:2]}/{sha256}.json│
│      TTL:   permanent until ontology version changes             │
│      Value: eliminates LLM calls for known entities              │
│                                                                  │
└──────────────────────────────────────────────────────────────────┘
```

The L1 cache is the highest-value single optimization in the entire system. Nougat GPU inference is the primary bottleneck (estimated 51 days for full corpus, single GPU). On any subsequent pipeline run, if the PDF has not changed (same crop images), 95%+ of Nougat inference is served from cache. This reduces subsequent runs from 51 days to approximately 6 hours (5% cache miss rate × 51 days + I/O overhead).

Implementation note: The cache key must include the Nougat model version. When upgrading from `nougat-base` to a future `nougat-large`, the cache is automatically invalidated because the model version changes. Store this as: `SHA256(f"{model_name}:{model_version}:{crop_sha256}")`.

### 2.6 Versioning and Reprocessing Model

```python
@dataclass
class StageVersion:
    stage_name:      str
    pipeline_version: str   # "2.3.1" — global pipeline semver
    code_hash:       str    # SHA256 of the stage's source files
    config_hash:     str    # SHA256 of the stage's config (patterns, thresholds)
    ontology_hash:   str    # SHA256 of ontology.json (for grounding stages)

    def cache_key(self) -> str:
        return hashlib.sha256(
            f"{self.code_hash}:{self.config_hash}:{self.ontology_hash}".encode()
        ).hexdigest()
```

**Reprocessing scenarios and cost**:

| Change | Stages affected | Nougat re-run? | GPU cost |
|--------|----------------|----------------|---------|
| Fix metric pattern in `table_extractor.py` | `numeric_facts` only | No | Zero |
| Add new formula grounding rule | `formulas`, `scientific_claims` | No | Zero |
| Update figure classifier model | `figures` only | No | Minimal (CPU) |
| New Nougat model version | `regions`, then all downstream | Yes | Full |
| Add new ontology alias | `numeric_facts`, `formulas` | No | Zero |
| Fix GROBID coordinate merge distance | `regions`, then all downstream | Partial | Partial |

---

## Section 3 — Provenance Architecture

### 3.1 The Complete Evidence Chain

Every scientific object in the SODB must be fully traceable from Neo4j node back to PDF pixel. The chain is:

```
Neo4j Node
    ↓  (node.fact_id or node.formula_id)
SODB numeric_facts.parquet / formulas.parquet
    ↓  (row.source_region_id)
SODB regions.parquet
    ↓  (row.crop_path)
PNG crop image (data/sodb/{paper_id}/assets/crops/{region_id}.png)
    ↓  (row.grobid_block_ids)
GROBID TEI XML (<figure> or <formula> element with coords attribute)
    ↓  (coords attribute: "6,120.00,450.00,400.00,600.00")
Original PDF (page 6, bbox [120, 450, 400, 600] in PDF points)
```

This chain must be reconstructable by any researcher, auditor, or debugging tool without human intervention.

### 3.2 provenance.parquet Schema

```python
PROVENANCE_SCHEMA = pa.schema([
    pa.field("provenance_id",     pa.string(), nullable=False),
    # SHA256(object_id + object_type + event_type + timestamp)
    pa.field("paper_id",          pa.string(), nullable=False),
    pa.field("object_id",         pa.string(), nullable=False),
    # The ID of the scientific object (formula_id, fact_id, etc.)
    pa.field("object_type",       pa.string(), nullable=False),
    # "formula" | "table" | "figure" | "numeric_fact" | "claim"
    pa.field("event_type",        pa.string(), nullable=False),
    # "extracted" | "grounded" | "validated" | "rejected" | "merged" | "overridden"
    pa.field("event_timestamp",   pa.timestamp("us"), nullable=False),
    pa.field("stage_name",        pa.string()),
    # "nougat_region_pipeline" | "table_extractor" | "formula_extractor" | "ontology_grounder"
    pa.field("stage_version",     pa.string()),
    pa.field("source_file",       pa.string()),
    # Source PDF path (for document-level provenance)
    pa.field("source_page",       pa.int32()),
    pa.field("source_bbox",       pa.string()),
    # JSON: {"x0": 120.0, "y0": 450.0, "x1": 400.0, "y1": 600.0}
    pa.field("source_parser",     pa.string()),
    # "grobid" | "nougat" | "hybrid" | "regex" | "llm"
    pa.field("parser_version",    pa.string()),
    pa.field("region_id",         pa.string()),
    # FK to regions.parquet — the GROBID+Nougat region that sourced this object
    pa.field("raw_source_text",   pa.string()),
    # The exact text/LaTeX/markdown that was the extraction input
    pa.field("extraction_rule",   pa.string()),
    # Pattern name or rule ID: "col_pattern.nse" | "latex_nse_formula"
    pa.field("confidence_before", pa.float32()),
    # Confidence before this event
    pa.field("confidence_after",  pa.float32()),
    # Confidence after this event
    pa.field("notes",             pa.string()),
    # Human-readable event description
    pa.field("is_correction",     pa.bool_()),
    # True if this event corrected a previous extraction
    pa.field("supersedes_id",     pa.string()),
    # If is_correction=True, the provenance_id of the record being corrected
])
```

**Critical design note**: `provenance.parquet` is **append-only**. No row is ever deleted or updated. Corrections create new rows with `is_correction=True` and `supersedes_id` pointing to the original. This creates an immutable audit log that can reconstruct the full history of how any scientific object was produced and refined.

### 3.3 Traceability Queries

With this provenance model, the following queries become straightforward:

```python
# "Show me every extraction event for formula XYZ"
duckdb.execute("""
    SELECT stage_name, event_type, source_parser, extraction_rule,
           confidence_before, confidence_after, notes
    FROM provenance
    WHERE object_id = 'formula_abc123' AND paper_id = 'sha256_def456'
    ORDER BY event_timestamp
""")

# "Which formulas were extracted by Nougat and later corrected?"
duckdb.execute("""
    SELECT DISTINCT p1.object_id, p1.paper_id
    FROM provenance p1
    JOIN provenance p2 ON p2.supersedes_id = p1.provenance_id
    WHERE p1.source_parser = 'nougat'
      AND p2.is_correction = true
      AND p1.object_type = 'formula'
""")

# "What is the average confidence drop for Nougat-extracted tables?"
duckdb.execute("""
    SELECT AVG(confidence_after - confidence_before) as confidence_delta
    FROM provenance
    WHERE source_parser = 'nougat'
      AND object_type = 'table'
      AND event_type = 'validated'
""")
```

### 3.4 Scientific Evidence Lineage for Neo4j

Every Neo4j node that represents a scientific claim must carry a `provenance_chain` property — a compressed JSON array of provenance IDs tracing the full path from PDF to KG node:

```cypher
CREATE (f:NumericFact {
    fact_id:       "sha1_...",
    value:         0.82,
    metric:        "NSE",
    provenance_chain: [
        "prov_001",  // extraction from TEI XML
        "prov_002",  // ontology grounding to metric.nse
        "prov_003",  // confidence validation
        "prov_004"   // KG loading
    ]
})
```

This makes every Neo4j node a verifiable scientific claim, not just a data point.

---

## Section 4 — Nougat Integration Strategy

### 4.1 The Swin-mBART Architecture: What It Means for Your Pipeline

The Swin Transformer encoder processes the input image as a hierarchy of local patches with shifted window attention. The critical property for the SODB integration is: **the encoder's attention is local**. A formula at position (x=200, y=500) in a full page image has its attention diluted by the entire remaining page. The same formula cropped to a 200×80px region has its full attention budget devoted to the formula content.

This is the mathematical justification for region-based parsing being superior to full-page parsing: by cropping, you increase the attention density on the scientifically relevant region by a factor proportional to `(page_area / crop_area)`. For a typical formula on an A4 page at 150 DPI: `(1240 × 1754) / (600 × 80) ≈ 45×` attention density improvement.

The mBART decoder's autoregressive generation means that an error in token position `t` propagates to all subsequent tokens. For a formula like `NSE = 1 - \frac{\sum_{i}(Q_{obs} - Q_{sim})^2}{\sum_{i}(Q_{obs} - \bar{Q})^2}`, a mis-tokenization of `\frac` at position 6 will produce syntactically broken LaTeX for the entire fraction. Region-based cropping reduces the probability of early errors by reducing the visual complexity the encoder must process.

### 4.2 Region Merging: Why MERGE_DISTANCE_Y = 120 Is a Design Decision, Not a Constant

The current `nougat_region_pipeline.py` uses:

```python
MERGE_DISTANCE_Y = 120   # ~1.7 cm in PDF points
MERGE_DISTANCE_X = 80    # ~1.1 cm in PDF points
```

These values encode an empirical claim: "In hydrology journal PDFs, a figure body and its caption are within 1.7 cm of each other vertically." This is approximately correct for journals using 12pt leading with standard margins, but it will fail for:

- **Compressed two-column layouts** (J. Hydrology uses tighter spacing → reduce to 80)
- **Large table footnotes** (sometimes 2+ cm below the table body → increase to 160)
- **Multi-panel figures** where each panel has its own caption (merging all panels is correct, but caption-per-panel suggests MERGE_DISTANCE_Y = 60 within-panel)

The SODB should store `merge_count` and `expanded_bbox` in `regions.parquet` so that these merge decisions are auditable and tunable without re-running Nougat.

### 4.3 The Full-Page Fallback: When and Why

The `PAGE_FULL_THRESHOLD` fallback in `nougat_region_pipeline.py` triggers full-page rendering when a region occupies too much of the page. This is architecturally correct but requires critical analysis.

**When full-page is correct**:
- Large hydrograph figures spanning 70%+ of a page — the multi-scale spatial relationships in the figure (legend, axes, curves) require the full spatial context that the Swin encoder provides
- Tables with complex multi-row headers that merge with the table body — tight cropping may cut the column headers

**When full-page is wrong**:
- Two-column pages where one column has a large figure — full-page mode will cause the mBART decoder to interleave text from both columns
- Pages with multiple large figures — full-page mode conflates multiple scientific objects into one Nougat output

**Recommendation**: Add a `column_isolation` check. If GROBID shows the document is two-column (detectable from coordinate distribution), never use full-page mode. Crop each column independently.

### 4.4 Hallucination Risk Taxonomy

Nougat hallucinations fall into four categories with different risk profiles:

**Category A: Token repetition loops**
Symptom: `\alpha \alpha \alpha...` or `NSE NSE NSE...` repeating infinitely.
Risk: HIGH (corrupts entire formula output)
Detection: Count consecutive repeated tokens. Threshold: >4 repetitions.
Mitigation: `nougat_quality.py` already handles this. Ensure it writes `is_hallucinated=True` to `regions.parquet`.

**Category B: Formula variable substitution**
Symptom: `\alpha` substituted for `\beta`, `Q_{sim}` for `Q_{obs}`, subscripts swapped.
Risk: MEDIUM (produces valid LaTeX but wrong semantics)
Detection: Cannot detect syntactically. Requires cross-reference with GROBID formula text (character-level comparison after LaTeX rendering).
Mitigation: Flag formulas where Nougat LaTeX and GROBID formula text differ significantly (edit distance > 0.3 on rendered text).

**Category C: Ghost structural elements**
Symptom: Nougat generates section headers or table rows that are not on the page.
Risk: LOW for tables (structured validation catches it), HIGH for text sections.
Detection: Row count in Nougat markdown table ≠ row count in GROBID TEI table element.
Mitigation: Row count comparison for tables. Accept GROBID table structure, use Nougat for cell content.

**Category D: LaTeX syntax errors**
Symptom: `\frac{NSE}{` (unclosed), `\sum_{i=1}^{n` (malformed), `$$NSE = ...` (wrong delimiter).
Risk: MEDIUM (causes downstream LaTeX rendering failures)
Detection: Run KaTeX or LaTeXML compilation check. Mark `tex_valid=False` in `formulas.parquet`.
Mitigation: Automated TeX validation in the formula extraction stage. ~15% of Nougat formula outputs fail this check.

### 4.5 The Coordinate-to-Object Gap: The Core of Region-Based Parsing

The deepest insight in the current system's design is:

```
GROBID coordinates ≠ scientific objects
```

This deserves elaboration. GROBID's TEI XML for a table looks like:

```xml
<figure type="table" coords="6,72.0,108.0,524.0,640.0">
  <head>Table 2.</head>
  <label>2</label>
  <figDesc>Calibration and validation statistics...</figDesc>
  <table>
    <row><cell>Watershed</cell><cell>NSE</cell>...</row>
    ...
  </table>
</figure>
```

The `coords` attribute gives a single bounding box. But the actual scientific object is:

1. The table header (rows 1–2, multi-level column headers)
2. The table body (rows 3–N)
3. The caption/title ("Table 2. Calibration and validation statistics...")
4. The footnotes (if any, potentially 50px below the table body)
5. The column header hierarchy (merged cells spanning multiple rows)
6. The row label structure (identifying sub-basins, periods, events)

GROBID provides items 1–4 partially. Items 5–6 require Nougat inference on the rendered region. The semantic region reconstruction in `nougat_region_pipeline.py` bridges this gap by merging blocks and providing context expansion. The SODB preserves this reconstruction work in `regions.parquet` so it is not repeated on every run.

---

## Section 5 — Scientific Object Model

### 5.1 Why `ScientificFact` in `extraction/models.py` Is Insufficient

The existing `ScientificFact` class in `extraction/models.py` is designed for the **current** extraction pattern: text-level NER producing satellite names, method names, and scalar metric values from running text. It is not designed for:

- **Structured table objects** with multi-level column contexts and calibration/validation period semantics
- **Formula objects** with LaTeX content, variable lists, and hydrology grounding
- **Figure objects** with chart type classification and semantic tags
- **Cross-object relationships** (a NumericFact lives in a Table which was extracted from a Region which corresponds to a GROBID bbox)

The SODB scientific object model must be **richer** than the current `ScientificFact`.

### 5.2 Base Protocol: `ScientificObject`

```python
from __future__ import annotations
from dataclasses import dataclass, field
from abc import ABC, abstractmethod
from typing import Protocol, runtime_checkable

@runtime_checkable
class ScientificObject(Protocol):
    """Protocol satisfied by all SODB scientific objects."""

    @property
    def object_id(self) -> str:
        """Stable, deterministic identifier."""
        ...

    @property
    def paper_id(self) -> str: ...

    @property
    def source_region_id(self) -> str | None:
        """FK to regions.parquet. None only for paper-level objects."""
        ...

    @property
    def confidence(self) -> float:
        """Extraction confidence 0.0–1.0."""
        ...

    def to_parquet_row(self) -> dict:
        """Serialize to a flat dict matching the parquet schema."""
        ...

    def to_neo4j_properties(self) -> dict:
        """Serialize to Neo4j node properties dict."""
        ...
```

### 5.3 Formula Object

```python
@dataclass(frozen=True)
class FormulaObject:
    """
    A mathematical formula extracted from a scientific paper.

    The formula_id is stable: same paper + same page + same normalized LaTeX
    always produces the same ID. This enables deduplication and cross-paper
    formula matching.
    """
    formula_id:           str          # SHA256(paper_id + page + latex_normalized[:64])
    paper_id:             str
    page:                 int
    latex:                str          # Full LaTeX: "NSE = 1 - \frac{...}{...}"
    latex_normalized:     str          # Variable-replaced form for deduplication
    semantic_type:        FormulaSemanticType
    # FormulaSemanticType: WATER_BALANCE | PERFORMANCE_METRIC | ROUTING |
    #                      PHYSICAL_LAW | STATISTICAL | UNKNOWN
    hydrology_grounding:  str | None   # "metric.nse" | "concept.water_balance" | None
    variables:            tuple[str, ...]  # ("NSE", "Q_obs", "Q_sim", "Q_bar")
    is_numbered:          bool
    equation_number:      str | None
    is_display:           bool         # True = standalone equation
    is_inline:            bool         # True = inline in text
    tex_valid:            bool         # KaTeX compilation check
    context_before:       str          # 2 sentences before
    context_after:        str          # 2 sentences after
    section_title:        str | None
    confidence:           float
    source_parser:        str          # "nougat" | "grobid" | "hybrid"
    source_region_id:     str | None   # FK to regions.parquet
    bbox:                 BBox | None  # None for Nougat-extracted (no bbox)
    pipeline_version:     str

    @property
    def object_id(self) -> str:
        return self.formula_id

    def is_hydrology_equation(self) -> bool:
        return self.hydrology_grounding is not None

    def variable_set(self) -> frozenset[str]:
        return frozenset(self.variables)
```

**Critical design note**: `bbox` is `None` for Nougat-extracted formulas. GROBID provides coordinates, Nougat does not. This asymmetry must be explicitly represented — not silently set to None. When `source_parser = "nougat"`, `bbox` is always `None`. When `source_parser = "grobid"`, `bbox` is always populated. When `source_parser = "hybrid"`, `bbox` comes from GROBID and LaTeX comes from Nougat.

### 5.4 Table Object and the Cell Matrix

```python
@dataclass
class TableObject:
    table_id:             str          # SHA256(paper_id + page + table_label)
    paper_id:             str
    page:                 int
    table_label:          str          # "Table 2", "Tab. 3"
    caption:              str
    semantic_type:        TableSemanticType
    # TableSemanticType: CALIBRATION_VALIDATION | COMPARISON | EVENT_SUMMARY |
    #                    DEM_ACCURACY | SUB_BASIN | PERFORMANCE | UNKNOWN
    hydrology_type:       str | None   # "streamflow_metrics" | "flood_accuracy" | None
    header_matrix:        list[list[str]]  # [row0_headers, row1_headers, ...]
    body_matrix:          list[list[str]]  # [row0_cells, row1_cells, ...]
    row_label_column:     int | None   # Index of the column containing row labels
    header_row_count:     int          # 1 or 2 for multi-level headers
    raw_markdown:         str          # Raw Nougat markdown output
    metrics_detected:     list[str]    # ["NSE", "KGE", "PBIAS"]
    has_numeric_facts:    bool
    numeric_facts_count:  int
    bbox:                 BBox | None
    source_parser:        str
    source_region_id:     str | None
    confidence:           float
    pipeline_version:     str
```

The key innovation here is **`header_matrix`** vs the existing approach of just storing `raw_markdown`. Multi-level column headers in hydrology calibration tables are common:

```
| Period      | Calibration (2010-2015) || Validation (2016-2020) |
|             | NSE   | KGE   | PBIAS   || NSE   | KGE   | PBIAS |
|-------------|-------|-------|---------||-----------------------|
| W280        | 0.82  | 0.79  | -3.2   || 0.71  | 0.68  | -5.1  |
```

The `header_matrix` is `[["Period", "Calibration (2010-2015)", "Validation (2016-2020)"], ["", "NSE", "KGE", "PBIAS", "NSE", "KGE", "PBIAS"]]`. The `column_context` for each NumericFact is derived by walking the column header matrix: fact for value 0.82 has `column_context = ["Calibration (2010-2015)", "NSE"]`. This is the critical information that existing `table_extractor.py` already extracts — the SODB formalizes it into a first-class object model.

### 5.5 NumericFact as the Atomic Evidence Unit

```python
@dataclass(frozen=True)
class NumericFactObject:
    """
    One atomic numeric measurement from a scientific table.

    This is the fundamental unit of quantitative scientific evidence.
    A NumericFact answers: "Which model, evaluated on which data,
    under which conditions, produced which score?"

    Invariant: every NumericFact is traceable to a specific cell in a
    specific table on a specific page of a specific PDF.
    """
    fact_id:         str       # SHA1(paper_id|table_id|col_ctx_hash|row_ctx_hash|value|seq)
    paper_id:        str
    table_id:        str       # FK to TableObject
    page:            int | None
    metric:          str       # "NSE"
    canonical_id:    str       # "metric.nse"
    metric_family:   str       # "efficiency"
    value:           float
    unit:            str | None
    raw_cell:        str       # "0.82" | "82%" | ".82"
    column_context:  tuple[str, ...]   # ("Calibration (2010-2015)", "NSE")
    row_context:     tuple[str, ...]   # ("W280",) or ("Sub-basin 3", "2020 event")
    period:          EvaluationPeriod  # CALIBRATION | VALIDATION | TESTING | UNKNOWN
    basin_id:        str | None        # Resolved watershed identifier
    event_id:        str | None        # Flood event identifier
    plausible:       bool              # value in [min_plausible, max_plausible]
    is_outlier:      bool              # value in valid range but statistically extreme
    confidence:      float
    source:          str               # "grobid_tei" | "nougat_table"
    pipeline_version: str
```

**The `period` field is not cosmetic — it is scientifically critical.** NSE=0.82 in calibration is expected and unremarkable. NSE=0.82 in validation is a strong result. NSE=0.71 in validation is a typical result. Aggregating all NSE values without period disambiguation produces a statistically meaningless distribution. The `EvaluationPeriod` enum must be inferred from `column_context` (contains "calibration", "validation", "testing") or from `row_context` (contains year ranges, "cal.", "val.").

---

## Section 6 — Numeric Fact Architecture

### 6.1 Why NumericFact Is the Primary Evidence Object

GeoHydroAI is a scientific intelligence system for a domain where performance is **quantified by specific metrics**. The entire value of the knowledge graph depends on correctly extracting, attributing, and reasoning over these numbers.

Consider the difference between:
- **Text-level extraction**: "The model achieved high performance."
- **NumericFact extraction**: `{metric: "NSE", value: 0.82, period: VALIDATION, basin: "W280", model: "HEC-HMS", paper: "doi:10.1016/j.jhydrol.2023...."}`

The first is qualitative and not comparable. The second enables:
- Meta-analysis: "What is the median validation NSE for HEC-HMS across all studies?"
- Contradiction detection: "Two papers report different NSE for the same watershed — investigate."
- Trend analysis: "Has NSE for SWAT improved over the past 10 years?"
- Method comparison: "For tropical watersheds, does LSTM outperform HEC-HMS by NSE?"

These are scientific intelligence capabilities that require correct NumericFact extraction. Every error in NumericFact extraction is a contamination of the scientific evidence base.

### 6.2 Metric Family Taxonomy

```python
class MetricFamily(str, Enum):
    EFFICIENCY    = "efficiency"      # NSE, KGE, KGE', KGEmod, R²
    ERROR         = "error"           # RMSE, MAE, MSE, MAPE, RSR, RE
    BIAS          = "bias"            # PBIAS, MBE, % bias
    CORRELATION   = "correlation"     # Pearson r, Spearman ρ, Kendall τ
    CLASSIFICATION = "classification" # OA, F1, Precision, Recall, IoU, Kappa, CSI, POD, FAR, HSS
    HYDRAULIC     = "hydraulic"       # MARD, depth RMSE, inundation extent error
    SPECTRAL      = "spectral"        # NDWI correlation, backscatter σ°
    VOLUMETRIC    = "volumetric"      # Volume error, flood volume, runoff coefficient
    TEMPORAL      = "temporal"        # Time to peak error, lag time error
    SPATIAL       = "spatial"         # Spatial overlap, Jaccard index, Hit rate
```

### 6.3 Plausibility Bounds

Every metric has physically or statistically defined plausibility bounds. These are not soft constraints — violating them indicates either an extraction error or a physically impossible result.

```python
METRIC_BOUNDS: dict[str, MetricBounds] = {
    "metric.nse":    MetricBounds(min=-np.inf, max=1.0, practical_min=-10.0,
                                  optimal=1.0, good_threshold=0.7,
                                  unit=None, family=MetricFamily.EFFICIENCY),
    "metric.kge":    MetricBounds(min=-np.inf, max=1.0, practical_min=-5.0,
                                  optimal=1.0, good_threshold=0.7,
                                  unit=None, family=MetricFamily.EFFICIENCY),
    "metric.pbias":  MetricBounds(min=-100.0, max=100.0, practical_min=-50.0,
                                  practical_max=50.0,
                                  optimal=0.0, good_threshold=10.0,
                                  unit="%", family=MetricFamily.BIAS),
    "metric.rmse":   MetricBounds(min=0.0, max=np.inf, practical_max=1000.0,
                                  optimal=0.0, good_threshold=None,
                                  unit="[same as observed]", family=MetricFamily.ERROR),
    "metric.oa":     MetricBounds(min=0.0, max=1.0, practical_min=0.5,
                                  optimal=1.0, good_threshold=0.85,
                                  unit=None, family=MetricFamily.CLASSIFICATION),
    "metric.f1":     MetricBounds(min=0.0, max=1.0,
                                  optimal=1.0, good_threshold=0.7,
                                  unit=None, family=MetricFamily.CLASSIFICATION),
    "metric.csi":    MetricBounds(min=0.0, max=1.0,
                                  optimal=1.0, good_threshold=0.6,
                                  unit=None, family=MetricFamily.CLASSIFICATION),
    "metric.pod":    MetricBounds(min=0.0, max=1.0,
                                  optimal=1.0, good_threshold=0.7,
                                  unit=None, family=MetricFamily.CLASSIFICATION),
    "metric.far":    MetricBounds(min=0.0, max=1.0,
                                  optimal=0.0, good_threshold=0.3,
                                  unit=None, family=MetricFamily.CLASSIFICATION),
    "metric.r2":     MetricBounds(min=0.0, max=1.0,
                                  optimal=1.0, good_threshold=0.7,
                                  unit=None, family=MetricFamily.CORRELATION),
}
```

**The `is_outlier` flag** is set when a value is within plausible bounds but statistically extreme relative to the distribution of that metric across the corpus. For example: NSE=0.99 in a calibration study is technically plausible (max=1.0) but occurs in <1% of studies. It should be flagged for review — it may indicate overfitting, a very simple model, or an extraction error.

Implementation: After building `global_numeric_facts.parquet`, compute per-metric distribution statistics (mean, std, p5, p95). Any value outside [p1, p99] is flagged `is_outlier=True`. This is computed at the global index rebuild stage, not at per-paper extraction time.

### 6.4 Column Context Disambiguation

The hardest problem in NumericFact extraction is **multi-period tables** where the same metric appears in multiple columns:

```
| Watershed | Calibration |     | Validation |      |
|           | NSE         | KGE | NSE        | KGE  |
|-----------|-------------|-----|------------|------|
| W280      | 0.82        |0.79 | 0.71       | 0.68 |
```

The `column_context` for the first NSE value is `["Calibration", "NSE"]` and the `period` is `CALIBRATION`. For the third column NSE value: `["Validation", "NSE"]` with `period = VALIDATION`. The `table_extractor.py` already handles this via multi-row header chain tracking — the SODB schema preserves this as a `list[str]` in `column_context` rather than collapsing it to a single header string.

This is architecturally important: **do not merge column header levels into a single string until the last moment**. Keep the full header chain as a list. The `col_header` field (merged) is for display; `column_context` (list) is for reasoning.

---

## Section 7 — Knowledge Graph Integration

### 7.1 The Paper-to-Graph Mapping

The SODB → Neo4j mapping is straightforward because every SODB parquet row maps to exactly one graph operation:

```python
SODB_TO_GRAPH_MAP = {
    "paper_metadata.parquet":   "MERGE (:Paper {paper_id: $paper_id}) SET p += $props",
    "formulas.parquet":         "MERGE (:Formula {formula_id: $formula_id}) SET f += $props",
    "tables.parquet":           "MERGE (:ScientificTable {table_id: $table_id}) SET t += $props",
    "figures.parquet":          "MERGE (:Figure {figure_id: $figure_id}) SET fig += $props",
    "numeric_facts.parquet":    "MERGE (:NumericFact {fact_id: $fact_id}) SET nf += $props",
    # Edges
    "formulas.parquet":         "MATCH (p:Paper {paper_id: $paper_id}), (f:Formula {formula_id: $formula_id}) "
                                "MERGE (p)-[:HAS_FORMULA {page: $page}]->(f)",
    "numeric_facts.parquet":    "MATCH (nf:NumericFact {fact_id: $fact_id}), (m:Metric {canonical_id: $canonical_id}) "
                                "MERGE (nf)-[:MEASURES]->(m)",
}
```

The crucial design principle: **the KG loader reads from SODB parquet, not from paper.json**. `paper.json` is a human-readable aggregation artifact. The KG is built from the structured, typed, provenance-tracked parquet rows.

### 7.2 Evidence Edges

Standard KG relationships (`HAS_FORMULA`, `USES_METHOD`) are well understood. The more important innovation is **evidence edges** — relationships that carry quantitative evidence as properties:

```cypher
// NumericFact is the evidence for a performance claim
(:Paper)-[:REPORTS_PERFORMANCE {
    value:      0.82,
    metric:     "NSE",
    period:     "validation",
    confidence: 0.85,
    fact_id:    "sha1_abc123",
    table_id:   "table_456",
    page:       6
}]->(:Metric {canonical_id: "metric.nse"})

// The fact itself carries full provenance
(:NumericFact {fact_id: "sha1_abc123", value: 0.82})-[:MEASURES]->(:Metric)
(:NumericFact)-[:SOURCED_FROM]->(:ScientificTable)
(:NumericFact)-[:FOR_BASIN]->(:Watershed)
(:NumericFact)-[:EVALUATED_IN {period: "validation"}]->(:EvaluationPeriod)
```

This dual representation — edge properties for queries, NumericFact node for deep provenance — is the correct architecture for scientific evidence graphs. The edge property enables fast aggregate queries:

```cypher
MATCH (:Paper)-[r:REPORTS_PERFORMANCE]->(:Metric {canonical_id: "metric.nse"})
WHERE r.period = "validation"
RETURN avg(r.value), percentileCont(r.value, 0.5)
```

While the NumericFact node enables deep audit:

```cypher
MATCH (nf:NumericFact {fact_id: "sha1_abc123"})
RETURN nf.column_context, nf.row_context, nf.raw_cell, nf.source, nf.confidence
```

### 7.3 Contradiction Detection Queries

```cypher
// Papers reporting very different NSE for the same watershed, same model
MATCH (p1:Paper)-[:HAS_NUMERIC_FACT]->(f1:NumericFact {period: "validation"})
      -[:MEASURES]->(m:Metric {canonical_id: "metric.nse"}),
      (f1)-[:FOR_BASIN]->(w:Watershed)
MATCH (p2:Paper)-[:HAS_NUMERIC_FACT]->(f2:NumericFact {period: "validation"})
      -[:MEASURES]->(m),
      (f2)-[:FOR_BASIN]->(w)
WHERE p1 <> p2
  AND abs(f1.value - f2.value) > 0.20
RETURN p1.title, p2.title, f1.value, f2.value, w.name
ORDER BY abs(f1.value - f2.value) DESC

// Formula duplication across papers (same equation, different variable names)
MATCH (f1:Formula), (f2:Formula)
WHERE f1.latex_normalized = f2.latex_normalized
  AND f1.paper_id <> f2.paper_id
  AND f1.semantic_type = "performance_metric"
RETURN f1.paper_id, f2.paper_id, f1.latex, f2.latex
```

### 7.4 Meta-Analysis Query Pattern

```cypher
// Method performance comparison: median validation NSE by method
MATCH (p:Paper)-[:USES_METHOD]->(meth:Method),
      (p)-[:HAS_NUMERIC_FACT]->(nf:NumericFact {period: "validation"})
      -[:MEASURES]->(:Metric {canonical_id: "metric.nse"})
WHERE nf.plausible = true
  AND nf.value IS NOT NULL
WITH meth.display_name AS method,
     collect(nf.value) AS nse_values,
     count(DISTINCT p) AS paper_count
WHERE paper_count >= 5
RETURN method, paper_count,
       reduce(s=0.0, x IN nse_values | s + x) / size(nse_values) AS mean_nse,
       size([x IN nse_values WHERE x >= 0.7]) * 1.0 / size(nse_values) AS good_rate
ORDER BY mean_nse DESC
```

---

## Section 8 — GPU + Distributed Pipeline Analysis

### 8.1 The Real GPU Budget

For GeoHydroAI at 3692 papers:

```
Corpus statistics (estimated):
  Papers:              3,692
  Average pages:       12
  Average regions/page: 4.5  (1.2 figures + 1.8 tables + 1.5 formulas/page)
  Total regions:       3,692 × 12 × 4.5 = 199,368

Nougat throughput (facebook/nougat-base, RTX 3080 10GB):
  Single region (200×150 crop, 150 DPI):  ~8 seconds
  Single full page (1240×1654, 150 DPI):  ~25 seconds
  Batch of 4 small formulas:              ~12 seconds (3 s/formula)

Total GPU time (no cache, no batching):
  199,368 regions × 8 s = 1,594,944 s ≈ 18.5 days

With the L1 cache (95% hit rate on re-runs):
  Re-run GPU time: 199,368 × 5% × 8 s = 79,747 s ≈ 22 hours

With batching (formulas batched 4×):
  Formula regions (~40% of total): 79,747 regions × 8 s / 4 = 159,494 s
  Other regions (60%): 119,621 regions × 8 s = 956,968 s
  Total: 159,494 + 956,968 = 1,116,462 s ≈ 13 days (single GPU, first run)

With 2 GPUs (current config: num_gpus=0.5 × 2 actors):
  First run: 13 days / 2 = 6.5 days
  Re-run: 22 hours / 2 = 11 hours
```

The L1 Nougat cache is the single most impactful optimization. It must be the first thing implemented.

### 8.2 Ray Actor Topology for SODB Pipeline

```
Ray Cluster
├── Driver Process (orchestration)
│   ├── PipelineRegistry (DuckDB, single writer)
│   ├── SODBIndexer (global index rebuild, on-demand)
│   └── Task submission loop
│
├── Stage 1: Parsing (8 concurrent tasks)
│   ├── GROBIDWorker × 8          (num_gpus=0, num_cpus=1)
│   └── Output: TEI XML → stage1 complete in registry
│
├── Stage 2: Region Construction (2 actors)
│   ├── NougatActor × 2           (num_gpus=0.5, num_cpus=2)
│   │   └── L1 cache check before every inference
│   └── SODBRegionWriter × 2      (num_gpus=0, num_cpus=1)
│       └── Writes regions.parquet
│
├── Stage 3: Extraction (4 concurrent actors)
│   ├── TableExtractorActor × 2   (num_gpus=0, num_cpus=1)
│   │   └── Reads regions.parquet → writes tables.parquet, numeric_facts.parquet
│   ├── FormulaExtractorActor × 2 (num_gpus=0, num_cpus=1)
│   │   └── Reads regions.parquet → writes formulas.parquet
│   └── FigureClassifierActor × 2 (num_gpus=0, num_cpus=1)
│       └── Reads regions.parquet → writes figures.parquet
│
├── Stage 4: Grounding (4 concurrent actors)
│   └── OntologyGrounderActor × 4 (num_gpus=0, num_cpus=1)
│       └── L4 grounding cache → updates formulas, numeric_facts
│
└── Stage 5: Aggregation (8 concurrent tasks)
    └── PaperJsonBuilder × 8      (num_gpus=0, num_cpus=1)
        └── Reads all parquets → writes paper.json
```

**Critical constraint**: `SODBRegionWriter` must be separate from `NougatActor`. Nougat actors hold GPU memory; keeping them alive while writing to parquet holds GPU memory unnecessarily. The pattern: `NougatActor.infer()` returns a dict; `SODBRegionWriter.write()` takes the dict and writes to parquet. GPU memory is released between inference and write.

### 8.3 Priority-Based Region Scheduling

Not all regions are equal. GPU time should be allocated to regions in order of downstream value:

```python
class RegionPriority(IntEnum):
    # Priority 1: Tables with suspected NumericFacts (highest downstream value)
    TABLE_WITH_METRICS   = 1
    # Priority 2: Formulas (unique Nougat capability — GROBID cannot do this)
    FORMULA              = 2
    # Priority 3: Tables (general table reconstruction)
    TABLE_GENERAL        = 3
    # Priority 4: Figures with text (hydrographs, confusion matrices)
    FIGURE_WITH_TEXT     = 4
    # Priority 5: Pure image figures (photographs, satellite images)
    FIGURE_IMAGE_ONLY    = 5
    # Skip: regions where GROBID output is already high quality (table row count matches)
    SKIP_GROBID_SUFFICIENT = 99
```

**Prioritization heuristics**:
- A TABLE region with column headers matching `_COLUMN_PATTERNS` in `table_extractor.py` → Priority 1
- A FORMULA region (any formula bbox) → Priority 2
- A TABLE region with no metric keywords in caption → Priority 3
- A FIGURE region with caption containing "hydrograph", "discharge", "calibration" → Priority 4
- A FIGURE region with caption containing "location", "study area", "map" with no numeric content → Priority 5

### 8.4 Failure Recovery and Checkpointing

The SODB manifest provides natural checkpointing. If a Ray worker crashes mid-paper:

1. The paper's manifest shows which stages are `complete` vs `pending`
2. On restart, the paper is submitted only from the first incomplete stage
3. No completed work is redone

**Atomic write pattern**: Never write a parquet file partially. Use a `.tmp` suffix, write completely, then `os.rename()` to the final path. `os.rename()` is atomic on POSIX filesystems. The manifest is updated only after the rename succeeds.

```python
def atomic_write_parquet(data: list[dict], schema: pa.Schema,
                          output_path: Path) -> None:
    tmp_path = output_path.with_suffix(".tmp.parquet")
    table = pa.Table.from_pylist(data, schema=schema)
    pq.write_table(table, tmp_path, compression="zstd")
    os.rename(tmp_path, output_path)   # atomic on POSIX
```

**Stale worker detection**: Extend the existing `heartbeat_at` mechanism in `registry_db.py` to track SODB stage progress. If a worker has been in `stage = "nougat_inference"` for >30 minutes without a heartbeat, mark it stale and reset to the previous stage boundary.

---

## Section 9 — Hydrology-Specific Intelligence

### 9.1 Why Hydrology Papers Are Different

General-domain scientific papers are information-dense but structurally simple: abstract → introduction → methods → results → discussion. Hydrology papers have a characteristic structure that encodes scientific evidence in very specific ways:

**1. Performance tables are the primary evidence object**, not prose. The critical scientific claim in a hydrology paper is not "the model performed well" (which appears in prose) but `{NSE: 0.82, KGE: 0.79, PBIAS: -3.2, period: calibration, watershed: W280}` (which appears in Table 2). The entire scientific validity of the study rests on these numbers.

**2. Formulas define metrics, not just models**. In ML papers, formulas define architectures (loss functions, attention mechanisms). In hydrology papers, formulas define the performance criteria by which a model is judged. The NSE formula is not just a mathematical expression — it is the definition of what "good performance" means for that study. Grounding formulas to hydrology concepts is therefore equivalent to identifying the evidential standard of the paper.

**3. Spatial provenance is essential**. A calibration result of NSE=0.82 is uninterpretable without knowing the watershed. The same model on different watersheds may produce NSE values ranging from 0.3 to 0.95. Watershed linkage in the KG enables meaningful comparative analysis that is impossible from text alone.

**4. Temporal provenance is essential**. Calibration vs validation is the fundamental split in hydrology modeling. An NSE aggregated across calibration and validation periods is scientifically misleading. The SODB `period` field in `numeric_facts.parquet` is not a cosmetic metadata field — it is the primary filter for all meaningful meta-analysis.

### 9.2 Hydrology Formula Taxonomy for Grounding

```python
HYDROLOGY_FORMULA_PATTERNS = [
    # Performance metrics (highest priority)
    FormulaPattern(
        canonical_id = "metric.nse",
        latex_regex  = r"NSE\s*=\s*1\s*-\s*\\frac",
        variable_signature = frozenset({"NSE", "Q", "Q_{obs}", "Q_{sim}"}),
        semantic_type = FormulaSemanticType.PERFORMANCE_METRIC,
    ),
    FormulaPattern(
        canonical_id = "metric.kge",
        latex_regex  = r"KGE\s*=\s*1\s*-\s*\\sqrt",
        variable_signature = frozenset({"KGE", "r", "\\alpha", "\\beta"}),
        semantic_type = FormulaSemanticType.PERFORMANCE_METRIC,
    ),
    # Water balance
    FormulaPattern(
        canonical_id = "concept.water_balance",
        latex_regex  = r"P\s*=\s*Q\s*[+]\s*E[T]?\s*[+]",
        variable_signature = frozenset({"P", "Q", "ET", "E", "S", "\\Delta S"}),
        semantic_type = FormulaSemanticType.WATER_BALANCE,
    ),
    # Manning's equation
    FormulaPattern(
        canonical_id = "concept.manning_equation",
        latex_regex  = r"Q\s*=\s*\\frac{1}{n}",
        variable_signature = frozenset({"Q", "n", "A", "R", "S"}),
        semantic_type = FormulaSemanticType.ROUTING,
    ),
    # Rational method
    FormulaPattern(
        canonical_id = "concept.rational_method",
        latex_regex  = r"Q\s*=\s*C\s*[iI]\s*A",
        variable_signature = frozenset({"Q", "C", "i", "I", "A"}),
        semantic_type = FormulaSemanticType.ROUTING,
    ),
    # SCS curve number
    FormulaPattern(
        canonical_id = "concept.scs_cn",
        latex_regex  = r"S\s*=\s*\\frac{25400}{CN}\s*-\s*254",
        variable_signature = frozenset({"S", "CN"}),
        semantic_type = FormulaSemanticType.INFILTRATION,
    ),
]
```

**Grounding strategy**: Two-pass. First pass: regex match on LaTeX content. Second pass: variable set intersection — if extracted variables have >60% overlap with the pattern's `variable_signature`, accept the grounding with confidence proportional to overlap. This handles formulas where variable names differ (e.g., `E_{Nash}` instead of `NSE`) but the structure is identical.

### 9.3 Figure Semantic Classification for Hydrology

The figure classifier must recognize seven hydrology-specific figure types that contain scientifically extractable information:

```python
HYDROLOGY_FIGURE_TYPES = {
    "hydrograph": {
        "description": "Time series of discharge/stage with observed and simulated",
        "caption_keywords": ["hydrograph", "discharge", "observed", "simulated",
                             "calibration", "streamflow", "runoff"],
        "axis_patterns": {
            "x": r"(time|date|day|hour|year|month)",
            "y": r"(discharge|Q|flow|stage|runoff|m³|m3|mm/day)",
        },
        "legend_patterns": ["observed", "simulated", "HEC-HMS", "SWAT", "model"],
        "contains_numeric_evidence": True,
        "extraction_value": "HIGH",  # calibration plots are scientific evidence
    },
    "flood_extent_map": {
        "description": "Spatial map showing flood inundation extent",
        "caption_keywords": ["flood", "inundation", "extent", "mapping", "water body"],
        "color_indicators": ["blue", "cyan", "water"],
        "contains_numeric_evidence": False,
        "extraction_value": "MEDIUM",
    },
    "confusion_matrix": {
        "description": "Classification accuracy matrix (TP/TN/FP/FN)",
        "caption_keywords": ["confusion", "accuracy", "precision", "recall", "classification"],
        "structural_indicators": ["2x2_grid", "TP", "TN", "FP", "FN"],
        "contains_numeric_evidence": True,
        "extraction_value": "HIGH",
    },
    "dem_visualization": {
        "description": "Digital elevation model visualization",
        "caption_keywords": ["DEM", "elevation", "terrain", "topography", "SRTM", "ALOS"],
        "colormap_indicators": ["terrain", "viridis_r", "elevation_colormap"],
        "contains_numeric_evidence": False,
        "extraction_value": "LOW",
    },
    "watershed_map": {
        "description": "Watershed boundary with sub-basins and gauge stations",
        "caption_keywords": ["watershed", "basin", "catchment", "gauge", "sub-basin"],
        "structural_indicators": ["polygon_boundaries", "stream_network", "point_markers"],
        "contains_numeric_evidence": False,
        "extraction_value": "MEDIUM",  # extract watershed names and areas
    },
    "scatter_calibration": {
        "description": "Observed vs simulated scatter plot with 1:1 line",
        "caption_keywords": ["observed", "simulated", "scatter", "regression", "R²"],
        "axis_patterns": {
            "x": r"(observed|measured)",
            "y": r"(simulated|modeled|predicted)",
        },
        "contains_numeric_evidence": True,
        "extraction_value": "HIGH",
    },
    "satellite_composite": {
        "description": "Satellite imagery composite (optical or SAR)",
        "caption_keywords": ["Sentinel", "Landsat", "SAR", "backscatter", "imagery", "RGB"],
        "contains_numeric_evidence": False,
        "extraction_value": "LOW",
    },
}
```

**Figures with `extraction_value: "HIGH"` should be prioritized for Nougat inference** because they contain extractable numeric evidence. Figures with `extraction_value: "LOW"` (DEM visualizations, satellite composites) should be processed only after high-value regions.

### 9.4 Watershed Ontology Design

The watershed ontology must handle three levels of spatial granularity:

```
Global basin system (e.g., "Nile Basin")
    ↓
Country-level watershed (e.g., "Upper Blue Nile, Ethiopia")
    ↓
Study watershed (e.g., "Gilgel Gibe watershed, 5,000 km²")
    ↓
Sub-basin (e.g., "Sub-basin W280, 124 km²")
```

Watershed identity is ambiguous in the literature — the same watershed may be referred to by different names, alternative spellings, or sub-basin codes. The SODB resolves this via:

1. **Exact match**: "Gilgel Gibe" → `watershed.ethiopia.gilgel_gibe`
2. **Alias match**: "Blue Nile headwaters" → `watershed.ethiopia.blue_nile_upper`
3. **Area-based confirmation**: If a paper mentions a watershed name with area ~5000 km² in Ethiopia, confirm against the known area of candidate watersheds
4. **Sub-basin code resolution**: "W280" → resolve from context (what larger watershed, what paper series uses this notation)
5. **Unresolved**: If no match, create a provisional `watershed.unknown.{hash}` node and flag for manual review

---

## Section 10 — Production Implementation Plan

### 10.1 Critical Path Analysis

The following dependency graph determines implementation order:

```
┌─────────────────────────────────────────────────────────────┐
│  CRITICAL PATH: Must complete before SODB is functional      │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  1. sodb/schemas.py          (ALL parquet schemas)          │
│     ↓                                                       │
│  2. sodb/writer.py           (atomic write + manifest)      │
│     ↓                                                       │
│  3. L1 Nougat cache          (crop SHA256 → output text)    │
│     ↓                                                       │
│  4. regions.parquet writer   (nougat_region_pipeline →      │
│                               regions.parquet)              │
│     ↓                                                       │
│  5. table_extractor refactor (reads regions.parquet,        │
│                               writes numeric_facts.parquet) │
│     ↓                                                       │
│  6. paper_json_builder.py    (SODB parquets → paper.json)   │
│                                                             │
└─────────────────────────────────────────────────────────────┘

PARALLEL PATH (does not block critical path):
  - formula_extractor.py    (reads regions.parquet, writes formulas.parquet)
  - figure_classifier.py    (reads figures.parquet regions, writes figures.parquet)
  - sodb/reader.py          (DuckDB query layer over SODB parquets)
  - global index rebuild    (UNION ALL over per-paper parquets)
```

### 10.2 Phase 1: Foundation (Weeks 1–3)

**Week 1: Schema and writer infrastructure**

```python
# Create src/sodb/__init__.py
# Create src/sodb/schemas.py — single source of truth for all parquet schemas
# Create src/sodb/writer.py — SODBWriter with atomic write + manifest tracking
# Create src/sodb/reader.py — DuckDBReader wrapping SODB query layer

# Critical: implement L1 Nougat cache
# File: src/sodb/nougat_cache.py
class NougatInferenceCache:
    def get(self, crop_image: PIL.Image, model_key: str) -> str | None: ...
    def put(self, crop_image: PIL.Image, model_key: str, output: str) -> None: ...
    def cache_key(self, crop_image: PIL.Image, model_key: str) -> str:
        image_bytes = crop_image.tobytes()
        return hashlib.sha256(f"{model_key}:{hashlib.sha256(image_bytes).hexdigest()}".encode()).hexdigest()
```

**Week 2: Regions parquet integration**

Modify `nougat_region_pipeline.py` to write `regions.parquet` as its primary output (in addition to or replacing the current JSON file output). This is the most important integration point.

```python
# Modify NougatRegionPipeline to:
# 1. Check L1 cache before every inference call
# 2. Write regions.parquet per paper
# 3. Update .sodb_manifest.json after successful write
# 4. Store crop images to assets/crops/
```

**Week 3: Table extractor refactor**

Refactor `table_extractor.py` to be a SODB stage:
- Input: `regions.parquet` (TABLE_BODY regions with `nougat_output` field)
- Output: `tables.parquet` + `numeric_facts.parquet`
- Add `period` field (calibration/validation disambiguation)
- Add `basin_id` field (row context → watershed resolution)
- Add `metric_family` field

### 10.3 Phase 2: Scientific Objects (Weeks 4–6)

**Week 4: Formula extraction**

```python
# Create src/extraction/formula_extractor.py
# Input: regions.parquet (FORMULA regions)
# Process: parse Nougat LaTeX output, validate with KaTeX, extract variables,
#          run hydrology formula grounding
# Output: formulas.parquet
```

**Week 5: Figure classification**

```python
# Create src/extraction/figure_classifier.py
# Input: regions.parquet (FIGURE_BODY regions), asset crops
# Process: CLIP zero-shot classification against hydrology_figure_types
#          Caption-text classification as fallback
# Output: figures.parquet
```

**Week 6: Paper JSON builder from SODB**

```python
# Create src/aggregation/paper_json_builder.py
# Input: all SODB parquets for one paper
# Process: join parquets, apply conflict resolution, build paper.json v2
# Output: data/enriched/{paper_id}.paper.json
```

### 10.4 Phase 3: Graph and Analytics (Weeks 7–10)

**Week 7–8: Neo4j loader extension**

Extend `graph/build_graph.py` to load from SODB parquets (not just `paper.json`):
- Load `Formula` nodes from `global_formulas.parquet`
- Load `NumericFact` nodes from `global_numeric_facts.parquet`
- Load evidence edges with provenance properties
- Implement contradiction detection queries

**Week 9–10: Analytics layer**

- Implement `global_numeric_facts.parquet` rebuild
- Connect DuckDB query layer to dashboard
- Add meta-analysis query templates to dashboard pages

### 10.5 Technical Debt Risks

**Risk 1: The disconnected Nougat pipeline**
This is the highest-risk item. `NougatRegionPipeline` and `process_paper.py` are completely separate code paths. The SODB integration requires that `regions.parquet` becomes the shared contract. This requires modifying both paths to use the SODB manifest for coordination. If done wrong, papers can have a `regions.parquet` that does not match their `paper.json`.

**Mitigation**: Implement a manifest validator that checks consistency between all SODB parquets before `paper_json_builder.py` runs.

**Risk 2: Schema version drift**
Multiple parquet schemas across 10+ file types will diverge if not governed centrally. `src/sodb/schemas.py` must be the single source of truth. Any change to a schema must bump the `SODB_SCHEMA_VERSION` constant and trigger re-generation of affected parquets.

**Risk 3: DuckDB single-writer bottleneck**
The existing `registry_db.py` uses a single DuckDB connection with RLock. Extending this to SODB global index operations will create a bottleneck if global index rebuilds are triggered too frequently. Solution: global indexes are rebuilt in batch (nightly or post-run), not after every paper.

**Risk 4: Nougat model version transition**
When upgrading from `facebook/nougat-base` to a future model, the L1 cache becomes invalid for all 199,368 regions. This is a full GPU re-run (6.5 days with 2 GPUs). Mitigation: include model version in cache keys, keep the old model available for comparison during transition, run new model in parallel on a sample first.

**Risk 5: Ontology mutation**
Adding new aliases to the ontology should be a zero-cost operation (re-ground only affected entities). But renaming a `canonical_id` is catastrophic — it invalidates all `numeric_facts.parquet` rows that reference the old ID and all Neo4j nodes that use it. **Canonical IDs are immutable**. Only aliases change. This must be enforced by ontology version control.

### 10.6 Migration Strategy for Existing Corpus

The existing 3692 papers have been processed into `paper.json` files. The migration:

```
Phase A: Build SODB from existing artifacts (no re-ingestion)
  - Read existing paper.json files
  - Extract entities into SODB parquets
  - Populate paper_metadata.parquet from existing JSON metadata
  - Mark stage_status as "migrated" (not "complete") to distinguish from fresh extractions
  Status: Provides immediate analytics layer over existing corpus

Phase B: Backfill regions.parquet
  - Re-run nougat_region_pipeline for all papers (with L1 cache, ~22 hours per GPU)
  - Upgrade migrated SODB entries to complete stage_status

Phase C: Backfill formulas.parquet
  - Run formula_extractor over all regions.parquet
  - No GPU required (reads from parquet, Nougat output already cached)
  Status: All formulas available in SODB with provenance

Phase D: Upgrade numeric_facts.parquet
  - Re-run table_extractor with new schema (period, basin_id, metric_family)
  - Overwrite existing numeric_facts.parquet with upgraded schema
  Status: Full meta-analysis capability enabled
```

Phase A can be completed in <1 day and immediately enables DuckDB analytics over existing paper.json content. Phases B–D can run incrementally without blocking the main pipeline.

### 10.7 Testing Strategy

**Unit tests** (test each stage in isolation):
```python
def test_sodb_writer_atomic():
    """Verify that partial writes never produce readable parquet files."""

def test_manifest_pipeline_hash():
    """Verify that pipeline hash change correctly invalidates stage."""

def test_numeric_fact_period_extraction():
    """Verify calibration/validation disambiguation on known tables."""

def test_formula_grounding():
    """Verify NSE formula → metric.nse on known LaTeX strings."""

def test_nougat_cache_hit():
    """Verify cache hit returns identical output to original inference."""
```

**Integration tests** (test full stage pipelines):
```python
def test_regions_to_numeric_facts():
    """Run table_extractor on a known paper's regions.parquet, verify fact count and values."""

def test_sodb_to_paper_json():
    """Run paper_json_builder on known SODB, compare to expected paper.json."""

def test_paper_json_to_neo4j():
    """Run KG loader on known paper.json, verify node count and properties."""
```

**Scientific consistency validation**:
```python
def validate_numeric_facts_plausibility(sodb_root: Path) -> ValidationReport:
    """Check all numeric facts against plausibility bounds."""

def validate_formula_tex_syntax(sodb_root: Path) -> ValidationReport:
    """Check all Latex formulas for TeX syntax errors."""

def validate_provenance_chain_completeness(sodb_root: Path) -> ValidationReport:
    """Check that all scientific objects have complete provenance chains."""
```

---

## Appendix: The Invariants

These are the architectural invariants that must not be violated:

```
INVARIANT 1: regions.parquet is the handoff contract
  No extraction stage reads raw TEI XML or raw Nougat JSON files.
  All extraction stages read from regions.parquet.

INVARIANT 2: Scientific objects have stable, deterministic IDs
  formula_id   = SHA256(paper_id + page + latex_normalized[:64])
  fact_id      = SHA1(paper_id | table_id | col_ctx_hash | row_ctx_hash | value | seq)
  region_id    = SHA256(paper_id + page + str(grobid_block_ids))
  Reprocessing produces the same IDs for the same inputs.

INVARIANT 3: GROBID provides coordinates; Nougat provides LaTeX
  Never assign a bbox to a Nougat-extracted formula.
  Never use GROBID formula text as the canonical LaTeX (it is not LaTeX).
  The HybridParser already enforces this via parser_capabilities.

INVARIANT 4: Ontology canonical IDs are immutable
  metric.nse cannot be renamed to metric.nash_sutcliffe.
  Aliases are added; canonical IDs are never changed.
  Violation requires a full SODB migration.

INVARIANT 5: provenance.parquet is append-only
  No row is ever deleted or updated.
  Corrections create new rows with is_correction=True.
  This is the scientific audit log.

INVARIANT 6: paper.json is derived, not authoritative
  The KG loader reads from SODB parquets (via global indexes), not paper.json.
  paper.json is a human-readable export artifact.
  If paper.json and SODB disagree, SODB is authoritative.

INVARIANT 7: The L1 cache key includes the model version
  When Nougat model changes, all cache entries for that model are invalidated.
  Cache key = SHA256("{model_name}:{model_version}:{crop_sha256}")
```

---

*Document prepared from analysis of GeoHydroAI codebase at /home/niko/projects/knoweledg_graf*
*Primary files analyzed: src/ingestion/pipeline.py, src/ingestion/nougat_region_pipeline.py,*
*src/orchestration/process_paper.py, src/document/models.py, src/document/provenance.py,*
*src/extraction/models.py, src/extraction/table_extractor.py, src/registry/registry_db.py,*
*src/document/hybrid_parser.py, src/analytics/parquet_schema.py*
