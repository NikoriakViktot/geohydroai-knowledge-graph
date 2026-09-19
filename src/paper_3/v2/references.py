"""Mandatory references — sources the author named as required, ready for the
v2 literature overlay.

v1 is immutable. Its §REFERENCES says «To be completed» and lists sources by
document, not by citation. The overlay (product A in `v2/__init__.py`) appends a
proper reference list; the entries that *must* be in it regardless of what the
harvest finds live in `briefs/technical_sources.yaml` with `mandatory: true` and
a `formal_citation`. This module renders that list, plus one PENDING marker for
the fact the sources themselves expose: three distinct realisations of the
BS-77 → EVRF2019 transition that the draft currently cites as one.
"""
from __future__ import annotations

from pathlib import Path

import yaml

from src.paper_3.v2.markers import Marker, render

TECHNICAL_PATH = Path(__file__).resolve().parents[1] / "briefs" / "technical_sources.yaml"

#: The marker id is fixed so that a re-run of the assembler is byte-identical.
REALISATIONS_MARKER_ID = "REF01"


def mandatory_references(path: Path | None = None) -> list[dict]:
    """Entries with `mandatory: true`, in file order. Each carries a formal citation."""
    raw = yaml.safe_load(Path(path or TECHNICAL_PATH).read_text(encoding="utf-8")) or []
    out: list[dict] = []
    for entry in raw:
        if not entry.get("mandatory"):
            continue
        if not (entry.get("formal_citation") or "").strip():
            raise ValueError(f"{entry.get('id')}: mandatory source without a formal_citation")
        out.append({
            "id": entry["id"],
            "source_type": entry.get("source_type", ""),
            "formal_citation": entry["formal_citation"].strip(),
            "formal_citation_en": (entry.get("formal_citation_en") or "").strip(),
            "url": entry.get("url", "") or "",
            "bears_on": list(entry.get("bears_on", [])),
            "key_facts": list(entry.get("key_facts", [])),
        })
    return out


def realisations_marker() -> Marker:
    """The one thing registering these sources revealed, said in the manuscript."""
    return Marker(
        id=REALISATIONS_MARKER_ID,
        title="Three realisations of BS-77 → EVRF2019 are cited as one",
        fields={
            "backs": "V1.2, V6.2",
            "section": "3.4 EPSG:9902 and vertical-reference data",
            "stub": ("EPSG:9902 (grid ua_2019z.asc, 154 points, SD 0.034 m, offset "
                     "0.079–0.285 m, mean 0.151 m) is not the CRS-EU «UA_KRON / NH to "
                     "EVRF2019zero» grid (idw_ua_2019_z.asc, IDW, Sept 2020), and neither is "
                     "the Стопхай et al. 2026 raster (UKG2017 vs EVRF2019zero_AMST: 0.000–0.250 "
                     "m, mean 0.070 m; 106 benchmarks 0…−0.104 m). §3.4 and §9 name them in "
                     "one breath («EPSG registry / BKG EVRF2019»); cite each for what it is "
                     "and say which one the gauge offsets +0.1715…+0.2157 m come from."),
            "source_data": "briefs/technical_sources.yaml: EPSG_9902, CRS_EU_UA_KRON_EVRF2019ZERO, STOPKHAI_2026_BS77_EVRF2019",
            "status": "not_started",
            "blocks_submission": "no",
        },
    )


def render_references_block(path: Path | None = None) -> str:
    """Markdown the overlay appends under §REFERENCES: the mandatory list, then the marker."""
    lines = ["### Mandatory references (author-designated)", ""]
    for ref in mandatory_references(path):
        lines.append(f"- {ref['formal_citation']}")
        if ref["formal_citation_en"]:
            lines.append(f"  - *{ref['formal_citation_en']}*")
    lines += ["", render(realisations_marker()), ""]
    return "\n".join(lines)
