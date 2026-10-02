"""Does a quotation (and its numbers) occur in the cited source? Deterministic, no LLM.

Matching reuses src/services/evidence_text.verify_quote (NFKC normalisation; exact, then a
sliding window accepted at ≥ 0.92). On top of it this service:
  * resolves the source (DOI, paper_id, or a cite key of the project),
  * splits quotations at "…" and requires the fragments in order in one passage,
  * tells a verbatim copy (FOUND_EXACT) from one equal after normalisation (FOUND_NORMALIZED),
  * returns the source's own sentences around the match (the evidence span),
  * checks expected numbers near the quotation, sign included (a range dash is not a minus),
  * reports attribution: the citations inside the matched sentences, i.e. a possible
    secondary citation (Iqbal 2023 quoting Hawker 2022).
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from dataclasses import dataclass

from src.contracts.api import (
    Attribution, EvidenceSpan, NotChecked, NumberCheck, PaperRef, QuoteItem, QuoteResult, Searched,
)
from src.contracts.identity import PaperIdentity
from src.services.evidence_text import MIN_QUOTE_CHARS, normalize_text, verify_quote
from src.services import fulltext
from src.services.identity import normalize_doi

_ELLIPSIS = re.compile(r"\s*(?:…|\.\.\.)\s*")
_CONTEXT = 80


def fragments(quote: str) -> list[str]:
    return [f.strip() for f in _ELLIPSIS.split(quote or "") if f.strip()]


def too_short(quote: str) -> bool:
    frags = fragments(quote)
    return not frags or max(len(normalize_text(f)) for f in frags) < MIN_QUOTE_CHARS


def paper_ref(p: PaperIdentity) -> PaperRef:
    return PaperRef(paper_id=p.paper_id, doi=p.doi, title=p.title, year=p.year, venue=p.venue,
                    identity_status=p.identity_status)


# ── source resolution ──────────────────────────────────────────────────────────

def resolve_source(source: str, project_id: str | None = None):
    """identity_store.Resolved for a DOI, a paper_id / file stem, or a cite key of the project."""
    from src.services import identity_store
    doi = normalize_doi(source)
    if doi:
        return identity_store.resolve(doi=doi)
    found = identity_store.resolve(paper_id=source) or identity_store.resolve(file=source)
    if found is None and project_id:
        found = _resolve_cite_key(source, project_id)
    return found


def _resolve_cite_key(key: str, project_id: str):
    from sqlalchemy import select

    from src.db.engine import session_scope
    from src.db.models import CiteKey
    from src.services import identity_store
    with session_scope() as s:
        row = s.execute(select(CiteKey.doi, CiteKey.paper_id)
                        .where(CiteKey.project_id == project_id, CiteKey.key == key)).first()
    if row is None:
        return None
    if row.doi:
        return identity_store.resolve(doi=row.doi)
    if row.paper_id:
        return identity_store.resolve(paper_id=row.paper_id) or identity_store.resolve(file=row.paper_id)
    return None


# ── matching ───────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class _Located:
    start: int          # in the normalised passage
    end: int
    exact: bool         # every fragment occurs verbatim (unnormalised) in the passage


def _locate(frags: list[str], passage: fulltext.Passage, p_norm: str) -> _Located | None:
    """All fragments, in order, in the normalised passage."""
    pos, start = 0, None
    for f in frags:
        i = p_norm.find(normalize_text(f), pos)
        if i < 0:
            return None
        start = i if start is None else start
        pos = i + len(normalize_text(f))
    raw_pos, exact = 0, True
    for f in frags:
        j = passage.text.find(f, raw_pos)
        if j < 0:
            exact = False
            break
        raw_pos = j + len(f)
    return _Located(start, pos, exact)


def _sentence_offsets(passage: fulltext.Passage, p_norm: str) -> list[tuple[int, int, int, int, fulltext.SentenceInfo]]:
    """(norm_start, norm_end, raw_start, raw_end, sentence) for each sentence that can be located."""
    out, n_pos, r_pos = [], 0, 0
    for s in passage.sentences:
        s_norm = normalize_text(s.text)
        i = p_norm.find(s_norm, n_pos) if s_norm else -1
        j = passage.text.find(s.text, r_pos) if s.text else -1
        if i < 0 or j < 0:
            continue
        out.append((i, i + len(s_norm), j, j + len(s.text), s))
        n_pos, r_pos = i + len(s_norm), j + len(s.text)
    return out


def _span_and_attribution(passage, p_norm, start, end, paper: PaperRef, doc) -> tuple[EvidenceSpan, Attribution]:
    sents = [x for x in _sentence_offsets(passage, p_norm) if x[0] < end and x[1] > start]
    if sents:
        r0, r1 = sents[0][2], sents[-1][3]
        text = passage.text[r0:r1]
        page = next((x[4].page for x in sents if x[4].page), passage.page)
    else:                                   # sentence boundaries unknown: the whole passage
        r0, r1, text, page = 0, len(passage.text), passage.text, passage.page
    refs = {r.xml_id: r for r in doc.references or []}
    labels, dois = [], []
    for *_, s in sents:
        for label, ref_id in s.cites:
            if label not in labels:
                labels.append(label)
            ref = refs.get(ref_id)
            d = normalize_doi(ref.doi) if ref is not None and ref.doi else None
            if d and d not in dois:
                dois.append(d)
    span = EvidenceSpan(span_id=hashlib.sha256(f"{paper.paper_id}\x00{passage.passage_id}\x00{text}".encode()).hexdigest()[:16],
                        paper=paper, passage_id=passage.passage_id, kind=passage.kind, section=passage.section or None,
                        section_n=passage.section_n or None, page=page, char_start=r0, char_end=r1, text=text)
    return span, Attribution(cites_other_sources=bool(labels), in_text_refs=labels, resolved_dois=dois)


# ── numbers ────────────────────────────────────────────────────────────────────

def _number_positions(value: str, text_norm: str) -> list[int]:
    """Positions of `value` in normalised text. The sign must agree: '-5.000' is not '5.000',
    while the dash of a range such as '1.12-1.61' is not a minus sign."""
    v = normalize_text(value).replace(" ", "")
    negative = v.startswith("-")
    body = re.escape(v.lstrip("+-")).replace(r"\.", "[.,]")
    out = []
    for m in re.finditer(rf"(?<![\d.,]){body}(?![\d]|[.,]\d)", text_norm):
        i = m.start()
        has_minus = i > 0 and text_norm[i - 1] == "-" and not (i > 1 and text_norm[i - 2].isalnum())
        if has_minus == negative:
            out.append(i - 1 if negative else i)
    return out


def check_numbers(values: list[str], passage: fulltext.Passage, start: int, end: int,
                  others: list[fulltext.Passage]) -> list[NumberCheck]:
    p_norm = normalize_text(passage.text)
    out = []
    for value in values:
        positions = _number_positions(value, p_norm)
        if positions:
            best = min(positions, key=lambda i: 0 if start <= i < end else min(abs(i - start), abs(i - end)))
            dist = 0 if start <= best < end else min(abs(best - start), abs(best - end))
            ctx = p_norm[max(0, best - _CONTEXT):best + _CONTEXT].strip()
            out.append(NumberCheck(value=value, found=True, distance_chars=dist, context=ctx))
        else:
            where = [o.passage_id for o in others
                     if o.passage_id != passage.passage_id and _number_positions(value, normalize_text(o.text))][:5]
            out.append(NumberCheck(value=value, found=False, elsewhere=where))
    return out


# ── one quotation ──────────────────────────────────────────────────────────────

def verify_one(item: QuoteItem, default_project: str | None = None) -> QuoteResult:
    base = {"key": item.key, "source": item.source, "quote": item.quote}
    found = resolve_source(item.source, item.project_id or default_project)
    if found is None:
        return QuoteResult(**base, status="SOURCE_UNAVAILABLE", text_source="none",
                           detail="the source is not in the corpus; 'not found' here says nothing about the paper")
    candidates = [p for p in (found.canonical, found.paper) if p is not None]
    doc, paper = None, candidates[0]
    for p in candidates:
        doc = fulltext.load_document(p.paper_id)
        if doc is not None:
            paper = p
            break
    ref = paper_ref(paper)
    if doc is None:
        return QuoteResult(**base, status="SOURCE_UNAVAILABLE", paper=ref, text_source="none",
                           detail=f"no GROBID full text for {paper.paper_id}")

    items = fulltext.passages(doc)
    searched = Searched(sections=len({p.passage_id.split(".p")[0] for p in items if p.kind == "paragraph"}),
                        passages=len(items), chars=sum(len(p.text) for p in items))
    frags = fragments(item.quote)
    longest = max(frags, key=lambda f: len(normalize_text(f)))
    verdict = verify_quote(longest, {p.passage_id: p.text for p in items})
    if not verdict.verified:
        return QuoteResult(**base, status="NOT_FOUND", score=verdict.similarity or None, paper=ref,
                           text_source="corpus_tei", searched=searched, detail=verdict.reason)

    passage = next(p for p in items if p.passage_id == verdict.passage_id)
    p_norm = normalize_text(passage.text)
    located = _locate(frags, passage, p_norm) if not verdict.repaired else None
    if located is not None:
        status, start, end, score = ("FOUND_EXACT" if located.exact else "FOUND_NORMALIZED"), located.start, located.end, 1.0
    else:
        # fuzzy, or the longest fragment matched but the others are not in order around it
        window = verdict.text if verdict.repaired else normalize_text(longest)
        start = max(0, p_norm.find(window))
        end = start + len(window)
        status, score = "FOUND_FUZZY", verdict.similarity
    span, attribution = _span_and_attribution(passage, p_norm, start, end, ref, doc)
    detail = None
    if verdict.repaired:
        detail = f"fuzzy match ({verdict.similarity:.3f}); quote the span text, not the request's wording"
    elif located is None:
        detail = "the longest fragment matched; the other fragments are not in order in the same passage"
    return QuoteResult(**base, status=status, score=score, paper=ref, span=span,
                       numbers=check_numbers(item.expected_numbers, passage, start, end, items),
                       attribution=attribution, text_source="corpus_tei", searched=searched, detail=detail)


# ── batches ────────────────────────────────────────────────────────────────────

def expand_open_citations(item: dict, project_id: str | None) -> tuple[list[QuoteItem], list[NotChecked]]:
    """One open_citations.json item → one QuoteItem per quotation of each citing sentence."""
    key = str(item.get("key") or "") or None
    source = normalize_doi(item.get("doi")) or key
    out, skipped = [], []
    for i, c in enumerate(item.get("citations") or []):
        for j, q in enumerate(c.get("quotations") or []):
            k = f"{key}#{i}.{j}" if key else None
            if not source:
                skipped.append(NotChecked(key=k, quote=q, reason="the item has neither a DOI nor a key"))
            elif too_short(q):
                skipped.append(NotChecked(key=k, quote=q, reason=f"shorter than {MIN_QUOTE_CHARS} characters: "
                                                                 "too common to prove anything"))
            else:
                out.append(QuoteItem(key=k, source=source, quote=q, manuscript_sentence=c.get("sentence"),
                                     project_id=project_id))
    return out, skipped


def cross_reference(items: list[QuoteItem], results: list[QuoteResult]) -> list[QuoteResult]:
    """NOT_FOUND items whose quotation was found for another work cited in the same manuscript
    sentence get `found_in`. open_citations.json attaches every quotation of a sentence to
    each work cited in it, so the quotation usually belongs to one of the co-cited works."""
    found: dict[tuple[str, str], list[str]] = {}
    for it, r in zip(items, results):
        if it.manuscript_sentence and r.status.startswith("FOUND"):
            found.setdefault((it.manuscript_sentence, normalize_text(it.quote)), []).append(r.key or r.source)
    out = []
    for it, r in zip(items, results):
        hits = found.get((it.manuscript_sentence, normalize_text(it.quote)), []) if it.manuscript_sentence else []
        if r.status == "NOT_FOUND" and hits:
            r = r.model_copy(update={"found_in": hits, "detail": f"{r.detail}; found in co-cited {', '.join(hits)}"})
        out.append(r)
    return out


def summarise(results: list[QuoteResult]) -> dict[str, int]:
    return dict(Counter(r.status for r in results))
