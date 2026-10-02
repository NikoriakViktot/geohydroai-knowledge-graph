"""RFC 9457 problem+json errors (docs/api/README.md §5)."""

from __future__ import annotations

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

PROBLEM_MEDIA_TYPE = "application/problem+json"

#: code → (HTTP status, title)
CODES: dict[str, tuple[int, str]] = {
    "BAD_REQUEST": (400, "Bad request"),
    "UNAUTHENTICATED": (401, "Missing or invalid API key"),
    "FORBIDDEN_SCOPE": (403, "API key lacks the required scope"),
    "INVALID_LINK": (403, "Not a file link from this server"),
    "NOT_FOUND": (404, "Not found"),
    "NOT_IN_CORPUS": (404, "Paper not in corpus"),
    "GATE_NOT_PASSED": (409, "Acceptance gate not passed"),
    "CONFLICT": (409, "Conflict"),
    "LINK_EXPIRED": (410, "File link expired"),
    "PAYLOAD_TOO_LARGE": (413, "Payload too large"),
    "UNSUPPORTED_MEDIA_TYPE": (415, "Unsupported media type"),
    "VALIDATION_FAILED": (422, "Request violates the contract"),
    "INVALID_DOI": (422, "Not a DOI"),
    "UNKNOWN_PROJECT": (422, "Project not registered"),
    "SOURCE_UNAVAILABLE": (424, "Source full text unavailable"),
    "RATE_LIMITED": (429, "Rate limited"),
    "QUOTA_EXHAUSTED": (429, "LLM quota exhausted"),
    "NOT_IMPLEMENTED": (501, "Endpoint documented but not implemented yet"),
    "STORE_UNAVAILABLE": (503, "A backing store is unavailable"),
    "LLM_UNAVAILABLE": (503, "No language-model provider available"),
    "UPSTREAM_TIMEOUT": (504, "Upstream registry timed out"),
}


class Problem(Exception):
    """Raise anywhere in a route or service to answer with problem+json."""

    def __init__(self, code: str, detail: str = "", *, errors: list | None = None,
                 headers: dict[str, str] | None = None, extra: dict | None = None):
        super().__init__(detail or code)
        self.code = code
        self.status, self.title = CODES.get(code, (500, "Internal error"))
        self.detail = detail
        self.errors = errors or []
        self.headers = headers or {}
        self.extra = extra or {}


def _body(request: Request, status: int, code: str, title: str, detail: str,
          errors: list | None = None, extra: dict | None = None) -> dict:
    body = {
        "type": f"https://ghai.local/problems/{code.lower().replace('_', '-')}",
        "title": title, "status": status, "code": code, "detail": detail,
        "instance": getattr(request.state, "request_id", None), "errors": errors or [],
    }
    body.update(extra or {})
    return body


async def problem_handler(request: Request, exc: Problem) -> JSONResponse:
    return JSONResponse(_body(request, exc.status, exc.code, exc.title, exc.detail, exc.errors, exc.extra),
                        status_code=exc.status, headers=exc.headers, media_type=PROBLEM_MEDIA_TYPE)


async def validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    errors = [{"loc": list(e.get("loc", [])), "msg": e.get("msg", ""), "type": e.get("type", "")}
              for e in exc.errors()]
    return JSONResponse(_body(request, 422, "VALIDATION_FAILED", CODES["VALIDATION_FAILED"][1],
                              "The request does not match the contract; nothing was coerced.", errors),
                        status_code=422, media_type=PROBLEM_MEDIA_TYPE)


async def http_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    code = {401: "UNAUTHENTICATED", 403: "FORBIDDEN_SCOPE", 404: "NOT_FOUND",
            405: "BAD_REQUEST", 413: "PAYLOAD_TOO_LARGE", 415: "UNSUPPORTED_MEDIA_TYPE"}.get(exc.status_code, "BAD_REQUEST")
    return JSONResponse(_body(request, exc.status_code, code, CODES.get(code, (0, "Error"))[1], str(exc.detail)),
                        status_code=exc.status_code, media_type=PROBLEM_MEDIA_TYPE,
                        headers=getattr(exc, "headers", None))
