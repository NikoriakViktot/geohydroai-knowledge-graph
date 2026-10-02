"""ghai_client — Python client for the GeoHydroAI Knowledge API (single file, needs only httpx).

    from ghai_client import GHAI
    api = GHAI.from_env()                      # GHAI_API_URL (…/v1), GHAI_API_KEY
    api.papers.resolve(doi="10.1029/2025GL120832")
    api.quotes.verify([{"source": "10.5194/nhess-19-2405-2019",
                        "quote": "does not accurately capture inundated cells"}])

Before you use results in a manuscript, read the agent rules: `api.agent_rules()`
(GET /v1/agent-rules). Every response carries `provenance`; keep it with what you use.

Errors come back as GHAIError with the API's problem code (`exc.code`, e.g.
NOT_IN_CORPUS, VALIDATION_FAILED, SOURCE_UNAVAILABLE) and, for 422, `exc.errors`.
The same file is served by the API at GET /v1/client.py.
"""

from __future__ import annotations

import os
import time
from collections.abc import Iterator
from typing import Any

import httpx

__version__ = "0.1.0"
__all__ = ["GHAI", "GHAIError"]

_RETRY_CODES = {503, 504}
_BATCH = 50


class GHAIError(Exception):
    """An application/problem+json answer, or a transport failure (status 0)."""

    def __init__(self, status: int, code: str, detail: str, problem: dict | None = None):
        super().__init__(f"{status} {code}: {detail}")
        self.status = status
        self.code = code
        self.detail = detail
        self.problem = problem or {}
        self.errors = self.problem.get("errors", [])


class _Group:
    def __init__(self, api: "GHAI"):
        self._api = api


class GHAI:
    """Synchronous client. `base_url` ends with /v1 (e.g. http://127.0.0.1:8090/v1)."""

    def __init__(self, base_url: str, api_key: str | None = None, *, timeout: float = 120.0,
                 retries: int = 2, client: httpx.Client | None = None):
        self.base_url = base_url.rstrip("/")
        self.retries = retries
        headers = {"Accept": "application/json", "User-Agent": f"ghai-client/{__version__}"}
        if api_key:
            headers["X-API-Key"] = api_key
        self._http = client or httpx.Client(timeout=timeout)
        self._headers = headers
        self.papers = Papers(self)
        self.quotes = Quotes(self)
        self.theses = Theses(self)
        self.doi = Doi(self)
        self.graph = Graph(self)
        self.metrics = Metrics(self)
        self.ontology = Ontology(self)

    @classmethod
    def from_env(cls, **kwargs) -> "GHAI":
        url = os.environ.get("GHAI_API_URL", "http://127.0.0.1:8090/v1")
        return cls(url, os.environ.get("GHAI_API_KEY"), **kwargs)

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "GHAI":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ── transport ──────────────────────────────────────────────────────────────

    def request(self, method: str, path: str, *, params: dict | None = None, json: Any = None,
                raw: bool = False) -> Any:
        url = f"{self.base_url}/{path.lstrip('/')}"
        params = {k: v for k, v in (params or {}).items() if v is not None}
        for attempt in range(self.retries + 1):
            try:
                r = self._http.request(method, url, params=params, json=json, headers=self._headers)
            except httpx.HTTPError as exc:
                if attempt < self.retries:
                    time.sleep(2 ** attempt)
                    continue
                raise GHAIError(0, "TRANSPORT", f"{type(exc).__name__}: {exc}") from exc
            if r.status_code in _RETRY_CODES and attempt < self.retries:
                wait = r.headers.get("Retry-After", "")
                time.sleep(min(float(wait), 30.0) if wait.isdigit() else 2 ** attempt)
                continue
            if r.status_code >= 400:
                try:
                    problem = r.json()
                except ValueError:
                    problem = {"code": "HTTP_ERROR", "detail": r.text[:300]}
                raise GHAIError(r.status_code, problem.get("code", "HTTP_ERROR"), problem.get("detail", ""), problem)
            if raw:
                return r.text
            return r.json()
        raise GHAIError(0, "RETRIES_EXHAUSTED", url)

    def get(self, path: str, **params) -> Any:
        return self.request("GET", path, params=params)

    def post(self, path: str, json: Any = None, **params) -> Any:
        return self.request("POST", path, params=params, json=json)

    def pages(self, path: str, *, items_key: str = "items", **params) -> Iterator[dict]:
        """Every item of a cursor-paginated GET endpoint."""
        cursor = None
        while True:
            body = self.get(path, **params, cursor=cursor)
            yield from body.get(items_key, [])
            cursor = body.get("next_cursor")
            if not cursor:
                return

    # ── system ─────────────────────────────────────────────────────────────────

    def health(self) -> dict:
        return self.get("health")

    def capabilities(self) -> dict:
        return self.get("capabilities")

    def stats(self) -> dict:
        return self.get("stats")

    def manifest(self) -> dict:
        return self.get("manifest")

    def agent_rules(self, markdown: bool = False) -> Any:
        if markdown:
            return self.request("GET", "agent-rules", params={"format": "markdown"}, raw=True)
        return self.get("agent-rules")


