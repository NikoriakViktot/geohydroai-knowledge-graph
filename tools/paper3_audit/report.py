"""Post-export renderers: novelty verdicts into 05, per-thesis verdicts into 01 §9, and the
completion report (counts from 02/03/manifest). No LLM; nothing invented."""
from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

import pandas as pd
import yaml

from tools.paper3_audit import manifest
from tools.paper3_audit.claims import AtomicClaim, ThesisRow
from tools.paper3_audit.config import DELIVERABLES, OUT_DIR, WORK_DIR

NOVELTY_YAML = OUT_DIR / "novelty_verdicts.yaml"


def fill_novelty(out_dir: Path = OUT_DIR) -> int:
    path = out_dir / DELIVERABLES["matrix"]
    text = path.read_text(encoding="utf-8")
    doc = yaml.safe_load(NOVELTY_YAML.read_text(encoding="utf-8")) or {}
    n = 0
    for v in doc.get("verdicts", []):
        pat = re.compile(rf"^(\| {re.escape(v['nq_id'])} \| .*? \| .*? \| )\[MAIN AGENT: [^\]]*\] \|$", re.MULTILINE)
        cell = f"**{v['verdict']}** — {v['statement']} Denominator: {v['denominator']}. Closest: " + "; ".join(v["closest"])
        text, k = pat.subn(lambda m: m.group(1) + cell.replace("|", "/") + " |", text)
        n += k
    path.write_text(text, encoding="utf-8")
    return n


def thesis_table(theses: list[ThesisRow], claims: list[AtomicClaim], out_dir: Path = OUT_DIR, work_dir: Path = WORK_DIR) -> str:
    cs = json.loads((work_dir / "claim_status.json").read_text(encoding="utf-8"))
    ts = json.loads((work_dir / "thesis_status.json").read_text(encoding="utf-8"))
    ev = pd.read_csv(out_dir / DELIVERABLES["evidence"], dtype=str).fillna("")
    lines = ["| thesis | pri | status | relevance | atomic claims → status | best sources (role*: quote verified) | caveat |", "|---|---|---|---|---|---|---|"]
    for t in theses:
        atomic = [c for c in claims if c.thesis_id == t.id]
        rel = sorted({cs[c.atomic_id]["relevance"] for c in atomic}, key=["CORE", "SUPPORTING", "SUPPLEMENTARY", "DROP"].index)[0] if atomic else ""
        parts = "; ".join(f"{c.atomic_id.split('.')[-1]}: {cs[c.atomic_id]['status']}" for c in atomic)
        sub = ev[(ev["thesis_id"] == t.id) & (ev["paper_id"] != "")]
        srcs = []
        for role, grp in sub.groupby("evidence_role"):
            names = []
            for r in grp.sort_values(["quote_verified", "confidence"], ascending=[False, False]).head(3).itertuples():
                key = r.citation_key or (f"{str(r.authors).split(';')[0].split(',')[0]} {r.year}".strip() if r.authors else str(r.paper_id)[:30])
                names.append(key + ("*" if r.quote_verified == "True" else ""))
            srcs.append(f"{role}: {', '.join(names)}")
        caveat = "; ".join(sorted({cs[c.atomic_id]["rule"].split(" ", 1)[-1][:70] for c in atomic if cs[c.atomic_id]["status"] != "VERIFIED_SUPPORTED"}))
        lines.append(f"| {t.id} | {t.priority} | **{ts[t.id]}** | {rel} | {parts} | {'; '.join(srcs).replace('|', '/') or '—'} | {caveat.replace('|', '/')} |")
    return "\n".join(lines)


def completion_report(theses, claims, out_dir: Path = OUT_DIR, work_dir: Path = WORK_DIR) -> str:
    m = manifest.load()
    ts = json.loads((work_dir / "thesis_status.json").read_text(encoding="utf-8"))
    cs = json.loads((work_dir / "claim_status.json").read_text(encoding="utf-8"))
    ev = pd.read_csv(out_dir / DELIVERABLES["evidence"], dtype=str).fillna("")
    led = pd.read_csv(out_dir / DELIVERABLES["ledger"], dtype=str).fillna("")
    judged = pd.read_parquet(work_dir / "screen_judged.parquet")
    cand = pd.read_parquet(work_dir / "candidates.parquet")
    cited = set(ev.loc[ev["paper_id"] != "", "paper_id"])
    tc = pd.Series(list(ts.values())).value_counts().to_dict()
    cc = pd.Series([v["status"] for v in cs.values()]).value_counts().to_dict()
    high_open = [t for t, s in ts.items() if s in ("NO_EVIDENCE_IN_CORPUS", "SOURCE_FOUND_METADATA_UNVERIFIED", "CONTRADICTED_OR_QUALIFIED")
                 and next(x for x in theses if x.id == t).priority == "high"]
    files = sorted(p.name for p in out_dir.iterdir() if p.is_file() and not p.name.startswith("_"))
    return "\n".join([
        f"# Completion report — {date.today()}", "",
        f"- corpus searched: {m.get('n_normalized_files')} normalized papers ({m.get('n_chroma')} SPECTER2 chunks; {m.get('n_with_openalex_id')} with OpenAlex ids; queries sha256 {str(m.get('queries_sha256'))[:16]}…)",
        f"- theses processed: {len(theses)} (atomic claims: {len(claims)})",
        f"- thesis statuses: {tc}", f"- atomic-claim statuses: {cc}",
        f"- candidate pairs recorded: {len(cand)} ({int(cand['prefilter_pass'].sum())} prefilter-pass, {int(cand['screen_selected'].sum())} screened-selected)",
        f"- unique papers screened by LLM (Gemini 3.1/3.5 flash-lite; Ollama mistral-nemo for TH-RES-16..18, {int((judged["model"].astype(str).str.startswith("ollama")).sum())} pairs): {judged["paper_id"].nunique()} ({len(judged)} pair judgements; {int((judged['role'] != 'NOT_RELEVANT').sum())} retained; quotes verified: {int(judged['quote_verified'].astype(bool).sum())})",
        f"- unique papers retained as evidence (02): {len(cited)}; sources in the ledger (03): {len(led)}",
        f"- unresolved high-priority theses: {len(high_open)} → {', '.join(high_open) or 'none'}",
        f"- files created in {out_dir.name}/: {', '.join(files)}",
    ])
