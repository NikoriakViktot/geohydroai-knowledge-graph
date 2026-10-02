# Building a paper: from a paper repository to a delivered, reviewed manuscript

> **Українською.**
> - Ця сторінка для агента (Claude Code), який працює в репозиторії статті (floodstate-eo, kakhovka-terrain, SWOT-DNIPRO): що він готує сам, що робить через інструменти `ghai`, і як цех статей у репозиторії знань збирає тези, докази, цитати, бібліографію, рукопис, таблиці, рисунки й рецензію та доставляє їх назад.
> - Код збирання живе в одному місці (репозиторій знань, `python -m src.workbench`). Дані й власний аналіз статті живуть у її репозиторії. Коміт у репозиторії статті робить людина.
> - Порядок обов'язковий: паспорт → власність тверджень → докази з літератури → цитати й бібліографія → рисунки й таблиці → збирання → рецензія → доставка.

**Applies to**: an agent in a paper repository with the `ghai` MCP server (see [MCP_TOOLS.md](MCP_TOOLS.md)), and anyone running the workbench. Rules: [AGENT_RULES.md](AGENT_RULES.md) (W6 is this page in one paragraph). The manifest: [PROJECT_MANIFEST.md](PROJECT_MANIFEST.md).

---

## 1. Who does what

| | Paper repository (yours) | Knowledge repository (the workbench) |
|---|---|---|
| owns | the question, the data, its own analysis scripts and their numbers (OWN_EVIDENCE), templates, theses and atomic claims, the .bib, review rules, the passport | the corpus, the literature, the API, the build machinery: assembly, review engine, bibliography, citations, literature evidence, delivery |
| writes | the inputs listed in `ghai.project.yaml` | `data/workbench/<project>/out/`, then delivers into your repository with a manifest under `.ghai/deliveries/` |
| commits | **the human**, after reading `git status` | never commits anywhere |

A paper's own numbers never come from the knowledge repository, and literature never comes from memory (R-SCI-1, R-SCI-9). The knowledge repository holds no paper-specific folders, scripts or dumps (R-DATA-4): everything paper-specific is data in your repository, read through the manifest.

## 2. The contract in one picture

```
your repo                                   knowledge repo (python -m src.workbench <project_id> <step>)
─────────                                   ─────────────────────────────────────────────────────────────
ghai.project.yaml  ──────── read ────────▶  manifest (paths, dialect, style, languages)
PASSPORT.md, theses.json,                   theses ── POST /theses/validate
atomic_claims.yaml, *.bib,  ──── pull ───▶  literature ── retrieval, screening, references → 01–10
templates/, OWN_EVIDENCE.csv,               citations (W1) ── /manuscripts/citations → /quotes/verify
tables/*.csv, figures/,                     bibliography (W2) ── /bib/audit → /bib/render
captions.md, review_rules.yaml              tables, figures, assemble [--final], translate, review
                                 ◀── deliver ── out/: manuscript, OPEN_ITEMS, reports, .ghai/deliveries/<stamp>.json
human: git status → git commit
```

The layer of truth is Postgres in the knowledge repository; your theses and claims are validated against contract v1 and recorded there by project id.

## 3. Stage 0 — Passport (before anything else)

`PASSPORT.md` (path: `paths.passport`) answers, in this order: the question; the paper's own result; what it borrows (from which paper or dataset); the novelty boundary, stated first when it is tight; the key figure and its independent check; the open limitations; a verdict (ready / not yet). Nothing below starts while the passport is missing or says "not yet": a manuscript cannot be better than the result it reports.

## 4. Stage 1 — Claim ownership

Every sentence of the manuscript is one of two kinds, and the two never mix:

- **Own numbers.** Produced by your repository's analysis, recorded in `OWN_EVIDENCE.csv` (`claim_id, claim_text, value_resolved, n_resolved, uncertainty_resolved, scientific_caveat, audit_status, remaining_action`). A template reaches them only through `{{claim:ID}}`. Only `audit_status` in SUPPORTED, SUPPORTED_WITH_LIMITATION, REVISE_UNCERTAINTY, NOT_TESTABLE renders; the last two only as limitations. Anything else withholds the paragraph behind a `[PENDING]` marker.
- **Literature claims.** Theses (`theses.json`) and atomic claims (`atomic_claims.yaml`), contract v1. Check them before anything else:
  - tool `validate_theses` (or step `theses`): nothing is coerced; fix the document where the error points.
  - `UNKNOWN_PROJECT`: the paper is not registered; run `init` (§11), never invent a namespace.

## 5. Stage 2 — Literature evidence

