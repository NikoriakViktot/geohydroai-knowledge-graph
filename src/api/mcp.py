"""MCP adapter: the Knowledge API as tools for Claude Code in the paper repositories.

    POST http://127.0.0.1:8090/mcp      streamable HTTP · JSON responses · stateless

    .mcp.json in a consumer repository (docs/api/MCP_TOOLS.md):
      {"mcpServers": {"ghai": {"type": "http", "url": "http://127.0.0.1:8090/mcp",
                               "headers": {"X-API-Key": "${GHAI_API_KEY}"}}}}

The key is the REST key. ApiKeyMiddleware checks it (X-API-Key, or Authorization: Bearer)
against the same key store before the transport reads a byte: no key or an unknown key is a
401 problem+json. Every tool is a thin wrapper that calls its REST endpoint in-process with the
caller's key, so scopes, provenance, problem+json errors and contracts are exactly the REST
ones, and there is no second implementation to drift. Only implemented endpoints are tools.
Resources are the documentation pages (ghai://docs/<page>); prompts are the standard workflows
of AGENT_RULES.md §7, read from that file.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Annotated, Any, Literal

import httpx
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.streamable_http_manager import StreamableHTTPASGIApp
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from pydantic import Field
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from src.api import docs_loader
from src.api.deps import API_VERSION, hash_key
from src.api.problems import CODES, PROBLEM_MEDIA_TYPE
from src.contracts.api import QuoteItem, SearchFilters

MCP_PATH = "/mcp"
#: tool -> the REST endpoint it calls (as documented in docs/api/endpoints); tests keep the three in step
TOOL_ENDPOINTS: dict[str, tuple[str, str]] = {
    "corpus_manifest": ("GET", "/manifest"),
    "api_capabilities": ("GET", "/capabilities"),
    "resolve_paper": ("GET", "/papers/resolve"),
    "resolve_papers": ("POST", "/papers/resolve-batch"),
    "get_paper_sections": ("GET", "/papers/{paper_id}/sections"),
    "get_paper_text": ("GET", "/papers/{paper_id}/text"),
    "get_paper_references": ("GET", "/papers/{paper_id}/references"),
    "get_paper_tables": ("GET", "/papers/{paper_id}/tables"),
    "get_paper_entities": ("GET", "/papers/{paper_id}/entities"),
    "locate_paper": ("GET", "/locate"),
    "search_literature": ("POST", "/search/chunks"),
    "search_papers": ("POST", "/search/papers"),
    "similar_papers": ("POST", "/search/similar"),
    "verify_quotes": ("POST", "/quotes/verify"),
    "validate_theses": ("POST", "/theses/validate"),
    "resolve_doi": ("GET", "/doi/{doi}"),
    "verify_bib_entries": ("POST", "/doi/verify"),
    "format_bib": ("POST", "/bib/format"),
    "render_bibliography": ("POST", "/bib/render"),
    "audit_bib": ("POST", "/bib/audit"),
    "manuscript_citations": ("POST", "/manuscripts/citations"),
    "metric_facts": ("GET", "/metrics/facts"),
    "extract_metrics": ("POST", "/metrics/extract"),
    "metric_ontology": ("GET", "/metrics/ontology"),
    "normalize_terms": ("POST", "/ontology/normalize"),
    "ontology_entities": ("GET", "/ontology/entities"),
    "graph_paper": ("GET", "/graph/papers/{doi_or_paper_id}"),
    "graph_citations": ("GET", "/graph/papers/{doi_or_paper_id}/citations"),
    "papers_with_entity": ("GET", "/graph/entities/{label}/{canonical_id}/papers"),
    "graph_queries": ("GET", "/graph/queries"),
    "run_graph_query": ("POST", "/graph/queries/{name}"),
}
LOCAL_HOSTS = ["127.0.0.1:*", "localhost:*", "[::1]:*"]
LOCAL_ORIGINS = ["http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*"]
READ = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False)
#: tools that ask outside registries (Crossref, OpenAlex, Unpaywall) through the API's cache
READ_REGISTRY = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True,
                                open_world_hint=True)


def raw_key(headers) -> str | None:
    key = (headers.get("x-api-key") or "").strip()
    if key:
        return key
    auth = headers.get("authorization") or ""
    return auth[7:].strip() or None if auth.lower().startswith("bearer ") else None


async def _problem(scope: Scope, receive: Receive, send: Send, code: str, detail: str,
                   headers: dict[str, str] | None = None) -> None:
    status, title = CODES[code]
    body = {"type": f"https://ghai.local/problems/{code.lower().replace('_', '-')}", "title": title,
            "status": status, "code": code, "detail": detail,
            "instance": (scope.get("state") or {}).get("request_id"), "errors": []}
    await JSONResponse(body, status_code=status, media_type=PROBLEM_MEDIA_TYPE, headers=headers)(scope, receive, send)


class ApiKeyMiddleware:
    """Refuse MCP traffic without a known consumer key; scopes are then enforced by each REST call."""

    def __init__(self, app: ASGIApp, api: Any):
        self.app = app
        self.api = api

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        raw = raw_key(Headers(scope=scope))
        if not raw:
            await _problem(scope, receive, send, "UNAUTHENTICATED",
                           "Send your consumer key in the X-API-Key header (.mcp.json: \"headers\").",
                           {"WWW-Authenticate": 'ApiKey header="X-API-Key"'})
            return
        try:
            principal = await run_in_threadpool(self.api.state.key_store.lookup, hash_key(raw))
        except Exception as exc:  # the key store lives in Postgres
            await _problem(scope, receive, send, "STORE_UNAVAILABLE", f"key store unreachable: {type(exc).__name__}",
                           {"Retry-After": "30"})
            return
        if principal is None:
            await _problem(scope, receive, send, "UNAUTHENTICATED", "Unknown or revoked API key.",
                           {"WWW-Authenticate": 'ApiKey header="X-API-Key"'})
            return
        scope.setdefault("state", {})["consumer"] = principal.consumer
        await self.app(scope, receive, send)


class _Rest:
    """In-process calls to /v1 with the caller's key: the tool is exactly its REST endpoint."""

    def __init__(self, app: Any):
        self.app = app
        self._client: httpx.AsyncClient | None = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app),
                                             base_url="http://ghai.internal/v1/", timeout=300.0)
        return self._client

    async def __call__(self, ctx: Context, method: str, path: str, *, params: dict | None = None,
                       body: Any = None) -> dict:
        headers = ctx.headers or {}
        key = raw_key(headers)
        if not key:  # ApiKeyMiddleware lets nothing through without one; a direct call has none
            raise ToolError(json.dumps({"status": 401, "code": "UNAUTHENTICATED", "detail": "no API key"}))
        forward = {"X-API-Key": key}
        if headers.get("x-request-id"):
            forward["X-Request-Id"] = headers["x-request-id"]
        params = {k: v for k, v in (params or {}).items() if v is not None}
        r = await self.client.request(method, path, params=params, json=body, headers=forward)
        if r.status_code >= 400:
            try:
                problem = r.json()
            except ValueError:
                problem = {"status": r.status_code, "code": "ERROR", "detail": r.text[:500]}
            keep = {k: problem.get(k) for k in ("status", "code", "title", "detail", "errors") if problem.get(k)}
            keep.update({k: v for k, v in problem.items() if k in ("counts", "warnings", "unverified_dois")})
            raise ToolError(json.dumps(keep, ensure_ascii=False))
        return r.json()


