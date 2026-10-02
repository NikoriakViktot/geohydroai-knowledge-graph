"""Import the research data of the per-paper folders into the layer of truth.

    python -m src.etl.paper_folders --dry-run     # parse + validate, print the report
    python -m src.etl.paper_folders               # load (one transaction, provenance in ops.run)

The files are read from the P6 freeze (data/frozen/paper_folders_20261002), the stable copy
once the folders are delivered to their repositories and removed. GHAI_PAPER_FOLDERS_ROOT
overrides it, e.g. ``GHAI_PAPER_FOLDERS_ROOT=.`` for the live tree. Source paths stay
repository-relative, so ops.source_file rows are comparable across roots.

Sources and namespaces follow the inventory of 2026-10-02 and the author's decisions
(API_PLAN_v1/11 §2.4). Every row is validated against src/contracts/research.py;
rejected rows are reported, never coerced. Labels carry who made them: there are no
human relevance labels in these folders (the "reviewed" screening sheet was labelled
by ChatGPT, the "human_verified" numbers by Claude).

Re-running is idempotent: rows that came from a file are replaced by that file's rows.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import logging
import math
import os
import re
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

import yaml
from pydantic import ValidationError

from src.contracts import research as C
from src.extraction.numbers import parse_number
from src.services.identity import normalize_doi

log = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[2]


def _source_root() -> Path:
    from src.workbench.decommission import DEFAULT_NAME, FROZEN_DIR
    raw = os.getenv("GHAI_PAPER_FOLDERS_ROOT")
    if not raw:
        return FROZEN_DIR / DEFAULT_NAME
    path = Path(raw)
    return path if path.is_absolute() else (ROOT / path).resolve()


SOURCE_ROOT = _source_root()

FS3 = "floodstate-eo:paper3"
KT2 = "kakhovka-terrain:paper2"
KR1 = "kakhovka-report:v1"
ART1 = "article1"

PROJECTS = {
    FS3: ("floodstate-eo", "Paper 3 — daily inundation of the Kakhovka breach (U-Net)"),
    KT2: ("kakhovka-terrain", "Paper 2 — bed DEM, terrain and roughness; also the vegetation/roughness paper"),
    "swot-dnipro:paper1": ("SWOT-DNIPRO", "Paper 1 — water-surface geometry of the former reservoir"),
    KR1: ("knowledge-graph (archived)", "Earlier Kakhovka scientific report (src/paper_3, data/paper_3_audit)"),
    ART1: ("floodstate-eo", "Article 1 — flood-mapping methods review (articles/flood_mapping_methods_review)"),
}

LA = "paper_unet-case-kakhovka/literature_audit_paper3"
PUB = "paper_unet-case-kakhovka/publication"
P2 = "paper_terrain-case-kakhovka/publication"
D3 = "data/paper_3_audit"
R3 = "paper_3_audit"


# ── helpers ────────────────────────────────────────────────────────────────────

@dataclass
class Batch:
    table: str                      # model class name in src.db.models
    source: str                     # repository-relative path, read under SOURCE_ROOT
    rows: list[dict] = field(default_factory=list)
    rejects: list[dict] = field(default_factory=list)
    children: list[tuple[str, list[dict]]] = field(default_factory=list)   # (table, rows) e.g. thesis refs
    notes: list[str] = field(default_factory=list)                         # accepted legacy encodings


def _p(rel: str) -> Path:
    return SOURCE_ROOT / rel


def _blank(v) -> bool:
    return v is None or (isinstance(v, float) and math.isnan(v)) or (isinstance(v, str) and not v.strip())


def _s(v) -> str | None:
    return None if _blank(v) else str(v).strip()


def _bool(v) -> bool | None:
    if _blank(v):
        return None
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in ("true", "1", "yes", "y")


def _float(v) -> float | None:
    if _blank(v):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        try:
            return parse_number(str(v))
        except ValueError:
            return None


def labeler_from(text: str | None, default_kind: str = "unknown", fallback: str = "unattributed") -> C.Labeler:
    """'Claude (Fable 5.1) for …' → model claude-fable-5.1; 'gemini-…' / 'ollama:…' / 'ChatGPT' → model."""
    t = (text or "").strip()
    m = re.search(r"claude\s*\(([^)]+)\)", t, re.I)
    if m:
        return C.Labeler(labeler_kind="model", labeler="claude-" + re.sub(r"\s+", "-", m.group(1).strip().lower()))
    low = t.lower()
    if low.startswith("gemini"):
        return C.Labeler(labeler_kind="model", labeler=t)
    if low.startswith("ollama:"):
        return C.Labeler(labeler_kind="model", labeler=t.split(":", 1)[1])
    if "chatgpt" in low or "gpt-" in low:
        return C.Labeler(labeler_kind="model", labeler="chatgpt")
    return C.Labeler(labeler_kind=default_kind, labeler=t or fallback)


_STATUS = {"verified": "verified", "verify": "to_verify", "missing (search)": "missing", "missing": "missing",
           "in bib, no status note": "unknown"}


def ref_status(raw: str | None) -> str:
    return _STATUS.get((raw or "").strip().lower(), "unknown")


def _validate(model, data: dict, batch: Batch, ref: str) -> dict | None:
    try:
        return model(**data).model_dump()
    except ValidationError as exc:
        batch.rejects.append({"ref": ref, "errors": [f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()][:5]})
        return None


def _thesis_row(obj: dict) -> tuple[dict, list[dict]]:
    refs = obj.pop("refs")
    authored = obj.pop("authored_by")
    row = dict(obj, labeler_kind=authored["labeler_kind"], labeler=authored["labeler"])
    children = [dict(project_id=obj["project_id"], thesis_id=obj["thesis_id"], **r) for r in refs]
    return row, children


def _merge_refs(refs: list[dict]) -> list[dict]:
    """The same (key, relation) can be listed twice; keep the most informative status."""
    rank = {"verified": 3, "to_verify": 2, "missing": 1, "unknown": 0}
    out: dict[tuple, dict] = {}
    for r in refs:
        k = (r["key"], r["relation"])
        if k not in out or rank[r["status"]] > rank[out[k]["status"]]:
            out[k] = r
    return list(out.values())


# ── theses ─────────────────────────────────────────────────────────────────────

def bundle_theses(project_id: str, rel: str, authored: C.Labeler) -> Batch:
    """theses.json of an article bundle. Strict: refs must be a list of objects and
    search_queries a list of strings — or, for the CSV-dump variant, parse the
    documented grammars 'Key[RELATION:status]; …' and 'q1 | q2' exactly."""
    b = Batch("Thesis", rel)
    for t in json.loads(_p(rel).read_text(encoding="utf-8")):
        refs = t.get("refs") or []
        if isinstance(refs, str):
            parsed = []
            for chunk in [c.strip() for c in refs.split(";") if c.strip()]:
                m = re.fullmatch(r"(?P<key>[^\[\]]+)\[(?P<rel>[A-Z_]+):(?P<st>[^\]]*)\]", chunk)
                if not m:
                    b.rejects.append({"ref": t.get("id"), "errors": [f"refs: cannot parse {chunk!r}"]})
                    parsed = None
                    break
                parsed.append({"key": m["key"].strip(), "relation": m["rel"], "status": ref_status(m["st"]), "status_raw": m["st"]})
            if parsed is None:
                continue
            refs = parsed
        else:
            refs = [{"key": r["key"], "relation": r["relation"], "status": ref_status(r.get("status")),
                     "status_raw": r.get("status")} for r in refs]
        queries = t.get("search_queries") or []
        if isinstance(queries, str):
            queries = [q.strip() for q in queries.split("|") if q.strip()]
        tables = t.get("tables") or []
        if isinstance(tables, str):
            tables = [x.strip() for x in re.split(r"[;,]", tables) if x.strip()]
        data = {"project_id": project_id, "thesis_id": t["id"], "kind": "literature", "statement": t.get("thesis", ""),
                "section": _s(t.get("section")), "category": _s(t.get("category")),
                "priority": _s(t.get("priority")), "quantitative": _s(t.get("quantitative")),
                "tables": tables, "needs": _s(t.get("needs")), "refs": _merge_refs(refs),
                "search_queries": queries, "authored_by": authored.model_dump()}
        ok = _validate(C.Thesis, data, b, t.get("id"))
        if ok:
            row, children = _thesis_row(ok)
            b.rows.append(row)
            b.children.append(("ThesisRef", children))
    return b


def supplement_theses(project_id: str, rel: str, authored: C.Labeler) -> Batch:
    b = Batch("Thesis", rel)
    for t in yaml.safe_load(_p(rel).read_text(encoding="utf-8"))["theses"]:
        refs = [{"key": r["key"], "relation": r["relation"], "status": ref_status(r.get("status")),
                 "status_raw": r.get("status")} for r in (t.get("refs") or [])]
        data = {"project_id": project_id, "thesis_id": t["id"], "kind": "literature",
                "statement": t.get("thesis") or t.get("statement", ""), "section": _s(t.get("section")),
                "category": _s(t.get("category")), "priority": _s(t.get("priority")),
                "needs": _s(t.get("needs")), "refs": _merge_refs(refs),
                "search_queries": list(t.get("search_queries") or []), "authored_by": authored.model_dump()}
        ok = _validate(C.Thesis, data, b, t["id"])
        if ok:
            row, children = _thesis_row(ok)
            b.rows.append(row)
            b.children.append(("ThesisRef", children))
    return b


def report_theses(rel: str = "src/paper_3/theses.yaml") -> tuple[Batch, Batch]:
    """The Kakhovka report's 36 theses and their positive controls."""
    tb, cb = Batch("Thesis", rel), Batch("PositiveControl", rel)
    authored = C.Labeler(labeler_kind="unknown", labeler="src/paper_3/theses.yaml (authorship not recorded)")
    for t in yaml.safe_load(_p(rel).read_text(encoding="utf-8")):
        extra = {k: t.get(k) for k in ("block", "rationale", "openalex_queries", "key_terms", "negative_terms",
                                       "expected_relations", "manuscript_anchor", "numeric_anchor") if t.get(k) is not None}
        data = {"project_id": KR1, "thesis_id": t["id"], "kind": "report", "statement": t.get("statement", ""),
                "search_queries": list(t.get("search_queries") or []), "extra": extra, "authored_by": authored.model_dump()}
        ok = _validate(C.Thesis, data, tb, t["id"])
        if ok:
            row, _ = _thesis_row(ok)
            tb.rows.append(row)
        for pc in t.get("positive_controls") or []:
            doi = normalize_doi(pc.get("doi"))
            if not doi:
                cb.rejects.append({"ref": f"{t['id']}/{pc.get('doi')}", "errors": ["doi: not a DOI"]})
                continue
            evidence = {k: pc.get(k) for k in ("quote", "section", "note") if pc.get(k) is not None}
            cdata = {"project_id": KR1, "thesis_id": t["id"], "doi": doi, "relevance": _s(pc.get("relevance")),
                     "expected_stage": _s(pc.get("expected_stage")), "difficulty": _s(pc.get("difficulty")),
                     "source_of_seed": _s(pc.get("source_of_seed")), "manually_checked": _bool(pc.get("manually_checked")),
                     "evidence": evidence}
            okc = _validate(C.PositiveControl, cdata, cb, f"{t['id']}/{doi}")
            if okc:
                cb.rows.append(okc)
    return tb, cb


