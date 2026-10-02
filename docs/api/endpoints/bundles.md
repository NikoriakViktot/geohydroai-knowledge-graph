# Bundles — consumer artefacts into the layer of truth

Article repositories used to write files "for the knowledge graph" that nothing read: `graph.json`, `open_citations.json`, `paper2_graph.json`, `theses.json`. These endpoints import them under the project namespace, after contract validation.

---

## `POST /bundles/import`
- **Status**: planned (phase 4, WP 4.5) · **Scope** `write` · **J**
- **Purpose**: import a consumer bundle into Postgres (`project.*`, `biblio.cite_key`) and project it into Neo4j under `:Bundle*` labels.

**Request**: `multipart/form-data` or JSON:

| Field | Type | Notes |
|---|---|---|
| `project_id` | string | e.g. `floodstate-eo:paper3` |
| `kind` | `graph_json \| open_citations \| paper_graph \| theses \| atomic_claims \| references_bib` | |
| `document` | object / file | validated against the matching contract |
| `source` | `{repo, commit, path}` | stored with the rows (provenance) |
| `replace` | bool | `true` supersedes the previous import of the same `kind` (history kept) |

**Behaviour**:
- Node ids are namespaced (`floodstate-eo:paper3/TH-INT-01`).
- Writes to Neo4j are MERGE only.
- `?Placeholder` references become `NEEDS_SOURCE` rows, which discovery can pick up later.

**Result**: `{"imported": {"theses": 51, "refs": 175, "search_queries": 112, "quotations": 27}, "rejected": [{"loc", "msg"}], "superseded_import_id"?}`

**Errors**: `422 VALIDATION_FAILED`, with every offending field listed. A theses file whose `refs` or `search_queries` are strings (a CSV dump) is rejected, not coerced.

---

## Contract schemas
See `GET /schemas/{name}` in [system.md](system.md#get-schemasname).

---

## Validation endpoints

`POST /theses/validate` ([evidence.md](evidence.md#post-thesesvalidate)) checks a document without importing it. Use it in the consumer's CI.
