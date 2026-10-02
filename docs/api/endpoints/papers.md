# Papers — corpus membership, identity, content

Source of truth:
- `core.paper`, `core.paper_alias`, `core.paper_file`, `core.cohort_member` (PostgreSQL);
- the TEI file store for text, sections and references.

All endpoints: **Scope** `read` · **Mode** S. Shared objects: [SCHEMAS.md](../SCHEMAS.md).

---

## `GET /papers/resolve`
- **Status**: implemented (2026-10-02; reads `core.paper*` in Postgres: 5,230 papers).
- **Purpose**: "is this work in the corpus, and under which id?" Exactly one of `doi`, `paper_id`, `title`, `file`, `openalex_id` is required.

| Query param | Type | Notes |
|---|---|---|
| `doi` | string | any form (`https://doi.org/…`, `doi:…`, upper case); normalised before lookup |
| `paper_id` | string | corpus id (file stem) |
| `title` | string | fuzzy match on the normalised title; `year` recommended |
| `year` | int | used with `title` (± 1) |
| `file` | string | any historical file stem or slug (`10.1029_2025gl120832`, `doi_slug_colon` forms) |
| `openalex_id` | string | `W…` or full URL |
| `include` | `aliases,files,cohorts` | comma list; default none |

**Response 200**: `{"match": "exact" | "alias" | "title_fuzzy", "score": 1.0, "paper": PaperIdentity, "canonical": PaperRef?, "provenance": …}`

- When the matched record is a duplicate, `paper` is the duplicate and `canonical` is the paper to use.
- `title_fuzzy` matches need `score ≥ 0.95`; below that the endpoint answers `404`.

**Errors**: `404 NOT_IN_CORPUS` (with `detail` naming the normalised DOI); `422 INVALID_DOI`; `400` if zero or several selectors are given.

```bash
curl -s -H "X-API-Key: $GHAI_API_KEY" "$GHAI_API_URL/papers/resolve?doi=10.1029/2025GL120832&include=aliases"
```
```json
{"match": "alias", "score": 1.0,
 "paper": {"paper_id": "10.1029_2025gl120832", "doi": "10.1029/2025gl120832",
           "title": "SWOT satellite observations of the Kakhovka dam break flood …", "year": 2026,
           "identity_status": "ok", "duplicate_of": null,
           "aliases": [{"alias_type": "doi", "alias": "10.1029/2025gl120832", "source": "scan:paper_json.metadata.doi"}],
           "files": [], "cohorts": []},
 "canonical": null, "provenance": {"identity_run_id": "492120b5-…", "…": "…"}}
```

**Agent notes**:
- Resolve before you cite. A `404` is a fact about this corpus, not about the literature; offer `POST /discovery/search` or `POST /acquire`.
- `title_doi_mismatch` means the PDF's header DOI points at a different work. The record's `doi` is then `null` on purpose; do not "repair" it from the header.

---

## `POST /papers/resolve-batch`
- **Status**: implemented (2026-10-02) · `read` · S
- **Purpose**: resolve up to 500 identifiers at once (bibliography keys, DOI lists, ledger rows).

**Request**: `{"items": [{"key": "Lehnigk_2026", "doi": "10.1029/2025gl120832"}, {"key": "Giustarini_2013", "title": "A change detection approach…", "year": 2013}]}`. Each item has the same selectors as `GET /papers/resolve`; `key` is echoed back.

**Response 200**: `{"results": [{"key": "Lehnigk_2026", "status": "found", "paper": PaperRef, "match": "alias"}, {"key": "Giustarini_2013", "status": "not_in_corpus"}], "summary": {"found": 1, "not_in_corpus": 1, "invalid": 0}, "provenance": …}`

**Errors**: `413 PAYLOAD_TOO_LARGE` above 500 items; per-item problems are reported in `results[].status` (`found | not_in_corpus | invalid | ambiguous`), not as a request error.

---

## `GET /papers/{paper_id}`
- **Status**: implemented (2026-10-02: identity, aliases, files, cohorts; the `openalex` and `abstract` fields are planned for phase 1) · `read` · S
- **Purpose**: one paper: identity plus OpenAlex enrichment (citations, authors, institutions, topics).

