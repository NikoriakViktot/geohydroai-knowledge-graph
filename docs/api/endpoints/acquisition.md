# Acquisition — find new papers, screen, download legally, ingest

The flow and its 14 ingestion steps are described in [`API_PLAN_v1/05_PIPELINES.md`](../../../API_PLAN_v1/05_PIPELINES.md).

**Legal rule**: PDFs are fetched only from open-access sources:
- Unpaywall, Europe PMC, OpenAlex OA locations, publisher OA pages, arXiv;
- a Wayback copy of an OA page that blocks bots.

A paywalled work ends as `needs_manual`; a human may then upload a copy they are entitled to use (`POST /ingest/upload`). Shadow libraries are never used.

---

## `GET /locate`
- **Status**: implemented (2026-10-02) · **Scope** `read` · S · command line: `scripts/pdf <query> [--open] [--json]`
- **Purpose**: "where can I read this paper?" Returns the corpus files (PDF, TEI) and the legal open-access copies.
- **Query**: `q` is one of:
  - a DOI in any form;
  - a publisher URL with the DOI in its path (Wiley, Springer, T&F, IOP, AGU …);
  - a ScienceDirect PII URL (`/pii/S0924…`; the DOI comes from Crossref's alternative-id);
  - an arXiv id or URL;
  - a corpus `paper_id` or file stem.
  - The API never fetches other pages: give the DOI. The command line may read a publisher page's `citation_doi` tag; MDPI and some others refuse automated readers.
- **Response 200**: `{"query", "doi", "resolved_from", "title", "year", "venue", "in_corpus": PaperRef?, "files": [{"paper_id", "kind": "pdf"|"tei", "path", "windows_path", "exists", "status"}], "is_oa", "oa_status", "best_pdf_url", "open_access": [{"url", "kind": "pdf"|"landing", "version", "license", "host", "source"}], "doi_url", "notes", "provenance"}`.
  - `windows_path` is the `\\wsl.localhost\<distro>\…` form, to open a corpus PDF from Windows.
  - `open_url` (PDFs only) opens the PDF in a browser for 12 hours: `GET /files/{token}`. The page `/ui` uses it.
  - `open_access` lists PDFs first, then landing pages, by version: published, then accepted, then submitted.
  - Sources: OpenAlex locations, Unpaywall (contact from `OPEN_ALEX_EMAIL`, never stored) and arXiv. Answers are cached in `biblio.http_cache`.
- **Errors**: `404 NOT_FOUND` when the query resolves to nothing (`detail` says why); `503`.
- **Agent notes**:
  - `oa_status = bronze` means free to read on the publisher site without an open licence. Do not redistribute the file.
  - When nothing is open, the copy must come from the user's own access (`POST /ingest/upload`). Shadow libraries are never an option.

---

## `GET /files/{token}`
- **Status**: implemented (2026-10-02) · **Scope**: none (signed link) · S
- **Purpose**: open a corpus PDF in a browser. A browser cannot send `X-API-Key` when it follows a link, so `GET /locate` returns `files[].open_url = …/v1/files/<token>`. The token names the file and an expiry 12 hours ahead, signed with HMAC-SHA256.
- **Response 200**: the PDF itself, `Content-Disposition: inline`, so the browser's own viewer shows it.
- **Errors**: `403 INVALID_LINK` (not signed here, outside the corpus files, or the file is gone); `410 LINK_EXPIRED`.
- **Security**:
  - The server listens on 127.0.0.1 only.
  - A link opens one file until it expires.
  - The signing secret is `GHAI_FILE_SECRET`, or `~/.config/ghai/file_secret` (created once, mode 0600).
- **Agent notes**: these links are for a person reading the paper. Agents do not fetch them (R-DATA-3); read passages through `/papers/{id}/text`.

---

## `POST /discovery/search`
- **Status**: planned (phase 3, WP 3.2) · **Scope** `read` · S for ≤ 200 results, **J** above
- **Purpose**: search OpenAlex (and optionally Crossref and arXiv) for works matching queries and filters. Returns candidates marked by whether the corpus already has them.

**Request**:

| Field | Type | Notes |
|---|---|---|
| `queries` | string[1..20] | frozen and hashed (`queries_sha256` in the result) |
| `filters` | `{from_date?, to_date?, types?: [article\|review\|preprint\|dataset], languages?: ["en"], concepts?: string[]}` | |
| `sources` | `[openalex, crossref, arxiv]` | default `[openalex]` |
| `per_query` | int | default 50, ≤ 200 |
| `rank` | `{embedding: bool}` | adds SPECTER2 similarity of title+abstract to the query |

**Response 200**: `{"candidates": [Candidate], "queries_sha256": string, "counts": {"total": n, "in_corpus": n, "stub_in_graph": n, "new": n}, "provenance"}`

**Agent notes**:
- `corpus_status = stub_in_graph` means corpus papers already cite the work. That is a strong signal of relevance.
- Keep `queries_sha256` with any statement about what the search found.

---

## `POST /discovery/snowball`
- **Status**: planned (phase 3) · **Scope** `read` · **J**
- **Purpose**: references (backward) and citing works (forward) of seed DOIs, through OpenAlex `cites:` / `cited_by:` with a Crossref title fallback.
- **Request**: `{"seed_dois": string[1..50], "direction": "backward" | "forward" | "both", "hops": 1 | 2, "max_per_seed": 40, "min_citations": 0, "filters": {...}}`.
- **Result artifact**: `{"candidates": [Candidate + {"via": seed_doi, "hop": 1}]}`.

---

## `POST /discovery/screen`
- **Status**: planned (phase 3) · **Scope** `llm` (only with `mode` containing `llm`) · **J**
- **Purpose**: decide which candidates are relevant to a question, a thesis or an atomic claim.
- **Request**: `{"candidate_ids": string[] | null, "job_id": string | null, "criteria": {"query"?: string, "project_id"?: string, "thesis_id"?: string, "atomic_id"?: string}, "mode": "embedding" | "embedding+llm", "embedding_threshold": 0.55, "llm_band": [0.45, 0.65], "max_llm_calls": 300}`.
- **Behaviour**:
  - Candidates scoring above the band are accepted; those below are rejected.
  - Only the band in between goes to the LLM. Its labels are stored as `ScreeningLabel` with `labeler_kind = model`.
- **Result**: the candidates updated with `screening` and `review_status`.

---

## `GET /discovery/candidates`
- **Status**: planned (phase 3) · **Scope** `read` · S
- **Query**: `status` (`new | screened | accepted | rejected | needs_manual | acquired | ingested | failed`), `job_id`, `project_id`, `limit`, `cursor`.
- **Response 200**: `{"items": [Candidate], "next_cursor", "provenance"}`.

---

## `PATCH /discovery/candidates/{candidate_id}`
- **Status**: planned (phase 3) · **Scope** `write` · S
- **Request**: `{"review_status": "accepted" | "rejected" | "needs_manual", "note": string?}`. The reviewer is recorded from the API key, and a human review is marked `labeler_kind = human` only when the key belongs to a human-operated consumer.
- **Errors**: `409 CONFLICT` if the candidate is already acquired or ingested.

---

## `POST /acquire`
- **Status**: planned (phase 3, WP 3.4) · **Scope** `write` · **J**
- **Purpose**: download open-access PDFs for DOIs or candidates.
- **Request**: `{"dois": string[] | null, "candidate_ids": string[] | null, "allow_wayback_for_oa": true, "ingest_after": false}`.
- **Result artifact**: `{"acquisitions": [Acquisition], "needs_manual": [{"doi", "attempted_url", "publisher_host", "why"}]}`.
- **Behaviour**:
  - Each file is validated: `%PDF` signature, size > 20 KB, page count, text probe.
  - Each file is hashed (sha256) and stored as `pdf/<doi_slug>.pdf`.
  - Route, licence and version are recorded in `core.acquisition`.
  - A hash already in the corpus gives `duplicate`.

---

## `POST /ingest/upload`
- **Status**: planned (phase 2, WP 2.8) · **Scope** `write` · **J**
- **Purpose**: add a PDF you supply (e.g. paywalled, legally obtained) and run the ingestion steps.
- **Request**: `multipart/form-data`:
  - `file` — PDF, ≤ 100 MB;
  - optional `doi`, `project_id`, `steps` (comma list), `cohort`.
- **Errors**: `415` (not a PDF), `413`; a known sha256 returns the existing paper as a `duplicate` job result.

---

## `POST /ingest`
- **Status**: planned (phase 2, WP 2.8) · **Scope** `write` · **J**
- **Purpose**: run the per-paper chain for DOIs or corpus papers: GROBID → TEI check → (Nougat) → extraction → chunks + vectors → normalisation → OpenAlex → graph subgraph → numeric facts → registry → identity.

**Request**:

| Field | Type | Notes |
|---|---|---|
| `dois` / `paper_ids` | string[] | DOIs are acquired first when no PDF is present |
| `steps` | string[] | default all: `grobid, tei_validate, nougat, extract, vectors, normalize, enrich, graph, facts, registry, identity` |
| `options` | `{nougat: false, judge: true, force: false}` | `force` re-runs steps whose inputs did not change |
| `project_id` / `cohort` | string? | e.g. tag papers harvested for a project |

**Job steps** report per paper: `{"paper_id", "step", "status", "outputs": {"tei": path, "chunks": n, "neo4j": {"merged_nodes": n}}}`.

**Agent notes**:
- Ingestion is idempotent by sha256 and DOI.
- A paper that already exists as a citation stub is **promoted**, not duplicated.
- Expect ~1–2 min per paper; GROBID and the GPU are serialised across all jobs.

---

## `POST /pipelines/discover-and-ingest`
- **Status**: planned (phase 3, WP 3.5) · **Scope** `write` + `llm` · **J**
- **Purpose**: one job for the whole chain: search → snowball → deduplicate → screen → review (auto-accept above a threshold) → acquire → ingest → report.
- **Request**: the JobSpec of `05_PIPELINES.md §2`: `queries`, `filters`, `seeds`, `snowball`, `screening`, `limits {max_candidates, max_accept, max_llm_calls, max_gpu_minutes}`, `acquire`, `ingest`, `cohort`, `project_id`.
- **Result artifacts**:
  - `report.md` and `report.json`. Per candidate: path (in_corpus/new), score, role, acquisition route, licence, ingestion steps, nodes and chunks created.
  - `queries_sha256`.
- **Behaviour**:
  - Stops at the first exhausted budget with status `partial`.
  - When `project_id` is given and the result is meant to support absence or novelty claims, the gate rules of [AGENT_RULES R-SCI-3](../AGENT_RULES.md) apply.
