"""The two source volumes, their page map, and the posts we care about.

Page numbers are **PDF page indices (1-based)**, not the printed folio — the
printed numbering shifts around the unnumbered Додаток pages, so the folio is
useless as a key. Every entry is validated at run time against the caption the
page must carry (`assert_caption`), so a re-issued PDF cannot silently move a
table under a different post.
"""
from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from src.paper_3._utils import OUT_DIR, PROJECT_ROOT, PUBLISH_DIR

#: Where the PDFs live once copied in, so a run does not depend on F: being mounted.
SOURCE_DIR = OUT_DIR / "insitu" / "source"
#: Human-facing outputs, tracked next to the manuscript draft.
DEFAULT_OUT = PUBLISH_DIR / "insitu_2023"

YEAR = 2023


class PageMapError(RuntimeError):
    """A page did not carry the caption the page map expects."""


# ── volumes ───────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Volume:
    id: str
    label: str
    filename_marker: str      # substring that identifies the file on disk
    printed_title: str


VOLUMES: dict[str, Volume] = {
    "v229_seas": Volume(
        "v229_seas", "Інв. №229 — Том 2 (моря)", "№229",
        "Щорічні дані про режим та ресурси вод морів і морських гирл річок, 2023, Том 2"),
    "v230_mouths": Volume(
        "v230_mouths", "Інв. №230 — Том 2(2) (морські гирла річок)", "№230",
        "Щорічні дані про режим та ресурси вод морів і морських гирл річок, 2023, Том 2(2)"),
}


def find_pdfs(pdf_dir: Path) -> dict[str, Path]:
    """{volume_id: path}. Raises FileNotFoundError naming what is missing."""
    found: dict[str, Path] = {}
    for vol in VOLUMES.values():
        matches = sorted(p for p in Path(pdf_dir).glob("*.pdf")
                         if vol.filename_marker in p.name)
        if len(matches) != 1:
            raise FileNotFoundError(
                f"{vol.label}: expected exactly one *.pdf containing "
                f"{vol.filename_marker!r} in {pdf_dir}, found {len(matches)}")
        found[vol.id] = matches[0]
    return found


def sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def git_short() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                              cwd=str(PROJECT_ROOT), capture_output=True,
                              text=True, timeout=10).stdout.strip()
    except Exception:
        return ""


def copy_sources(pdfs: dict[str, Path], source_dir: Path | None = None
                 ) -> dict[str, Path]:
    """Copy the PDFs into the untracked source dir (once). Returns the copies."""
    target = Path(source_dir) if source_dir else SOURCE_DIR
    target.mkdir(parents=True, exist_ok=True)
    out: dict[str, Path] = {}
    for vol_id, src in pdfs.items():
        dst = target / src.name
        if not dst.exists() or sha256(dst) != sha256(src):
            shutil.copy2(src, dst)
        out[vol_id] = dst
    return out


# ── page map ──────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class PageSpan:
    volume: str
    table: str
    first: int
    last: int
    caption: str          # substring every page in the span must contain

    @property
    def pages(self) -> range:
        return range(self.first, self.last + 1)


PAGE_MAP: tuple[PageSpan, ...] = (
    PageSpan("v230_mouths", "stations",          146, 146, "СПИСОК  ПОСТІВ"),
    PageSpan("v230_mouths", "obs_notes",         147, 148, ""),
    PageSpan("v230_mouths", "descriptions",      149, 158, ""),
    PageSpan("v230_mouths", "hazard_events",     159, 160, "1.6.1"),
    # p165 (Миколаїв) prints the title but not the «Таблиця 2.1.1» label.
    PageSpan("v230_mouths", "daily_levels",      162, 168, "РІВНІ ВОДИ"),
    PageSpan("v230_mouths", "level_frequency",   169, 171, "2.1.2"),
    PageSpan("v230_mouths", "hazard_thresholds", 172, 173, "2.1.3"),
    # p184 opens «ОГЛЯД ГІДРОМЕТЕОРОЛОГІЧНИХ УМОВ»; pp193–195 are the dam-breach
    # subsection (checked by heading inside narrative.run).
    PageSpan("v230_mouths", "overview",          184, 195, ""),
    PageSpan("v229_seas",   "stations",           10,  10, "Перелік морських"),
    PageSpan("v229_seas",   "obs_notes",          11,  11, "строки спостережень"),
    PageSpan("v229_seas",   "descriptions",       12,  27, ""),
    PageSpan("v229_seas",   "sea_levels",         34,  35, "1.1.1"),
    PageSpan("v229_seas",   "sea_level_stats",    36,  39, "1.1.2"),
    PageSpan("v229_seas",   "surges",             40,  40, "1.1.3"),
)


