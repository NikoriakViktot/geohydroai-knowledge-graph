"""System endpoints: health, capabilities, stats, manifest, contract schemas."""

from __future__ import annotations

import json
from collections import Counter

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from src.api import docs_loader
from src.api.deps import API_VERSION, COLLECTION, EMBEDDING_MODEL, ROOT, provenance, require_scope
from src.api.documentation import route_doc
from src.api.problems import Problem

router = APIRouter()
SCHEMAS_DIR = ROOT / "contracts" / "schemas"


@router.get("/health", **route_doc("GET", "/health"))
async def health(request: Request) -> dict:
    from src.services import health as health_service
    result = await run_in_threadpool(health_service.health)
    return {**result, "version": API_VERSION}


@router.get("/capabilities", **route_doc("GET", "/capabilities"))
def capabilities() -> dict:
    docs = [d for variants in docs_loader.load_endpoint_docs().values() for d in variants]
    implemented = sorted(f"{d.method} {d.path}" + (f"?{d.variant}" if d.variant else "")
                         for d in docs if d.status == "implemented")
    by_group: dict[str, Counter] = {}
    for d in docs:
        by_group.setdefault(d.group, Counter())[d.status] += 1
    return {
        "api_version": API_VERSION,
        "groups": {g: {"implemented": c["implemented"], "planned": c["planned"]} for g, c in sorted(by_group.items())},
        "implemented_endpoints": implemented,
        "llm": {"enabled": False, "detail": "LLM endpoints are planned (phase 4)"},
        "limits": {"read_rps": 20, "max_batch": {"resolve": 500, "quotes": 50, "doi_verify": 50}, "upload_mb": 100},
    }


@router.get("/stats", **route_doc("GET", "/stats"))
async def stats(request: Request, _=Depends(require_scope("read"))) -> dict:
    from src.services import identity_store
    try:
        data = await run_in_threadpool(identity_store.stats)
    except Exception as exc:
        raise Problem("STORE_UNAVAILABLE", f"postgres: {type(exc).__name__}", headers={"Retry-After": "30"}) from exc
    return {**data, "provenance": provenance(request).model_dump(mode="json")}


@router.get("/manifest", **route_doc("GET", "/manifest"))
async def manifest(request: Request, _=Depends(require_scope("read"))) -> dict:
    value = await run_in_threadpool(request.app.state.manifest.get)
    if value is None:
        raise Problem("STORE_UNAVAILABLE", "manifest unavailable (postgres unreachable)", headers={"Retry-After": "30"})
    return {**value, "collection": COLLECTION, "embedding_model": EMBEDDING_MODEL,
            "provenance": provenance(request).model_dump(mode="json")}


@router.get("/schemas/{name}", **route_doc("GET", "/schemas/{name}"))
def schema(name: str) -> JSONResponse:
    path = SCHEMAS_DIR / f"{name}.json"
    if not name.replace(".", "").replace("_", "").isalnum() or not path.is_file():
        available = sorted(p.stem for p in SCHEMAS_DIR.glob("*.json"))
        raise Problem("NOT_FOUND", f"no contract {name!r}; available: {', '.join(available)}")
    return JSONResponse(json.loads(path.read_text(encoding="utf-8")), media_type="application/schema+json")


CLIENT_FILE = ROOT / "clients" / "python" / "ghai_client" / "__init__.py"


@router.get("/client.py", **route_doc("GET", "/client.py"))
def client_py():
    """The single-file Python client (save it as ghai_client.py)."""
    from fastapi.responses import PlainTextResponse
    return PlainTextResponse(CLIENT_FILE.read_text(encoding="utf-8"), media_type="text/x-python",
                             headers={"Content-Disposition": 'attachment; filename="ghai_client.py"'})