**Response 200**: `PaperIdentity` + `{"openalex": {"cited_by_count", "authors": [{"name", "orcid", "institutions"}], "topics": [{"name", "score"}], "referenced_works_count"}, "abstract": string?, "provenance"}`.

**Errors**: `404 NOT_FOUND`. A duplicate id returns `200` with `identity_status = duplicate` and `duplicate_of`; follow it.

---

## `GET /papers/{paper_id}/sections`
- **Status**: planned (phase 1) · `read` · S
- **Purpose**: the TEI structure: section titles, levels, pages, character counts. Use it to target `/text`.

**Response 200**: `{"sections": [{"n": "3.2", "title": "Hydraulic modelling", "level": 2, "pages": [5, 6], "chars": 4210}], "has_abstract": true, "figures": 6, "tables": 3, "formulas": 4, "provenance"}`

**Errors**: `404 NOT_FOUND`; `424 SOURCE_UNAVAILABLE` if the paper has no TEI.

---

## `GET /papers/{paper_id}/text`
- **Status**: planned (phase 1) · `read` · S
- **Purpose**: verbatim text of a section, a page, or the passages around a query. This is how an agent reads a source; never quote from memory.

| Query param | Type | Notes |
|---|---|---|
| `section` | string | section `n` or title (case-insensitive prefix) |
| `page` | int | |
| `q` | string | return only paragraphs containing these words (± 1 paragraph) |
| `max_chars` | int | default 20,000; hard cap 100,000 |

**Response 200**: `{"spans": [EvidenceSpan], "truncated": bool, "provenance"}`. Each span carries `section`, `page`, offsets and `coords`.

**Errors**: `404`; `424 SOURCE_UNAVAILABLE`; `400` when no selector is given.

**Agent notes**:
- The API returns short evidence passages, never whole PDFs: the corpus contains non-open-access works.
- Quote `span.text` exactly and cite `paper.doi` plus `page`.

---

## `GET /papers/{paper_id}/references`
- **Status**: planned (phase 1) · `read` · S
- **Purpose**: the paper's bibliography as parsed by GROBID, each entry resolved against the corpus.

**Response 200**: `{"references": [{"n": 12, "raw": "Bates, P.D. (2022) Flood inundation prediction. Annu. Rev. Fluid Mech. 54…", "title", "year", "doi"?, "in_corpus": PaperRef?}], "counts": {"total": 88, "with_doi": 61, "in_corpus": 9}, "provenance"}`

---

## `GET /papers/{paper_id}/entities`
- **Status**: planned (phase 1) · `read` · S
- **Purpose**: methods, sensors, DEMs, metrics, study area and task, normalised to the ontology (`canonical_id`).

**Response 200**: `{"methods": [{"canonical_id": "method.hand", "surface_form": "HAND", "confidence": 0.9, "evidence": "…", "role": "used"|"cited"|null}], "sensors": [...], "metrics": [MetricFact], "study_area": {"countries": [...], "rivers": [...]}, "task": "flood_mapping_satellite", "study_type": "case_study", "provenance"}`

**Agent notes**:
- Entity labels come from a gazetteer plus rules and have **not** been validated against a human gold set: the `accepted` flag carries no information.
- Treat them as search hints, not as facts about the paper. Confirm with `/text`.

---

## `GET /papers/{paper_id}/tables`
- **Status**: planned (phase 1) · `read` · S
- **Purpose**: TEI tables (caption plus rows) and the numeric facts extracted from them.

**Response 200**: `{"tables": [{"table_id": "tab_2", "label": "Table 2", "caption": "…", "page": 7, "rows": [["Station", "NSE", "KGE"], ["Kherson", "0.82", "0.77"]], "facts": [MetricFact]}], "provenance"}`

**Agent notes**:
- GROBID table parsing can misalign columns. Check a value against the row and column header before you rely on it.
- `range_verdict = suspect` marks values outside the metric's valid range.