def _dump(model) -> dict | None:
    return None if model is None else model.model_dump(mode="json", exclude_unset=True)


def instructions() -> str:
    return (
        "Tools of the GeoHydroAI Knowledge API (REST /v1, a corpus of ~4.8k flood, hydrology and "
        "remote-sensing papers). Every answer carries `provenance`; record it. Before writing anything "
        "from the literature read the resource ghai://docs/AGENT_RULES; before searching, ghai://docs/QUERY_GUIDE "
        "(queries in English, written like a sentence of a paper, several phrasings; similarity scores rank but "
        "do not prove relevance; a zero in the graph is not a zero in the literature), or start from the prompt "
        "`research`. The rules that matter most:\n\n"
        + docs_loader.top_rules_markdown()
    )


_WORKFLOW = re.compile(r"\*\*(W\d+) — ([^*]+?)\*\*(.*?)(?=\n\*\*W\d+ — |\n## |\Z)", re.S)


def workflows() -> dict[str, tuple[str, str]]:
    """{id: (title, markdown)} from AGENT_RULES.md §7."""
    text = docs_loader.read_page("AGENT_RULES")
    section = text.split("## 7.", 1)[-1].split("\n## ", 1)[0]
    return {m.group(1): (m.group(2).strip(), m.group(3).strip(" :\n")) for m in _WORKFLOW.finditer(section)}


