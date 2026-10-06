"""
qa_experiment.py — end-to-end QA: vector RAG vs knowledge graph vs graph + RAG
(docs_v2/EQUATION_KG_PLAN.md, phase 6, task 5).

Questions, reference answers and reference evidence are written by a person (gold page, kind
qa_item). For each question three systems retrieve context through the running Knowledge API
(no second copy of Chroma in memory) and one answerer writes an answer from that context only,
citing the context ids:

    rag     POST /search/chunks (k = 8)                                   ids C:<chunk_id>
    kg      laws named in the question  → GET /laws/{id}                  ids L:<law_id>, E:<eq_id>
            quantities named            → GET /equations/search?quantity  ids E:<eq_id>
            metrics named               → GET /metrics/facts              ids F:<fact_id>
    kg_rag  both contexts

The answerer is the same model and prompt for all three (GEMINI_MODEL, default
gemini-3.1-flash-lite); only the context differs. Answers are written blind to
data/gold/equation_kg_<version>/qa_runs/<stamp>/answers.jsonl (target kind qa_answer; the system is
not shown) and judged by a person on the gold page. RUN.json maps answer ids to systems and holds
the model, prompt hash and per-answer automatic checks (cited ids exist in the context; cited papers
∩ reference papers). Never overwrites.

Usage:
    python -m src.evaluation.qa_experiment --gold v1 [--dry-run] [--limit N]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
GOLD = ROOT / "data" / "gold"
SYSTEMS = ("rag", "kg", "kg_rag")
K_CHUNKS = 8
PROMPT = """You answer a scientific question about hydrology, flood and remote-sensing literature.
Use ONLY the context records below. Every sentence that states a fact must cite the ids of the records
it comes from in square brackets, e.g. [C:abc] or [E:paper:formula_3]. If the context does not contain
the answer, say "The evidence provided does not answer this." and stop. Do not use outside knowledge.

Question: {question}

Context records:
{context}