class Papers(_Group):
    def resolve(self, *, doi: str | None = None, paper_id: str | None = None, title: str | None = None,
                year: int | None = None, file: str | None = None, openalex_id: str | None = None,
                include: str | None = None) -> dict:
        return self._api.get("papers/resolve", doi=doi, paper_id=paper_id, title=title, year=year, file=file,
                             openalex_id=openalex_id, include=include)

    def resolve_batch(self, items: list[dict]) -> list[dict]:
        out = []
        for i in range(0, len(items), 500):
            out += self._api.post("papers/resolve-batch", {"items": items[i:i + 500]})["results"]
        return out

    def get(self, paper_id: str) -> dict:
        return self._api.get(f"papers/{paper_id}")

    def sections(self, paper_id: str) -> dict:
        return self._api.get(f"papers/{paper_id}/sections")

    def text(self, paper_id: str, *, section: str | None = None, page: int | None = None, q: str | None = None,
             max_chars: int | None = None) -> dict:
        return self._api.get(f"papers/{paper_id}/text", section=section, page=page, q=q, max_chars=max_chars)

    def references(self, paper_id: str) -> dict:
        return self._api.get(f"papers/{paper_id}/references")

    def tables(self, paper_id: str) -> dict:
        return self._api.get(f"papers/{paper_id}/tables")


class Quotes(_Group):
    def verify(self, items: list[dict], *, project_id: str | None = None) -> dict:
        """Verify quotations; batches of 50 are sent one after another and merged."""
        results, not_checked, summary, prov = [], [], {}, None
        for i in range(0, len(items), _BATCH):
            body = self._api.post("quotes/verify", {"items": items[i:i + _BATCH], "project_id": project_id})
            results += body["items"]
            not_checked += body.get("not_checked", [])
            prov = body.get("provenance")
            for k, v in body.get("summary", {}).items():
                summary[k] = summary.get(k, 0) + v
        return {"items": results, "not_checked": not_checked, "summary": summary, "provenance": prov}

    def verify_open_citations(self, document: dict, *, project_id: str | None = None) -> dict:
        """All items of an open_citations.json file, so that `found_in` can point across items.
        Items are packed so that no request expands to more than 50 quotations."""
        batches, current, n = [], [], 0
        for item in document.get("items", []):
            k = sum(len(c.get("quotations") or []) for c in item.get("citations") or [])
            if k == 0:
                continue
            if current and n + k > _BATCH:
                batches.append(current)
                current, n = [], 0
            current.append({"open_citations_item": item})
            n += k
        if current:
            batches.append(current)
        merged: dict = {"items": [], "not_checked": [], "summary": {}, "provenance": None}
        for batch in batches:
            body = self._api.post("quotes/verify", {"items": batch, "project_id": project_id})
            merged["items"] += body["items"]
            merged["not_checked"] += body.get("not_checked", [])
            merged["provenance"] = body.get("provenance")
            for k, v in body.get("summary", {}).items():
                merged["summary"][k] = merged["summary"].get(k, 0) + v
        return merged


