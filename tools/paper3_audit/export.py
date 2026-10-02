"""Deliverables 02 / 03 / 05 and the evidence status of every atomic claim and thesis.

Status is assigned by rule (recorded as ``status_rule``) from the screened, quote-
verified rows, then human overrides (overrides.yaml) are applied LAST and marked.
"""
from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path

import pandas as pd
import yaml

from tools.paper3_audit import corpus as corpus_mod
from tools.paper3_audit import manifest
from tools.paper3_audit.bibtex import load_bib_entries
from tools.paper3_audit.claims import AtomicClaim, ThesisRow
from tools.paper3_audit.config import (DELIVERABLES, NOT_RELEVANT, OUT_DIR, OVERRIDES_YAML,
                                       QUOTE_REQUIRED, RELEVANCE, STATUSES, WORK_DIR)

logger = logging.getLogger(__name__)

EVIDENCE_COLUMNS = [
    "thesis_id", "atomic_claim_id", "priority", "manuscript_section", "evidence_role", "status",
    "paper_id", "citation_key", "title", "authors", "year", "journal", "doi", "section", "page",
    "chunk_id", "evidence_summary", "supports_or_challenges", "confidence", "notes",
    "quote_verified", "evidence_quote", "retrieval_route", "lane", "override_applied", "status_rule",
]
LEDGER_COLUMNS = [
    "paper_id", "citation_key", "title", "authors", "first_author", "year", "year_source", "journal",
    "doi", "openalex_id", "cited_by_count", "cohort", "source_file", "has_enriched", "duplicate_of",
    "in_corpus", "retrieval_routes", "n_claims_candidate", "n_claims_screened", "roles_assigned",
    "n_quotes_verified", "crossref_status", "openalex_status", "bib_key", "bib_resolution", "notes",
]

#: claims.md C01–C14 ↔ the theses that carry their literature (hand map, 2026-09-28).
CLAIM_THESES = {
    "C01": ["TH-RES-01", "TH-DIS-01", "TH-INT-03"],
    "C02": ["TH-RES-02", "TH-MET-03", "TH-MET-01"],
    "C03": ["TH-RES-03"],
    "C04": ["TH-RES-04", "TH-MET-08"],
    "C05": ["TH-RES-05", "TH-INT-06", "TH-MET-09"],
    "C06": ["TH-RES-06", "TH-RES-07", "TH-MET-10", "TH-DAT-05", "TH-DAT-03"],
    "C07": ["TH-MET-06", "TH-INT-10", "TH-MET-07"],
    "C08": ["TH-RES-08", "TH-INT-02", "TH-INT-05", "TH-MET-05", "TH-DIS-02"],
    "C09": ["TH-RES-10", "TH-MET-12", "TH-INT-07"],
    "C10": ["TH-RES-11"],
    "C11": ["TH-RES-12", "TH-INT-08"],
    "C12": ["TH-RES-09", "TH-MET-11", "TH-DAT-07"],
    "C13": ["TH-RES-13", "TH-MET-14"],
    "C14": ["TH-RES-14", "TH-RES-15", "TH-INT-04", "TH-DIS-04", "TH-RES-16", "TH-RES-17", "TH-RES-18"],
}
STATUS_ORDER = ["CONTRADICTED_OR_QUALIFIED", "NO_EVIDENCE_IN_CORPUS", "SOURCE_FOUND_METADATA_UNVERIFIED",
                "VERIFIED_COMPARATOR_ONLY", "VERIFIED_PARTIAL", "VERIFIED_SUPPORTED", "NOT_NEEDED_FOR_MANUSCRIPT"]


# ── overrides ─────────────────────────────────────────────────────────────────

def load_overrides(path: Path = OVERRIDES_YAML) -> list[dict]:
    if not path.exists():
        return []
    doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    rows = doc.get("overrides") if isinstance(doc, dict) else doc
    out = []
    for r in rows or []:
        if not r.get("justification"):
            raise ValueError(f"override without justification: {r}")
        out.append(r)
    return out


# ── status derivation ─────────────────────────────────────────────────────────

