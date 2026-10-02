"""Evidence-role screening: Gemini labels each (atomic claim, paper) pair with one of
eight roles from passages of the paper's own text; SUPPORTS / CONTRASTS survive only
with a verbatim quote (``src.paper_3.evidence.verify_quote``). One model lane per
process; resumable; quota-capped.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd

from src.paper_3._utils import REFUSAL, load_paper_json, paper_sections, resolve_gemini_key
from src.paper_3.classify_relation import (CALL_DELAY_SECONDS, call_gemini, call_ollama,
                                           done_triples, load_raw, parse_response, prompt_sha256)
from src.paper_3.evidence import EvidencePassage, build_passages, verify_quote
from src.paper_3.theses import SYSTEM_CLASSES
from tools.paper3_audit.claims import AtomicClaim
from tools.paper3_audit.config import (CIRCUIT_BACKOFF_SECONDS, NOT_RELEVANT, QUOTA_DAILY_CAP,
                                       QUOTE_REQUIRED, ROLE_VOCABULARY, ROLES, SEARCH_LOG, WORK_DIR)
from tools.paper3_audit.retrieve import CANDIDATES_FILE

logger = logging.getLogger(__name__)

RAW_FILE = "screen_raw.jsonl"
JUDGED_FILE = "screen_judged.parquet"
REJECTED_FILE = "screen_rejected.csv"
QUOTA_FILE = "quota_ledger.json"
MAX_PASSAGES = 12

_DEFINITIONS = """ROLE DEFINITIONS (pick the ONE that best describes what the passages establish)
CONTRASTS             - the paper reports a finding inconsistent with, or explicitly
                        qualifying, the claim. Disagreement about magnitude counts.
                        Silence does not.
SUPPORTS              - the paper reports a finding or measurement consistent with the claim.
COMPARATOR            - the paper reports a NUMBER for the same event / quantity / setting
                        under its own definitions (AOI, date, sensor, reference water), so
                        it can be placed next to ours but not used as validation.
METHOD_FROM           - the paper supplies a method, algorithm, tool or workflow the claim
                        depends on (it does not test the claim).
LIMITATION            - the paper documents a failure mode, error source or limit of the
                        method/sensor the claim relies on.
DEFINITION            - the paper defines the term, metric or concept the claim uses.
DATASET_DOCUMENTATION - the paper documents a dataset or product (fields, accuracy,
                        version, licence) the claim uses.
BACKGROUND            - the paper establishes context (the event, the site, the general
                        problem) without supplying a method, number or test."""

PROMPT_TEMPLATE = """STRICT RULES - READ BEFORE EVERYTHING ELSE:
1. Judge ONLY from the PASSAGES below. Do not use outside knowledge about this
   paper, its authors, or the field.
2. evidence_quote MUST be copied character-for-character from ONE passage. Do not
   paraphrase, do not join two passages, do not correct typos. The quote is checked
   against the source automatically and a quote that fails the check is discarded.
3. If no passage bears on the SUBJECT of the claim, set role to "NOT_RELEVANT",
   evidence_quote to "" and rationale to exactly:
   "{refusal}"
4. Output a single JSON object and nothing else. No prose before or after.
5. The claim describes what the literature is expected to contribute. You are NOT
   asked whether the paper confirms the manuscript's own results (its reconstruction,
   W_total, A_new, RF20, U-Net arms, C14). Ask only: what does THIS paper contribute
   to the claim's subject? A passage that reports a number for the same event,
   quantity or setting is COMPARATOR; one that supplies a method, a definition, a
   product description or a documented failure mode takes that role; a finding that
   agrees or disagrees with the claim's substance is CONTRASTS or SUPPORTS.

CLAIM {claim_id} (manuscript section {section}): {statement}

{definitions}

PAPER: {title} ({year}, {journal}) doi:{doi}
EVIDENCE LEVEL: full_text — the passages below are sections of the paper's full text.

PASSAGES:
{passages}

Return exactly this JSON shape:
{{"role": "CONTRASTS|SUPPORTS|COMPARATOR|METHOD_FROM|LIMITATION|DEFINITION|DATASET_DOCUMENTATION|BACKGROUND|NOT_RELEVANT",
  "confidence": 0.0,
  "passage_id": "P1",
  "evidence_quote": "",
  "rationale": "at most 40 words",
  "is_own_result": true,
  "quantity": null}}