Answer (at most 200 words):"""
PROMPT_SHA = hashlib.sha256(PROMPT.encode()).hexdigest()[:16]


def _sha16(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()[:16]


class Api:
    def __init__(self, base: str | None = None, key: str | None = None, transport=None):
        base = base or os.getenv("GHAI_API_URL", "http://127.0.0.1:8090")
        key = key or os.getenv("GHAI_API_KEY")
        if not key:
            raise SystemExit("GHAI_API_KEY is not set (source ~/.config/ghai/env)")
        base = re.sub(r"/v1/?$", "", base.rstrip("/"))           # GHAI_API_URL may already end in /v1
        self.c = httpx.Client(base_url=base + "/v1/", headers={"X-API-Key": key}, timeout=120, transport=transport)

    def _ok(self, r) -> dict:
        if r.status_code != 200:                                  # a retrieval failure must not look like "no evidence"
            raise RuntimeError(f"{r.request.method} {r.request.url.path}: {r.status_code} {r.text[:300]}")
        return r.json()

    def get(self, path: str, **params) -> dict:
        return self._ok(self.c.get(path, params={k: v for k, v in params.items() if v is not None}))

    def post(self, path: str, body: dict) -> dict:
        return self._ok(self.c.post(path, json=body))


# ── retrieval ──────────────────────────────────────────────────────────────────

def rag(api: Api, q: str) -> list[dict]:
    hits = api.post("search/chunks", {"query": q, "k": K_CHUNKS}).get("hits") or []
    return [{"id": f"C:{h['chunk_id']}", "paper_id": (h.get("paper") or {}).get("paper_id"), "page": h.get("page"),
             "text": h.get("text", "")[:1200]} for h in hits]


def named_laws(q: str) -> list[str]:
    from src.ontology.laws import registry, s_text
    return [l["id"] for l in registry()["laws"] if s_text(l["id"], {"lead_in": q}) >= 1.0]


def named_quantities(q: str) -> list[str]:
    """Ontology names occurring in the question, longest first, without overlaps."""
    from src.ontology.quantities import _clean, ontology
    text = f" {_clean(q)} "
    found, taken = [], []
    for alias, qid in sorted(ontology()["alias"].items(), key=lambda kv: -len(kv[0])):
        if len(alias) < 4:
            continue
        i = text.find(f" {alias} ")
        if i >= 0 and not any(a <= i < b for a, b in taken) and qid not in found:
            found.append(qid)
            taken.append((i, i + len(alias) + 2))
    return found[:3]


def named_metrics(q: str) -> list[str]:
    from src.ontology.laws import registry
    out = []
    for law in registry()["laws"]:
        if law.get("kind") == "metric" and re.search("|".join(law.get("text") or ["$^"]), q, re.I):
            out += [c for c in law.get("concepts") or [] if c.startswith("metric.")]
    return sorted(set(out))


def kg(api: Api, q: str) -> list[dict]:
    recs = []
    for law_id in named_laws(q):
        body = api.get(f"laws/{law_id}", limit=8)
        law = body.get("law") or {}
        forms = "; ".join(f"{f['latex']}" + (f" ({f['variant']})" if f.get("variant") else "") for f in body.get("forms", []))
        recs.append({"id": f"L:{law_id}", "text": f"{law.get('name')} — {law.get('reference')}. Forms: {forms}"})
        for i in body.get("instances") or []:
            recs.append({"id": f"E:{i['eq_id']}", "paper_id": i.get("paper_id"), "page": i.get("page"),
                         "text": f"{i.get('title')} ({i.get('year')}), p. {i.get('page')}: {i.get('latex_raw')} "
                                 f"[link {i.get('status')} {i.get('score')}]"})
    for qid in named_quantities(q):
        body = api.get("equations/search", quantity=qid, limit=8)
        for i in body.get("items") or []:
            params = "; ".join(f"{m.get('symbol')}: {m.get('description')}" + (f" [{m['unit']}]" if m.get("unit") else "")
                               for m in i.get("matched_parameters") or [])
            recs.append({"id": f"E:{i['eq_id']}", "paper_id": i.get("paper_id"), "page": i.get("page"),
                         "text": f"{i.get('title')} ({i.get('year')}), p. {i.get('page')}: {i.get('latex_raw')} — "
                                 f"{i.get('purpose') or ''} — {params}"})
    for m in named_metrics(q):
        body = api.get("metrics/facts", metric=m, limit=15)
        for f in body.get("items") or []:
            ev = f.get("evidence") or {}
            paper = f.get("paper") or {}
            where = ev.get("text") or f"{' / '.join(ev.get('row_context') or [])} × {ev.get('col_header') or ''}"
            recs.append({"id": f"F:{f.get('fact_id')}", "paper_id": paper.get("paper_id"), "page": ev.get("page"),
                         "text": f"{paper.get('title') or paper.get('paper_id')}: {m} = {f.get('value')} ({where}), "
                                 f"{ev.get('table_label') or ''}, p. {ev.get('page')}"})
    seen, out = set(), []
    for r in recs:
        if r["id"] not in seen:
            seen.add(r["id"])
            out.append(r)
    return out[:30]


def contexts(api: Api, q: str) -> dict[str, list[dict]]:
    r, g = rag(api, q), kg(api, q)
    return {"rag": r, "kg": g, "kg_rag": g + r}


# ── answering ──────────────────────────────────────────────────────────────────

def render(records: list[dict]) -> str:
    return "\n".join(f"[{r['id']}] {r['text']}" for r in records) or "(no records)"


def answer(question: str, records: list[dict], llm) -> str:
    return llm(PROMPT.format(question=question, context=render(records)))


def gemini(prompt: str) -> str:
    os.environ.setdefault("GEMINI_MODEL", "gemini-3.1-flash-lite")
    from src.paper_audit._utils import call_gemini
    return call_gemini(prompt, max_tokens=600, no_thinking=True) or "(no answer: model call failed)"


def checks(text: str, records: list[dict], reference_evidence: list[str]) -> dict:
    cited = re.findall(r"\[([CLEF]:[^\]\s]+)\]", text or "")
    ids = {r["id"] for r in records}
    papers = {r.get("paper_id") for r in records if r["id"] in set(cited) and r.get("paper_id")}
    ref_papers = {x.split(",")[0].split()[0].strip() for x in reference_evidence if x.strip()}
    return {"cited": len(cited), "cited_unknown": sorted(set(cited) - ids),
            "cited_papers": sorted(papers), "reference_papers_cited": sorted(papers & ref_papers),
            "reference_paper_recall": round(len(papers & ref_papers) / len(ref_papers), 4) if ref_papers else None,
            "abstained": "does not answer this" in (text or "")}


def questions() -> list[dict]:
    from src.services import verify
    out = []
    for r in verify.listing(current=True, target_kind="qa_item", limit=1000):
        c = r.get("corrected") or {}
        if c.get("question"):
            out.append({"qid": r["target_id"], "question": c["question"], "answer": c.get("answer"),
                        "evidence": c.get("evidence") or []})
    return sorted(out, key=lambda x: x["qid"])


def run(qs: list[dict], api: Api, llm, stamp: str, *, dry_run: bool = False) -> tuple[list[dict], dict]:
    rng = random.Random(stamp)
    answers, mapping, auto = [], {}, {}
    for q in qs:
        ctx = contexts(api, q["question"])
        for system in SYSTEMS:
            aid = _sha16(f"{stamp}|{q['qid']}|{system}")
            text = None if dry_run else answer(q["question"], ctx[system], llm)
            mapping[aid] = [system, q["qid"]]
            auto[aid] = {"context_ids": [r["id"] for r in ctx[system]],
                         "context_sha": _sha16(render(ctx[system])),
                         **(checks(text, ctx[system], q["evidence"]) if text else {})}
            cites = {r["id"]: f"{r['id']} — {r.get('paper_id') or ''} p. {r.get('page') or '?'}" for r in ctx[system]}
            answers.append({"target_kind": "qa_answer", "target_id": aid, "question": q["question"],
                            "reference_answer": q["answer"], "reference_evidence": q["evidence"], "answer": text,
                            "citations": [cites[c] for c in re.findall(r"\[([CLEF]:[^\]\s]+)\]", text or "") if c in cites]})
    rng.shuffle(answers)                                   # the annotator cannot tell the systems apart by order
    meta = {"stamp": stamp, "systems": SYSTEMS, "questions": len(qs), "model": os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite"),
            "prompt_sha": PROMPT_SHA, "k_chunks": K_CHUNKS, "dry_run": dry_run, "answer_system": mapping, "auto": auto}
    return answers, meta


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--gold", required=True)
    ap.add_argument("--dry-run", action="store_true", help="retrieve only; no model calls")
    ap.add_argument("--limit", type=int)
    args = ap.parse_args(argv)
    gold_dir = GOLD / f"equation_kg_{args.gold}"
    if not (gold_dir / "MANIFEST.json").exists():
        print(f"no frozen gold set at {gold_dir}", file=sys.stderr)
        return 2
    qs = questions()[: args.limit] if args.limit else questions()
    if not qs:
        print("no questions yet: a person adds them on the gold page (Питання (QA))", file=sys.stderr)
        return 2
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = gold_dir / "qa_runs" / stamp
    out.mkdir(parents=True)
    answers, meta = run(qs, Api(), gemini, stamp, dry_run=args.dry_run)
    with (out / "answers.jsonl").open("w") as fh:
        for a in answers:
            fh.write(json.dumps(a, ensure_ascii=False) + "\n")
    (out / "RUN.json").write_text(json.dumps(meta, indent=1, ensure_ascii=False))
    print(f"{len(answers)} answers → {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