def derive_status(claim: AtomicClaim, rows: pd.DataFrame, refs_resolved: set[str]) -> tuple[str, str]:
    """(status, rule_id). `rows` = judged rows of this claim (role != NOT_RELEVANT)."""
    if claim.not_needed:
        return "NOT_NEEDED_FOR_MANUSCRIPT", "R0 not_needed"
    if len(rows) == 0:
        keys = {r.key for r in (claim.thesis.refs if claim.thesis else ()) if not r.placeholder}
        if keys & refs_resolved:
            return "SOURCE_FOUND_METADATA_UNVERIFIED", "R5 named ref resolved in CrossRef/OpenAlex but not in corpus; nothing retained"
        return "NO_EVIDENCE_IN_CORPUS", "R6 nothing retained"
    contr = rows[(rows["role"] == "CONTRASTS") & rows["quote_verified"]]
    if len(contr):
        return "CONTRADICTED_OR_QUALIFIED", f"R1 {len(contr)} verified CONTRASTS"
    have = set()
    for r in rows.itertuples():
        if r.role in QUOTE_REQUIRED and not r.quote_verified:
            continue
        have.add(r.role)
    required = set(claim.required_roles)
    if required and required <= have:
        return "VERIFIED_SUPPORTED", f"R2 all required roles {sorted(required)} met"
    if required & have:
        return "VERIFIED_PARTIAL", f"R3 roles met {sorted(required & have)} of {sorted(required)}"
    if have == {"COMPARATOR"} or (have and have <= {"COMPARATOR", "BACKGROUND"}):
        return "VERIFIED_COMPARATOR_ONLY", f"R4 only {sorted(have)}"
    if have:
        return "VERIFIED_PARTIAL", f"R3b roles present {sorted(have)} but none required ({sorted(required)})"
    return "NO_EVIDENCE_IN_CORPUS", "R6 nothing retained after quote gate"


def thesis_status(statuses: list[str]) -> str:
    real = [s for s in statuses if s != "NOT_NEEDED_FOR_MANUSCRIPT"]
    if not real:
        return "NOT_NEEDED_FOR_MANUSCRIPT"
    if "CONTRADICTED_OR_QUALIFIED" in real:
        return "CONTRADICTED_OR_QUALIFIED"
    return min(real, key=lambda s: STATUS_ORDER.index(s))


# ── the step ──────────────────────────────────────────────────────────────────

