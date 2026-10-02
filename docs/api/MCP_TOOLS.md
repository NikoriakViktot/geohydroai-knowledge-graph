# MCP tools — the API as tools for Claude Code sessions

**Endpoint**: `http://127.0.0.1:8090/mcp` (streamable HTTP) · **Status**: planned (phase 1, WP 1.12; LLM tools from phase 4).

Each tool calls the same service function as its REST endpoint. Inputs and outputs follow [SCHEMAS.md](SCHEMAS.md). The descriptions below are the exact text the agent sees, so they restate the rules that matter at the moment of the call.

## Configuration in a consumer repository

`.mcp.json` at the repository root:

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

The consumer key `claude-mcp` has the scopes `read` and `llm`. Write tools (`submit_ingest`, `import_bundle`) need a key with `write`, and a person must explicitly decide to grant it.

Add to the consumer repository's `CLAUDE.md`:

```markdown
## Literature
- Literature facts come only from the `ghai` MCP tools. Follow docs/api/AGENT_RULES.md of geohydroai-knowledge-graph.
- Never quote a paper from memory; use `get_paper_text` or `verify_quotes`.
- Record corpus_manifest_id and span ids for every citation you check.
```

---

## Read tools (scope `read`)

| Tool | Input | Output | REST |
|---|---|---|---|
| `resolve_paper` | `{doi?, paper_id?, title?, year?}` | `PaperIdentity` or not-in-corpus | `GET /papers/resolve` |
| `resolve_papers` | `{items: [{key?, doi?, title?, year?}] ≤ 500}` | per-item status | `POST /papers/resolve-batch` |
| `get_paper_sections` | `{paper_id}` | section list | `GET /papers/{id}/sections` |
| `get_paper_text` | `{paper_id, section? \| page? \| q?, max_chars?}` | `EvidenceSpan[]` | `GET /papers/{id}/text` |
| `get_paper_references` | `{paper_id}` | parsed bibliography with corpus links | `GET /papers/{id}/references` |
| `search_literature` | `{query, k?, year_from?, year_to?, chunk_types?, exclude_cohorts?, project_id?}` | `ChunkHit[]` + coverage + validity | `POST /search/chunks` |
| `search_papers` | `{queries[], k?, filters?}` | ranked papers | `POST /search/papers` |
| `similar_papers` | `{doi \| paper_id, k?}` | ranked papers | `POST /search/similar` |
| `verify_quotes` | `{items: QuoteItem[] ≤ 50}` | `QuoteResult[]` | `POST /quotes/verify` |
| `resolve_doi` | `{doi}` | `DoiMetadata` | `GET /doi/{doi}` |
| `verify_bib_entries` | `{entries ≤ 50, project_id?}` | `DoiVerifyResult[]` | `POST /doi/verify` |
| `format_bib` | `{dois[] ≤ 100, project_id?}` | BibTeX entries | `POST /bib/format` |
| `manuscript_citations` | `{manuscript, bibtex}` | `CitationOccurrence[]` | `POST /manuscripts/citations` |
| `metric_facts` | `{metric, min?, max?, method?, sensor?, paper_id?}` | `MetricFact[]` + summary | `GET /metrics/facts` |
| `extract_metrics` | `{text \| paper_id}` | `MetricFact[]` + rejected | `POST /metrics/extract` |
| `normalize_terms` | `{terms: [{text, expected_type?, context?}]}` | canonical ids | `POST /ontology/normalize` |
| `graph_paper` | `{doi \| paper_id}` | neighbourhood | `GET /graph/papers/{id}` |
| `graph_citations` | `{doi \| paper_id, direction}` | citing/cited works | `GET /graph/papers/{id}/citations` |
| `papers_with_entity` | `{label, canonical_id, year_from?}` | papers + evidence | `GET /graph/entities/{label}/{id}/papers` |
| `job_status` | `{job_id}` | `Job` | `GET /jobs/{id}` |
| `corpus_manifest` | `{}` | manifest | `GET /manifest` |

## Model tools (scope `llm`; consume shared quota)

| Tool | Input | Output | REST |
|---|---|---|---|
| `check_claim` | `{claim, sources[], quoted?, project_id?, strictness?}` | `ClaimCheckResult` | `POST /claims/check` |
| `novelty_check` | `{project_id, question_id, question, slice}` | job id → verdict with gate | `POST /theses/novelty` |
| `synthesize` | `{question, filters?, mode?}` | sentences with evidence ids | `POST /generate/synthesis` |
| `rewrite_check` | `{original, rewritten, sources?}` | issues | `POST /generate/rewrite-check` |

## Write tools (scope `write`; only with explicit human approval)

| Tool | Input | REST |
|---|---|---|
| `submit_ingest` | `{dois \| paper_ids, steps?, project_id?}` | `POST /ingest` |
| `acquire_papers` | `{dois}` | `POST /acquire` |
| `import_bundle` | `{project_id, kind, document, source}` | `POST /bundles/import` |

---

## Tool descriptions (as shown to the agent)

The server registers each tool with a description built from this text:

- **`search_literature`** — "Semantic search over the GeoHydroAI corpus (≈4.8k flood/hydrology/remote-sensing papers). Returns verbatim passages with DOI, section and page, plus `coverage` (how many papers were searched) and `retrieval_validity`.
  - A passage being similar is not evidence: verify with `verify_quotes` or `check_claim` before citing.
  - An empty result means the search found nothing, not that the literature has nothing (AGENT_RULES R-SCI-3)."
- **`verify_quotes`** — "Checks that quoted words, and optionally numbers, occur in the cited source's full text. Returns the source's own wording, the location, and whether that sentence itself cites other works (`attribution.cites_other_sources` means a possible secondary citation; trace the original).
  - Quotes shorter than 25 characters are rejected.
  - `SOURCE_UNAVAILABLE` means unverified, never 'false'."
- **`get_paper_text`** — "Returns verbatim text of a section, page or the paragraphs matching `q`. Use it instead of recalling what a paper says. Quote the returned text exactly, with DOI and page."
- **`check_claim`** — "Model-assessed check of whether the cited sources support a manuscript sentence: SUPPORTED, PARTIALLY_SUPPORTED, OVERSTATED, CONTRADICTED, NOT_FOUND_IN_SOURCE or SOURCE_UNAVAILABLE, with verbatim evidence spans and a suggested rewrite that stays within the evidence.
  - Label the verdict as model-assessed.
  - Rewrite or remove OVERSTATED/CONTRADICTED claims (R-SCI-5).
  - Consumes LLM quota."
- **`metric_facts`** — "Reported metric values (NSE, KGE, OA, F1, RMSE …) with evidence.
  - Values outside the metric's valid range are flagged `suspect` and must not be used.
  - Confirm table values against the table and match units and periods before comparing papers."
- **`resolve_doi`** / **`verify_bib_entries`** — "Registry metadata (Crossref/OpenAlex) and field-by-field verification of bibliography entries.
  - Online and print years can differ; both are reported.
  - A verified DOI does not mean the source supports your sentence."
- **`novelty_check`** — "Answers 'has this been done?'. Returns CANDIDATE_GAP only when the project's acceptance gate has passed; otherwise RETRIEVAL_UNVALIDATED.
  - Never write 'first', 'novel' or 'no study' without CANDIDATE_GAP."
