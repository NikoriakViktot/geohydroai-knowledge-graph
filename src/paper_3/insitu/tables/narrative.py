"""«Огляд гідрометеорологічних умов…» — the yearbook's own prose on 2023,
including the subsection «Вплив підриву Каховської ГЕС на рівні води».

Two outputs:

* `overview_2023.csv` — every paragraph verbatim, with page, subsection and the
  posts it names. Nothing is summarised.
* `narrative_facts_2023.csv` — one row per sentence that states a number with a
  unit (cm, m БС, km³, %, hh:mm). The number is lifted by regex; the sentence
  travels with it, so the lift can be disputed against the source.

The «X см (Y м БС)» pairs in the breach subsection are an independent statement
of the datum: Y must equal zero of post + X/100. That is checked.
"""
from __future__ import annotations

import re

from src.paper_3.insitu.pages import PageCache
from src.paper_3.insitu.sources import detect_posts, span
from src.paper_3.insitu.tables import Extracted
from src.paper_3.insitu.values import YEAR_DEFAULT

VOLUME = "v230_mouths"
BREACH_HEADING = "ВПЛИВ ПІДРИВУ"

_LEVEL_BS = re.compile(r"(\d{3,4})\s*см\s*\((\d{1,2},\d{1,2})\s*м\s*БС\)")
_LEVEL = re.compile(r"(?<![\d,])(\d{3,4})\s*см")
_M_BS = re.compile(r"(\d{1,2},\d{1,2})\s*м\s*БС")
_M_DELTA = re.compile(r"на\s+(\d{1,2},\d{1,2})\s*м")
_KM3 = re.compile(r"(\d{1,2},\d)\s*км3")
_PCT = re.compile(r"(\d{1,3})\s*%")
_TIME = re.compile(r"\b(\d{2}:\d{2})\b")
_DAY = re.compile(r"\b(\d{1,2})\s+(червня|липня|травня)\b")
_MONTH = {"травня": 5, "червня": 6, "липня": 7}
_SENT = re.compile(r"(?<=[а-яїієґ\)\»])\.\s+(?=[А-ЯЇІЄҐ«\d])")

#: Prose declines post names («у Херсоні», «в Парутиному», «в Очакові»,
#: «Касперівці»), so the exact-alias matcher of `sources` misses them here.
#: Stems, matched at a word start, only for the narrative.
_STEMS = {
    "Херсон": ("Херсон",), "Касперівка": ("Касперів",), "Олександрівка": ("Олександрів",),
    "Миколаїв": ("Миколаїв", "Миколаєв"), "Парутине": ("Парутин",), "Станіслав": ("Станіслав",),
    "Геройське": ("Геройськ",), "Очаків": ("Очак",), "Нова Каховка": ("Нов[аійу] Каховк",),
}
_STEM_RE = {name: re.compile(r"\b(?:" + "|".join(stems) + r")")
            for name, stems in _STEMS.items()}
FIG5 = "Рис. 5"


def posts_in(text: str) -> list[str]:
    """Estuary posts named in `text`, declensions included, in order of appearance."""
    hits = [(m.start(), name) for name, pat in _STEM_RE.items()
            for m in [pat.search(text)] if m]
    return [name for _, name in sorted(hits)]


def paragraphs(text: str) -> list[str]:
    """Reflow PDF lines into paragraphs. A paragraph starts after a blank line
    or at a line indented by two or more spaces; a bare page number is dropped."""
    out: list[str] = []
    cur: list[str] = []
    for line in text.splitlines():
        raw = line.rstrip()
        if not raw.strip() or raw.strip().isdigit():
            if cur:
                out.append(" ".join(cur))
                cur = []
            continue
        if re.match(r"^\s{2,}\S", raw) and cur:
            out.append(" ".join(cur))
            cur = []
        cur.append(raw.strip())
    if cur:
        out.append(" ".join(cur))
    return [re.sub(r"\s+", " ", p).strip() for p in out if p.strip()]


def sentences(paragraph: str) -> list[str]:
    return [s.strip() for s in _SENT.split(paragraph) if s.strip()]


