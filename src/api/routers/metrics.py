"""Metrics and ontology: deterministic extraction, the corpus fact table, normalisation."""

from __future__ import annotations

import re
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, Query, Request
from starlette.concurrency import run_in_threadpool

from src.api.deps import provenance, require_scope
from src.api.documentation import route_doc
from src.api.problems import Problem
from src.contracts.api import (
    MetricDefinition, MetricFactsResponse, MetricOntologyResponse, MetricsExtractRequest, MetricsExtractResponse,
    NormalizeRequest, NormalizeResponse, OntologyEntitiesResponse, OntologyEntity, PaperRef,
)

router = APIRouter()
_DTD = re.compile(r"<!\s*(DOCTYPE|ENTITY)", re.IGNORECASE)


def _ref(p) -> PaperRef | None:
    if p is None:
        return None
    return PaperRef(paper_id=p.paper_id, doi=p.doi, title=p.title, year=p.year, venue=p.venue,
                    identity_status=p.identity_status)


def _wanted(names: list[str]) -> set[str] | None:
    from src.services import metrics as M
    if not names:
        return None
    out, unknown = set(), []
    for n in names:
        cid = M.resolve_metric(n)
        (out.add(cid) if cid else unknown.append(n))
    if unknown:
        raise Problem("VALIDATION_FAILED", f"unknown metrics {unknown}; see GET /v1/metrics/ontology",
                      errors=[{"loc": ["body", "metrics"], "msg": f"unknown: {unknown}", "type": "value_error"}])
    return out


@router.post("/metrics/extract", response_model=MetricsExtractResponse, **route_doc("POST", "/metrics/extract"))
async def extract(request: Request, body: MetricsExtractRequest,
                  mode: str = Query("deterministic", pattern="^(deterministic|llm)$"),
                  _=Depends(require_scope("read"))) -> MetricsExtractResponse:
    from src.document.parser import TEIParser
    from src.services import metrics as M
    if mode == "llm":
        raise Problem("NOT_IMPLEMENTED", "mode=llm is planned for phase 4 (scope llm, asynchronous job)",
                      extra={"docs": "/v1/docs/endpoint?method=POST&path=/metrics/extract"})
    given = [k for k in ("text", "tei_xml", "paper_id") if getattr(body, k)]
    if len(given) != 1:
        raise Problem("VALIDATION_FAILED", "give exactly one of text, tei_xml, paper_id",
                      errors=[{"loc": ["body"], "msg": "exactly one of text, tei_xml, paper_id", "type": "value_error"}])
    wanted = _wanted(body.metrics)
    paper = None
    if body.text:
        facts, rejected = await run_in_threadpool(M.extract_text, body.text, wanted)
    elif body.tei_xml:
        if _DTD.search(body.tei_xml):
            raise Problem("VALIDATION_FAILED", "DOCTYPE and ENTITY declarations are not accepted",
                          errors=[{"loc": ["body", "tei_xml"], "msg": "no DTD allowed", "type": "value_error"}])
        try:
            doc = TEIParser().parse_text(body.tei_xml, "upload")
        except ValueError as exc:
            raise Problem("VALIDATION_FAILED", str(exc)[:300]) from exc

        def run():
            with tempfile.TemporaryDirectory() as d:
                path = Path(d) / "upload.tei.xml"
                path.write_text(body.tei_xml, encoding="utf-8")
                return M.extract_document(doc, path, "upload", wanted)
        facts, rejected = await run_in_threadpool(run)
    else:
        from src.api.routers.papers import _document
        from src.services import fulltext
        identity, doc = await _document(body.paper_id)
        paper = _ref(identity)
        path = await run_in_threadpool(fulltext.tei_path, identity.paper_id)
        facts, rejected = await run_in_threadpool(M.extract_document, doc, path, identity.paper_id, wanted)
        for f in facts:
            f["paper"] = paper
    return MetricsExtractResponse(facts=facts, rejected=rejected, paper=paper, provenance=provenance(request))