def run(theses: list[ThesisRow], claims: list[AtomicClaim], novelty: list[dict],
        work_dir: Path = WORK_DIR, out_dir: Path = OUT_DIR) -> int:
    index = corpus_mod.load_runtime_index(work_dir)
    meta = {r["paper_id"]: r for r in index.to_dict("records")}
    extra_path = work_dir / "paper_meta_crossref.json"
    if extra_path.exists():
        for pid, rec in json.loads(extra_path.read_text(encoding="utf-8")).items():
            if pid in meta and rec and not rec.get("_none"):
                m = meta[pid]
                if not m.get("year") and rec.get("year"):
                    m["year"], m["year_source"] = rec["year"], "crossref"
                if not m.get("journal") and rec.get("journal"):
                    m["journal"] = rec["journal"]
                if not m.get("authors") and rec.get("authors"):
                    m["authors"] = "; ".join(f"{a['family']}, {a['given']}".strip(", ") for a in rec["authors"])
                    m["first_author"] = rec["authors"][0]["family"] if rec["authors"] else ""
    cand = pd.read_parquet(work_dir / "candidates.parquet")
    jpath = work_dir / "screen_judged.parquet"
    judged = pd.read_parquet(jpath) if jpath.exists() else pd.DataFrame(columns=["atomic_claim_id", "paper_id", "role"])
    bib = load_bib_entries(work_dir)
    refs_path = work_dir / "references_verified.csv"
    refs = pd.read_csv(refs_path) if refs_path.exists() else pd.DataFrame(columns=["bib_key", "resolution", "paper_id", "doi", "crossref_status", "openalex_status"])
    refs_resolved = set(refs.loc[refs["resolution"].astype(str).str.startswith(("crossref", "openalex")), "bib_key"])
    pid_to_key = {str(r.paper_id): r.bib_key for r in refs.itertuples() if isinstance(r.paper_id, str) and r.paper_id}
    overrides = load_overrides()
    ov_pair = {(o["atomic_claim_id"], o.get("paper_id")): o for o in overrides if o.get("paper_id")}
    ov_claim = {o["atomic_claim_id"]: o for o in overrides if not o.get("paper_id")}

    # apply per-pair overrides to the judged rows
    judged = judged.copy()
    judged["override_applied"] = False
    judged["override_note"] = ""
    for (cid, pid), o in ov_pair.items():
        m = (judged["atomic_claim_id"] == cid) & (judged["paper_id"] == pid)
        if not m.any():
            new = {c: "" for c in judged.columns}
            new.update({"atomic_claim_id": cid, "paper_id": pid, "thesis_id": cid.split(".")[0],
                        "quote_verified": bool(o.get("quote_verified", False)), "confidence": o.get("confidence", 1.0)})
            judged = pd.concat([judged, pd.DataFrame([new])], ignore_index=True)
            m = (judged["atomic_claim_id"] == cid) & (judged["paper_id"] == pid)
        if o.get("role"):
            judged.loc[m, "role"] = o["role"]
        if "quote_verified" in o:
            judged.loc[m, "quote_verified"] = bool(o["quote_verified"])
        if o.get("evidence_quote"):
            judged.loc[m, "evidence_quote"] = o["evidence_quote"]
        if o.get("rationale"):
            judged.loc[m, "rationale"] = o["rationale"]
        judged.loc[m, "override_applied"] = True
        judged.loc[m, "override_note"] = f"{o.get('read_by', '')} {o.get('date', '')}: {o['justification']}"
    retained = judged[judged["role"] != NOT_RELEVANT]

    # ── 02 thesis evidence ────────────────────────────────────────────────────
    ev_rows, claim_status = [], {}
    cand_routes = {(r.atomic_claim_id, r.paper_id): r.routes for r in cand.itertuples()}
    for claim in claims:
        rows = retained[retained["atomic_claim_id"] == claim.atomic_id]
        status, rule = derive_status(claim, rows, refs_resolved)
        relevance = claim.manuscript_relevance
        if claim.atomic_id in ov_claim:
            o = ov_claim[claim.atomic_id]
            if o.get("status"):
                status, rule = o["status"], f"OVERRIDE ({o.get('read_by','')} {o.get('date','')}): {o['justification']}"
            if o.get("manuscript_relevance"):
                relevance = o["manuscript_relevance"]
        claim_status[claim.atomic_id] = (status, rule, relevance)
        base = {"thesis_id": claim.thesis_id, "atomic_claim_id": claim.atomic_id, "priority": claim.priority,
                "manuscript_section": claim.thesis.section if claim.thesis else "", "status": status, "status_rule": rule}
        if len(rows) == 0:
            ev_rows.append({**{c: "" for c in EVIDENCE_COLUMNS}, **base, "evidence_role": "", "notes": "no source retained"})
            continue
        for r in rows.itertuples():
            m = meta.get(r.paper_id, {})
            ev_rows.append({**{c: "" for c in EVIDENCE_COLUMNS}, **base,
                            "evidence_role": r.role, "paper_id": r.paper_id,
                            "citation_key": pid_to_key.get(r.paper_id, ""), "title": m.get("title", r.title),
                            "authors": m.get("authors", ""), "year": m.get("year", r.year), "journal": m.get("journal", ""),
                            "doi": m.get("doi_clean", r.doi), "section": getattr(r, "quote_section", ""),
                            "page": getattr(r, "quote_page", ""), "chunk_id": getattr(r, "quote_chunk_id", ""),
                            "evidence_summary": getattr(r, "rationale", ""),
                            "supports_or_challenges": ("challenges" if r.role == "CONTRASTS" else "supports" if r.role in ("SUPPORTS",) else "neutral"),
                            "confidence": getattr(r, "confidence", ""),
                            "notes": "; ".join(x for x in (f"route={cand_routes.get((claim.atomic_id, r.paper_id), '')}", getattr(r, "demotion_reason", ""), getattr(r, "override_note", "")) if x),
                            "quote_verified": bool(getattr(r, "quote_verified", False)), "evidence_quote": getattr(r, "evidence_quote", ""),
                            "retrieval_route": cand_routes.get((claim.atomic_id, r.paper_id), ""), "lane": getattr(r, "lane", ""),
                            "override_applied": bool(getattr(r, "override_applied", False))})
    ev = pd.DataFrame(ev_rows, columns=EVIDENCE_COLUMNS)
    ev.to_csv(out_dir / DELIVERABLES["evidence"], index=False)

    # ── 03 source ledger ──────────────────────────────────────────────────────
    ledger = []
    touched = set(cand["paper_id"]) | set(judged["paper_id"])
    screened = judged.groupby("paper_id").size().to_dict()
    roles = retained.groupby("paper_id")["role"].agg(lambda s: "+".join(sorted(set(s)))).to_dict()
    nq = retained[retained["quote_verified"].astype(bool)].groupby("paper_id").size().to_dict()
    n_cand = cand.groupby("paper_id")["atomic_claim_id"].nunique().to_dict()
    routes = cand.groupby("paper_id")["routes"].agg(lambda s: "+".join(sorted({x for v in s for x in str(v).split("+") if x}))).to_dict()
    ref_by_key = {r.bib_key: r for r in refs.itertuples()}
    for pid in sorted(touched):
        m = meta.get(pid, {})
        key = pid_to_key.get(pid, "")
        rr = ref_by_key.get(key)
        ledger.append({"paper_id": pid, "citation_key": key, "title": m.get("title", ""), "authors": m.get("authors", ""),
                       "first_author": m.get("first_author", ""), "year": m.get("year", ""), "year_source": m.get("year_source", ""),
                       "journal": m.get("journal", ""), "doi": m.get("doi_clean", ""), "openalex_id": m.get("openalex_id", ""),
                       "cited_by_count": m.get("cited_by_count", ""), "cohort": m.get("cohort", ""), "source_file": m.get("source_file", ""),
                       "has_enriched": m.get("has_enriched", ""), "duplicate_of": m.get("duplicate_of", ""), "in_corpus": True,
                       "retrieval_routes": routes.get(pid, ""), "n_claims_candidate": n_cand.get(pid, 0),
                       "n_claims_screened": screened.get(pid, 0), "roles_assigned": roles.get(pid, ""), "n_quotes_verified": nq.get(pid, 0),
                       "crossref_status": getattr(rr, "crossref_status", "") if rr else "", "openalex_status": getattr(rr, "openalex_status", "") if rr else "",
                       "bib_key": key, "bib_resolution": getattr(rr, "resolution", "") if rr else "", "notes": ""})
    for r in refs.itertuples():
        if isinstance(r.paper_id, str) and r.paper_id and r.paper_id in touched:
            continue
        e = bib.get(r.bib_key, {}).get("fields", {})
        ledger.append({"paper_id": r.paper_id if isinstance(r.paper_id, str) else "", "citation_key": r.bib_key, "title": e.get("title", ""),
                       "authors": e.get("author", ""), "first_author": "", "year": e.get("year", ""), "year_source": "bib",
                       "journal": e.get("journal", e.get("booktitle", "")), "doi": r.doi if isinstance(r.doi, str) else "",
                       "openalex_id": getattr(r, "openalex_id", "") if isinstance(getattr(r, "openalex_id", ""), str) else "",
                       "cited_by_count": "", "cohort": "", "source_file": "", "has_enriched": "", "duplicate_of": "",
                       "in_corpus": bool(isinstance(r.paper_id, str) and r.paper_id), "retrieval_routes": "bib_key_only",
                       "n_claims_candidate": 0, "n_claims_screened": 0, "roles_assigned": "", "n_quotes_verified": 0,
                       "crossref_status": r.crossref_status, "openalex_status": r.openalex_status, "bib_key": r.bib_key,
                       "bib_resolution": r.resolution, "notes": "named in theses.csv / references.bib; not retrieved from the corpus"})
    pd.DataFrame(ledger, columns=LEDGER_COLUMNS).to_csv(out_dir / DELIVERABLES["ledger"], index=False)

    # ── 05 claim–citation matrix ──────────────────────────────────────────────
    write_matrix(theses, claims, claim_status, ev, novelty, out_dir)

    counts = pd.Series([s for s, _, _ in claim_status.values()]).value_counts().to_dict()
    th_status = {t.id: thesis_status([claim_status[c.atomic_id][0] for c in claims if c.thesis_id == t.id]) for t in theses}
    manifest.update(status_counts_atomic=counts, status_counts_thesis=pd.Series(list(th_status.values())).value_counts().to_dict(),
                    n_unique_papers_screened=int(judged["paper_id"].nunique()) if len(judged) else 0,
                    n_unique_papers_retained=int(retained["paper_id"].nunique()) if len(retained) else 0,
                    n_overrides=len(overrides), exported_on=str(date.today()))
    (work_dir / "thesis_status.json").write_text(json.dumps(th_status, indent=1), encoding="utf-8")
    (work_dir / "claim_status.json").write_text(json.dumps({k: {"status": v[0], "rule": v[1], "relevance": v[2]} for k, v in claim_status.items()}, indent=1), encoding="utf-8")
    logger.info("export: %d evidence rows, %d ledger rows; atomic statuses %s", len(ev), len(ledger), counts)
    return 0


