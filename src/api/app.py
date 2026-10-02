"""GeoHydroAI Knowledge API — FastAPI application.

Run:  uvicorn src.api.app:app --host 127.0.0.1 --port 8090

Docs: Swagger UI at /docs, OpenAPI at /v1/openapi.json, agent rules at
/v1/agent-rules, machine index at /llms.txt — all generated from docs/api.
"""

from __future__ import annotations

import uuid

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import PlainTextResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from src.api import docs_loader
from src.api.deps import API_VERSION, KeyStore, ManifestCache, PostgresKeyStore
from src.api.documentation import api_description, route_doc
from src.api.problems import Problem, http_handler, problem_handler, validation_handler
from src.api.routers import docs as docs_router
from src.api.routers import papers as papers_router
from src.api.routers import planned as planned_router
from src.api.routers import system as system_router

PREFIX = "/v1"
_LINK = '</v1/agent-rules>; rel="agent-rules", </llms.txt>; rel="describedby", </v1/openapi.json>; rel="service-desc"'


def _implemented(app: FastAPI) -> set[tuple[str, str]]:
    out = set()
    for route in app.routes:
        path = getattr(route, "path", "")
        if not path.startswith(PREFIX):
            continue
        doc_path = path[len(PREFIX):].replace(":path}", "}")
        for method in getattr(route, "methods", set()) - {"HEAD", "OPTIONS"}:
            out.add((method, doc_path))
    return out


def create_app(key_store: KeyStore | None = None, manifest: ManifestCache | None = None) -> FastAPI:
    app = FastAPI(
        title="GeoHydroAI Knowledge API",
        version=API_VERSION,
        description=api_description(),
        openapi_url=f"{PREFIX}/openapi.json",
        docs_url="/docs",
        redoc_url="/redoc",
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

    for router in (system_router.router, docs_router.router, papers_router.router):
        app.include_router(router, prefix=PREFIX)
    app.include_router(planned_router.build_router(_implemented(app)), prefix=PREFIX)

    @app.get("/llms.txt", include_in_schema=False)
    def llms_txt() -> PlainTextResponse:
        return PlainTextResponse((docs_loader.DOCS_DIR / "llms.txt").read_text(encoding="utf-8"))

    # /llms.txt lives at the root by convention; document it from its /v1 entry.
    app.add_api_route("/v1/llms.txt", llms_txt, methods=["GET"], **route_doc("GET", "/llms.txt"))
    return app


app = create_app()
