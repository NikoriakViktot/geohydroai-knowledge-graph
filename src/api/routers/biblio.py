"""Bibliography: DOI metadata and verification against Crossref, OpenAlex and DataCite."""

from __future__ import annotations

from collections import Counter

from fastapi import APIRouter, Depends, Query, Request
from starlette.concurrency import run_in_threadpool

from src.api.deps import provenance, require_scope
from src.api.documentation import route_doc
from src.api.problems import Problem
from src.contracts.api import (
    BibAuditRequest, BibAuditResponse, BibFormatEntry, BibFormatRequest, BibFormatResponse, BibRenderRequest, BibRenderResponse, DoiResponse,
    DoiVerifyRequest, DoiVerifyResponse, LocateResponse, ManuscriptCitationsRequest, ManuscriptCitationsResponse,
    RenderedReference,
)
from src.services.identity import normalize_doi

router = APIRouter()


def _store_error(exc: Exception) -> Problem:
    return Problem("STORE_UNAVAILABLE", f"postgres (registry cache): {type(exc).__name__}", headers={"Retry-After": "30"})


@router.get("/doi/{doi:path}", response_model=DoiResponse, **route_doc("GET", "/doi/{doi}"))
async def doi_metadata(request: Request, doi: str,
                       refresh: bool = Query(False, description="ignore cached registry answers"),
                       _=Depends(require_scope("read"))) -> DoiResponse:
    from src.services import doi as service
    d = normalize_doi(doi)
    if d is None:
        raise Problem("INVALID_DOI", f"not a DOI: {doi!r}")
    try:
        reg = await run_in_threadpool(service.lookup, d, refresh)
        meta = service.metadata(d, reg)
        in_corpus = await run_in_threadpool(service._in_corpus, d) if meta else None
    except Exception as exc:
        raise _store_error(exc) from exc
    if meta is None:
        if "unavailable" in reg.fetched.values():
            raise Problem("UPSTREAM_TIMEOUT", f"a registry did not answer for {d}; not cached, retry later",
                          headers={"Retry-After": "60"}, extra={"fetched": reg.fetched})
        raise Problem("NOT_FOUND", f"no registry knows {d} (Crossref, DataCite, OpenAlex)", extra={"fetched": reg.fetched})
    return DoiResponse(**meta.model_dump(), in_corpus=in_corpus, provenance=provenance(request))


@router.post("/doi/verify", response_model=DoiVerifyResponse, **route_doc("POST", "/doi/verify"))
async def doi_verify(request: Request, body: DoiVerifyRequest,
                     _=Depends(require_scope("read"))) -> DoiVerifyResponse:
    from src.services import doi as service
    try:
        results = await run_in_threadpool(service.verify_many, body.entries, body.project_id, body.refresh)
    except Exception as exc:
        raise _store_error(exc) from exc
    return DoiVerifyResponse(results=results, summary=dict(Counter(r.verdict for r in results)),
                             provenance=provenance(request))


@router.get("/locate", response_model=LocateResponse, **route_doc("GET", "/locate"))
async def locate(request: Request,
                 q: str = Query(..., min_length=3, max_length=2000,
                                description="DOI, doi.org or publisher URL, ScienceDirect PII URL, arXiv id, paper_id"),
                 _=Depends(require_scope("read"))) -> LocateResponse:
    from src.services import locate as service
    try:
        body = await run_in_threadpool(service.locate, q, False)
    except Exception as exc:
        raise _store_error(exc) from exc
    if not body["doi"] and not body["in_corpus"]:
        raise Problem("NOT_FOUND", "; ".join(body["notes"]) or f"cannot resolve {q!r}")
    from src.services import filelinks
    base = str(request.base_url).rstrip("/")
    for f in body["files"]:
        if f["kind"] == "pdf" and f["exists"]:
            f["open_url"] = f"{base}/v1/files/{filelinks.sign(filelinks.relative(f['path']))}"
    return LocateResponse(**body, provenance=provenance(request))


@router.get("/files/{token}", **route_doc("GET", "/files/{token}"))
async def file_link(token: str):
    """A corpus PDF through a signed link from /locate (no API key: browsers cannot send one)."""
    from fastapi.responses import FileResponse

    from src.services import filelinks
    try:
        path = filelinks.verify(token)
    except filelinks.ExpiredLink as exc:
        raise Problem("LINK_EXPIRED", "this file link has expired; ask GET /v1/locate for a new one") from exc
    except filelinks.InvalidLink as exc:
        raise Problem("INVALID_LINK", str(exc)) from exc
    media = "application/pdf" if path.suffix.lower() == ".pdf" else "application/octet-stream"
    return FileResponse(path, media_type=media, filename=path.name, content_disposition_type="inline",
                        headers={"Cache-Control": "private, max-age=3600", "X-Robots-Tag": "noindex"})


@router.post("/manuscripts/citations", response_model=ManuscriptCitationsResponse,
             **route_doc("POST", "/manuscripts/citations"))
