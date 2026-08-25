# SDOM/SODB Agent Execution Plan

> **Audience**: AI agents, automated pipelines, CI/CD tooling  
> **Purpose**: Precise, file-reference-anchored execution plan for SDOM/SODB migration  
> **Format**: Phase → Task → Files → Changes → Dependencies → Validation → Output → Rollback → Risk  
> **Date**: 2026-05-18  
> **Status**: AUTHORITATIVE — supersedes SDOM_MIGRATION.md task ordering

---

## Execution Index

| Phase | ID | Task | Risk | Files |
|---|---|---|---|---|
| 0 | P0-T1 | Fix RC-1: metric_text bug | LOW | pipeline.py |
| 0 | P0-T2 | Fix RC-4: satellite extraction | LOW | pipeline.py |
| 0 | P0-T3 | Load disambiguation rules | LOW | pipeline.py, ontology_loader.py |
| 1 | P1-T1 | Define regions.parquet schema | LOW | parquet_schema.py |
| 1 | P1-T2 | Wire NougatRegionPipeline → regions.parquet | MEDIUM | nougat_region_pipeline.py |
| 1 | P1-T3 | Add L1 Nougat cache | MEDIUM | nougat_parser.py, nougat_actor.py |
| 1 | P1-T4 | Read regions.parquet in process_paper | MEDIUM | process_paper.py |
| 1 | P1-T5 | Wire NumericFact extraction into pipeline | MEDIUM | process_paper.py, table_extractor.py |
| 2 | P2-T1 | Create SODBWriter | MEDIUM | NEW: src/document/sodb_writer.py |
| 2 | P2-T2 | Create SODB manifest system | MEDIUM | NEW: src/document/sodb_manifest.py |
| 2 | P2-T3 | Migrate formulas.parquet | MEDIUM | NEW: src/extraction/formula_extractor.py |
| 2 | P2-T4 | Migrate tables.parquet | MEDIUM | table_extractor.py |
| 2 | P2-T5 | Migrate numeric_facts.parquet | LOW | table_extractor.py, table_kg_loader.py |
| 3 | P3-T1 | Enforce parser boundary | HIGH | pipeline.py, process_paper.py |
| 3 | P3-T2 | Extract KG synchronizer | HIGH | NEW: src/graph/kg_synchronizer.py |
| 3 | P3-T3 | Extract vector synchronizer | HIGH | NEW: src/retrieval/vector_synchronizer.py |
| 3 | P3-T4 | PaperAssembler from parquet | HIGH | NEW: src/assembly/paper_assembler.py |
| 4 | P4-T1 | Decompose pipeline.py god object | CRITICAL | pipeline.py |
| 4 | P4-T2 | Delete tei_to_sections.py | LOW | tei_to_sections.py |
| 4 | P4-T3 | Async GeoNames actor | MEDIUM | NEW: src/actors/geonames_actor.py |

---

## Phase 0: Critical Bug Fixes (No Architecture Changes Required)

### P0-T1: Fix RC-1 — metric_text Missing Methods Section

#### Files
- `src/ingestion/pipeline.py` (primary — ~line 472)

#### Changes

Current state:
```python
# pipeline.py ~line 472
metric_text = (
    paper.get("abstract", "") + " " +
    paper.get("results", "")
)
```

Required change:
```python
metric_text = (
    paper.get("abstract", "") + " " +
    paper.get("methods", "") + " " +
    paper.get("results", "")
)
```

Also check and update any secondary metric extraction sites: grep `pipeline.py` for `metric_text` and `extract_metrics` to locate all call sites. Confirm no other section-exclusion patterns at lines 350-600.

#### Dependencies
- None. Standalone fix.

#### Validation
```bash
# Run on 5-paper test subset
python -m src.ingestion.pipeline --papers data/test_subset/ --dry-run

# Expected: NSE/KGE/RMSE appear in extracted metrics
# Verify with: grep -c "NSE\|KGE\|RMSE" data/processed/*/metrics.json
```

#### Expected Output
- Increase NSE extraction from 0% to ~55-65% of papers containing methods sections
- No regression in entity extraction (NER operates on separate text path)

#### Rollback
- Revert the 3-line change. No state mutation, no data written.

#### Risk: LOW
- Pure addition. Does not change parsing logic. Cannot break existing extractions.

---

### P0-T2: Fix RC-4 — Satellite Extraction Missing Acquisition Verbs

#### Files
- `src/ingestion/pipeline.py` (search for `_USAGE_POSITIVE`)

#### Changes

Locate `_USAGE_POSITIVE` list. Add missing acquisition verbs:

```python
_USAGE_POSITIVE = [
    # existing entries preserved
    "use", "used", "using",
    "employ", "employed", "employing",     # ADD
    "apply", "applied", "applying",        # ADD
    "utilize", "utilized", "utilizing",    # ADD
    "incorporate", "incorporated",          # ADD
    "leverage", "leveraged",                # ADD
    "acquire", "acquired",                  # ADD
    "retrieve", "retrieved",                # ADD
    "obtain", "obtained",                   # ADD
    "download", "downloaded",               # ADD
    "collect", "collected",                 # ADD
]
```

Also check RC-5 collapse: locate Sentinel pattern (likely a regex or dict normalization). Sentinel-1, Sentinel-2, Sentinel-3 should NOT be collapsed to generic "SENTINEL" — they are distinct sensors with different band/resolution profiles.

#### Dependencies
- P0-T1 should run first (same file, avoid merge conflicts)

#### Validation
```bash
# Test on papers with known Sentinel references
python -m src.ingestion.pipeline --papers data/test_sentinel/ --dry-run
grep -c "Sentinel-[123]" data/processed/*/entities.json
```

#### Expected Output
- Satellite entity extraction increase from ~27% to ~55-65% of papers with satellite mentions
- Sentinel-1/2/3 appear as distinct entities, not collapsed

#### Rollback
- Revert list additions. No state mutation.

#### Risk: LOW

---

### P0-T3: Load Disambiguation Rules

#### Files
- `src/ingestion/pipeline.py` (search for `ontology_disambiguation_rules`)
- `src/ontology/ontology_loader.py` (if exists)
- `data/ontology/ontology_disambiguation_rules.json` (source file — verify it exists)

#### Changes

1. Verify file exists:
```bash
ls data/ontology/ontology_disambiguation_rules.json
```

2. Locate where ontology files are loaded (search for `ontology_normalization_rules.json` — which IS loaded — to find the loading pattern).

3. Add parallel load for `ontology_disambiguation_rules.json` using the same pattern.

4. Find where disambiguation should be applied: after NER entity extraction, before KG grounding. Insert call to disambiguation function.

