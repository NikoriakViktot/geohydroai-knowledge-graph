"""Evidence passages and verbatim-quote verification. No LLM calls in this module.

Every claim that reaches the gap matrix must be traceable to a sentence that
actually exists in a source document. `verify_quote` is what enforces that: a quote
the model paraphrased, invented, or spliced together from two passages is rejected
and excluded from every count, rather than being silently believed.

The normalisation is deliberately narrow. It absorbs the differences a PDF-to-text
pipeline introduces — smart quotes, ligatures, soft hyphens, collapsed whitespace —
and nothing else. It does not absorb reordered words, substituted synonyms, or
dropped clauses, because those are exactly the failures it exists to catch.
"""
from __future__ import annotations

import difflib
import re
import unicodedata
from dataclasses import dataclass, field

#: Accept a fuzzy match at or above this ratio. Below it, the quote is rejected.
#: 0.92 absorbs a handful of character-level PDF artefacts across a ~200-character
#: sentence while still rejecting a paraphrase, which typically scores below 0.85.
FUZZY_THRESHOLD = 0.92

#: Quotes shorter than this are not verifiable — too many short strings occur by
#: chance in unrelated passages for a match to mean anything.
MIN_QUOTE_CHARS = 25

_LIGATURES = {
    "ﬀ": "ff", "ﬁ": "fi", "ﬂ": "fl", "ﬃ": "ffi", "ﬄ": "ffl",
    "æ": "ae", "œ": "oe",
}
_QUOTES = {
    "‘": "'", "’": "'", "‚": "'", "‛": "'",
    "“": '"', "”": '"', "„": '"', "‟": '"',
    "′": "'", "″": '"',
}
_DASHES = {
    "‐": "-", "‑": "-", "‒": "-", "–": "-",
    "—": "-", "―": "-", "−": "-",
}
#: Soft hyphen and the zero-width family: deleted outright, never mapped to a space.
_INVISIBLE = dict.fromkeys(
    ["­", "​", "‌", "‍", "﻿"], ""
)

_TRANSLATE = {ord(k): v for k, v in
              {**_LIGATURES, **_QUOTES, **_DASHES, **_INVISIBLE}.items()}

_WS = re.compile(r"\s+")

SENT_SPLIT = re.compile(r"(?<=[.!?])\s+")


def normalize_text(raw: str) -> str:
    """Canonical form for comparison: NFKC, folded punctuation, collapsed space.

    Case is preserved — a quote that differs only in case is still a faithful
    copy, but lowercasing everything would let more false matches through the
    fuzzy stage, and we gain nothing by it.
    """
    if not raw:
        return ""
    text = unicodedata.normalize("NFKC", raw)
    text = text.translate(_TRANSLATE)
    return _WS.sub(" ", text).strip()


@dataclass(frozen=True)
class EvidencePassage:
    """One candidate passage offered to the model, with its provenance."""

    passage_id: str          # "P1", "P2", … — the handle the model cites
    paper_id: str
    text: str                # verbatim, as it appears in the source
    section: str
    char_offset: int
    source_file: str
    n_families_hit: int = 0
    chunk_id: str | None = None
    page: int | None = None

    def as_prompt_line(self) -> str:
        return f"[{self.passage_id}] {self.text}"


@dataclass
class QuoteVerdict:
    """Result of checking one model-supplied quote against the offered passages."""

    verified: bool
    passage_id: str | None = None
    similarity: float = 0.0
    repaired: bool = False
    #: The text to store. For a fuzzy match this is the PASSAGE's wording, never
    #: the model's — otherwise a near-miss paraphrase would be laundered into the
    #: output as if it were a quotation.
    text: str = ""
    reason: str = ""


