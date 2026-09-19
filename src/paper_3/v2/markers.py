"""The placeholder convention: what the manuscript still needs, said in the manuscript.

One grammar, four kinds, rendered as a block quote:

    > **[PENDING FIG16]** Sentinel-2 before/after fragmentation map of the pool
    > `backs: M3.1, M3.2` · `section: 6.6 Sentinel-2 planform transformation`
    > `produces: SWOT-DNIPRO scripts/fig_sentinel_planform.py`
    > `scientific_status: blocked` · `unblock_by: A3, A4, A5`
    > `status: not_started` · `blocks_submission: yes`

Four properties this buys:

* **machine-checkable** — a submission gate is `assert not find_marker_ids(text)`;
* **visually obvious** — a block quote renders in every markdown renderer, and
  even `build_docx.py`'s hand-rolled parser, which understands none of this,
  emits the literal text. Ugly, but it fails *safe*: an invisible placeholder is
  exactly how a placeholder reaches a reviewer;
* **actionable** — `produces:` names the script in the other repo, `backs:` the
  claim IDs, `unblock_by:` the audit actions;
* **one source** — `V2_OPEN_ITEMS.md` is parsed back out of the assembled
  document rather than maintained beside it. `audit_numeric_claims.py` in this
  repo is twelve hand-typed tuples that have drifted from the manuscript they
  describe; this is the fix for that failure mode.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Mapping

KIND_BY_PREFIX = {
    "FIG": "figure",
    "TAB": "table",
    "OPEN": "open_item",
    "REF": "reference",
}
PREFIX_BY_KIND = {v: k for k, v in KIND_BY_PREFIX.items()}

STATUSES = ("not_started", "in_progress", "blocked", "ready", "done")

#: Fields each kind must carry. A marker missing one of these cannot be acted on,
#: which makes it decoration rather than a work item.
REQUIRED_FIELDS = {
    "figure": ("backs", "section", "produces", "status", "blocks_submission"),
    "table": ("backs", "section", "produces", "status", "blocks_submission"),
    "open_item": ("section", "next_step", "status", "blocks_submission"),
    "reference": ("section", "stub", "status", "blocks_submission"),
}

#: The head line. `find_marker_ids` and `parse_markers` share it, which is what
#: makes "no marker was silently dropped" provable rather than hoped for.
MARKER_HEAD = re.compile(
    r"^> \*\*\[PENDING (?P<id>(?:FIG|TAB|OPEN|REF)\d{2})\]\*\* (?P<title>.+?)\s*$",
    re.MULTILINE)

_FIELD = re.compile(r"`(?P<key>[a-z_]+):\s*(?P<value>[^`]*)`")

_TRUE = {"yes", "true", "1"}


@dataclass(frozen=True)
class Marker:
    """One outstanding piece of work, recorded where it is needed."""

    id: str
    title: str
    fields: Mapping[str, str] = field(default_factory=dict)
    char_offset: int = -1

    @property
    def kind(self) -> str:
        return KIND_BY_PREFIX.get(self.id[:-2], "unknown")

    @property
    def backs(self) -> tuple[str, ...]:
        return _split(self.fields.get("backs", ""))

    @property
    def unblock_by(self) -> tuple[str, ...]:
        return _split(self.fields.get("unblock_by", ""))

    @property
    def section(self) -> str:
        return self.fields.get("section", "").strip()

    @property
    def status(self) -> str:
        return self.fields.get("status", "").strip()

    @property
    def blocks_submission(self) -> bool:
        return self.fields.get("blocks_submission", "").strip().lower() in _TRUE

    @property
    def scientifically_blocked(self) -> bool:
        return self.fields.get("scientific_status", "").strip().lower() == "blocked"


def _split(value: str) -> tuple[str, ...]:
    return tuple(p.strip() for p in (value or "").replace(";", ",").split(",")
                 if p.strip())


#: Field order in the rendered block. Fixed so that rendering is deterministic
#: and a re-run of the assembler produces byte-identical output.
_FIELD_ORDER = ("backs", "section", "next_step", "stub", "produces",
                "source_data", "scientific_status", "unblock_by",
                "depends_on", "status", "blocks_submission", "owner", "note")

#: How many fields go on one line before wrapping.
_PER_LINE = 2


def render(marker: Marker) -> str:
    """Render a marker as its canonical block quote."""
    lines = [f"> **[PENDING {marker.id}]** {marker.title.strip()}"]

    present = [(k, marker.fields[k]) for k in _FIELD_ORDER
               if k in marker.fields and str(marker.fields[k]).strip()]
    extra = sorted(k for k in marker.fields
                   if k not in _FIELD_ORDER and str(marker.fields[k]).strip())
    present += [(k, marker.fields[k]) for k in extra]

    for i in range(0, len(present), _PER_LINE):
        chunk = present[i:i + _PER_LINE]
        lines.append("> " + " · ".join(f"`{k}: {v}`" for k, v in chunk))
    return "\n".join(lines)


def find_marker_ids(text: str) -> list[str]:
    """Every marker ID the head pattern can see. The drop-detection oracle."""
    return [m.group("id") for m in MARKER_HEAD.finditer(text)]


def parse_markers(text: str) -> list[Marker]:
    """Parse every marker, in document order.

    Fields are read from the block-quote lines immediately following the head,
    so a marker stops at the first line that is not part of its own quote.
    """
    out: list[Marker] = []
    matches = list(MARKER_HEAD.finditer(text))

    for i, match in enumerate(matches):
        body_start = match.end()
        body_end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        # `$` under re.MULTILINE stops *before* the newline, so the body opens
        # with the line break that ends the head. Drop it, or the first
        # splitlines() entry is "" and the continuation loop exits at once.
        body = text[body_start:body_end].lstrip("\n")

        # Stop at the first line that is not a continuation of this quote.
        collected: list[str] = []
        for line in body.splitlines():
            if not line.startswith(">"):
                break
            collected.append(line)

        fields = {m.group("key"): m.group("value").strip()
                  for m in _FIELD.finditer("\n".join(collected))}
        out.append(Marker(id=match.group("id"), title=match.group("title").strip(),
                          fields=fields, char_offset=match.start()))
    return out


def validate(marker: Marker) -> list[str]:
    """Problems with one marker. Empty list means it is actionable."""
    problems: list[str] = []

    if marker.kind == "unknown":
        problems.append(f"{marker.id}: unknown kind prefix")
        return problems
    if not marker.title:
        problems.append(f"{marker.id}: no title")

    for required in REQUIRED_FIELDS[marker.kind]:
        if not marker.fields.get(required, "").strip():
            problems.append(f"{marker.id}: missing required field {required!r}")

    status = marker.status
    if status and status not in STATUSES:
        problems.append(f"{marker.id}: illegal status {status!r} "
                        f"(expected one of {', '.join(STATUSES)})")

    blocks = marker.fields.get("blocks_submission", "").strip().lower()
    if blocks and blocks not in _TRUE | {"no", "false", "0"}:
        problems.append(f"{marker.id}: blocks_submission must be yes or no, "
                        f"got {blocks!r}")

    if marker.scientifically_blocked and not marker.unblock_by:
        problems.append(f"{marker.id}: scientific_status is blocked but nothing "
                        f"says what would unblock it")

    return problems


def validate_all(markers: list[Marker]) -> list[str]:
    """Problems across a whole set, including duplicate IDs."""
    problems: list[str] = []
    seen: dict[str, int] = {}
    for marker in markers:
        seen[marker.id] = seen.get(marker.id, 0) + 1
        problems.extend(validate(marker))
    for marker_id, count in sorted(seen.items()):
        if count > 1:
            problems.append(f"{marker_id}: appears {count} times — IDs must be "
                            f"unique or the checklist cannot address them")
    return problems


def assert_none_remain(text: str) -> None:
    """The submission gate. A manuscript with a PENDING marker is not finished."""
    remaining = find_marker_ids(text)
    if remaining:
        raise AssertionError(
            f"{len(remaining)} PENDING marker(s) still in the manuscript: "
            f"{', '.join(remaining)}")
