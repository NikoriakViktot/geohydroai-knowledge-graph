"""RAW phase — ask the model, record the answer, judge nothing.

This module makes LLM calls and appends one JSON line per (thesis, paper, model)
to `relations_raw.jsonl`. It applies no thresholds, verifies no quotes, overwrites
nothing and skips triples it has already recorded, so an interrupted run costs only
the calls it had not yet made.

Splitting the raw record from the judged output is what made the Article-1 audit
defensible: `finalize_relations.py` is deterministic on top of this file, so the
verdicts can be recomputed after a rule changes without paying for the model again.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from src.paper_3._utils import OUT_DIR, REFUSAL, load_paper_json, paper_sections, resolve_gemini_key
from src.paper_3.evidence import EvidencePassage, build_passages
from src.paper_3.theses import NOT_RELEVANT, RELATIONS, SYSTEM_CLASSES, Thesis, load_theses

logger = logging.getLogger(__name__)

RAW_FILE = "relations_raw.jsonl"

#: Seconds between model calls. The gateway retries across its model pool with
#: no delay at all, so an unpaced run fires hundreds of requests per minute,
#: collects HTTP 429, and trips the circuit breaker within seconds — which is a
#: rate limit being mistaken for an exhausted quota. Free-tier Gemini allows
#: roughly 15 requests per minute.
CALL_DELAY_SECONDS = 4.5

_GENERATION_CONFIG = {
    "temperature": 0.0,
    "max_output_tokens": 800,
    "response_mime_type": "application/json",
}

#: Relation definitions, given to the model verbatim. CONTRADICTS is listed first
#: so the cheapest reading of the list is not the one that agrees with us.
_DEFINITIONS = """RELATION DEFINITIONS
CONTRADICTS     - this paper reports a finding that is inconsistent with, or
                  explicitly rejects, the thesis. Disagreement about magnitude
                  counts. Silence does not.
SUPPORTS        - this paper reports a finding or measurement consistent with
                  the thesis.
METHOD_RELEVANT - the paper does not test the thesis but supplies a method,
                  dataset, product specification or error budget that the thesis
                  depends on.
ANALOGUE        - the paper establishes the same mechanism in a different system,
                  scale, sensor or region. Transferable, not direct evidence."""

PROMPT_TEMPLATE = """STRICT RULES - READ BEFORE EVERYTHING ELSE:
1. Judge ONLY from the PASSAGES below. Do not use outside knowledge about this
   paper, its authors, or the field.
2. evidence_quote MUST be copied character-for-character from ONE passage. Do not
   paraphrase, do not join two passages, do not correct typos. The quote is checked
   against the source automatically and a quote that fails the check is discarded.
3. If no passage supports any relation, set relation to "NOT_RELEVANT",
   evidence_quote to "", and rationale to exactly:
   "{refusal}"
4. Output a single JSON object and nothing else. No prose before or after.

THESIS {thesis_id} (block {block}): {statement}

{definitions}

PAPER: {title} ({year}, {journal}) doi:{doi}
EVIDENCE LEVEL: {evidence_level} — the passages below are {source_note}.

PASSAGES:
{passages}

Return exactly this JSON shape:
{{"relation": "CONTRADICTS|SUPPORTS|METHOD_RELEVANT|ANALOGUE|NOT_RELEVANT",
  "confidence": 0.0,
  "passage_id": "P1",
  "evidence_quote": "",
  "rationale": "at most 40 words",
  "system_class": "{systems}",
  "is_own_result": true}}

