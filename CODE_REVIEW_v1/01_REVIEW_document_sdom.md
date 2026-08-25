# Module Review 01 — `src/document/` (SDOM Core)

**Version**: 1.0 | **Date**: 2026-06-11
**Scope**: `src/document/` — 19 files, ~4,460 LOC. `TEIDocument`, `TEIParser`, `NougatParser`, `HybridParser`, `ParserRouter`, `LayoutAwareChunker`, quality assessment, SODB manifest.
**Verdict**: **GOOD design, one broken contract** — Score 8/10

---

## 1. What is done well

- **Single canonical domain object.** `TEIDocument` (models.py:235) is genuinely parser-agnostic; the docstring contract ("No code outside src/document/ should ever touch raw XML") is honored by consumers.
- **Frozen value objects.** 9 of 11 dataclasses in `models.py` are `@dataclass(frozen=True)` (lines 33, 43, 60, 120, 144, 158, 167, 183, 192) — `Author`, `Affiliation`, `Section`, `Figure`, `Table`, `Formula`, `Reference`, etc. are immutable as intended.
- **Internal indexes** (`_ref_index`, `_figure_index`, `_table_index`) rebuilt in `__post_init__` — O(1) reference resolution with a clean invariant.
- **Parser provenance model** (`ParserKind`, `ParserCapability`, `ParserProvenance`) is exactly the right abstraction for a multi-parser system; the rule "Nougat never claims COORDINATES" is enforceable through capabilities.
- **Graceful degradation**: router returns `_empty_doc()` instead of raising on parser failure, keeping batch pipelines alive.

---

## 2. Findings

### F-DOC-1 — `TEIDocument` is NOT frozen, contradicting the documented invariant (CRITICAL — contract)

**Evidence**:
- `CLAUDE.md` (Architectural Invariants): *"`TEIDocument` is frozen (`@dataclass(frozen=True)`) — never mutate after construction"*
- `src/document/models.py:235` — actual code:
  ```python
  @dataclass          # ← NOT frozen
  class TEIDocument:
  ```
- `src/document/parser_router.py:179-181` and `238-240` mutate the instance post-construction:
  ```python
  doc = self._get_tei().parse_text(grobid_xml, paper_id)
  doc.parser_kind         = ParserKind.GROBID
  doc.parser_capabilities = GROBID_CAPABILITIES
  doc.parser_provenance   = [ParserProvenance(...)]
  ```

**Impact**: This is not a runtime crash (the class is mutable, so assignment works), but it silently voids the project's strongest correctness guarantee. Any downstream consumer may now mutate a shared `TEIDocument`, and nothing will catch it. The documented architecture and the real architecture have diverged — for a codebase intended as a teaching reference, this is the most important fix.

**Recommendation** (pick one, consistently):
1. **Preferred**: make `TEIDocument` `frozen=True`; in `parser_router.py` use `dataclasses.replace(doc, parser_kind=..., parser_capabilities=..., parser_provenance=[...])`. Note `__post_init__`/`_rebuild_indexes` uses plain assignment to private fields — switch to `object.__setattr__` (standard frozen-dataclass idiom).
2. Fallback: keep it mutable but **fix the documentation** (CLAUDE.md, docstrings) and add a `freeze()`/copy-on-write discipline. Honest docs beat aspirational docs.

### F-DOC-2 — Broad `except Exception` in router fallback chain (MEDIUM)

`parser_router.py:186-188, 251-252` catch all exceptions from `parse_text` and convert to empty doc / hybrid fallback. A programming error (e.g., `AttributeError` from a refactor) is indistinguishable from a malformed-XML error. **Recommendation**: parsers should raise a typed `ParseError` family (a `NougatParseError` already exists — mirror it with `TEIParseError`), and the router should catch only those; let true bugs propagate.

### F-DOC-3 — `nougat_parser.py` holds 4 `except Exception` handlers and imports `fitz` (allowed) but duplicates page-render logic with `src/ingestion/pdf_reader.py` (MEDIUM)

The PDF-to-image rendering exists in both files. Consolidate into one internal helper inside `src/document/` so the "only parser.py and nougat_parser.py may import fitz" invariant has fewer surfaces to defend (see also [02_REVIEW_ingestion_extraction.md](02_REVIEW_ingestion_extraction.md) F-ING-1).

### F-DOC-4 — Mixed `print` + `logging` for status output (LOW)

`parser_router.py` and `nougat_parser.py` mix `log.info` with occasional `print`. Standardize on `logging`; reserve `print` for CLI entry points only.

---

## 3. Recommendations summary

| # | Action | Effort |
|---|--------|--------|
| 1 | Freeze `TEIDocument` + `dataclasses.replace()` in router (or fix docs) | 0.5–1 day incl. test run |
| 2 | Introduce `TEIParseError`; narrow router exception handling | 0.5 day |
| 3 | Deduplicate PDF rendering between `nougat_parser.py` and `ingestion/pdf_reader.py` | 1 day |
| 4 | Logging consistency | 0.5 day |

**Module score: 8/10** — the best-designed package in the repo; fixing F-DOC-1 would make it a genuine reference implementation.