def span(volume: str, table: str) -> PageSpan:
    for s in PAGE_MAP:
        if s.volume == volume and s.table == table:
            return s
    raise KeyError(f"no page span for {volume}/{table}")


def assert_caption(page_text: str, expected: str, volume: str, page: int) -> None:
    """Raise PageMapError unless `expected` occurs on the page (whitespace-insensitive)."""
    if not expected:
        return
    norm = re.sub(r"\s+", " ", page_text or "")
    want = re.sub(r"\s+", " ", expected)
    if want not in norm:
        raise PageMapError(
            f"{volume} p{page}: expected caption {expected!r} not found — the "
            f"PDF may be a different edition; page map needs re-verifying")


# ── posts ─────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Post:
    name: str            # canonical, as printed in the yearbook
    post_id: str         # ASCII key for CSVs
    water_body: str
    aliases: tuple[str, ...] = ()

    def matches(self, text: str) -> bool:
        return any(a in text for a in (self.name, *self.aliases))


ESTUARY_POSTS: tuple[Post, ...] = (
    Post("Каховська ГЕС", "kakhovka_hpp", "р. Дніпро", ("Каховська ГЕС",)),
    Post("Нова Каховка", "nova_kakhovka", "р. Дніпро", ("Нова Каховка",)),
    Post("Херсон", "kherson", "р. Дніпро", ("Херсон",)),
    Post("Касперівка", "kasperivka", "р. Дніпро, рук. Рвач", ("Касперівка", "Кізомис")),
    Post("Олександрівка", "oleksandrivka", "р. Південний Буг", ("Олександрівка",)),
    Post("Миколаїв", "mykolaiv", "р. Південний Буг", ("Миколаїв",)),
    Post("Парутине", "parutyne", "лим. Бузький", ("Парутине",)),
    Post("Станіслав", "stanislav", "лим. Дніпровський", ("Станіслав",)),
    Post("Геройське", "heroiske", "лим. Дніпро-Бузький", ("Геройське",)),
    Post("Очаків", "ochakiv", "лим. Дніпро-Бузький", ("Очаків",)),
)

_BY_NAME = {p.name: p for p in ESTUARY_POSTS}


def post_by_name(name: str) -> Post:
    return _BY_NAME[name]


def detect_post(text: str) -> Post | None:
    """The single estuary post named in `text`, or None.

    Longest alias wins, so «Нова Каховка» is not read as «Каховська ГЕС» and
    «Миколаївський ЦГМ» (an agency) does not count as the Миколаїв post: an
    alias must be followed by a non-letter or end of string.
    """
    best: tuple[int, Post] | None = None
    for post in ESTUARY_POSTS:
        for alias in (post.name, *post.aliases):
            for m in re.finditer(re.escape(alias), text):
                tail = text[m.end():m.end() + 1]
                if tail and (tail.isalpha()):
                    continue
                if best is None or len(alias) > best[0]:
                    best = (len(alias), post)
    return best[1] if best else None


def detect_posts(text: str) -> list[Post]:
    """Every estuary post named in `text`, in order of first appearance."""
    hits: list[tuple[int, Post]] = []
    for post in ESTUARY_POSTS:
        for alias in (post.name, *post.aliases):
            for m in re.finditer(re.escape(alias), text):
                tail = text[m.end():m.end() + 1]
                if tail and tail.isalpha():
                    continue
                hits.append((m.start(), post))
                break
            else:
                continue
            break
    seen: set[str] = set()
    out: list[Post] = []
    for _, post in sorted(hits, key=lambda h: h[0]):
        if post.name not in seen:
            seen.add(post.name)
            out.append(post)
    return out


#: «Відмітка нуля поста - 5,00 м БС - 77» / «-5.000 м (БС)» / «-3,02 м БС-77» /
#: «– 5,000 м БС – 77» (the descriptions use an en-dash).
ZERO_RE = re.compile(r"(?<![\d.,])[-–—]\s*(\d{1,2}[.,]\d{1,3})\s*м\b")
HEIGHT_SYSTEM_RE = re.compile(r"БС\s*[-–—]?\s*77|БС\b|Балтійськ\w*")


def parse_zero(text: str) -> tuple[float | None, str, str]:
    """(zero_of_post_m, height_system, verbatim_line) from a caption or list cell."""
    m = ZERO_RE.search(text or "")
    if not m:
        return None, "", ""
    value = -float(m.group(1).replace(",", "."))
    system = ""
    hs = HEIGHT_SYSTEM_RE.search(text, m.end())
    if hs:
        system = "БС-77" if "77" in hs.group(0) else "БС"
    line = text[max(0, m.start() - 24):hs.end() if hs else m.end()].strip()
    return value, system, line
