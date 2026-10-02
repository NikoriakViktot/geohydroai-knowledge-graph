"""Papers: corpus membership and identity from the layer of truth; full text from the TEI store."""

from __future__ import annotations

from collections import Counter

from fastapi import APIRouter, Depends, Query, Request
from starlette.concurrency import run_in_threadpool

from src.api.deps import provenance, require_scope
from src.api.documentation import route_doc
from src.api.problems import Problem
from src.contracts.api import (
    EvidenceSpan, PaperRef, ReferenceEntry, ReferencesResponse, ResolveBatchRequest, ResolveBatchResponse,
    ResolveBatchResult, ResolveResponse, SectionInfo, SectionsResponse, TableEntry, TablesResponse, TextResponse,
)
from src.contracts.identity import PaperIdentity

router = APIRouter()
MAX_TEXT_CHARS = 100_000
_INCLUDE = frozenset({"aliases", "files", "cohorts"})


def _ref(p: PaperIdentity | None) -> PaperRef | None:
    if p is None:
        return None
    return PaperRef(paper_id=p.paper_id, doi=p.doi, title=p.title, year=p.year, venue=p.venue,
                    identity_status=p.identity_status)


def _include(raw: str | None) -> frozenset[str]:
    wanted = frozenset(x.strip() for x in (raw or "").split(",") if x.strip())
    unknown = wanted - _INCLUDE
    if unknown:
        raise Problem("BAD_REQUEST", f"unknown include values {sorted(unknown)}; allowed: {sorted(_INCLUDE)}")
    return wanted


async def _resolve(**kwargs):
    from src.services import identity_store
    try:
        return await run_in_threadpool(lambda: identity_store.resolve(**kwargs))
    except identity_store.InvalidSelector as exc:
        code = "INVALID_DOI" if "not a DOI" in str(exc) else "BAD_REQUEST"
        raise Problem(code, str(exc)) from exc
    except Problem:
        raise
    except Exception as exc:
        raise Problem("STORE_UNAVAILABLE", f"postgres: {type(exc).__name__}", headers={"Retry-After": "30"}) from exc


@router.get("/papers/resolve", response_model=ResolveResponse, **route_doc("GET", "/papers/resolve"))
async def resolve(request: Request,
                  doi: str | None = None, paper_id: str | None = None, title: str | None = None,
                  year: int | None = None, file: str | None = None, openalex_id: str | None = None,
                  include: str | None = Query(None, description="comma list of aliases,files,cohorts"),
                  _=Depends(require_scope("read"))) -> ResolveResponse:
    found = await _resolve(doi=doi, paper_id=paper_id, file=file, openalex_id=openalex_id,
                           title=title, year=year, include=_include(include))
    if found is None:
        what = doi or paper_id or file or openalex_id or title
        raise Problem("NOT_IN_CORPUS", f"no paper in the corpus for {what!r}")
    return ResolveResponse(match=found.match, score=found.score, paper=found.paper,
                           canonical=_ref(found.canonical), provenance=provenance(request))


@router.post("/papers/resolve-batch", response_model=ResolveBatchResponse,
             **route_doc("POST", "/papers/resolve-batch"))
async def resolve_batch(request: Request, body: ResolveBatchRequest,
                        _=Depends(require_scope("read"))) -> ResolveBatchResponse:
    results: list[ResolveBatchResult] = []
    for item in body.items:
        selectors = {k: v for k, v in item.model_dump().items() if k not in ("key", "year") and v}
        if len(selectors) != 1:
            results.append(ResolveBatchResult(key=item.key, status="invalid",
                                              detail="give exactly one of doi, paper_id, file, openalex_id, title"))
            continue
        try:
            found = await _resolve(**selectors, year=item.year, include=frozenset())
        except Problem as exc:
            if exc.code in ("INVALID_DOI", "BAD_REQUEST"):
                results.append(ResolveBatchResult(key=item.key, status="invalid", detail=exc.detail))
                continue
            raise
        if found is None:
            results.append(ResolveBatchResult(key=item.key, status="not_in_corpus"))
        else:
            results.append(ResolveBatchResult(key=item.key, status="found", match=found.match,
                                              paper=_ref(found.paper), canonical=_ref(found.canonical)))
    summary = dict(Counter(r.status for r in results))
    return ResolveBatchResponse(results=results, summary=summary, provenance=provenance(request))


@router.get("/papers/{paper_id}", **route_doc("GET", "/papers/{paper_id}"))
async def paper(request: Request, paper_id: str, _=Depends(require_scope("read"))) -> dict:
    found = await _resolve(paper_id=paper_id, include=_INCLUDE)
    if found is None:
        raise Problem("NOT_FOUND", f"no paper with paper_id {paper_id!r}")
    body = found.paper.model_dump(mode="json")
    body["canonical"] = _ref(found.canonical).model_dump(mode="json") if found.canonical else None
    body["openalex"] = None  # planned (phase 1): OpenAlex enrichment fields
    body["provenance"] = provenance(request).model_dump(mode="json")
    return body


