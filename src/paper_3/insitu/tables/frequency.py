"""Level frequency / exceedance tables.

№230 Таблиця 2.1.2 (pp 169–171): a 16-column ruled header at the top of the
page (interval, I…XII, n, %, exceedance %), then several post blocks per page,
each opened by a caption line naming the post («р. Дніпро - м. Херсон*!») and
followed by unruled rows like «569-550 | … | 54 | 0.69 | 0.69».

№229 Таблиця 1.1.2 (pp 36–39): per station a ruled gradation table (4 cols)
and a ruled statistics table (9 cols), with the station named above them.
"""
from __future__ import annotations

import re

from src.paper_3.insitu.grid import (body_rows, cluster_rows, column_bounds,
                                     first_y_of, text_of, words_in)
from src.paper_3.insitu.pages import PageCache
from src.paper_3.insitu.sources import PageMapError, detect_post, span
from src.paper_3.insitu.tables import Extracted
from src.paper_3.insitu.values import YEAR_DEFAULT, parse_cell

_INTERVAL = re.compile(r"^(?:більше|более)?\s*(\d{3})(?:\s*-\s*(\d{3}))?$")
MONTH_KEYS = tuple(f"m{m:02d}" for m in range(1, 13))


def _interval(raw: str) -> tuple[float | None, float | None]:
    m = _INTERVAL.match(raw.strip())
    if not m:
        return None, None
    a = float(m.group(1))
    b = float(m.group(2)) if m.group(2) else None
    if b is None:
        return a, None          # «більше 570» → open-ended upwards
    return min(a, b), max(a, b)


# ── №230 2.1.2 ────────────────────────────────────────────────────────────────

def parse_frequency_page(pc: PageCache, page: int, volume: str = "v230_mouths"
                         ) -> tuple[list[dict], list[dict], list[str]]:
    tables = pc.tables(volume, page)
    words = pc.words(volume, page)
    if not tables:
        raise PageMapError(f"{volume} p{page}: no ruled header for Таблиця 2.1.2")
    header = tables[0]
    bounds = column_bounds(header)
    if len(bounds) != 16:
        raise PageMapError(f"{volume} p{page}: 2.1.2 header has {len(bounds)} "
                           f"columns, expected 16")
    note_y = first_y_of(words, "Примітка", y_after=header["bbox"][3])
    bottom = note_y or (max(w[3] for w in words) + 1)
    zone = words_in(words, y_top=header["bbox"][3], y_bottom=bottom)

    rows_out: list[dict] = []
    anomalies: list[str] = []
    current = None
    caption_flags = ""
    for row in cluster_rows(zone):
        line = " ".join(w[4] for w in row)
        post = detect_post(line)
        if post is not None and not _INTERVAL.match(row[0][4].strip()):
            current = post
            caption_flags = "".join(ch for ch in line if ch in "*!?")
            continue
        cells, orphans = _cells(row, bounds)
        lo, hi = _interval(cells[0])
        if lo is None:
            if any(c.strip() for c in cells):
                anomalies.append(f"p{page}: unplaced row {line!r}")
            continue
        if current is None:
            anomalies.append(f"p{page}: data row before any post caption: {line!r}")
            continue
        if orphans:
            anomalies.append(f"p{page} {current.name}: orphan words {orphans} in {line!r}")
        rec = {"post_name": current.name, "post_id": current.post_id,
               "interval_lo_cm": lo, "interval_hi_cm": hi,
               "caption_flags": caption_flags}
        for key, raw in zip(MONTH_KEYS, cells[1:13]):
            rec[key] = parse_cell(raw).value
        rec["n_total"] = parse_cell(cells[13]).value
        rec["pct"] = parse_cell(cells[14]).value
        rec["exceedance_pct"] = parse_cell(cells[15]).value
        rec.update({"raw_row": " | ".join(cells), "source_volume": volume,
                    "page": page, "table": "2.1.2"})
        rows_out.append(rec)

    footnotes = []
    if note_y:
        footnotes.append({"volume": volume, "page": page, "post_name": "",
                          "text": text_of(words_in(words, y_top=note_y))})
    return rows_out, footnotes, anomalies


def _cells(row, bounds):
    from src.paper_3.insitu.grid import assign_columns
    return assign_columns(row, bounds)


# ── №229 1.1.2 ────────────────────────────────────────────────────────────────

