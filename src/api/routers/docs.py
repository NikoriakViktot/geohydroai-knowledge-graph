"""Documentation and agent rules served by the API itself."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Query
from fastapi.responses import PlainTextResponse

from src.api import docs_loader
from src.api.deps import API_VERSION
from src.api.documentation import endpoint_docs, route_doc
from src.api.problems import Problem

router = APIRouter()


@router.get("/agent-rules", **route_doc("GET", "/agent-rules"))
def agent_rules(format: Literal["json", "markdown"] = "json"):
    if format == "markdown":
        return PlainTextResponse(docs_loader.read_page("AGENT_RULES"), media_type="text/markdown; charset=utf-8")
    return {
        "version": API_VERSION,
        "top_rules": docs_loader.top_rules_markdown(),
        "rules": [r.__dict__ for r in docs_loader.load_agent_rules()],
        "rules_by_group": {g: list(ids) for g, ids in docs_loader.load_group_rules().items()},
        "markdown_url": "/v1/agent-rules?format=markdown",
    }


@router.get("/docs/index", **route_doc("GET", "/docs/index"))
def docs_index() -> dict:
    endpoints = []
    for (method, path), variants in sorted(docs_loader.load_endpoint_docs().items(), key=lambda kv: (kv[0][1], kv[0][0])):
        for d in variants:
            endpoints.append({"method": method, "path": path, "variant": d.variant, "group": d.group,
                              "summary": d.summary, "status": d.status, "status_text": d.status_text,
                              "scope": d.scope, "mode": d.mode, "rules": list(d.rules)})
    counts = {"implemented": sum(e["status"] == "implemented" for e in endpoints),
              "planned": sum(e["status"] == "planned" for e in endpoints)}
    return {"endpoints": endpoints, "pages": docs_loader.page_names(), "counts": counts}


@router.get("/docs/pages/{name:path}", **route_doc("GET", "/docs/pages/{name}"))
def docs_page(name: str) -> PlainTextResponse:
    try:
        return PlainTextResponse(docs_loader.read_page(name), media_type="text/markdown; charset=utf-8")
    except FileNotFoundError:
        raise Problem("NOT_FOUND", f"no page {name!r}; see /v1/docs/index for the list") from None


@router.get("/docs/endpoint", **route_doc("GET", "/docs/endpoint"))
def docs_endpoint(method: str = Query(..., examples=["POST"]), path: str = Query(..., examples=["/search/chunks"])) -> dict:
    variants = endpoint_docs(method, path.split("?", 1)[0])
    if not variants:
        raise Problem("NOT_FOUND", f"{method.upper()} {path} is not documented; see /v1/docs/index")
    by_id = {r.id: r for r in docs_loader.load_agent_rules()}
    rule_ids = list(dict.fromkeys(r for d in variants for r in d.rules))
    main = next((d for d in variants if d.variant is None), variants[0])
    return {
        "endpoint": {"method": main.method, "path": main.path, "group": main.group, "summary": main.summary,
                     "status": main.status, "status_text": main.status_text, "scope": main.scope, "mode": main.mode},
        "markdown": "\n\n---\n\n".join(d.markdown for d in variants),
        "variants": [d.variant for d in variants if d.variant],
        "rules": [by_id[r].__dict__ for r in rule_ids if r in by_id],
    }