- Search with several phrasings: `search_literature`, `search_papers`. A similar passage is not evidence.
- Read what a source says: `get_paper_sections`, `get_paper_text`.
- Before citing, check the quotation in the source: `verify_quotes`.
- Run per atomic claim: step `literature prepare | retrieve | select | screen | references | export | report`. It writes the audit deliverables 01–10 into `paths.literature_audit`. Roles: SUPPORTS, CONTRASTS, COMPARATOR, METHOD_FROM, LIMITATION, DEFINITION, DATASET_DOCUMENTATION, BACKGROUND. Statuses: VERIFIED_SUPPORTED … NO_EVIDENCE_IN_CORPUS. Human decisions go into `overrides.yaml`, each with a justification.
- Absence and novelty: "not found" is never "does not exist". Write "first", "novel" or "no study" only with CANDIDATE_GAP (R-SCI-3); the assembler refuses those words in rendered text.
- Missing literature: discovery and legal open-access ingestion (W4; planned). Never fetch `open_url` links, never paywalled copies (R-DATA-3).

## 6. Stage 3 — Citations and bibliography

**W1, citations** (step `citations`; tools `manuscript_citations`, `verify_quotes`):
1. Every author–year citation is resolved to the .bib.
2. Every quotation is checked in the full text of the sources its sentence cites. A sentence that cites several works is judged over all of them: the quoted words come from one.
3. Verdicts per key:
   - VERIFIED: holds a found quotation;
   - FIX: the quoted words are in none of the cited sources, or their numbers are missing;
   - OPEN: the source text is unavailable, or the key is missing or ambiguous; unverified, never "false";
   - UNCHECKED: cited without quoted words; claim support is POST /claims/check, planned.
4. `attribution.cites_other_sources` means a possible secondary citation: trace the original (R-SCI-4).

**W2, bibliography** (step `bibliography`; tools `audit_bib`, `verify_bib_entries`, `format_bib`, `render_bibliography`). Every entry is checked against Crossref, DataCite and OpenAlex, then marked ok, fix or unresolved. Corrected entries are suggested in `bibliography_suggestions.bib`; the .bib itself is never rewritten. `REFERENCES.md` holds the rendered reference list in `citation.style`. Metadata come from the registries, never from memory (R-SCI-10).

## 7. Stage 4 — Figures and tables

- **Tables** are CSV files in `paths.tables`: one file per table, the numbers produced by your analysis. Claim tables (`assembly.table_specs`) are built from OWN_EVIDENCE rows instead.
- **Figures** are images in `paths.figures`, named `<ID>_<anything>.png|pdf`. Captions are paragraphs in `paths.captions`, opening with `**<ID> Title.**`.
- Steps `tables` and `figures` write inventories with sha256 and caption checks: every figure needs a caption, every caption an image. The paper's own manifests are never touched.
- Numbering is automatic, by first appearance in the templates. Refer to a table or figure with `{{ref:table:T3}}` or `{{ref:figure:F2}}`, never with a typed "Table 5".

## 8. Stage 5 — Assembly

Step `assemble`. The dialect is set by `placeholders.dialect`:

- `ghai` — section templates (`*.md`, in name order) with these placeholders:
  - `{{claim:M1.1}}`, plus `.n`, `.unc`, `.text`, `.caveat`;
  - `{{cite:key}}` and `{{cite:a,b}}`;
  - `{{table:T3}}` and `{{figure:F2}}`;
  - `{{ref:table:T3}}`;
  - `{{section:NAME}}`;
  - `{{pending:FIG08|title|backs=…|section=…|produces=…}}`.
- `floodstate_fill` — one template, every number a table cell: `{{T12|region=X,date=Y|column|agg|fmt}}`. An unresolvable cell becomes `[[MISSING: …]]`.
- `none` — the manuscript is written directly; nothing is assembled.

Output:
- the manuscript (`paths.manuscript_out`), with `OPEN_ITEMS.md` (every remaining marker), `claims_used.csv` and `assembly_report.json`;
- a `.docx` in an ignored `private/` folder.

`assemble --final` is the submission gate: with any `[PENDING]` marker or `[[MISSING]]` cell left, nothing is staged.

Other languages: step `translate`. Numbers, DOIs, ids, citations and markers are locked; a paragraph whose tokens do not round-trip stays in English under a marker. The glossary is `paths.glossary`.

## 9. Stage 6 — Review

Step `review` writes `REVIEW_REPORT.md` and `review_findings.json`, combining:

- the paper's own rules in `paths.review_rules` (ghai.review_rules/v1). Rule types:
  - `forbidden` phrases;
  - `pattern`, `near` and `requirements` on regex windows;
  - `co_occurrence`, `variants` and `absent` for consistency across files;
  - `table_interval`, `table_rows` and `table_text` for checks against tables.
