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
- **Status**: implemented (2026-10-02; GROBID TEI of `core.paper_file`, 5,039 papers) · `read` · S
- **Purpose**: the TEI outline: section handles, numbers, titles, levels, pages, paragraph and character counts. Use it to target `/text`.

**Response 200**: `{"paper": PaperRef, "sections": [{"id": "s3.s1", "n": "3.2", "title": "Hydraulic modelling", "level": 2, "pages": [5, 6], "paragraphs": 4, "chars": 4210}], "has_abstract": true, "figures": 6, "tables": 3, "formulas": 4, "provenance"}`

- `id` is the handle used in `passage_id`s: `s<i>` for the i-th top-level section and `s<i>.s<j>` for its subsections.
- `n` is the number printed in the paper. It is `null` when GROBID found none.

**Errors**: `404 NOT_FOUND`; `424 SOURCE_UNAVAILABLE` if the paper has no TEI (`canonical` is named when the id is a duplicate); `503 STORE_UNAVAILABLE`.

---

## `GET /papers/{paper_id}/text`
- **Status**: implemented (2026-10-02) · `read` · S
- **Purpose**: verbatim text of a section, a page, or the passages around a query. This is how an agent reads a source; never quote from memory.

| Query param | Type | Notes |
|---|---|---|
| `section` | string | section number (`3.2`), handle (`s3`), title prefix (case-insensitive), or `abstract` |
| `page` | int | PDF page from GROBID coordinates; passages without coordinates are not matched |
| `q` | string | paragraphs that contain every word of ≥ 3 letters, ± 1 paragraph |
| `max_chars` | int | default 20,000; hard cap 100,000 |

At least one of `section`, `page` and `q` is required. They combine with AND.

**Response 200**: `{"paper": PaperRef, "spans": [EvidenceSpan], "truncated": bool, "matched_passages": int, "provenance"}`.
- Spans are whole passages (paragraph, abstract, figure caption or table) in document order. The last one may be cut at `max_chars`; then `char_end` < the passage length and `truncated = true`.
- `passage_id` is stable for one TEI file, so `/quotes/verify` and `/text` refer to the same passages.

**Errors**: `400` when no selector is given; `404`; `424 SOURCE_UNAVAILABLE`; `503`.

**Agent notes**:
- The API returns short evidence passages, never whole PDFs: the corpus contains non-open-access works.
- Quote `span.text` exactly and cite `paper.doi` plus `page`.
- `q` is a literal word filter, not semantic search. For meaning, use `POST /search/chunks`.

---

## `GET /papers/{paper_id}/references`
- **Status**: implemented (2026-10-02) · `read` · S
- **Purpose**: the paper's bibliography as parsed by GROBID, each entry resolved against the corpus, with the number of in-text citations that point at it.

**Response 200**: `{"paper": PaperRef, "references": [ReferenceEntry], "counts": {"total": 88, "with_doi": 61, "in_corpus": 9, "cited_in_text": 85}, "provenance"}`

`ReferenceEntry`:
- `n`: position in the list, from 1;
- `xml_id`: the target of the in-text markers, e.g. `b12`;
- `raw`, `title`, `authors`, `year`, `venue`;
- `doi`: normalised, as parsed by GROBID;
- `cited_in_text`: the number of markers pointing at this entry;
- `in_corpus: PaperRef?` and `match: "doi" | "title"`.

**Matching**:
- `doi`: the GROBID DOI equals a corpus DOI alias.
- `title`: no DOI match, and the title equals a corpus title after normalisation (≥ 20 characters, year ± 1). It must designate exactly one paper.
- There is no fuzzy matching: a wrong "in corpus" is worse than a missed one.
- Duplicates resolve to their canonical paper.

**Errors**: `404`; `424 SOURCE_UNAVAILABLE` (no TEI); `503`.

**Agent notes**:
- GROBID's reference parsing is imperfect: DOIs can be truncated or glued to the next field, and titles can carry the journal. Before you cite a reference, verify it with `POST /doi/verify` (planned) or the registry.
- `in_corpus = null` means "not matched", not "not in the corpus". Try `GET /papers/resolve?title=…&year=…`, which uses fuzzy matching.
- `cited_in_text = 0` usually means GROBID did not link the markers. It does not mean the work is uncited.

---

## `GET /papers/{paper_id}/entities`
- **Status**: implemented (2026-10-02) · `read` · S
- **Purpose**: methods, sensors and metric mentions normalised to the ontology (`canonical_id`), with the task, study type and study area the extractor assigned.
- **Source**: the paper's normalized JSON (`core.paper_file`, kind `normalized`) through the graph loader's edge logic, plus the grounding file `data/graph_inputs/entity_grounding.parquet`. These are the same data as the graph's edges.

**Response 200**: `{"paper": PaperRef, "methods": [EntityMention], "sensors": [...], "metrics": [...], "task": {"label", "confidence", "source"}?, "study_type": {...}?, "study_area": {"primary_country", "countries": [{"name", "source", "confidence"}], "rivers": [...], "dropped_country_names": int}, "source_file", "provenance"}`

- `EntityMention` = `{canonical_id, display_name, surface_form, role: used|mentioned|null, confidence, grounded, tei_mentions, tei_evidence[], evidence[], page}`. Grounded mentions come first.
- `metrics` are mentions only. Values are in `GET /metrics/facts` (tables) and `POST /metrics/extract` (text).
- Country names that are obviously not names are dropped and counted: URLs, "al.", anything with a digit such as "WGS84".

**Errors**: `404`; `424 SOURCE_UNAVAILABLE` when the paper has no normalized entity file.

**Agent notes**:
- Entity labels come from a gazetteer plus rules and have **not** been validated against a human gold set: the `accepted` flag of the old pipeline carries no information.
- `grounded = false` means the term is not written in the paper's text as such. Treat the mention as unsupported until `/text?q=…` shows the concept.
- `study_area.countries` mixes the study area with countries named in the literature review (`source = ner`). Use `primary_country` and read the text.

---

## `GET /papers/{paper_id}/tables`
- **Status**: implemented (2026-10-02: tables, captions and cells; `facts` is planned, work package 1.8) · `read` · S
- **Purpose**: TEI tables (caption plus rows) and, later, the numeric facts extracted from them.

**Response 200**: `{"paper": PaperRef, "tables": [{"table_id": "tab_2", "xml_id": "tab_2", "label": "2", "caption": "…", "page": 7, "rows": [["Station", "NSE", "KGE"], ["Kherson", "0.82", "0.77"]], "facts": null}], "provenance"}`

- `table_id` is the passage id used by `/text` and `/quotes/verify`.
- `facts = null` means "not computed". It does not mean "no facts".

**Errors**: `404`; `424 SOURCE_UNAVAILABLE`; `503`.

**Agent notes**:
- GROBID table parsing can misalign columns. Check a value against the row and column header before you rely on it, or read the PDF page (`page`).
- When `facts` arrives, `range_verdict = suspect` will mark values outside the metric's valid range.

