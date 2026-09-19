"""War-related observation gaps, in the yearbook's own words.

Sources: the «Відомості про строки спостережень…» pages (№230 pp 147–148,
№229 p11), every «Примітка» block the table parsers collected, and the
station list's status column. A record is a **verbatim sentence** that names
a post and states a cause; the category is a keyword tag on top, never a
rewording. Dates are lifted only when written in full (dd.mm.yyyy) or as
dd.mm within a sentence about 2023.
"""
from __future__ import annotations

import re

from src.paper_3.insitu.pages import PageCache
from src.paper_3.insitu.sources import detect_posts, span
from src.paper_3.insitu.tables import Extracted
from src.paper_3.insitu.values import YEAR_DEFAULT

CAUSES: tuple[tuple[str, str], ...] = (
    ("dam_breach", r"підрив\w*\s+гребл|знищенн\w*\s+Каховськ|руйнуванн\w*\s+Каховськ"),
    ("occupation", r"окупац"),
    ("shelling", r"обстріл|артилер"),
    ("access_ban", r"заборон|доступ"),
    ("equipment_destroyed", r"зруйнов|руйнуванн|пошкодж|вийшов\s+із\s+ладу|поломк"),
    ("well_flooded", r"переповненн\w*\s+колодяз|затопленн\w*\s+(?:установки|павільйон)"),
    ("evacuation", r"евакуац"),
    ("mining", r"мінуванн"),
    ("suspended", r"призупинен|припинен|перебої|не\s+провод"),
    ("closed", r"закрит"),
    ("relocated", r"перенес|тимчасов\w*\s+місц"),
    ("data_absent", r"дан[іи][^.;]{0,60}відсутн|відсутн[^.;]{0,30}дан|забракован"),
)
#: Legend lines under a table («/ - спотворення рівня…», «10, 11.02 - голки»)
#: explain marks, they do not report a gap.
_LEGEND = re.compile(r"(?:^|\s)[/!?*]+\s*-\s|-\s*голки|значення\s+сумнівн|"
                     r"з\s+почащен|розрахунок\s+проведено")
_FULL_DATE = re.compile(r"(\d{2})\.(\d{2})\.(\d{4})")
_SHORT_DATE = re.compile(r"(?<![\d.])(\d{2})\.(\d{2})(?![\d.])")
#: «з 07.06.23», «з 03.08.2023», «з 11.05» — a gap starts at the date after «з».
_FROM = re.compile(r"\bз\s+(\d{1,2})\.(\d{2})(?:\.(\d{2}|\d{4}))?(?![\d.])")
_TO = re.compile(r"\b(?:по|до)\s+(\d{1,2})\.(\d{2})(?:\.(\d{2}|\d{4}))?(?![\d.])")


def _iso(d: str, m: str, y: str | None) -> str:
    year = YEAR_DEFAULT if not y else (2000 + int(y) if len(y) == 2 else int(y))
    return f"{year}-{m}-{int(d):02d}"
_SENT_SPLIT = re.compile(r"(?<=[а-яїієґ\)\»;])\.\s+(?=[А-ЯЇІЄҐ«\d])|;\s+(?=[а-яА-ЯїЇіІєЄ])")


def sentences(text: str) -> list[str]:
    flat = re.sub(r"\s+", " ", text or "").strip()
    return [s.strip() for s in _SENT_SPLIT.split(flat) if s.strip()]


def categorise(sentence: str) -> list[str]:
    low = sentence.lower()
    return [tag for tag, pat in CAUSES if re.search(pat, low)]


def dates_in(sentence: str) -> tuple[str, str]:
    frm = _FROM.search(sentence)
    if frm:
        d, m, y = frm.groups()
        if 1 <= int(m) <= 12 and 1 <= int(d) <= 31:
            to = _TO.search(sentence, frm.end())
            return (_iso(d, m, y),
                    _iso(*to.groups()) if to and 1 <= int(to.group(2)) <= 12 else "")
    full = [f"{y}-{m}-{d}" for d, m, y in _FULL_DATE.findall(sentence)]
    if full:
        return full[0], (full[1] if len(full) > 1 else "")
    # A bare dd.mm with no «з» before it is a date *in* the sentence (an
    # ice-needle day, an event date), not the start of a gap.
    return "", ""


def records_from(text: str, volume: str, page: int, source: str,
                 default_post: str = "") -> list[dict]:
    """One record per sentence that names a cause. A sentence naming no post
    is attributed to the post the block last named («На МГП-I Геройське … .
    З 31.12.2023 пост закритий.»), and only failing that to `default_post`
    (the post whose table the footnote sits under)."""
    out = []
    last: list = []
    for sent in sentences(text):
        posts = detect_posts(sent)
        cats = categorise(sent)
        if posts:
            last = posts
        if not cats or _LEGEND.search(sent):
            continue
        attributed = posts or last
        if not attributed and not default_post:
            continue
        names = [p.name for p in attributed] or [default_post]
        ids = [p.post_id for p in attributed]
        frm, to = dates_in(sent)
        out.append({"post_names": ";".join(names), "post_ids": ";".join(ids),
                    "from_date": frm, "to_date": to,
                    "cause_categories": ";".join(cats),
                    "verbatim_quote": sent, "source": source,
                    "source_volume": volume, "page": page})
    return out


def run(pc: PageCache, footnotes: list[dict] | None = None,
        station_rows: list[dict] | None = None) -> dict[str, Extracted]:
    ex = Extracted("data_gaps")
    for volume in ("v230_mouths", "v229_seas"):
        s = span(volume, "obs_notes")
        for page in s.pages:
            ex.rows.extend(records_from(pc.text(volume, page), volume, page,
                                        "observation_notes"))
            ex.pages.append(f"{volume}:p{page}")
    for fn in footnotes or []:
        ex.rows.extend(records_from(fn["text"], fn["volume"], fn["page"],
                                    "table_footnote", fn.get("post_name", "")))
    for st in station_rows or []:
        status = st.get("status_2023") or ""
        if re.search(r"до\s+\d|закрит", status):
            ex.rows.append({"post_names": st["post_name"], "post_ids": st["post_id"],
                            "from_date": "", "to_date": "",
                            "cause_categories": "status_column",
                            "verbatim_quote": f"Період дії: {status}",
                            "source": "station_list", "source_volume": st["source_volume"],
                            "page": st["page"]})
    # Dedupe exact repeats (the same footnote is printed under several tables).
    seen: set[tuple] = set()
    unique = []
    for r in ex.rows:
        key = (r["verbatim_quote"], r["post_names"])
        if key not in seen:
            seen.add(key)
            unique.append(r)
    ex.rows = unique
    posts = {n for r in ex.rows for n in r["post_names"].split(";") if n}
    ex.check("gap_records", len(ex.rows) >= 8,
             f"{len(ex.rows)} verbatim gap records for {len(posts)} posts")
    for must in ("Нова Каховка", "Геройське", "Станіслав", "Касперівка", "Херсон"):
        ex.check(f"gap_mentions_{must}", must in posts, f"{must} named in a gap record")
    return {ex.name: ex}