@router.get("/metrics/facts", response_model=MetricFactsResponse, **route_doc("GET", "/metrics/facts"))
async def facts(request: Request,
                metric: str | None = Query(None, description="canonical id or name, e.g. metric.nse or NSE"),
                min: float | None = None, max: float | None = None,
                method: str | None = None, sensor: str | None = None,
                paper_id: str | None = None, doi: str | None = None,
                source: str = Query("any", pattern="^(text|table|any)$"),
                range_verdict: str = Query("ok", pattern="^(ok|suspect|unknown_metric|any)$"),
                limit: int = Query(100, ge=1, le=1000), cursor: str | None = None,
                _=Depends(require_scope("read"))) -> MetricFactsResponse:
    from src.services import graph_read, identity_store
    from src.services import metrics as M
    cid = None
    if metric:
        cid = M.resolve_metric(metric)
        if cid is None:
            raise Problem("VALIDATION_FAILED", f"unknown metric {metric!r}; see GET /v1/metrics/ontology",
                          errors=[{"loc": ["query", "metric"], "msg": "unknown metric", "type": "value_error"}])
    try:
        offset = graph_read.decode_cursor(cursor)
    except graph_read.BadParams as exc:
        raise Problem("VALIDATION_FAILED", str(exc), errors=exc.errors) from exc
    paper_ids = None
    if paper_id or doi:
        found = await run_in_threadpool(lambda: identity_store.resolve(doi=doi) if doi else
                                        identity_store.resolve(paper_id=paper_id))
        if found is None:
            raise Problem("NOT_IN_CORPUS", f"no paper {doi or paper_id!r} in the corpus")
        paper_ids = [p.paper_id for p in (found.paper, found.canonical) if p]
    if source == "text":
        body = {"items": [], "total": 0, "summary": {"n": 0, "papers": 0, "median": None, "p10": None, "p90": None},
                "coverage": {"papers_with_text_facts": 0,
                             "note": "text facts are not stored yet; use POST /metrics/extract with a paper_id"}}
    else:
        body = await run_in_threadpool(lambda: M.query_facts(metric=cid, lo=min, hi=max, paper_ids=paper_ids,
                                                             method=method, sensor=sensor, range_verdict=range_verdict,
                                                             limit=limit, offset=offset))
    refs = await run_in_threadpool(identity_store.papers_by_id, [i["paper_id"] for i in body["items"]])
    items = []
    for i in body["items"]:
        pid = i.pop("paper_id")
        i["paper"] = _ref(refs.get(pid))
        items.append(i)
    nxt = offset + len(items)
    return MetricFactsResponse(items=items, summary=body["summary"], coverage=body["coverage"],
                               next_cursor=graph_read.encode_cursor(nxt) if nxt < body["total"] else None,
                               provenance=provenance(request))


@router.get("/metrics/ontology", response_model=MetricOntologyResponse, **route_doc("GET", "/metrics/ontology"))
async def metric_ontology(request: Request, _=Depends(require_scope("read"))) -> MetricOntologyResponse:
    from src.services import metrics as M
    rows = await run_in_threadpool(M.ontology_metrics)
    return MetricOntologyResponse(metrics=[MetricDefinition(**r) for r in rows], provenance=provenance(request))


@router.post("/ontology/normalize", response_model=NormalizeResponse, **route_doc("POST", "/ontology/normalize"))
async def normalize(request: Request, body: NormalizeRequest, _=Depends(require_scope("read"))) -> NormalizeResponse:
    from src.services import ontology
    results = await run_in_threadpool(ontology.normalize, [t.model_dump() for t in body.terms], body.allow_semantic)
    return NormalizeResponse(results=results, provenance=provenance(request))


@router.get("/ontology/entities", response_model=OntologyEntitiesResponse, **route_doc("GET", "/ontology/entities"))
async def entities(request: Request, type: str | None = None, q: str | None = None,
                   limit: int = Query(100, ge=1, le=1000), cursor: str | None = None,
                   _=Depends(require_scope("read"))) -> OntologyEntitiesResponse:
    from src.services import graph_read, ontology
    if type and type not in ontology.TYPES:
        raise Problem("VALIDATION_FAILED", f"type must be one of {list(ontology.TYPES)}",
                      errors=[{"loc": ["query", "type"], "msg": f"one of {list(ontology.TYPES)}", "type": "literal_error"}])
    try:
        offset = graph_read.decode_cursor(cursor)
    except graph_read.BadParams as exc:
        raise Problem("VALIDATION_FAILED", str(exc), errors=exc.errors) from exc
    items, total = await run_in_threadpool(ontology.entities, type, q, limit, offset)
    nxt = offset + len(items)
    return OntologyEntitiesResponse(items=[OntologyEntity(**i) for i in items], count=total,
                                    next_cursor=graph_read.encode_cursor(nxt) if nxt < total else None,
                                    provenance=provenance(request))
