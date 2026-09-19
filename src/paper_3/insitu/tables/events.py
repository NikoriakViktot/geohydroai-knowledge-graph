"""Hazardous-event tables — the dam-breach wave in the estuary's own words.

* №230 Додаток Таблиця 1.6.1 (pp 159–160): ruled 3-column prose table
  (post, date, description). Part 2 (p159) is June 2023: the breach wave.
* №230 Таблиця 2.1.3 (pp 172–173): 17-column ruled header, unruled body of
  post blocks; each block opens with the critical marks (higher / lower) and
  lists flood / low-water / surge / set-down / ice-jam events with dates,
  relative magnitudes and durations. Part 2 (p173) is the breach.
* №229 Таблиця 1.1.3 (p40): fully ruled surge/set-down table for the coastal
  stations.
"""
from __future__ import annotations

import re

from src.paper_3.insitu.grid import (body_rows, column_bounds, first_y_of,
                                     text_of, words_in)
from src.paper_3.insitu.pages import PageCache
from src.paper_3.insitu.sources import PageMapError, detect_post, span
from src.paper_3.insitu.tables import Extracted
from src.paper_3.insitu.values import parse_cell

_LEVEL = re.compile(r"(?:до|досяг(?:ла)?)\s+(?:небезпечної\s+)?(?:відмітки\s+)?(\d{3,4})\s*см")
_TEXTUAL = re.compile(r"[А-Яа-яЇїІіЄєҐґ]")
#: «10.05», «10.05.23», «01.05.2023» — a calendar date, never a level.
_DATE_TOKEN = re.compile(r"\d{1,2}\.\d{2}(?:\.\d{2,4})?[?.,;]?")
_EXCEED = re.compile(r"на\s+(\d{1,4})\s*см\s+(вище|нижче)")
_DURATION = re.compile(r"(?:склала|тривалість[^.]*?)\s+(\d{1,4})\s*год")
_UNKNOWN = re.compile(r"невідом")

THRESHOLD_COLUMNS = (
    "critical_high_cm", "critical_low_cm",
    "flood_max_cm", "flood_date",
    "lowwater_min_cm", "lowwater_date",
    "surge_max_cm", "surge_date", "surge_rel_cm", "surge_duration_h",
    "setdown_min_cm", "setdown_date", "setdown_rel_cm", "setdown_duration_h",
    "icejam_max_cm", "icejam_date", "icejam_duration_h",
)


# ── Додаток 1.6.1 ─────────────────────────────────────────────────────────────

def parse_events_page(pc: PageCache, page: int, volume: str = "v230_mouths"
                      ) -> tuple[list[dict], list[dict], list[str]]:
    tables = pc.tables(volume, page)
    words = pc.words(volume, page)
    part = re.search(r"1\.6\.1\s*\(ч\.\s*(\d)\)", pc.text(volume, page))
    out: list[dict] = []
    anomalies: list[str] = []
    last_bottom = 0.0
    for t in tables:
        if not t["rows"] or not (t["rows"][0][0] or "").startswith("Водоток"):
            continue
        label = ""
        for row in t["rows"][1:]:
            if row[0]:
                label = row[0].replace("\n", " ").strip()
            date_text = (row[1] or "").replace("\n", " ").strip()
            desc = (row[2] or "").replace("\n", " ").strip()
            post = detect_post(label)
            if post is None:
                anomalies.append(f"p{page}: event row with unknown post {label!r}")
                continue
            lvl = _LEVEL.search(desc)
            exc = _EXCEED.search(desc)
            dur = _DURATION.search(desc)
            out.append({
                "post_name": post.name, "post_id": post.post_id,
                "post_label": label, "date_text": date_text,
                "max_level_cm": float(lvl.group(1)) if lvl else None,
                "exceedance_cm": (float(exc.group(1)) * (1 if exc.group(2) == "вище" else -1)
                                  if exc else None),
                "duration_h": float(dur.group(1)) if dur else None,
                "magnitude_unknown": bool(_UNKNOWN.search(desc)),
                "description": desc,
                "part": part.group(1) if part else "",
                "source_volume": volume, "page": page, "table": "Додаток 1.6.1",
            })
        last_bottom = max(last_bottom, t["bbox"][3])
    footnotes = []
    below = words_in(words, y_top=last_bottom)
    if below:
        footnotes.append({"volume": volume, "page": page, "post_name": "",
                          "text": text_of(below)})
    return out, footnotes, anomalies


# ── Таблиця 2.1.3 ─────────────────────────────────────────────────────────────

