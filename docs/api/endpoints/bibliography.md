# Bibliography — DOI metadata, verification, BibTeX, manuscript citations

**Backing data**:
- `biblio.*` in Postgres (migrations 0003–0004): verifications, cite keys, technical sources, and `biblio.http_cache`.
- `biblio.http_cache` keeps registry answers: a 200 for 180 days; a definitive 404/410 for 30 days.
- Crossref is the primary registry. DataCite answers for DOIs Crossref does not know (datasets). OpenAlex fills gaps and supplies the open-access fields. Contact for the polite pools: `OPEN_ALEX_EMAIL`.
- Failed or timed-out lookups are **not** cached as "not found". When a refresh fails and an expired answer exists, that answer is used and the response says so (`fetched[registry] = "stale_cache"`).

**Citation-key convention**:
- `Surname_YYYY`, with two authors `Wilson_Sader_2002`;
- organisations and datasets: `UNOSAT_3616_2023`, `ATL13_v6`;
- the authors' own papers: `Paper1_Nikoriak_2026`;
- aliases live in `biblio.cite_key` (`Roberts_2017` ≡ `Roberts_2017_blockCV`).

---

## `GET /doi/{doi}`
- **Status**: implemented (2026-10-02) · **Scope** `read` · S
- **Purpose**: registry metadata for a DOI, merged from Crossref, DataCite and OpenAlex. Each field names its source.
- **Path**: the DOI in any form (`10.1007/…`, `https://doi.org/…`, `doi:…`); slashes need no encoding. Query: `refresh=true` ignores cached answers.
- **Response 200**: `DoiMetadata` + `{"in_corpus": PaperRef?, "provenance"}`. Key fields:
  - `year_online`, `year_print`, `year_issued`, and `date_online`, `date_print` (partial ISO dates);
  - `sources`: the registry of each field;
  - `fetched`: per registry, `network | cache | stale_cache | not_found | unavailable`.
- **Errors**:
  - `422 INVALID_DOI`;
  - `404 NOT_FOUND`: no registry knows it, e.g. JMLR papers without a DOI;
  - `504 UPSTREAM_TIMEOUT`: a registry did not answer and nothing is cached. Retry later; it is not "not found".
  - `503 STORE_UNAVAILABLE`: the cache in Postgres is unavailable.
- **Agent notes**:
  - `year_online` and `year_print` can differ: Biancamaria et al. was online 2015-10-27 and in print 2016-03, vol. 37.
  - Cite the print year with its volume unless the journal style says otherwise, and keep both in the bib note.

---

## `POST /doi/verify`
- **Status**: implemented (2026-10-02; ≤ 50 entries per request) · **Scope** `read` · S · **no LLM**
- **Purpose**: verify bibliography entries field by field against the registries.

**Request**: `{"entries": [BibInput] (1–50), "project_id": ProjectId?, "refresh": false}`.
- `BibInput` fields: `{key?, doi?, title?, authors?, year?, journal?, volume?, issue?, pages?}`. `authors` is a BibTeX `"Family, G. and …"` string or a list; `"and others"` marks a truncated list.
- An entry may instead be `{"bibtex": "@article{…}"}`.
- With `project_id`, an entry without a DOI takes it from the project's cite key (`biblio.cite_key`).

```json
{"entries": [{"key": "Monti_2024", "doi": "10.24425/agg.2023.146162", "title": "The Nova Kakhovka dam collapse flooding as seen from Sentinel-1 SAR satellite images", "authors": "Monti, R. and Rossi, L. and Reguzzoni, M.", "year": 2024, "journal": "Advances in Geodesy and Geoinformation"}],
 "project_id": "floodstate-eo:paper3"}
```

**Response 200**: `{"results": [DoiVerifyResult], "summary": {"VERIFIED": n, "VERIFIED_WITH_NOTES": n, "MISMATCH": n, "UNRESOLVED": n, "NOT_A_DOI": n}, "provenance"}`

**Rules**:

| Check | Accepted when | Otherwise |
|---|---|---|
| Title | similarity ≥ 0.90 after normalisation; the subtitle is optional | `major` (`MISMATCH`); 0.90–0.98 is `info` |
| Year | equal to the online, print or issued year; "2024a" is read as 2024 | `major`. When online ≠ print, a note gives both dates |
| Authors | the first family name agrees (accents and case ignored) | `major`. Other missing names, a different count (unless `and others`), or different initials are `minor` |
| Journal | one name contains the other, or similarity ≥ 0.85; abbreviations are fine | `info` |
| Volume, issue, pages | equal; `article_number` is accepted for pages | `minor`. A field the registry has but the entry lacks is `info`. A registry range such as `50-50` is a note, not a difference |

