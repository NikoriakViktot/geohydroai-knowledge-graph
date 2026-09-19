"""№230 Таблиця 2.1.1 — daily mean water levels, one estuary post per page.

Page anatomy (verified on pp 162–168): a ruled 2-row header («Число» + I…XII)
whose leaf cells give 13 column bounds; an unruled body of up to 31 day rows;
a ruled «Період» summary header (8 leaf columns, or 11 on the Олександрівка /
Миколаїв variant that splits the minimum into open-channel and winter); one
or two summary rows («2023», then the long-term period); then «Примітка:».
Captions above the header carry the post and «Відмітка нуля поста».
"""
from __future__ import annotations

import statistics

from src.paper_3.insitu.grid import (body_rows, column_bounds, first_y_of,
                                     text_of, words_in)
from src.paper_3.insitu.pages import PageCache
from src.paper_3.insitu.sources import PageMapError, detect_post, parse_zero, span
from src.paper_3.insitu.tables import Extracted
from src.paper_3.insitu.values import (DAYS_IN_MONTH_2023, YEAR_DEFAULT,
                                       parse_cell, to_height_m)

VOLUME = "v230_mouths"
TABLE = "2.1.1"

MONTHLY_ROWS = {"Середн": "mean", "Вищ": "max", "Нижч": "min"}

SUMMARY_8 = ("period", "mean", "max", "max_dates", "max_n",
             "min", "min_dates", "min_n")
SUMMARY_11 = ("period", "mean", "max", "max_dates", "max_n",
              "min", "min_dates", "min_n",
              "min_winter", "min_winter_dates", "min_winter_n")


def _header_and_summary(tables: list[dict], volume: str, page: int) -> tuple[dict, dict]:
    header = summary = None
    for t in tables:
        first = (t["rows"][0][0] or "").strip() if t["rows"] and t["rows"][0] else ""
        if first.startswith("Число") and header is None:
            header = t
        elif first.startswith("Період") and summary is None:
            summary = t
    if header is None or summary is None:
        raise PageMapError(f"{volume} p{page}: expected a «Число» header and a "
                           f"«Період» summary table for Таблиця {TABLE}; got "
                           f"{[(t['row_count'], t['col_count']) for t in tables]}")
    return header, summary