def build_server(api: Any) -> MCPServer:
    """The MCP server whose tools call ``api`` (the FastAPI app) in-process."""
    # MCPServer() calls logging.basicConfig(INFO, RichHandler) when the root logger has no handler, which
    # would re-route every library's INFO log of the API process; a placeholder handler makes it a no-op.
    root = logging.getLogger()
    placeholder = logging.NullHandler() if not root.handlers else None
    if placeholder:
        root.addHandler(placeholder)
    try:
        server = MCPServer("ghai", title="GeoHydroAI Knowledge API", version=API_VERSION,
                           instructions=instructions(), website_url="http://127.0.0.1:8090/docs")
    finally:
        if placeholder:
            root.removeHandler(placeholder)
    rest = _Rest(api)

    # ── system ────────────────────────────────────────────────────────────────
    @server.tool(annotations=READ)
    async def corpus_manifest(ctx: Context) -> dict[str, Any]:
        """The corpus manifest: corpus_manifest_id, paper and chunk counts, vector collection and embedding model.
        Record corpus_manifest_id with every result you keep (R-SCI-8)."""
        return await rest(ctx, "GET", "manifest")

    @server.tool(annotations=READ)
    async def api_capabilities(ctx: Context) -> dict[str, Any]:
        """Which endpoints are implemented and which are only planned. A planned endpoint has no tool yet (R-ACC-2)."""
        return await rest(ctx, "GET", "capabilities")

    # ── papers ────────────────────────────────────────────────────────────────
    @server.tool(annotations=READ)
    async def resolve_paper(ctx: Context,
                            doi: Annotated[str | None, Field(description="DOI, with or without https://doi.org/")] = None,
                            paper_id: str | None = None,
                            title: Annotated[str | None, Field(description="title match; give year too")] = None,
                            year: int | None = None) -> dict[str, Any]:
        """Is a work in the corpus? Returns its identity (paper_id, DOI, title, year, identity_status) or
        NOT_IN_CORPUS. Not in the corpus is not 'does not exist' (R-SCI-3)."""
        return await rest(ctx, "GET", "papers/resolve",
                          params={"doi": doi, "paper_id": paper_id, "title": title, "year": year})

    @server.tool(annotations=READ)
    async def resolve_papers(ctx: Context,
                             items: Annotated[list[dict[str, Any]], Field(
                                 description="up to 500 of {key?, doi?, title?, year?}", max_length=500)]
                             ) -> dict[str, Any]:
        """Resolve many works at once (a reference list, a .bib): per item in_corpus, not_found or ambiguous."""
        return await rest(ctx, "POST", "papers/resolve-batch", body={"items": items})

    @server.tool(annotations=READ)
    async def get_paper_sections(ctx: Context, paper_id: str) -> dict[str, Any]:
        """The sections of a paper (from its GROBID TEI) with their sentence counts. Read before get_paper_text."""
        return await rest(ctx, "GET", f"papers/{paper_id}/sections")

    @server.tool(annotations=READ)
    async def get_paper_text(ctx: Context, paper_id: str,
                             section: Annotated[str | None, Field(description="section title (prefix match)")] = None,
                             page: int | None = None,
                             q: Annotated[str | None, Field(description="words to find; returns matching paragraphs")] = None,
                             max_chars: Annotated[int | None, Field(ge=200, le=50000)] = None) -> dict[str, Any]:
        """Verbatim text of a section, a page or the paragraphs matching `q`, as evidence spans with DOI,
        section and page. Use it instead of recalling what a paper says; quote the returned text exactly (R-SCI-1)."""
        return await rest(ctx, "GET", f"papers/{paper_id}/text",
                          params={"section": section, "page": page, "q": q, "max_chars": max_chars})

    @server.tool(annotations=READ)
    async def get_paper_references(ctx: Context, paper_id: str) -> dict[str, Any]:
        """The paper's parsed bibliography, each reference linked to the corpus when it is in it."""
        return await rest(ctx, "GET", f"papers/{paper_id}/references")

    @server.tool(annotations=READ)
    async def get_paper_tables(ctx: Context, paper_id: str) -> dict[str, Any]:
        """The paper's tables (TEI) with captions and cells. Confirm literature numbers against them (W5)."""
        return await rest(ctx, "GET", f"papers/{paper_id}/tables")

    @server.tool(annotations=READ)
    async def get_paper_entities(ctx: Context, paper_id: str) -> dict[str, Any]:
        """Methods, sensors, metrics, places and the task of a paper, each with its evidence sentence and whether
        the term occurs as a word in the full text (grounded)."""
        return await rest(ctx, "GET", f"papers/{paper_id}/entities")

    @server.tool(annotations=READ_REGISTRY)
    async def locate_paper(ctx: Context,
                           query: Annotated[str, Field(description="DOI, doi.org or publisher URL, ScienceDirect "
                                                                   "PII URL, arXiv id, or paper_id")]) -> dict[str, Any]:
        """Where a paper can be read: its corpus files and legal open-access copies (OpenAlex, Unpaywall, arXiv).
        `open_url` links are for people; never fetch them (R-DATA-3)."""
        return await rest(ctx, "GET", "locate", params={"q": query})

    # ── search ────────────────────────────────────────────────────────────────
    @server.tool(annotations=READ)
    async def search_literature(ctx: Context,
                                query: Annotated[str, Field(min_length=3, max_length=1000)],
                                k: Annotated[int, Field(ge=1, le=200)] = 20,
                                filters: SearchFilters | None = None,
                                min_score: Annotated[float | None, Field(ge=-1, le=1)] = None,
                                project_id: str | None = None) -> dict[str, Any]:
        """Semantic search over the corpus. Returns verbatim passages with DOI, section and page, plus `coverage`
        (how many papers were searched) and `retrieval_validity`. A similar passage is not evidence: verify with
        verify_quotes before citing. An empty result means this search found nothing, not that the literature has
        nothing (R-SCI-3). Use several phrasings."""
        return await rest(ctx, "POST", "search/chunks",
                          body={"query": query, "k": k, "filters": _dump(filters) or {}, "min_score": min_score,
                                "project_id": project_id})

    @server.tool(annotations=READ)
    async def search_papers(ctx: Context,
                            queries: Annotated[list[str], Field(min_length=1, max_length=20)],
                            k: Annotated[int, Field(ge=1, le=200)] = 20,
                            filters: SearchFilters | None = None,
                            aggregate: Literal["max", "mean", "count"] = "max",
                            project_id: str | None = None) -> dict[str, Any]:
        """Papers ranked over several queries, each with its best passages, `coverage` and `retrieval_validity`."""
        return await rest(ctx, "POST", "search/papers",
                          body={"queries": queries, "k": k, "filters": _dump(filters) or {}, "aggregate": aggregate,
                                "project_id": project_id})

    @server.tool(annotations=READ)
    async def similar_papers(ctx: Context, paper_id: str | None = None, doi: str | None = None,
                             k: Annotated[int, Field(ge=1, le=200)] = 20,
                             filters: SearchFilters | None = None) -> dict[str, Any]:
        """Papers whose text is closest to one corpus paper (give paper_id or doi)."""
        return await rest(ctx, "POST", "search/similar",
                          body={"paper_id": paper_id, "doi": doi, "k": k, "filters": _dump(filters) or {}})

    # ── evidence ──────────────────────────────────────────────────────────────
    @server.tool(annotations=READ)
    async def verify_quotes(ctx: Context,
                            items: Annotated[list[QuoteItem], Field(min_length=1, max_length=50)],
                            project_id: Annotated[str | None, Field(
                                description="project whose cite keys may be used as `source`")] = None
                            ) -> dict[str, Any]:
        """Check that quoted words, and optionally numbers, occur in the cited source's full text. Returns the
        source's own wording, its location, and whether that sentence itself cites other works
        (`attribution.cites_other_sources`: a possible secondary citation, trace the original, R-SCI-4).
        Quotes shorter than 25 characters are rejected. SOURCE_UNAVAILABLE means unverified, never 'false'."""
        return await rest(ctx, "POST", "quotes/verify",
                          body={"items": [_dump(i) for i in items], "project_id": project_id})

    @server.tool(annotations=READ)
    async def validate_theses(ctx: Context, kind: Literal["theses", "atomic_claims"], project_id: str,
                              document: Annotated[Any, Field(description="the theses.json list or the "
                                                                         "atomic_claims.yaml text, as written")],
                              authored_by: dict[str, Any] | None = None) -> dict[str, Any]:
        """Strict check of a theses or atomic-claims document against contract v1. Nothing is coerced: errors
        locate each problem with the document's own field names; fix the source document (R-DATA-1)."""
        return await rest(ctx, "POST", "theses/validate",
                          body={"kind": kind, "project_id": project_id, "document": document,
                                "authored_by": authored_by})

    # ── bibliography ──────────────────────────────────────────────────────────
    @server.tool(annotations=READ_REGISTRY)
    async def resolve_doi(ctx: Context, doi: str) -> dict[str, Any]:
        """Registry metadata of a DOI (Crossref, DataCite, OpenAlex): title, authors, venue, online and print
        years, volume, pages, and whether the work is in the corpus. Metadata come from here, never from memory."""
        return await rest(ctx, "GET", f"doi/{doi}")

    @server.tool(annotations=READ_REGISTRY)
    async def verify_bib_entries(ctx: Context,
                                 entries: Annotated[list[dict[str, Any]], Field(
                                     min_length=1, max_length=50,
                                     description="{key, doi, title, authors, year, journal, volume, issue, pages} "
                                                 "or {\"bibtex\": \"@article{…}\"}")],
                                 project_id: str | None = None) -> dict[str, Any]:
        """Field-by-field verification of bibliography entries against the registries: VERIFIED,
        VERIFIED_WITH_NOTES, MISMATCH, UNRESOLVED or NOT_A_DOI. A verified DOI does not mean the source supports
        your sentence."""
        return await rest(ctx, "POST", "doi/verify", body={"entries": entries, "project_id": project_id})

    @server.tool(annotations=READ_REGISTRY)
    async def format_bib(ctx: Context, dois: Annotated[list[str], Field(min_length=1, max_length=100)],
                         project_id: Annotated[str | None, Field(
                             description="avoid cite keys already used in this project")] = None) -> dict[str, Any]:
        """BibTeX entries from DOIs, in the house key convention (Surname_YYYY), from registry metadata."""
        return await rest(ctx, "POST", "bib/format", body={"dois": dois, "project_id": project_id})

    @server.tool(annotations=READ)
    async def render_bibliography(ctx: Context, bibtex: str, keys: list[str] | None = None,
                                  manuscript: Annotated[str | None, Field(
                                      description="when given, the cited keys are taken from its citations")] = None,
                                  style: Literal["apa", "agu", "copernicus", "elsevier-harvard"] = "apa"
                                  ) -> dict[str, Any]:
        """A formatted reference list from a .bib (all entries, `keys`, or the ones a manuscript cites), sorted, with
        unresolved and uncited keys reported."""
        return await rest(ctx, "POST", "bib/render",
                          body={"bibtex": bibtex, "keys": keys, "manuscript": manuscript, "style": style})

    @server.tool(annotations=READ_REGISTRY)
    async def audit_bib(ctx: Context, bibtex: str, project_id: str | None = None,
                        search_missing: Annotated[bool, Field(
                            description="look up DOIs for entries without one (Crossref)")] = True) -> dict[str, Any]:
        """Audit a .bib (≤ 300 entries): DOIs against the registries, missing DOIs, duplicates, the year in the key,
        VERIFY notes. Each entry is ok, fix or unresolved, with a suggested corrected entry."""
        return await rest(ctx, "POST", "bib/audit",
                          body={"bibtex": bibtex, "project_id": project_id, "search_missing": search_missing})

    @server.tool(annotations=READ)
    async def manuscript_citations(ctx: Context, manuscript: str, bibtex: str,
                                   project_id: str | None = None) -> dict[str, Any]:
        """Every author–year citation of a manuscript, resolved to .bib keys, with its sentence, section, line and
        the quoted words to feed into verify_quotes (W1)."""
        return await rest(ctx, "POST", "manuscripts/citations",
                          body={"manuscript": manuscript, "bibtex": bibtex, "project_id": project_id})

    # ── metrics and ontology ──────────────────────────────────────────────────
    @server.tool(annotations=READ)
    async def metric_facts(ctx: Context,
                           metric: Annotated[str | None, Field(description="canonical id or name: metric.nse, NSE, "
                                                                           "KGE, OA, F1, RMSE …")] = None,
                           min: float | None = None, max: float | None = None,
                           method: str | None = None, sensor: str | None = None,
                           paper_id: str | None = None, doi: str | None = None,
                           source: Literal["text", "table", "any"] = "any",
                           range_verdict: Literal["ok", "suspect", "unknown_metric", "any"] = "ok",
                           limit: Annotated[int, Field(ge=1, le=1000)] = 100,
                           cursor: str | None = None) -> dict[str, Any]:
        """Reported metric values from the literature, each with its evidence. Values outside the metric's valid
        range are `suspect` and must not be used. Confirm table values against the table and match units and
        periods before comparing papers (R-SCI-2, W5). Page with `cursor`."""
        return await rest(ctx, "GET", "metrics/facts",
                          params={"metric": metric, "min": min, "max": max, "method": method, "sensor": sensor,
                                  "paper_id": paper_id, "doi": doi, "source": source, "range_verdict": range_verdict,
                                  "limit": limit, "cursor": cursor})

    @server.tool(annotations=READ)
    async def extract_metrics(ctx: Context, text: str | None = None, paper_id: str | None = None,
                              metrics: Annotated[list[str] | None, Field(
                                  description="restrict to these metrics")] = None) -> dict[str, Any]:
        """Deterministic extraction of metric values from a text or a corpus paper: the metric name must precede
        the value in the same sentence; rejected candidates are listed with the reason."""
        return await rest(ctx, "POST", "metrics/extract",
                          body={k: v for k, v in (("text", text), ("paper_id", paper_id)) if v} | {"metrics": metrics or []})

    @server.tool(annotations=READ)
    async def metric_ontology(ctx: Context) -> dict[str, Any]:
        """The metric vocabulary: canonical ids, names, aliases, valid ranges and groups."""
        return await rest(ctx, "GET", "metrics/ontology")

    @server.tool(annotations=READ)
    async def normalize_terms(ctx: Context,
                              terms: Annotated[list[dict[str, Any]], Field(
                                  min_length=1, max_length=200,
                                  description="[{text, expected_type?, context?}]; give context for acronyms")],
                              allow_semantic: bool = False) -> dict[str, Any]:
        """Map terms to ontology ids (Method, Sensor, Metric, …). Exact and alias matches only unless
        allow_semantic; an unmatched term stays unmatched."""
        return await rest(ctx, "POST", "ontology/normalize", body={"terms": terms, "allow_semantic": allow_semantic})

    @server.tool(annotations=READ)
    async def ontology_entities(ctx: Context, type: str | None = None, q: str | None = None,
                                limit: Annotated[int, Field(ge=1, le=1000)] = 100,
                                cursor: str | None = None) -> dict[str, Any]:
        """Browse the ontology (1,118 entities): filter by type and text."""
        return await rest(ctx, "GET", "ontology/entities", params={"type": type, "q": q, "limit": limit,
                                                                   "cursor": cursor})

    # ── graph ─────────────────────────────────────────────────────────────────
    @server.tool(annotations=READ)
    async def graph_paper(ctx: Context, paper: Annotated[str, Field(description="DOI or paper_id")],
                          include: str | None = None) -> dict[str, Any]:
        """A paper's neighbourhood in the knowledge graph: citations, methods, sensors, metrics, places."""
        return await rest(ctx, "GET", f"graph/papers/{paper}", params={"include": include})

    @server.tool(annotations=READ)
    async def graph_citations(ctx: Context, paper: Annotated[str, Field(description="DOI or paper_id")],
                              direction: Literal["out", "in", "both"] = "out", in_corpus_only: bool = False,
                              limit: Annotated[int, Field(ge=1, le=1000)] = 100,
                              cursor: str | None = None) -> dict[str, Any]:
        """Works a paper cites (out) or that cite it (in). Many cited works are stubs with a title only."""
        return await rest(ctx, "GET", f"graph/papers/{paper}/citations",
                          params={"direction": direction, "in_corpus_only": in_corpus_only or None, "limit": limit,
                                  "cursor": cursor})

    @server.tool(annotations=READ)
    async def papers_with_entity(ctx: Context,
                                 label: Literal["Method", "Sensor", "Metric", "Topic", "Country", "FloodEvent"],
                                 canonical_id: str, year_from: int | None = None, year_to: int | None = None,
                                 min_confidence: Annotated[float, Field(ge=0, le=1)] = 0.6,
                                 role: Literal["used", "mentioned"] | None = None,
                                 grounded: Literal["true", "false", "any"] = "true",
                                 limit: Annotated[int, Field(ge=1, le=1000)] = 100,
                                 cursor: str | None = None) -> dict[str, Any]:
        """Papers linked to an ontology entity (from normalize_terms), with the evidence of each link. Counts need
        a stated denominator and exclude duplicates (R-SCI-7)."""
        return await rest(ctx, "GET", f"graph/entities/{label}/{canonical_id}/papers",
                          params={"year_from": year_from, "year_to": year_to, "min_confidence": min_confidence,
                                  "role": role, "grounded": grounded, "limit": limit, "cursor": cursor})

    @server.tool(annotations=READ)
    async def graph_queries(ctx: Context) -> dict[str, Any]:
        """The catalogue of named, read-only graph queries and their parameters."""
        return await rest(ctx, "GET", "graph/queries")

    @server.tool(annotations=READ)
    async def run_graph_query(ctx: Context, name: str, params: dict[str, Any] | None = None,
                              limit: Annotated[int, Field(ge=1, le=1000)] = 100) -> dict[str, Any]:
        """Run one named graph query from graph_queries."""
        return await rest(ctx, "POST", f"graph/queries/{name}", body={"params": params or {}, "limit": limit})

    # ── resources: the documentation, as served at /v1/docs/pages ─────────────
    for page in docs_loader.page_names():
        server.resource(f"ghai://docs/{page}", name=page, title=f"docs/api/{page}.md",
                        description="GeoHydroAI Knowledge API documentation page",
                        mime_type="text/markdown")(_page_reader(page))

    # ── prompts: the standard workflows of AGENT_RULES.md §7 ─────────────────
    for wid, (title, steps) in workflows().items():
        server.prompt(name=wid.lower(), title=f"{wid} — {title}",
                      description=f"Workflow {wid} of AGENT_RULES.md: {title}")(_workflow_prompt(wid, title, steps))
    server.prompt(name="research", title="Research a question in the corpus",
                  description="How to query the corpus and choose the data source (QUERY_GUIDE.md §0), "
                              "applied to your question")(_research_prompt)
    return server


