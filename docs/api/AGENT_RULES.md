# Rules for AI agents and automated clients

> **Українською.**
> - Ці правила обов'язкові для будь-якого агента (Claude Code через MCP, скрипти й LLM-конвеєри в репозиторіях статей), який працює з GeoHydroAI Knowledge API.
> - Вони захищають наукову чесність статей: не вигадувати, не посилювати, не видавати «не знайдено» за «не існує», зберігати провенанс.
> - Порушення правила **MUST** — це дефект результату, навіть якщо текст «виглядає правильно».

**Applies to**: Claude Code sessions in `floodstate-eo`, `SWOT-DNIPRO`, `kakhovka-terrain`, `article1` (via MCP or HTTP), and any script or LLM pipeline that calls this API.

**Keywords**: **MUST** / **MUST NOT** are hard rules. **SHOULD** may be broken only with a recorded reason.

**Read before anything else**: [README.md](README.md) §5 (errors) and §6 (provenance and validity).

---

## 0. The five rules that matter most

1. **Quote, don't recall.** What a paper says comes from an `EvidenceSpan` returned by the API, never from your memory of the paper (R-SCI-1).
2. **Numbers come with their sentence.** Every number attributed to a source has a span containing that number (R-SCI-2).
3. **"Not found" is not "does not exist".** Absence and novelty statements need the gate (R-SCI-3).
4. **Model verdicts are labelled as model verdicts.** There are no human labels in this corpus yet (R-SCI-6).
5. **Everything you use goes into your provenance record** (R-SCI-8).

---

## 1. Access (R-ACC)

| Id | Rule | Why |
|---|---|---|
| R-ACC-1 | You **MUST** read corpus data only through this API or its MCP tools. You **MUST NOT** read the knowledge repository's files directly (no `wsl.exe … cat`, no copying `data/` or `paper_*` folders) | the files are inputs to the layer of truth, not the truth; direct reads bypass identity, duplicates and provenance |
| R-ACC-2 | Before using an endpoint marked *planned* in this reference, you **MUST** confirm in `GET /capabilities` that it is live | the reference is ahead of the implementation |
| R-ACC-3 | You **MUST** use your own consumer key from `GHAI_API_KEY`. You **MUST NOT** use another consumer's key or ask for the `admin` scope | scopes are the safety boundary |
| R-ACC-4 | On `429` / `503` you **MUST** wait `Retry-After`. Retry `5xx` at most 3 times with exponential backoff. You **MUST NOT** retry other `4xx` unchanged | shared quota and shared GPU |
| R-ACC-5 | Work that takes longer than a few seconds **MUST** go through jobs. Poll at most every 5 s, or use the event stream. Send an `Idempotency-Key` so a retry does not start a second job | one worker, serialised GPU and GROBID |
| R-ACC-6 | You **SHOULD** batch: `resolve-batch`, `quotes/verify` with up to 50 items, `doi/verify` with up to 50 entries | 50 single calls cost 50× the overhead |

## 2. Scientific integrity (R-SCI)

