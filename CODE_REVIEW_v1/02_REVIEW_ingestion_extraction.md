# Module Review 02 — `src/ingestion/` + `src/extraction/` (Parsing & Scientific Extraction)

**Version**: 1.0 | **Date**: 2026-06-11
**Scope**: `src/ingestion/` (~40 files, 3,548 LOC core + stages + knowledge), `src/extraction/` (15 files, 5,770 LOC).
**Verdict**: **FAIR — functional, but carries the project's main scientific-validity risks** — Score 5.5/10

---

## 1. What is done well

- Stage 0/1/2 of the SDOM pipeline (`stage0/ingestor.py`, `stage1/parser_runner.py`, `stage2/engineer.py`) are cleanly separated, content-addressed (SHA-256), and well-tested (48 + 51 + 51 tests).
- `regex_extractor.py` has 95% type-hint coverage and well-organized pattern banks with named groups.
- The LLM judge prompt (`judge_stage.py:_build_prompt`) encodes genuinely good domain rules: study country must come from study area not author affiliation; rivers only if actual study watershed; the `accepted`/`corrected_value` consistency constraint (rule 4) is a smart self-check.
- The knowledge base (`src/ingestion/knowledge/`, 1,118 entities / 3,161 aliases) is JSON-driven, not hardcoded in Python.

---

## 2. Findings — Architectural invariant violations

### F-ING-1 — `lxml` and `fitz` imported outside the document layer (HIGH)

The stated invariants: *only `src/document/parser.py` may import lxml; only `parser.py` and `nougat_parser.py` may import fitz.* Verified violations:

| File | Line | Import | Why it matters |
|------|------|--------|----------------|
| `src/ingestion/tei_validator.py` | 15 | `from lxml import etree` | Second XML parsing surface; validator can drift from parser's interpretation of TEI |
| `src/ingestion/nougat_region_pipeline.py` | 54 | `from lxml import etree` | Re-parses TEI to get coordinates — duplicates `TEIParser` logic |
| `src/ingestion/nougat_region_pipeline.py` | 50 | `import fitz` | PDF cropping outside document layer |
| `src/ingestion/pdf_triage.py` | — | `import fitz` | PDF metadata probing |
| `src/ingestion/pdf_reader.py` | — | `import fitz` | Parallel PDF reader, duplicates nougat_parser rendering |

**Recommendation**: create `src/document/pdf_io.py` (page rendering, text probing, region cropping) and `src/document/tei_io.py` (validated TEI access for non-parser consumers). Migrate the 5 call sites; then enforce with a lint rule (e.g., a 10-line `tests/test_import_invariants.py` that greps imports — cheap and permanent).

### F-ING-2 — `nougat_region_pipeline.py` is a god file (1,083 LOC) mixing 4 concerns (MEDIUM)

TEI coordinate parsing + PDF cropping + Nougat dispatch + Parquet I/O in one module. Each is independently restartable per the project's own stage philosophy. Split into `region_extractor.py` (TEI→bbox), `crop_renderer.py` (PDF→PNG), `region_writer.py` (Parquet).

---

## 3. Findings — Scientific validity (the most important section)

### F-EXT-1 — NSE misclassified as a 0–1 normalised metric (CRITICAL — science)

`src/extraction/regex_extractor.py`, in `R2_PATTERNS`:

```python
# R² is always 0–1; reuse standard normalised extractor
R2_PATTERNS: list[re.Pattern] = [
    ...
    _r(rf"\bNSE\b{_SEP}{_NUM}"),   # Nash–Sutcliffe Efficiency (same 0–1 scale)
]
```

**This is scientifically wrong.** NSE ∈ (−∞, 1]. NSE < 0 means the model is worse than the mean of observations — a *meaningful, publishable* result that appears regularly in hydrological literature (and NSE between 0 and 0.5 is "unsatisfactory" per Moriasi et al. 2007). Routing NSE through a normalised [0–1] extractor:
1. Silently drops or rejects negative NSE values → **systematic optimism bias in the corpus**: the knowledge graph will over-represent "successful" models.
2. Conflates NSE with R² under one metric family, breaking downstream `metric_ontology` grouping.

Additionally, even R² is not always reported in [0,1] (adjusted R² can be negative), and **Cohen's Kappa** (`KAPPA_PATTERNS`) ∈ [−1, 1] — negative kappa (agreement worse than chance) would also be dropped by a 0–1 extractor.

**Recommendation**: give every metric an explicit valid range in `metric_ontology` (e.g., `NSE: (-inf, 1]`, `KGE: (-inf, 1]`, `kappa: [-1, 1]`, `R2: (-inf, 1]` to be safe, `IoU/F1/precision/recall: [0, 1]`), extract with a sign-aware `_NUM` pattern, and validate against the range instead of assuming normalisation. Flag out-of-range values as `suspect` rather than dropping.