async def manuscript_citations(request: Request, body: ManuscriptCitationsRequest,
                               _=Depends(require_scope("read"))) -> ManuscriptCitationsResponse:
    from src.services import manuscripts
    result = await run_in_threadpool(manuscripts.find, body.manuscript, body.bibtex)
    if result["summary"].get("entries", 0) == 0:
        raise Problem("VALIDATION_FAILED", "the BibTeX text has no entries",
                      errors=[{"loc": ["body", "bibtex"], "msg": "no @type{key, …} entries", "type": "value_error"}])
    return ManuscriptCitationsResponse(**result, provenance=provenance(request))


def _project_keys(project_id: str | None) -> dict[str, str | None]:
    if not project_id:
        return {}
    from sqlalchemy import select

    from src.db.engine import session_scope
    from src.db.models import CiteKey
    with session_scope() as s:
        return {k: d for k, d in s.execute(select(CiteKey.key, CiteKey.doi).where(CiteKey.project_id == project_id))}


@router.post("/bib/format", response_model=BibFormatResponse, **route_doc("POST", "/bib/format"))
async def bib_format(request: Request, body: BibFormatRequest, _=Depends(require_scope("read"))) -> BibFormatResponse:
    from datetime import date

    from src.services import bibformat
    from src.services import doi as service

    def work():
        today = date.today()
        prepared, keys, dois = [], [], []
        for raw in body.dois:
            d = normalize_doi(raw)
            if d is None:
                prepared.append((raw, None, [f"{raw!r} is not a DOI"]))
                continue
            reg = service.lookup(d)
            meta = service.metadata(d, reg)
            if meta is None:
                why = "a registry did not answer; retry" if "unavailable" in reg.fetched.values() else \
                    "no registry knows this DOI"
                prepared.append((d, None, [why]))
                continue
            key, notes = bibformat.house_key(meta)
            prepared.append((d, meta, notes))
            keys.append(key)
            dois.append(d)
        resolved = iter(bibformat.disambiguate(keys, _project_keys(body.project_id), dois))
        entries = []
        for d, meta, notes in prepared:
            if meta is None:
                entries.append(BibFormatEntry(doi=d, notes=notes))
                continue
            key, collision = next(resolved)
            if collision:
                notes = notes + [f"key suffixed: the plain key is used for another work"
                                 + (f" in {body.project_id}" if body.project_id else " in this request")]
            entries.append(BibFormatEntry(doi=d, key=key, bibtex=bibformat.bibtex(meta, key, today),
                                          collision=collision, notes=notes, in_corpus=service._in_corpus(d)))
        return entries
    try:
        entries = await run_in_threadpool(work)
    except Exception as exc:
        raise _store_error(exc) from exc
    return BibFormatResponse(entries=entries, provenance=provenance(request))


@router.post("/bib/render", response_model=BibRenderResponse, **route_doc("POST", "/bib/render"))
async def bib_render(request: Request, body: BibRenderRequest, _=Depends(require_scope("read"))) -> BibRenderResponse:
    from src.services import bibformat, manuscripts
    from src.services.bibtex import parse_bib
    entries = {e["key"]: e["fields"] for e in parse_bib(body.bibtex)}
    if not entries:
        raise Problem("VALIDATION_FAILED", "the BibTeX text has no entries",
                      errors=[{"loc": ["body", "bibtex"], "msg": "no @type{key, …} entries", "type": "value_error"}])
    if body.keys is not None and body.manuscript is not None:
        raise Problem("VALIDATION_FAILED", "give keys or manuscript, not both",
                      errors=[{"loc": ["body"], "msg": "keys or manuscript", "type": "value_error"}])
    unresolved: list[str] = []
    if body.manuscript is not None:
        found = await run_in_threadpool(manuscripts.find, body.manuscript, body.bibtex)
        wanted = sorted({o["cite_key"] for o in found["occurrences"] if o["cite_key"]})
        unresolved = sorted({o["cite_text"] for o in found["occurrences"] if o["status"] != "resolved"})
    elif body.keys is not None:
        wanted = list(dict.fromkeys(body.keys))
        unresolved = [k for k in wanted if k not in entries]
        wanted = [k for k in wanted if k in entries]
    else:
        wanted = list(entries)
    ordered = sorted(wanted, key=lambda k: bibformat.sort_key(entries[k]))
    refs = [RenderedReference(key=k, text=bibformat.render_entry(entries[k], body.style)) for k in ordered]
    return BibRenderResponse(references=refs, unresolved_keys=unresolved,
                             uncited_entries=sorted(set(entries) - set(wanted)) if body.manuscript is not None else [],
                             provenance=provenance(request))


@router.post("/bib/audit", response_model=BibAuditResponse, **route_doc("POST", "/bib/audit"))
async def bib_audit(request: Request, body: BibAuditRequest, _=Depends(require_scope("read"))) -> BibAuditResponse:
    from src.services import bibaudit
    try:
        result = await run_in_threadpool(bibaudit.audit, body.bibtex, body.project_id, body.search_missing)
    except ValueError as exc:
        raise Problem("PAYLOAD_TOO_LARGE", str(exc)) from exc
    except Exception as exc:
        raise _store_error(exc) from exc
    if result["summary"]["entries"] == 0:
        raise Problem("VALIDATION_FAILED", "the BibTeX text has no entries",
                      errors=[{"loc": ["body", "bibtex"], "msg": "no @type{key, …} entries", "type": "value_error"}])
    return BibAuditResponse(**result, provenance=provenance(request))