def parse_thresholds_page(pc: PageCache, page: int, volume: str = "v230_mouths"
                          ) -> tuple[list[dict], list[dict], list[str]]:
    tables = pc.tables(volume, page)
    words = pc.words(volume, page)
    if not tables:
        raise PageMapError(f"{volume} p{page}: no ruled header for Таблиця 2.1.3")
    header = tables[0]
    bounds = column_bounds(header)
    if len(bounds) != 17:
        raise PageMapError(f"{volume} p{page}: 2.1.3 header has {len(bounds)} "
                           f"columns, expected 17")
    part = re.search(r"2\.1\.3\s*ч\.\s*(\d)", pc.text(volume, page))
    page_bottom = max(w[3] for w in words) + 1
    note_y = first_y_of(words, "Примітка", y_after=header["bbox"][3])
    body_limit = note_y or page_bottom
    zone = words_in(words, y_top=header["bbox"][3], y_bottom=body_limit)

    # A row may mix numbers (event cells) with prose (a per-post remark such as
    # «Небезпечних гідрологічних явищ не спостерігалось»). Prose words never
    # belong in a cell, so split each row before assigning columns.
    from src.paper_3.insitu.grid import assign_columns, cluster_rows
    labels: list[tuple[float, object]] = []
    notes: list[tuple[float, str]] = []
    data: list[dict] = []
    for row in cluster_rows(zone, tol=3.0):
        numbers = [w for w in row if any(ch.isdigit() for ch in w[4])]
        prose = [w for w in row if not any(ch.isdigit() for ch in w[4])
                 and _TEXTUAL.search(w[4])]
        line = " ".join(w[4] for w in row)
        post = detect_post(line)
        dates_only = bool(numbers) and all(_DATE_TOKEN.fullmatch(w[4]) for w in numbers)
        if post is not None:
            # The block label («р. Дніпро - м. Херсон»); it may share a
            # baseline with a data row, so numbers on the line still count.
            labels.append((row[0][3], post))
        elif len(prose) >= 3:
            # A sentence («До 10.05.23 небезпечних гідрологічних явищ не
            # спостерігалось») — its dates belong to the sentence, not to a
            # cell, even when the critical marks share the baseline.
            date_words = [w for w in numbers if _DATE_TOKEN.fullmatch(w[4])]
            numbers = [w for w in numbers if w not in date_words]
            notes.append((row[0][3], " ".join(
                w[4] for w in sorted(prose + date_words, key=lambda w: w[0]))))
            if not numbers:
                continue
        elif dates_only:
            # A date on a baseline of its own belongs to a wrapped sentence.
            notes.append((row[0][3], line))
            continue
        elif prose:
            notes.append((row[0][3], " ".join(w[4] for w in prose)))
        if numbers:
            cells, orphans = assign_columns(numbers, bounds)
            data.append({"y": row[0][3], "cells": cells, "orphans": orphans})

    # Blocks start at a row that carries the critical marks (columns 0 and 1).
    blocks: list[list[dict]] = []
    for row in data:
        starts = parse_cell(row["cells"][0]).value is not None and \
            parse_cell(row["cells"][1]).value is not None
        if starts or not blocks:
            blocks.append([row])
        else:
            blocks[-1].append(row)

    out: list[dict] = []
    anomalies: list[str] = []
    for bi, block in enumerate(blocks):
        y0 = block[0]["y"]
        y1 = blocks[bi + 1][0]["y"] if bi + 1 < len(blocks) else page_bottom
        inside = [p for y, p in labels if y0 - 14 <= y < y1]
        if not inside:
            nearest = min(labels, key=lambda lp: abs(lp[0] - y0), default=None)
            inside = [nearest[1]] if nearest else []
            anomalies.append(f"p{page}: block at y={y0:.0f} had no label inside; "
                             f"nearest used: {inside[0].name if inside else 'none'}")
        post = inside[0]
        crit_hi = parse_cell(block[0]["cells"][0]).value
        crit_lo = parse_cell(block[0]["cells"][1]).value
        block_notes = [text for y, text in notes if y0 - 4 <= y < y1]
        for row in block:
            cells = row["cells"]
            rec = {"post_name": post.name, "post_id": post.post_id,
                   "critical_high_cm": crit_hi, "critical_low_cm": crit_lo,
                   "part": part.group(1) if part else ""}
            populated = 0
            for key, raw in zip(THRESHOLD_COLUMNS[2:], cells[2:]):
                if key.endswith("_date"):
                    rec[key] = raw
                    populated += bool(raw)
                else:
                    c = parse_cell(raw)
                    rec[key] = c.value
                    rec[f"{key}_flags"] = c.flags + c.undocumented_flags if c.raw else ""
                    populated += c.value is not None
            if populated == 0 and row is block[0]:
                continue        # a block whose only row is the critical marks
            rec.update({"note": "", "raw_row": " | ".join(cells),
                        "source_volume": volume, "page": page, "table": "2.1.3"})
            out.append(rec)
        if block_notes:
            # Consecutive prose lines are one remark wrapped over several rows.
            out.append({"post_name": post.name, "post_id": post.post_id,
                        "critical_high_cm": crit_hi, "critical_low_cm": crit_lo,
                        "part": part.group(1) if part else "",
                        **{k: None for k in THRESHOLD_COLUMNS[2:]},
                        "note": " ".join(block_notes), "raw_row": " ".join(block_notes),
                        "source_volume": volume, "page": page, "table": "2.1.3"})
    footnotes = []
    if note_y:
        footnotes.append({"volume": volume, "page": page, "post_name": "",
                          "text": text_of(words_in(words, y_top=note_y))})
    return out, footnotes, anomalies


