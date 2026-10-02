# MCP tools — the API as tools for Claude Code sessions

**Endpoint**: `http://127.0.0.1:8090/mcp` (streamable HTTP, JSON responses, stateless) · **Status**: implemented for the read tools (2026-10-02); the model and write tools follow their REST endpoints (phase 4).

Every tool calls its REST endpoint in-process with your key (`src/api/mcp.py`). Scopes, `provenance`, problem+json errors and the contracts in [SCHEMAS.md](SCHEMAS.md) are therefore exactly those of REST. A REST problem comes back as a tool error whose text is the problem JSON (`status`, `code`, `detail`, `errors`); act on `code` as [AGENT_RULES.md](AGENT_RULES.md) §8 says. Only implemented endpoints are tools; `api_capabilities` tells you what exists.

The server also carries:
- **instructions**: the five rules of AGENT_RULES §0, sent at `initialize`;
- **resources**: every documentation page as `ghai://docs/<page>` (e.g. `ghai://docs/AGENT_RULES`, `ghai://docs/endpoints/search`), Markdown;
- **prompts**: the standard workflows of AGENT_RULES §7 as `w1` … `w6` (W6: build a paper, [PAPER_WORKFLOW.md](PAPER_WORKFLOW.md)), each with an optional `project_id`, and `research` (arguments `question`, `project_id`): the agent prompt of [QUERY_GUIDE.md](QUERY_GUIDE.md) §0 applied to a question.

## Configuration in a consumer repository

`.mcp.json` at the repository root (the key is never written into it; the repositories are public):

```json
{
  "mcpServers": {
    "ghai": {
      "type": "http",
      "url": "http://127.0.0.1:8090/mcp",
      "headers": { "X-API-Key": "${GHAI_API_KEY}" }
    }
  }
}
```

The key comes from the environment of the `claude` process. Keep it in `~/.config/ghai/env` (mode 0600, one line `GHAI_API_KEY=…`) and export it in the shell that starts Claude Code. The consumer key `claude-mcp` has the scopes `read` and `llm`. Write tools (`submit_ingest`, `import_bundle`) will need a key with `write`, which a person must explicitly decide to grant.

The server is local: requests must come to `127.0.0.1` or `localhost` (DNS-rebinding protection answers 421 otherwise), both WSL distributions reach it on the shared loopback, and a request without a known key is `401 UNAUTHENTICATED` before any MCP message is read. `Authorization: Bearer <key>` is accepted as well.

The workbench step `init` writes `.mcp.json` and a section of the repository's `CLAUDE.md` (between `<!-- ghai:begin -->` and `<!-- ghai:end -->`): literature only from the `ghai` tools, never quoted from memory, provenance recorded, and how built outputs are delivered ([PAPER_WORKFLOW.md](PAPER_WORKFLOW.md)).

---

## Read tools (scope `read`) — implemented

| Tool | Input | Output | REST |
|---|---|---|---|
| `corpus_manifest` | `{}` | manifest (`corpus_manifest_id`, counts, collection, model) | `GET /manifest` |
| `api_capabilities` | `{}` | implemented and planned endpoints | `GET /capabilities` |
| `resolve_paper` | `{doi?, paper_id?, title?, year?}` | `PaperIdentity` or `NOT_IN_CORPUS` | `GET /papers/resolve` |
| `resolve_papers` | `{items: [{key?, doi?, title?, year?}] ≤ 500}` | per-item status | `POST /papers/resolve-batch` |
| `get_paper_sections` | `{paper_id}` | section list | `GET /papers/{paper_id}/sections` |
| `get_paper_text` | `{paper_id, section? \| page? \| q?, max_chars?}` | `EvidenceSpan[]` | `GET /papers/{paper_id}/text` |
| `get_paper_references` | `{paper_id}` | parsed bibliography with corpus links | `GET /papers/{paper_id}/references` |
| `get_paper_tables` | `{paper_id}` | tables with captions and cells | `GET /papers/{paper_id}/tables` |
| `get_paper_entities` | `{paper_id}` | methods, sensors, metrics, places, task with evidence and grounding | `GET /papers/{paper_id}/entities` |
| `locate_paper` | `{query}` (DOI, URL, PII, arXiv id, paper_id) | corpus files and legal open-access copies | `GET /locate` |
| `search_literature` | `{query, k?, filters?, min_score?, project_id?}` | `ChunkHit[]` + coverage + validity | `POST /search/chunks` |
| `search_papers` | `{queries[] ≤ 20, k?, filters?, aggregate?, project_id?}` | ranked papers | `POST /search/papers` |
| `similar_papers` | `{paper_id \| doi, k?, filters?}` | ranked papers | `POST /search/similar` |
| `verify_quotes` | `{items: QuoteItem[] ≤ 50, project_id?}` | `QuoteResult[]` | `POST /quotes/verify` |
| `validate_theses` | `{kind, project_id, document, authored_by?}` | counts and warnings, or errors | `POST /theses/validate` |
| `resolve_doi` | `{doi}` | `DoiMetadata` | `GET /doi/{doi}` |
| `verify_bib_entries` | `{entries ≤ 50, project_id?}` | `DoiVerifyResult[]` | `POST /doi/verify` |
| `format_bib` | `{dois[] ≤ 100, project_id?}` | BibTeX entries | `POST /bib/format` |
| `render_bibliography` | `{bibtex, keys?, manuscript?, style?}` | formatted reference list | `POST /bib/render` |
| `audit_bib` | `{bibtex, project_id?, search_missing?}` | per-entry ok / fix / unresolved | `POST /bib/audit` |
| `manuscript_citations` | `{manuscript, bibtex, project_id?}` | `CitationOccurrence[]` | `POST /manuscripts/citations` |
| `metric_facts` | `{metric?, min?, max?, method?, sensor?, paper_id?, doi?, source?, range_verdict?, limit?, cursor?}` | `MetricFact[]` + summary | `GET /metrics/facts` |
| `extract_metrics` | `{text \| paper_id, metrics?}` | `MetricFact[]` + rejected | `POST /metrics/extract` |
| `metric_ontology` | `{}` | metric vocabulary and valid ranges | `GET /metrics/ontology` |
| `normalize_terms` | `{terms: [{text, expected_type?, context?}], allow_semantic?}` | canonical ids | `POST /ontology/normalize` |
| `ontology_entities` | `{type?, q?, limit?, cursor?}` | ontology entities | `GET /ontology/entities` |
| `graph_paper` | `{paper: doi \| paper_id, include?}` | neighbourhood | `GET /graph/papers/{doi_or_paper_id}` |
| `graph_citations` | `{paper, direction, in_corpus_only?, limit?, cursor?}` | citing or cited works | `GET /graph/papers/{doi_or_paper_id}/citations` |
| `papers_with_entity` | `{label, canonical_id, year_from?, year_to?, min_confidence?, role?, grounded?, limit?, cursor?}` | papers + evidence | `GET /graph/entities/{label}/{canonical_id}/papers` |
| `graph_queries` | `{}` | catalogue of named read-only queries | `GET /graph/queries` |
| `run_graph_query` | `{name, params?, limit?}` | rows | `POST /graph/queries/{name}` |

