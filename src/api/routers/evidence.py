"""Evidence: is a quotation in its source; is a theses document valid (docs/api/endpoints/evidence.md)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from starlette.concurrency import run_in_threadpool

from src.api.deps import provenance, require_scope
from src.api.documentation import route_doc
from src.api.problems import Problem
from src.contracts.api import (
    OpenCitationsQuote, QuoteItem, QuoteVerifyRequest, QuoteVerifyResponse, ThesesValidateRequest,
    ThesesValidateResponse,
)
from src.paper_3.evidence import MIN_QUOTE_CHARS

router = APIRouter()
MAX_SYNC_QUOTES = 50


@router.post("/quotes/verify", response_model=QuoteVerifyResponse, **route_doc("POST", "/quotes/verify"))
async def quotes_verify(request: Request, body: QuoteVerifyRequest,
                        _=Depends(require_scope("read"))) -> QuoteVerifyResponse:
    from src.services import quotes
    if body.acquire_missing:
        raise Problem("NOT_IMPLEMENTED", "acquire_missing needs acquisition jobs (phase 2); call without it and "
                      "treat SOURCE_UNAVAILABLE as 'not checked'", extra={"docs": "/v1/docs/pages/endpoints/acquisition"})
    items: list[QuoteItem] = []
    not_checked, errors = [], []
    for i, it in enumerate(body.items):
        if isinstance(it, OpenCitationsQuote):
            got, skipped = quotes.expand_open_citations(it.open_citations_item, body.project_id)
            items += got
            not_checked += skipped
        elif quotes.too_short(it.quote):
            errors.append({"loc": ["body", "items", i, "quote"], "type": "too_short",
                           "msg": f"the quotation (or its longest '…' fragment) is shorter than {MIN_QUOTE_CHARS} "
                                  "characters: too common to prove anything"})
        else:
            items.append(it)
    if errors:
        raise Problem("VALIDATION_FAILED", f"{len(errors)} quotation(s) too short; nothing was checked", errors=errors)
    if len(items) > MAX_SYNC_QUOTES:
        raise Problem("PAYLOAD_TOO_LARGE", f"{len(items)} quotations after expansion; at most {MAX_SYNC_QUOTES} per "
                      "request until asynchronous jobs exist (phase 2): split the request")
    try:
        results = await run_in_threadpool(lambda: [quotes.verify_one(x, body.project_id) for x in items])
        results = quotes.cross_reference(items, results)
    except Problem:
        raise
    except Exception as exc:
        raise Problem("STORE_UNAVAILABLE", f"postgres or TEI store: {type(exc).__name__}",
                      headers={"Retry-After": "30"}) from exc
    return QuoteVerifyResponse(items=results, not_checked=not_checked, summary=quotes.summarise(results),
                               provenance=provenance(request))


@router.post("/theses/validate", response_model=ThesesValidateResponse, **route_doc("POST", "/theses/validate"))
async def theses_validate(request: Request, body: ThesesValidateRequest,
                          _=Depends(require_scope("read"))) -> ThesesValidateResponse:
    from src.services import thesis_validation as tv
    try:
        doc = tv.parse(body.document)
    except ValueError as exc:
        raise Problem("VALIDATION_FAILED", str(exc),
                      errors=[{"loc": ["document"], "msg": str(exc), "type": "parse_error"}]) from exc
    if body.kind == "theses":
        report = tv.validate_theses(doc, body.project_id, body.authored_by)
    else:
        known, note = await run_in_threadpool(tv.project_theses, body.project_id)
        report = tv.validate_atomic_claims(doc, body.project_id, body.authored_by, known)
        if note:
            report.warnings.append(note)
    if not report.valid:
        raise Problem("VALIDATION_FAILED", f"{len(report.errors)} problem(s); the document violates contract v1 and "
                      "nothing was coerced", errors=report.errors,
                      extra={"counts": dict(report.counts), "warnings": report.warnings})
    return ThesesValidateResponse(valid=True, counts=dict(report.counts), warnings=report.warnings,
                                  provenance=provenance(request))
