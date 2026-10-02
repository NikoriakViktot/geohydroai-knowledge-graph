# Metrics and ontology

**Backing code**:
- `src/extraction/` (regex + table extractors, `numbers.py`, `metric_ontology.py`);
- `src/ingestion/knowledge` (KnowledgeBase);
- `src/normalization/ontology_matcher.py` (1,118 entities, 3,161 aliases).

**Backing data**: Neo4j `NumericFact` (25,049, 2026-10-02) and `data/analytics/numeric_facts.parquet`: 24,884 rows from 990 papers, **GROBID tables only**. Text facts are extracted on request (`POST /metrics/extract`) and not stored yet.

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
- **Status**: implemented (2026-10-02; table facts) · **Scope** `read` · S
- **Purpose**: reported metric values with their evidence. Example: "which papers report NSE ≥ 0.8 for SWAT?" → `GET /metrics/facts?metric=NSE&min=0.8&method=method.swat`.

| Query param | Type | Notes |
|---|---|---|
| `metric` | string | canonical id (`metric.nse`) or a name (`NSE`, `Nash-Sutcliffe`); unknown names are `422` |
| `min` / `max` | float | value filter, inclusive |
| `method` / `sensor` | string | canonical id. Joins through the paper's entity edges that are **grounded** on its text (see graph.md) |
| `paper_id` / `doi` | string | one paper; its duplicate copies are included |
| `source` | `text \| table \| any` | default `any`. `text` returns nothing yet: use `POST /metrics/extract` |
| `range_verdict` | `ok \| suspect \| unknown_metric \| any` | default `ok` |
| `limit` (≤ 1,000), `cursor` | | |

**Response 200**: `{"items": [MetricFact], "next_cursor", "summary": {"n", "papers", "median", "p10", "p90"}, "coverage": {"papers_with_table_facts": 990, "source": "GROBID TEI tables only"}, "provenance"}`. The summary covers all matches, not just this page.

`MetricFact`:
- `{metric, label, value, value_hi?, raw_value?, unit?, unit_raw?, qualifier?, range_verdict, source: text|table, fact_id?, paper: PaperRef?, evidence}`;
- `evidence` = `{text?, passage_id?, section?, table_label?, col_header?, row_context[], page?}`.
- `unit` is set only when recognised. The table column often holds other text ("Eq. rainfall"); that goes to `unit_raw`.

**Agent notes**:
- Table facts are parsed by GROBID and can be misaligned: confirm the value with `GET /papers/{id}/tables` before quoting it.
- Never compare values across papers that use different units or different periods (calibration vs validation; see `row_context`).
- An absence ("no paper reports …") is about 990 papers' tables, not the corpus: state that denominator (R-SCI-3).

---

## `POST /metrics/extract`
- **Status**: implemented (2026-10-02; deterministic mode) · **Scope** `read` · S (no LLM)
- **Purpose**: extract metric values from text you supply, from a TEI document, or from a corpus paper (its sentences plus its tables).

**Request**: exactly one of:
- `{"text": string ≤ 50,000 chars}`
- `{"tei_xml": string ≤ 5 MB}`: a `DOCTYPE` or `ENTITY` declaration is refused (`422`); entities are never expanded.
- `{"paper_id": string}`

Optionally `"metrics": ["metric.nse", "KGE", …]` restricts the output; unknown names are `422`. `mode=llm` answers `501` (phase 4).

**Response 200**: `{"facts": [MetricFact], "rejected": [{"raw": "NSE of 1.7", "metric": "metric.nse", "reason": "outside valid range (Nash-Sutcliffe Efficiency ≤ 1)", "evidence"}], "paper": PaperRef?, "provenance"}`

**Rules**:
- A value is reported only when a metric name stands right before it in the same sentence. Examples:
  - "NSE = −0.27", "an overall accuracy of 94.2 %", "RMSE of 1.2 m", "KGE' value of 0.90";
  - "the R 2 value was found to be 0.98";
  - "NSE scores varied from 0.76 to 0.87": a range, with `value_hi` and `qualifier = range`.