# ── №229 1.1.3 ────────────────────────────────────────────────────────────────

def parse_surges_page(pc: PageCache, page: int, volume: str = "v229_seas"
                      ) -> tuple[list[dict], int, list[str]]:
    tables = pc.tables(volume, page)
    out: list[dict] = []
    skipped = 0
    anomalies: list[str] = []
    grid = next((t for t in tables if t["rows"] and (t["rows"][0][0] or "").strip() == "№"),
                None)
    if grid is None:
        raise PageMapError(f"{volume} p{page}: no «№» table for Таблиця 1.1.3")
    for row in grid["rows"][2:]:
        name = (row[1] or "").replace("\n", " ").strip()
        post = detect_post(name)
        if post is None:
            skipped += 1
            continue
        crit = re.match(r"(\d{3})\s*-\s*(\d{3})", (row[2] or "").strip())
        out.append({
            "post_name": post.name, "post_id": post.post_id,
            "station_label": name,
            "critical_low_cm": float(crit.group(1)) if crit else None,
            "critical_high_cm": float(crit.group(2)) if crit else None,
            "surge_date": (row[3] or "").strip(),
            "surge_exceedance_cm": parse_cell(row[4]).value,
            "surge_duration_h": (row[5] or "").strip(),
            "setdown_date": (row[6] or "").strip(),
            "setdown_drop_cm": parse_cell(row[7]).value,
            "setdown_duration_h": (row[8] or "").strip(),
            "note": (row[9] or "").strip(),
            "raw_row": " | ".join((c or "").replace("\n", " ") for c in row),
            "source_volume": volume, "page": page, "table": "1.1.3",
        })
    return out, skipped, anomalies


def run(pc: PageCache) -> dict[str, Extracted]:
    ev = Extracted("hazard_events_2023")
    s = span("v230_mouths", "hazard_events")
    pc.check_span(s)
    for page in s.pages:
        rows, notes, anomalies = parse_events_page(pc, page)
        ev.pages.append(f"v230_mouths:p{page}")
        ev.rows.extend(rows)
        ev.footnotes.extend(notes)
        ev.anomalies.extend(anomalies)
    june = [r for r in ev.rows if "червня" in r["date_text"]]
    ev.check("june_breach_events", len(june) >= 5,
             f"{len(june)} June-2023 event rows across "
             f"{len({r['post_name'] for r in june})} posts")

    th = Extracted("hazard_thresholds_2023")
    s2 = span("v230_mouths", "hazard_thresholds")
    pc.check_span(s2)
    for page in s2.pages:
        rows, notes, anomalies = parse_thresholds_page(pc, page)
        th.pages.append(f"v230_mouths:p{page}")
        th.rows.extend(rows)
        th.footnotes.extend(notes)
        th.anomalies.extend(anomalies)
    th.check("threshold_posts", len({r["post_name"] for r in th.rows}) >= 5,
             f"posts: {sorted({r['post_name'] for r in th.rows})}")

    su = Extracted("surges_2023")
    s3 = span("v229_seas", "surges")
    pc.check_span(s3)
    for page in s3.pages:
        rows, skipped, anomalies = parse_surges_page(pc, page)
        su.pages.append(f"v229_seas:p{page}")
        su.rows.extend(rows)
        su.skipped_other_posts += skipped
        su.anomalies.extend(anomalies)
    su.check("surge_posts", {"Очаків", "Парутине"} <= {r["post_name"] for r in su.rows},
             f"posts: {sorted({r['post_name'] for r in su.rows})}")
    return {ev.name: ev, th.name: th, su.name: su}
