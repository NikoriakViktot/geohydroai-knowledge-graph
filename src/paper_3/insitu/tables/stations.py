"""Station registries and the unified-sea-zero corrections.

* №230 p146 «СПИСОК ПОСТІВ І СТВОРІВ…» — a ruled 11-column table (two header
  rows). Water body is printed once per group and carried forward.
* №229 p10 «Перелік морських гідрометеорологічних берегових станцій і постів»
  — ragged; rebuilt from words anchored on the «№» column. Column 5 is
  «Поправка для приведення до єдиного нуля моря, см».
"""
from __future__ import annotations

import re

from src.paper_3.insitu.grid import words_in
from src.paper_3.insitu.pages import PageCache
from src.paper_3.insitu.sources import PageMapError, detect_post, parse_zero, span
from src.paper_3.insitu.tables import Extracted

_TYPE = re.compile(r"\b(МГП|МГ|ГП)\s*-\s*([IІ1]{1,2})\b")
_DATE = re.compile(r"\d{2}\.\d{2}\.\d{4}")


def _post_type(text: str) -> str:
    m = _TYPE.search(text or "")
    if not m:
        return ""
    return f"{m.group(1)}-{m.group(2).replace('1', 'І').replace('I', 'І')}"


def _num(text: str) -> float | None:
    t = (text or "").strip().replace(",", ".")
    try:
        return float(t)
    except ValueError:
        return None


def parse_v230(pc: PageCache, page: int = 146, volume: str = "v230_mouths"
               ) -> tuple[list[dict], list[str]]:
    tables = pc.tables(volume, page)
    grid = next((t for t in tables if t["rows"] and
                 (t["rows"][0][0] or "").startswith("№")), None)
    if grid is None or grid["col_count"] != 11:
        raise PageMapError(f"{volume} p{page}: expected the 11-column station list")
    out: list[dict] = []
    anomalies: list[str] = []
    water = ""
    for row in grid["rows"][2:]:
        cells = [(c or "").replace("\n", " ").strip() for c in row]
        if not cells[0].strip().isdigit():
            continue
        if cells[1]:
            water = re.sub(r"(?<=\w)- (?=\w)", "-", cells[1])   # «Дніпро- Бузький» line-break
        name_cell = cells[2]
        post = detect_post(name_cell)
        if post is None:
            anomalies.append(f"p{page}: station row without a known post: {name_cell!r}")
            continue
        zero_m, system, zero_line = parse_zero(cells[5])
        dates = _DATE.findall(cells[6])
        out.append({
            "post_no": int(cells[0]), "post_name": post.name, "post_id": post.post_id,
            "water_body": water, "station_cell": name_cell,
            "post_type": _post_type(name_cell),
            "dist_from_apex_km": _num(cells[3]), "dist_from_sea_edge_km": _num(cells[4]),
            "zero_of_post_m": zero_m, "height_system": system,
            "zero_verbatim": cells[5],
            "opened": dates[0] if dates else cells[6],
            "opened_all_dates": "; ".join(dates),
            "status_2023": cells[7], "agency": cells[8],
            "tables_listed": cells[9], "storage": cells[10],
            "source_volume": volume, "page": page,
        })
    return out, anomalies


def parse_v229(pc: PageCache, page: int = 10, volume: str = "v229_seas"
               ) -> tuple[list[dict], list[dict], int, list[str]]:
    """(stations, corrections, skipped_other, anomalies) from the words of p10."""
    words = pc.words(volume, page)
    header_bottom = max((w[3] for w in words if w[4] in ("1", "2", "3", "4", "5", "6", "7")
                         and w[3] < 215), default=0)
    body = words_in(words, y_top=header_bottom)
    anchors = sorted((w for w in body if w[0] < 70 and w[4].isdigit()), key=lambda w: w[3])
    stations: list[dict] = []
    corrections: list[dict] = []
    skipped = 0
    anomalies: list[str] = []
    for a in anchors:
        y = a[3]
        near = [w for w in body if abs(w[3] - y) <= 12]
        name = " ".join(w[4] for w in sorted((w for w in near if 75 <= w[0] < 158),
                                             key=lambda w: (w[3], w[0])))
        ptype = " ".join(w[4] for w in near if 158 <= w[0] < 205)
        zero = " ".join(w[4] for w in near if 205 <= w[0] < 250)
        corr = " ".join(w[4] for w in near if 280 <= w[0] < 335)
        obs = "".join(w[4] for w in sorted((w for w in near if 340 <= w[0] < 445),
                                           key=lambda w: w[0]))
        post = detect_post(name)
        if post is None:
            skipped += 1
            continue
        zero_m, system, _ = parse_zero(zero + " м БС")
        rec = {"post_no": int(a[4]), "post_name": post.name, "post_id": post.post_id,
               "water_body": post.water_body, "station_cell": name,
               "post_type": _post_type(ptype) or ptype,
               "dist_from_apex_km": None, "dist_from_sea_edge_km": None,
               "zero_of_post_m": zero_m, "height_system": "БС",
               "zero_verbatim": zero, "opened": "", "opened_all_dates": "",
               "status_2023": "", "agency": "ГМЦ ЧАМ", "tables_listed": "",
               "storage": "", "observations": obs,
               "source_volume": volume, "page": page}
        stations.append(rec)
        c = _num(corr)
        if c is None:
            anomalies.append(f"p{page} {post.name}: unified-zero correction unreadable {corr!r}")
        corrections.append({"post_name": post.name, "post_id": post.post_id,
                            "zero_of_post_m": zero_m,
                            "unified_sea_zero_correction_cm": c,
                            "verbatim": f"{name} | {ptype} | {zero} | {corr}",
                            "source_volume": volume, "page": page})
    return stations, corrections, skipped, anomalies


def run(pc: PageCache) -> dict[str, Extracted]:
    st = Extracted("stations")
    s = span("v230_mouths", "stations")
    pc.check_span(s)
    rows, anomalies = parse_v230(pc, s.first)
    st.pages.append(f"v230_mouths:p{s.first}")
    st.rows.extend(rows)
    st.anomalies.extend(anomalies)

    corr = Extracted("datum_corrections")
    s2 = span("v229_seas", "stations")
    pc.check_span(s2)
    rows2, corrs, skipped, anomalies2 = parse_v229(pc, s2.first)
    st.pages.append(f"v229_seas:p{s2.first}")
    st.rows.extend(rows2)
    st.skipped_other_posts += skipped
    st.anomalies.extend(anomalies2)
    corr.rows.extend(corrs)
    corr.skipped_other_posts = skipped
    corr.pages.append(f"v229_seas:p{s2.first}")

    v230 = [r for r in st.rows if r["source_volume"] == "v230_mouths"]
    st.check("v230_station_count", len(v230) == 10,
             f"{len(v230)} estuary posts listed on p146 (expected 10)")
    zeros = {r["post_name"]: r["zero_of_post_m"] for r in v230}
    st.check("zero_of_post_minus_5", all(z == -5.0 for n, z in zeros.items()
                                         if n != "Олександрівка"),
             f"zeros: {zeros}")
    st.check("oleksandrivka_zero", zeros.get("Олександрівка") == -3.02,
             f"Олександрівка zero {zeros.get('Олександрівка')}")
    got = {r["post_name"]: r["unified_sea_zero_correction_cm"] for r in corr.rows}
    corr.check("unified_zero_corrections",
               got.get("Очаків") == 321 and got.get("Парутине") == 440,
               f"{got}")
    return {st.name: st, corr.name: corr}
