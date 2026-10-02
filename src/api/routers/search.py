"""Search: semantic retrieval over the Chroma projection (docs/api/endpoints/search.md)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from starlette.concurrency import run_in_threadpool

from src.api.deps import EMBEDDING_MODEL, provenance, require_scope
from src.api.documentation import route_doc
from src.api.problems import Problem
from src.contracts.api import (
    ChunkHit, ChunkSearchRequest, ChunkSearchResponse, PaperHit, PaperRef, PaperSearchRequest, PaperSearchResponse,
    SimilarRequest,
)

router = APIRouter()
_VALIDITY_DETAIL = {"reason": "no recall measurement exists for this (collection, corpus manifest); "
                              "an empty result means only that this search found nothing (R-SCI-3)"}


def _ref(p) -> PaperRef | None:
    if p is None:
        return None
    return PaperRef(paper_id=p.paper_id, doi=p.doi, title=p.title, year=p.year, venue=p.venue,
                    identity_status=p.identity_status)


async def _run(fn, *args):
    try:
        return await run_in_threadpool(fn, *args)
    except Problem:
        raise
    except (LookupError, ValueError) as exc:
        raise Problem("STORE_UNAVAILABLE", f"vector index: {exc}", headers={"Retry-After": "60"}) from exc
    except Exception as exc:
        raise Problem("STORE_UNAVAILABLE", f"vector index or postgres: {type(exc).__name__}",
                      headers={"Retry-After": "30"}) from exc


async def _papers(ids: list[str]) -> dict:
    from src.services import identity_store
    return await run_in_threadpool(identity_store.papers_by_id, ids)


def _prov(request: Request):
    from src.services import search
    return provenance(request, collection=search.collection_name(), embedding_model=EMBEDDING_MODEL)


def _hit(h: dict, refs: dict) -> ChunkHit:
    return ChunkHit(chunk_id=h["chunk_id"], score=h["score"], chunk_type=h.get("chunk_type"),
                    paper=_ref(refs.get(h.get("paper_id"))), section=h.get("section"), page=h.get("page"),
                    text=h.get("text") or "")


@router.post("/search/chunks", response_model=ChunkSearchResponse, **route_doc("POST", "/search/chunks"))
async def chunks(request: Request, body: ChunkSearchRequest, _=Depends(require_scope("read"))) -> ChunkSearchResponse:
    from src.services import search
    result = await _run(search.search_chunks, body.query, body.k, body.filters.model_dump(), body.min_score)
    refs = await _papers([h["paper_id"] for h in result["hits"]])
    return ChunkSearchResponse(hits=[_hit(h, refs) for h in result["hits"]], coverage=result["coverage"],
                               retrieval_validity="NOT_MEASURED", validity_detail=_VALIDITY_DETAIL,
                               provenance=_prov(request))


def _paper_hits(rows: list[dict], refs: dict) -> list[PaperHit]:
    return [PaperHit(paper_id=r["paper_id"], paper=_ref(refs.get(r["paper_id"])), score=r["score"], hits=r["hits"],
                     queries_matched=r["queries_matched"], best_chunks=[_hit(h, refs) for h in r["best_chunks"]])
            for r in rows]


@router.post("/search/papers", response_model=PaperSearchResponse, **route_doc("POST", "/search/papers"))
async def papers(request: Request, body: PaperSearchRequest, _=Depends(require_scope("read"))) -> PaperSearchResponse:
    from src.services import search
    bad = [i for i, q in enumerate(body.queries) if not 3 <= len(q.strip()) <= 1000]
    if bad:
        raise Problem("VALIDATION_FAILED", "each query needs 3–1,000 characters",
                      errors=[{"loc": ["body", "queries", i], "msg": "3–1,000 characters", "type": "string_length"}
                              for i in bad])
    result = await _run(search.search_papers, body.queries, body.k, body.max_candidates, body.filters.model_dump(),
                        body.aggregate)
    refs = await _papers([r["paper_id"] for r in result["papers"]])
    return PaperSearchResponse(papers=_paper_hits(result["papers"], refs), coverage=result["coverage"],
                               retrieval_validity="NOT_MEASURED", validity_detail=_VALIDITY_DETAIL,
                               provenance=_prov(request))


@router.post("/search/similar", response_model=PaperSearchResponse, **route_doc("POST", "/search/similar"))
async def similar(request: Request, body: SimilarRequest, _=Depends(require_scope("read"))) -> PaperSearchResponse:
    from src.services import identity_store, search
    if bool(body.paper_id) == bool(body.doi):
        raise Problem("VALIDATION_FAILED", "give exactly one of paper_id, doi",
                      errors=[{"loc": ["body"], "msg": "exactly one of paper_id, doi", "type": "value_error"}])
    found = await _run(lambda: identity_store.resolve(doi=body.doi) if body.doi else
                       identity_store.resolve(paper_id=body.paper_id))
    if found is None:
        raise Problem("NOT_IN_CORPUS", f"no paper {body.doi or body.paper_id!r} in the corpus")
    seed = (found.canonical or found.paper).paper_id
    result = await _run(search.similar_papers, seed, body.k, body.filters.model_dump())
    if result is None:
        raise Problem("SOURCE_UNAVAILABLE", f"{seed} has no chunks in the vector index yet")
    refs = await _papers([r["paper_id"] for r in result["papers"]])
    return PaperSearchResponse(papers=_paper_hits(result["papers"], refs), coverage=result["coverage"],
                               retrieval_validity="NOT_MEASURED", validity_detail=_VALIDITY_DETAIL,
                               provenance=_prov(request))
