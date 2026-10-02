"""Theses (TH-*) and their hand-written atomic claims → ``src.paper_3.theses.Thesis``.

``atomic_claims.yaml`` is the single source of truth for what is searched: every
query variant, key-term family and counter-evidence query lives there, written by
a person after reading the thesis. The runner only validates and executes it.
"""
# Moved from tools/paper3_audit/claims.py (P6, 2026-10-02); run paths come from config.py (cfg).
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from src.workbench.literature.theses import Thesis
from src.workbench.literature.config import GENERIC_TERMS, PASSES, RELEVANCE, ROLES
from src.workbench.literature import config as cfg

_REF = re.compile(r"^\s*(\??)([^\[]+)\[([A-Z_]+):([^\]]*)\]\s*$")


@dataclass(frozen=True)
class ThesisRef:
    key: str
    relation: str          # SUPPORTED_BY | COMPARATOR | METHOD_FROM | NEEDS_SOURCE
    status: str            # verified | VERIFY | in bib, no status note | missing (search)
    placeholder: bool      # '?Key' — a source that does not exist yet


@dataclass(frozen=True)
class ThesisRow:
    id: str
    section: str
    category: str
    priority: str
    tables: tuple[str, ...]
    thesis: str
    needs: str
    refs: tuple[ThesisRef, ...]
    search_queries: tuple[str, ...]

    @property
    def pass_id(self) -> str:
        for k, v in PASSES.items():
            if v == self.priority:
                return k
        return "C"


@dataclass(frozen=True)
class AtomicClaim:
    thesis_id: str
    atomic_id: str
    statement: str
    required_roles: tuple[str, ...]
    key_terms: tuple[tuple[str, ...], ...]      # family 0 = distinguishing concept
    negative_terms: tuple[str, ...]
    queries: tuple[tuple[str, str], ...]        # (text, kind) kind ∈ original|extra|counter
    not_needed: bool = False
    manuscript_relevance: str = "SUPPORTING"
    note: str = ""
    thesis: ThesisRow | None = field(default=None, compare=False)

    @property
    def priority(self) -> str:
        return self.thesis.priority if self.thesis else "low"

    @property
    def pass_id(self) -> str:
        return self.thesis.pass_id if self.thesis else "C"

    @property
    def search_queries(self) -> tuple[str, ...]:
        return tuple(q for q, _ in self.queries)

    @property
    def counter_queries(self) -> tuple[str, ...]:
        return tuple(q for q, kind in self.queries if kind == "counter")

    def to_thesis(self) -> Thesis:
        return Thesis(
            id=self.atomic_id,
            block=self.thesis.section if self.thesis else "",
            statement=self.statement,
            rationale=self.thesis.needs if self.thesis else "",
            search_queries=self.search_queries,
            openalex_queries=(),
            key_terms=self.key_terms,
            negative_terms=self.negative_terms,
            positive_controls=(),
            expected_relations=self.required_roles,
            manuscript_anchor=self.thesis.section if self.thesis else "",
            numeric_anchor=None,
        )


def parse_ref(raw: str) -> ThesisRef | None:
    m = _REF.match(raw)
    if not m:
        return None
    placeholder, key, relation, status = m.groups()
    return ThesisRef(key=key.strip(), relation=relation.strip(), status=status.strip(),
                     placeholder=bool(placeholder))


def load_theses(path: Path = None, supplement: Path | None = None) -> list[ThesisRow]:
    """theses.json of the frozen bundle, plus theses_supplement.yaml of the audit
    (results added to the bundle after theses.csv was generated; same schema)."""
    if path is None:
        path = cfg.THESES_JSON
    data = json.loads(path.read_text(encoding="utf-8"))
    sup = supplement if supplement is not None else cfg.THESES_SUPPLEMENT
    if sup and Path(sup).exists():
        extra = yaml.safe_load(Path(sup).read_text(encoding="utf-8")) or {}
        known = {r["id"] for r in data}
        for rec in extra.get("theses", []):
            if rec["id"] in known:
                raise ValueError(f"supplement thesis {rec['id']} duplicates a bundle thesis")
            data.append(rec)
    rows = []
    for rec in data:
        refs = []
        for r in rec.get("refs") or []:
            if isinstance(r, dict):
                key = str(r.get("key", "")).strip()
                refs.append(ThesisRef(key=key.lstrip("?"), relation=str(r.get("relation", "")),
                                      status=str(r.get("status", "")), placeholder=key.startswith("?")))
            else:
                parsed = parse_ref(str(r))
                if parsed:
                    refs.append(parsed)
        tables = rec.get("tables") or ""
        if isinstance(tables, str):
            tables = tuple(t.strip() for t in tables.split(",") if t.strip())
        else:
            tables = tuple(tables)
        rows.append(ThesisRow(
            id=rec["id"], section=rec.get("section", ""), category=rec.get("category", ""),
            priority=rec.get("priority", "low"), tables=tables, thesis=rec.get("thesis", ""),
            needs=rec.get("needs", ""), refs=tuple(refs),
            search_queries=tuple(rec.get("search_queries") or []),
        ))
    return rows