def facts_from(sentence: str, page: int, subsection: str,
               inherited_posts: list[str] | None = None) -> list[dict]:
    """Numeric statements in one sentence, each with the sentence attached.

    A sentence that names no post inherits the posts named earlier in its
    paragraph («У Херсоні … . Максимального значення рівень води досяг …»)."""
    posts = posts_in(sentence) or list(inherited_posts or [])
    post_names = ";".join(posts)
    day = _DAY.search(sentence)
    date = (f"{YEAR_DEFAULT}-{_MONTH[day.group(2)]:02d}-{int(day.group(1)):02d}"
            if day else "")
    time = _TIME.search(sentence)
    base = {"post_names": post_names, "date": date,
            "time": time.group(1) if time else "", "sentence": sentence,
            "subsection": subsection, "source_volume": VOLUME, "page": page}
    out: list[dict] = []
    taken: set[int] = set()
    for m in _LEVEL_BS.finditer(sentence):
        out.append({**base, "kind": "level_with_height", "value": float(m.group(1)),
                    "unit": "cm", "height_bs_m": float(m.group(2).replace(",", "."))})
        taken.add(m.start(1))
    for m in _LEVEL.finditer(sentence):
        if m.start(1) in taken:
            continue
        out.append({**base, "kind": "level", "value": float(m.group(1)),
                    "unit": "cm", "height_bs_m": None})
    for m in _M_BS.finditer(sentence):
        if not any(o["kind"] == "level_with_height" and
                   o["height_bs_m"] == float(m.group(1).replace(",", ".")) for o in out):
            out.append({**base, "kind": "height", "value": float(m.group(1).replace(",", ".")),
                        "unit": "m BS", "height_bs_m": float(m.group(1).replace(",", "."))})
    for m in _KM3.finditer(sentence):
        out.append({**base, "kind": "volume", "value": float(m.group(1).replace(",", ".")),
                    "unit": "km3", "height_bs_m": None})
    for m in _PCT.finditer(sentence):
        out.append({**base, "kind": "percent", "value": float(m.group(1)),
                    "unit": "%", "height_bs_m": None})
    for m in _M_DELTA.finditer(sentence):
        out.append({**base, "kind": "level_change", "value": float(m.group(1).replace(",", ".")),
                    "unit": "m", "height_bs_m": None})
    return out


def run(pc: PageCache, daily_rows: list[dict] | None = None) -> dict[str, Extracted]:
    s = span(VOLUME, "overview")
    pc.check_span(s)
    over = Extracted("overview_2023")
    facts = Extracted("narrative_facts_2023")

    # Collect paragraphs across pages first: a paragraph cut by a page break
    # («…знизився до | передпаводкової відмітки.») continues in lowercase.
    collected: list[dict] = []
    for page in s.pages:
        over.pages.append(f"{VOLUME}:p{page}")
        for para in paragraphs(pc.text(VOLUME, page)):
            if collected and para[:1].islower() and \
                    not re.search(r"[.!?:»]\s*$", collected[-1]["text"]):
                collected[-1]["text"] += " " + para
                collected[-1]["pages"].append(page)
            else:
                collected.append({"page": page, "pages": [page], "text": para})

    subsection = "overview"
    breach_page = None
    for i, item in enumerate(collected):
        para = item["text"]
        flat = re.sub(r"\s+", " ", para)
        if BREACH_HEADING in flat:
            subsection = "dam_breach"
            breach_page = item["page"]
        posts = posts_in(para)
        over.rows.append({"page": item["page"], "paragraph_no": i + 1,
                          "subsection": subsection, "post_names": ";".join(posts),
                          "n_chars": len(para), "text": para, "source_volume": VOLUME})
        if subsection == "dam_breach":
            inherited: list[str] = []
            for sent in sentences(para):
                facts.rows.extend(facts_from(sent, item["page"], subsection, inherited))
                inherited = posts_in(sent) or inherited
        if subsection == "dam_breach" and flat.startswith(FIG5):
            subsection = "overview"          # the figure caption closes the subsection
    facts.pages = [f"{VOLUME}:p{breach_page}-{s.last}"] if breach_page else []
    over.check("breach_subsection_found", breach_page is not None,
               f"«{BREACH_HEADING}…» on p{breach_page}" if breach_page else "not found")

    # ── the datum, stated by the yearbook itself ──────────────────────────
    pairs = [r for r in facts.rows if r["kind"] == "level_with_height"]
    bad = [f"{r['value']:.0f} см ↔ {r['height_bs_m']:.2f} м БС"
           for r in pairs if abs((-5.0 + r["value"] / 100.0) - r["height_bs_m"]) > 0.006]
    facts.check("narrative_datum_consistent", bool(pairs) and not bad,
                f"{len(pairs)} «см (м БС)» pairs; all satisfy −5.000 + cm/100"
                if pairs and not bad else (f"mismatches: {bad}" if bad else "no pairs found"))

    # ── narrative extremes vs the daily table ─────────────────────────────
    if daily_rows:
        def daily_max(post_id, month="06"):
            vals = [d["level_cm"] for d in daily_rows if d["post_id"] == post_id
                    and d["date"].startswith(f"{YEAR_DEFAULT}-{month}")
                    and d["level_cm"] is not None]
            return max(vals) if vals else None
        for post_id, name, stated in (("kherson", "Херсон", 1068.0),
                                      ("mykolaiv", "Миколаїв", 602.0),
                                      ("parutyne", "Парутине", 599.0),
                                      ("ochakiv", "Очаків", 573.0)):
            narrated = [r for r in facts.rows if r["kind"] in ("level", "level_with_height")
                        and r["value"] == stated and name in r["post_names"]]
            dm = daily_max(post_id)
            facts.check(f"narrative_max_{post_id}",
                        bool(narrated) and dm is not None and dm <= stated,
                        f"narrative max {stated:.0f} cm "
                        f"{'found' if narrated else 'NOT found'}; June daily-mean max "
                        f"{dm:.0f} cm ≤ it" if dm is not None else "no June daily values")
    return {over.name: over, facts.name: facts}