class Theses(_Group):
    def validate(self, kind: str, project_id: str, document: Any, authored_by: dict | None = None) -> dict:
        """kind: 'theses' | 'atomic_claims'. A 422 GHAIError carries `errors` with locations."""
        return self._api.post("theses/validate", {"kind": kind, "project_id": project_id, "document": document,
                                                  "authored_by": authored_by})


class Doi(_Group):
    def get(self, doi: str, refresh: bool = False) -> dict:
        return self._api.get(f"doi/{doi}", refresh=refresh or None)

    def verify(self, entries: list[dict], *, project_id: str | None = None, refresh: bool = False) -> dict:
        """Entries: {key, doi, title, authors, year, journal, volume, issue, pages} or {"bibtex": "@article{…}"}."""
        results, summary, prov = [], {}, None
        for i in range(0, len(entries), _BATCH):
            body = self._api.post("doi/verify", {"entries": entries[i:i + _BATCH], "project_id": project_id,
                                                 "refresh": refresh})
            results += body["results"]
            prov = body.get("provenance")
            for k, v in body.get("summary", {}).items():
                summary[k] = summary.get(k, 0) + v
        return {"results": results, "summary": summary, "provenance": prov}


class Graph(_Group):
    def paper(self, doi_or_paper_id: str, include: str | None = None) -> dict:
        return self._api.get(f"graph/papers/{doi_or_paper_id}", include=include)

    def citations(self, doi_or_paper_id: str, *, direction: str = "out", in_corpus_only: bool = False) -> Iterator[dict]:
        return self._api.pages(f"graph/papers/{doi_or_paper_id}/citations", direction=direction,
                               in_corpus_only=in_corpus_only or None, limit=1000)

    def entity_papers(self, label: str, canonical_id: str, **filters) -> Iterator[dict]:
        """label: Method | Sensor | Metric | Topic | Country | FloodEvent; filters: year_from, year_to,
        min_confidence, role, grounded ('true' default | 'false' | 'any')."""
        return self._api.pages(f"graph/entities/{label}/{canonical_id}/papers", limit=1000, **filters)

    def queries(self) -> list[dict]:
        return self._api.get("graph/queries")["queries"]

    def run(self, name: str, params: dict | None = None, limit: int = 100) -> dict:
        return self._api.post(f"graph/queries/{name}", {"params": params or {}, "limit": limit})


class Metrics(_Group):
    def extract(self, *, text: str | None = None, tei_xml: str | None = None, paper_id: str | None = None,
                metrics: list[str] | None = None) -> dict:
        body = {k: v for k, v in (("text", text), ("tei_xml", tei_xml), ("paper_id", paper_id)) if v}
        return self._api.post("metrics/extract", {**body, "metrics": metrics or []})

    def facts(self, **filters) -> Iterator[dict]:
        """filters: metric, min, max, method, sensor, paper_id, doi, source, range_verdict."""
        return self._api.pages("metrics/facts", limit=1000, **filters)

    def ontology(self) -> list[dict]:
        return self._api.get("metrics/ontology")["metrics"]


class Ontology(_Group):
    def normalize(self, terms: list[str | dict], allow_semantic: bool = False) -> list[dict]:
        items = [t if isinstance(t, dict) else {"text": t} for t in terms]
        out = []
        for i in range(0, len(items), 200):
            out += self._api.post("ontology/normalize", {"terms": items[i:i + 200],
                                                         "allow_semantic": allow_semantic})["results"]
        return out

    def entities(self, type: str | None = None, q: str | None = None) -> Iterator[dict]:
        return self._api.pages("ontology/entities", type=type, q=q, limit=1000)