def load_atomic_claims(path: Path = None,
                       theses: list[ThesisRow] | None = None
                       ) -> tuple[list[AtomicClaim], list[dict], dict]:
    """Returns (claims, novelty_questions, meta). Original thesis queries are always
    kept first; YAML variants are appended, never substituted."""
    if path is None:
        path = cfg.ATOMIC_CLAIMS_YAML
    doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    theses = theses if theses is not None else load_theses()
    by_id = {t.id: t for t in theses}
    claims = []
    for entry in doc.get("claims") or []:
        tid = entry["thesis_id"]
        trow = by_id.get(tid)
        queries: list[tuple[str, str]] = []
        seen = set()
        for kind, source in (("original", trow.search_queries if trow else ()),
                             ("extra", entry.get("extra_queries") or ()),
                             ("counter", entry.get("counterevidence_queries") or ())):
            for q in source:
                q = " ".join(str(q).split())
                if q and q not in seen:
                    seen.add(q)
                    queries.append((q, kind))
        primary = tuple(str(t) for t in entry.get("key_terms_primary") or ())
        support = tuple(tuple(str(t) for t in fam) for fam in entry.get("key_terms_support") or ())
        claims.append(AtomicClaim(
            thesis_id=tid, atomic_id=entry["atomic_id"],
            statement=" ".join(str(entry.get("statement", "")).split()),
            required_roles=tuple(entry.get("required_roles") or ()),
            key_terms=(primary, *support) if primary else support,
            negative_terms=tuple(str(t) for t in entry.get("negative_terms") or ()),
            queries=tuple(queries),
            not_needed=bool(entry.get("not_needed", False)),
            manuscript_relevance=str(entry.get("manuscript_relevance", "SUPPORTING")),
            note=str(entry.get("note", "")),
            thesis=trow,
        ))
    meta = {k: v for k, v in doc.items() if k not in ("claims", "novelty_questions")}
    return claims, list(doc.get("novelty_questions") or []), meta


def validate(theses: list[ThesisRow], claims: list[AtomicClaim]) -> list[str]:
    problems = []
    covered = {c.thesis_id for c in claims}
    for t in theses:
        if t.id not in covered:
            problems.append(f"{t.id}: no atomic claim")
    ids = [c.atomic_id for c in claims]
    for dup in {i for i in ids if ids.count(i) > 1}:
        problems.append(f"{dup}: duplicate atomic_id")
    for c in claims:
        if c.thesis is None:
            problems.append(f"{c.atomic_id}: unknown thesis_id {c.thesis_id}")
        if not c.atomic_id.startswith(c.thesis_id + "."):
            problems.append(f"{c.atomic_id}: id must start with '{c.thesis_id}.'")
        if c.not_needed:
            continue
        if not c.statement:
            problems.append(f"{c.atomic_id}: empty statement")
        bad_roles = [r for r in c.required_roles if r not in ROLES]
        if bad_roles or not c.required_roles:
            problems.append(f"{c.atomic_id}: required_roles invalid {bad_roles or '(empty)'}")
        if c.manuscript_relevance not in RELEVANCE:
            problems.append(f"{c.atomic_id}: manuscript_relevance {c.manuscript_relevance!r}")
        if not c.key_terms or not c.key_terms[0]:
            problems.append(f"{c.atomic_id}: key_terms_primary is empty")
        else:
            generic = [t for t in c.key_terms[0] if t.lower() in GENERIC_TERMS]
            if generic:
                problems.append(f"{c.atomic_id}: generic primary key term(s) {generic}")
        if len(c.key_terms) < 2:
            problems.append(f"{c.atomic_id}: needs >= 1 support family")
        if len(c.search_queries) < 2:
            problems.append(f"{c.atomic_id}: needs >= 2 queries")
        # Counter-evidence is mandatory for high-priority claims that assert a FINDING
        # (role SUPPORTS); a pure method/definition/dataset claim has nothing to contradict.
        if c.priority == "high" and "SUPPORTS" in c.required_roles and not c.counter_queries:
            problems.append(f"{c.atomic_id}: high-priority SUPPORTS claim needs counterevidence_queries")
    return problems


def queries_sha256(claims: list[AtomicClaim]) -> str:
    payload = sorted((c.atomic_id, q, kind) for c in claims for q, kind in c.queries)
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False).encode("utf-8")).hexdigest()


def by_pass(claims: list[AtomicClaim], pass_id: str | None) -> list[AtomicClaim]:
    if not pass_id:
        return list(claims)
    return [c for c in claims if c.pass_id == pass_id.upper()]