def _cite(row) -> str:
    key = row.citation_key or ""
    fa = (row.authors or "").split(";")[0].split(",")[0].strip() if isinstance(row.authors, str) else ""
    return key or (f"{fa} {row.year}".strip() if fa else str(row.paper_id)[:40])


def write_matrix(theses, claims, claim_status, ev: pd.DataFrame, novelty: list[dict], out_dir: Path) -> None:
    by_thesis = {t.id: t for t in theses}
    lines = ["# 05 — Claim → evidence role → best sources → strength → caveat", "",
             f"Generated {date.today()} from `02_thesis_evidence.csv` (rule-assigned statuses; overrides marked). "
             "Kakhovka numbers appear only when `human_verified=yes` in `kakhovka_numbers.csv`.", ""]
    strength = {"VERIFIED_SUPPORTED": "strong", "VERIFIED_PARTIAL": "partial", "VERIFIED_COMPARATOR_ONLY": "comparator only",
                "CONTRADICTED_OR_QUALIFIED": "contested/qualified", "NO_EVIDENCE_IN_CORPUS": "none in corpus",
                "SOURCE_FOUND_METADATA_UNVERIFIED": "metadata only", "NOT_NEEDED_FOR_MANUSCRIPT": "n/a"}
    lines += ["## A. Manuscript claims C01–C14", "", "| claim | theses | atomic claims (status) | best sources by role | strength | caveat |", "|---|---|---|---|---|---|"]
    for cid, tids in CLAIM_THESES.items():
        atomic = [c for c in claims if c.thesis_id in tids]
        st = [claim_status[c.atomic_id][0] for c in atomic]
        overall = thesis_status(st) if st else "NOT_NEEDED_FOR_MANUSCRIPT"
        srcs = _best_sources(ev[ev["atomic_claim_id"].isin([c.atomic_id for c in atomic])])
        caveat = "; ".join(sorted({claim_status[c.atomic_id][1].split(" ", 1)[1][:60] for c in atomic if claim_status[c.atomic_id][0] not in ("VERIFIED_SUPPORTED",)}))[:300]
        lines.append(f"| {cid} | {', '.join(tids)} | {'; '.join(f'{c.atomic_id} ({claim_status[c.atomic_id][0]})' for c in atomic)} | {srcs} | {strength.get(overall, overall)} | {caveat} |")
    lines += ["", "## B. Theses TH-* (one row per atomic claim)", "", "| thesis | pri | atomic claim | required roles | status | relevance | best sources by role | caveat |", "|---|---|---|---|---|---|---|---|"]
    for c in claims:
        st, rule, rel = claim_status[c.atomic_id]
        srcs = _best_sources(ev[ev["atomic_claim_id"] == c.atomic_id])
        lines.append(f"| {c.thesis_id} | {c.priority} | {c.atomic_id}: {c.statement[:110]}… | {', '.join(c.required_roles)} | {st} | {rel} | {srcs} | {rule[:120]} |")
    lines += ["", "## C. Novelty questions (brief §14) — verdict is a human decision", "",
              "| # | question | closest retained papers | verdict |", "|---|---|---|---|"]
    for nq in novelty:
        sub = ev[ev["atomic_claim_id"].isin(nq.get("atomic_claim_ids", [])) & (ev["paper_id"] != "")]
        closest = "; ".join(sorted({_cite(r) for r in sub.itertuples()}))[:400]
        lines.append(f"| {nq['nq_id']} | {nq['question']} | {closest} | [MAIN AGENT: YES / PARTIAL / NO EVIDENCE FOUND IN CORPUS] |")
    (out_dir / DELIVERABLES["matrix"]).write_text("\n".join(lines) + "\n", encoding="utf-8")


def _best_sources(sub: pd.DataFrame) -> str:
    if sub.empty or (sub["paper_id"] == "").all():
        return "—"
    parts = []
    for role, grp in sub[sub["paper_id"] != ""].groupby("evidence_role"):
        grp = grp.sort_values(["quote_verified", "confidence"], ascending=[False, False])
        names = [_cite(r) + ("*" if r.quote_verified else "") for r in grp.head(3).itertuples()]
        parts.append(f"{role}: {', '.join(names)}")
    return "; ".join(parts).replace("|", "/")
