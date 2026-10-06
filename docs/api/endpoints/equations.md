# Equations, quantities and laws — the equation-centric graph (read-only)

**Backing code**: `src/document/equation_records.py` (equations, parameters, PNG), `src/document/formula_structure.py` (text and structural hashes), `src/document/formula_algebra.py` (ALGEBRAIC equivalence), `src/document/formula_code.py` (Python/Julia), `src/ontology/quantities.py` (quantity concepts, dimensions), `src/ontology/laws.py` + `laws.yaml` (law registry, evidence scores). Plan: `docs_v2/EQUATION_KG_PLAN.md`.

**Backing data**: Neo4j `Equation`, `Parameter`, `Quantity`, `QuantityConcept`, `FormulaStructure`, `PhysicalLaw`; edges `HAS_EQUATION`, `HAS_PARAMETER`, `QUANTIFIES`, `NORMALIZED_TO`, `COMPUTES`, `HAS_STRUCTURE`, `ALGEBRAIC_EQUIVALENT`, `EQUATION_INSTANCE_OF`, `INVOLVES`, `RELATES_TO`, `DEFINES_METRIC`, `EQUATION_GROUNDS_TO`.
- 33,364 equations from GROBID TEI; 7–8 k have LaTeX from Nougat (page mode), the rest GROBID's plain text only (2026-10-05, before the end of the corpus Nougat run).
- Parameters come from the definition clause after the equation and from the paper's glossary; `quantity` is the surface name, `quantity_id` its concept (138 concepts, `quantities-v0.3`).
- About 58 % of LaTeX formulas parse into a structure; only those have a structural hash, equivalents and code.
- Law links: 21 laws (`laws-v0.3`); `accepted` (score ≥ 0.5) and `candidate` (≥ 0.3).

**Nothing here is a human label.** Extraction, quantity matching, equivalence verdicts and law links are machine outputs (R-SCI-6). The weights of the law score are provisional until the equation gold set calibrates them.

All endpoints: **Scope** `read` · **Mode** S. Neo4j reads run in a managed read transaction with a 10 s timeout.

---

## `GET /equations/search`
- **Status**: implemented (2026-10-06) · **Scope** `read` · S
- **Purpose**: find equations by what they compute or contain. Example: "formulas that use Manning's n" → `GET /equations/search?quantity=quantity.manning_n`; "papers that write NSE" → `?law=law.nse`.

| Query param | Type | Notes |
|---|---|---|
| `quantity` | string | a concept id (`quantity.discharge`) or a name ("water depth"), normalised; matches a non-stale parameter |
| `law` | string | `law.*` id; with `law_status=accepted` (default), `candidate` or `any` |
| `paper` | string | `paper_id` |
| `structural_hash` | string | every equation with this structure (EXACT equivalents) |
| `q` | string | case-insensitive text in the equation's purpose, lead-in sentence or section |
| `has_code` | bool | only equations whose structure has generated code |
| `limit` / `cursor` | int / string | 1–500, default 50 |

- **Response 200**: `{"items": [EquationSummary], "count", "next_cursor", "resolved": {"quantity_id"?, "quantity_method"?}, "provenance"}`.
  - `EquationSummary` = `{eq_id, paper_id, title, year, equation_number, page, latex_raw, canonical_expression, purpose, structure_status, laws: [{law_id, status, score}], matched_parameters: [{symbol, description, unit, quantity_id, dimension_check}]}`.
- **Errors**: `422` for an unknown quantity name or law id, or no filter at all; `503` (Neo4j unavailable).
- **Agent notes**:
  - A quantity filter finds equations whose **definition clause** names the quantity. An equation whose symbols are defined elsewhere in the paper may be missed: absence is not evidence (R-SCI-3).
  - Count papers, not equations, and state the denominator (R-SCI-7).

---

## `GET /equations/{eq_id}/chain`
- **Status**: implemented (2026-10-06) · **Scope** `read` · S
- **Purpose**: the provenance chain of one equation, from the PDF to the reported result: paper (page, bbox, PNG hash) → equation (raw LaTeX, hashes) → parameters → quantity concepts with dimensions → laws (with evidence components) → related Method/Metric → numeric facts the same paper reports for that metric (table, page, cell).
- **Response 200**: `{"equation", "paper", "parameters", "quantities": [{quantity_id, label, dimension, symbols}], "laws": [LawLink], "concepts": [{label, canonical_id, via}], "reported_values": [{fact_id, metric, value, unit, table_label, page, raw_cell, row_context, col_header}], "provenance"}`.
- **Errors**: `404 NOT_FOUND`; `503`.
- **Agent notes**:
  - `reported_values` are the paper's table facts for the metric the equation defines or the law relates to; they are values **reported in the same paper**, not computed with this equation. Quote them with their table and page (R-SCI-2).
  - A link with `status = candidate` is unconfirmed: say so, or leave it out.

---