is_own_result is true only if the finding is this paper's own result, false if the
paper is reporting what someone else found. If (and only if) the role is COMPARATOR
and a passage states a number, fill quantity as
{{"value": "", "unit": "", "quantity_name": "", "aoi": "", "date_or_period": "",
  "temporal": "snapshot|cumulative|persistence|n/a", "reference_water": "", "sensor": ""}}
using only what the passages say ("" when not stated)."""

_GENERATION_CONFIG = {"temperature": 0.0, "max_output_tokens": 1000,
                      "response_mime_type": "application/json"}

JUDGED_COLUMNS = [
    "atomic_claim_id", "thesis_id", "paper_id", "doi", "title", "year", "role", "confidence",
    "passage_id", "evidence_quote", "quote_verified", "quote_similarity", "quote_repaired",
    "quote_section", "quote_source_file", "quote_chunk_id", "quote_page", "rationale",
    "is_own_result", "quantity_json", "model", "lane", "evidence_level", "prompt_sha256",
    "demotion_reason", "parse_ok", "ts",
]
REJECTED_COLUMNS = ["atomic_claim_id", "paper_id", "doi", "role", "model", "reason",
                    "claimed_quote", "best_passage_id", "similarity"]


# ── prompt ────────────────────────────────────────────────────────────────────

def build_prompt(claim: AtomicClaim, paper_meta: dict, passages: list[EvidencePassage]) -> str:
    return PROMPT_TEMPLATE.format(
        refusal=REFUSAL, claim_id=claim.atomic_id,
        section=(claim.thesis.section if claim.thesis else ""),
        statement=" ".join(claim.statement.split()), definitions=_DEFINITIONS,
        title=paper_meta.get("title") or "(title unavailable)",
        year=paper_meta.get("year") or "n.d.",
        journal=paper_meta.get("journal") or "(journal unavailable)",
        doi=paper_meta.get("doi") or "(no DOI)",
        passages="\n\n".join(p.as_prompt_line() for p in passages),
    )


def normalise_role(parsed: dict) -> dict:
    role = str(parsed.get("role", "")).strip().upper()
    if role not in ROLES:
        role = NOT_RELEVANT
    try:
        confidence = min(max(float(parsed.get("confidence", 0.0)), 0.0), 1.0)
    except (TypeError, ValueError):
        confidence = 0.0
    own = parsed.get("is_own_result")
    quantity = parsed.get("quantity")
    if not isinstance(quantity, dict) or not str(quantity.get("value", "")).strip():
        quantity = None
    return {"role": role, "confidence": confidence,
            "passage_id": str(parsed.get("passage_id", "") or ""),
            "evidence_quote": str(parsed.get("evidence_quote", "") or ""),
            "rationale": str(parsed.get("rationale", "") or ""),
            "is_own_result": own if isinstance(own, bool) else False,
            "quantity": quantity}


# ── passages with chunk provenance ────────────────────────────────────────────

def attach_chunk_provenance(passages: list[EvidencePassage], hits: pd.DataFrame | None) -> list[EvidencePassage]:
    """Best-effort: give a passage the chunk_id/page of a semantic hit whose head it contains."""
    if hits is None or hits.empty:
        return passages
    out = []
    for p in passages:
        chunk_id, page = None, None
        for h in hits.itertuples():
            head = (h.text_head or "")[:80].strip()
            if head and head in p.text:
                chunk_id, page = h.chunk_id, h.page
                break
        if chunk_id:
            from dataclasses import replace
            p = replace(p, chunk_id=chunk_id, page=(int(page) if page not in (None, "") and str(page).isdigit() else None))
        out.append(p)
    return out


# ── judging ───────────────────────────────────────────────────────────────────

def _passage_map(raw: dict) -> dict[str, str]:
    return {p["passage_id"]: p["text"] for p in raw.get("passages_offered", [])}


def _passage_meta(raw: dict, pid: str) -> dict:
    for p in raw.get("passages_offered", []):
        if p.get("passage_id") == pid:
            return p
    return {}


def finalize_screen_row(raw: dict) -> tuple[dict, dict | None]:
    """One raw record → judged row (+ rejection row when a required quote fails)."""
    base = {k: "" for k in JUDGED_COLUMNS}
    base.update({
        "atomic_claim_id": raw.get("atomic_claim_id", ""), "thesis_id": raw.get("thesis_id", ""),
        "paper_id": raw.get("paper_id", ""), "doi": raw.get("doi", "") or "",
        "title": raw.get("title", "") or "", "year": raw.get("year"),
        "model": raw.get("model", ""), "lane": raw.get("lane", ""),
        "evidence_level": raw.get("evidence_level", "full_text"),
        "prompt_sha256": raw.get("prompt_sha256", ""), "ts": raw.get("ts", ""),
        "role": NOT_RELEVANT, "confidence": 0.0, "quote_verified": False,
        "quote_similarity": 0.0, "quote_repeated": False, "quote_repaired": False,
        "is_own_result": False, "quantity_json": "", "demotion_reason": "", "parse_ok": False,
    })
    base.pop("quote_repeated", None)
    parsed = parse_response(raw.get("raw_response", ""))
    reject_base = {k: base.get(k, "") for k in ("atomic_claim_id", "paper_id", "doi", "role", "model")}
    if parsed is None:
        base["rationale"] = "model response could not be parsed as JSON"
        return base, {**reject_base, "reason": "unparseable response",
                      "claimed_quote": (raw.get("raw_response") or "")[:200],
                      "best_passage_id": "", "similarity": 0.0}
    f = normalise_role(parsed)
    base.update({"parse_ok": True, "role": f["role"], "confidence": f["confidence"],
                 "passage_id": f["passage_id"], "rationale": f["rationale"],
                 "is_own_result": f["is_own_result"],
                 "quantity_json": json.dumps(f["quantity"], ensure_ascii=False) if f["quantity"] else ""})
    if f["role"] == NOT_RELEVANT:
        return base, None

    verdict = verify_quote(f["evidence_quote"], _passage_map(raw))
    base["quote_verified"] = verdict.verified
    base["quote_similarity"] = verdict.similarity
    base["quote_repaired"] = verdict.repaired
    base["evidence_quote"] = verdict.text if verdict.verified else ""
    if verdict.passage_id:
        base["passage_id"] = verdict.passage_id
        meta = _passage_meta(raw, verdict.passage_id)
        base["quote_section"] = meta.get("section", "")
        base["quote_source_file"] = meta.get("source_file", "")
        base["quote_chunk_id"] = meta.get("chunk_id") or ""
        base["quote_page"] = meta.get("page") if meta.get("page") is not None else ""

    if f["role"] in QUOTE_REQUIRED and not verdict.verified:
        reject = {**reject_base, "role": f["role"], "reason": verdict.reason,
                  "claimed_quote": (f["evidence_quote"] or "")[:300],
                  "best_passage_id": verdict.passage_id or "", "similarity": verdict.similarity}
        base["role"], base["demotion_reason"] = NOT_RELEVANT, f"{f['role']} without verified quote"
        return base, reject
    if f["role"] == "COMPARATOR" and not f["quantity"] and not verdict.verified:
        base["role"] = "BACKGROUND"
        base["demotion_reason"] = "COMPARATOR without a stated quantity or a verified quote"
    return base, None


def raw_files(work_dir: Path = WORK_DIR) -> list[Path]:
    """One raw file per lane (screen_raw.<lane>.jsonl) plus the legacy shared one."""
    return sorted(p for p in work_dir.glob("screen_raw*.jsonl"))


def finalize(work_dir: Path = WORK_DIR) -> tuple[pd.DataFrame, pd.DataFrame]:
    rows = [r for p in raw_files(work_dir) for r in load_raw(p)]
    judged, rejected = [], []
    for raw in rows:
        j, r = finalize_screen_row(raw)
        judged.append(j)
        if r:
            rejected.append(r)
    jf = pd.DataFrame(judged, columns=JUDGED_COLUMNS)
    rf = pd.DataFrame(rejected, columns=REJECTED_COLUMNS)
    # parquet needs one type per column: pages/years arrive as int or "".
    for col in ("quote_page", "year", "passage_id"):
        jf[col] = jf[col].apply(lambda v: "" if v is None or (isinstance(v, float) and pd.isna(v)) else str(v))
    jf["confidence"] = pd.to_numeric(jf["confidence"], errors="coerce").fillna(0.0)
    for col in ("quote_verified", "quote_repaired", "is_own_result", "parse_ok"):
        jf[col] = jf[col].astype(bool)
    jf.to_parquet(work_dir / JUDGED_FILE, index=False)
    rf.to_csv(work_dir / REJECTED_FILE, index=False)
    if len(jf):
        for model, grp in jf.groupby("model"):
            attempted = int(grp["role"].isin(QUOTE_REQUIRED).sum() + (grp["demotion_reason"].str.contains("without verified quote")).sum())
            rej = int(grp["demotion_reason"].str.contains("without verified quote").sum())
            rate = rej / attempted if attempted else 0.0
            level = logging.WARNING if rate > 0.15 else logging.INFO
            logger.log(level, "lane %s: %d/%d SUPPORTS/CONTRASTS quotes rejected (%.0f%%)",
                       model, rej, attempted, 100 * rate)
    return jf, rf


# ── quota ledger ──────────────────────────────────────────────────────────────

class Quota:
    def __init__(self, path: Path, cap: int = QUOTA_DAILY_CAP):
        self.path, self.cap = path, cap
        self.data = json.loads(path.read_text()) if path.exists() else {}

    def used(self, model: str) -> int:
        return int(self.data.get(model, {}).get(str(date.today()), 0))

    def allow(self, model: str) -> bool:
        return self.used(model) < self.cap

    def record(self, model: str) -> None:
        d = self.data.setdefault(model, {})
        d[str(date.today())] = d.get(str(date.today()), 0) + 1
        self.path.write_text(json.dumps(self.data, indent=1))


def pin_lane(lane: str) -> None:
    from src.dashboard_dash import ai_gateway
    ai_gateway.MODEL_POOL[:] = [lane]
    logger.info("Gemini pool pinned to %s", lane)


# ── the step ──────────────────────────────────────────────────────────────────

def run(claims: list[AtomicClaim], lane: str, limit: int | None = None, both_lanes: bool = False,
        llm: str = "gemini", finalize_only: bool = False, work_dir: Path = WORK_DIR,
        caller=None, delay: float = CALL_DELAY_SECONDS, passage_builder=None,
        shard: tuple[int, int] | None = None) -> int:
    if finalize_only:
        jf, rf = finalize(work_dir)
        logger.info("finalize: %d judged, %d rejected", len(jf), len(rf))
        _augment_search_log(jf, rf)
        return 0

    cand_path = work_dir / CANDIDATES_FILE
    if not cand_path.exists():
        raise FileNotFoundError(f"{cand_path} missing — run retrieve first")
    candidates = pd.read_parquet(cand_path)
    hits_path = work_dir / "semantic_hits.parquet"
    hits_all = pd.read_parquet(hits_path) if hits_path.exists() else None

    by_id = {c.atomic_id: c for c in claims}
    mask = candidates["screen_selected"].fillna(False) if "screen_selected" in candidates else candidates["prefilter_pass"].fillna(False)
    todo = candidates[mask & candidates["atomic_claim_id"].isin(by_id)]
    todo = todo.assign(_p=todo["priority"].map({"high": 0, "medium": 1, "low": 2}).fillna(3)).sort_values(["_p", "atomic_claim_id", "prefilter_score"], ascending=[True, True, False])
    if shard:
        k, n = shard
        todo = todo.iloc[[i for i in range(len(todo)) if i % n == k]]
        logger.info("shard %d/%d → %d pairs", k, n, len(todo))
    if llm != "gemini":
        lane = "ollama"          # the raw record must name the model that answered, never a Gemini lane
    raw_path = work_dir / f"screen_raw.{lane}.jsonl"
    already = [r for p in raw_files(work_dir) for r in load_raw(p)]
    done_any = {(r.get("atomic_claim_id"), r.get("paper_id")) for r in already}
    done_lane = {(r.get("atomic_claim_id"), r.get("paper_id")) for r in already if r.get("lane") == lane}

    api_key = ""
    if llm == "gemini":
        api_key = resolve_gemini_key()
        if not api_key:
            raise RuntimeError("No Gemini API key (GEMINI_API_KEY / GOOGLE_API_KEY)")
        pin_lane(lane)
    if caller is None:
        caller = (lambda prompt: call_gemini(prompt, api_key)) if llm == "gemini" else (lambda prompt: call_ollama(prompt))
    quota = Quota(work_dir / f"quota_ledger.{lane}.json", cap=QUOTA_DAILY_CAP if llm == "gemini" else 10**6)
    model_key = lane if llm == "gemini" else "ollama"

    written = skipped = no_passages = failed = 0
    with raw_path.open("a", encoding="utf-8") as handle:
        for row in todo.itertuples():
            key = (row.atomic_claim_id, row.paper_id)
            if written and written % 20 == 0 and not both_lanes:
                # another lane may have screened this pair meanwhile — refresh cheaply
                done_any = {(r.get("atomic_claim_id"), r.get("paper_id")) for p in raw_files(work_dir) for r in load_raw(p)}
            if key in done_lane or (key in done_any and not both_lanes):
                skipped += 1
                continue
            if limit and written >= limit:
                break
            if not quota.allow(model_key):
                logger.warning("quota cap %d reached for %s today — stopping lane cleanly", quota.cap, model_key)
                break
            claim = by_id[row.atomic_claim_id]
            thesis = claim.to_thesis()
            if passage_builder is not None:
                passages = passage_builder(row, claim)
            else:
                paper = load_paper_json(row.paper_id)
                if not paper:
                    logger.warning("%s/%s: paper not loadable", row.atomic_claim_id, row.paper_id)
                    continue
                passages = build_passages(paper_sections(paper), row.paper_id, f"{row.paper_id}.json",
                                          thesis.key_terms, thesis.negative_terms, max_passages=MAX_PASSAGES)
                if hits_all is not None:
                    h = hits_all[(hits_all["atomic_claim_id"] == row.atomic_claim_id) & (hits_all["paper_id"] == row.paper_id)]
                    passages = attach_chunk_provenance(passages, h)
            if not passages:
                no_passages += 1
                continue
            meta = {"title": row.title, "year": row.year, "doi": row.doi, "journal": ""}
            prompt = build_prompt(claim, meta, passages)
            text, model = caller(prompt)
            quota.record(model_key)
            if llm == "gemini":
                time.sleep(delay)
            if not text.strip():
                failed += 1
                if failed >= 3 and failed % 3 == 0:
                    logger.warning("%d consecutive/aggregate failures — backing off %ds (circuit breaker?)",
                                   failed, CIRCUIT_BACKOFF_SECONDS)
                    time.sleep(CIRCUIT_BACKOFF_SECONDS)
                continue
            handle.write(json.dumps({
                "atomic_claim_id": row.atomic_claim_id, "thesis_id": claim.thesis_id,
                "paper_id": row.paper_id, "doi": row.doi, "title": row.title, "year": row.year,
                "model": model, "lane": lane, "evidence_level": "full_text",
                "role_vocabulary": ROLE_VOCABULARY, "quote_source_authority": "normalized_json",
                "prompt_sha256": prompt_sha256(prompt), "raw_response": text,
                "passages_offered": [{"passage_id": p.passage_id, "text": p.text, "section": p.section,
                                      "char_offset": p.char_offset, "source_file": p.source_file,
                                      "chunk_id": p.chunk_id, "page": p.page} for p in passages],
                "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }, ensure_ascii=False) + "\n")
            handle.flush()
            written += 1
            if written % 25 == 0:
                logger.info("  … %d written (%d skipped, %d no passages, %d failed; quota %d/%d)",
                            written, skipped, no_passages, failed, quota.used(model_key), quota.cap)
    logger.info("screen[%s]: %d written, %d skipped, %d no passages, %d failed", lane, written, skipped,
                no_passages, failed)
    jf, rf = finalize(work_dir)
    _augment_search_log(jf, rf)
    return 0


def _augment_search_log(judged: pd.DataFrame, rejected: pd.DataFrame, log_path: Path = SEARCH_LOG) -> None:
    """Rewrite 04_search_log.jsonl with retained/rejected per atomic claim."""
    if not log_path.exists():
        return
    retained = {}
    if len(judged):
        for cid, grp in judged[judged["role"] != NOT_RELEVANT].groupby("atomic_claim_id"):
            retained[cid] = [{"paper_id": r.paper_id, "role": r.role, "quote_verified": bool(r.quote_verified)}
                             for r in grp.itertuples()]
    rej = {}
    if len(rejected):
        for cid, grp in rejected.groupby("atomic_claim_id"):
            rej[cid] = [{"paper_id": r.paper_id, "reason": r.reason} for r in grp.itertuples()]
    not_rel = {}
    if len(judged):
        for cid, grp in judged[judged["role"] == NOT_RELEVANT].groupby("atomic_claim_id"):
            not_rel[cid] = [r.paper_id for r in grp.itertuples()]
    lines = []
    for line in log_path.read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(line)
        except Exception:
            continue
        cid = rec.get("atomic_claim_id")
        rec["screen"] = {"retained": retained.get(cid, []), "rejected_quote": rej.get(cid, []),
                         "not_relevant": not_rel.get(cid, [])}
        lines.append(json.dumps(rec, ensure_ascii=False))
    log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
