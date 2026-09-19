"""Cell parsing that keeps every flag the yearbook attaches to a number.

The legends (№229 p8, №230 p11) document «?» (doubtful) and «!» (value taken
from more-frequent observations); the footnote on the Херсон table (№230
p162) documents «/» as «спотворення рівня внаслідок підриву греблі Каховської
ГЕС». Footnote markers «*»/«**» point at a per-page note. Anything else — the
ice-condition letters Ш, З/Z, І and a trailing dash on winter values — is not
explained anywhere in either volume, so it is carried verbatim as
`undocumented_flags` and never interpreted.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

#: Legend-documented qualifiers, and the two footnote markers.
FLAG_MEANING = {
    "?": "doubtful",
    "!": "from_frequent_observations",
    "/": "dam_breach_distortion",
    "*": "footnote_marker",
}

#: «нб» — «не було» (did not occur); blank cells are simply absent.
NOT_OBSERVED = ("нб", "-", "—", "–")

_CELL = re.compile(r"^\s*(?P<num>-?\d+(?:[.,]\d+)?)\s*(?P<rest>.*?)\s*$")


@dataclass(frozen=True)
class Cell:
    raw: str
    value: float | None
    flags: str                      # legend-documented, in order of appearance
    meanings: tuple[str, ...]
    undocumented_flags: str         # everything else, verbatim, whitespace removed
    not_observed: bool = False

    @property
    def empty(self) -> bool:
        return self.value is None and not self.not_observed and not self.raw.strip()


def parse_cell(raw: str | None) -> Cell:
    text = (raw or "").strip()
    if not text:
        return Cell("", None, "", (), "")
    if text.lower() in NOT_OBSERVED:
        return Cell(text, None, "", (), "", not_observed=True)
    m = _CELL.match(text)
    if not m:
        # Not a number at all — keep it, value unknown.
        return Cell(text, None, "", (), re.sub(r"\s+", "", text))
    value = float(m.group("num").replace(",", "."))
    rest = re.sub(r"\s+", "", m.group("rest"))
    documented = "".join(ch for ch in rest if ch in FLAG_MEANING)
    undocumented = "".join(ch for ch in rest if ch not in FLAG_MEANING)
    meanings = tuple(dict.fromkeys(FLAG_MEANING[ch] for ch in documented))
    return Cell(text, value, documented, meanings, undocumented)


def to_height_m(level_cm: float | None, zero_of_post_m: float | None) -> float | None:
    """Absolute height in the post's height system: zero + level/100."""
    if level_cm is None or zero_of_post_m is None:
        return None
    return round(zero_of_post_m + level_cm / 100.0, 3)


YEAR_DEFAULT = 2023
DAYS_IN_MONTH_2023 = (31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)
