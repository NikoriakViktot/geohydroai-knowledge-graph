"""Validate a theses.json / atomic_claims.yaml document against contract v1, strictly.

Nothing is coerced: a CSV dump with `refs` as a "Key[REL:status]" string, or YAML lists
written as Python-literal strings, fail with the location of the problem. The only
accepted variants are the documented producer vocabularies that the import maps
one-to-one (e.g. the ref status "VERIFY" → to_verify, kept verbatim in status_raw).

Error locations use the document's own field names, e.g. ["theses", 0, "refs"].
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field

import yaml
from pydantic import ValidationError

from src.contracts import research as C

#: Documented ref-status vocabulary of the article bundles → contract RefStatus.
REF_STATUS = {"verified": "verified", "verify": "to_verify", "to_verify": "to_verify", "missing": "missing",
              "missing (search)": "missing", "in bib, no status note": "unknown", "unknown": "unknown"}
THESIS_FIELDS = {"id", "thesis", "statement", "kind", "section", "category", "priority", "quantitative", "tables",
                 "needs", "refs", "search_queries", "extra"}
CLAIM_FIELDS = {"atomic_id", "thesis_id", "statement", "required_roles", "manuscript_relevance", "key_terms_primary",
                "key_terms_support", "negative_terms", "extra_queries", "counterevidence_queries", "not_needed", "note"}
CLAIMS_TOP = {"claims", "authored_by", "queries_version", "novelty_questions"}
_LIST_FIELDS = ("required_roles", "key_terms_primary", "key_terms_support", "negative_terms", "extra_queries",
                "counterevidence_queries")
#: contract field → document field, for error locations
_DOC_NAME = {"thesis_id": "id", "statement": "thesis"}


@dataclass
class Report:
    errors: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    counts: Counter = field(default_factory=Counter)

    @property
    def valid(self) -> bool:
        return not self.errors

    def error(self, loc: list, msg: str, type_: str = "value_error") -> None:
        self.errors.append({"loc": [str(x) if not isinstance(x, int) else x for x in loc], "msg": msg, "type": type_})


def parse(document) -> object:
    """A parsed document, or JSON/YAML text. Raises ValueError for unparsable text."""
    if not isinstance(document, str):
        return document
    try:
        return json.loads(document)
    except json.JSONDecodeError:
        pass
    try:
        return yaml.safe_load(document)
    except yaml.YAMLError as exc:
        raise ValueError(f"neither JSON nor YAML: {exc}") from exc


def _pydantic_errors(exc: ValidationError, prefix: list, report: Report, names: dict[str, str]) -> None:
    for e in exc.errors():
        loc = list(e["loc"])
        if loc and loc[0] in names:
            loc[0] = names[loc[0]]
        report.error(prefix + loc, e["msg"], e["type"])


def _looks_like_python_list(value) -> bool:
    return isinstance(value, str) and value.strip().startswith("[") and value.strip().endswith("]")


def _list_of_strings(value, loc: list, report: Report) -> list[str] | None:
    if value is None:
        return []
    if _looks_like_python_list(value):
        report.error(loc, "a string that looks like a list: write a JSON/YAML list instead", "list_type")
        return None
    if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
        report.error(loc, "expected a list of strings", "list_type")
        return None
    return value


# ── theses ─────────────────────────────────────────────────────────────────────

def validate_theses(doc, project_id: str, authored_by: C.Labeler | None, kind: str = "literature") -> Report:
    report = Report()
    if isinstance(doc, dict) and "theses" in doc:
        unknown = set(doc) - {"theses", "authored_by"}
        for k in sorted(unknown):
            report.error([k], "unknown top-level field (allowed: theses, authored_by)", "extra_forbidden")
        if authored_by is None and doc.get("authored_by"):
            authored_by = _labeler(doc["authored_by"])
        doc = doc["theses"]
    if not isinstance(doc, list):
        report.error(["theses"], "expected a list of theses (or an object with a 'theses' list)", "list_type")
        return report
    if authored_by is None:
        report.warnings.append("authorship not recorded: give authored_by, or the import labels it 'unknown'")
        authored_by = C.Labeler(labeler_kind="unknown", labeler="not recorded")
    seen: Counter = Counter()
    for i, t in enumerate(doc):
        loc = ["theses", i]
        if not isinstance(t, dict):
            report.error(loc, "expected an object", "dict_type")
            continue
        for k in sorted(set(t) - THESIS_FIELDS):
            report.error(loc + [k], "unknown field; put producer-specific data under 'extra'", "extra_forbidden")
        refs = _refs(t.get("refs"), loc + ["refs"], report)
        queries = _list_of_strings(t.get("search_queries"), loc + ["search_queries"], report)
        tables = t.get("tables")
        if isinstance(tables, str) and not _looks_like_python_list(tables):
            # documented bundle encoding: "T16; T21" (or "") — unambiguous, split and reported
            tables = [x.strip() for x in re.split(r"[;,]", tables) if x.strip()]
            report.counts["tables_as_string"] += 1
        tables = _list_of_strings(tables, loc + ["tables"], report)
        if refs is None or queries is None or tables is None:
            continue
        data = {"project_id": project_id, "thesis_id": str(t.get("id") or ""), "kind": t.get("kind", kind),
                "statement": t.get("thesis") or t.get("statement") or "", "section": t.get("section"),
                "category": t.get("category"), "priority": t.get("priority"), "quantitative": t.get("quantitative"),
                "tables": tables, "needs": t.get("needs"), "refs": refs, "search_queries": queries,
                "extra": t.get("extra") or {}, "authored_by": authored_by.model_dump()}
        try:
            C.Thesis(**data)
        except ValidationError as exc:
            _pydantic_errors(exc, loc, report, {**_DOC_NAME, "statement": "thesis" if "thesis" in t else "statement"})
            continue
        seen[data["thesis_id"]] += 1
        report.counts["theses"] += 1
        report.counts["refs"] += len(refs)
        report.counts["search_queries"] += len(queries)
    for tid, n in seen.items():
        if n > 1:
            report.error(["theses"], f"thesis id {tid!r} occurs {n} times", "duplicate")
    if report.counts["tables_as_string"]:
        report.warnings.append(f"{report.counts['tables_as_string']} theses give 'tables' as a string; accepted "
                               "('T16; T21' → ['T16', 'T21']) but the contract form is a list")
    return report


def _refs(value, loc: list, report: Report) -> list[dict] | None:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(r, dict) for r in value):
        report.error(loc, "expected a list of objects {key, relation, status}", "list_type")
        return None
    out, ok = [], True
    for j, r in enumerate(value):
        for k in sorted(set(r) - {"key", "relation", "status"}):
            report.error(loc + [j, k], "unknown field (allowed: key, relation, status)", "extra_forbidden")
            ok = False
        raw = r.get("status")
        status = REF_STATUS.get(str(raw).strip().lower()) if raw is not None else "unknown"
        if status is None:
            report.error(loc + [j, "status"], f"unknown status {raw!r}; allowed: {sorted(REF_STATUS)}", "literal_error")
            ok = False
            continue
        out.append({"key": r.get("key"), "relation": r.get("relation"), "status": status, "status_raw": raw})
    if not ok:
        return None
    try:
        for j, r in enumerate(out):
            C.ThesisRef(**r)
    except ValidationError as exc:
        _pydantic_errors(exc, loc + [j], report, {})
        return None
    return out


# ── atomic claims ──────────────────────────────────────────────────────────────

def _labeler(raw) -> C.Labeler:
    if isinstance(raw, dict):
        return C.Labeler(**raw)
    from src.etl.paper_folders import labeler_from
    return labeler_from(str(raw))


def validate_atomic_claims(doc, project_id: str, authored_by: C.Labeler | None,
                           known_theses: set[str] | None = None) -> Report:
    report = Report()
    if not isinstance(doc, dict) or not isinstance(doc.get("claims"), list):
        report.error(["claims"], "expected an object with a 'claims' list", "dict_type")
        return report
    for k in sorted(set(doc) - CLAIMS_TOP):
        report.error([k], f"unknown top-level field (allowed: {sorted(CLAIMS_TOP)})", "extra_forbidden")
    if doc.get("novelty_questions") is not None:
        report.counts["novelty_questions"] = len(doc["novelty_questions"] or [])
        report.warnings.append("novelty_questions are counted but not validated by contract v1")
    if authored_by is None:
        if doc.get("authored_by"):
            try:
                authored_by = _labeler(doc["authored_by"])
            except ValidationError as exc:
                _pydantic_errors(exc, ["authored_by"], report, {})
                return report
        else:
            report.warnings.append("authorship not recorded: give authored_by, or the import labels it 'unknown'")
            authored_by = C.Labeler(labeler_kind="unknown", labeler="not recorded")
    seen: Counter = Counter()
    missing_theses: set[str] = set()
    for i, c in enumerate(doc["claims"]):
        loc = ["claims", i]
        if not isinstance(c, dict):
            report.error(loc, "expected an object", "dict_type")
            continue
        for k in sorted(set(c) - CLAIM_FIELDS):
            report.error(loc + [k], "unknown field", "extra_forbidden")
        if c.get("not_needed"):
            report.counts["not_needed"] += 1
            report.warnings.append(f"{c.get('atomic_id')}: not_needed, not imported")
            continue
        lists, bad = {}, False
        for f in _LIST_FIELDS:
            v = c.get(f)
            if _looks_like_python_list(v):
                report.error(loc + [f], "a string that looks like a list: write a YAML list instead", "list_type")
                bad = True
            lists[f] = v if v is not None else []
        if bad:
            continue
        data = {"project_id": project_id, "atomic_id": c.get("atomic_id") or "", "thesis_id": c.get("thesis_id") or "",
                "statement": c.get("statement") or "", "manuscript_relevance": c.get("manuscript_relevance"),
                **lists, "queries_version": doc.get("queries_version"), "authored_by": authored_by.model_dump()}
        try:
            C.AtomicClaim(**data)
        except ValidationError as exc:
            _pydantic_errors(exc, loc, report, {})
            continue
        seen[data["atomic_id"]] += 1
        report.counts["claims"] += 1
        if known_theses is not None and data["thesis_id"] not in known_theses:
            missing_theses.add(data["thesis_id"])
    for aid, n in seen.items():
        if n > 1:
            report.error(["claims"], f"atomic_id {aid!r} occurs {n} times", "duplicate")
    if missing_theses:
        report.warnings.append(f"thesis_id not among the project's theses: {sorted(missing_theses)}")
    return report


def project_theses(project_id: str) -> tuple[set[str] | None, str | None]:
    """Thesis ids of a project in project.thesis, and a note when they could not be read."""
    try:
        from sqlalchemy import select

        from src.db.engine import session_scope
        from src.db.models import Thesis
        with session_scope() as s:
            ids = set(s.scalars(select(Thesis.thesis_id).where(Thesis.project_id == project_id)))
    except Exception as exc:
        return None, f"thesis ids not checked against the project: postgres unavailable ({type(exc).__name__})"
    if not ids:
        return None, f"thesis ids not checked: project {project_id} has no theses in the layer of truth yet"
    return ids, None