def _page_reader(name: str):
    def read() -> str:
        return docs_loader.read_page(name)
    return read


def research_instructions() -> str:
    """The agent prompt of QUERY_GUIDE.md §0 (its fenced block)."""
    section = docs_loader.read_page("QUERY_GUIDE").split("## 0.", 1)[-1].split("\n## ", 1)[0]
    m = re.search(r"```text\n(.*?)\n```", section, re.S)
    return m.group(1).strip() if m else section.strip()


def _research_prompt(question: str, project_id: str = "") -> str:
    who = f" The answer is for project `{project_id}`; pass it as project_id to the search tools." if project_id else ""
    return (f"{research_instructions()}\n\nQuestion: {question}{who}\n\n"
            "The full guide (sources, measured score ranges, filters, recipes) is the resource "
            "ghai://docs/QUERY_GUIDE; the rules are ghai://docs/AGENT_RULES.")


def _workflow_prompt(wid: str, title: str, steps: str):
    def prompt(project_id: str = "") -> str:
        who = f" for project `{project_id}`" if project_id else ""
        return (f"Run workflow {wid} — {title}{who}, with the `ghai` tools: each REST endpoint named below has a "
                f"tool of the same purpose (api_capabilities lists what is implemented).\n\n{steps}\n\n"
                f"Rules (full text: resource ghai://docs/AGENT_RULES):\n{docs_loader.top_rules_markdown()}\n\n"
                "Report what you checked, what failed, and the provenance (corpus_manifest_id, collection, span ids).")
    return prompt


def mount(app: Any, allowed_hosts: list[str] | None = None) -> MCPServer:
    """Build the server, put it at /mcp behind ApiKeyMiddleware; the caller runs ``session_manager.run()``."""
    server = build_server(app)
    security = TransportSecuritySettings(enable_dns_rebinding_protection=True,
                                         allowed_hosts=allowed_hosts or LOCAL_HOSTS,
                                         allowed_origins=LOCAL_ORIGINS)
    server.streamable_http_app(json_response=True, stateless_http=True, transport_security=security)
    endpoint = ApiKeyMiddleware(StreamableHTTPASGIApp(server.session_manager), app)
    app.router.add_route(MCP_PATH, endpoint, include_in_schema=False)
    return server
