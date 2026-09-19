"""№229 Табл. 1.1.1 — daily sea level at the coastal stations (Очаків, Парутине).

The table is fully ruled, so `find_tables` returns the whole 38×13 grid:
header, 31 day rows, a blank spacer, then «сер. міс.», «макс.», «мінім.».
The caption states the basis («Щогодинні» vs «Строкові спостереження») and
whether a recorder exists; the page tail carries the annual figures.
"""
from __future__ import annotations

import re
import statistics

from src.paper_3.insitu.pages import PageCache
from src.paper_3.insitu.sources import PageMapError, detect_post, parse_zero, span
from src.paper_3.insitu.tables import Extracted
from src.paper_3.insitu.values import (DAYS_IN_MONTH_2023, YEAR_DEFAULT,
                                       parse_cell, to_height_m)

VOLUME = "v229_seas"
TABLE = "1.1.1"

_ANNUAL = re.compile(
    r"Середній\s+річний\s*\|?\s*(?P<mean>\S+).*?"
    r"Максимальний\s+за\s+рік\s*\|?\s*(?P<max>\S+).*?Дата\s*\|?\s*(?P<max_date>\S+).*?"
    r"Мінімальний\s+за\s+рік\s*\|?\s*(?P<min>\S+).*?Дата\s*\|?\s*(?P<min_date>\S+)",
    re.S)


def parse_page(pc: PageCache, page: int, volume: str = VOLUME) -> dict:
    text = pc.text(volume, page)
    flat = re.sub(r"\s+", " ", text)
    tables = pc.tables(volume, page)
    grid = next((t for t in tables if t["rows"] and
                 (t["rows"][0][0] or "").strip().startswith("міс/день")), None)
    if grid is None:
        raise PageMapError(f"{volume} p{page}: no «міс/день» table for Табл. {TABLE}")
    post = detect_post(flat[:400])
    if post is None:
        raise PageMapError(f"{volume} p{page}: no estuary post in caption: {flat[:120]!r}")
    zero_m, zero_system, zero_line = parse_zero(flat)
    basis = ("hourly" if "Щогодинні" in flat else
             "term" if "Строкові" in flat else "unstated")
    recorder = ("yes" if re.search(r"самописця\s+Є\b", flat) else
                "no" if re.search(r"самописця\s+НЕМА", flat) else "unstated")
    anomalies: list[str] = []
    days: list[dict] = []
    monthly: list[dict] = []

    for row in grid["rows"][1:]:
        label = (row[0] or "").strip()
        if label.isdigit():
            day = int(label)
            for month in range(1, 13):
                cell = parse_cell(row[month])
                if cell.empty:
                    continue
                if day > DAYS_IN_MONTH_2023[month - 1]:
                    anomalies.append(f"p{page} {post.name}: {cell.raw!r} on a "
                                     f"non-existent day {month:02d}-{day:02d}")
                    continue
                days.append({
                    "post_name": post.name, "post_id": post.post_id,
                    "date": f"{YEAR_DEFAULT}-{month:02d}-{day:02d}",
                    "level_cm": cell.value, "flags": cell.flags,
                    "flag_meanings": ";".join(cell.meanings),
                    "undocumented_flags": cell.undocumented_flags,
                    "not_observed": cell.not_observed,
                    "height_m": to_height_m(cell.value, zero_m),
                    "zero_of_post_m": zero_m, "height_system": zero_system,
                    "basis": basis, "recorder": recorder,
                    "raw": cell.raw, "source_volume": volume, "page": page,
                    "table": TABLE,
                })
        elif label:
            kind = {"сер. міс.": "mean", "макс.": "max", "мінім.": "min"}.get(label)
            if kind is None:
                anomalies.append(f"p{page} {post.name}: unexpected row label {label!r}")
                continue
            for month in range(1, 13):
                cell = parse_cell(row[month])
                if cell.empty:
                    continue
                monthly.append({"post_name": post.name, "post_id": post.post_id,
                                "period": f"{YEAR_DEFAULT}-{month:02d}", "kind": kind,
                                "level_cm": cell.value,
                                "flags": cell.flags + cell.undocumented_flags,
                                "raw": cell.raw, "source_volume": volume,
                                "page": page, "table": TABLE})

    m = _ANNUAL.search(flat)
    annual: list[dict] = []
    if m:
        for kind, key in (("mean", "mean"), ("max", "max"), ("min", "min")):
            c = parse_cell(m.group(key))
            annual.append({"post_name": post.name, "post_id": post.post_id,
                           "period": str(YEAR_DEFAULT), "kind": kind,
                           "level_cm": c.value, "flags": c.flags + c.undocumented_flags,
                           "date": m.group(f"{key}_date") if key != "mean" else "",
                           "raw": c.raw, "source_volume": volume, "page": page,
                           "table": TABLE})
    else:
        anomalies.append(f"p{page} {post.name}: annual summary line not found")

    return {"post": post, "zero_m": zero_m, "days": days, "monthly": monthly,
            "annual": annual, "anomalies": anomalies, "basis": basis}


def run(pc: PageCache) -> dict[str, Extracted]:
    s = span(VOLUME, "sea_levels")
    pc.check_span(s)
    daily = Extracted("sea_levels_2023")
    summary = Extracted("sea_level_summary_2023")
    for page in s.pages:
        parsed = parse_page(pc, page)
        daily.pages.append(f"{VOLUME}:p{page}")
        daily.rows.extend(parsed["days"])
        summary.rows.extend(parsed["monthly"])
        summary.rows.extend(parsed["annual"])
        daily.anomalies.extend(parsed["anomalies"])

        # Monthly mean of the parsed days vs the table's own «сер. міс.» row.
        post = parsed["post"]
        bad: list[str] = []
        for month in range(1, 13):
            vals = [d["level_cm"] for d in parsed["days"]
                    if d["date"].startswith(f"{YEAR_DEFAULT}-{month:02d}")
                    and d["level_cm"] is not None]
            ref = next((r["level_cm"] for r in parsed["monthly"]
                        if r["kind"] == "mean" and r["period"].endswith(f"-{month:02d}")), None)
            if vals and ref is not None and abs(statistics.fmean(vals) - ref) > 1.0:
                bad.append(f"{month:02d}: {statistics.fmean(vals):.1f} vs {ref:.0f}")
        daily.check(f"monthly_consistency_{post.post_id}", not bad,
                    "all 12 monthly means within 1 cm of «сер. міс.»" if not bad
                    else "; ".join(bad))
    return {daily.name: daily, summary.name: summary}
