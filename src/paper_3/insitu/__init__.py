"""In-situ gauge data for Paper 3, extracted from the 2023 hydromet yearbooks.

Two volumes of «Щорічні дані про режим та ресурси вод морів і морських гирл
річок» (ГМЦ ЧАМ, ДСНС України, 2023):

  * Інв. №229 — Том 2, the seas volume: coastal stations, incl. Очаків and
    Парутине with hourly-based sea level and a per-station correction to a
    unified sea zero;
  * Інв. №230 — Том 2(2), the river-mouths volume: the Дніпро–Буг mouth area,
    pp 146–195, with the estuary posts Херсон, Касперівка, Олександрівка,
    Миколаїв, Очаків, Станіслав, Геройське, Парутине (and the silent
    Нова Каховка / Каховська ГЕС).

Why this module exists: every estuary post reports levels in centimetres above
a zero of post at −5.000 m БС-77 (Baltic 1977), while SWOT and ICESat-2 report
ellipsoidal / EGM2008 heights. The yearbooks also say, verbatim, which posts
stopped or degraded in 2023 and why, and they mark dam-breach-distorted daily
values with «/». All of that is provenance the manuscript needs and none of it
was machine-readable.

Three rules:

1. **Nothing is interpreted that the source does not state.** Value flags are
   kept verbatim; only the three the legend documents («?», «!», «/») get a
   meaning. Ice suffixes stay as `undocumented_flags`.
2. **Every row names its page.** A number in the manuscript must be traceable
   to a page of a specific volume.
3. **A shifted page fails loudly.** Each page is checked for the caption the
   page map expects; a re-issued PDF raises `PageMapError` rather than yielding
   a plausible wrong table.

`fitz` is never imported here — all PDF access goes through
`src.document.pdf_io` (see the repo invariant in CLAUDE.md).
"""
from __future__ import annotations

from src.paper_3.insitu.sources import ESTUARY_POSTS, PageMapError, VOLUMES

__all__ = ["ESTUARY_POSTS", "PageMapError", "VOLUMES"]
