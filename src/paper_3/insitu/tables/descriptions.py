"""«Опис постів та станцій» — prose descriptions, one block per post.

A block opens with a header line carrying the 5-digit station code, e.g.
«м. Херсон, р Дніпро, 80805, МГП-І.» or «МГП-І Станіслав, Дніпровський
лиман, 98032,». Everything up to the next header (of any station) is that
post's description. The block text is kept verbatim; a few fields are lifted
out for convenience and each is a substring of the text.
"""
from __future__ import annotations

import re

from src.paper_3.insitu.pages import PageCache
from src.paper_3.insitu.sources import detect_post, parse_zero, span
from src.paper_3.insitu.tables import Extracted

#: The station code is followed by a comma in a header («…, 80805, МГП-І.»);
#: postal codes in «Адреса:» lines are followed by a full stop («…, 57508.»).
_CODE = re.compile(r"\b(\d{5}),")
_TYPE = re.compile(r"\b(МГП|МГ|ГП)\s*-\s*([IІ1]{1,2})")
_ADDRESS = re.compile(r"Адреса:\s*([^\n]+(?:\n[^\n]+)?)")
_ZERO_LINE = re.compile(r"Відмітка\s+нуля\s+поста[^\n]*")
#: «о 06 і 18 годині (UTC)» / «Строки спостережень – 06 та 18 год. (UTC)».
_TIMES = re.compile(r"(?:о\s+)?(\d{2})\s+(?:і|та)\s+(\d{2})\s+год")


def _is_header(line: str) -> bool:
    if line.lstrip().startswith("Адреса"):
        return False
    return bool(_CODE.search(line))


def parse_volume(pc: PageCache, volume: str) -> tuple[list[dict], int, list[str]]:
    s = span(volume, "descriptions")
    lines: list[tuple[int, str]] = []
    for page in s.pages:
        for line in pc.text(volume, page).splitlines():
            lines.append((page, line.rstrip()))

    blocks: list[dict] = []
    current: dict | None = None
    for page, line in lines:
        if _is_header(line):
            if current:
                blocks.append(current)
            current = {"page": page, "header": line.strip(), "lines": [line]}
        elif current is not None:
            current["lines"].append(line)
    if current:
        blocks.append(current)

    out: list[dict] = []
    skipped = 0
    anomalies: list[str] = []
    for b in blocks:
        post = detect_post(b["header"])
        if post is None:
            skipped += 1
            continue
        text = "\n".join(b["lines"]).strip()
        code = _CODE.search(b["header"])
        ptype = _TYPE.search(b["header"])
        zl = _ZERO_LINE.search(text)
        zero_m, system, _ = parse_zero(zl.group(0)) if zl else (None, "", "")
        if zl is None:
            anomalies.append(f"{volume} p{b['page']} {post.name}: no «Відмітка нуля поста» line")
        addr = _ADDRESS.search(text)
        times = _TIMES.search(text)
        instruments = [k for k, pat in (("СРМ", r"\bСРМ\b"), ("СРВ", r"\bСРВ\b"),
                                        ("футшток", r"футшто"), ("рейка", r"рейк"),
                                        ("палі", r"\bпал[іяь]"))
                       if re.search(pat, text)]
        out.append({
            "post_name": post.name, "post_id": post.post_id,
            "code": code.group(1) if code else "",
            "post_type": (f"{ptype.group(1)}-{ptype.group(2).replace('1', 'І').replace('I', 'І')}"
                          if ptype else ""),
            "header_line": b["header"],
            "address": addr.group(1).replace("\n", " ").strip() if addr else "",
            "zero_of_post_line": zl.group(0).strip() if zl else "",
            "zero_of_post_m": zero_m, "height_system": system,
            "obs_times_utc": f"{times.group(1)},{times.group(2)}" if times else "",
            "level_instruments": ";".join(instruments),
            "n_chars": len(text), "text": text,
            "source_volume": volume, "page": b["page"],
        })
    return out, skipped, anomalies


def run(pc: PageCache) -> dict[str, Extracted]:
    ex = Extracted("station_descriptions")
    for volume in ("v230_mouths", "v229_seas"):
        rows, skipped, anomalies = parse_volume(pc, volume)
        ex.rows.extend(rows)
        ex.skipped_other_posts += skipped
        ex.anomalies.extend(anomalies)
        ex.pages.append(f"{volume}:p{span(volume, 'descriptions').first}-"
                        f"{span(volume, 'descriptions').last}")
    kh = [r for r in ex.rows if r["post_id"] == "kherson" and r["source_volume"] == "v230_mouths"]
    ex.check("manuscript_kherson_80805",
             bool(kh) and kh[0]["code"] == "80805" and kh[0]["zero_of_post_m"] == -5.0,
             (f"Херсон code {kh[0]['code']}, zero {kh[0]['zero_of_post_m']} m "
              f"{kh[0]['height_system']}" if kh else "Херсон description not found")
             + " — manuscript [V1.1, V1.3] state 80805 at −5.000 m BS-77")
    nk = [r for r in ex.rows if r["post_id"] == "nova_kakhovka"]
    ex.check("nova_kakhovka_is_tailwater_post_80802",
             bool(nk) and nk[0]["code"] == "80802",
             (f"yearbook Нова Каховка code {nk[0]['code']} (ГП-ІІ, 2.5 km below the dam)"
              if nk else "not found")
             + " — NOT the manuscript's reservoir station 80977 (+12.000 m)")
    return {ex.name: ex}
