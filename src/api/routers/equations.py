"""Equations, quantities and laws: the equation-centric graph, read-only
(docs/api/endpoints/equations.md)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request

from src.api.deps import provenance, require_scope
from src.api.documentation import route_doc
from src.api.problems import Problem
from src.api.routers.graph import _call
from src.contracts.equations import (
    ChainResponse, EquationResponse, EquationSearchResponse, LawResponse, LawsResponse, LawStatus,
    NormalizeRequest, NormalizeResponse, QuantitiesResponse, QuantityResponse,
)

router = APIRouter()


async def _eq_call(fn, *args, **kwargs):
    from src.services import equation_read as er
    try:
        return await _call(fn, *args, **kwargs)
    except er.Unresolved as exc:                      # a name that maps to nothing, an unknown law
        raise Problem("VALIDATION_FAILED", str(exc), errors=exc.errors) from exc


@router.get("/equations/search", response_model=EquationSearchResponse, **route_doc("GET", "/equations/search"))
async def search(request: Request, quantity: str | None = None, law: str | None = None,
                 law_status: LawStatus = "accepted", paper: str | None = None,
                 structural_hash: str | None = None, q: str | None = Query(None, min_length=2, max_length=200),
                 has_code: bool = False, limit: int = Query(50, ge=1, le=500), cursor: str | None = None,
                 _=Depends(require_scope("read"))) -> EquationSearchResponse:
    from src.services import equation_read as er
    from src.services import graph_read as g
    offset = await _call(g.decode_cursor, cursor)
    body = await _eq_call(er.search, quantity=quantity, law=law, law_status=law_status, paper=paper,
                          structural_hash=structural_hash, q=q, has_code=has_code, limit=limit, offset=offset)
    return EquationSearchResponse(**body, provenance=provenance(request))


@router.get("/equations/{eq_id:path}/chain", response_model=ChainResponse,
            **route_doc("GET", "/equations/{eq_id}/chain"))
async def chain(request: Request, eq_id: str, _=Depends(require_scope("read"))) -> ChainResponse:
    from src.services import equation_read as er
    body = await _eq_call(er.chain, eq_id)
    if body is None:
        raise Problem("NOT_FOUND", f"no equation {eq_id!r} in the graph")
    return ChainResponse(**body, provenance=provenance(request))


@router.get("/equations/{eq_id:path}", response_model=EquationResponse, **route_doc("GET", "/equations/{eq_id}"))
async def equation(request: Request, eq_id: str,
                   include: str = Query("parameters,laws,equivalents,code", description="comma list"),
                   _=Depends(require_scope("read"))) -> EquationResponse:
    from src.services import equation_read as er
    parts = tuple(x.strip() for x in include.split(",") if x.strip())
    unknown = sorted(set(parts) - set(er.EQ_PARTS))
    if unknown:
        raise Problem("BAD_REQUEST", f"unknown include values {unknown}; allowed: {list(er.EQ_PARTS)}")
    body = await _eq_call(er.equation, eq_id, parts)
    if body is None:
        raise Problem("NOT_FOUND", f"no equation {eq_id!r} in the graph")
    return EquationResponse(**body, provenance=provenance(request))


@router.get("/quantities", response_model=QuantitiesResponse, **route_doc("GET", "/quantities"))
async def quantities(request: Request, q: str | None = None,
                     kind: str | None = Query(None, pattern="^(physical|statistical|model|mathematical)$"),
                     limit: int = Query(200, ge=1, le=500), _=Depends(require_scope("read"))) -> QuantitiesResponse:
    from src.services import equation_read as er
    body = await _eq_call(er.quantities, q, kind, limit)
    return QuantitiesResponse(**body, provenance=provenance(request))


@router.post("/quantities/normalize", response_model=NormalizeResponse, **route_doc("POST", "/quantities/normalize"))
async def normalize(request: Request, body: NormalizeRequest, _=Depends(require_scope("read"))) -> NormalizeResponse:
    from starlette.concurrency import run_in_threadpool

    from src.services import equation_read as er
    out = await run_in_threadpool(er.normalize, [i.model_dump() for i in body.items])
    return NormalizeResponse(**out, provenance=provenance(request))


@router.get("/quantities/{quantity_id}", response_model=QuantityResponse, **route_doc("GET", "/quantities/{quantity_id}"))
async def quantity(request: Request, quantity_id: str, _=Depends(require_scope("read"))) -> QuantityResponse:
    from src.services import equation_read as er
    body = await _eq_call(er.quantity, quantity_id)
    if body is None:
        raise Problem("NOT_FOUND", f"no quantity concept {quantity_id!r}; see GET /v1/quantities")
    return QuantityResponse(**body, provenance=provenance(request))


@router.get("/laws", response_model=LawsResponse, **route_doc("GET", "/laws"))
async def laws(request: Request, _=Depends(require_scope("read"))) -> LawsResponse:
    from src.services import equation_read as er
    body = await _eq_call(er.laws)
    return LawsResponse(**body, provenance=provenance(request))


@router.get("/laws/{law_id}", response_model=LawResponse, **route_doc("GET", "/laws/{law_id}"))
async def law(request: Request, law_id: str, status: LawStatus = "accepted",
              limit: int = Query(50, ge=1, le=500), cursor: str | None = None,
              _=Depends(require_scope("read"))) -> LawResponse:
    from src.services import equation_read as er
    from src.services import graph_read as g
    offset = await _call(g.decode_cursor, cursor)
    body = await _eq_call(er.law, law_id, status, limit, offset)
    if body is None:
        raise Problem("NOT_FOUND", f"no law {law_id!r}; see GET /v1/laws")
    return LawResponse(**body, provenance=provenance(request))