def parse_page(pc: PageCache, page: int, volume: str = VOLUME) -> dict:
    """One page → {"post", "zero_m", "zero_system", "zero_line", "days", "summary",
    "footnote", "anomalies", "caption"}."""
    tables = pc.tables(volume, page)
    words = pc.words(volume, page)
    header, summary = _header_and_summary(tables, volume, page)
    anomalies: list[str] = []

    caption = text_of(words_in(words, y_bottom=header["bbox"][1]))
    post = detect_post(caption)
    if post is None:
        raise PageMapError(f"{volume} p{page}: no estuary post named in the caption "
                           f"above Таблиця {TABLE}: {caption[:120]!r}")
    zero_m, zero_system, zero_line = parse_zero(caption)
    if zero_m is None:
        anomalies.append(f"p{page} {post.name}: zero of post not found in caption")

    # ── daily body ────────────────────────────────────────────────────────
    bounds = column_bounds(header)
    if len(bounds) != 13:
        raise PageMapError(f"{volume} p{page}: header has {len(bounds)} leaf "
                           f"columns, expected 13 (day + 12 months)")
    days: list[dict] = []
    monthly: list[dict] = []
    for row in body_rows(words, header["bbox"][3], summary["bbox"][1], bounds):
        cells = row["cells"]
        if row["orphans"]:
            anomalies.append(f"p{page} {post.name}: words between columns at "
                             f"y={row['y']:.0f}: {row['orphans']}")
        day_txt = cells[0].strip()
        kind = MONTHLY_ROWS.get(day_txt.rstrip("."))
        if kind is not None:
            # «Середн. / Вищ. / Нижч.» — the month's mean, highest and lowest.
            for month in range(1, 13):
                cell = parse_cell(cells[month])
                if cell.empty:
                    continue
                monthly.append({
                    "post_name": post.name, "post_id": post.post_id,
                    "period": f"{YEAR_DEFAULT}-{month:02d}", "kind": kind,
                    "level_cm": cell.value, "flags": cell.flags,
                    "flag_meanings": ";".join(cell.meanings),
                    "undocumented_flags": cell.undocumented_flags,
                    "not_observed": cell.not_observed,
                    "height_m": to_height_m(cell.value, zero_m),
                    "raw": cell.raw, "source_volume": volume, "page": page,
                    "table": TABLE,
                })
            continue
        if not day_txt.isdigit():
            if any(c.strip() for c in cells[1:]):
                anomalies.append(f"p{page} {post.name}: row without a day number "
                                 f"at y={row['y']:.0f}: {cells}")
            continue
        day = int(day_txt)
        for month in range(1, 13):
            cell = parse_cell(cells[month])
            if cell.empty:
                continue
            if day > DAYS_IN_MONTH_2023[month - 1]:
                anomalies.append(f"p{page} {post.name}: value {cell.raw!r} on "
                                 f"{YEAR_DEFAULT}-{month:02d}-{day:02d}, which does not exist")
                continue
            days.append({
                "post_name": post.name, "post_id": post.post_id,
                "date": f"{YEAR_DEFAULT}-{month:02d}-{day:02d}",
                "level_cm": cell.value,
                "flags": cell.flags, "flag_meanings": ";".join(cell.meanings),
                "undocumented_flags": cell.undocumented_flags,
                "not_observed": cell.not_observed,
                "height_m": to_height_m(cell.value, zero_m),
                "zero_of_post_m": zero_m, "height_system": zero_system,
                "raw": cell.raw, "source_volume": volume, "page": page,
                "table": TABLE,
            })

    # ── summary block ─────────────────────────────────────────────────────
    sbounds = column_bounds(summary)
    schema = {8: SUMMARY_8, 11: SUMMARY_11}.get(len(sbounds))
    if schema is None:
        raise PageMapError(f"{volume} p{page}: summary header has {len(sbounds)} "
                           f"leaf columns, expected 8 or 11")
    note_y = first_y_of(words, "Примітка", y_after=summary["bbox"][3])
    page_bottom = max(w[3] for w in words) + 1
    # A long date wraps onto two lines in the narrow 11-column layout
    # («08.04.193» / «2») while the row's other cells sit on the middle
    # baseline, so the fragments cluster as rows of their own above and
    # below. A row with no period label is folded into the nearest labelled
    # row by baseline, in baseline order.
    raw_rows = body_rows(words, summary["bbox"][3], note_y or page_bottom, sbounds)
    labelled = [r for r in raw_rows if r["cells"][0].strip()]
    fragments = [r for r in raw_rows if not r["cells"][0].strip()
                 and any(c.strip() for c in r["cells"])]
    if fragments and not labelled:
        anomalies.append(f"p{page} {post.name}: summary fragments with no labelled "
                         f"row to join: {[f['cells'] for f in fragments]}")
    parts: dict[int, list[dict]] = {i: [r] for i, r in enumerate(labelled)}
    for frag in fragments:
        if not labelled:
            break
        # A fragment fills a cell the labelled row left empty (its date wrapped);
        # a row whose cell is already complete is not a candidate even when
        # its baseline happens to be nearer.
        cols = [k for k, c in enumerate(frag["cells"]) if c.strip()]
        eligible = [k for k in range(len(labelled))
                    if all(not labelled[k]["cells"][c].strip() for c in cols)] \
            or list(range(len(labelled)))
        i = min(eligible, key=lambda k: abs(labelled[k]["y"] - frag["y"]))
        parts[i].append(frag)
    merged: list[list[str]] = []
    for i in range(len(labelled)):
        cells = [""] * len(sbounds)
        for r in sorted(parts[i], key=lambda r: r["y"]):
            for k, extra in enumerate(r["cells"]):
                if not extra:
                    continue
                joiner = "" if (cells[k][-1:].isdigit() and extra[:1].isdigit()) else " "
                cells[k] = (cells[k] + joiner + extra).strip() if cells[k] else extra
        merged.append(cells)
    summary_rows: list[dict] = []
    for cells in merged:
        rec = {"post_name": post.name, "post_id": post.post_id,
               "layout_variant": "open_winter" if len(sbounds) == 11 else "standard",
               "source_volume": volume, "page": page, "table": TABLE,
               "raw_row": " | ".join(cells)}
        for key, raw in zip(schema, cells):
            if key.endswith("_dates") or key == "period":
                rec[key] = raw
            else:
                c = parse_cell(raw)
                rec[f"{key}_cm" if key in ("mean", "max", "min", "min_winter") else key] = c.value
                if key in ("mean", "max", "min", "min_winter"):
                    rec[f"{key}_flags"] = c.flags + c.undocumented_flags
        if len(sbounds) == 8:
            rec.update({"min_winter_cm": None, "min_winter_flags": "",
                        "min_winter_dates": "", "min_winter_n": None})
        summary_rows.append(rec)

    footnote = text_of(words_in(words, y_top=note_y)) if note_y else ""

    return {"post": post, "zero_m": zero_m, "zero_system": zero_system,
            "zero_line": zero_line, "days": days, "monthly": monthly,
            "summary": summary_rows, "footnote": footnote,
            "anomalies": anomalies, "caption": caption}


