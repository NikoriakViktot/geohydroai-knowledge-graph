"""Papers: corpus membership and identity from the layer of truth."""

from __future__ import annotations

from collections import Counter

from fastapi import APIRouter, Depends, Query, Request
from starlette.concurrency import run_in_threadpool

from src.api.deps import provenance, require_scope
from src.api.documentation import route_doc
from src.api.problems import Problem
from src.contracts.api import (
    PaperRef, ResolveBatchRequest, ResolveBatchResponse, ResolveBatchResult, ResolveResponse,
)
from src.contracts.identity import PaperIdentity

router = APIRouter()
_INCLUDE = frozenset({"aliases", "files", "cohorts"})


def _ref(p: PaperIdentity | None) -> PaperRef | None:
    if p is None:
        return None
    return PaperRef(paper_id=p.paper_id, doi=p.doi, title=p.title, year=p.year, venue=p.venue,
                    identity_status=p.identity_status)


def _include(raw: str | None) -> frozenset[str]:
    wanted = frozenset(x.strip() for x in (raw or "").split(",") if x.strip())
    unknown = wanted - _INCLUDE
    if unknown:
        raise Problem("BAD_REQUEST", f"unknown include values {sorted(unknown)}; allowed: {sorted(_INCLUDE)}")
    return wanted


async def _resolve(**kwargs):
    from src.services import identity_store
    try:
        return await run_in_threadpool(lambda: identity_store.resolve(**kwargs))
    except identity_store.InvalidSelector as exc:
        code = "INVALID_DOI" if "not a DOI" in str(exc) else "BAD_REQUEST"
        raise Problem(code, str(exc)) from exc
    except Problem:
        raise
    except Exception as exc:
        raise Problem("STORE_UNAVAILABLE", f"postgres: {type(exc).__name__}", headers={"Retry-After": "30"}) from exc


@router.get("/papers/resolve", response_model=ResolveResponse, **route_doc("GET", "/papers/resolve"))
async def resolve(request: Request,
                  doi: str | None = None, paper_id: str | None = None, title: str | None = None,
                  year: int | None = None, file: str | None = None, openalex_id: str | None = None,
                  include: str | None = Query(None, description="comma list of aliases,files,cohorts"),
                  _=Depends(require_scope("read"))) -> ResolveResponse:
    found = await _resolve(doi=doi, paper_id=paper_id, file=file, openalex_id=openalex_id,
                           title=title, year=year, include=_include(include))
    if found is None:
        what = doi or paper_id or file or openalex_id or title
        raise Problem("NOT_IN_CORPUS", f"no paper in the corpus for {what!r}")
    return ResolveResponse(match=found.match, score=found.score, paper=found.paper,
                           canonical=_ref(found.canonical), provenance=provenance(request))


@router.post("/papers/resolve-batch", response_model=ResolveBatchResponse,
             **route_doc("POST", "/papers/resolve-batch"))
async def resolve_batch(request: Request, body: ResolveBatchRequest,
                        _=Depends(require_scope("read"))) -> ResolveBatchResponse:
    results: list[ResolveBatchResult] = []
    for item in body.items:
        selectors = {k: v for k, v in item.model_dump().items() if k not in ("key", "year") and v}
        if len(selectors) != 1:
            results.append(ResolveBatchResult(key=item.key, status="invalid",
                                              detail="give exactly one of doi, paper_id, file, openalex_id, title"))
            continue
        try:
            found = await _resolve(**selectors, year=item.year, include=frozenset())
        except Problem as exc:
            if exc.code in ("INVALID_DOI", "BAD_REQUEST"):
                results.append(ResolveBatchResult(key=item.key, status="invalid", detail=exc.detail))
                continue
            raise
        if found is None:
            results.append(ResolveBatchResult(key=item.key, status="not_in_corpus"))
        else:
            results.append(ResolveBatchResult(key=item.key, status="found", match=found.match,
                                              paper=_ref(found.paper), canonical=_ref(found.canonical)))
    summary = dict(Counter(r.status for r in results))
    return ResolveBatchResponse(results=results, summary=summary, provenance=provenance(request))


@router.get("/papers/{paper_id}", **route_doc("GET", "/papers/{paper_id}"))
async def paper(request: Request, paper_id: str, _=Depends(require_scope("read"))) -> dict:
    found = await _resolve(paper_id=paper_id, include=_INCLUDE)
    if found is None:
        raise Problem("NOT_FOUND", f"no paper with paper_id {paper_id!r}")
    body = found.paper.model_dump(mode="json")
    body["canonical"] = _ref(found.canonical).model_dump(mode="json") if found.canonical else None
    body["openalex"] = None  # planned (phase 1): OpenAlex enrichment fields
    body["provenance"] = provenance(request).model_dump(mode="json")
    return body
