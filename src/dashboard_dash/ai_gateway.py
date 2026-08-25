"""
ai_gateway.py  —  Fault-tolerant Gemini wrapper for GeoHydroAI dashboard.

Adapts the project's Django AI Gateway pattern to plain Python (no Django cache).
Circuit breaker uses a module-level dict instead of Redis.

Responsibility: hide all API-level complexity from callers.
- MODEL_POOL with priority fallback
- Precise error classification (404/429/503/timeout/unknown)
- In-memory circuit breaker (5 failures → 300 s cool-down)
- Returns structured GatewayResult dict; never raises
- build_research_prompt() builds evidence-grounded prompts
"""
from __future__ import annotations

import logging
import time
from typing import Any

log = logging.getLogger(__name__)

try:
    from google import genai as _genai
except ImportError:
    _genai = None  # type: ignore[assignment]

# ── Model pool ────────────────────────────────────────────────────────────────
MODEL_POOL: list[str] = [
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-2.0-flash",
    "gemini-2.0-flash-lite",
]

_HTTP_TIMEOUT_MS = 60_000

# ── Error signal tables ───────────────────────────────────────────────────────
_SIGNALS_404     = ("404", "not_found", "invalid_argument", "not found", "model not found", "is not found")
_SIGNALS_429     = ("429", "resource_exhausted", "quota", "rate limit", "too many", "rate_limit_exceeded")
_SIGNALS_503     = ("503", "unavailable", "high demand", "service unavailable")
_SIGNALS_TIMEOUT = ("timeout", "timed out", "deadline", "read timeout")

# ── In-memory circuit breaker ─────────────────────────────────────────────────
_cb: dict[str, Any] = {"failures": 0, "open_until": 0.0}
_CIRCUIT_THRESHOLD = 5
_CIRCUIT_TTL       = 300  # seconds


def _circuit_is_open() -> bool:
    return time.monotonic() < _cb["open_until"]


def _circuit_record_failure() -> None:
    _cb["failures"] += 1
    if _cb["failures"] >= _CIRCUIT_THRESHOLD:
        _cb["open_until"] = time.monotonic() + _CIRCUIT_TTL
        log.error("[CIRCUIT] breaker OPEN after %d failures — AI skipped for %ds",
                  _cb["failures"], _CIRCUIT_TTL)


def _circuit_record_success() -> None:
    _cb["failures"]   = 0
    _cb["open_until"] = 0.0


# ── Error classification ──────────────────────────────────────────────────────

def classify_ai_error(exc: Exception, model: str = "") -> dict:
    msg  = str(exc).lower()
    base = {"model": model, "retryable": True}
    if any(s in msg for s in _SIGNALS_404):
        return {**base, "status_code": 404, "reason": "invalid_model"}
    if any(s in msg for s in _SIGNALS_429):
        return {**base, "status_code": 429, "reason": "quota_exhausted"}
    if any(s in msg for s in _SIGNALS_503):
        return {**base, "status_code": 503, "reason": "high_demand"}
    if any(s in msg for s in _SIGNALS_TIMEOUT):
        return {**base, "status_code": 408, "reason": "timeout"}
    return {**base, "status_code": 0, "reason": "unknown"}


# ── Result helpers ────────────────────────────────────────────────────────────

def _fail_result(attempts: list, reason: str) -> dict:
    return {"ok": False, "text": "", "model_used": None,
            "attempts": attempts, "fallback": True, "fallback_reason": reason}


# ── Core gateway ──────────────────────────────────────────────────────────────