def article_theses(rel: str = "paper_my/theses_v4.json") -> Batch:
    b = Batch("Thesis", rel)
    authored = C.Labeler(labeler_kind="unknown", labeler="paper_my/gen_theses_v4.py (authorship not recorded)")
    for t in json.loads(_p(rel).read_text(encoding="utf-8")):
        refs = [{"key": str(k), "relation": "SUPPORTED_BY", "status": "unknown", "status_raw": None}
                for k in (t.get("draft_refs") or []) if str(k).strip()]
        extra = {k: t.get(k) for k in ("type", "evidence_type", "paradigm", "keywords", "metric_ids", "sensor_ids",
                                       "claimed_value", "display_no", "section_id", "section_label", "sources_needed")
                 if t.get(k) not in (None, "", [])}
        data = {"project_id": ART1, "thesis_id": str(t["id"]), "kind": "article", "statement": t.get("claim", ""),
                "section": _s(t.get("section")), "refs": _merge_refs(refs), "extra": extra,
                "authored_by": authored.model_dump()}
        ok = _validate(C.Thesis, data, b, str(t["id"]))
        if ok:
            row, children = _thesis_row(ok)
            b.rows.append(row)
            b.children.append(("ThesisRef", children))
    return b


_LIST_FIELDS = ("required_roles", "key_terms_primary", "key_terms_support", "negative_terms",
                "extra_queries", "counterevidence_queries")


