"""What controls are still needed, and a worksheet to fill them in.

The bottleneck at this point is not code — it is finding papers that genuinely
belong to each thesis and reading them. This module says exactly how many records
that is and writes a sheet to fill in, so the work is bounded and visible rather
than open-ended.

The four selection rules it encodes:

1. A control must be found **independently of this pipeline** — external search,
   citation chasing, or a paper already known to the researcher. A control the
   pipeline suggested is the pipeline marking its own work.
2. A critical thesis's holdout should be a **different paper**, not a different
   passage of the development control.
3. Development and holdout should be **methodologically different** — one
   ICESat-2, the other SWOT or classical hydraulics — so recall is not a test of
   one narrow vocabulary.
4. One paper must not be the **sole holdout for several critical theses**: their
   controls would then rise and fall together and measure one thing, not several.
"""
from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

from src.paper_3._utils import OUT_DIR
from src.paper_3.acceptance import MIN_CONTROLS_CRITICAL, MIN_CONTROLS_ORDINARY
from src.paper_3.theses import Thesis, load_theses

logger = logging.getLogger(__name__)

WORKSHEET = "CONTROL_WORKSHEET.csv"

WORKSHEET_COLUMNS = [
    "thesis_id", "block", "is_critical", "need", "role", "relevance",
    "difficulty", "status", "doi", "source_of_control", "expected_stage",
    "quote", "section", "page", "supports_what", "does_not_support", "note",
    "thesis_statement",
]


def requirements(theses: list[Thesis] | None = None) -> pd.DataFrame:
    """One row per control record required, existing or still to be found."""
    theses = theses if theses is not None else load_theses()
    rows = []

    for t in theses:
        needed = (MIN_CONTROLS_CRITICAL if t.is_critical else MIN_CONTROLS_ORDINARY)
        existing = [c for c in t.positive_controls if not c.excluded]

        # A critical thesis needs at least one direct development control and at
        # least one direct holdout; an ordinary one needs a single control.
        if t.is_critical:
            wanted = [("development", "direct", "easy"),
                      ("holdout", "direct", "hard")]
        else:
            wanted = [("development", "direct", "easy")]
        while len(wanted) < needed:
            wanted.append(("development", "direct", "easy"))

        used: set[str] = set()
        for role, relevance, difficulty in wanted:
            match = next(
                (c for c in existing
                 if c.doi not in used and c.role == role
                 and (c.relevance == relevance or not c.manually_checked)),
                None)
            if match is None:
                match = next((c for c in existing if c.doi not in used
                              and c.role == role), None)
            if match is not None:
                used.add(match.doi)
                status = ("verified" if match.verified else "needs reading")
                rows.append({
                    "thesis_id": t.id, "block": t.block,
                    "is_critical": t.is_critical, "need": "existing",
                    "role": match.role, "relevance": match.relevance,
                    "difficulty": match.difficulty, "status": status,
                    "doi": match.doi,
                    "source_of_control": match.source_of_control,
                    "expected_stage": match.expected_stage,
                    "quote": match.quote, "section": match.section,
                    "page": match.page, "supports_what": match.supports_what,
                    "does_not_support": match.does_not_support,
                    "note": match.note,
                    "thesis_statement": " ".join(t.statement.split()),
                })
            else:
                rows.append({
                    "thesis_id": t.id, "block": t.block,
                    "is_critical": t.is_critical, "need": "NEW",
                    "role": role, "relevance": relevance,
                    "difficulty": difficulty, "status": "to find",
                    "doi": "", "source_of_control": "",
                    "expected_stage": "semantic_retrieval",
                    "quote": "", "section": "", "page": "",
                    "supports_what": "", "does_not_support": "", "note": "",
                    "thesis_statement": " ".join(t.statement.split()),
                })

        # Controls beyond the requirement are still worth recording.
        for c in existing:
            if c.doi in used:
                continue
            rows.append({
                "thesis_id": t.id, "block": t.block, "is_critical": t.is_critical,
                "need": "extra", "role": c.role, "relevance": c.relevance,
                "difficulty": c.difficulty,
                "status": "verified" if c.verified else "needs reading",
                "doi": c.doi, "source_of_control": c.source_of_control,
                "expected_stage": c.expected_stage, "quote": c.quote,
                "section": c.section, "page": c.page,
                "supports_what": c.supports_what,
                "does_not_support": c.does_not_support, "note": c.note,
                "thesis_statement": " ".join(t.statement.split()),
            })

    return pd.DataFrame(rows, columns=WORKSHEET_COLUMNS)


def correlated_holdouts(theses: list[Thesis] | None = None) -> dict[str, list[str]]:
    """DOIs acting as the only holdout for more than one critical thesis.

    Correlated controls measure one thing while appearing to measure several.
    """
    theses = theses if theses is not None else load_theses()
    sole: dict[str, list[str]] = {}
    for t in theses:
        if not t.is_critical:
            continue
        holdouts = [c for c in t.holdout_controls if c.verified]
        if len(holdouts) == 1:
            sole.setdefault(holdouts[0].doi, []).append(t.id)
    return {doi: ids for doi, ids in sole.items() if len(ids) > 1}


def summary(theses: list[Thesis] | None = None) -> dict:
    theses = theses if theses is not None else load_theses()
    frame = requirements(theses)
    return {
        "records_required": int((frame["need"] != "extra").sum()),
        "records_existing": int((frame["need"] == "existing").sum()),
        "records_to_find": int((frame["need"] == "NEW").sum()),
        "records_extra": int((frame["need"] == "extra").sum()),
        "needs_reading": int((frame["status"] == "needs reading").sum()),
        "verified": int((frame["status"] == "verified").sum()),
        "unique_dois_existing": int(frame.loc[frame["doi"] != "", "doi"].nunique()),
        "correlated_holdouts": correlated_holdouts(theses),
    }


def run(out_dir: Path | None = None, theses: list[Thesis] | None = None) -> Path:
    """Write CONTROL_WORKSHEET.csv and log what is outstanding."""
    target = Path(out_dir) if out_dir else OUT_DIR
    theses = theses if theses is not None else load_theses()
    target.mkdir(parents=True, exist_ok=True)

    frame = requirements(theses)
    path = target / WORKSHEET
    frame.to_csv(path, index=False)

    stats = summary(theses)
    logger.info("Control worksheet → %s", path)
    logger.info("  %d records required: %d already listed, %d still to find",
                stats["records_required"], stats["records_existing"],
                stats["records_to_find"])
    logger.info("  %d listed controls still need reading; %d verified",
                stats["needs_reading"], stats["verified"])
    if stats["correlated_holdouts"]:
        logger.warning("  correlated holdouts (one paper, several critical "
                       "theses): %s", stats["correlated_holdouts"])
    return path
