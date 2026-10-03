"""The human verification layer and the paper inspection it needs (docs/api/endpoints/verify.md).

Writes need the 'verify' scope, which is given to people only; every row is labeler_kind 'human'
with the key's consumer as labeler (AGENT_RULES R-ACC-7, R-SCI-6).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from starlette.concurrency import run_in_threadpool

from src.api.deps import provenance, require_scope
from src.api.documentation import route_doc
from src.api.problems import Problem
from src.contracts.verify import (
    Check, CheckBatch, CheckList, CheckSummary, LinksRequest, LinksResponse, PaperLinks, Problem as ProblemKind,
    RegionsResponse, TargetKind, Verdict,
)

router = APIRouter()


def _store_error(exc: Exception) -> Problem:
    return Problem("STORE_UNAVAILABLE", f"postgres (verify): {type(exc).__name__}", headers={"Retry-After": "30"})


def _base(request: Request) -> str:
    return str(request.base_url).rstrip("/")


@router.post("/verifications", response_model=CheckList, status_code=201, **route_doc("POST", "/verifications"))
async def add_checks(request: Request, body: CheckBatch, principal=Depends(require_scope("verify"))) -> CheckList:
    from src.services import verify
    try:
        ids = await run_in_threadpool(verify.record, body.checks, principal.consumer, principal.key_id)
    except Exception as exc:
        raise _store_error(exc) from exc
    items = [Check(**c.model_dump(), check_id=i, labeler=principal.consumer) for c, i in zip(body.checks, ids)]
    return CheckList(items=items, count=len(items), provenance=provenance(request))


@router.get("/verifications", response_model=CheckList, **route_doc("GET", "/verifications"))
async def list_checks(request: Request,
                      target_kind: TargetKind | None = None,
                      target_id: list[str] | None = Query(None, description="repeat for several targets"),
                      paper_id: str | None = None, project_id: str | None = None,
                      verdict: Verdict | None = None, problem: ProblemKind | None = None,
                      history: bool = Query(False, description="every judgement instead of the latest per target"),
                      limit: int = Query(500, ge=1, le=5000),
                      _=Depends(require_scope("read"))) -> CheckList:
    from src.services import verify
    try:
        rows = await run_in_threadpool(lambda: verify.listing(
            current=not history, target_kind=target_kind, target_ids=target_id, paper_id=paper_id,
            project_id=project_id, verdict=verdict, problem=problem, limit=limit))
    except Exception as exc:
        raise _store_error(exc) from exc
    return CheckList(items=[Check(**r) for r in rows], count=len(rows), provenance=provenance(request))


@router.get("/verifications/summary", response_model=CheckSummary, **route_doc("GET", "/verifications/summary"))
async def checks_summary(request: Request, project_id: str | None = None,
                         _=Depends(require_scope("read"))) -> CheckSummary:
    from src.services import verify
    try:
        body = await run_in_threadpool(verify.summary, project_id)
    except Exception as exc:
        raise _store_error(exc) from exc
    return CheckSummary(**body, provenance=provenance(request))


@router.get("/papers/{paper_id}/regions", response_model=RegionsResponse,
            **route_doc("GET", "/papers/{paper_id}/regions"))
async def paper_regions(request: Request, paper_id: str, _=Depends(require_scope("read"))) -> RegionsResponse:
    from src.services import regions
    found = await run_in_threadpool(regions.regions, paper_id, _base(request))
    if found is None:
        raise Problem("SOURCE_UNAVAILABLE", f"no regions for {paper_id!r}: Nougat (nougat_region_pipeline) has not run on it")
    try:
        pdf = (await run_in_threadpool(regions.pdf_paths, [paper_id])).get(paper_id)
    except Exception:
        pdf = None
    counts: dict[str, int] = {}
    for r in found:
        counts[r["region_type"]] = counts.get(r["region_type"], 0) + 1
    return RegionsResponse(paper_id=paper_id, regions=found, counts=counts,
                           pdf_url=regions.file_url(_base(request), pdf), provenance=provenance(request))


@router.post("/papers/links", response_model=LinksResponse, **route_doc("POST", "/papers/links"))
async def paper_links(request: Request, body: LinksRequest, _=Depends(require_scope("read"))) -> LinksResponse:
    from src.services import identity_store, regions
    try:
        paths = await run_in_threadpool(regions.pdf_paths, body.paper_ids)
        papers = await run_in_threadpool(identity_store.papers_by_id, body.paper_ids)
    except Exception as exc:
        raise _store_error(exc) from exc
    base = _base(request)
    links = []
    for pid in body.paper_ids:
        doi = getattr(papers.get(pid), "doi", None)
        links.append(PaperLinks(paper_id=pid, pdf_url=regions.file_url(base, paths.get(pid)),
                                doi_url=f"https://doi.org/{doi}" if doi else None))
    return LinksResponse(links=links, provenance=provenance(request))
