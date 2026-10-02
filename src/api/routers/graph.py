"""Graph: the Neo4j projection, read-only (docs/api/endpoints/graph.md)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from starlette.concurrency import run_in_threadpool

from src.api.deps import provenance, require_scope
from src.api.documentation import route_doc
from src.api.problems import Problem
from src.contracts.api import (
    CitationsResponse, CypherRequest, EntityPapersResponse, GraphPaperResponse, GraphQueryRequest,
    NamedQueriesResponse, NamedQueryInfo, TabularResponse,
)

router = APIRouter()
_PARTS = ("authors", "methods", "sensors", "metrics", "topics", "countries", "facts")


async def _call(fn, *args, **kwargs):
    from neo4j.exceptions import Neo4jError, ServiceUnavailable

    from src.services import graph_read as g
    try:
        return await run_in_threadpool(lambda: fn(*args, **kwargs))
    except g.BadParams as exc:
        raise Problem("VALIDATION_FAILED", str(exc), errors=exc.errors) from exc
    except g.QueryTimeout as exc:
        raise Problem("UPSTREAM_TIMEOUT", str(exc)) from exc
    except g.NotReadOnly as exc:
        raise Problem("VALIDATION_FAILED", str(exc),
                      errors=[{"loc": ["body", "query"], "msg": str(exc), "type": "not_read_only"}]) from exc
    except (ServiceUnavailable, OSError) as exc:
        raise Problem("STORE_UNAVAILABLE", f"neo4j: {type(exc).__name__}", headers={"Retry-After": "30"}) from exc
    except Neo4jError as exc:
        raise Problem("BAD_REQUEST", f"neo4j: {exc.code}") from exc


@router.get("/graph/papers/{doi_or_paper_id:path}/citations", response_model=CitationsResponse,
            **route_doc("GET", "/graph/papers/{doi_or_paper_id}/citations"))
async def citations(request: Request, doi_or_paper_id: str,
                    direction: str = Query("out", pattern="^(out|in|both)$"),
                    in_corpus_only: bool = False,
                    limit: int = Query(100, ge=1, le=1000), cursor: str | None = None,
                    _=Depends(require_scope("read"))) -> CitationsResponse:
    from src.services import graph_read as g
    offset = await _call(g.decode_cursor, cursor)
    body = await _call(g.citations, doi_or_paper_id, direction, in_corpus_only, limit, offset)
    if body is None:
        raise Problem("NOT_FOUND", f"no paper {doi_or_paper_id!r} in the graph")
    return CitationsResponse(**body, provenance=provenance(request))


@router.get("/graph/papers/{doi_or_paper_id:path}", response_model=GraphPaperResponse,
            **route_doc("GET", "/graph/papers/{doi_or_paper_id}"))
async def paper(request: Request, doi_or_paper_id: str,
                include: str = Query("authors,methods,sensors,metrics,topics,countries",
                                     description=f"comma list of {', '.join(_PARTS)}"),
                _=Depends(require_scope("read"))) -> GraphPaperResponse:
    from src.services import graph_read as g
    parts = tuple(x.strip() for x in include.split(",") if x.strip())
    unknown = sorted(set(parts) - set(_PARTS))
    if unknown:
        raise Problem("BAD_REQUEST", f"unknown include values {unknown}; allowed: {list(_PARTS)}")
    body = await _call(g.paper, doi_or_paper_id, parts)
    if body is None:
        raise Problem("NOT_FOUND", f"no paper {doi_or_paper_id!r} in the graph")
    return GraphPaperResponse(**body, provenance=provenance(request))


@router.get("/graph/entities/{label}/{canonical_id}/papers", response_model=EntityPapersResponse,
            **route_doc("GET", "/graph/entities/{label}/{canonical_id}/papers"))
async def entity_papers(request: Request, label: str, canonical_id: str,
                        year_from: int | None = None, year_to: int | None = None,
                        min_confidence: float = Query(0.6, ge=0, le=1),
                        role: str | None = Query(None, pattern="^(used|mentioned)$"),
                        grounded: str = Query("true", pattern="^(true|false|any)$",
                                              description="Method/Sensor/Metric: 'true' keeps edges whose term occurs "
                                                          "as a word in the paper's TEI text"),
                        limit: int = Query(100, ge=1, le=1000), cursor: str | None = None,
                        _=Depends(require_scope("read"))) -> EntityPapersResponse:
    from src.services import graph_read as g
    if label not in g.ENTITY_LABELS:
        raise Problem("VALIDATION_FAILED", f"label must be one of {list(g.ENTITY_LABELS)}",
                      errors=[{"loc": ["path", "label"], "msg": f"one of {list(g.ENTITY_LABELS)}", "type": "literal_error"}])
    offset = await _call(g.decode_cursor, cursor)
    body = await _call(g.entity_papers, label, canonical_id, year_from=year_from, year_to=year_to,
                       min_confidence=min_confidence, role=role, limit=limit, offset=offset, grounded=grounded)
    return EntityPapersResponse(**body, provenance=provenance(request))


@router.get("/graph/queries", response_model=NamedQueriesResponse, **route_doc("GET", "/graph/queries"))
async def queries(request: Request, _=Depends(require_scope("read"))) -> NamedQueriesResponse:
    from src.services import graph_read as g
    return NamedQueriesResponse(queries=[NamedQueryInfo(**g.describe(q)) for q in g.CATALOGUE.values()],
                                provenance=provenance(request))


@router.post("/graph/queries/{name}", response_model=TabularResponse, **route_doc("POST", "/graph/queries/{name}"))
async def run_query(request: Request, name: str, body: GraphQueryRequest,
                    _=Depends(require_scope("read"))) -> TabularResponse:
    from src.services import graph_read as g
    if name not in g.CATALOGUE:
        raise Problem("NOT_FOUND", f"no named query {name!r}; see GET /v1/graph/queries")
    columns, rows, truncated = await _call(g.run_named, name, body.params, body.limit)
    return TabularResponse(columns=columns, rows=rows, truncated=truncated, provenance=provenance(request))


@router.post("/graph/cypher", response_model=TabularResponse, **route_doc("POST", "/graph/cypher"))
async def cypher(request: Request, body: CypherRequest, _=Depends(require_scope("admin"))) -> TabularResponse:
    from src.services import graph_read as g
    columns, rows, truncated = await _call(g.run_cypher, body.query, body.params, body.limit)
    return TabularResponse(columns=columns, rows=rows, truncated=truncated, provenance=provenance(request))