def _legacy_list(value):
    """Lists written as Python-literal strings ("['A', 'B']") by the Paper 2 bundle.
    Only a string that parses to a list is accepted; anything else stays as is and fails
    the contract. Returns (value, was_legacy)."""
    import ast
    if isinstance(value, str) and value.strip().startswith("["):
        try:
            parsed = ast.literal_eval(value)
        except (ValueError, SyntaxError):
            return value, False
        if isinstance(parsed, list):
            return parsed, True
    return value, False


def atomic_claims(project_id: str, rel: str) -> Batch:
    b = Batch("AtomicClaim", rel)
    doc = yaml.safe_load(_p(rel).read_text(encoding="utf-8"))
    authored = labeler_from(doc.get("authored_by"), fallback=f"{rel} (authorship not recorded)")
    for c in doc["claims"]:
        lists, legacy = {}, []
        for f in _LIST_FIELDS:
            value, was_legacy = _legacy_list(c.get(f) or [])
            lists[f] = value
            if was_legacy:
                legacy.append(f)
        data = {"project_id": project_id, "atomic_id": c.get("atomic_id", ""), "thesis_id": c.get("thesis_id", ""),
                "statement": c.get("statement") or "", "manuscript_relevance": c.get("manuscript_relevance"),
                **lists, "queries_version": _s(doc.get("queries_version")), "authored_by": authored.model_dump()}
        ok = _validate(C.AtomicClaim, data, b, c.get("atomic_id") or "?")
        if ok:
            a = ok.pop("authored_by")
            b.rows.append(dict(ok, labeler_kind=a["labeler_kind"], labeler=a["labeler"]))
            if legacy:
                b.notes.append(f"{c['atomic_id']}: {', '.join(legacy)} were Python-literal strings")
    return b


# ── bibliography ───────────────────────────────────────────────────────────────

def citation_keys(rel: str = f"{LA}/citation_keys.yaml") -> Batch:
    b = Batch("CiteKey", rel)
    for key, value in (yaml.safe_load(_p(rel).read_text(encoding="utf-8")) or {}).items():
        if isinstance(value, dict):
            pid, doi = _s(value.get("paper_id")), normalize_doi(value.get("doi"))
        else:
            pid, doi = _s(value), None
        b.rows.append({"project_id": FS3, "key": key, "doi": doi, "paper_id": pid, "extra": {}})
    return b


