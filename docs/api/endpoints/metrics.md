# Metrics and ontology

**Backing code**:
- `src/extraction/` (regex + table extractors, `numbers.py`, `metric_ontology.py`);
- `src/ingestion/knowledge` (KnowledgeBase);
- `src/normalization/ontology_matcher.py` (1,118 entities, 3,161 aliases).

**Backing data**: Neo4j `NumericFact` (25,049, 2026-10-02) and `data/analytics/numeric_facts.parquet` (24,884 rows from 990 papers).

**Valid ranges**, enforced as `range_verdict`; out-of-range values are never rescaled:

| Metric | Range |
|---|---|
| NSE, KGE, R² | ≤ 1 (no lower bound) |
| kappa | −1…1 |
| OA, F1, IoU, CSI, POD, precision, recall | 0…1 (a value in (1, 100] is read as a percentage only for these) |
| PBIAS | −100…+∞ % |
| RMSE, MAE | ≥ 0 |

---

## `GET /metrics/facts`
- **Status**: planned (phase 1, WP 1.8) · **Scope** `read` · S
- **Purpose**: reported metric values with their evidence. Example: "which papers report NSE ≥ 0.8 for SWAT?"

| Query param | Type | Notes |
|---|---|---|
| `metric` | string | canonical id (`metric.nse`) or alias (`NSE`) |
| `min` / `max` | float | value filter |
| `method` / `sensor` | string | canonical id; joins through the paper's entities |
| `paper_id` / `doi` | string | |
| `source` | `text \| table \| any` | default `any` |
| `range_verdict` | `ok \| suspect \| any` | default `ok` |
| `limit`, `cursor` | | |

**Response 200**: `{"items": [MetricFact], "next_cursor", "summary": {"n": int, "papers": int, "median": float, "p10": float, "p90": float}, "coverage", "provenance"}`

**Agent notes**:
- Table facts are parsed by GROBID and can be misaligned: confirm the value with `GET /papers/{id}/tables` before quoting it.
- Never compare values across papers that use different units (`unit` field) or different periods (calibration vs validation).

---

## `POST /metrics/extract`
- **Status**: planned (phase 1) · **Scope** `read` · S (no LLM)
- **Purpose**: extract metric values from text you supply, from a TEI document, or from a corpus paper. This path is deterministic and regex/table based.

**Request**: exactly one of:
- `{"text": string ≤ 50,000 chars}`
- `{"tei_xml": string}`
- `{"paper_id": string}`

Optionally `"metrics": ["metric.nse", …]` to restrict the output.

**Response 200**: `{"facts": [MetricFact], "rejected": [{"raw": "NSE = 1.7", "reason": "outside valid range (NSE ≤ 1)"}], "provenance"}`

**Behaviour**:
- Accepts the Unicode minus `−`, a decimal comma and a trailing `%`.
- A value whose metric name does not appear in the evidence text is not reported. This enforces the no-fabrication rule: "Percent" and topic words never become OA.

```json
{"text": "The calibrated model reached NSE = −0.27 at Kherson and an overall accuracy of 94.2 %."}
```
```json
{"facts": [
  {"metric": "metric.nse", "value": -0.27, "raw_value": "−0.27", "unit": null, "range_verdict": "ok", "source": "text",
   "evidence": {"text": "The calibrated model reached NSE = −0.27 at Kherson"}},
  {"metric": "metric.overall_accuracy", "value": 0.942, "raw_value": "94.2 %", "unit": "%", "range_verdict": "ok", "source": "text",
   "evidence": {"text": "an overall accuracy of 94.2 %"}}],
 "rejected": [], "provenance": {"…": "…"}}
```

---

## `POST /metrics/extract?mode=llm`
- **Status**: planned (phase 4) · **Scope** `llm` · **J**
- **Purpose**: two-level LLM extraction for a corpus paper (`SectionExtractor`). It catches values that are written in prose and that regex misses.
- **Request**: `{"paper_id": string, "metrics": string[]?}`.
- **Result** (job artifact): `{"facts": [MetricFact], "rejected": [...]}`.
- **Agent notes**:
  - Every value is checked to appear in the source text; values that fail are dropped, not repaired.
  - Prefer the deterministic endpoint. Use this one only when it finds nothing and the text clearly reports a value.

---

## `GET /metrics/ontology`
- **Status**: planned (phase 1) · **Scope** `read` · S
- **Purpose**: canonical metric definitions: `{"metrics": [{"canonical_id": "metric.nse", "name": "Nash–Sutcliffe efficiency", "aliases": ["NSE", "Nash-Sutcliffe"], "range": {"lo": null, "hi": 1}, "unit": null, "group": "hydrological_efficiency", "percent_scale": false}]}`.

---

## `POST /ontology/normalize`
- **Status**: planned (phase 1) · **Scope** `read` · S
- **Purpose**: map raw terms to ontology ids (methods, sensors, DEMs, metrics, parameters).

**Request**: `{"terms": [{"text": "Sentinel-1 SAR", "expected_type": "sensor"?, "context": "…"?}] (≤ 200), "allow_semantic": false}`.

**Response 200**: `{"results": [{"text": "Sentinel-1 SAR", "canonical_id": "sensor.sentinel_1", "display_name": "Sentinel-1", "type": "sensor", "match_type": "alias|exact|context|semantic|none", "confidence": 0.95}], "provenance"}`

**Agent notes**:
- `allow_semantic=true` adds an embedding fallback (accepted at ≥ 0.82), but those matches are less reliable: review them.
- Short acronyms collide (`HAND`, `ET`, `SAR`). Always pass `context` for terms of 4 characters or fewer.

---

## `GET /ontology/entities`
- **Status**: planned (phase 1) · **Scope** `read` · S
- **Query**: `type` (`method | sensor | metric | parameter | concept | dem`), `q` (substring), `limit`, `cursor`.
- **Response 200**: `{"items": [{"canonical_id", "display_name", "type", "aliases": string[], "parent"?}], "next_cursor", "provenance"}`.
