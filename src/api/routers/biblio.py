"""Bibliography: DOI metadata and verification against Crossref, OpenAlex and DataCite."""

from __future__ import annotations

from collections import Counter

from fastapi import APIRouter, Depends, Query, Request
from starlette.concurrency import run_in_threadpool

from src.api.deps import provenance, require_scope
from src.api.documentation import route_doc
from src.api.problems import Problem
from src.contracts.api import DoiResponse, DoiVerifyRequest, DoiVerifyResponse
from src.services.identity import normalize_doi

router = APIRouter()


def _store_error(exc: Exception) -> Problem:
    return Problem("STORE_UNAVAILABLE", f"postgres (registry cache): {type(exc).__name__}", headers={"Retry-After": "30"})


@router.get("/doi/{doi:path}", response_model=DoiResponse, **route_doc("GET", "/doi/{doi}"))
async def doi_metadata(request: Request, doi: str,
                       refresh: bool = Query(False, description="ignore cached registry answers"),
                       _=Depends(require_scope("read"))) -> DoiResponse:
    from src.services import doi as service
    d = normalize_doi(doi)
    if d is None:
        raise Problem("INVALID_DOI", f"not a DOI: {doi!r}")
    try:
        reg = await run_in_threadpool(service.lookup, d, refresh)
        meta = service.metadata(d, reg)
        in_corpus = await run_in_threadpool(service._in_corpus, d) if meta else None
    except Exception as exc:
        raise _store_error(exc) from exc
    if meta is None:
        if "unavailable" in reg.fetched.values():
            raise Problem("UPSTREAM_TIMEOUT", f"a registry did not answer for {d}; not cached, retry later",
                          headers={"Retry-After": "60"}, extra={"fetched": reg.fetched})
        raise Problem("NOT_FOUND", f"no registry knows {d} (Crossref, DataCite, OpenAlex)", extra={"fetched": reg.fetched})
    return DoiResponse(**meta.model_dump(), in_corpus=in_corpus, provenance=provenance(request))


@router.post("/doi/verify", response_model=DoiVerifyResponse, **route_doc("POST", "/doi/verify"))
async def doi_verify(request: Request, body: DoiVerifyRequest,
                     _=Depends(require_scope("read"))) -> DoiVerifyResponse:
    from src.services import doi as service
    try:
        results = await run_in_threadpool(service.verify_many, body.entries, body.project_id, body.refresh)
    except Exception as exc:
        raise _store_error(exc) from exc
    return DoiVerifyResponse(results=results, summary=dict(Counter(r.verdict for r in results)),
                             provenance=provenance(request))