def p2_cite_keys(rel: str = f"{P2}/literature/references_p74_status.csv") -> Batch:
    b = Batch("CiteKey", rel)
    for r in csv.DictReader(_p(rel).open(encoding="utf-8")):
        b.rows.append({"project_id": KT2, "key": r["key"], "doi": normalize_doi(r.get("doi")), "paper_id": None,
                       "extra": {k: r[k] for k in ("role", "corpus_status", "metadata") if _s(r.get(k))}})
    return b


def references_verified(rel: str = f"{LA}/_work/references_verified.csv") -> Batch:
    """Verification by tools/paper3_audit/references.verify_all (rule-based registry check)."""
    b = Batch("BibVerification", rel)
    labeler = C.Labeler(labeler_kind="rule", labeler="tools/paper3_audit/references.verify_all")
    for i, r in enumerate(csv.DictReader(_p(rel).open(encoding="utf-8"))):
        status = r["crossref_status"]
        if status == "resolved" and r.get("year_match") == "True":
            verdict = "verified_with_notes" if _s(r.get("flags")) else "verified"
        elif status == "resolved":
            verdict = "mismatch"
        else:
            verdict = "unresolved"
        details = {k: r[k] for k in ("crossref_title", "crossref_year", "crossref_journal", "crossref_volume",
                                     "crossref_pages", "title_ratio", "openalex_status", "openalex_id",
                                     "resolution", "flags", "url_status") if _s(r.get(k))}
        data = {"project_id": FS3, "cite_key": r["bib_key"], "doi": normalize_doi(r.get("doi")), "verdict": verdict,
                "checked_against": "registry_mix", "details": details,
                "checked_at": _s(r.get("verified_on")), "labeler": labeler.model_dump()}
        ok = _validate(C.BibVerification, data, b, r["bib_key"])
        if ok:
            lab = ok.pop("labeler")
            b.rows.append(dict(ok, labeler_kind=lab["labeler_kind"], labeler=lab["labeler"]))
    return b


def report_references(rel: str = f"{D3}/REFERENCES.csv") -> tuple[Batch, Batch]:
    keys, ver = Batch("CiteKey", rel), Batch("BibVerification", rel)
    labeler = C.Labeler(labeler_kind="rule", labeler="src/paper_3/v2/reference_registry")
    for r in csv.DictReader(_p(rel).open(encoding="utf-8")):
        doi = normalize_doi(r.get("doi"))
        keys.rows.append({"project_id": KR1, "key": r["cite_key"], "doi": doi, "paper_id": None,
                          "extra": {k: r[k] for k in ("cite_as", "title", "authors", "year", "journal", "url", "source")
                                    if _s(r.get(k))}})
        method = r.get("resolution_method", "")
        verdict = "verified" if r.get("resolved") == "True" else "unresolved"
        data = {"project_id": KR1, "cite_key": r["cite_key"], "doi": doi, "verdict": verdict,
                "checked_against": "url" if method.startswith("url") else "crossref",
                "details": {"resolution_method": method, "note": r.get("note") or None},
                "labeler": labeler.model_dump()}
        ok = _validate(C.BibVerification, data, ver, r["cite_key"])
        if ok:
            lab = ok.pop("labeler")
            ver.rows.append(dict(ok, labeler_kind=lab["labeler_kind"], labeler=lab["labeler"]))
    return keys, ver


def technical_sources(rel: str = "src/paper_3/briefs/technical_sources.yaml") -> Batch:
    b = Batch("TechnicalSource", rel)
    for t in yaml.safe_load(_p(rel).read_text(encoding="utf-8")):
        data = {"source_id": t["id"], "cite_as": t.get("cite_as") or t["id"], "source_type": t.get("source_type") or "unknown",
                "title": _s(t.get("title")), "identifier": _s(t.get("identifier")), "issuer": _s(t.get("issuer")),
                "url": _s(t.get("url")), "status": _s(t.get("status")),
                "extra": {k: t.get(k) for k in ("bears_on", "draft_citation", "note") if t.get(k) is not None}}
        ok = _validate(C.TechnicalSource, data, b, t["id"])
        if ok:
            b.rows.append(ok)
    return b


def method_references(project_id: str, rel: str) -> Batch:
    b = Batch("MethodReference", rel)
    if rel.endswith(".yaml"):
        items = [{"doi_or_key": normalize_doi(m.get("doi")) or str(m.get("doi")), "used_for": m.get("used_for") or "",
                  "note": _s(m.get("note")), "extra": {}} for m in yaml.safe_load(_p(rel).read_text(encoding="utf-8"))]
    else:
        items = [{"doi_or_key": normalize_doi(r.get("doi")) or r["key"], "used_for": r.get("used_for") or "",
                  "note": _s(r.get("verdict")),
                  "extra": {k: r[k] for k in ("key", "expected_title", "type", "cr_title", "cr_year", "title_ratio",
                                              "openalex_id", "in_corpus", "paper_id") if _s(r.get(k))}}
                 for r in csv.DictReader(_p(rel).open(encoding="utf-8"))]
    seen = set()
    for it in items:
        k = (it["doi_or_key"], it["used_for"])
        if k in seen or not it["used_for"]:
            b.rejects.append({"ref": str(k), "errors": ["duplicate or empty used_for"]})
            continue
        seen.add(k)
        b.rows.append(dict(it, project_id=project_id))
    return b


