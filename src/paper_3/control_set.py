"""Freeze the control set, so tuning cannot quietly change what it is tested against.

Holding controls out of retrieval stops the crudest circularity. It does not stop
the softer one:

    pick a holdout → find it is hard → swap in an easier DOI → 100% recall

So the *set* is frozen too. After `--step freeze-controls`, `key_terms`,
`search_queries` and the gates stay editable — that is what development controls
are for — but the identity of a control cannot move: its DOI, role, thesis,
relevance and difficulty are all hashed.

A control that turns out on reading not to be what it was taken for can still be
removed, but only as an explicit `excluded: true` with a reason and ideally a
`replacement_doi`. That leaves the swap in the audit trail instead of erasing it.
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from src.paper_3._utils import OUT_DIR
from src.paper_3.theses import Thesis, load_theses

logger = logging.getLogger(__name__)

CONTROL_SET_FILE = "CONTROL_SET.json"

#: The fields whose change would alter what the benchmark tests. Everything else
#: about a control — the quote, the note, whether it has been read yet — may
#: still be filled in after the freeze.
IDENTITY_FIELDS = ("doi", "role", "relevance", "difficulty", "expected_stage")


def control_identity(thesis_id: str, control) -> dict:
    return {"thesis_id": thesis_id,
            **{f: getattr(control, f) for f in IDENTITY_FIELDS},
            "excluded": control.excluded}


def identities(theses: list[Thesis]) -> list[dict]:
    rows = [control_identity(t.id, c) for t in theses for c in t.positive_controls]
    return sorted(rows, key=lambda r: (r["thesis_id"], r["doi"]))


def fingerprint(theses: list[Thesis]) -> str:
    """SHA-256 over the control identities alone, not the whole theses file."""
    payload = json.dumps(identities(theses), sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def load(out_dir: Path | None = None) -> dict:
    path = (Path(out_dir) if out_dir else OUT_DIR) / CONTROL_SET_FILE
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("%s unreadable: %s", path.name, exc)
        return {}


def freeze(out_dir: Path | None = None, theses: list[Thesis] | None = None,
           force: bool = False) -> dict:
    """Record the control set as frozen. Refuses to move it silently."""
    from src.paper_3.freeze import RETRIEVAL_RULES_VERSION

    target = Path(out_dir) if out_dir else OUT_DIR
    theses = theses if theses is not None else load_theses()
    target.mkdir(parents=True, exist_ok=True)

    current = fingerprint(theses)
    existing = load(target)

    # Completeness is a precondition for the FIRST freeze; drift is the concern
    # for every one after it. Checking drift first keeps the more specific
    # message when both apply.
    if not existing.get("frozen") and not force:
        # Freezing an incomplete set is worse than not freezing: every control
        # found later registers as drift, so the freeze gets forced, and forcing
        # becomes routine. Select first, freeze once.
        from src.paper_3.control_plan import summary as control_summary

        stats = control_summary(theses)
        if stats["records_to_find"]:
            raise RuntimeError(
                f"The control set is not complete yet: "
                f"{stats['records_to_find']} of {stats['records_required']} "
                f"required records are still to find, and "
                f"{stats['needs_reading']} listed controls have not been read.\n"
                f"Freeze once the set is chosen — freezing now would make every "
                f"control found later look like drift. Run `--step controls` to "
                f"see what is outstanding, or --force to freeze a partial set "
                f"knowingly.")

    if existing.get("frozen") and existing.get("sha256") != current and not force:
        drift = diff(theses, existing)
        raise RuntimeError(
            "The control set is frozen and has changed since:\n  "
            + "\n  ".join(drift)
            + "\n\nA control may be retired with `excluded: true` plus an "
              "`exclusion_reason` (and ideally a `replacement_doi`), which keeps "
              "the swap in the audit trail. Pass --force only to re-freeze "
              "deliberately; the previous set is kept in `history`.")

    payload = {
        "frozen": True,
        "frozen_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "sha256": current,
        "retrieval_rules_version_before_tuning": RETRIEVAL_RULES_VERSION,
        "n_controls": sum(len(t.positive_controls) for t in theses),
        "n_verified": sum(len(t.verified_controls) for t in theses),
        "n_holdout": sum(len(t.holdout_controls) for t in theses),
        "identity_fields": list(IDENTITY_FIELDS),
        "controls": identities(theses),
        "history": ([*existing.get("history", []),
                     {k: v for k, v in existing.items() if k != "history"}]
                    if existing else []),
    }
    (target / CONTROL_SET_FILE).write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info("Control set frozen: %d controls (%d verified, %d holdout), "
                "sha256 %s, rules v%s",
                payload["n_controls"], payload["n_verified"],
                payload["n_holdout"], current[:12],
                payload["retrieval_rules_version_before_tuning"])
    return payload


def diff(theses: list[Thesis], frozen: dict | None = None,
         out_dir: Path | None = None) -> list[str]:
    """Human-readable differences between the live controls and the frozen set."""
    frozen = frozen if frozen is not None else load(out_dir)
    if not frozen.get("frozen"):
        return []

    before = {(r["thesis_id"], r["doi"]): r for r in frozen.get("controls", [])}
    after = {(r["thesis_id"], r["doi"]): r for r in identities(theses)}

    out: list[str] = []
    for key in sorted(set(before) - set(after)):
        out.append(f"REMOVED  {key[0]} {key[1]} — retire it with `excluded: true` "
                   f"and a reason instead of deleting it")
    for key in sorted(set(after) - set(before)):
        out.append(f"ADDED    {key[0]} {key[1]}")
    for key in sorted(set(before) & set(after)):
        changed = [f for f in IDENTITY_FIELDS
                   if before[key].get(f) != after[key].get(f)]
        if changed:
            out.append(
                f"CHANGED  {key[0]} {key[1]} — "
                + ", ".join(f"{f}: {before[key].get(f)!r} → {after[key].get(f)!r}"
                            for f in changed))
    return out


def status(theses: list[Thesis] | None = None,
           out_dir: Path | None = None) -> tuple[bool, list[str]]:
    """(frozen_and_unchanged, drift). An unfrozen set reports (False, [])."""
    theses = theses if theses is not None else load_theses()
    frozen = load(out_dir)
    if not frozen.get("frozen"):
        return False, []
    drift = diff(theses, frozen)
    return not drift, drift