def call_ai_with_gateway(prompt: str, config_kwargs: dict, api_key: str) -> dict:
    """
    Try every model in MODEL_POOL and return a structured GatewayResult dict.
    Never raises.

    Returns:
        {"ok": bool, "text": str, "model_used": str|None,
         "attempts": list[dict], "fallback": bool, "fallback_reason": str}
    """
    if _genai is None:
        log.error("[AI_GATEWAY] google-genai SDK not installed")
        return _fail_result([], "sdk_missing")

    if not api_key:
        log.error("[AI_GATEWAY] GEMINI_API_KEY not set")
        return _fail_result([], "no_api_key")

    if _circuit_is_open():
        log.warning("[AI_GATEWAY] circuit breaker OPEN — skipping all AI calls")
        return _fail_result([], "circuit_open")

    n      = len(MODEL_POOL)
    client = _genai.Client(
        api_key=api_key,
        http_options=_genai.types.HttpOptions(timeout=_HTTP_TIMEOUT_MS),
    )
    attempts: list[dict] = []

    for idx, model in enumerate(MODEL_POOL, start=1):
        log.info("[AI_GATEWAY] attempt=%d/%d model=%s", idx, n, model)
        t0 = time.monotonic()
        try:
            response = client.models.generate_content(
                model=model,
                contents=prompt,
                config=_genai.types.GenerateContentConfig(**config_kwargs),
            )
            raw_text   = response.text
            latency_ms = int((time.monotonic() - t0) * 1000)

            try:
                finish = str(response.candidates[0].finish_reason)
                if "MAX_TOKENS" in finish:
                    log.error("[AI_GATEWAY] TRUNCATED by %s (%d chars) — trying next", model, len(raw_text))
                    attempts.append({"model": model, "status": "failed",
                                     "reason": "truncated", "latency_ms": latency_ms})
                    continue
            except (AttributeError, IndexError):
                finish = "unknown"

            log.info("[AI_GATEWAY] success model=%s latency_ms=%d chars=%d", model, latency_ms, len(raw_text))
            _circuit_record_success()
            attempts.append({"model": model, "status": "success", "latency_ms": latency_ms})
            return {"ok": True, "text": raw_text, "model_used": model,
                    "attempts": attempts, "fallback": False}

        except Exception as exc:
            latency_ms = int((time.monotonic() - t0) * 1000)
            err        = classify_ai_error(exc, model)
            attempts.append({"model": model, "status": "failed",
                              "reason": err["reason"], "status_code": err["status_code"],
                              "latency_ms": latency_ms})
            log.warning("[AI_GATEWAY] failed model=%s reason=%s latency_ms=%d",
                        model, err["reason"], latency_ms)
            if idx < n:
                log.info("[AI_GATEWAY] switching from %s → next model (%d remaining)", model, n - idx)

    _circuit_record_failure()
    log.error("[AI_GATEWAY] all %d models exhausted", n)
    return _fail_result(attempts, "all_models_failed")


# ── Evidence-grounded prompt builder ─────────────────────────────────────────