def manual_downloads(rel: str = f"{R3}/MANUAL_DOWNLOAD_LIST.csv") -> Batch:
    b = Batch("Acquisition", rel)
    for r in csv.DictReader(_p(rel).open(encoding="utf-8")):
        doi = normalize_doi(r.get("doi"))
        b.rows.append({"doi": doi, "paper_id": None, "project_id": KR1, "route": "legacy_unknown",
                       "status": "needs_manual", "url": _s(r.get("attempted_pdf_url")),
                       "failure_reason": _s(r.get("why_not_downloaded")),
                       "priority": int(r["priority"]) if _s(r.get("priority")) and r["priority"].isdigit() else None,
                       "extra": {k: r[k] for k in ("publisher_host", "title", "year", "journal", "slices", "theses",
                                                   "save_as") if _s(r.get(k))}})
    return b


# ── evidence ───────────────────────────────────────────────────────────────────

def _relevance_from_role(role: str | None) -> str:
    return "NOT_RELEVANT" if role == "NOT_RELEVANT" else ("RELEVANT" if role else "UNKNOWN")


def screen_judged(rel: str = f"{LA}/_work/screen_judged.parquet") -> Batch:
    import pandas as pd
    b = Batch("ScreeningLabel", rel)
    for i, r in pd.read_parquet(_p(rel)).iterrows():
        role = _s(r.get("role"))
        extra = {k: (None if _blank(r.get(k)) else (r.get(k).item() if hasattr(r.get(k), "item") else r.get(k)))
                 for k in ("lane", "evidence_level", "passage_id", "quote_similarity", "quote_section", "quote_page",
                           "quote_chunk_id", "is_own_result", "demotion_reason", "parse_ok", "title", "year")}
        data = {"project_id": FS3, "subject_kind": "atomic_claim", "subject_id": r["atomic_claim_id"],
                "paper_id": _s(r.get("paper_id")), "doi": normalize_doi(r.get("doi")), "role": role, "role_raw": role,
                "relevance": _relevance_from_role(role), "confidence": _float(r.get("confidence")),
                "quote": _s(r.get("evidence_quote")), "quote_verified": _bool(r.get("quote_verified")),
                "rationale": _s(r.get("rationale")), "prompt_sha256": _s(r.get("prompt_sha256")),
                "extra": {k: v for k, v in extra.items() if v is not None},
                "labeler": labeler_from(_s(r.get("model"))).model_dump()}
        ok = _validate(C.ScreeningLabel, data, b, f"row {i}")
        if ok:
            lab = ok.pop("labeler")
            b.rows.append(dict(ok, labeler_kind=lab["labeler_kind"], labeler=lab["labeler"], source_row=int(i)))
    return b


def report_relations(rel: str = f"{D3}/relations.parquet") -> Batch:
    import pandas as pd
    b = Batch("ScreeningLabel", rel)
    for i, r in pd.read_parquet(_p(rel)).iterrows():
        raw = _s(r.get("relation"))
        role = C.LEGACY_ROLES.get(raw, raw)
        data = {"project_id": KR1, "subject_kind": "thesis", "subject_id": r["thesis_id"],
                "paper_id": _s(r.get("paper_id")), "doi": normalize_doi(r.get("doi")), "role": role, "role_raw": raw,
                "relevance": _relevance_from_role(role), "confidence": _float(r.get("confidence")),
                "quote": _s(r.get("evidence_quote")), "quote_verified": _bool(r.get("quote_verified")),
                "rationale": _s(r.get("rationale")), "prompt_sha256": _s(r.get("prompt_sha256")),
                "extra": {k: (r.get(k).item() if hasattr(r.get(k), "item") else r.get(k))
                          for k in ("evidence_level", "system_class", "is_own_result", "override_applied",
                                    "override_note", "quote_source_authority", "title", "year")
                          if not _blank(r.get(k))},
                "labeler": labeler_from(_s(r.get("model"))).model_dump()}
        ok = _validate(C.ScreeningLabel, data, b, f"row {i}")
        if ok:
            lab = ok.pop("labeler")
            b.rows.append(dict(ok, labeler_kind=lab["labeler_kind"], labeler=lab["labeler"], source_row=int(i)))
    return b


