"""GeoHydroAI Knowledge API — FastAPI application.

Run:  uvicorn src.api.app:app --host 127.0.0.1 --port 8090

Docs: Swagger UI at /docs, OpenAPI at /v1/openapi.json, agent rules at
/v1/agent-rules, machine index at /llms.txt — all generated from docs/api.
MCP: the same endpoints as tools at /mcp (src/api/mcp.py, docs/api/MCP_TOOLS.md).
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from src.api import docs_loader
from src.api import mcp as mcp_adapter
from src.api.deps import API_VERSION, KeyStore, ManifestCache, PostgresKeyStore
from src.api.documentation import api_description, route_doc
from src.api.problems import Problem, http_handler, problem_handler, validation_handler
from src.api.routers import biblio as biblio_router
from src.api.routers import docs as docs_router
from src.api.routers import evidence as evidence_router
from src.api.routers import graph as graph_router
from src.api.routers import metrics as metrics_router
from src.api.routers import papers as papers_router
from src.api.routers import search as search_router
from src.api.routers import planned as planned_router
from src.api.routers import system as system_router

PREFIX = "/v1"
_LINK = '</v1/agent-rules>; rel="agent-rules", </llms.txt>; rel="describedby", </v1/openapi.json>; rel="service-desc"'


def _routes(routes, prefix: str = ""):
    """(full path, route) pairs, descending into routers that FastAPI 0.141 keeps as one _IncludedRouter."""
    for route in routes:
        inner = getattr(route, "original_router", None)
        if inner is not None:
            yield from _routes(inner.routes, prefix + (getattr(route.include_context, "prefix", "") or ""))
        else:
            yield prefix + getattr(route, "path", ""), route


def _implemented(app: FastAPI) -> set[tuple[str, str]]:
    out = set()
    for path, route in _routes(app.routes):
        if not path.startswith(PREFIX):
            continue
        doc_path = path[len(PREFIX):].replace(":path}", "}")
        for method in (getattr(route, "methods", None) or set()) - {"HEAD", "OPTIONS"}:
            out.add((method, doc_path))
    return out


@asynccontextmanager
async def _lifespan(app: FastAPI):
    """The MCP transport's task group lives as long as the server (a mounted app's own lifespan never runs)."""
    async with app.state.mcp.session_manager.run():
        yield


def create_app(key_store: KeyStore | None = None, manifest: ManifestCache | None = None,
               mcp_allowed_hosts: list[str] | None = None) -> FastAPI:
    app = FastAPI(
        title="GeoHydroAI Knowledge API",
        version=API_VERSION,
        description=api_description(),
        openapi_url=f"{PREFIX}/openapi.json",
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=_lifespan,
    )
    app.state.key_store = key_store or PostgresKeyStore()
    app.state.manifest = manifest or ManifestCache()

    app.add_exception_handler(Problem, problem_handler)
    app.add_exception_handler(RequestValidationError, validation_handler)
    app.add_exception_handler(StarletteHTTPException, http_handler)

    @app.middleware("http")
    async def headers(request: Request, call_next):
        request.state.request_id = request.headers.get("x-request-id") or f"req_{uuid.uuid4().hex[:16]}"
        response = await call_next(request)
        response.headers["X-Request-Id"] = request.state.request_id
        response.headers["X-API-Version"] = API_VERSION
        response.headers["Link"] = _LINK
        return response

    for router in (system_router.router, docs_router.router, papers_router.router, evidence_router.router,
                   biblio_router.router, graph_router.router, metrics_router.router,
                   search_router.router):
        app.include_router(router, prefix=PREFIX)
    app.include_router(planned_router.build_router(_implemented(app)), prefix=PREFIX)

    @app.get("/ui", include_in_schema=False)
    def ui() -> HTMLResponse:
        """A small page for people: find a paper, open its local PDF in the browser."""
        return HTMLResponse((Path(__file__).parent / "static" / "ui.html").read_text(encoding="utf-8"),
                            headers={"Cache-Control": "no-cache"})

    @app.get("/", include_in_schema=False)
    def root() -> RedirectResponse:
        return RedirectResponse("/ui")

    @app.get("/llms.txt", include_in_schema=False)
    def llms_txt() -> PlainTextResponse:
        return PlainTextResponse((docs_loader.DOCS_DIR / "llms.txt").read_text(encoding="utf-8"))

    # /llms.txt lives at the root by convention; document it from its /v1 entry.
    app.add_api_route("/v1/llms.txt", llms_txt, methods=["GET"], **route_doc("GET", "/llms.txt"))
    app.state.mcp = mcp_adapter.mount(app, allowed_hosts=mcp_allowed_hosts)
    return app


app = create_app()