#### Dependencies
- Requires understanding of current ontology loading in pipeline.py (read lines 1-150 for import/init block)

#### Validation
```bash
# Test entity disambiguation
python -c "
from src.ontology import load_disambiguation_rules
rules = load_disambiguation_rules()
assert len(rules) == 15, f'Expected 15 rules, got {len(rules)}'
print('OK')
"
```

#### Expected Output
- 15 disambiguation rules active (e.g., "VIC" → VIC model, not Victoria lake)
- Measurable improvement in entity precision for ambiguous method names

#### Rollback
- Remove the load call. JSON file not modified.

#### Risk: LOW

---

## Phase 1: Connect Nougat Pipeline (Targeted Wiring, No Refactor)

### P1-T1: Define regions.parquet Schema

#### Files
- `src/analytics/parquet_schema.py` (extend existing schemas)

#### Changes

Add to `parquet_schema.py`:

```python
import pyarrow as pa

REGIONS_SCHEMA = pa.schema([
    pa.field("region_id",       pa.string(),  nullable=False),  # UUID
    pa.field("paper_id",        pa.string(),  nullable=False),
    pa.field("page",            pa.int32(),   nullable=False),
    pa.field("bbox_x0",         pa.float32(), nullable=False),
    pa.field("bbox_y0",         pa.float32(), nullable=False),
    pa.field("bbox_x1",         pa.float32(), nullable=False),
    pa.field("bbox_y1",         pa.float32(), nullable=False),
    pa.field("region_type",     pa.string(),  nullable=False),  # FORMULA|TABLE|FIGURE|TEXT
    pa.field("source_parser",   pa.string(),  nullable=False),  # GROBID|NOUGAT|HYBRID
    pa.field("nougat_text",     pa.string(),  nullable=True),
    pa.field("nougat_latex",    pa.string(),  nullable=True),
    pa.field("confidence",      pa.float32(), nullable=True),
    pa.field("crop_path",       pa.string(),  nullable=True),   # relative path to crop PNG
    pa.field("merge_group_id",  pa.string(),  nullable=True),   # if region was merged
    pa.field("pipeline_hash",   pa.string(),  nullable=False),  # reproducibility
    pa.field("created_at",      pa.timestamp("ms"), nullable=False),
])

FORMULAS_SCHEMA = pa.schema([
    pa.field("formula_id",      pa.string(),  nullable=False),
    pa.field("paper_id",        pa.string(),  nullable=False),
    pa.field("region_id",       pa.string(),  nullable=True),   # FK → regions
    pa.field("xml_id",          pa.string(),  nullable=True),   # GROBID xml:id
    pa.field("page",            pa.int32(),   nullable=True),
    pa.field("text",            pa.string(),  nullable=True),   # plain text fallback
    pa.field("latex",           pa.string(),  nullable=True),   # Nougat LaTeX
    pa.field("formula_class",   pa.string(),  nullable=True),   # NSE|RMSE|CONTINUITY|...
    pa.field("confidence",      pa.float32(), nullable=True),
    pa.field("source_parser",   pa.string(),  nullable=False),
    pa.field("pipeline_hash",   pa.string(),  nullable=False),
    pa.field("created_at",      pa.timestamp("ms"), nullable=False),
])

NUMERIC_FACTS_SCHEMA = pa.schema([
    pa.field("fact_id",         pa.string(),  nullable=False),
    pa.field("paper_id",        pa.string(),  nullable=False),
    pa.field("source_region_id",pa.string(),  nullable=True),   # FK → regions
    pa.field("table_id",        pa.string(),  nullable=True),
    pa.field("table_label",     pa.string(),  nullable=True),
    pa.field("page",            pa.int32(),   nullable=True),
    pa.field("col_header",      pa.string(),  nullable=True),
    pa.field("row_context",     pa.string(),  nullable=True),
    pa.field("metric",          pa.string(),  nullable=False),  # NSE|KGE|RMSE|...
    pa.field("canonical_id",    pa.string(),  nullable=False),  # ontology ID
    pa.field("node_label",      pa.string(),  nullable=False),  # Neo4j label
    pa.field("value",           pa.float64(), nullable=True),
    pa.field("unit",            pa.string(),  nullable=True),
    pa.field("confidence",      pa.float32(), nullable=False),
    pa.field("basin_id",        pa.string(),  nullable=True),
    pa.field("period_type",     pa.string(),  nullable=True),   # CALIBRATION|VALIDATION|TEST
    pa.field("source",          pa.string(),  nullable=False),  # table_extractor|nougat_...
    pa.field("pipeline_hash",   pa.string(),  nullable=False),
    pa.field("created_at",      pa.timestamp("ms"), nullable=False),
])
```

#### Dependencies
- None (additive change to existing module)

#### Validation
```python
import pyarrow as pa
from src.analytics.parquet_schema import REGIONS_SCHEMA, FORMULAS_SCHEMA, NUMERIC_FACTS_SCHEMA
# Verify no duplicate field names
for schema in [REGIONS_SCHEMA, FORMULAS_SCHEMA, NUMERIC_FACTS_SCHEMA]:
    names = [f.name for f in schema]
    assert len(names) == len(set(names))
```

#### Expected Output
- Three new schemas importable from `src.analytics.parquet_schema`
- Schemas used by P1-T2, P2-T3, P2-T4, P2-T5

#### Rollback
- Remove the three schema definitions. No data written.

#### Risk: LOW

---

### P1-T2: Wire NougatRegionPipeline → regions.parquet

#### Files
- `src/ingestion/nougat_region_pipeline.py` (primary)
- `src/analytics/parquet_schema.py` (P1-T1 must be complete)

#### Changes

In `nougat_region_pipeline.py`, locate the output step. Currently writes to `data/nougat_regions/{paper_id}.json`. 

Add parallel parquet write after (do not remove JSON write until Phase 3):

```python
import pyarrow as pa
import pyarrow.parquet as pq
from src.analytics.parquet_schema import REGIONS_SCHEMA
from pathlib import Path
import uuid
from datetime import datetime, timezone

def _write_regions_parquet(regions: list[dict], paper_id: str, sodb_dir: Path, pipeline_hash: str) -> Path:
    out_dir = sodb_dir / paper_id
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "regions.parquet"
    
    rows = []
    for r in regions:
        rows.append({
            "region_id":      r.get("region_id", str(uuid.uuid4())),
            "paper_id":       paper_id,
            "page":           r["page"],
            "bbox_x0":        r["bbox"][0],
            "bbox_y0":        r["bbox"][1],
            "bbox_x1":        r["bbox"][2],
            "bbox_y1":        r["bbox"][3],
            "region_type":    r["region_type"],
            "source_parser":  r.get("source_parser", "HYBRID"),
            "nougat_text":    r.get("nougat_text"),
            "nougat_latex":   r.get("nougat_latex"),
            "confidence":     r.get("confidence"),
            "crop_path":      r.get("crop_path"),
            "merge_group_id": r.get("merge_group_id"),
            "pipeline_hash":  pipeline_hash,
            "created_at":     datetime.now(timezone.utc),
        })
    
    table = pa.Table.from_pylist(rows, schema=REGIONS_SCHEMA)
    # Atomic write: write to .tmp then rename
    tmp_path = out_path.with_suffix(".parquet.tmp")
    pq.write_table(table, tmp_path, compression="snappy")
    tmp_path.rename(out_path)
    return out_path
```

