# Bibliography — DOI metadata, verification, BibTeX, manuscript citations

**Backing data**:
- `biblio.*` in Postgres (migration 0002): works, verifications, cite keys, technical sources, and an HTTP cache with TTL.
- Crossref and OpenAlex (polite pool, contact from `OPEN_ALEX_EMAIL`).
- Failed or timed-out lookups are **not** cached as "not found".

**Citation-key convention**:
- `Surname_YYYY`, with two authors `Wilson_Sader_2002`;
- organisations and datasets: `UNOSAT_3616_2023`, `ATL13_v6`;
- the authors' own papers: `Paper1_Nikoriak_2026`;
- aliases live in `biblio.cite_key` (`Roberts_2017` ≡ `Roberts_2017_blockCV`).

---

## `GET /doi/{doi}`
- **Status**: planned (phase 1, WP 1.10) · **Scope** `read` · S
- **Purpose**: registry metadata for a DOI, merged from Crossref and OpenAlex (DataCite for dataset DOIs). The source of each field is reported.
- **Path**: URL-encoded DOI in any form.
- **Response 200**: `DoiMetadata` + `{"in_corpus": PaperRef?, "provenance"}`.
- **Errors**: `422 INVALID_DOI`; `404 NOT_FOUND` (no registry knows it, e.g. JMLR papers without a DOI); `504 UPSTREAM_TIMEOUT`.
- **Agent notes**:
  - `year_online` and `year_print` can differ: Biancamaria et al. was online 2015-10-27 and in print 2016-03, vol. 37.
  - Cite the print year with its volume unless the journal style says otherwise, and keep both in the bib note.

---

## `POST /doi/verify`
- **Status**: planned (phase 1) · **Scope** `read` · S for ≤ 50 entries, **J** above
- **Purpose**: verify bibliography entries field by field against the registries.

**Request**: `{"entries": [{"key": "Monti_2024", "doi": "10.24425/agg.2023.146162", "title": "The Nova Kakhovka dam collapse flooding as seen from Sentinel-1 SAR satellite images", "authors": "Monti, R. and Rossi, L. and Reguzzoni, M.", "year": 2024, "journal": "Advances in Geodesy and Geoinformation", "volume": null, "pages": null}], "project_id": "floodstate-eo:paper3"}`. Entries may also be raw BibTeX strings: `{"bibtex": "@article{…}"}`.

**Response 200**: `{"results": [DoiVerifyResult], "summary": {"VERIFIED": n, "VERIFIED_WITH_NOTES": n, "MISMATCH": n, "UNRESOLVED": n, "NOT_A_DOI": n}, "provenance"}`

**Rules**:

| Check | Accepted when |
|---|---|
| Title | similarity ≥ 0.90 after normalisation |
| Year | equal to the online or print year (± 0); a 1-year difference explained by online/print gives `VERIFIED_WITH_NOTES` |
| Authors | family names and initials compared |
| Volume, pages | `article_number` accepted for pages; a registry value such as `50-50` is reported, not trusted |
| Suffix years | "2024a" is accepted as 2024 |

Results are stored in `biblio.verification` with `labeler_kind = rule`.

```json
{"results": [{"input_key": "Monti_2024", "verdict": "VERIFIED_WITH_NOTES",
  "diffs": [{"field": "pages", "given": null, "registry": "50-50", "source": "crossref", "severity": "info"}],
  "notes": ["Crossref pages '50-50' look like an article-number artefact; take pages from the journal page",
            "first-author given name: Roberto"],
  "registry": {"doi": "10.24425/agg.2023.146162", "year_online": 2024, "…": "…"},
  "in_corpus": {"paper_id": "10.24425_agg.2023.146162", "…": "…"}}],
 "summary": {"VERIFIED_WITH_NOTES": 1}, "provenance": {"…": "…"}}
```

**Agent notes**:
- A `VERIFIED` DOI says the bibliographic record is right. It says nothing about whether the cited **content** supports your sentence: use `POST /quotes/verify` / `POST /claims/check` for that.

---

## `POST /bib/format`
- **Status**: planned (phase 1) · **Scope** `read` · S
- **Purpose**: DOIs to BibTeX entries in the house key convention, with a verification note.
- **Request**: `{"dois": string[] (≤ 100), "project_id": string?, "key_style": "Surname_YYYY"}`.
- **Response 200**: `{"entries": [{"doi", "key": "Lehnigk_2026", "bibtex": "@article{Lehnigk_2026, …, note = {Crossref-verified 2026-10-02}}", "collision": false}], "provenance"}`.
- **Behaviour**:
  - The key year follows the registry print year.
  - Key collisions within `project_id` get suffixes `a`, `b` and are flagged.
  - Cyrillic author names are transliterated (national standard) for keys only; the `author` field keeps the original.

---

## `POST /bib/audit`
- **Status**: planned (phase 1) · **Scope** `read` · **J**
- **Purpose**: a full audit of a `.bib` file.
- **Request**: multipart `file=@references.bib` or JSON `{"bibtex": string}`; plus `project_id`.
- **Checks**:
  - DOI verification (as `/doi/verify`);
  - missing DOIs where the registry has one;
  - duplicate entries (same DOI, two keys);
  - key collisions and key/year disagreement;
  - mixed field case (`DOI=` vs `doi=`);
  - entries with free-text status notes (`VERIFY …`).
- **Result artifact**: `{"entries": [{"key", "status": "ok|fix|unresolved", "problems": [...], "suggested_bibtex"?}], "summary": {...}}`.

---

## `POST /bib/render`
- **Status**: planned (phase 1) · **Scope** `read` · S
- **Purpose**: a formatted reference list for a manuscript, from a set of keys or from the manuscript text itself.
- **Request**: `{"bibtex": string, "keys": string[]?, "manuscript": string?, "style": "agu" | "copernicus" | "elsevier-harvard" | "apa"}`. With `manuscript`, the keys are taken from its citations (as `/manuscripts/citations`).
- **Response 200**: `{"references": [{"key", "text": "Lehnigk, K. E., Pavelsky, T. M., & Lang, K. A. (2026). SWOT satellite observations …"}], "unresolved_keys": string[], "uncited_entries": string[], "provenance"}`.

---

## `POST /manuscripts/citations`
- **Status**: planned (phase 1) · **Scope** `read` · S
- **Purpose**: find every citation in a manuscript and map it to bibliography keys. Replaces `p100b.citing_sentences`.
- **Request**: `{"manuscript": string (markdown), "bibtex": string, "project_id": string?}`.
- **Behaviour**:
  - Recognises "(Surname et al. YYYY)", "(Surname et al., YYYY)", "Surname and Other (YYYY)", several citations in one bracket, and suffixed years.
  - Quoted words inside the sentence are returned as `quoted`.
  - Table placeholders (`{{T…}}`) are left untouched.
- **Response 200**: `{"occurrences": [CitationOccurrence], "missing_keys": [{"cite_text", "section", "sentence"}], "uncited_entries": string[], "provenance"}`.
- **Agent notes**: feed `occurrences` with `quoted` words straight into `POST /quotes/verify`.
