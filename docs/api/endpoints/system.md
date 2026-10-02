# System endpoints

How to read an entry:
- **Status**: *implemented*, or *planned (phase N, WP x.y of `API_PLAN_v1/06_ROADMAP.md`)*.
- **Scope**: required API-key scope.
- **Mode**: **S** sync, **J** job (`202` + `Job`).
- Every 2xx JSON body includes `provenance` ([SCHEMAS](../SCHEMAS.md#provenance)) unless noted.

---

## `GET /health`
- **Status**: implemented (2026-10-02) · **Scope**: none · **Mode**: S
- **Purpose**: liveness of the service and of each dependency. Use it before a batch of calls and after any `503`.

**Response 200** (always `200` while the API process is up; per-dependency status inside):

| Field | Type | Notes |
|---|---|---|
| `status` | `ok \| degraded \| down` | `degraded` = API up, some dependency down |
| `dependencies` | object | `{postgres, neo4j, chroma, grobid, ollama, gemini}` → `{status: ok\|down\|disabled, latency_ms?, detail?}` |
| `version` | string | |

```json
{"status": "degraded", "version": "1.0.0",
 "dependencies": {"postgres": {"status": "ok", "latency_ms": 2},
                  "neo4j": {"status": "ok", "latency_ms": 5},
                  "chroma": {"status": "ok", "latency_ms": 11},
                  "grobid": {"status": "down", "detail": "container stopped; started on demand by ingest jobs"},
                  "ollama": {"status": "ok"}, "gemini": {"status": "ok", "detail": "quota 212/480 today (lane 3.1)"}}}
```

**Agent notes**: `grobid: down` is normal; ingest jobs start it. Do not retry search because GROBID is down.

---

## `GET /capabilities`
- **Status**: implemented (2026-10-02) · **Scope**: none · **Mode**: S
- **Purpose**: which endpoint groups are live on this deployment, and the limits in force. **Agents must check this before using an endpoint marked *planned* in this reference.**

**Response 200**: `{"api_version": "1.0.0", "groups": {"papers": true, "search": true, "graph": true, "metrics": true, "bibliography": true, "evidence": false, "generate": false, "acquisition": false, "jobs": true, "bundles": false, "admin": true}, "llm": {"enabled": true, "providers": ["gemini", "ollama"], "default_model": "gemini-3.1-flash-lite"}, "limits": {"read_rps": 20, "max_batch": {"quotes": 50, "doi_verify": 50, "resolve": 500}, "upload_mb": 100}}`

---

## `GET /stats`
- **Status**: implemented (2026-10-02) · **Scope**: `read` · **Mode**: S
- **Purpose**: corpus size and freshness, from the layer of truth plus projection states.

**Response 200**:

| Field | Type | Notes |
|---|---|---|
| `papers` | object | `{total, by_identity_status: {ok, no_doi, duplicate, title_doi_mismatch, truncated_json, not_a_paper}}` from `core.paper` |
| `files` | object | `{pdf, tei, paper_json, normalized, enriched, sodb, nougat_regions}` |
| `projections` | object[] | `{store, name, item_count, built_at, run_id}` from `core.projection_state`, e.g. Chroma `flood_papers_768d_v2`, Neo4j, parquet |
| `last_ingest_at` | datetime? | |

Reference values on 2026-10-02:
- 5,230 papers (4,212 ok, 792 no DOI, 206 duplicates, 8 title/DOI mismatches, 11 truncated JSON, 1 not a paper);
- Chroma v1: 1,354,158 chunks (v2 being rebuilt).

---

## `GET /manifest`
- **Status**: implemented (2026-10-02; live manifest — freezing is `POST /admin/manifest/freeze`, planned) · **Scope**: `read` · **Mode**: S
- **Purpose**: the corpus manifest that every answer is computed against. Record its id in your own run manifest.

**Response 200**: `{"corpus_manifest_id": "sha256…", "frozen": false, "papers": 5023, "collection": "flood_papers_768d_v2", "embedding_model": "allenai/specter2_base@3447645e", "retrieval_rules_version": "1.2.0", "identity_run_id": "…", "created_at": "…"}`.

A frozen manifest (see `POST /admin/manifest/freeze`) is immutable. Answers computed against it are reproducible as long as its projections are kept.

---

## `GET /schemas/{name}`
- **Status**: implemented (2026-10-02; files in `contracts/schemas/`) · **Scope**: none · **Mode**: S
- **Purpose**: JSON Schema of a contract, e.g. `GET /schemas/PaperIdentity.v1`. Validate your documents locally before sending them.
- **Path**: `name` = `<Contract>.v<major>`. Available today: `PaperIdentity.v1`, `PaperAlias.v1`, `PaperFile.v1`, `CohortMember.v1`, `Problem.v1`, `Provenance.v1`, `PaperRef.v1`, `ResolveResponse.v1`, `ResolveBatchRequest.v1`, `ResolveBatchResponse.v1`, `Labeler.v1`, `Thesis.v1`, `ThesisRef.v1`, `AtomicClaim.v1`, `PositiveControl.v1`, `CitationOccurrence.v1`, `ScreeningLabel.v1`, `QuoteCheck.v1`, `LiteratureNumber.v1`, `TechnicalSource.v1`, `BibVerification.v1`. Planned: `BibEntry.v1`, `GraphBundle.v1`, `QuoteItem.v1`, `ClaimCheckResult.v1`.
- **Errors**: `404 NOT_FOUND`.
- **Response**: the schema document itself (no `provenance` wrapper).

---

## `GET /client.py`
- **Status**: implemented (2026-10-02) · **Scope**: none · **Mode**: S
- **Purpose**: the single-file Python client (`clients/python/ghai_client/__init__.py`; depends only on `httpx`) for repositories without a package manager.
  - Save it next to your scripts: `curl -s http://127.0.0.1:8090/v1/client.py -o ghai_client.py`.
  - Then `from ghai_client import GHAI; api = GHAI.from_env()`. It reads `GHAI_API_URL` (…/v1) and `GHAI_API_KEY`.
  - Or install it: `uv pip install "git+ssh://…/geohydroai-knowledge-graph.git#subdirectory=clients/python"`.
- **What it does**:
  - Methods are grouped as the endpoints are: `papers`, `quotes`, `theses`, `doi`, `graph`, `metrics`, `ontology`, plus `health()`, `stats()`, `manifest()` and `agent_rules()`.
  - Cursor-paginated endpoints are iterators.
  - Batches above the API limits are split and merged: 50 quotations (`verify_open_citations` packs whole items), 50 bibliography entries, 500 resolutions.
  - problem+json answers raise `GHAIError` with `.code`, `.detail` and `.errors`.
  - 503 and 504 are retried, honouring `Retry-After`.

---

## `GET /llms.txt`
- **Status**: implemented (2026-10-02; served at the root, `GET /llms.txt`) · **Scope**: none · **Mode**: S
- **Purpose**: the compact machine-readable index of this API for language models ([../llms.txt](../llms.txt)). An agent that has only this file can still find the rules and every endpoint.

---

## `GET /agent-rules`
- **Status**: implemented (2026-10-02) · **Scope**: none · **Mode**: S
- **Purpose**: the mandatory rules for AI agents ([AGENT_RULES.md](../AGENT_RULES.md)), served by the API so that an agent can fetch them at the start of every session.
- **Query**: `format=json` (default) or `format=markdown`.
- **Response 200 (json)**: `{"version": "1.0.0", "top_rules": "…markdown of the five most important rules…", "rules": [{"id": "R-SCI-3", "category": "SCI", "level": "MUST NOT", "text": "…", "why": "…"?}], "rules_by_group": {"all": ["R-ACC-1", …], "search": [...]}, "markdown_url": "/v1/agent-rules?format=markdown"}`.
- **Agent notes**: fetch this once per session, before the first data call. Every other response carries `Link: </v1/agent-rules>; rel="agent-rules"` as a reminder.

---

## `GET /docs/index`
- **Status**: implemented (2026-10-02) · **Scope**: none · **Mode**: S
- **Purpose**: machine-readable index of the documentation: every endpoint with its status, scope, mode, summary and rule ids, plus the list of documentation pages.
- **Response 200**: `{"endpoints": [{"method", "path", "variant"?, "group", "summary", "status", "status_text", "scope", "mode", "rules": [...]}], "pages": ["README", "AGENT_RULES", "SCHEMAS", "MCP_TOOLS", "endpoints/search", …], "counts": {"implemented": n, "planned": n}}`.

---

## `GET /docs/pages/{name}`
- **Status**: implemented (2026-10-02) · **Scope**: none · **Mode**: S
- **Purpose**: one documentation page as Markdown: `README`, `AGENT_RULES`, `SCHEMAS`, `MCP_TOOLS` or `endpoints/<group>`.
- **Response 200**: `text/markdown`. **Errors**: `404 NOT_FOUND`.

---

## `GET /docs/endpoint`
- **Status**: implemented (2026-10-02) · **Scope**: none · **Mode**: S
- **Purpose**: the full documentation of one endpoint **with the complete text of every agent rule that applies to it**. Use it right before you call an endpoint for the first time.
- **Query**: `method` (e.g. `POST`), `path` (as documented, e.g. `/search/chunks`).
- **Response 200**: `{"endpoint": {method, path, group, summary, status, scope, mode}, "markdown": "…the endpoint section…", "variants": [...], "rules": [{"id", "level", "text", "why"?}]}`.
- **Errors**: `404 NOT_FOUND` for an undocumented endpoint.