def screening_sheet(rel: str = f"{R3}/SCREENING_SHEET_reviewed.csv") -> Batch:
    """Relevance verdicts for the geodesy brief. Labelled by ChatGPT (author, 2026-10-02)."""
    b = Batch("ScreeningLabel", rel)
    grade = {"usable": "RELEVANT", "marginal": "PARTIALLY_RELEVANT", "irrelevant": "NOT_RELEVANT"}
    for i, r in enumerate(csv.DictReader(_p(rel).open(encoding="utf-8"))):
        verdict = (r.get("manual_verdict") or "").strip().lower()
        data = {"project_id": KR1, "subject_kind": "query", "subject_id": "briefs/geodesy",
                "paper_id": _s(r.get("slug")), "doi": normalize_doi(r.get("doi")), "role": None, "role_raw": verdict or None,
                "relevance": grade.get(verdict, "UNKNOWN"), "rationale": _s(r.get("manual_note")),
                "extra": {k: r[k] for k in ("title", "source_type", "auto_admitted", "read_priority") if _s(r.get(k))},
                "labeler": C.Labeler(labeler_kind="model", labeler="chatgpt").model_dump()}
        ok = _validate(C.ScreeningLabel, data, b, f"row {i}")
        if ok:
            lab = ok.pop("labeler")
            b.rows.append(dict(ok, labeler_kind=lab["labeler_kind"], labeler=lab["labeler"], source_row=i))
    return b


def calibration_sheet(rel: str = f"{D3}/CALIBRATION_SHEET_R1_labelled.csv") -> Batch:
    """External model-assisted review (CALIBRATION_PROVENANCE.json says so explicitly)."""
    b = Batch("ScreeningLabel", rel)
    for i, r in enumerate(csv.DictReader(_p(rel).open(encoding="utf-8"))):
        label = (r.get("label") or "").strip().upper() or "UNKNOWN"
        data = {"project_id": KR1, "subject_kind": "thesis", "subject_id": r["thesis_id"],
                "paper_id": _s(r.get("paper_id")), "doi": normalize_doi(r.get("doi")), "role": None, "role_raw": label,
                "relevance": label if label in C.RELEVANCES else "UNKNOWN",
                "rationale": _s(r.get("short_justification")) or _s(r.get("note")),
                "extra": {k: r[k] for k in ("stratum", "rank", "evidence_direction", "paper_scope", "verified_title",
                                            "retrieval_origin", "row_id") if _s(r.get(k))},
                "labeler": C.Labeler(labeler_kind="model_assisted_external",
                                     labeler="external model-assisted review (CALIBRATION_PROVENANCE.json)").model_dump()}
        ok = _validate(C.ScreeningLabel, data, b, f"row {i}")
        if ok:
            lab = ok.pop("labeler")
            b.rows.append(dict(ok, labeler_kind=lab["labeler_kind"], labeler=lab["labeler"], source_row=i))
    return b


def thesis_evidence(rel: str = f"{LA}/02_thesis_evidence.csv") -> Batch:
    b = Batch("ClaimEvidence", rel)
    for i, r in enumerate(csv.DictReader(_p(rel).open(encoding="utf-8"))):
        role = _s(r.get("evidence_role"))
        if role and role not in C.EVIDENCE_ROLES:
            b.rejects.append({"ref": f"row {i}", "errors": [f"role {role!r} not in the vocabulary"]})
            continue
        b.rows.append({"project_id": FS3, "thesis_id": _s(r.get("thesis_id")), "atomic_id": _s(r.get("atomic_claim_id")),
                       "role": role, "status": r["status"], "paper_id": _s(r.get("paper_id")),
                       "cite_key": _s(r.get("citation_key")), "doi": normalize_doi(r.get("doi")),
                       "section": _s(r.get("section")), "page": _s(r.get("page")), "chunk_id": _s(r.get("chunk_id")),
                       "quote_verified": _bool(r.get("quote_verified")), "evidence_quote": _s(r.get("evidence_quote")),
                       "status_rule": _s(r.get("status_rule")), "source_row": i,
                       "extra": {k: r[k] for k in ("priority", "manuscript_section", "evidence_summary",
                                                   "supports_or_challenges", "confidence", "notes", "retrieval_route",
                                                   "lane", "override_applied", "title", "year", "journal") if _s(r.get(k))}})
    return b


def literature_numbers(rel: str = f"{LA}/kakhovka_numbers.csv") -> Batch:
    """Numbers read from papers. `human_verified=yes` rows were verified by Claude, so they are model-verified."""
    b = Batch("LiteratureNumber", rel)
    for r in csv.DictReader(_p(rel).open(encoding="utf-8")):
        verified = labeler_from(r.get("verified_by")) if _s(r.get("verified_by")) else None
        raw = _s(r.get("value")) or _s(r.get("raw_match")) or ""
        data = {"project_id": FS3, "number_id": r["number_id"], "paper_id": _s(r.get("paper_id")),
                "doi": normalize_doi(r.get("doi")), "cite_key": _s(r.get("citation_key")),
                "quantity_name": _s(r.get("quantity_name")), "value": _float(r.get("value")), "value_raw": raw,
                "unit": _s(r.get("unit")), "window_text": _s(r.get("window_text")), "page": _s(r.get("page")),
                "extra": {k: r[k] for k in ("aoi", "dates", "temporal_semantics", "reference_water", "sensor",
                                            "land_or_total", "section", "source", "note", "raw_match") if _s(r.get(k))}
                         | ({"human_verified_flag": r["human_verified"], "flag_note": "flag set although the verifier was a model"}
                            if _s(r.get("human_verified")) else {}),
                "verified_by": verified.model_dump() if verified else None}
        ok = _validate(C.LiteratureNumber, data, b, r["number_id"])
        if ok:
            v = ok.pop("verified_by")
            b.rows.append(dict(ok, verified_kind=v["labeler_kind"] if v else None, verified_by=v["labeler"] if v else None))
    return b


