"""What our own data support — the SWOT-DNIPRO audit, as machine-readable input.

Two independent layers gate a claim, and they answer different questions:

    knowledge graph  →  what does the published literature say?
    SWOT-DNIPRO audit →  what do our own data actually support?

A claim reaches the corrected manuscript only when both agree. The literature
layer alone is not enough: the audit has already found that `F₂ ≈ 0 → 0.31` is
unsupported and that the water-body-count claim is contradicted, and no amount
of citation makes a wrong sentence right.

**Unknown blocks by default.** A claim nobody has audited carries
`audit_status: UNKNOWN` and `blocks_publication: true`, so it cannot reach the
manuscript merely because nobody looked at it. That asymmetry is the point: the
cost of wrongly blocking a good claim is a review cycle; the cost of wrongly
passing a bad one is a retraction.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

STATUS_PATH = Path(__file__).resolve().parent / "scientific_claim_status.yaml"

#: Ordered from strongest to weakest. Only SUPPORTED lets a claim stand as it is.
AUDIT_STATUSES = (
    "SUPPORTED",                 # the data back it as written
    "SUPPORTED_WITH_LIMITATION", # backed, but only with the recorded caveat in the text
    "REVISE_UNCERTAINTY",        # the finding holds; the stated interval does not
    "REQUIRES_REANALYSIS",       # cannot be judged until an action is run
    "NOT_TESTABLE",              # the data to test it do not exist; a limitation, not a finding
    "UNSUPPORTED",               # the data do not back it
    "CONTRADICTED",              # the data point the other way
    "UNKNOWN",                   # nobody has looked — blocks by default
)

#: The statuses under which a claim may appear in the corrected manuscript
#: unchanged. Everything else needs a patch, a re-analysis, or removal.
#: SUPPORTED_WITH_LIMITATION and NOT_TESTABLE are rendered by the assembler
#: only together with their caveat, so they are not "as is" here.
PUBLISHABLE_AS_IS = frozenset({"SUPPORTED"})


@dataclass(frozen=True)
class ClaimStatus:
    """The audit's verdict on one claim ID."""

    claim_id: str
    audit_status: str = "UNKNOWN"
    findings: tuple[str, ...] = ()
    required_actions: tuple[str, ...] = ()
    note: str = ""
    #: Explicit override. `None` means "derive from audit_status", which is what
    #: keeps the default safe as new statuses are added.
    blocks_publication_override: bool | None = None

    @property
    def blocks_publication(self) -> bool:
        if self.blocks_publication_override is not None:
            return self.blocks_publication_override
        return self.audit_status not in PUBLISHABLE_AS_IS

    @property
    def audited(self) -> bool:
        return self.audit_status != "UNKNOWN"

    @property
    def needs_action(self) -> bool:
        return bool(self.required_actions)


def _as_tuple(value) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    return tuple(str(v).strip() for v in value if str(v).strip())


def load_status(path: Path | None = None) -> dict[str, ClaimStatus]:
    """Read scientific_claim_status.yaml. Missing file → empty mapping."""
    target = Path(path) if path else STATUS_PATH
    if not target.exists():
        logger.warning("%s missing — every claim will be treated as UNKNOWN",
                       target.name)
        return {}

    raw = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    out: dict[str, ClaimStatus] = {}
    for claim_id, entry in raw.items():
        entry = entry or {}
        out[claim_id] = ClaimStatus(
            claim_id=claim_id,
            audit_status=str(entry.get("audit_status", "UNKNOWN")).strip(),
            findings=_as_tuple(entry.get("findings")),
            required_actions=_as_tuple(entry.get("required_actions")),
            note=(entry.get("note") or "").strip(),
            blocks_publication_override=entry.get("blocks_publication"),
        )
    return out


def status_for(claim_id: str, status: dict[str, ClaimStatus] | None = None
               ) -> ClaimStatus:
    """The status of one claim. An unlisted claim is UNKNOWN, which blocks."""
    status = status if status is not None else load_status()
    return status.get(claim_id, ClaimStatus(claim_id=claim_id))


def blocks_publication(claim_id: str,
                       status: dict[str, ClaimStatus] | None = None) -> bool:
    return status_for(claim_id, status).blocks_publication


def blocking_claims(status: dict[str, ClaimStatus] | None = None) -> tuple[str, ...]:
    status = status if status is not None else load_status()
    return tuple(sorted(c for c, s in status.items() if s.blocks_publication))


