# GeoHydroAI Knowledge API — Reference

> **Українською коротко.**
> - Це довідник API для людей і для ІІ-агентів: кожен ендпоінт, запит, відповідь, помилки, приклади.
> - Агенти (Claude Code через MCP, скрипти репозиторіїв статей) **зобов'язані** спершу прочитати [AGENT_RULES.md](AGENT_RULES.md).
> - Проєктні рішення й обґрунтування — у [`API_PLAN_v1/`](../../API_PLAN_v1/INDEX.md); тут лише контракт.
> - Статус кожного ендпоінта (реалізовано чи заплановано, фаза) вказано в його описі.

**Version**: v1 (contract) · **Status**: design-complete. Implementation follows `API_PLAN_v1/06_ROADMAP.md`. Every endpoint below carries a **Status** line; do not call an endpoint marked *planned* until `GET /v1/capabilities` lists it.

---

## 1. Documents

| File | Contents |
|---|---|
| [AGENT_RULES.md](AGENT_RULES.md) | **Mandatory rules for AI agents and automated clients.** Read this first |
| [SCHEMAS.md](SCHEMAS.md) | Shared objects: `Provenance`, `Problem`, `Job`, `PaperIdentity`, `Coverage`, `EvidenceSpan` … |
| [endpoints/system.md](endpoints/system.md) | `/health`, `/stats`, `/manifest`, `/capabilities`, `/schemas`, `/client.py`, `/llms.txt` |
| [endpoints/papers.md](endpoints/papers.md) | corpus membership, identity, metadata, full text, references, entities, tables |
| [endpoints/search.md](endpoints/search.md) | semantic, paper-level, similar and hybrid search |
| [endpoints/graph.md](endpoints/graph.md) | Neo4j paper neighbourhood, citations, entities, named queries |
| [endpoints/metrics.md](endpoints/metrics.md) | metric facts, metric extraction, metric ontology, term normalisation |
| [endpoints/bibliography.md](endpoints/bibliography.md) | DOI metadata, DOI verification, BibTeX formatting, bibliography audit and rendering, manuscript citations |
| [endpoints/evidence.md](endpoints/evidence.md) | quote verification, claim checks, theses, atomic claims, novelty |
| [endpoints/generate.md](endpoints/generate.md) | evidence-grounded synthesis, related work, rewrite check |
| [endpoints/acquisition.md](endpoints/acquisition.md) | discovery of new papers, screening, open-access download, ingestion, the composite pipeline |
| [endpoints/jobs.md](endpoints/jobs.md) | asynchronous jobs: status, events, cancel, retry |
| [endpoints/bundles.md](endpoints/bundles.md) | consumer bundles (graph.json, open_citations.json) and contract validation |
| [endpoints/admin.md](endpoints/admin.md) | rebuilds, integrity check, manifest freeze, raw Cypher |
| [MCP_TOOLS.md](MCP_TOOLS.md) | the same capabilities as MCP tools for Claude Code sessions |
| [llms.txt](llms.txt) | compact machine-readable index (served at `GET /llms.txt`) |

---

## 2. Base URL and transport

| | |
|---|---|
| Base URL (local) | `http://127.0.0.1:8090/v1` — reachable from both WSL distros (shared loopback, verified 2026-10-02) |
| Base URL (server, after migration) | `https://<host>/v1` behind VPN ([09_MIGRATION.md §6](../../API_PLAN_v1/09_MIGRATION.md)) |
| Encoding | UTF-8 JSON; uploads `multipart/form-data`; job progress as Server-Sent Events |
| OpenAPI | `GET /v1/openapi.json` (generated from the implementation; this reference is the normative design) |
| MCP | `http://127.0.0.1:8090/mcp` (streamable HTTP) — see [MCP_TOOLS.md](MCP_TOOLS.md) |

---

## 3. Authentication and scopes

Every request except `GET /health`, `GET /capabilities`, `GET /schemas/*`, `GET /client.py` and `GET /llms.txt` needs:

```http
X-API-Key: <key issued to your consumer>
```