- open markers;
- the results of theses, citations and bibliography.

Severities: CRITICAL / MAJOR (blocks submission), MINOR, INFO. A finding names the location, what was observed, what the paper's terminology or tables require, and a fix. Revisions are written as anchored edits (`revisions.yaml`: `original` occurs exactly once, or `insert_after`); a failed match aborts.

## 10. Stage 7 — Delivery

Step `deliver` plans every file before writing anything:

| plan | when |
|---|---|
| new | the file does not exist in your repository |
| update | a clean tracked file differs (the old version stays in git) |
| same | identical bytes |
| conflict | not overwritten without `--force`: the file is modified, untracked or ignored and differs; or the repository holds a newer committed version; or it changed since the last delivery |
| blocked | never written: secret-like content, a `never_deliver` glob, or a binary build (pdf, docx, tif) in a public repository outside an ignored `private/` folder |

What is written goes as one stream with `.ghai/deliveries/<stamp>_<project>.json` and `.sha256`, and is checked there with `sha256sum -c`. Each record describes its own delivery: a later one may change a file again (two papers of one repository share `CLAUDE.md`), so verify the newest record. Then the human reads `git status` and commits. A `private/` folder carries its own `.gitignore`; its files are for the author only.

## 11. Running it

From the knowledge repository (or, in a paper repository, `ghai-workbench …`, which runs the same command there):

```bash
python -m src.workbench projects                                   # the registry of papers
python -m src.workbench <project_id> init [--dry-run]              # once: manifest, .mcp.json, CLAUDE.md section, registry
python -m src.workbench <project_id> status                        # where the paper stands
python -m src.workbench <project_id> theses                        # contract v1
python -m src.workbench <project_id> literature prepare|retrieve|select|screen|references|export|report
python -m src.workbench <project_id> citations                     # W1
python -m src.workbench <project_id> bibliography                  # W2
python -m src.workbench <project_id> tables | figures
python -m src.workbench <project_id> assemble [--final]
python -m src.workbench <project_id> translate
python -m src.workbench <project_id> review
python -m src.workbench <project_id> deliver [--dry-run] [--force]
```

`--to DIR` runs a step against a scratch directory instead of the repository; nothing is recorded. Reports are staged in `data/workbench/<project>/out/<reviews>/workbench/` until `deliver`.

## 12. Checklist before handing a manuscript to a human

- the passport says ready, and the novelty boundary in the text matches it;
- `assemble --final` passes: no `[PENDING]`, no `[[MISSING]]`;
- `review`: no CRITICAL or MAJOR finding left unexplained;
- citations: no FIX; every OPEN listed in OPEN_ITEMS with its reason;
- bibliography: no `fix` entry; every cited key resolved;
- every literature number has its span (R-SCI-2), every model verdict is labelled model-assessed (R-SCI-6);
- the provenance blocks name the knowledge commit, the repository HEAD and the corpus manifest (R-SCI-8);
- `deliver --dry-run` shows no blocked file; the human has read `git status`.

## 13. What an agent must never do

- type a number into a template or a manuscript: numbers come from OWN_EVIDENCE, a table cell or a verified literature span;
- cite from memory, or cite a source for words it does not contain;
- write "first", "novel" or "no study" without CANDIDATE_GAP;
- edit delivered files in the knowledge repository, or put paper-specific scripts, data or folders there (R-DATA-4);
- deliver PDFs, docx builds, keys or `.env` files into a public repository; write a key into `.mcp.json` or the manifest;
- commit in the paper repository on the human's behalf, or force a delivery over someone's uncommitted work;
- bypass the gate: remove a marker without resolving it, or downgrade a MAJOR finding to make the report clean.

## 14. Glossary and links

| term | meaning |
|---|---|
| manifest | `ghai.project.yaml` in the paper repository: [PROJECT_MANIFEST.md](PROJECT_MANIFEST.md) |
| registry | `project.project` in Postgres: where each paper's repository is and what was last delivered |
| OWN_EVIDENCE | the paper's own numbers with their status, produced by its analysis |
| marker | `> **[PENDING ID]** …`, a block quote naming what is still missing; FIG, TAB, OPEN, REF, REV |
| staging | `data/workbench/<project>/{in,out,work}` in the knowledge repository |
| delivery manifest | `.ghai/deliveries/<stamp>_<project>.json` + `.sha256` in the paper repository |

Tools: [MCP_TOOLS.md](MCP_TOOLS.md) · schemas: [SCHEMAS.md](SCHEMAS.md) · errors: [README.md](README.md) §5 · evidence endpoints: [endpoints/evidence.md](endpoints/evidence.md) · bibliography endpoints: [endpoints/bibliography.md](endpoints/bibliography.md).