Insert call to `_write_regions_parquet()` at end of pipeline run method.

#### Dependencies
- P1-T1 (REGIONS_SCHEMA must exist)
- `pipeline_hash` generation: `SHA256(f"{model_name}:{model_version}:{config_json}")`

#### Validation
```bash
python -c "
from src.ingestion.nougat_region_pipeline import NougatRegionPipeline
import pathlib
p = NougatRegionPipeline()
result = p.run('data/test_subset/test_paper.pdf', 'test_paper')
assert pathlib.Path('data/sodb/test_paper/regions.parquet').exists()
import pyarrow.parquet as pq
t = pq.read_table('data/sodb/test_paper/regions.parquet')
print(f'regions written: {len(t)}')
"
```

#### Expected Output
- `data/sodb/{paper_id}/regions.parquet` written after each pipeline run
- Schema-conformant, snappy-compressed
- Atomic write (no partial reads possible)

#### Rollback
- Remove `_write_regions_parquet` call from pipeline. JSON output untouched.

#### Risk: MEDIUM
- New I/O path. Risk: disk full, permission errors. Mitigation: atomic write pattern.

---

### P1-T3: Add L1 Nougat Cache

#### Files
- `src/document/nougat_parser.py` (primary — `NougatParser.parse_image`)
- `src/actors/nougat_actor.py` (secondary — `parse_image` method)

#### Changes

In `NougatParser.parse_image()`:

```python
import hashlib
import json
import pickle
from pathlib import Path

_L1_CACHE_DIR = Path(os.getenv("NOUGAT_L1_CACHE_DIR", "data/nougat_cache/l1"))

def _cache_key(self, image_bytes: bytes) -> str:
    info = self.model_info()
    prefix = f"{info['model_name']}:{info['model_version']}"
    img_hash = hashlib.sha256(image_bytes).hexdigest()
    return hashlib.sha256(f"{prefix}:{img_hash}".encode()).hexdigest()

def parse_image(self, image, paper_id: str):
    # Serialize image to bytes for cache key
    import io
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    image_bytes = buf.getvalue()
    
    key = self._cache_key(image_bytes)
    cache_path = _L1_CACHE_DIR / key[:2] / f"{key}.pkl"
    
    if cache_path.exists():
        with open(cache_path, "rb") as f:
            return pickle.load(f)
    
    # Actual inference
    result = self._run_inference(image)
    
    # Write cache (atomic)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = cache_path.with_suffix(".pkl.tmp")
    with open(tmp, "wb") as f:
        pickle.dump(result, f)
    tmp.rename(cache_path)
    
    return result
```

Cache directory uses 2-level sharding (`key[:2]/{key}.pkl`) to avoid inode exhaustion at 199k+ files.

#### Dependencies
- `NougatParser.model_info()` must return stable `model_name` and `model_version` (verify: `src/document/nougat_parser.py`)

#### Validation
```bash
# First call: should take ~8 seconds
time python -c "from src.document.nougat_parser import NougatParser; p = NougatParser(); ..."

# Second call on same image: should take <50ms
time python -c "same as above"

# Cache entry should exist
ls -la data/nougat_cache/l1/
```

#### Expected Output
- Second run of identical crop: <50ms (vs 8s inference)
- Cache directory sharded by first 2 hex chars
- Atomic writes prevent corrupt cache entries

#### Rollback
- Remove cache read/write logic from `parse_image`. Delete `data/nougat_cache/l1/` if needed.

#### Risk: MEDIUM
- Pickle deserialization risk: use only for internal model outputs. Never deserialize user-provided data.

---

### P1-T4: Read regions.parquet in process_paper

#### Files
- `src/orchestration/process_paper.py` (primary)
- `src/ingestion/nougat_region_pipeline.py` (must run before process_paper)

#### Changes

In `process_paper()` Ray task, after TEIParser step, add optional regions enrichment:

```python
from pathlib import Path
import pyarrow.parquet as pq

def _load_regions(paper_id: str, sodb_dir: Path) -> list[dict] | None:
    regions_path = sodb_dir / paper_id / "regions.parquet"
    if not regions_path.exists():
        return None
    table = pq.read_table(regions_path)
    return table.to_pylist()

# In process_paper body, after tei_doc is obtained:
sodb_dir = Path(os.getenv("SODB_DIR", "data/sodb"))
regions = _load_regions(paper_id, sodb_dir)
if regions:
    # Enrich TEIDocument formulas with Nougat LaTeX
    formula_regions = [r for r in regions if r["region_type"] == "FORMULA" and r.get("nougat_latex")]
    for formula in tei_doc.formulas:
        # Match by page proximity (rough) or xml_id if available
        # Simple approach: attach latex to formula.text if no latex yet
        pass  # implement matching logic
```

**Important**: This is an enrichment step. If `regions.parquet` is absent, processing continues normally (graceful degradation).

#### Dependencies
- P1-T1 (schema)
- P1-T2 (regions.parquet must be written before process_paper runs)
- `SODB_DIR` environment variable (default: `data/sodb`)

#### Validation
```bash
# Run process_paper on paper with regions.parquet present
# Verify formulas in output have latex field populated
python -c "
import json
data = json.load(open('data/processed/test_paper/paper.json'))
formulas = [s for s in data.get('formulas', [])]
print(f'Formulas with latex: {sum(1 for f in formulas if f.get(\"latex\"))}')
"
```

#### Expected Output
- Papers with regions.parquet have enriched formula representations
- Papers without regions.parquet process identically to current behavior
- No change to paper.json format (backwards compatible)

#### Rollback
- Remove `_load_regions` call from process_paper. No data modified.

#### Risk: MEDIUM

---

### P1-T5: Wire NumericFact Extraction into Pipeline

#### Files
- `src/orchestration/process_paper.py` (primary)
- `src/extraction/table_extractor.py` (NumericFact extraction logic already exists)
- `src/analytics/parquet_schema.py` (NUMERIC_FACTS_SCHEMA from P1-T1)

#### Changes