async def _document(paper_id: str):
    """(PaperIdentity, TEIDocument) of a corpus paper; 404 / 424 / 503 as documented."""
    from src.services import fulltext
    found = await _resolve(paper_id=paper_id, include=frozenset())
    if found is None:
        raise Problem("NOT_FOUND", f"no paper with paper_id {paper_id!r}")
    try:
        doc = await run_in_threadpool(fulltext.load_document, found.paper.paper_id)
    except Exception as exc:
        raise Problem("STORE_UNAVAILABLE", f"postgres or TEI store: {type(exc).__name__}",
                      headers={"Retry-After": "30"}) from exc
    if doc is None:
        extra = {"canonical": found.canonical.paper_id} if found.canonical else {}
        raise Problem("SOURCE_UNAVAILABLE", f"no GROBID full text for {paper_id!r}", extra=extra)
    return found.paper, doc


@router.get("/papers/{paper_id}/sections", response_model=SectionsResponse,
            **route_doc("GET", "/papers/{paper_id}/sections"))
async def sections(request: Request, paper_id: str, _=Depends(require_scope("read"))) -> SectionsResponse:
    from src.services import fulltext
    paper, doc = await _document(paper_id)
    info = fulltext.outline(doc)
    return SectionsResponse(paper=_ref(paper), sections=[SectionInfo(**x) for x in info["sections"]],
                            has_abstract=info["has_abstract"], figures=info["figures"], tables=info["tables"],
                            formulas=info["formulas"], provenance=provenance(request))


@router.get("/papers/{paper_id}/text", response_model=TextResponse, **route_doc("GET", "/papers/{paper_id}/text"))
async def text(request: Request, paper_id: str,
               section: str | None = Query(None, description="section number, handle (s3) or title prefix; 'abstract'"),
               page: int | None = Query(None, ge=1),
               q: str | None = Query(None, description="paragraphs containing all these words, ± 1 paragraph"),
               max_chars: int = Query(20_000, ge=1, le=MAX_TEXT_CHARS),
               _=Depends(require_scope("read"))) -> TextResponse:
    import hashlib

    from src.services import fulltext
    if not (section or page or q):
        raise Problem("BAD_REQUEST", "give at least one of section, page, q; use /sections to see the outline")
    paper, doc = await _document(paper_id)
    ref = _ref(paper)
    selected = fulltext.select(doc, section=section, page=page, q=q)
    spans, used, truncated = [], 0, False
    for p in selected:
        room = max_chars - used
        if room <= 0:
            truncated = True
            break
        body = p.text if len(p.text) <= room else p.text[:room]
        truncated = truncated or len(body) < len(p.text)
        spans.append(EvidenceSpan(
            span_id=hashlib.sha256(f"{paper.paper_id}\x00{p.passage_id}\x00{body}".encode()).hexdigest()[:16],
            paper=ref, passage_id=p.passage_id, kind=p.kind, section=p.section or None, section_n=p.section_n or None,
            page=p.page, char_start=0, char_end=len(body), text=body))
        used += len(body)
    return TextResponse(paper=ref, spans=spans, truncated=truncated, matched_passages=len(selected),
                        provenance=provenance(request))


@router.get("/papers/{paper_id}/references", response_model=ReferencesResponse,
            **route_doc("GET", "/papers/{paper_id}/references"))
async def references(request: Request, paper_id: str, _=Depends(require_scope("read"))) -> ReferencesResponse:
    from collections import Counter as _Counter

    from src.services import identity_store
    from src.services.identity import normalize_doi
    paper, doc = await _document(paper_id)
    refs = list(doc.references or [])
    markers = _Counter(c.ref_id for s in doc.all_sentences() for c in s.citations)
    try:
        matches = await run_in_threadpool(identity_store.match_references,
                                          [(r.doi, r.title, r.year) for r in refs])
    except Exception as exc:
        raise Problem("STORE_UNAVAILABLE", f"postgres: {type(exc).__name__}", headers={"Retry-After": "30"}) from exc
    entries = []
    for n, (r, (hit, how)) in enumerate(zip(refs, matches), start=1):
        entries.append(ReferenceEntry(
            n=n, xml_id=r.xml_id, raw=r.raw or None, title=r.title or None,
            authors=[a.full_name for a in r.authors], year=r.year, venue=r.journal or None,
            doi=normalize_doi(r.doi) if r.doi else None, cited_in_text=markers.get(r.xml_id, 0),
            in_corpus=_ref(hit), match=how))
    counts = {"total": len(entries), "with_doi": sum(e.doi is not None for e in entries),
              "in_corpus": sum(e.in_corpus is not None for e in entries),
              "cited_in_text": sum(e.cited_in_text > 0 for e in entries)}
    return ReferencesResponse(paper=_ref(paper), references=entries, counts=counts, provenance=provenance(request))


@router.get("/papers/{paper_id}/tables", response_model=TablesResponse, **route_doc("GET", "/papers/{paper_id}/tables"))
async def tables(request: Request, paper_id: str, _=Depends(require_scope("read"))) -> TablesResponse:
    from src.services.fulltext import _page
    paper, doc = await _document(paper_id)
    out = [TableEntry(table_id=f"tab_{k}", xml_id=t.xml_id or None, label=t.label or None, caption=t.caption or None,
                      page=_page(t.coords), rows=[[str(c) for c in row] for row in (t.rows or ())])
           for k, t in enumerate(doc.tables)]
    return TablesResponse(paper=_ref(paper), tables=out, provenance=provenance(request))