| Scope | Grants |
|---|---|
| `read` | all GET endpoints, search, quote verification, DOI lookup, bibliography formatting/rendering |
| `llm` | endpoints that call a language model (claim checks, thesis extraction, synthesis, LLM screening). Each call consumes shared quota |
| `write` | anything that changes the stores: acquisition, ingestion, bundle import, candidate review |
| `admin` | rebuilds, integrity check, manifest freeze, raw read-only Cypher |

Keys are issued per consumer (`floodstate-eo`, `swot-dnipro`, `kakhovka-terrain`, `article1`, `claude-mcp`, `dashboard`). A key never carries more scopes than its consumer needs. `claude-mcp` has `read` + `llm` by default.

---

## 4. Request conventions

| Topic | Rule |
|---|---|
| Identifiers | `paper_id` is the corpus id (v1: the file stem, e.g. `10.1029_2025gl120832`). DOIs are accepted in any form (`https://doi.org/…`, `doi:…`, upper case) and normalised to lower case without prefix |
| Namespaces | Data that belongs to one article carries `project_id`, `<repository>:<paper>` or a bare name in lower case (pattern `^[a-z0-9][a-z0-9-]*(:[a-z0-9][a-z0-9-]*)?$`). Registered: `floodstate-eo:paper3`, `kakhovka-terrain:paper2`, `swot-dnipro:paper1`, `kakhovka-report:v1` (archived), `article1` (in floodstate-eo, `articles/flood_mapping_methods_review/`). An unregistered id is `UNKNOWN_PROJECT` |
| Pagination | `limit` (default 20, max 200) and opaque `cursor`; responses return `next_cursor` (null at the end) |
| Idempotency | POST endpoints that create jobs accept `Idempotency-Key: <uuid>`; repeating it returns the original job (`200`, `"duplicate_of"`). Natural keys (normalised DOI, PDF sha256) also deduplicate |
| Time | ISO 8601 with timezone, UTC |
| Limits | 20 requests/s per key for `read`; LLM endpoints are additionally bounded by the shared quota (§7) |
| Sync timeout | synchronous endpoints answer within 30 s; anything longer becomes a job |

### Response headers

| Header | Meaning |
|---|---|
| `X-Request-Id` | id of this request (also in logs and in `Problem.instance`) |
| `X-API-Version` | server version, e.g. `1.0.0` |
| `X-Corpus-Manifest` | `corpus_manifest_id` the answer was computed against |
| `Retry-After` | seconds to wait after `429` / `503` |
| `Location` | `/v1/jobs/{job_id}` on `202 Accepted` |

---

## 5. Errors

