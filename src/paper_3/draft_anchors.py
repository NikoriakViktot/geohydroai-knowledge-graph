"""Read the manuscript draft so the analysis stays tied to what it actually claims.

Extracts three things from `Kakhovka_scientific_report_article_draft_v1.md`:

  * the section headings, so every thesis's `manuscript_anchor` can be checked
    against a section that exists rather than one someone remembered;
  * the numeric claims, so `numeric_anchor` values can be located in the text;
  * the informal citation list under §REFERENCES, which is currently marked
    "To be completed" and is the seed for `REFERENCES_CANDIDATES.csv`.

No network, no LLM — this is a parser, and it reports what it cannot find rather
than filling gaps in.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from src.paper_3._utils import DRAFT_PATH, OUT_DIR
from src.paper_3.theses import Thesis, load_theses

logger = logging.getLogger(__name__)

_HEADING = re.compile(r"^(#{1,4})\s+(.+?)\s*$", re.MULTILINE)
_BULLET = re.compile(r"^\s*[-*]\s+(.+?)\s*$", re.MULTILINE)
#: A number with a unit or a percentage — enough to locate a claim, not to parse it.
_NUMERIC = re.compile(
    r"[-+]?\d+(?:[.,]\d+)?\s*(?:cm/km|m/km|cm|mm|m\b|km|%|°|ppm)", re.IGNORECASE)
_DOI = re.compile(r"10\.\d{4,9}/[-._;()/:a-z0-9]+", re.IGNORECASE)


def parse_headings(text: str) -> list[dict]:
    return [{"level": len(m.group(1)), "title": m.group(2).strip()}
            for m in _HEADING.finditer(text)]


def _section_bodies(text: str) -> dict[str, str]:
    """Heading title → the text beneath it, up to the next heading."""
    matches = list(_HEADING.finditer(text))
    bodies: dict[str, str] = {}
    for i, m in enumerate(matches):
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        bodies[m.group(2).strip()] = text[start:end]
    return bodies


def parse_reference_stubs(text: str) -> list[dict]:
    """The bullet list under the REFERENCES heading, with any DOI it already has."""
    bodies = _section_bodies(text)
    body = ""
    for title, content in bodies.items():
        if title.strip().upper().startswith("REFERENCES"):
            body = content
            break
    if not body:
        logger.warning("no REFERENCES section found in the draft")
        return []

    stubs = []
    for m in _BULLET.finditer(body):
        entry = " ".join(m.group(1).split())
        doi = _DOI.search(entry)
        stubs.append({
            "citation_text": entry,
            "doi": doi.group(0).lower() if doi else "",
            "resolved": bool(doi),
        })
    return stubs


def parse_numeric_claims(text: str, max_claims: int = 400) -> list[dict]:
    """Numbers with units, each with the sentence it sits in."""
    bodies = _section_bodies(text)
    claims = []
    for section, body in bodies.items():
        for m in _NUMERIC.finditer(body):
            start = max(0, m.start() - 160)
            end = min(len(body), m.end() + 160)
            claims.append({
                "section": section,
                "value": m.group(0).strip(),
                "context": " ".join(body[start:end].split()),
            })
            if len(claims) >= max_claims:
                return claims
    return claims


def check_anchors(theses: list[Thesis], headings: list[dict]) -> list[dict]:
    """Confirm each thesis points at a section the draft really has.

    A thesis anchored to a section that does not exist is a thesis nobody will be
    able to act on, so this is reported loudly rather than tolerated.
    """
    titles = [h["title"] for h in headings]
    normalised = [" ".join(t.lower().split()) for t in titles]

    out = []
    for t in theses:
        anchor = " ".join(t.manuscript_anchor.lower().split())
        exact = anchor in normalised
        partial = [titles[i] for i, n in enumerate(normalised)
                   if not exact and (anchor in n or n in anchor)]
        out.append({
            "thesis_id": t.id,
            "manuscript_anchor": t.manuscript_anchor,
            "found": exact or bool(partial),
            "match_type": "exact" if exact else ("partial" if partial else "missing"),
            "closest": partial[0] if partial else ("" if exact else "— (no match)"),
        })
    return out


def run(out_dir: Path | None = None, draft_path: Path | None = None,
        theses: list[Thesis] | None = None) -> dict:
    """Parse the draft and write draft_anchors.json."""
    target = Path(out_dir) if out_dir else OUT_DIR
    path = Path(draft_path) if draft_path else DRAFT_PATH
    theses = theses if theses is not None else load_theses()

    if not path.exists():
        raise FileNotFoundError(
            f"Draft not found at {path}. Copy "
            "Kakhovka_scientific_report_article_draft_v1.md into paper_3_audit/ "
            "(it lives in the Ubuntu-24.04 WSL distro, which this session cannot "
            "reach directly).")

    text = path.read_text(encoding="utf-8")
    headings = parse_headings(text)
    anchors = check_anchors(theses, headings)
    stubs = parse_reference_stubs(text)
    claims = parse_numeric_claims(text)

    missing = [a for a in anchors if a["match_type"] == "missing"]
    for a in missing:
        logger.warning("%s anchors to %r, which is not a section in the draft",
                       a["thesis_id"], a["manuscript_anchor"])

    payload = {
        "draft_path": str(path),
        "n_headings": len(headings),
        "n_reference_stubs": len(stubs),
        "n_resolved_reference_dois": sum(1 for s in stubs if s["resolved"]),
        "n_numeric_claims": len(claims),
        "n_anchors_missing": len(missing),
        "headings": headings,
        "anchors": anchors,
        "reference_stubs": stubs,
        "numeric_claims": claims,
    }
    target.mkdir(parents=True, exist_ok=True)
    (target / "draft_anchors.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info("Draft: %d headings, %d anchors checked (%d missing), "
                "%d reference stubs, %d numeric claims",
                len(headings), len(anchors), len(missing), len(stubs), len(claims))
    return payload