def build_research_prompt(evidence_pack: dict) -> str:
    """
    Serialize a provenance-aware evidence_pack into a structured Gemini prompt.
    Every evidence item includes doi when available.
    Gemini is instructed to cite DOIs and never hallucinate.
    """
    q        = evidence_pack.get("query") or "(no specific query)"
    filters  = evidence_pack.get("filters") or {}
    corpus   = evidence_pack.get("corpus_summary") or evidence_pack.get("corpus") or {}
    metrics  = evidence_pack.get("metrics")
    domains  = evidence_pack.get("domains")
    inst     = evidence_pack.get("institutions")
    authors  = evidence_pack.get("authors")
    nf_ev    = evidence_pack.get("numeric_fact_evidence") or []
    graph    = evidence_pack.get("graph_evidence") or evidence_pack.get("graph") or []
    semantic = evidence_pack.get("semantic_evidence") or evidence_pack.get("semantic") or []
    abstracts= evidence_pack.get("abstract_context") or []
    limits   = evidence_pack.get("limitations") or []

    lines: list[str] = []

    # ── Hard anti-hallucination instruction (first, highest priority) ─────────
    lines += [
        "STRICT SYNTHESIS RULES — READ BEFORE EVERYTHING ELSE:",
        "1. Use ONLY the evidence sections below. Do not invent papers, DOIs, authors,",
        "   metrics, countries, or methods not present in the evidence.",
        "2. If evidence is insufficient to support a claim, write exactly:",
        '   "The available evidence is insufficient to support this claim."',
        "3. Every quantitative claim must cite its source section in parentheses,",
        "   e.g. (per NUMERIC FACTS) or (per GRAPH EVIDENCE).",
        "4. When a DOI is shown in the evidence, cite it for important claims.",
        "   Use format: [doi:10.xxxx/...]. Cite at least 3 DOIs when available.",
        "5. Do not cite doi=None or missing DOIs.",
        "6. Produce a PROVENANCE section at the end listing each major claim,",
        "   its supporting DOI(s), source layer, and confidence.",
        "7. Produce a LIMITATIONS section listing known evidence gaps.",
        "",
        f"RESEARCH QUERY: {q}",
        "",
        "ACTIVE FILTERS:",
        f"  Year range:    {filters.get('year_min', 'any')} – {filters.get('year_max', 'any')}",
        f"  Countries:     {', '.join(filters.get('countries') or []) or 'all'}",
        f"  Topics:        {', '.join(filters.get('topics') or []) or 'all'}",
        f"  Domains:       {', '.join(filters.get('domains') or []) or 'all'}",
        f"  Metrics:       {', '.join(filters.get('metrics') or []) or 'all'}",
        f"  Min citations: {filters.get('min_citations', 0)}",
        "",
    ]

    # ── Corpus overview ───────────────────────────────────────────────────────
    lines += ["═" * 60, "CORPUS OVERVIEW (DuckDB / Parquet)"]
    lines += [
        f"  Papers:  {corpus.get('papers', '?')}",
        f"  Authors: {corpus.get('authors', '?')}",
        f"  Topics:  {corpus.get('topics', '?')}",
    ]
    tc = corpus.get("top_countries")
    if tc is not None and hasattr(tc, "iterrows"):
        lines.append("  Top countries (by paper count):")
        for _, r in tc.head(8).iterrows():
            lines.append(f"    {r.get('country', '?'):20s} {int(r.get('papers', 0)):>5} papers")
    lines.append("")

    # ── Abstract context ──────────────────────────────────────────────────────
    if abstracts:
        lines += ["═" * 60, "ABSTRACT CONTEXT (selected papers: semantic + quantitative + scientometric)"]
        for ab in abstracts[:12]:
            doi  = ab.get("doi") or "doi=unavailable"
            title = (ab.get("title") or "")[:80]
            yr   = ab.get("year", "")
            cat  = ab.get("source_category", "")
            text = (ab.get("abstract") or "").replace("\n", " ")
            lines.append(f"  [doi:{doi}  year={yr}  category={cat}]")
            lines.append(f"  Title: {title}")
            lines.append(f"  Abstract: {text}")
            lines.append("")

    # ── Numeric fact evidence (individual measurements) ───────────────────────
    if nf_ev:
        lines += ["═" * 60, "NUMERIC FACT EVIDENCE (individual measurements · DuckDB / Parquet)"]
        lines.append(f"  {'Metric':12s}  {'Value':>8}  {'Conf':>5}  {'DOI':40s}  Title")
        for fact in nf_ev[:20]:
            doi   = fact.get("doi") or "—"
            title = (fact.get("title") or "")[:40]
            metric = str(fact.get("metric", "")).replace("metric.", "").upper()
            val   = fact.get("value")
            conf  = fact.get("confidence")
            val_s = f"{float(val):.3f}" if val is not None else "—"
            conf_s = f"{float(conf):.2f}" if conf is not None else "—"
            lines.append(f"  {metric:12s}  {val_s:>8}  {conf_s:>5}  {doi[:40]:40s}  {title}")
        lines.append("")

    # ── Metric distribution (aggregate) ──────────────────────────────────────
    if metrics is not None and hasattr(metrics, "iterrows") and not metrics.empty:
        lines += ["═" * 60, "METRIC DISTRIBUTION AGGREGATE (NumericFacts / DuckDB)"]
        lines.append(f"  {'Metric':28s}  {'n':>5}  {'mean':>8}  {'std':>8}  {'%exc':>6}")
        for _, r in metrics.head(10).iterrows():
            pct = f"{r.get('pct_excellent', 0)*100:.1f}%" if r.get('pct_excellent') is not None else "—"
            lines.append(
                f"  {str(r.get('metric','?')):28s}  {int(r.get('n',0)):5d}"
                f"  {float(r.get('mean',0)):8.3f}  {float(r.get('std',0)):8.3f}  {pct:>6}"
            )
        lines.append("")

    # ── Domain performance ────────────────────────────────────────────────────
    if domains is not None and hasattr(domains, "iterrows") and not domains.empty:
        lines += ["═" * 60, "DOMAIN PERFORMANCE (Neo4j / NSE)"]
        for _, r in domains.iterrows():
            lines.append(
                f"  {str(r.get('domain','?')):28s}  n={int(r.get('n',0)):4d}"
                f"  avg_NSE={float(r.get('avg_nse',0)):.3f}"
                f"  %excellent={float(r.get('pct_excellent',0))*100:.1f}%"
            )
        lines.append("")

    # ── Institution performance ───────────────────────────────────────────────
    if inst is not None and hasattr(inst, "iterrows") and not inst.empty:
        lines += ["═" * 60, "INSTITUTION PERFORMANCE (Neo4j)"]
        for _, r in inst.head(10).iterrows():
            lines.append(
                f"  {str(r.get('institution','?'))[:40]:40s} ({r.get('country','??')})"
                f"  papers={int(r.get('papers',0))}  avg_NSE={float(r.get('avg_nse',0)):.3f}"
                f"  %exc={float(r.get('pct_excellent',0))*100:.0f}%"
            )
        lines.append("")

    # ── Author influence ──────────────────────────────────────────────────────
    if authors is not None and hasattr(authors, "iterrows") and not authors.empty:
        lines += ["═" * 60, "AUTHOR INFLUENCE (DuckDB / OpenAlex)"]
        for _, r in authors.head(8).iterrows():
            h   = r.get("h_index")
            cit = r.get("cited_by_count")
            lines.append(
                f"  {str(r.get('display_name','?')):35s}"
                f"  papers={int(r.get('paper_count',0))}"
                f"  h={h if h is not None else '?'}"
                f"  citations={cit if cit is not None else '?'}"
            )
        lines.append("")

    # ── Graph evidence (with DOI) ─────────────────────────────────────────────
    if graph:
        lines += ["═" * 60, "GRAPH EVIDENCE (Neo4j)"]
        for item in graph[:15]:
            if isinstance(item, dict):
                t = item.get("type", "")
                doi = item.get("doi") or "—"
                if t == "top_nse_paper":
                    lines.append(
                        f"  NSE={item.get('avg_nse', 0):.3f}"
                        f"  doi:{doi}"
                        f"  year={item.get('year', '?')}"
                        f"  title={str(item.get('title',''))[:60]}"
                    )
                elif t == "metric_count":
                    lines.append(
                        f"  metric={item.get('metric','?')}  n={item.get('n','?')}"
                        f"  mean={item.get('mean','?')}"
                    )
                else:
                    lines.append(f"  {item}")
            else:
                lines.append(f"  {item}")
        lines.append("")

    # ── Semantic evidence (with DOI and title) ────────────────────────────────
    if semantic:
        lines += ["═" * 60, "SEMANTIC EVIDENCE (ChromaDB / SPECTER2)"]
        for hit in semantic[:8]:
            pid   = hit.get("paper_id", "?")
            doi   = hit.get("doi") or "—"
            title = (hit.get("title") or "")[:50]
            sec   = hit.get("section_title", "")
            score = hit.get("score", 0)
            text  = (hit.get("chunk_text") or hit.get("text", ""))[:280].replace("\n", " ")
            lines.append(f"  [paper={pid}  doi:{doi}  section={sec}  score={score:.3f}]")
            lines.append(f"  Title: {title}")
            lines.append(f"  \"{text}\"")
            lines.append("")

    # ── Known limitations ─────────────────────────────────────────────────────
    if limits:
        lines += ["═" * 60, "KNOWN LIMITATIONS (do not ignore these)"]
        for lim in limits:
            lines.append(f"  - {lim}")
        lines.append("")

    # ── Task instructions ─────────────────────────────────────────────────────
    lines += [
        "═" * 60,
        "TASK: Based ONLY on the evidence above, produce a structured synthesis:",
        "",
        "1. EXECUTIVE SUMMARY (3 sentences): what does this filtered corpus show?",
        "2. GEOGRAPHIC INSIGHTS: which regions dominate and in what domain?",
        "3. METHODOLOGICAL TRENDS: dominant approaches and their performance?",
        "4. MODEL QUALITY ASSESSMENT: NSE distribution, standout institutions?",
        "5. RESEARCH GAPS: what is missing or underrepresented in the evidence?",
        "6. CONFIDENCE: rate overall evidence quality (HIGH / MEDIUM / LOW) and explain why.",
        "7. PROVENANCE: for each major claim, list — claim | DOI(s) | source layer | confidence",
        "8. LIMITATIONS: list evidence gaps and what cannot be concluded from this corpus.",
        "",
        "FINAL REMINDER:",
        "- Cite specific numbers from the evidence tables.",
        "- Use [doi:...] format for DOI citations (only from evidence, never invented).",
        "- If a section is empty, write: 'No evidence available for this aspect.'",
        "- Do not invent any paper, metric, method, institution, or author.",
    ]

    return "\n".join(lines)