| Id | Rule |
|---|---|
| **R-SCI-1** | A statement of what a source says **MUST** rest on a verbatim `EvidenceSpan` from `/papers/{id}/text`, `/search/*`, `/quotes/verify` or `/claims/check`. Quote `span.text` exactly; cite DOI + page. You **MUST NOT** reconstruct a quotation from memory, even of a famous paper. |
| **R-SCI-2** | A number attributed to a source **MUST** appear in a returned span or `MetricFact` (`/quotes/verify` with `expected_numbers` checks this). Units and period (calibration / validation, date, area) **MUST** match the sentence you write. `range_verdict: suspect` values **MUST NOT** be used without a human check. Topic words never imply a metric: a "flood mapping" paper without "overall accuracy" in its text does not report OA. |
| **R-SCI-3** | **Absence and novelty.** An empty result means "this search, over `coverage.papers_in_slice` papers of manifest `corpus_manifest_id`, found nothing". You **MUST** report it in those terms, with the queries (`queries_sha256`). You **MUST NOT** write "no study has…", "for the first time…", "novel" or "unprecedented" unless `POST /theses/novelty` returned `CANDIDATE_GAP` (only possible when the project's acceptance gate has passed). Otherwise the status is `RETRIEVAL_UNVALIDATED`. |
| **R-SCI-4** | If a matched span has `attribution.cites_other_sources = true`, the source is reporting someone else's result. You **MUST** trace and cite the original. Example: Iqbal 2023 reports Hawker 2022's "1.12–1.61 m", and it is actually a before/after pair (1.61 → 1.12 m). |
| **R-SCI-5** | A claim check of `OVERSTATED` or `CONTRADICTED` **MUST** lead to a rewrite within the evidence (start from `suggested_rewrite`) or to removing the claim. You **MUST NOT** keep the claim and drop the citation, or keep the citation and add hedging that the source does not support. |
| **R-SCI-6** | Every label and verdict carries `labeler_kind`. As of 2026-10-02 **there are no human relevance labels in this corpus**: the 57 "reviewed" screening rows were labelled by ChatGPT, and the 38 `human_verified=yes` numbers by Claude. You **MUST** call model verdicts "model-assessed" and **MUST NOT** present them as expert or human judgement. |
| **R-SCI-7** | Counts of papers (prevalence, "N studies used X") **MUST** exclude duplicates and the harvested cohort `paper_3` (`exclude_cohorts`), state the slice denominator, and say that entity extraction has unmeasured precision. |
| **R-SCI-8** | For every API result used in a manuscript you **MUST** keep, in your repository's run manifest: `corpus_manifest_id`, `collection`, `embedding_model`, `queries_sha256` (searches), `job_id` (jobs), `llm.model` and `prompt_sha256` (model answers), and the `span_id`s you cited. |
| **R-SCI-9** | Your article's own results live in your repository. You **MUST NOT** import them as literature facts, and **MUST NOT** cite your own manuscript (or its companion paper) as independent literature support for the same claims. |
| **R-SCI-10** | Bibliographic metadata **MUST** come from `/doi/{doi}` or `/doi/verify`, not from memory. A `VERIFIED` DOI says the reference is right; it does **not** say the content supports your sentence (that is R-SCI-1/5). |

## 3. Writing data (R-DATA)

| Id | Rule |
|---|---|
| R-DATA-1 | Theses, atomic claims, bibliographies and graph bundles **MUST** be sent in the contract formats ([SCHEMAS.md](SCHEMAS.md), `GET /schemas/{name}`). Validate first (`POST /theses/validate`). On `422`, fix the source file; you **MUST NOT** reshape data to get past the validator. |
| R-DATA-2 | Every write **MUST** carry your `project_id` (`floodstate-eo:paper3`, `kakhovka-terrain:paper2`, `swot-dnipro:paper1`, `kakhovka-report:v1`, `article1`). You **MUST NOT** write into another project's namespace. |
| R-DATA-3 | You **MUST** acquire PDFs only through `POST /acquire` (open access) or upload files you are entitled to use (`POST /ingest/upload`). You **MUST NOT** fetch papers from shadow libraries or otherwise circumvent access controls. You **MUST NOT** ask for or reconstruct whole full texts, and **MUST NOT** fetch the file links (`files[].open_url`) that `GET /locate` returns: they are for a person to read the PDF in a browser. |
| R-DATA-4 | You **MUST NOT** create article-specific folders, dumps or scripts in the knowledge repository. Results come from the API and are stored in your own repository. |
| R-DATA-5 | Evidence is append-only. A correction is a new verdict (new run), never an edit or deletion of an old one. |

## 4. Language models and quota (R-LLM)

| Id | Rule |
|---|---|
| R-LLM-1 | You **SHOULD** use deterministic endpoints first (`/quotes/verify`, `/metrics/extract`, `/doi/verify`, `/search/*`) and an LLM endpoint only for what they cannot decide |
| R-LLM-2 | Reuse `evidence_pack_id` and identical prompts (the cache costs nothing). On `QUOTA_EXHAUSTED`, stop and report; **MUST NOT** loop |
| R-LLM-3 | Jobs that use a model **MUST** set a budget (`max_llm_calls`) |
| R-LLM-4 | A generated sentence with `supported: false`, or a DOI in `unverified_dois`, **MUST NOT** reach a manuscript |

## 5. Security and privacy (R-SEC)

| Id | Rule |
|---|---|
| R-SEC-1 | The API key comes from the environment. You **MUST NOT** print it, log it, put it in a URL or commit it |
| R-SEC-2 | The corpus includes works that are not open access. Outputs **MUST** quote only short evidence spans (a few sentences) with attribution |
| R-SEC-3 | Records with `identity_status = not_a_paper` (one administrative document with personal names) **MUST NOT** be used or quoted |

---

## 6. Which endpoint for which task

| You need to… | Use | Then |
|---|---|---|
| know whether a work is in the corpus | `GET /papers/resolve` / `POST /papers/resolve-batch` | `404` → `POST /discovery/search` or `POST /acquire` |
| read what a paper says | `GET /papers/{id}/sections`, then `GET /papers/{id}/text` | quote `span.text` |
| find passages on a topic | `POST /search/chunks` (several phrasings) | `POST /quotes/verify` on what you will cite |
| check that a quotation or number is in the source | `POST /quotes/verify` | `attribution` → trace the original |
| check that a sentence is supported | `POST /claims/check` | rewrite per R-SCI-5 |
| list citations in a manuscript | `POST /manuscripts/citations` | feed `quoted` into `/quotes/verify` |
| fix or audit a bibliography | `POST /bib/audit`, `POST /doi/verify` | `POST /bib/format`, `POST /bib/render` |
| get metric values from the literature | `GET /metrics/facts` | `GET /papers/{id}/tables` + `/quotes/verify` with `expected_numbers` |
| map a term to the ontology | `POST /ontology/normalize` (with `context`) | `GET /graph/entities/{label}/{id}/papers` |
| find new papers | `POST /discovery/search` / `/discovery/snowball` | `/discovery/screen` → review → `/acquire` → `/ingest` |
| state that something is new or missing | `POST /theses/novelty` | obey R-SCI-3 |
| draft related work | `POST /generate/related-work` | `POST /claims/check` on every citation |

## 7. Standard workflows

**W1 — Verify the citations of a manuscript** (replaces the manual `open_citations` session of 2026-10-01):
1. `POST /manuscripts/citations` with the manuscript and its `.bib`.
2. `POST /quotes/verify` for every occurrence with `quoted` words (batch ≤ 50).
3. `POST /claims/check` for every sentence whose source is in the corpus.
4. Write one status per citation:
   - `VERIFIED` — quote found and claim supported;
   - `FIX` — `CONTRADICTED`, or the numbers are wrong;
   - `WORDING` — `OVERSTATED`;
   - `OPEN` — `SOURCE_UNAVAILABLE`, or an `UNRESOLVED` DOI.
5. Record provenance (R-SCI-8).

**W2 — Bibliography**: `POST /bib/audit` → fix with `POST /doi/verify` and `POST /bib/format` → `POST /bib/render` for the reference list.

**W3 — Evidence for theses**: `POST /theses/validate` → `POST /bundles/import` (`kind: theses`, `atomic_claims`) → `POST /theses/evidence-run` (job) → review → `GET /theses/sets/{project_id}`.

**W4 — Add the literature that is missing**: `POST /pipelines/discover-and-ingest` with budgets, or the step-by-step `discovery/search` → `screen` → `PATCH candidates` → `acquire` → `ingest`. Re-run the searches of your claims afterwards.

**W5 — Literature numbers for a comparison table**: `GET /metrics/facts` → for each value `GET /papers/{id}/tables` → `POST /quotes/verify` with `expected_numbers` → keep only `FOUND_*` with matching units and period.

## 8. Error handling for agents

| Code | Do | Do not |
|---|---|---|
| `NOT_IN_CORPUS` | say so; offer discovery or acquisition | invent what the paper says |
| `SOURCE_UNAVAILABLE` | report the citation as unverified (`OPEN`) | judge from the abstract you remember |
| `GATE_NOT_PASSED` | write `RETRIEVAL_UNVALIDATED` | rephrase the gap claim to avoid the check |
| `VALIDATION_FAILED` | fix the source document | coerce types to pass |
| `QUOTA_EXHAUSTED` | stop the LLM part, keep the deterministic results, report | retry in a loop |
| `STORE_UNAVAILABLE` | wait `Retry-After`; check `/health` | fall back to reading files directly (R-ACC-1) |

## 9. Rules by endpoint group

The API attaches these rules to every endpoint in its OpenAPI description (`x-agent-rules`) and returns their full text from `GET /v1/docs/endpoint`. The group of an endpoint is the file it is documented in (`docs/api/endpoints/<group>.md`).

| Group | Rules |
|---|---|
| all | R-ACC-1, R-ACC-3, R-ACC-4, R-SEC-1, R-SCI-8 |
| system | R-ACC-2 |
| papers | R-SCI-1, R-SCI-10, R-SEC-2, R-SEC-3 |
| search | R-SCI-1, R-SCI-3, R-SCI-7, R-ACC-6 |
| graph | R-SCI-6, R-SCI-7 |
| metrics | R-SCI-2, R-SCI-6 |
| bibliography | R-SCI-10, R-DATA-1, R-ACC-6 |
| evidence | R-SCI-1, R-SCI-2, R-SCI-3, R-SCI-4, R-SCI-5, R-SCI-6, R-LLM-1, R-LLM-2, R-DATA-1 |
| generate | R-SCI-1, R-SCI-5, R-SCI-6, R-LLM-1, R-LLM-2, R-LLM-3, R-LLM-4 |
| acquisition | R-SCI-3, R-DATA-2, R-DATA-3, R-ACC-5, R-LLM-3 |
| jobs | R-ACC-5 |
| bundles | R-SCI-9, R-DATA-1, R-DATA-2, R-DATA-4, R-DATA-5 |
| admin | R-DATA-5 |

## 10. Checklist before you hand results to a human

- [ ] Every "the source says" has a verbatim span with DOI and page (R-SCI-1).
- [ ] Every number from a source was found by `/quotes/verify` or comes from a `MetricFact` with a matching unit and period (R-SCI-2).
- [ ] No "first", "novel", "no study" without `CANDIDATE_GAP` (R-SCI-3).
- [ ] Secondary citations traced to the original (R-SCI-4).
- [ ] `OVERSTATED` / `CONTRADICTED` claims rewritten or removed (R-SCI-5).
- [ ] Model verdicts labelled "model-assessed" (R-SCI-6).
- [ ] Provenance written to your run manifest (R-SCI-8).
- [ ] No API key in output or code (R-SEC-1).