In `process_paper()`, after tables are extracted from TEIDocument:

```python
from src.extraction.table_extractor import TableExtractor
import pyarrow as pa
import pyarrow.parquet as pq

def _extract_and_persist_numeric_facts(
    tei_doc,
    paper_id: str,
    sodb_dir: Path,
    pipeline_hash: str
) -> list[dict]:
    extractor = TableExtractor()
    facts = extractor.extract_from_tei_doc(tei_doc)  # returns list[NumericFact]
    
    if not facts:
        return []
    
    rows = [
        {
            "fact_id":          f.fact_id,
            "paper_id":         f.paper_id,
            "source_region_id": None,  # enriched in Phase 2
            "table_id":         f.table_id,
            "table_label":      f.table_label,
            "page":             f.page,
            "col_header":       f.col_header,
            "row_context":      f.row_context,
            "metric":           f.metric,
            "canonical_id":     f.canonical_id,
            "node_label":       f.node_label,
            "value":            f.value,
            "unit":             f.unit,
            "confidence":       f.confidence,
            "basin_id":         None,
            "period_type":      None,
            "source":           f.source,
            "pipeline_hash":    pipeline_hash,
            "created_at":       datetime.now(timezone.utc),
        }
        for f in facts
    ]
    
    out_path = sodb_dir / paper_id / "numeric_facts.parquet"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    table = pa.Table.from_pylist(rows, schema=NUMERIC_FACTS_SCHEMA)
    tmp = out_path.with_suffix(".parquet.tmp")
    pq.write_table(table, tmp, compression="snappy")
    tmp.rename(out_path)
    return rows
```

Also verify `TableExtractor` has a method callable on `TEIDocument`. If it currently only accepts raw XML — add an adapter that converts `tei_doc.tables` to the expected input format.

#### Dependencies
- P0-T1 (metric_text fix improves what gets found)
- P1-T1 (NUMERIC_FACTS_SCHEMA)
- `TableExtractor` must have `extract_from_tei_doc()` or equivalent

#### Validation
```bash
# After running on test corpus
python -c "
import pyarrow.parquet as pq
from pathlib import Path
facts_files = list(Path('data/sodb').glob('*/numeric_facts.parquet'))
print(f'Papers with facts: {len(facts_files)}')
total = sum(len(pq.read_table(f)) for f in facts_files)
print(f'Total numeric facts: {total}')
"
# Expected: >10,000 facts from 667+ papers (matches prior table_kg_loader run)
```

#### Expected Output
- `data/sodb/{paper_id}/numeric_facts.parquet` written for every paper with tables
- Facts are queryable via DuckDB: `SELECT metric, AVG(value) FROM 'data/sodb/**/numeric_facts.parquet' GROUP BY metric`

#### Rollback
- Remove `_extract_and_persist_numeric_facts` call. No existing data modified.

#### Risk: MEDIUM

---

## Phase 2: SODB Foundation

### P2-T1: Create SODBWriter

#### Files
- NEW: `src/document/sodb_writer.py`

#### Changes

Create `src/document/sodb_writer.py`:

```python
"""
SODBWriter — atomic, schema-validated Parquet writer for SODB objects.

One instance per paper. All write methods are atomic (tmp → rename).
All writes are idempotent: calling twice produces same result.
"""
from __future__ import annotations
import os
from pathlib import Path
from datetime import datetime, timezone
from typing import Any
import pyarrow as pa
import pyarrow.parquet as pq

from src.analytics.parquet_schema import (
    REGIONS_SCHEMA,
    FORMULAS_SCHEMA,
    NUMERIC_FACTS_SCHEMA,
)

_SODB_ROOT = Path(os.getenv("SODB_DIR", "data/sodb"))


class SODBWriter:
    def __init__(self, paper_id: str, pipeline_hash: str, sodb_root: Path = _SODB_ROOT):
        self.paper_id = paper_id
        self.pipeline_hash = pipeline_hash
        self.paper_dir = sodb_root / paper_id
        self.paper_dir.mkdir(parents=True, exist_ok=True)

    def write_regions(self, rows: list[dict[str, Any]]) -> Path:
        return self._write("regions.parquet", rows, REGIONS_SCHEMA)

    def write_formulas(self, rows: list[dict[str, Any]]) -> Path:
        return self._write("formulas.parquet", rows, FORMULAS_SCHEMA)

    def write_numeric_facts(self, rows: list[dict[str, Any]]) -> Path:
        return self._write("numeric_facts.parquet", rows, NUMERIC_FACTS_SCHEMA)

    def _write(self, filename: str, rows: list[dict], schema: pa.Schema) -> Path:
        out_path = self.paper_dir / filename
        for row in rows:
            row.setdefault("pipeline_hash", self.pipeline_hash)
            row.setdefault("created_at", datetime.now(timezone.utc))
        table = pa.Table.from_pylist(rows, schema=schema)
        tmp = out_path.with_suffix(".parquet.tmp")
        pq.write_table(table, tmp, compression="snappy")
        tmp.rename(out_path)
        return out_path

    def path(self, filename: str) -> Path:
        return self.paper_dir / filename

    def exists(self, filename: str) -> bool:
        return (self.paper_dir / filename).exists()
```

#### Dependencies
- P1-T1 (schemas must exist)

#### Validation
```python
from src.document.sodb_writer import SODBWriter
w = SODBWriter("test_paper", "hash_abc")
path = w.write_regions([{
    "region_id": "r1", "paper_id": "test_paper", "page": 1,
    "bbox_x0": 0.0, "bbox_y0": 0.0, "bbox_x1": 100.0, "bbox_y1": 50.0,
    "region_type": "FORMULA", "source_parser": "HYBRID",
    "nougat_text": None, "nougat_latex": None, "confidence": None,
    "crop_path": None, "merge_group_id": None,
}])
assert path.exists()
```

#### Expected Output
- `src/document/sodb_writer.py` importable
- Used by P1-T2, P1-T5 refactors in Phase 3

#### Rollback
- Delete file. No references until Phase 3.

#### Risk: MEDIUM (new file, no existing callers until wired in Phase 3)

---

### P2-T2: Create SODB Manifest System

#### Files
- NEW: `src/document/sodb_manifest.py`

#### Changes

Create `src/document/sodb_manifest.py`:

```python
"""
SODBManifest — per-paper stage completion tracker.

Written as JSON (not Parquet) for human readability and atomic updates.
Format: .sodb_manifest.json in paper's SODB directory.
"""
from __future__ import annotations
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

StageStatus = Literal["pending", "running", "done", "failed", "stale"]

STAGES = [
    "grobid_parse",
    "region_extract",
    "nougat_infer",
    "formula_extract",
    "table_extract",
    "numeric_facts",
    "chunk_build",
    "kg_sync",
    "vector_sync",
]

_SODB_ROOT = Path(os.getenv("SODB_DIR", "data/sodb"))


class SODBManifest:
    def __init__(self, paper_id: str, pipeline_hash: str, sodb_root: Path = _SODB_ROOT):
        self.paper_id = paper_id
        self.pipeline_hash = pipeline_hash
        self.manifest_path = sodb_root / paper_id / ".sodb_manifest.json"
        self._data: dict = self._load()

    def _load(self) -> dict:
        if self.manifest_path.exists():
            with open(self.manifest_path) as f:
                return json.load(f)
        return {
            "paper_id": self.paper_id,
            "pipeline_hash": self.pipeline_hash,
            "stages": {s: {"status": "pending"} for s in STAGES},
        }

    def _save(self) -> None:
        self.manifest_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.manifest_path.with_suffix(".json.tmp")
        with open(tmp, "w") as f:
            json.dump(self._data, f, indent=2, default=str)
        tmp.rename(self.manifest_path)

    def is_done(self, stage: str) -> bool:
        entry = self._data["stages"].get(stage, {})
        return (
            entry.get("status") == "done"
            and self._data.get("pipeline_hash") == self.pipeline_hash
        )

    def mark_running(self, stage: str) -> None:
        self._data["stages"][stage] = {
            "status": "running",
            "started_at": datetime.now(timezone.utc).isoformat(),
        }
        self._save()

    def mark_done(self, stage: str) -> None:
        self._data["stages"][stage] = {
            "status": "done",
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }
        self._save()

    def mark_failed(self, stage: str, error: str) -> None:
        self._data["stages"][stage] = {
            "status": "failed",
            "failed_at": datetime.now(timezone.utc).isoformat(),
            "error": error,
        }
        self._save()

    def is_stale(self) -> bool:
        return self._data.get("pipeline_hash") != self.pipeline_hash
```

#### Dependencies
- None (standalone utility)

#### Validation
```python
from src.document.sodb_manifest import SODBManifest
m = SODBManifest("test_paper", "hash_v1")
assert not m.is_done("grobid_parse")
m.mark_done("grobid_parse")
m2 = SODBManifest("test_paper", "hash_v1")
assert m2.is_done("grobid_parse")
m3 = SODBManifest("test_paper", "hash_v2")
assert not m3.is_done("grobid_parse")  # stale
```

#### Expected Output
- `src/document/sodb_manifest.py` importable
- Manifest files written as `.sodb_manifest.json` alongside parquets

#### Rollback
- Delete file. No callers until wired.

#### Risk: LOW

---

### P2-T3: Migrate formulas.parquet

#### Files
- `src/document/nougat_parser.py` (formula extraction after Nougat inference)
- NEW: `src/extraction/formula_extractor.py`
- `src/document/sodb_writer.py` (P2-T1)

#### Changes

Create `src/extraction/formula_extractor.py`:

```python
"""
FormulaExtractor — extracts Formula objects from TEIDocument and classifies them.

Formula classification uses a two-pass approach:
  Pass 1: structural (LaTeX pattern matching) for NSE, KGE, PBIAS, continuity
  Pass 2: contextual (surrounding section text) for ambiguous cases
"""
from __future__ import annotations
import re
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.document.models import TEIDocument, Formula

_NSE_PATTERN = re.compile(r"\\(frac|sum).*Q_\{?sim|Nash|NSE|E_?n\b", re.IGNORECASE)
_KGE_PATTERN = re.compile(r"KGE|Kling.?Gupta", re.IGNORECASE)
_RMSE_PATTERN = re.compile(r"RMSE|\\sqrt.*\\sum.*\^2", re.IGNORECASE)

def _classify_formula(latex: str | None, text: str | None) -> str | None:
    src = latex or text or ""
    if _NSE_PATTERN.search(src): return "NSE"
    if _KGE_PATTERN.search(src):  return "KGE"
    if _RMSE_PATTERN.search(src): return "RMSE"
    return None

@dataclass
class FormulaRecord:
    formula_id: str
    paper_id: str
    region_id: str | None
    xml_id: str | None
    page: int | None
    text: str | None
    latex: str | None
    formula_class: str | None
    confidence: float | None
    source_parser: str

def extract_formulas(tei_doc) -> list[FormulaRecord]:
    records = []
    for f in tei_doc.formulas:
        latex = getattr(f, "latex", None) or None
        text = f.text or None
        records.append(FormulaRecord(
            formula_id=str(uuid.uuid4()),
            paper_id=tei_doc.paper_id,
            region_id=None,
            xml_id=f.xml_id,
            page=getattr(f, "page", None),
            text=text,
            latex=latex,
            formula_class=_classify_formula(latex, text),
            confidence=getattr(f, "confidence", None),
            source_parser=tei_doc.parser_kind.value,
        ))
    return records
```

In `process_paper.py`, after P1-T5 wiring, also extract and write formulas:

```python
from src.extraction.formula_extractor import extract_formulas
formula_records = extract_formulas(tei_doc)
if formula_records:
    writer.write_formulas([vars(r) for r in formula_records])
```

#### Dependencies
- P2-T1 (SODBWriter)
- P1-T3 (Nougat cache, so latex fields are populated)
- `TEIDocument.formulas` must have `latex` attribute (verify in `src/document/models.py`)

#### Validation
```bash
python -c "
import pyarrow.parquet as pq
from pathlib import Path
formula_files = list(Path('data/sodb').glob('*/formulas.parquet'))
nse_count = 0
for f in formula_files:
    t = pq.read_table(f, columns=['formula_class'])
    nse_count += sum(1 for c in t['formula_class'].to_pylist() if c == 'NSE')
print(f'NSE formulas found: {nse_count}')
"
```

#### Expected Output
- `data/sodb/{paper_id}/formulas.parquet` for papers with formulas
- NSE/KGE/RMSE formulas classified and queryable
- `formula_class` populated for ~30-40% of formulas (hydrology-domain papers)

#### Rollback
- Remove `write_formulas` call. Delete `src/extraction/formula_extractor.py`.

#### Risk: MEDIUM

---

### P2-T4: Migrate tables.parquet (Structural Upgrade)

#### Files
- `src/extraction/table_extractor.py` (extend existing)
- `src/analytics/parquet_schema.py` (add TABLES_SCHEMA)
- `src/document/sodb_writer.py` (add `write_tables` method)

#### Changes

Add `TABLES_SCHEMA` to `parquet_schema.py`:

```python
TABLES_SCHEMA = pa.schema([
    pa.field("table_id",        pa.string(),  nullable=False),
    pa.field("paper_id",        pa.string(),  nullable=False),
    pa.field("region_id",       pa.string(),  nullable=True),
    pa.field("xml_id",          pa.string(),  nullable=True),
    pa.field("page",            pa.int32(),   nullable=True),
    pa.field("label",           pa.string(),  nullable=True),
    pa.field("caption",         pa.string(),  nullable=True),
    pa.field("header_row",      pa.list_(pa.string()), nullable=True),
    pa.field("row_count",       pa.int32(),   nullable=True),
    pa.field("col_count",       pa.int32(),   nullable=True),
    pa.field("has_numeric_data",pa.bool_(),   nullable=True),
    pa.field("source_parser",   pa.string(),  nullable=False),
    pa.field("pipeline_hash",   pa.string(),  nullable=False),
    pa.field("created_at",      pa.timestamp("ms"), nullable=False),
])
```

Note: full table cells are stored separately in a `table_cells.parquet` (future, Phase 3) to avoid unbounded row nesting. This schema stores table metadata + first header row only.

#### Dependencies
- P2-T1 (SODBWriter for write_tables)
- `TEIDocument.tables` structure (verify: `src/document/models.py` — `Table.rows` is `tuple[tuple[str,...],...]`)

#### Validation
```bash
python -c "
import duckdb
result = duckdb.sql(\"SELECT COUNT(*), AVG(row_count) FROM 'data/sodb/**/tables.parquet'\").fetchall()
print(f'Total tables: {result[0][0]}, avg rows: {result[0][1]:.1f}')
"
```

#### Expected Output
- Table metadata queryable via DuckDB glob
- `has_numeric_data` flag enables fast filtering for NumericFact pipeline

#### Rollback
- Remove write_tables call. Schema addition is backwards compatible.

#### Risk: LOW (metadata only, no cell content yet)

---

### P2-T5: Migrate numeric_facts.parquet (Provenance Enrichment)

#### Files
- `src/extraction/table_extractor.py`
- `src/extraction/table_kg_loader.py` (existing — currently writes to Neo4j only)

#### Changes

In `table_kg_loader.py`, after Neo4j write, also write to SODB parquet:

1. Add `source_region_id` FK linkage: join on `table_id` → `tables.parquet` → `region_id`
2. Add `period_type` detection: search row_context/col_header for "calibration", "validation", "calib", "valid", "cal", "val"
3. Add `basin_id` detection: lookup study_area from paper entities

```python
# In table_kg_loader.py, add period_type detection
_CALIBRATION_TERMS = {"calibration", "calib", "cal", "training", "fitting"}
_VALIDATION_TERMS = {"validation", "valid", "val", "test", "verification"}

def _detect_period_type(row_context: str, col_header: str) -> str | None:
    combined = (row_context + " " + col_header).lower()
    tokens = set(combined.split())
    if tokens & _CALIBRATION_TERMS:
        return "CALIBRATION"
    if tokens & _VALIDATION_TERMS:
        return "VALIDATION"
    return None
```

Apply this function to each `NumericFact` before writing.

#### Dependencies
- P2-T1 (SODBWriter)
- P2-T4 (tables.parquet must exist for region_id linkage)
- P1-T5 (initial numeric_facts.parquet may already exist — this enriches it)

#### Validation
```bash
python -c "
import duckdb
result = duckdb.sql(\"
  SELECT period_type, COUNT(*) as n
  FROM 'data/sodb/**/numeric_facts.parquet'
  GROUP BY period_type
\").fetchall()
for row in result: print(row)
"
# Expected: CALIBRATION: ~X, VALIDATION: ~Y, None: ~Z
```

#### Expected Output
- `period_type` populated for facts where calibration/validation context is detectable
- `source_region_id` linked to `tables.parquet` region_id where available
- Full provenance chain: Neo4j node → fact_id → numeric_facts.parquet → regions.parquet → crop PNG

#### Rollback
- Remove period_type/basin_id enrichment. numeric_facts.parquet reverts to Phase 1 format.

#### Risk: LOW (enrichment only, existing writes unaffected)

---

## Phase 3: Architecture Enforcement

### P3-T1: Enforce Parser Boundary

#### Files
- `src/ingestion/pipeline.py` (remove direct lxml usage)
- `src/orchestration/process_paper.py` (verify clean)

#### Changes

In `pipeline.py`, locate all `lxml.etree` or `BeautifulSoup(xml_path)` calls used for document parsing (not for TEI post-processing). These should be in `build_paper_json()` and related functions.

Audit approach:
```bash
grep -n "lxml\|etree\|BeautifulSoup" src/ingestion/pipeline.py
# List all lines. For each:
# - Document parsing → replace with TEIParser call
# - TEI utility (e.g., find specific tags) → acceptable to keep temporarily
```

Replace `build_paper_json()` XML parsing with TEIDocument consumption:

```python
# Before:
def build_paper_json(xml_path: Path, ...) -> dict:
    tree = etree.parse(xml_path)  # REMOVE
    root = tree.getroot()         # REMOVE
    # ... 500 lines of lxml ...
    
# After:
def build_paper_json(tei_doc: TEIDocument, ...) -> dict:
    # Consume tei_doc.sections, tei_doc.formulas, etc.
    # tei_doc already parsed upstream
```

This requires `process_paper.py` to pass `tei_doc` to `build_paper_json()` instead of `xml_path`.

**Warning**: This is a large change. Do in sub-tasks:
1. Add `tei_doc` parameter to `build_paper_json()` alongside `xml_path`
2. Use `tei_doc` for all new code
3. Remove `xml_path` parsing once all callers updated
4. Final: remove `xml_path` parameter

#### Dependencies
- P1-T4 (process_paper already has tei_doc)
- P2-T1 through P2-T5 (SODB layer must exist before decomposing the old pipeline)

#### Validation
```bash
grep -n "lxml\|etree.parse\|BeautifulSoup" src/ingestion/pipeline.py
# Should return 0 matches for document-parsing usage
# (TEI utility usage still acceptable)
```

#### Expected Output
- `pipeline.py` no longer imports lxml for document parsing
- All document parsing goes through TEIParser → TEIDocument path
- No regression in paper.json content (integration test: diff outputs before/after)

#### Rollback
- Revert pipeline.py to pre-change state (git). No data mutations.

#### Risk: HIGH — Large refactor of production code path. Requires comprehensive integration tests before deploying.

---

### P3-T2: Extract KG Synchronizer

#### Files
- NEW: `src/graph/kg_synchronizer.py`
- `src/ingestion/pipeline.py` (remove inline Neo4j writes)
- `src/extraction/table_kg_loader.py` (consolidate Neo4j writes here or in new module)

#### Changes