- This enforces the no-fabrication rule: "Percent" and topic words never become OA. "Open access (OA) papers were 45 %" is not a metric.
- After a name, only the first value or range is read. "MAE values of 461, 421 and 503" gives 461. Lists are a known gap.
- Inequalities such as "NSE > 0.5" are returned with `qualifier = ">"`. They are usually acceptance criteria, not results.
- **Percentages**: with an explicit `%`, a value is divided by 100 for any metric bounded above by 1. Without it, a value in (1, 100] is a percentage only for bounded ratio metrics (OA, F1, IoU, kappa, precision, recall, POD, FAR, CSI …). An NSE of 1.7 is rejected, never rescaled.
- Accepts the Unicode minus `−`, a decimal comma and units such as m, mm/year, m³/s and %.
- Text-extracted metrics are listed in `GET /metrics/ontology` (`extracted_from_text = true`).

```json
{"text": "The calibrated model reached NSE = −0.27 at Kherson and an overall accuracy of 94.2 %."}
```
```json
{"facts": [
  {"metric": "metric.nse", "label": "Nash-Sutcliffe Efficiency", "value": -0.27, "raw_value": "NSE = −0.27", "unit": null, "range_verdict": "ok", "source": "text",
   "evidence": {"text": "The calibrated model reached NSE = −0.27 at Kherson and an overall accuracy of 94.2 %."}},
  {"metric": "metric.overall_accuracy", "label": "Overall Accuracy", "value": 0.942, "raw_value": "overall accuracy of 94.2 %", "unit": "%", "range_verdict": "ok", "source": "text", "evidence": {"…": "…"}}],
 "rejected": [], "paper": null, "provenance": {"…": "…"}}
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
- **Status**: implemented (2026-10-02) · **Scope** `read` · S
- **Purpose**: canonical metric definitions, 83 on 2026-10-02: `{"metrics": [{"canonical_id": "metric.nse", "name": "Nash-Sutcliffe Efficiency", "aliases": ["NSE", "Nash-Sutcliffe", …], "range": {"lo": null, "hi": 1.0}, "percent_scale": false, "group": "hydrological_modeling", "in_registry": true, "extracted_from_text": true}], "provenance"}`. A `null` bound means unbounded.

---

## `POST /ontology/normalize`
- **Status**: implemented (2026-10-02) · **Scope** `read` · S
- **Purpose**: map raw terms to ontology ids (methods, sensors, DEMs, metrics, parameters).

**Request**: `{"terms": [{"text": "Sentinel-1 SAR", "expected_type": "sensor"?, "context": "…"?}] (≤ 200), "allow_semantic": false}`.

**Response 200**: `{"results": [{"text": "Sentinel-1 SAR", "canonical_id": "sensor.sentinel_1", "display_name": "Sentinel-1", "type": "sensor", "match_type": "alias|exact|disambiguation|semantic|unknown", "confidence": 1.0}], "provenance"}`

With `allow_semantic = false` (the default), no embedding model is loaded and unmatched terms come back `unknown`.

**Agent notes**:
- `allow_semantic=true` adds an embedding fallback (accepted at ≥ 0.82), but those matches are less reliable: review them.
- Short acronyms collide (`HAND`, `ET`, `SAR`). Always pass `context` for terms of 4 characters or fewer.

---

## `GET /ontology/entities`
- **Status**: implemented (2026-10-02) · **Scope** `read` · S
- **Query**:
  - `type` ∈ `method | sensor | metric | parameter | concept | data | organization | system | uncertainty`;
  - `q`: a substring of the id, display name or an alias;
  - `limit` (≤ 1,000), `cursor`.
- **Response 200**: `{"items": [{"canonical_id", "display_name", "type", "aliases": string[], "definition"?}], "count", "next_cursor", "provenance"}`. 1,118 entities on 2026-10-02.
