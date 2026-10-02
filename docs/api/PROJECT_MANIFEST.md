# ghai.project.yaml — how a paper repository describes its paper to the workbench

> **Українською.** Маніфест лежить у репозиторії статті, поруч зі статтею, і комітиться там. Він каже цеху статей, де тези, атомарні твердження, .bib, шаблони, власні числа, таблиці, рисунки, підписи, рецензії й правила рецензії, яким діалектом збирати рукопис, яким стилем цитувати, якими мовами. Усі шляхи — відносно кореня репозиторію. Ключів і секретів у маніфесті немає ніколи.

**Schema**: `ghai.project/v1` · **Where**: the registry's `manifest_path` (one manifest per paper; a repository may hold several papers) · **Written by**: `python -m src.workbench <project_id> init`, then owned and edited by the paper repository · **Read by**: every workbench step ([PAPER_WORKFLOW.md](PAPER_WORKFLOW.md)).

Paths are repository-relative and checked: no absolute paths, no `..`. A trailing `/` marks a directory. `{lang}` in `manuscript_out` is replaced by each language.

---

## Fields

| field | meaning |
|---|---|
| `schema` | `ghai.project/v1` |
| `project_id` | `<repository>:<paper>` or a bare name in lower case; must be registered (`init`) |
| `title` | the paper's working title |
| `repo` | `{distro, path, remote?, public}`: where the repository is. `public: true` (the default) admits text artefacts only into git; pdf/docx/tif go to ignored `private/` folders |
| `publication_dir` | where built outputs go by default |
| `paths.manuscript_templates` | a directory of section templates (`*.md`, name order, `_*` skipped), or one template file |
| `paths.manuscript_out` | the built manuscript; `{lang}` for several languages |
| `paths.theses`, `paths.atomic_claims` | contract v1 documents (`POST /theses/validate`) |
| `paths.bib` | the paper's BibTeX file |
| `paths.own_evidence` | `OWN_EVIDENCE.csv`: the paper's own numbers (`{{claim:…}}`) |
| `paths.tables`, `paths.figures`, `paths.captions` | CSV tables; images named `<ID>_…`; caption paragraphs `**<ID> Title.** …` |
| `paths.reviews` | reports go to `<reviews>/workbench/` |
| `paths.passport` | `PASSPORT.md` (stage 0) |
| `paths.literature_audit` | the literature evidence run: its inputs (`atomic_claims.yaml`, `overrides.yaml`, `novelty_verdicts.yaml`, `claims_theses.yaml` …) and deliverables 01–10 |
| `paths.review_rules` | `review_rules.yaml` (ghai.review_rules/v1), the paper's own deterministic checks |
| `paths.glossary` | translation glossary (YAML, English term → term) |
| `placeholders.dialect` | `ghai` (section templates + claims + citations), `floodstate_fill` (table cells), `none` (written directly) |
| `assembly.references_csv` | a legacy reference registry (Paper 1); otherwise citations come from `paths.bib` |
| `assembly.table_specs` | YAML of claim tables `{id: {title, columns: [[heading, field]], rows: [[label, claim_id]], note}}` |
| `assembly.sections` | `{{section:NAME}}` → a repository file |
| `assembly.number_tables`, `assembly.number_figures` | number by first appearance (default true) |
| `assembly.docx` | where the .docx goes (default: `<manuscript folder>/private/<name>.docx`) |
| `citation.style` | `apa`, `agu`, `copernicus` or `elsevier-harvard` (`POST /bib/render`) |
| `analysis` | `{runner: consumer, command}`: the paper's own analysis, run in its repository; `none` by default |
| `literature.chroma_collection` | the vector collection of the evidence run (`flood_papers_768d_v2`) |
| `languages` | `[en]`, or e.g. `[en, uk]` for a translated manuscript |
| `never_deliver` | extra globs no delivery may write; `.env`, `*.key`, Zone markers and caches are always refused |

## The papers registered on 2026-10-02