system_class is the water body THIS paper studied, or "not_stated" if it does not
say. is_own_result is true only if the finding is this paper's own result, false
if the paper is reporting what someone else found."""


#: What the model is being shown, so it does not read an abstract as if it were
#: the whole paper. A method claim in particular is usually invisible in an
#: abstract even when the paper makes it.
_SOURCE_NOTE = {
    "abstract": ("the paper's abstract only, as indexed by OpenAlex. Judge only "
                 "what the abstract states; absence from an abstract is not "
                 "absence from the paper"),
    "full_text": "sections of the paper's full text",
}


def build_prompt(thesis: Thesis, paper_meta: dict,
                 passages: list[EvidencePassage],
                 evidence_level: str = "full_text") -> str:
    return PROMPT_TEMPLATE.format(
        evidence_level=evidence_level,
        source_note=_SOURCE_NOTE.get(evidence_level, evidence_level),
        refusal=REFUSAL,
        thesis_id=thesis.id,
        block=thesis.block,
        statement=" ".join(thesis.statement.split()),
        definitions=_DEFINITIONS,
        title=paper_meta.get("title") or "(title unavailable)",
        year=paper_meta.get("year") or "n.d.",
        journal=paper_meta.get("journal") or "(journal unavailable)",
        doi=paper_meta.get("doi") or "(no DOI)",
        passages="\n\n".join(p.as_prompt_line() for p in passages),
        systems="|".join(SYSTEM_CLASSES),
    )


def prompt_sha256(prompt: str) -> str:
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()


# ── response parsing ──────────────────────────────────────────────────────────

_JSON_BLOCK = re.compile(r"\{.*\}", re.DOTALL)


def parse_response(text: str) -> dict | None:
    """Pull the JSON object out of a model response. None when unparseable.

    Models occasionally wrap JSON in a code fence despite being told not to, so
    the first balanced-looking block is extracted rather than trusting the whole
    string. Anything else is a parse failure and is recorded as such.
    """
    if not text or not text.strip():
        return None
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        pass
    match = _JSON_BLOCK.search(text)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


def normalise_parsed(parsed: dict) -> dict:
    """Coerce a parsed response into the fixed shape, without inventing values.

    An unrecognised relation becomes NOT_RELEVANT rather than being guessed at:
    a malformed answer is not evidence.
    """
    relation = str(parsed.get("relation", "")).strip().upper()
    if relation not in set(RELATIONS) | {NOT_RELEVANT}:
        relation = NOT_RELEVANT

    try:
        confidence = float(parsed.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = min(max(confidence, 0.0), 1.0)

    system = str(parsed.get("system_class", "not_stated")).strip().lower()
    if system not in SYSTEM_CLASSES:
        system = "not_stated"

    own = parsed.get("is_own_result")
    if not isinstance(own, bool):
        own = False

    return {
        "relation": relation,
        "confidence": confidence,
        "passage_id": str(parsed.get("passage_id", "") or ""),
        "evidence_quote": str(parsed.get("evidence_quote", "") or ""),
        "rationale": str(parsed.get("rationale", "") or ""),
        "system_class": system,
        "is_own_result": own,
    }


# ── model callers ─────────────────────────────────────────────────────────────

def call_gemini(prompt: str, api_key: str) -> tuple[str, str]:
    """(text, model_used). Empty text on failure — the gateway never raises."""
    from src.dashboard_dash.ai_gateway import call_ai_with_gateway

    result = call_ai_with_gateway(prompt, dict(_GENERATION_CONFIG), api_key)
    if not result.get("ok"):
        logger.warning("gateway failed: %s", result.get("fallback_reason", "unknown"))
        return "", result.get("model_used") or "gemini:failed"
    return result.get("text", ""), result.get("model_used") or "gemini"


def call_ollama(prompt: str, base_url: str | None = None,
                model: str | None = None, timeout: int = 120) -> tuple[str, str]:
    """Local fallback for when the Gemini quota is exhausted."""
    import os

    import requests

    base_url = base_url or os.getenv("OLLAMA_URL") or "http://localhost:11434"
    model = model or os.getenv("OLLAMA_MODEL") or "mistral-nemo:12b"
    try:
        r = requests.post(
            f"{base_url}/api/generate",
            json={"model": model, "prompt": prompt, "stream": False,
                  "format": "json", "options": {"temperature": 0.0}},
            timeout=timeout,
        )
        r.raise_for_status()
        return r.json().get("response", ""), f"ollama:{model}"
    except Exception as exc:
        logger.warning("ollama call failed: %s", exc)
        return "", f"ollama:{model}"


# ── the step ──────────────────────────────────────────────────────────────────

def load_raw(path: Path) -> list[dict]:
    """Read relations_raw.jsonl, skipping malformed lines rather than failing."""
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            logger.warning("skipping malformed raw line")
    return rows


def done_triples(rows: list[dict]) -> set[tuple[str, str, str, str]]:
    """Work already recorded, keyed including the evidence level.

    The level is part of the key on purpose: a paper adjudicated from its
    abstract must NOT be skipped when the full text later becomes available.
    That is what makes the full-text pass an upgrade rather than a no-op.
    """
    return {(r.get("thesis_id", ""), r.get("paper_id", ""), r.get("model", ""),
             r.get("evidence_level", "full_text"))
            for r in rows}


def run(
    candidates: pd.DataFrame | None = None,
    theses: list[Thesis] | None = None,
    out_dir: Path | None = None,
    llm: str = "gemini",
    limit: int | None = None,
    max_passages: int = 12,
    evidence_level: str = "full_text",
    quote_source_authority: str = "grobid_tei",
    passage_builder=None,
    delay: float = CALL_DELAY_SECONDS,
) -> Path:
    """Adjudicate every candidate that passed the prefilter.

    Appends to relations_raw.jsonl. Safe to re-run: triples already present are
    skipped, so a quota failure halfway through costs nothing on the retry.
    """
    theses = theses if theses is not None else load_theses()
    target = Path(out_dir) if out_dir else OUT_DIR
    target.mkdir(parents=True, exist_ok=True)
    raw_path = target / RAW_FILE

    if candidates is None:
        candidates_path = target / "thesis_candidates.parquet"
        if not candidates_path.exists():
            raise FileNotFoundError(
                f"{candidates_path} missing — run --step retrieve first")
        candidates = pd.read_parquet(candidates_path)

    api_key = ""
    if llm in ("gemini", "hybrid"):
        api_key = resolve_gemini_key()
        if not api_key:
            raise RuntimeError(
                "No Gemini API key: set GEMINI_API_KEY (or GOOGLE_API_KEY). "
                "Checked before any call so a misconfiguration is not discovered "
                "hundreds of calls in.")

    by_id = {t.id: t for t in theses}
    already = done_triples(load_raw(raw_path))
    todo = candidates[candidates["prefilter_pass"].fillna(False)]
    if limit:
        todo = todo.head(limit)

    logger.info("Adjudicating %d candidate pairs with %s at %s level "
                "(%d already recorded)",
                len(todo), llm, evidence_level, len(already))

    written = skipped = no_passages = failed = 0
    with raw_path.open("a", encoding="utf-8") as handle:
        for i, row in enumerate(todo.itertuples(), 1):
            thesis = by_id.get(row.thesis_id)
            if thesis is None:
                continue

            if passage_builder is not None:
                passages = passage_builder(row)
            else:
                paper = load_paper_json(row.paper_id)
                if not paper:
                    logger.warning("%s/%s: paper not loadable",
                                   row.thesis_id, row.paper_id)
                    continue
                passages = build_passages(
                    paper_sections(paper), row.paper_id, f"{row.paper_id}.json",
                    thesis.key_terms, thesis.negative_terms,
                    max_passages=max_passages)
            if not passages:
                no_passages += 1
                continue

            meta = {"title": row.title, "year": row.year,
                    "doi": row.doi, "journal": ""}
            prompt = build_prompt(thesis, meta, passages, evidence_level)

            if llm == "ollama":
                text, model = call_ollama(prompt)
            else:
                text, model = call_gemini(prompt, api_key)
                time.sleep(delay)

            if (row.thesis_id, row.paper_id, model, evidence_level) in already:
                skipped += 1
                continue

            # A failed call is not an answer. Persisting it would put an empty
            # response in the audit trail, which `finalize_row` turns into a
            # NOT_RELEVANT row — indistinguishable, downstream, from a model
            # that read the paper and found nothing.
            if not text.strip():
                failed += 1
                continue

            handle.write(json.dumps({
                "thesis_id": row.thesis_id,
                "paper_id": row.paper_id,
                "doi": row.doi,
                "title": row.title,
                "year": row.year,
                "model": model,
                "evidence_level": evidence_level,
                "quote_source_authority": quote_source_authority,
                "prompt_sha256": prompt_sha256(prompt),
                "raw_response": text,
                "passages_offered": [
                    {"passage_id": p.passage_id, "text": p.text,
                     "section": p.section, "char_offset": p.char_offset,
                     "source_file": p.source_file}
                    for p in passages
                ],
                "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }, ensure_ascii=False) + "\n")
            handle.flush()
            written += 1
            if i % 25 == 0:
                logger.info("  … %d/%d", i, len(todo))

    logger.info("Raw adjudication: %d written, %d skipped, %d had no usable "
                "passages, %d model calls failed", written, skipped,
                no_passages, failed)
    if failed:
        logger.warning("%d call(s) returned nothing and were NOT recorded — "
                       "re-run to retry them once the model is available again",
                       failed)
    return raw_path