Create `src/graph/kg_synchronizer.py`:

```python
"""
KGSynchronizer — reads SODB parquets and writes to Neo4j.

Operates on completed SODB state (all parquets written).
Idempotent: MERGE not CREATE for all Neo4j writes.
"""
from __future__ import annotations
from pathlib import Path
import pyarrow.parquet as pq
from neo4j import GraphDatabase

class KGSynchronizer:
    def __init__(self, neo4j_uri: str, neo4j_auth: tuple):
        self._driver = GraphDatabase.driver(neo4j_uri, auth=neo4j_auth)
    
    def sync_paper(self, paper_id: str, sodb_root: Path) -> dict:
        stats = {"numeric_facts": 0, "formulas": 0, "tables": 0}
        paper_dir = sodb_root / paper_id
        
        facts_path = paper_dir / "numeric_facts.parquet"
        if facts_path.exists():
            facts = pq.read_table(facts_path).to_pylist()
            stats["numeric_facts"] = self._write_numeric_facts(facts)
        
        return stats
    
    def _write_numeric_facts(self, facts: list[dict]) -> int:
        # Use MERGE to ensure idempotency
        cypher = """
        UNWIND $facts AS f
        MERGE (nf:NumericFact {fact_id: f.fact_id})
        SET nf += {
            paper_id: f.paper_id,
            metric: f.metric,
            value: f.value,
            unit: f.unit,
            confidence: f.confidence,
            period_type: f.period_type,
            basin_id: f.basin_id
        }
        MERGE (p:Paper {paper_id: f.paper_id})
        MERGE (p)-[:HAS_NUMERIC_FACT]->(nf)
        """
        with self._driver.session() as session:
            session.run(cypher, facts=facts)
        return len(facts)
```

Remove inline Neo4j writes from `pipeline.py` (locate by searching for `session.run` or `driver.session`). Replace with calls to `KGSynchronizer.sync_paper()`.

#### Dependencies
- P2-T5 (numeric_facts.parquet must be written with provenance before KG sync)
- P3-T1 (parser boundary enforced so KG sync has clean inputs)

#### Validation
```bash
# Run sync on 10 papers, verify Neo4j counts match parquet counts
python -c "
from src.graph.kg_synchronizer import KGSynchronizer
import os
s = KGSynchronizer(os.getenv('NEO4J_URI'), ('neo4j', os.getenv('NEO4J_PASS')))
stats = s.sync_paper('test_paper', Path('data/sodb'))
print(stats)
"
```

#### Expected Output
- Neo4j NumericFact nodes have `fact_id` matching `numeric_facts.parquet`
- Re-running sync produces same Neo4j state (MERGE idempotency)
- `pipeline.py` no longer contains inline Neo4j session management

#### Rollback
- Keep original Neo4j writes in pipeline.py. Delete `kg_synchronizer.py`.

#### Risk: HIGH — Neo4j is shared state. Test on staging/dev instance first.

---

### P3-T3: Extract Vector Synchronizer

#### Files
- NEW: `src/retrieval/vector_synchronizer.py`
- `src/orchestration/process_paper.py` (currently contains ChromaDB writes)

#### Changes

Create `src/retrieval/vector_synchronizer.py` following the same pattern as KGSynchronizer:
- Reads `chunks.parquet` from SODB
- Writes to ChromaDB collection
- Idempotent: upsert by `chunk_id`
- Supports batch sizes (default 500 chunks per upsert call)

Key consideration: ChromaDB upsert requires embeddings. Either:
- Option A: Store embeddings in `chunks.parquet` (adds ~6KB per chunk for float32[768])
- Option B: Re-embed at sync time using EmbeddingActor

Recommendation: **Option A** — store embeddings in parquet. Avoids EmbeddingActor dependency at sync time. Disk cost: 199k regions × 768 × 4 bytes = ~612MB for embeddings parquet (acceptable).

#### Dependencies
- P2-T1 (SODBWriter — add write_chunks method)
- chunks.parquet schema must include `embedding` as `pa.list_(pa.float32(), 768)`

#### Validation
```bash
# Verify ChromaDB count matches chunks.parquet count
python -c "
import chromadb, duckdb
client = chromadb.PersistentClient(path='data/chroma')
col = client.get_collection('papers')
chroma_count = col.count()
parquet_count = duckdb.sql(\"SELECT COUNT(*) FROM 'data/sodb/**/chunks.parquet'\").fetchone()[0]
print(f'ChromaDB: {chroma_count}, Parquet: {parquet_count}')
assert abs(chroma_count - parquet_count) < 100  # allow small drift
"
```

#### Rollback
- Keep ChromaDB writes in process_paper.py. Delete vector_synchronizer.py.

#### Risk: HIGH — ChromaDB is production retrieval store. Validate on copy first.

---

### P3-T4: PaperAssembler from Parquet

#### Files
- NEW: `src/assembly/paper_assembler.py`
- `src/ingestion/pipeline.py` (replace `build_paper_json()`)

#### Changes

`paper_assembler.py` reads all SODB parquets for a paper and assembles `paper.json`:

```python
"""
PaperAssembler — generates paper.json from SODB parquet files.

This is a pure read operation. No model inference, no external API calls.
paper.json is a derived artifact — it can always be regenerated from SODB.
"""
from pathlib import Path
import pyarrow.parquet as pq

class PaperAssembler:
    def __init__(self, sodb_root: Path):
        self.sodb_root = sodb_root

    def assemble(self, paper_id: str) -> dict:
        paper_dir = self.sodb_root / paper_id
        result = {"paper_id": paper_id}
        
        # Numeric facts summary
        facts_path = paper_dir / "numeric_facts.parquet"
        if facts_path.exists():
            facts = pq.read_table(facts_path).to_pylist()
            result["numeric_facts"] = facts
            result["metrics"] = list({f["metric"] for f in facts})
        
        # Formulas summary
        formulas_path = paper_dir / "formulas.parquet"
        if formulas_path.exists():
            formulas = pq.read_table(formulas_path).to_pylist()
            result["formulas"] = [
                {"latex": f["nougat_latex"] or f["text"], "class": f["formula_class"]}
                for f in formulas
            ]
        
        return result
```

paper.json becomes a **derived artifact** — regeneratable at any time from SODB. This is the correct relationship: SODB is source of truth, paper.json is a view.

#### Dependencies
- P2-T5 (all parquets must be populated)
- P3-T1 (parser boundary enforced)

#### Validation
```bash
# Regenerate paper.json from SODB for 10 papers, diff with originals
# Expect: metrics populated (was empty before), structure unchanged
```

#### Rollback
- Keep `build_paper_json()` in pipeline.py. Delete paper_assembler.py.