**Verdict**:
- `MISMATCH` if any difference is `major`.
- `VERIFIED_WITH_NOTES` if there are other differences or notes.
- `VERIFIED` otherwise.
- `UNRESOLVED` when there is no DOI, no registry knows it, or the registries did not answer (the notes say which).
- `NOT_A_DOI` when the DOI field is malformed.

**Results are not stored**: the endpoint is read-only. Registry answers are cached.

```json
{"results": [{"input_key": "Monti_2024", "doi": "10.24425/agg.2023.146162", "verdict": "VERIFIED_WITH_NOTES",
  "diffs": [],
  "notes": ["crossref pages '50-50' look like an article-number artefact; take the pages from the journal page"],
  "registry": {"doi": "10.24425/agg.2023.146162", "year_online": 2024, "authors": [{"family": "Monti", "given": "Roberto", "orcid": "0009-0006-1608-6648"}, "…"], "…": "…"},
  "in_corpus": {"paper_id": "10.24425_agg.2023.146162", "…": "…"}}],
 "summary": {"VERIFIED_WITH_NOTES": 1}, "provenance": {"…": "…"}}
```

Regression fixtures:
- Biancamaria 2016 → `VERIFIED_WITH_NOTES` (online 2015-10-27, print 2016-03, vol. 37);
- Monti 2024 → `VERIFIED_WITH_NOTES` (pages `50-50`);
- Pedregosa 2011 (JMLR, no DOI) → `UNRESOLVED`;
- ICESat-2 ATL13 v6 (`10.5067/ATLAS/ATL13.006`, DataCite) → `VERIFIED`.

**Agent notes**:
- A `VERIFIED` DOI says the bibliographic record is right. It says nothing about whether the cited **content** supports your sentence: use `POST /quotes/verify` / `POST /claims/check` for that.
- `UNRESOLVED` with "did not answer" means "not checked". Retry later; do not drop the reference.
- `registry.authors[].given` gives full given names. Take initials from there; do not guess them.

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
- **Status**: implemented (2026-10-02) · **Scope** `read` · S · **no LLM**
- **Purpose**: find every citation in a manuscript and map it to bibliography keys. Replaces `p100b.citing_sentences`.
- **Request**: `{"manuscript": string (markdown), "bibtex": string, "project_id": string?}`.
- **Behaviour**:
  - Recognises "(Surname et al. YYYY)", "(Surname et al., YYYY)", "Surname and Other (YYYY)", "A & B 2019", several citations in one bracket separated by ";", "Surname (2019, 2020)" and suffixed years ("2024a", "2024a, b").
  - "e.g.", "see" and "cf." prefixes are ignored.
  - Sentences are not split after "et al.", "e.g.", "Fig." or initials.
  - Markdown headings give `section`; fenced code is skipped; table placeholders (`{{T…}}`) are left untouched.
  - Quoted words inside the sentence ("…" or “…”) are returned as `quoted`.
- **Matching**:
  - A citation resolves when exactly one entry has the same year and first-author family name (accents and case ignored) and the right author count: one author, two (A and B), or three or more for "et al.". Two authors are accepted when no three-author entry exists.
  - A suffix picks between keys that end with it.
  - Corporate authors cited by acronym resolve through the house key: "(CEOBS 2023)" → `CEOBS_2023`.
- **Response 200**: `{"occurrences": [{"cite_text", "authors", "year", "status": "resolved"|"ambiguous"|"missing", "cite_key", "candidates", "doi", "section", "sentence", "line", "quoted"}], "missing_keys": [{"cite_text", "section", "sentence", "line"}], "uncited_entries": string[], "summary": {"resolved", "missing", "ambiguous", "entries", "cited_entries"}, "provenance"}`.
- **Errors**: `422` when the BibTeX text has no entries.
- **Agent notes**:
  - Feed `occurrences` with `quoted` words straight into `POST /quotes/verify` (`source = cite_key` with `project_id`, or `doi`).
  - A quotation in a sentence that cites several works is returned for each of them. Verify them together; `found_in` tells you which work holds the words.
  - `missing` usually means the bib lacks the entry, or its year or first author differs from the text. Check both before adding a duplicate.