def novelty_verdicts(rel: str = f"{LA}/novelty_verdicts.yaml") -> Batch:
    b = Batch("NoveltyVerdict", rel)
    for v in yaml.safe_load(_p(rel).read_text(encoding="utf-8"))["verdicts"]:
        den = v.get("denominator")
        b.rows.append({"project_id": FS3, "question_id": v["nq_id"], "verdict": v["verdict"],
                       "denominator": den if isinstance(den, dict) else {"value": den},
                       "closest": v.get("closest") or [], "statement": _s(v.get("statement")),
                       "labeler_kind": "unknown", "labeler": "novelty_verdicts.yaml (authorship not recorded)"})
    return b


def overrides(rel: str = f"{LA}/overrides.yaml") -> Batch:
    b = Batch("Override", rel)
    for o in yaml.safe_load(_p(rel).read_text(encoding="utf-8"))["overrides"]:
        lab = labeler_from(o.get("read_by"))
        role = _s(o.get("role"))
        if role and role not in C.EVIDENCE_ROLES:
            b.rejects.append({"ref": f"{o.get('atomic_claim_id')}/{o.get('paper_id')}", "errors": [f"role {role!r}"]})
            continue
        d = o.get("date")
        b.rows.append({"project_id": FS3, "atomic_id": o["atomic_claim_id"], "paper_id": _s(o.get("paper_id")),
                       "role": role, "justification": o.get("justification") or "",
                       "decided_on": d if isinstance(d, date) else (date.fromisoformat(str(d)) if _s(d) else None),
                       "extra": {k: o[k] for k in ("manuscript_relevance", "status") if _s(o.get(k))},
                       "labeler_kind": lab.labeler_kind, "labeler": lab.labeler})
    return b


def open_citations(rel: str = f"{LA}/valid_artsclt/open_citations.json") -> Batch:
    b = Batch("CitationOccurrence", rel)
    for item in json.loads(_p(rel).read_text(encoding="utf-8"))["items"]:
        for c in item.get("citations") or []:
            data = {"project_id": FS3, "cite_key": item["key"], "section": _s(c.get("section")),
                    "sentence": c["sentence"], "quoted": list(c.get("quotations") or []),
                    "doi": normalize_doi(item.get("doi")),
                    "extra": {"what_to_verify": item.get("what_to_verify"), "status": item.get("status"),
                              "has_table_placeholders": c.get("has_table_placeholders")}}
            ok = _validate(C.CitationOccurrence, data, b, item["key"])
            if ok:
                b.rows.append(ok)
    return b


def citation_verification(rel: str = f"{LA}/valid_artsclt/citation_verification.md",
                          checked_on: date = date(2026, 10, 1)) -> Batch:
    """The manual verification session of 2026-10-01 (Claude Opus 5.5 reading the corpus TEI)."""
    b = Batch("QuoteCheck", rel)
    text = _p(rel).read_text(encoding="utf-8")
    table = text.split("| Reference | Manuscript use | Verdict |", 1)[1].split("\n\n", 1)[0]
    labeler = C.Labeler(labeler_kind="model", labeler="claude-opus-5.5")
    for line in table.splitlines()[2:]:
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 3:
            continue
        ref, use, verdict_text = cells[0], cells[1], cells[2]
        m = re.match(r"([A-Za-zÀ-ž'\-]+)(?: et al\.)? (\d{4})", ref)
        key = f"{m.group(1)}_{m.group(2)}" if m else ref
        up = verdict_text.upper()
        verdict = ("FIX" if "FIX" in up else "WORDING" if "WORDING" in up else
                   "OPEN" if "OPEN" in up else "VERIFIED")
        data = {"project_id": FS3, "cite_key": key, "claim_use": use or None, "verdict": verdict,
                "finding": re.sub(r"\*\*", "", verdict_text), "checked_on": checked_on, "labeler": labeler.model_dump()}
        ok = _validate(C.QuoteCheck, data, b, key)
        if ok:
            lab = ok.pop("labeler")
            b.rows.append(dict(ok, labeler_kind=lab["labeler_kind"], labeler=lab["labeler"]))
    return b


# ── assembly ───────────────────────────────────────────────────────────────────