def _best_window(quote: str, passage: str) -> float:
    """Highest SequenceMatcher ratio between `quote` and any same-length window.

    Sliding rather than whole-passage, because a passage carries ±1 sentence of
    context: comparing against the full passage would penalise a perfectly good
    quote for the context it deliberately excludes.
    """
    if not quote or not passage:
        return 0.0
    n = len(quote)
    if len(passage) <= n:
        return difflib.SequenceMatcher(None, quote, passage).ratio()

    best = 0.0
    # Step by a fraction of the quote length: fine enough not to miss the true
    # alignment, coarse enough to stay linear in practice.
    step = max(1, n // 8)
    for start in range(0, len(passage) - n + 1, step):
        window = passage[start:start + n]
        # quick_ratio is an upper bound and much cheaper; skip hopeless windows.
        sm = difflib.SequenceMatcher(None, quote, window)
        if sm.quick_ratio() < best:
            continue
        best = max(best, sm.ratio())
        if best >= 0.999:
            break
    return best


def verify_quote(quote: str, passages: dict[str, str]) -> QuoteVerdict:
    """Check that `quote` was copied from one of `passages`.

    `passages` maps passage_id → verbatim passage text.

    Exact substring match wins outright. Otherwise the best sliding-window
    similarity decides, and a fuzzy acceptance stores the passage's own wording.
    A quote spanning two passages matches neither and is rejected — which is the
    intended behaviour, not a limitation.
    """
    if not quote or not quote.strip():
        return QuoteVerdict(False, reason="empty quote")
    if not passages:
        return QuoteVerdict(False, reason="no passages offered")

    q_norm = normalize_text(quote)
    if len(q_norm) < MIN_QUOTE_CHARS:
        return QuoteVerdict(False, reason=f"quote shorter than {MIN_QUOTE_CHARS} characters")

    normalised = {pid: normalize_text(text) for pid, text in passages.items()}

    for pid, p_norm in normalised.items():
        if q_norm in p_norm:
            return QuoteVerdict(True, passage_id=pid, similarity=1.0,
                                text=quote.strip(), reason="exact match")

    best_pid, best_ratio = None, 0.0
    for pid, p_norm in normalised.items():
        ratio = _best_window(q_norm, p_norm)
        if ratio > best_ratio:
            best_pid, best_ratio = pid, ratio

    if best_pid is not None and best_ratio >= FUZZY_THRESHOLD:
        return QuoteVerdict(
            True, passage_id=best_pid, similarity=round(best_ratio, 4),
            repaired=True,
            # Store the source's wording, not the model's near-miss.
            text=_extract_window(q_norm, passages[best_pid], normalised[best_pid]),
            reason=f"fuzzy match at {best_ratio:.3f}",
        )

    return QuoteVerdict(False, passage_id=best_pid, similarity=round(best_ratio, 4),
                        reason=f"no passage matched (best {best_ratio:.3f} "
                               f"< {FUZZY_THRESHOLD})")


def _extract_window(q_norm: str, raw_passage: str, p_norm: str) -> str:
    """Return the passage's own text closest to the quote.

    Falls back to the whole normalised passage when the alignment cannot be
    located — storing too much of the source is safe; storing the model's text
    is not.
    """
    n = len(q_norm)
    if len(p_norm) <= n:
        return p_norm
    best_start, best_ratio = 0, 0.0
    step = max(1, n // 8)
    for start in range(0, len(p_norm) - n + 1, step):
        ratio = difflib.SequenceMatcher(None, q_norm, p_norm[start:start + n]).ratio()
        if ratio > best_ratio:
            best_start, best_ratio = start, ratio
    return p_norm[best_start:best_start + n].strip()


# ── passage construction ──────────────────────────────────────────────────────

def split_sentences(text: str) -> list[str]:
    """Split on sentence-final punctuation followed by whitespace."""
    return [s.strip() for s in SENT_SPLIT.split(text or "") if s.strip()]


def build_passages(
    sections: dict[str, str],
    paper_id: str,
    source_file: str,
    key_terms: tuple[tuple[str, ...], ...],
    negative_terms: tuple[str, ...] = (),
    max_passages: int = 12,
    context: int = 1,
    min_families: int = 1,
) -> list[EvidencePassage]:
    """Select the passages of a paper most likely to bear on a thesis.

    Scores each sentence by how many key-term FAMILIES it hits, drops sentences
    that hit a negative term without any positive family, and returns the top
    `max_passages` with ±`context` sentences either side so the model can see
    what a claim refers to.

    Returns [] when nothing in the paper hits at least `min_families` families —
    which is how a paper with a good embedding score but no actual topical
    content is removed from the candidate set.
    """
    negatives = tuple(t.lower() for t in negative_terms)
    families = tuple(tuple(t.lower() for t in fam) for fam in key_terms)

    scored: list[tuple[int, str, int, int, list[str]]] = []
    for section, text in sections.items():
        sentences = split_sentences(text)
        for i, sentence in enumerate(sentences):
            low = sentence.lower()
            hits = sum(1 for fam in families if any(t in low for t in fam))
            if hits < min_families:
                continue
            if any(neg in low for neg in negatives) and hits < 2:
                continue
            scored.append((hits, section, i, len(sentence), sentences))

    if not scored:
        return []

    # Prefer more families hit, then longer sentences — a longer sentence carries
    # more verifiable context and makes a fabricated quote easier to catch.
    scored.sort(key=lambda r: (-r[0], -r[3]))

    passages: list[EvidencePassage] = []
    seen: set[tuple[str, int]] = set()
    for hits, section, i, _len, sentences in scored:
        if len(passages) >= max_passages:
            break
        if (section, i) in seen:
            continue
        lo, hi = max(0, i - context), min(len(sentences), i + context + 1)
        for j in range(lo, hi):
            seen.add((section, j))
        text = " ".join(sentences[lo:hi])
        offset = sum(len(s) + 1 for s in sentences[:lo])
        passages.append(EvidencePassage(
            passage_id=f"P{len(passages) + 1}",
            paper_id=paper_id,
            text=text,
            section=section,
            char_offset=offset,
            source_file=source_file,
            n_families_hit=hits,
        ))
    return passages
