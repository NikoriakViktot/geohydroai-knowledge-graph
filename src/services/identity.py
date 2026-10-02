"""Paper identity helpers (API_PLAN_v1/06_ROADMAP.md, WP0.3).

One DOI normaliser for the whole code base. Six slightly different copies exist
(paper_3/_utils, paper_audit/_utils, openalex_enrichment, openalex_actor,
tools/paper3_audit/corpus, build_candidates); new code uses this one, and the
copies are migrated to it step by step.
"""

from __future__ import annotations

import re

_DOI_PREFIX = re.compile(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*|urn:doi:)", re.IGNORECASE)
_DOI_SHAPE = re.compile(r"^10\.\d{4,9}/\S+$")
_TRAILING = ".,;:\"'"
_CLOSERS = {")": "(", "]": "[", "}": "{", ">": "<"}


def _strip_trailing(doi: str) -> str:
    """Drop sentence punctuation and *unbalanced* closing brackets at the end.

    A closing bracket that matches an opening one inside the DOI is part of it
    (e.g. ``10.1000/abc(1)``); one without a partner came from the surrounding text.
    """
    while doi:
        doi = doi.rstrip()
        if not doi:
            break
        last = doi[-1]
        if last in _TRAILING:
            doi = doi[:-1]
        elif last in _CLOSERS and doi.count(last) > doi.count(_CLOSERS[last]):
            doi = doi[:-1]
        else:
            break
    return doi


def normalize_doi(value: object) -> str | None:
    """Canonical DOI: no resolver prefix, lower case, no trailing punctuation.

    DOIs are case-insensitive by specification, so lower case is the canonical
    form. Returns None for empty input or anything that is not shaped like a DOI
    (``10.<registrant>/<suffix>``) — callers decide what to do with those.
    """
    if value is None:
        return None
    doi = str(value).strip().strip("<>")
    doi = _DOI_PREFIX.sub("", doi).strip()
    doi = _strip_trailing(doi).lower()
    if not _DOI_SHAPE.match(doi):
        return None
    return doi