### F-EXT-2 — Naive unit handling in RMSE/MAE extraction (HIGH — science)

```python
_UNIT = r"\s*(m(?:eters?)?|cm|mm|m\^?3\s*/\s*s|m3/s)?"
```

- Covers only m/cm/mm/m³/s. Missing: `km`, `ft`, `%`, `mm/day`, `m a.s.l.`, `cm/day`, unicode `m³/s` (the pattern `m\^?3` does not match `³`), `cms`/`cumecs`.
- The unit group is **optional**, so "RMSE = 0.45" (dimensionless? meters? normalized?) is stored without dimension — downstream `NumericFact` nodes mix incomparable quantities.
- No dimensional consistency check (RMSE of water depth in m vs discharge in m³/s are different physical quantities under one metric label).

**Recommendation**: (1) extend the unit vocabulary incl. unicode superscripts; (2) store `unit: None` explicitly as `dimensionless_or_unknown` and exclude unknown-unit facts from cross-paper numeric comparison; (3) attach the physical quantity (depth/discharge/extent) from sentence context or table header where available.

### F-EXT-3 — LLM judge has no ground truth; defensive normalisation hides model degradation (HIGH — science)

- `judge_stage.py` prompt rules are good, but **adherence is unmeasured**: there is no labeled evaluation set, so judge precision on `study_country`, `rivers`, `task` is unknown.
- `src/validation/judge_normalizer.py` silently repairs malformed LLM output (missing keys, wrong types). Repair is the right call for pipeline robustness, but **repair events are not aggregated** — if the Ollama model regresses and 40% of verdicts need repair, nobody notices.
- Judge failure is non-fatal and the paper is still marked SUCCESS (`process_paper.py`) — correct for throughput, but `provenance.judge_used=false` papers are not distinguishable in quality reporting.

**Recommendation**: (1) build a 50–100 paper gold set (stratified by study_type) and report judge precision/recall per field — this is also a prerequisite for the training dataset (see [10_TRAINING_DATASET_PLAN.md](10_TRAINING_DATASET_PLAN.md)); (2) log a `repair_count` metric per run; (3) add a dashboard panel for judge health.

### F-EXT-4 — Keyword banks duplicate the ontology (MEDIUM)

`regex_extractor.py` carries static lists (`_REVIEW_KW`, `_HYDRO_FORE_KW`, `_DL_KW`, `_SAR_SATELLITES`, `_METHODS_RULES`, …) while `src/ingestion/knowledge/` holds the canonical KB (1,118 entities, 3,161 aliases). Two sources of truth: adding a satellite to the KB does not update the regex bank. Known false-positive class: bare `"cnn"`, `"spot"`, `"planet"` substrings match non-entity text.

**Recommendation**: generate the keyword banks *from* the KB at load time (aliases → compiled patterns with word boundaries), keeping regex only for numeric/metric patterns that the KB cannot express.

### F-EXT-5 — Type-hint coverage gap in core extractors (MEDIUM)

`scientific_extractor.py` (944 LOC) and `section_extractor.py` (851 LOC) — roughly 61% of functions typed. These are the critical extraction path. Target 95%+ and add `mypy --strict` for `src/extraction/` to CI.

### F-EXT-6 — Hardcoded GeoNames username inside extraction logic (MEDIUM — security/config)

`src/ingestion/stages/geo_stage.py:329`:
```python
params = {"q": name, "maxRows": 5, "username": "viktornikoriak"}
```
Bypasses `settings.GEONAMES_USER` entirely. Same constant duplicated in `src/actors/geonames_actor.py:28`. Route both through `src/config/settings.py` (see [07](07_REVIEW_dashboard_misc.md) F-CFG-1).

---

## 4. Recommendations summary

| # | Action | Severity addressed | Effort |
|---|--------|--------------------|--------|
| 1 | Metric range model in `metric_ontology` (NSE/Kappa/KGE sign-aware) + re-extraction audit of existing corpus | CRITICAL | 2–3 days |
| 2 | Unit vocabulary + unknown-unit policy | HIGH | 1–2 days |
| 3 | Judge gold set (50–100 papers) + repair-rate telemetry | HIGH | 3–4 days |
| 4 | `pdf_io.py`/`tei_io.py` consolidation + import-invariant test | HIGH | 2 days |
| 5 | KB-generated keyword banks | MEDIUM | 2 days |
| 6 | Split `nougat_region_pipeline.py`; type hints in extractors | MEDIUM | 2–3 days |

**Module score: 5.5/10** — the extraction layer works, but its scientific guarantees are weaker than the rest of the architecture implies. Items 1–3 directly affect the trustworthiness of every downstream analytics product and the future training dataset.
