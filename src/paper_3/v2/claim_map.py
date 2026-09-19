"""Locate the draft's claim IDs — `[V6.1]`, `[M1.1]`, `[X2.1]` — in v1.

Every numeric result in the manuscript is cited inline against a row of
`outputs/tables/manuscript_evidence_matrix.csv`, which lives in the SWOT-DNIPRO
repo and is not reachable from here. So the claim ID is the only handle this
repo has on the paper's own evidence, and everything downstream — the scientific
status layer, the figure inventory, the coverage report — keys on it.

29 unique IDs occur across 46 places in v1.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

#: `[V6.1]`, `[M1.1]`, `[X2.1]`, `[V10.4]`. Deliberately not `\w+` — a loose
#: pattern would also swallow `[1.994, +5.071]` confidence intervals and
#: `[doi:10.1029/…]` citations, both of which appear in this draft.
CLAIM_ID = re.compile(r"\[(?P<id>[A-Z]{1,2}\d{1,3}\.\d{1,2})\]")


@dataclass(frozen=True)
class ClaimRef:
    """One occurrence of a claim ID, with where it sits."""

    claim_id: str
    char_offset: int
    line: int
    section: str = ""


def _sort_key(claim_id: str) -> tuple[str, int, int, str]:
    """Order V1.2 < V1.10 < V2.1; tolerate C-01, G1, S1.1a (own-evidence ids)."""
    m = re.match(r"([A-Z]+)-?(\d+)(?:\.(\d+))?([a-z]?)", claim_id)
    if not m:
        return (claim_id, 0, 0, "")
    prefix, major, minor, suffix = m.groups()
    return prefix, int(major), int(minor or 0), suffix


def extract_claim_refs(text: str, headings: list[tuple[int, str]] | None = None
                       ) -> list[ClaimRef]:
    """Every occurrence, in document order.

    `headings` is an optional list of `(char_offset, heading_text)` used to
    attribute each occurrence to its section; without it `section` stays empty.
    """
    refs: list[ClaimRef] = []
    for match in CLAIM_ID.finditer(text):
        offset = match.start()
        section = ""
        if headings:
            for head_offset, head_text in headings:
                if head_offset <= offset:
                    section = head_text
                else:
                    break
        refs.append(ClaimRef(
            claim_id=match.group("id"),
            char_offset=offset,
            line=text.count("\n", 0, offset) + 1,
            section=section,
        ))
    return refs


def claim_ids(text: str) -> list[str]:
    """Unique IDs, sorted by prefix then numerically (V2.1 before V10.1)."""
    return sorted({m.group("id") for m in CLAIM_ID.finditer(text)}, key=_sort_key)


def load_draft(path: Path | None = None) -> str:
    from src.paper_3._utils import DRAFT_PATH

    target = Path(path) if path else DRAFT_PATH
    if not target.exists():
        raise FileNotFoundError(
            f"Draft not found at {target}. Copy "
            "Kakhovka_scientific_report_article_draft_v1.md into paper_3_audit/ "
            "(it lives in the Ubuntu-24.04 WSL distro, unreachable from here).")
    return target.read_text(encoding="utf-8")