#### Risk: HIGH — Changes paper.json generation path. Run parallel for 2 weeks before switching.

---

## Phase 4: God Object Decomposition

### P4-T1: Decompose pipeline.py

#### Files
- `src/ingestion/pipeline.py` (2,148 lines → target <300 lines)
- NEW: `src/ingestion/stages/parse_stage.py`
- NEW: `src/ingestion/stages/extract_stage.py`
- NEW: `src/ingestion/stages/enrich_stage.py`

#### Changes

Target structure after decomposition:

```
src/ingestion/
├── pipeline.py          ← becomes orchestrator only (~200 lines)
│   - reads manifest
│   - calls stages in order
│   - handles retries at stage level
│   - writes to PipelineRegistry
│
├── stages/
│   ├── parse_stage.py   ← GROBID parse → regions/formulas/tables parquets
│   ├── extract_stage.py ← NumericFact extraction → numeric_facts parquet
│   ├── enrich_stage.py  ← OpenAlex/GeoNames enrichment
│   ├── chunk_stage.py   ← Chunking → chunks parquet
│   └── assemble_stage.py← paper.json assembly from parquets
```

**Migration strategy**: 
1. Create new stage files with extracted logic
2. Update `pipeline.py` to call stage functions (still same behavior)
3. Validate outputs identical to current pipeline
4. Remove original inline code from `pipeline.py`
5. `pipeline.py` becomes pure orchestrator

Do NOT attempt to do all of this in one commit. Each function group extraction is a separate PR.

#### Dependencies
- ALL previous phases must be complete and stable
- Comprehensive integration test baseline captured before starting

#### Validation
```bash
# Run full pipeline on 50-paper test set
# Compare paper.json output before/after decomposition
# Diff must be empty except for timestamp fields
```

#### Rollback
- Full git revert. Decomposition should be done in isolated branches.

#### Risk: CRITICAL — This is the most disruptive change. Execute only after Phases 0-3 stable in production.

---

### P4-T2: Delete tei_to_sections.py

#### Files
- `src/document/tei_to_sections.py` (delete)
- All callers (audit before deleting)

#### Changes

```bash
grep -rn "tei_to_sections\|from src.document.tei_to_sections" src/
# If any callers found: redirect to TEIParser path first
# If no callers found: safe to delete
rm src/document/tei_to_sections.py
```

#### Dependencies
- P3-T1 must be complete (parser boundary enforced — no lxml in pipeline.py)

#### Validation
```bash
python -c "import src.document.tei_to_sections"
# Should fail with ImportError after deletion
```

#### Expected Output
- Dead code removed
- No import resolution errors in remaining codebase

#### Rollback
- Restore from git history.

#### Risk: LOW (dead code if P3-T1 complete)

---

### P4-T3: Async GeoNames Actor

#### Files
- NEW: `src/actors/geonames_actor.py`
- `src/ingestion/pipeline.py` (replace synchronous GeoNames calls)

#### Changes

Create `src/actors/geonames_actor.py` following the pattern of `SpacyActor`:

```python
@ray.remote
class GeoNamesActor:
    def __init__(self):
        # Initialize GeoNames client/cache
        pass
    
    def lookup(self, place_names: list[str]) -> list[dict]:
        # Batch lookup with caching
        pass
```

In `pipeline.py`, replace synchronous GeoNames calls with:
```python
geo_future = geonames_actor.lookup.remote(place_names)
# ... do other work ...
geo_results = ray.get(geo_future)
```

#### Dependencies
- P4-T1 (pipeline.py decomposition makes injection point clear)

#### Validation
```bash
# Verify GeoNames lookups run concurrently with NER
# Measure wall-clock time for 100 papers before/after
```

#### Expected Output
- GeoNames lookups no longer block NER + embedding pipeline
- Estimated 15-20% throughput improvement per paper

#### Rollback
- Remove geonames_actor.py. Revert to synchronous calls.

#### Risk: MEDIUM (Ray actor introduces network failure modes)

---

## Cross-Cutting Concerns

### Pipeline Hash Computation

Used in manifest, all parquet `pipeline_hash` fields, and L1 cache keys:

```python
import hashlib, json

def compute_pipeline_hash(config: dict, code_version: str) -> str:
    payload = json.dumps({
        "code_version": code_version,
        "config": config,
    }, sort_keys=True)
    return "sha256:" + hashlib.sha256(payload.encode()).hexdigest()[:16]
```

`code_version` should be: `git rev-parse --short HEAD` at pipeline startup.  
`config` should include: model names, batch sizes, thresholds (anything that affects output determinism).

### SODB_DIR Environment Variable

All SODB-aware modules read `os.getenv("SODB_DIR", "data/sodb")`. Set this in `.env` or Ray runtime env:

```bash
export SODB_DIR=/mnt/data/geohydroai/sodb  # for large-scale runs
```

### Atomic Write Pattern

Every parquet write MUST use the tmp → rename pattern:

```python
tmp = out_path.with_suffix(".parquet.tmp")
pq.write_table(table, tmp)
tmp.rename(out_path)  # atomic on POSIX filesystems
```

Never write directly to the destination path — partial writes leave corrupt parquets.

### DuckDB Analytics Queries

After Phase 2 complete, the full corpus is queryable:

```sql
-- Average NSE by year
SELECT 
    SUBSTR(paper_id, 1, 4) AS year,
    AVG(value) AS avg_nse,
    COUNT(*) AS n_facts
FROM read_parquet('data/sodb/**/numeric_facts.parquet')
WHERE metric = 'NSE' AND period_type = 'VALIDATION'
GROUP BY year
ORDER BY year;
```

---

## Execution Order Summary

```
Phase 0 (Week 1):     P0-T1 → P0-T2 → P0-T3         [no dependencies between tasks]
Phase 1 (Month 1):    P1-T1 → P1-T2 → P1-T3          [P1-T1 must be first]
                      P1-T4 ← depends on P1-T2
                      P1-T5 ← depends on P1-T1, P0-T1
Phase 2 (Month 2-3):  P2-T1 → P2-T2 (parallel)
                      P2-T3, P2-T4 ← depend on P2-T1
                      P2-T5 ← depends on P2-T4
Phase 3 (Month 4-5):  P3-T1 (prerequisite for all Phase 3)
                      P3-T2, P3-T3, P3-T4 (parallel after P3-T1)
Phase 4 (Month 6+):   P4-T1 (all Phase 3 stable)
                      P4-T2 ← depends on P4-T1
                      P4-T3 ← depends on P4-T1
```

---

*Document synthesized from 11 project audit files. Authoritative on 2026-05-18.*  
*Supersedes task ordering in SDOM_MIGRATION.md where conflicts exist.*