## `GET /equations/{eq_id}`
- **Status**: implemented (2026-10-06) · **Scope** `read` · S
- **Purpose**: one equation with everything known about it.
- **Path**: `eq_id` = `paper_id:xml_id`, e.g. `10.5194_hess-17-837-2013:formula_3`.
- **Query**: `include=parameters,laws,equivalents,code` (default all).
- **Response 200**: `{"equation": {eq_id, paper_id, xml_id, equation_number, page, latex_raw, latex, text_grobid, formula_text_hash, formula_structural_hash, canonical_expression, structure_status, purpose, purpose_source, section, context_text, image_path, image_sha256}, "paper": {paper_id, doi, title, year}, "parameters": [Parameter]?, "computes": [{quantity, quantity_id, derivative}], "laws": [LawLink]?, "equivalents": {"exact": [{eq_id, paper_id}], "exact_count", "algebraic": [{structural_hash, canonical_expression, method, variable, mapping}]}?, "code": Code?, "provenance"}`.
  - `Parameter` = `{symbol, symbol_tex, description, unit, value, source, quantity, quantity_id, dimension_check}`; `dimension_check` ∈ `ok`, `ok_convention`, `mismatch`, `unknown`.
  - `LawLink` = `{law_id, name, status, score, variant, s_quantity, s_math, s_text, s_concept, math_method, mapping, capped}`.
  - `Code` = `{target, form, check, args, python, julia, python_annotated, julia_annotated}`; `*_annotated` carries a docstring for this equation (source, page, meaning, unit and quantity of every argument).
- **Errors**: `404 NOT_FOUND`; `400` for an unknown `include` value; `503`.
- **Agent notes**:
  - `latex_raw` is the extracted text, never replaced by the canonical form. Check a formula against `image_path` (the PNG of its GROBID box) before quoting it.
  - Generated code is a translation of the **extracted** formula: `check = passed` means the code agrees with SymPy, not that the extraction is right. Units are as written in the paper and are not converted.
  - `dimension_check = mismatch` flags an extraction or quantity-matching error as often as an error in the paper.

---

## `GET /quantities`
- **Status**: implemented (2026-10-06) · **Scope** `read` · S
- **Purpose**: the quantity concepts with their dimensions and how often parameters use them.
- **Query**: `q` (text in id, label or aliases), `kind` (`physical`, `statistical`, `model`, `mathematical`), `limit` (1–500, default 200).
- **Response 200**: `{"items": [{quantity_id, label, kind, dimension, alt_dimensions, typical_unit, aliases, parameters, equations}], "count", "ontology_version", "provenance"}`.

---

## `GET /quantities/{quantity_id}`
- **Status**: implemented (2026-10-06) · **Scope** `read` · S
- **Purpose**: one concept: dimension, aliases, the surface names that normalise to it (with method), the units seen with their dimension check, and the laws that involve it.
- **Response 200**: `{"quantity", "surface_names": [{name, method, score, parameters}], "units": [{unit, dimension_check, parameters}], "laws": [{law_id, name, symbol}], "counts": {"parameters", "equations", "papers"}, "provenance"}`.
- **Errors**: `404 NOT_FOUND`.

---

## `POST /quantities/normalize`
- **Status**: implemented (2026-10-06) · **Scope** `read` · S
- **Purpose**: map quantity names (and optional units) to concepts and check the unit's dimension. No database: the same rules and map the graph loader uses.
- **Body**: `{"items": [{"name": "observed channel top width", "unit": "m"}]}` (1–200 items).
- **Response 200**: `{"items": [{name, unit, quantity_id, label, method, score, qualifiers, dimension, unit_dimension, dimension_check}], "ontology_version", "provenance"}`; `method` ∈ `exact`, `stripped`, `head`, `embedding` (from the map), `unmatched`, `not_a_quantity`.

---

## `GET /laws`
- **Status**: implemented (2026-10-06) · **Scope** `read` · S
- **Purpose**: the law registry with the number of linked equations and papers.
- **Response 200**: `{"items": [{law_id, name, kind, reference, variants, accepted_equations, accepted_papers, candidate_equations}], "laws_version", "weights", "thresholds", "provenance"}`.

---

## `GET /laws/{law_id}`
- **Status**: implemented (2026-10-06) · **Scope** `read` · S
- **Purpose**: one law: reference forms and variants with verified Python and Julia code, its quantity concepts, related Method/Metric, and the equations linked to it with their evidence.
- **Query**: `status=accepted|candidate|any` (default `accepted`), `limit` (1–500, default 50), `cursor`.
- **Response 200**: `{"law": {law_id, name, kind, reference, text_cues}, "forms": [{latex, variant, code_python, code_julia, code_check}], "quantities": [{symbol, quantity_id}], "concepts": [canonical_id], "instances": [{eq_id, paper_id, title, year, page, latex_raw, LawLink…}], "count", "next_cursor", "provenance"}`.
- **Errors**: `404 NOT_FOUND`.
- **Agent notes**:
  - The reference code implements the **registry's** form; a paper's variant may differ (SCS-CN λ = 0.2 vs 0.05). Use the variant recorded on the link.
  - Instances are machine links (R-SCI-6). `s_math = 0.8` means "same shape under renaming", which needs the other channels to mean anything.