These are the manifests `init` writes; after that the paper repository edits its own.

### `floodstate-eo:paper3` — `case_studies/kakhovka_2023/ghai.project.yaml`

```yaml
# Paper 3 — daily inundation of the Kakhovka breach (U-Net): how the GeoHydroAI paper workbench builds this paper.
# Schema: docs/api/PROJECT_MANIFEST.md of the knowledge repository. Paths are relative to the
# repository root. No keys or secrets belong here: the repository may be public.
schema: ghai.project/v1
project_id: floodstate-eo:paper3
title: Paper 3 — daily inundation of the Kakhovka breach (U-Net)
repo:
  distro: Ubuntu-24.04
  path: /home/niko/repo/floodstate-eo
  public: true
publication_dir: case_studies/kakhovka_2023/publication
paths:
  manuscript_templates: case_studies/kakhovka_2023/publication/manuscript_template.md
  manuscript_out: case_studies/kakhovka_2023/publication/manuscript.md
  theses: case_studies/kakhovka_2023/publication/literature/theses.json
  atomic_claims: case_studies/kakhovka_2023/literature_audit/atomic_claims.yaml
  bib: docs/references.bib
  tables: case_studies/kakhovka_2023/publication/tables/
  figures: case_studies/kakhovka_2023/publication/figures/
  captions: case_studies/kakhovka_2023/publication/captions.md
  reviews: case_studies/kakhovka_2023/reviews/
  passport: case_studies/kakhovka_2023/publication/PASSPORT.md
  literature_audit: case_studies/kakhovka_2023/literature_audit/
  review_rules: case_studies/kakhovka_2023/review_rules.yaml
placeholders:
  dialect: floodstate_fill
assembly:
  sections: {}
  number_tables: true
  number_figures: true
citation:
  style: apa
analysis:
  runner: none
literature:
  chroma_collection: flood_papers_768d_v2
languages:
- en
never_deliver:
- _work/**
```

### `swot-dnipro:paper1` — `ghai.project.yaml`

```yaml
# Paper 1 — water-surface geometry of the former reservoir: how the GeoHydroAI paper workbench builds this paper.
# Schema: docs/api/PROJECT_MANIFEST.md of the knowledge repository. Paths are relative to the
# repository root. No keys or secrets belong here: the repository may be public.
schema: ghai.project/v1
project_id: swot-dnipro:paper1
title: Paper 1 — water-surface geometry of the former reservoir
repo:
  distro: Ubuntu-24.04
  path: /home/niko/repo/SWOT-DNIPRO
  public: true
publication_dir: outputs/paper
paths:
  manuscript_templates: outputs/paper/templates/
  manuscript_out: outputs/paper/knowledge_repo/paper1_manuscript_{lang}.md
  own_evidence: outputs/paper/knowledge_repo/evidence/OWN_EVIDENCE.csv
  tables: outputs/paper/tables/
  figures: outputs/paper/figures/
  passport: outputs/paper/PASSPORT.md
  glossary: outputs/paper/templates/translate_glossary.yaml
placeholders:
  dialect: ghai
assembly:
  references_csv: outputs/paper/knowledge_repo/references/REFERENCES.csv
  table_specs: outputs/paper/templates/table_specs.yaml
  sections:
    '7_8': outputs/paper/knowledge_repo/SECTION_7_8_STUB.md
  number_tables: true
  number_figures: true
citation:
  style: apa
analysis:
  runner: none
literature:
  chroma_collection: flood_papers_768d_v2
languages:
- en
- uk
never_deliver:
- _work/**
```

### `kakhovka-terrain:paper2` — `case_studies/kakhovka/ghai.project.yaml`