def validate(status: dict[str, ClaimStatus],
             claim_ids: list[str], allow_extra: bool = False) -> list[str]:
    """Problems with the status file. Empty list means it is well-formed.

    ``allow_extra`` tolerates entries for claims the draft does not cite yet —
    the own-evidence ids (S1.x, K1.x, C-nn …) that v2 introduces.
    """
    problems: list[str] = []

    missing = sorted(set(claim_ids) - set(status))
    if missing:
        problems.append(
            f"{len(missing)} claim(s) in the draft have no status entry: "
            f"{', '.join(missing)}")

    unknown_ids = sorted(set(status) - set(claim_ids))
    if unknown_ids and not allow_extra:
        problems.append(
            f"status entries for claim(s) not in the draft: "
            f"{', '.join(unknown_ids)}")

    for claim_id, entry in sorted(status.items()):
        if entry.audit_status not in AUDIT_STATUSES:
            problems.append(f"{claim_id}: illegal audit_status "
                            f"{entry.audit_status!r}")
        if entry.audit_status == "REQUIRES_REANALYSIS" and not entry.required_actions:
            problems.append(f"{claim_id}: REQUIRES_REANALYSIS names no action, so "
                            f"nobody can tell what would unblock it")
        if entry.audit_status in ("UNSUPPORTED", "CONTRADICTED") and not entry.findings:
            problems.append(f"{claim_id}: {entry.audit_status} cites no finding — "
                            f"a verdict with no evidence behind it is an opinion")
        if entry.blocks_publication_override is False \
                and entry.audit_status not in PUBLISHABLE_AS_IS:
            problems.append(
                f"{claim_id}: blocks_publication is forced to false while "
                f"audit_status is {entry.audit_status} — record the reason in "
                f"`note`, because this overrides the default safety rule")

    return problems


def summary(status: dict[str, ClaimStatus] | None = None) -> dict:
    status = status if status is not None else load_status()
    counts: dict[str, int] = {}
    for entry in status.values():
        counts[entry.audit_status] = counts.get(entry.audit_status, 0) + 1
    actions = sorted({a for e in status.values() for a in e.required_actions})
    return {
        "n_claims": len(status),
        "by_status": counts,
        "n_blocking": len(blocking_claims(status)),
        "n_audited": sum(1 for e in status.values() if e.audited),
        "required_actions": actions,
    }


# ── importing a copied audit run ──────────────────────────────────────────────

def import_audit(audit_dir: Path, claim_ids: list[str]) -> dict[str, ClaimStatus]:
    """Parse a copied SWOT-DNIPRO audit run into ClaimStatus entries.

    The audit lives in the Ubuntu-24.04 distro and has to be copied into
    `paper_3_audit/audit_run/` by hand. Its exact layout is not known here, so
    this reads whichever of these it finds, in order, and reports what it could
    not map rather than guessing:

      1. `claim_status.yaml` / `.json` — already in this schema
      2. any `*.csv` with a claim-id column and a status column
      3. nothing → every claim stays UNKNOWN

    A claim the audit does not mention keeps UNKNOWN, and therefore keeps
    blocking. Silence in the audit is not approval.
    """
    audit_dir = Path(audit_dir)
    if not audit_dir.exists():
        raise FileNotFoundError(
            f"{audit_dir} not found. Copy the audit run from "
            "\\\\wsl.localhost\\Ubuntu-24.04\\home\\niko\\repo\\SWOT-DNIPRO\\"
            "audit_runs\\<run-id> into paper_3_audit/audit_run/ first.")

    for name in ("claim_status.yaml", "claim_status.yml", "claim_status.json"):
        candidate = audit_dir / name
        if candidate.exists():
            logger.info("Importing audit statuses from %s", candidate.name)
            return load_status(candidate)

    csvs = sorted(audit_dir.rglob("*.csv"))
    if csvs:
        return _import_from_csv(csvs, claim_ids)

    logger.warning("No recognisable status file in %s — all claims stay UNKNOWN. "
                   "Files present: %s", audit_dir,
                   ", ".join(p.name for p in sorted(audit_dir.iterdir())[:12]))
    return {}


_ID_COLUMNS = ("claim_id", "claim", "id", "claim id")
_STATUS_COLUMNS = ("audit_status", "status", "verdict", "assessment")
_FINDING_COLUMNS = ("findings", "finding", "finding_id", "evidence")
_ACTION_COLUMNS = ("required_actions", "actions", "action", "recommended_action")
_NOTE_COLUMNS = ("note", "notes", "reason", "comment")