def run(pc: PageCache) -> dict[str, Extracted]:
    s = span(VOLUME, "daily_levels")
    pc.check_span(s)
    daily = Extracted("daily_levels_2023")
    monthly = Extracted("level_monthly_2023")
    summary = Extracted("level_summary_2023")
    per_post: dict[str, dict] = {}

    for page in s.pages:
        parsed = parse_page(pc, page)
        post = parsed["post"]
        daily.pages.append(f"{VOLUME}:p{page}")
        daily.rows.extend(parsed["days"])
        monthly.rows.extend(parsed["monthly"])
        summary.rows.extend(parsed["summary"])
        daily.anomalies.extend(parsed["anomalies"])
        if parsed["footnote"]:
            daily.footnotes.append({"volume": VOLUME, "page": page,
                                    "post_name": post.name, "text": parsed["footnote"]})
        per_post[post.name] = parsed

    # ── checks ────────────────────────────────────────────────────────────
    for name, parsed in per_post.items():
        pid = parsed["post"].post_id
        # Monthly: mean of the parsed days vs the table's own «Середн.» row.
        bad: list[str] = []
        n_months = 0
        for month in range(1, 13):
            vals = [d["level_cm"] for d in parsed["days"]
                    if d["date"].startswith(f"{YEAR_DEFAULT}-{month:02d}")
                    and d["level_cm"] is not None]
            ref = next((r["level_cm"] for r in parsed["monthly"] if r["kind"] == "mean"
                        and r["period"].endswith(f"-{month:02d}")), None)
            if vals and ref is not None:
                n_months += 1
                if abs(statistics.fmean(vals) - ref) > 1.0:
                    bad.append(f"{month:02d}: {statistics.fmean(vals):.1f} vs {ref:.0f}")
        daily.check(f"monthly_consistency_{pid}", not bad and (n_months > 0 or not parsed["days"]),
                    f"{n_months} months compared; " + ("all within 1 cm of «Середн.»"
                                                        if not bad else "; ".join(bad)))
        # Annual extremes exclude the dam-breach period («*» — «розрахунок
        # проведено за 11 місяців»), so compare against the undistorted days.
        clean = [d["level_cm"] for d in parsed["days"]
                 if d["level_cm"] is not None and "/" not in d["flags"]]
        annual = next((r for r in parsed["summary"] if str(r.get("period", "")).strip()
                       == str(YEAR_DEFAULT)), None)
        if annual and clean and annual.get("max_cm") is not None:
            daily.check(f"extremes_{pid}",
                        max(clean) <= annual["max_cm"] + 0.5
                        and min(clean) >= annual["min_cm"] - 0.5,
                        f"undistorted daily range {min(clean):.0f}–{max(clean):.0f} cm within "
                        f"table {annual['min_cm']:.0f}–{annual['max_cm']:.0f} cm")

    kherson = [d for d in daily.rows if d["post_id"] == "kherson"]
    june = [d for d in kherson if d["date"].startswith(f"{YEAR_DEFAULT}-06")]
    may = [d for d in kherson if d["date"].startswith(f"{YEAR_DEFAULT}-05")]
    dam = [d for d in june if "/" in d["flags"]]
    daily.check("kherson_june_dam_breach_flagged", bool(dam),
                f"{len(dam)} of {len(june)} June values carry «/»")
    if june and may:
        jmax = max(d["level_cm"] for d in june if d["level_cm"] is not None)
        mmax = max(d["level_cm"] for d in may if d["level_cm"] is not None)
        daily.check("kherson_june_exceeds_may", jmax > mmax,
                    f"June max {jmax:.0f} cm vs May max {mmax:.0f} cm")
    nk = [d for d in daily.rows if d["post_id"] == "nova_kakhovka"]
    daily.check("nova_kakhovka_absent", not nk,
                f"{len(nk)} daily rows (yearbook: data absent — occupation, dam destroyed)")
    return {daily.name: daily, monthly.name: monthly, summary.name: summary}