def collect() -> list[Batch]:
    fs3_authored = C.Labeler(labeler_kind="import", labeler="floodstate-eo workflows/paper/p100_literature_theses.py")
    kt2_authored = C.Labeler(labeler_kind="import", labeler="kakhovka-terrain p72_publication_assemble.py (CSV dump)")
    rt, rc = report_theses()
    rk, rv = report_references()
    return [
        bundle_theses(FS3, f"{PUB}/literature/theses.json", fs3_authored),
        supplement_theses(FS3, f"{LA}/theses_supplement.yaml",
                          C.Labeler(labeler_kind="unknown", labeler="theses_supplement.yaml (authorship not recorded)")),
        atomic_claims(FS3, f"{LA}/atomic_claims.yaml"),
        citation_keys(), references_verified(), open_citations(), citation_verification(),
        screen_judged(), thesis_evidence(), literature_numbers(), novelty_verdicts(), overrides(),
        method_references(FS3, f"{LA}/07c_method_references.csv"),
        bundle_theses(KT2, f"{P2}/literature/theses.json", kt2_authored),
        atomic_claims(KT2, f"{P2}/literature/atomic_claims.yaml"),
        p2_cite_keys(),
        rt, rc, rk, rv, report_relations(), screening_sheet(), calibration_sheet(),
        technical_sources(), method_references(KR1, "src/paper_3/briefs/method_references.yaml"),
        manual_downloads(),
        article_theses(),
    ]


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def load(batches: list[Batch]) -> uuid.UUID:
    from sqlalchemy import delete, update
    from sqlalchemy.dialects.postgresql import insert

    from src.db import models as M
    from src.db.engine import session_scope
    from src.etl.identity import git_state

    commit, dirty = git_state()
    run_id = uuid.uuid4()
    with session_scope() as s:
        s.add(M.Run(run_id=run_id, kind="etl", name="paper_folders", status="running", git_commit=commit,
                    git_dirty=dirty, params={"sources": sorted({b.source for b in batches}),
                                             "source_root": _rel_root()}))
        s.flush()
        for pid, (repo, label) in PROJECTS.items():
            s.execute(insert(M.Project).values(project_id=pid, repo=repo, paper_label=label)
                      .on_conflict_do_update(index_elements=["project_id"], set_={"repo": repo, "paper_label": label}))
        counts: dict[str, int] = {}
        done_sources: set[tuple[str, str]] = set()
        for b in batches:
            path = _p(b.source)
            sha = _sha256(path)
            s.execute(insert(M.SourceFile).values(sha256=sha, path=b.source, run_id=run_id, size_bytes=path.stat().st_size)
                      .on_conflict_do_nothing())
            model = getattr(M, b.table)
            if (b.table, b.source) not in done_sources and hasattr(model, "source_path"):
                s.execute(delete(model).where(model.source_path == b.source))
                done_sources.add((b.table, b.source))
            prov = {"source_path": b.source, "source_sha256": sha}
            rows = [dict(r, **prov, run_id=run_id) for r in b.rows]
            pk = [c.name for c in model.__table__.primary_key.columns]
            serial = pk == ["id"]
            for i in range(0, len(rows), 500):
                chunk = rows[i:i + 500]
                stmt = insert(model).values(chunk)
                if not serial:
                    stmt = stmt.on_conflict_do_update(index_elements=pk,
                                                      set_={k: stmt.excluded[k] for k in chunk[0] if k not in pk})
                s.execute(stmt)
            counts[f"{b.table}:{b.source}"] = len(rows)
            for child_table, child_rows in b.children:
                if not child_rows:
                    continue
                child = getattr(M, child_table)
                for r in child_rows:
                    s.execute(delete(child).where(child.project_id == r["project_id"], child.thesis_id == r["thesis_id"]))
                s.execute(insert(child).values(child_rows).on_conflict_do_nothing())
                counts[f"{child_table}:{b.source}"] = counts.get(f"{child_table}:{b.source}", 0) + len(child_rows)
        s.execute(update(M.Run).where(M.Run.run_id == run_id).values(
            status="succeeded", finished_at=datetime.now(timezone.utc),
            counts={"rows": counts, "rejects": sum(len(b.rejects) for b in batches)}))
    return run_id


def _rel_root() -> str:
    try:
        return SOURCE_ROOT.relative_to(ROOT).as_posix() or "."
    except ValueError:
        return str(SOURCE_ROOT)


def report(batches: list[Batch]) -> str:
    lines = [f"{'table':18s} {'rows':>6s} {'rejects':>7s}  source"]
    for b in batches:
        n_children = sum(len(c) for _, c in b.children)
        extra = f" (+{n_children} refs)" if n_children else ""
        legacy = f" [{len(b.notes)} rows in a legacy encoding]" if b.notes else ""
        lines.append(f"{b.table:18s} {len(b.rows):6d} {len(b.rejects):7d}  {b.source}{extra}{legacy}")
        for r in b.rejects[:3]:
            lines.append(f"{'':27s}↳ {r['ref']}: {'; '.join(r['errors'])[:150]}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--report", type=Path, help="write rejects and counts as JSON")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    batches = collect()
    print(f"source root: {_rel_root()}")
    print(report(batches))
    if args.report:
        args.report.write_text(json.dumps([{"table": b.table, "source": b.source, "rows": len(b.rows),
                                            "rejects": b.rejects} for b in batches], indent=1, default=str),
                               encoding="utf-8")
    if args.dry_run:
        return 0
    print(f"loaded, run_id={load(batches)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