All errors are `application/problem+json` ([SCHEMAS.md#problem](SCHEMAS.md#problem)):

```json
{"type": "https://ghai.local/problems/not-in-corpus", "title": "Paper not in corpus",
 "status": 404, "code": "NOT_IN_CORPUS", "detail": "No paper with doi 10.1234/xyz",
 "instance": "req_01J9…", "errors": []}
```

| HTTP | `code` | When | What the client should do |
|---|---|---|---|
| 400 | `BAD_REQUEST` | malformed JSON, unknown query parameter | fix the request |
| 401 | `UNAUTHENTICATED` | missing or invalid `X-API-Key` | stop; ask the operator for a key |
| 403 | `FORBIDDEN_SCOPE` | key lacks the scope | stop; do not retry with another key |
| 403 | `INVALID_LINK` | a `/files/{token}` link this server did not sign, or whose file is gone | ask `GET /locate` for a new link |
| 404 | `NOT_FOUND` | unknown job, schema, named query | check the id |
| 404 | `NOT_IN_CORPUS` | paper/DOI not in the corpus | consider `POST /discovery/search` or `POST /acquire` |
| 409 | `GATE_NOT_PASSED` | a gap/novelty statement was requested but the acceptance gate is not passed | report `RETRIEVAL_UNVALIDATED`; never claim a gap |
| 409 | `CONFLICT` | state conflict (e.g. candidate already ingested) | re-read the resource |
| 410 | `LINK_EXPIRED` | a `/files/{token}` link older than 12 hours | ask `GET /locate` for a new link |
| 413 | `PAYLOAD_TOO_LARGE` | upload > 100 MB, batch above its limit | split the request |
| 415 | `UNSUPPORTED_MEDIA_TYPE` | upload is not `application/pdf` | — |
| 422 | `VALIDATION_FAILED` | body violates the contract; `errors[]` lists `{loc, msg, type}` | fix the data; nothing is coerced silently |
| 422 | `INVALID_DOI` | value is not shaped like a DOI | — |
| 422 | `UNKNOWN_PROJECT` | `project_id` matches the pattern but is not in the registry (`project.project`) | register the paper once with the workbench `init` step; never invent a namespace |
| 424 | `SOURCE_UNAVAILABLE` | the full text needed for the answer is not in the corpus | acquire the source first, or report it as unverified |
| 429 | `RATE_LIMITED` | request rate above the per-key limit | wait `Retry-After` |
| 429 | `QUOTA_EXHAUSTED` | shared LLM quota used up for the window | wait `Retry-After`, or submit as a job |
| 503 | `STORE_UNAVAILABLE` | Postgres, Neo4j or Chroma down | wait `Retry-After`; check `GET /health` |
| 503 | `LLM_UNAVAILABLE` | no model provider reachable | retry later |
| 504 | `UPSTREAM_TIMEOUT` | Crossref/OpenAlex did not answer | retry once; results are not cached as "not found" |

---

## 6. Provenance and validity — the parts that make answers citable

Every successful response contains a `provenance` object ([SCHEMAS.md#provenance](SCHEMAS.md#provenance)):

- `api_version`, `git_commit` of the service;
- `corpus_manifest_id`, the identity of the corpus snapshot;
- `collection` and `embedding_model` for vector answers;
- `kb_version`;
- `llm` (provider, model, `prompt_sha256`, cached) for model answers;
- `generated_at`.

Search and evidence responses also carry:

- `coverage` — how many papers/chunks were searched after filters (the denominator of any "found / not found" statement);
- `retrieval_validity` ∈ `VALIDATED` | `UNVALIDATED` | `NOT_MEASURED`:
  - `VALIDATED` only when positive-control recall ≥ 90 % has been measured for this collection and manifest;
  - otherwise "0 results" means *the search found nothing*, **not** *the literature has nothing*.

The status `CANDIDATE_GAP` is never returned unless the acceptance gate of the project has passed ([AGENT_RULES.md R-SCI-3](AGENT_RULES.md)).

---

## 7. Jobs and quota

- **Jobs.** An endpoint marked **J** answers `202 Accepted` with a `Job` ([SCHEMAS.md#job](SCHEMAS.md#job)) and `Location: /v1/jobs/{job_id}`.
  - Follow progress with `GET /jobs/{id}/events` (SSE), or poll `GET /jobs/{id}` no more often than every 5 s.
  - Jobs survive restarts; steps are idempotent, and `POST /jobs/{id}/retry` resumes from the first failed step.
- **LLM quota.** Gemini is limited to ≈ 480 requests/day per lane (two lanes).
  - The quota is shared by all consumers and recorded in `ops.llm_call`.
  - LLM endpoints return `QUOTA_EXHAUSTED` with `Retry-After` rather than degrading silently.
  - Repeated identical prompts are served from cache (`provenance.llm.cached = true`) and cost nothing.

---

## 8. Data model behind the API

| Store | Role |
|---|---|
| PostgreSQL `ghai` (schemas `core`, `biblio`, `project`, `evidence`, `ops`) | **layer of truth**: identity, files, acquisitions, bibliography, theses, labels, verdicts, jobs, keys, LLM ledger |
| File store (`data/literature`, `data/sodb`) | PDFs, TEI, SODB, addressed by sha256 from `core.paper_file` |
| ChromaDB `flood_papers_768d_v2` | vector projection (SPECTER2, 768-d) |
| Neo4j | graph projection |
| Parquet (`data/parquet`, `data/analytics`) | analytics projection |

Contracts are pydantic models in `src/contracts/`, exported as JSON Schema to [`contracts/schemas/`](../../contracts/schemas/) and served at `GET /v1/schemas/{name}`.