All of them are marked read-only. `locate_paper`, `resolve_doi`, `verify_bib_entries`, `format_bib` and `audit_bib` may ask the registries (Crossref, DataCite, OpenAlex, Unpaywall) through the API's cache.

## Model tools (scope `llm`; consume shared quota) — planned

| Tool | Input | Output | REST |
|---|---|---|---|
| `check_claim` | `{claim, sources[], quoted?, project_id?, strictness?}` | `ClaimCheckResult` | `POST /claims/check` |
| `novelty_check` | `{project_id, question_id, question, slice}` | job id → verdict with gate | `POST /theses/novelty` |
| `synthesize` | `{question, filters?, mode?}` | sentences with evidence ids | `POST /generate/synthesis` |
| `rewrite_check` | `{original, rewritten, sources?}` | issues | `POST /generate/rewrite-check` |
| `job_status` | `{job_id}` | `Job` | `GET /jobs/{job_id}` |

## Write tools (scope `write`; only with explicit human approval) — planned

| Tool | Input | REST |
|---|---|---|
| `submit_ingest` | `{dois \| paper_ids, steps?, project_id?}` | `POST /ingest` |
| `acquire_papers` | `{dois}` | `POST /acquire` |
| `import_bundle` | `{project_id, kind, document, source}` | `POST /bundles/import` |

---

## What the descriptions tell the agent

Each tool's description restates the rule that matters at the moment of the call:

- **`search_literature`**: a similar passage is not evidence; verify with `verify_quotes` before citing. An empty result means this search found nothing, not that the literature has nothing (R-SCI-3). Use several phrasings.
- **`verify_quotes`**: returns the source's own wording, its location, and `attribution.cites_other_sources` (a possible secondary citation: trace the original, R-SCI-4). Quotes shorter than 25 characters are rejected. `SOURCE_UNAVAILABLE` means unverified, never "false".
- **`get_paper_text`**: use it instead of recalling what a paper says; quote the returned text exactly, with DOI and page (R-SCI-1).
- **`metric_facts`**: values outside the metric's valid range are `suspect` and must not be used; confirm table values against the table and match units and periods (R-SCI-2, W5).
- **`resolve_doi`**, **`verify_bib_entries`**: metadata come from the registries, never from memory; online and print years can differ; a verified DOI does not mean the source supports your sentence.
- **`locate_paper`**: `open_url` links are for people; never fetch them (R-DATA-3).
- **`validate_theses`**: nothing is coerced; fix the source document (R-DATA-1). `UNKNOWN_PROJECT` means the paper is not registered.
- **`papers_with_entity`**: counts need a stated denominator and exclude duplicates (R-SCI-7).
- When implemented, **`check_claim`** will return a model-assessed verdict (label it so; rewrite or remove OVERSTATED and CONTRADICTED claims, R-SCI-5), and **`novelty_check`** will return `CANDIDATE_GAP` only when the project's acceptance gate has passed (never write "first", "novel" or "no study" without it).