```yaml
# Paper 2 — bed DEM, terrain and roughness; also the vegetation/roughness paper: how the GeoHydroAI paper workbench builds this paper.
# Schema: docs/api/PROJECT_MANIFEST.md of the knowledge repository. Paths are relative to the
# repository root. No keys or secrets belong here: the repository may be public.
schema: ghai.project/v1
project_id: kakhovka-terrain:paper2
title: Paper 2 — bed DEM, terrain and roughness; also the vegetation/roughness paper
repo:
  distro: Ubuntu-24.04
  path: /home/niko/repo/kakhovka-terrain
  public: true
publication_dir: case_studies/kakhovka/publication
paths:
  manuscript_templates: case_studies/kakhovka/publication/templates/
  manuscript_out: case_studies/kakhovka/publication/manuscript_draft.md
  theses: case_studies/kakhovka/publication/literature/theses.json
  atomic_claims: case_studies/kakhovka/publication/literature/atomic_claims.yaml
  bib: case_studies/kakhovka/publication/literature/references_p74.bib
  tables: case_studies/kakhovka/publication/tables/
  figures: case_studies/kakhovka/publication/figures/
  captions: case_studies/kakhovka/publication/captions.md
  passport: case_studies/kakhovka/publication/PASSPORT.md
placeholders:
  dialect: ghai
assembly:
  sections: {}
  number_tables: true
  number_figures: true
citation:
  style: apa
analysis:
  runner: none
literature:
  chroma_collection: flood_papers_768d_v2
languages:
- en
never_deliver:
- _work/**
```

### `article1` — `articles/flood_mapping_methods_review/ghai.project.yaml`

```yaml
# Article 1 — flood-mapping methods review: how the GeoHydroAI paper workbench builds this paper.
# Schema: docs/api/PROJECT_MANIFEST.md of the knowledge repository. Paths are relative to the
# repository root. No keys or secrets belong here: the repository may be public.
schema: ghai.project/v1
project_id: article1
title: Article 1 — flood-mapping methods review
repo:
  distro: Ubuntu-24.04
  path: /home/niko/repo/floodstate-eo
  public: true
publication_dir: articles/flood_mapping_methods_review
paths:
  manuscript_out: articles/flood_mapping_methods_review/manuscript/Article_1_Flood_Mapping_Methods_V3.md
  theses: articles/flood_mapping_methods_review/literature/theses_v4.json
  tables: articles/flood_mapping_methods_review/publication/tables/
  figures: articles/flood_mapping_methods_review/publication/figures/
  reviews: articles/flood_mapping_methods_review/reviews/
  passport: articles/flood_mapping_methods_review/PASSPORT.md
placeholders:
  dialect: none
assembly:
  sections: {}
  number_tables: true
  number_figures: true
citation:
  style: apa
analysis:
  runner: none
literature:
  chroma_collection: flood_papers_768d_v2
languages:
- en
never_deliver:
- _work/**
```


## Review rules (`ghai.review_rules/v1`)

```yaml
schema: ghai.review_rules/v1
files:   {manuscript: {path: …/manuscript.md, label: manuscript.md}, …}
tables:  {T12: {path: …/tables/T12.csv, label: tables/T12.csv}}
rules:
  - {id: C, type: forbidden, in: [manuscript, captions], major_in: [manuscript], phrases_from: {path: tests/test_terminology_freeze.py, variable: FORBIDDEN}, observed: "forbidden phrase '{phrase}'"}
  - {id: E, type: near, in: manuscript, pattern: '7 June', window: 160, present: ['maximum|peak'], missing: ['reconstruct'], severity: MAJOR, observed: "…"}
  - {id: A, type: table_interval, table: T12, row: [{column: region, contains: CORRIDOR}], quantities: [{name: A_new, unit: km², columns: [a_]}], …}
```

Rule types: `forbidden`, `pattern`, `near`, `requirements`, `co_occurrence`, `variants`, `absent`, `table_interval`, `table_rows`, `table_text`. Messages are templates (`{match}`, `{phrase}`, `{central:.0f}`, `{row[column]}` …). floodstate-eo Paper 3 keeps checks A–H of its 2026-09-28 audit this way; they reproduce that audit's findings exactly.