def parse_sea_stats_page(pc: PageCache, page: int, volume: str = "v229_seas"
                         ) -> tuple[list[dict], int, list[str]]:
    tables = sorted(pc.tables(volume, page), key=lambda t: t["bbox"][1])
    words = pc.words(volume, page)
    out: list[dict] = []
    skipped = 0
    anomalies: list[str] = []
    prev_bottom = 0.0
    i = 0
    while i < len(tables):
        t = tables[i]
        first = (t["rows"][0][0] or "").strip()
        if not first.startswith("Градації"):
            prev_bottom = t["bbox"][3]
            i += 1
            continue
        caption = text_of(words_in(words, y_top=prev_bottom, y_bottom=t["bbox"][1]))
        post = detect_post(caption)
        n_cases = re.search(r"Кількість\s+випадків\s+(\d+)", re.sub(r"\s+", " ", caption))
        basis = "hourly" if "Щогодинні" in caption else ("term" if "Строкові" in caption else "")
        stats = tables[i + 1] if i + 1 < len(tables) and \
            (tables[i + 1]["rows"][0][0] or "").strip().startswith("Середня") else None
        if post is None:
            skipped += 1
        else:
            for row in t["rows"][2:]:
                lo, hi = _interval(row[0] or "")
                if lo is None:
                    continue
                out.append({"post_name": post.name, "post_id": post.post_id,
                            "kind": "gradation", "interval_lo_cm": lo,
                            "interval_hi_cm": hi,
                            "n": parse_cell(row[1]).value,
                            "pct": parse_cell(row[2]).value,
                            "exceedance_pct": parse_cell(row[3]).value,
                            "n_cases_total": int(n_cases.group(1)) if n_cases else None,
                            "basis": basis, "raw_row": " | ".join(c or "" for c in row),
                            "source_volume": volume, "page": page, "table": "1.1.2"})
            if stats is not None and len(stats["rows"]) >= 2:
                keys = ("mean_year_cm", "mean_se", "range_cm", "sd_cm", "sd_se",
                        "skewness", "skew_se", "kurtosis", "kurt_se")
                rec = {"post_name": post.name, "post_id": post.post_id,
                       "kind": "statistics", "n_cases_total":
                       int(n_cases.group(1)) if n_cases else None, "basis": basis}
                for key, raw in zip(keys, stats["rows"][1]):
                    rec[key] = parse_cell(raw).value
                rec.update({"raw_row": " | ".join(c or "" for c in stats["rows"][1]),
                            "source_volume": volume, "page": page, "table": "1.1.2"})
                out.append(rec)
            else:
                anomalies.append(f"p{page} {post.name}: statistics table missing")
        prev_bottom = (stats or t)["bbox"][3]
        i += 2 if stats is not None else 1
    return out, skipped, anomalies


def run(pc: PageCache) -> dict[str, Extracted]:
    freq = Extracted("level_frequency_2023")
    s = span("v230_mouths", "level_frequency")
    pc.check_span(s)
    for page in s.pages:
        rows, notes, anomalies = parse_frequency_page(pc, page)
        freq.pages.append(f"v230_mouths:p{page}")
        freq.rows.extend(rows)
        freq.footnotes.extend(notes)
        freq.anomalies.extend(anomalies)
    posts = sorted({r["post_name"] for r in freq.rows})
    # Касперівка and Станіслав have no 2.1.2 block in 2023 (partial records;
    # the page's «Примітка» says so), so five posts is the full set.
    freq.check("frequency_posts", len(posts) >= 5,
               f"{len(posts)} posts with frequency rows: {', '.join(posts)}")
    for post in posts:
        tot = [r for r in freq.rows if r["post_name"] == post and r["pct"] is not None]
        pct = sum(r["pct"] for r in tot)
        freq.check(f"frequency_pct_sums_{post}", abs(pct - 100) <= 0.5,
                   f"percentages sum to {pct:.2f}")

    stats = Extracted("sea_level_stats_2023")
    s2 = span("v229_seas", "sea_level_stats")
    pc.check_span(s2)
    for page in s2.pages:
        rows, skipped, anomalies = parse_sea_stats_page(pc, page)
        stats.pages.append(f"v229_seas:p{page}")
        stats.rows.extend(rows)
        stats.skipped_other_posts += skipped
        stats.anomalies.extend(anomalies)
    stats.check("sea_stats_posts",
                {"Очаків", "Парутине"} <= {r["post_name"] for r in stats.rows},
                f"posts: {sorted({r['post_name'] for r in stats.rows})}")
    return {freq.name: freq, stats.name: stats}