def _pick(columns: list[str], candidates: tuple[str, ...]) -> str | None:
    lowered = {c.lower().strip(): c for c in columns}
    for name in candidates:
        if name in lowered:
            return lowered[name]
    return None


def _import_from_csv(paths: list[Path], claim_ids: list[str]
                     ) -> dict[str, ClaimStatus]:
    import pandas as pd

    known = set(claim_ids)
    out: dict[str, ClaimStatus] = {}

    for path in paths:
        try:
            frame = pd.read_csv(path)
        except Exception as exc:
            logger.debug("skipping %s: %s", path.name, exc)
            continue

        columns = list(frame.columns)
        id_col = _pick(columns, _ID_COLUMNS)
        status_col = _pick(columns, _STATUS_COLUMNS)
        if not id_col or not status_col:
            continue

        logger.info("Reading audit statuses from %s (%s → %s)",
                    path.name, id_col, status_col)
        finding_col = _pick(columns, _FINDING_COLUMNS)
        action_col = _pick(columns, _ACTION_COLUMNS)
        note_col = _pick(columns, _NOTE_COLUMNS)

        for row in frame.itertuples(index=False):
            claim_id = str(getattr(row, id_col, "") or "").strip()
            if claim_id not in known:
                continue
            raw_status = str(getattr(row, status_col, "") or "").strip().upper()
            raw_status = raw_status.replace(" ", "_").replace("-", "_")
            if raw_status not in AUDIT_STATUSES:
                logger.warning("%s: unmapped audit status %r — left UNKNOWN",
                               claim_id, raw_status)
                continue
            out[claim_id] = ClaimStatus(
                claim_id=claim_id,
                audit_status=raw_status,
                findings=_split_cell(getattr(row, finding_col, "") if finding_col else ""),
                required_actions=_split_cell(
                    getattr(row, action_col, "") if action_col else ""),
                note=str(getattr(row, note_col, "") or "").strip() if note_col else "",
            )

    unmapped = sorted(known - set(out))
    if unmapped:
        logger.warning("%d claim(s) not mentioned by the audit — they stay UNKNOWN "
                       "and keep blocking: %s", len(unmapped), ", ".join(unmapped))
    return out


def _split_cell(value) -> tuple[str, ...]:
    text = str(value or "").strip()
    if not text or text.lower() in {"nan", "none", "-"}:
        return ()
    return tuple(part.strip() for part in text.replace(";", ",").split(",")
                 if part.strip())


def write_status(status: dict[str, ClaimStatus], path: Path | None = None,
                 claim_ids: list[str] | None = None) -> Path:
    """Write the YAML, filling in UNKNOWN for any claim the audit did not mention."""
    target = Path(path) if path else STATUS_PATH
    complete = dict(status)
    for claim_id in (claim_ids or []):
        complete.setdefault(claim_id, ClaimStatus(claim_id=claim_id))

    from src.paper_3.v2.claim_map import _sort_key

    payload: dict[str, dict] = {}
    for claim_id in sorted(complete, key=_sort_key):
        entry = complete[claim_id]
        body: dict = {"audit_status": entry.audit_status}
        if entry.findings:
            body["findings"] = list(entry.findings)
        if entry.required_actions:
            body["required_actions"] = list(entry.required_actions)
        if entry.blocks_publication_override is not None:
            body["blocks_publication"] = entry.blocks_publication_override
        if entry.note:
            body["note"] = entry.note
        payload[claim_id] = body

    header = (
        "# What our own data support, per claim ID of the v1 draft.\n"
        "#\n"
        "# Source: the SWOT-DNIPRO audit run, copied into paper_3_audit/audit_run/\n"
        "# and imported with `scientific_status.import_audit`.\n"
        "#\n"
        "# UNKNOWN blocks publication by default. A claim nobody has audited must\n"
        "# not reach the corrected manuscript merely because nobody looked at it.\n"
        f"# Statuses: {' | '.join(AUDIT_STATUSES)}\n"
        "#\n"
        "# Only SUPPORTED lets a claim stand as written. Everything else needs a\n"
        "# recorded patch, a re-analysis, or removal.\n\n"
    )
    target.write_text(
        header + yaml.safe_dump(payload, sort_keys=False, allow_unicode=True),
        encoding="utf-8")
    return target
