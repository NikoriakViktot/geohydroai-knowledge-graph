"""Documented-but-not-implemented endpoints.

Every endpoint in docs/api/endpoints that no router implements yet is registered
here with its full documentation and agent rules, and answers
501 NOT_IMPLEMENTED with a pointer to its documentation. The whole contract is
therefore discoverable from the running API (Swagger, /v1/openapi.json) before
each piece is built.
"""

from __future__ import annotations

import re

from fastapi import APIRouter, Request

from src.api import docs_loader
from src.api.documentation import route_doc
from src.api.problems import Problem

#: Path parameters that may contain "/" (DOIs, file names, page names).
_SLASHY = {"doi", "doi_or_paper_id", "name"}


def route_path(doc_path: str) -> str:
    """Documented path → Starlette path (DOI-bearing params accept '/')."""
    return re.sub(r"\{(\w+)\}", lambda m: "{%s:path}" % m.group(1) if m.group(1) in _SLASHY else m.group(0), doc_path)


def _stub(method: str, path: str, status_text: str):
    async def endpoint(request: Request):
        raise Problem("NOT_IMPLEMENTED", f"{method} {path} is documented but not implemented yet ({status_text}).",
                      extra={"docs": f"/v1/docs/endpoint?method={method}&path={path}",
                             "capabilities": "/v1/capabilities"})
    endpoint.__name__ = f"planned_{method.lower()}_{re.sub(r'[^a-z0-9]+', '_', path.lower()).strip('_')}"
    return endpoint


def build_router(implemented: set[tuple[str, str]]) -> APIRouter:
    router = APIRouter()
    entries = [(m, p, v) for (m, p), v in docs_loader.load_endpoint_docs().items() if (m, p) not in implemented]
    # Longer, more specific paths first so that /x/{id}/y is not shadowed by /x/{id:path}.
    for method, path, variants in sorted(entries, key=lambda e: (-e[1].count("/"), e[1], e[0])):
        main = next((d for d in variants if d.variant is None), variants[0])
        router.add_api_route(route_path(path), _stub(method, path, main.status_text), methods=[method],
                             **route_doc(method, path))
    return router
