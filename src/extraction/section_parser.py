"""
Deterministic, rule-based section parser for scientific PDF text.

parse_document_sections(text) → dict
    keys: title | abstract | introduction | methods | results | discussion
    values: str | None   (None = section not found or not detectable)

Design contract
───────────────
- No LLM, no external libraries beyond the standard library + re.
- Deterministic: same input → same output every time.
- Handles 300+ PDFs without manual tuning.
- Robust to: two-column layouts, numbered / unnumbered / all-caps /
  spaced-letter headings ("A B S T R A C T"), combined sections
  ("Results and Discussion"), missing section markers.
"""
from __future__ import annotations

import re
import unicodedata

# ── Public contract ────────────────────────────────────────────────────────────

SECTION_KEYS = ("title", "abstract", "introduction", "methods", "results", "discussion")

SectionDict = dict[str, str | None]


def parse_document_sections(text: str) -> SectionDict:
    """
    Parse raw PDF text into structured scientific sections.

    Parameters
    ----------
    text : str
        Full concatenated text of a single PDF (all pages joined).

    Returns
    -------
    SectionDict — keys: title, abstract, introduction, methods, results, discussion.
    Missing sections return None.  All values are whitespace-cleaned.
    """
    if not text or not text.strip():
        return {k: None for k in SECTION_KEYS}

    text    = _normalize(text)
    lines   = text.splitlines()
    markers = _find_markers(lines)

    first_content_idx = markers[0][0] if markers else len(lines)
    result: SectionDict = {k: None for k in SECTION_KEYS}

    # Title from the preamble (before first section heading)
    result["title"] = _extract_title(lines, first_content_idx)

    # Extract text for each canonical section
    for i, (line_idx, section_name) in enumerate(markers):
        if section_name.startswith("_"):
            continue                      # boundary marker — skip content extraction
        if result[section_name] is not None:
            continue                      # first occurrence wins

        content_start = line_idx + 1
        content_end   = markers[i + 1][0] if i + 1 < len(markers) else len(lines)
        block         = _extract_block(lines, content_start, content_end)
        if block:
            result[section_name] = block

    # Abstract fallback: no header found → first substantial paragraph(s)
    if result["abstract"] is None:
        result["abstract"] = _abstract_fallback(lines, first_content_idx)

    return result


# ── Section heading registry ──────────────────────────────────────────────────
#
# Each entry maps a canonical name to a list of raw regex strings.
# Patterns are matched against a "cleaned" heading line:
#   • section number stripped  ("1.", "2.1.", "3.1.2 " …)
#   • trailing punctuation stripped
#   • re.fullmatch → the entire cleaned line must match
#
# Names starting with "_" are boundary markers used to end sections but are
# NOT extracted as output keys.

_SECTION_DEFS: dict[str, list[str]] = {

    "abstract": [
        r"a\s*b\s*s\s*t\s*r\s*a\s*c\s*t",      # "abstract" or "A B S T R A C T"
        r"s\s*u\s*m\s*m\s*a\s*r\s*y",
    ],

    "introduction": [
        r"introduction",
        r"background(?:\s+and\s+motivation)?",
        r"overview",
        r"motivation",
    ],

    "methods": [
        r"methods?(?:\s+and\s+materials?)?",
        r"materials?\s+(?:and|&)\s+methods?",
        r"methodology",
        r"experimental\s+(?:setup|design|methods?|procedures?)",
        r"study\s+area(?:\s+and\s+(?:data|methods?))?",
        r"data(?:set)?(?:\s+and\s+methods?|\s+description|\s+acquisition|\s+processing)?",
        r"proposed\s+(?:method|approach|framework|algorithm)",
        r"remote\s+sensing\s+data(?:\s+and\s+methods?)?",
        r"sar\s+data(?:\s+and\s+(?:methods?|processing))?",
        r"image\s+(?:acquisition|processing|classification)",
        r"flood\s+(?:detection|mapping)\s+method",
    ],

    "results": [
        r"results?(?:\s+and\s+discussion)?",
        r"experimental\s+results?",
        r"accuracy\s+assessment(?:\s+and\s+discussion)?",
        r"performance\s+(?:evaluation|assessment|analysis)",
        r"evaluation(?:\s+and\s+results?)?",
        r"validation(?:\s+(?:results?|analysis))?",
        r"findings",
        r"flood\s+(?:mapping\s+)?results?",
        r"classification\s+results?",
    ],

    "discussion": [
        r"discussion(?:\s+and\s+(?:conclusion|analysis|interpretation))?",
        r"analysis\s+and\s+discussion",
        r"interpretation(?:\s+and\s+discussion)?",
    ],

    # ── Boundary markers (end sections; content not extracted) ────────────────

    "_conclusion": [
        r"conclusions?",
        r"concluding\s+remarks?",
        r"summary\s+and\s+conclusions?",
        r"final\s+remarks?",
        r"summary",
    ],

    "_references": [
        r"references",
        r"bibliography",
        r"cited\s+(?:works?|references?|literature)",
        r"literature\s+cited",
    ],

    "_acknowledgments": [
        r"acknowledgm?ents?",
        r"funding",
        r"author\s+contributions?",
        r"declarations?",
        r"competing\s+interests?",
        r"data\s+availability(?:\s+statement)?",
        r"supplementary(?:\s+(?:materials?|information))?",
        r"appendix",
        r"conflict\s+of\s+interest",
        r"ethics\s+(?:statement|approval)",
    ],

    # Terminates the abstract block; not extracted as a section
    "_keywords": [
        r"keywords?",
        r"index\s+terms?",
        r"subject\s+classification",
        r"nomenclature",
        r"abbreviations?",
    ],
}


# ── Pre-compile heading table ─────────────────────────────────────────────────
# Format: [(fullmatch_pattern, canonical_name), ...]
# Ordered so that more-specific patterns are checked before generic ones.

def _build_heading_table() -> list[tuple[re.Pattern, str]]:
    table = []
    # Put canonical sections first, boundary markers after
    ordered = sorted(
        _SECTION_DEFS.items(),
        key=lambda kv: (1 if kv[0].startswith("_") else 0, kv[0]),
    )
    for canonical, patterns in ordered:
        for pat_str in patterns:
            table.append((
                re.compile(pat_str, re.IGNORECASE),
                canonical,
            ))
    return table


_HEADING_TABLE = _build_heading_table()

# Strips leading section numbers (Arabic and Roman):
#   "1.", "2.1.", "3.1.2 ", "I.", "II.", "III.", "IV.", "I) ", "A. "
_NUM_PREFIX_RE = re.compile(
    r"^(?:"
    r"\d+(?:\.\d+)*[.)]\s+"     # Arabic:  "1.", "2.1)", "3.1.2."
    r"|[IVXivx]+[.)]\s+"        # Roman:   "I.", "II.", "III.", "IV."
    r"|[A-Z][.)]\s+"            # Letter:  "A.", "B.", "C."
    r")"
)
# Strips trailing punctuation / whitespace
_TRAIL_PUNCT_RE = re.compile(r"[\s.:)]+$")


def _classify_heading(line: str) -> str | None:
    """
    Return the canonical section name if *line* is a section heading, else None.

    A line qualifies as a heading only if:
      1. Stripped length ≤ 100 characters.
      2. The ENTIRE cleaned line matches a known heading pattern (fullmatch).
      3. The line does not end with a comma or semicolon (mid-sentence continuations).
    """
    stripped = line.strip()
    if not stripped or len(stripped) > 100:
        return None

    # Rule 3: reject obvious sentence continuations
    if stripped.endswith((",", ";")):
        return None

    # Fast path: inline "Keywords: ..." / "Index Terms — ..." headers
    # These appear with content on the same line so fullmatch won't work.
    _INLINE_BOUNDARY_RE = re.compile(
        r"^(?:keywords?|index\s+terms?|nomenclature|abbreviations?)\s*[:\-—]",
        re.IGNORECASE,
    )
    if _INLINE_BOUNDARY_RE.match(stripped):
        return "_keywords"

    # Build the cleaned candidate: remove section number then trailing punctuation
    cleaned = _NUM_PREFIX_RE.sub("", stripped)
    cleaned = _TRAIL_PUNCT_RE.sub("", cleaned).strip()

    if not cleaned:
        return None

    for pattern, canonical in _HEADING_TABLE:
        if pattern.fullmatch(cleaned):
            return canonical

    return None


# ── Section marker finder ─────────────────────────────────────────────────────

def _find_markers(lines: list[str]) -> list[tuple[int, str]]:
    """
    Scan *lines* and return [(line_index, canonical_name), ...] sorted by index.

    Each canonical section appears at most once (first occurrence wins).
    Boundary markers ("_conclusion", "_references", …) may appear multiple times.
    """
    seen_canonical: set[str] = set()
    markers: list[tuple[int, str]] = []

    for i, line in enumerate(lines):
        name = _classify_heading(line)
        if name is None:
            continue

        # De-duplicate canonical (non-boundary) sections
        if not name.startswith("_"):
            if name in seen_canonical:
                continue
            seen_canonical.add(name)

        markers.append((i, name))

    return markers


# ── Noise line detection ──────────────────────────────────────────────────────

_NOISE_RES: list[re.Pattern] = [
    # DOI
    re.compile(r"10\.\d{4,9}/", re.IGNORECASE),
    # ISSN / ISBN
    re.compile(r"\bissn\b|\bisbn\b", re.IGNORECASE),
    # Volume / issue / pages
    re.compile(r"\bvol(?:ume)?\.?\s*\d+", re.IGNORECASE),
    re.compile(r"\bno\.?\s*\d+\b", re.IGNORECASE),
    re.compile(r"\bpp\.?\s*\d+", re.IGNORECASE),
    # Publisher tags
    re.compile(r"research\s+article", re.IGNORECASE),
    re.compile(r"original\s+(?:research|article|paper)", re.IGNORECASE),
    re.compile(r"review\s+article", re.IGNORECASE),
    re.compile(r"open\s+access", re.IGNORECASE),
    re.compile(r"peer.?reviewed", re.IGNORECASE),
    # Copyright
    re.compile(r"[©®]\s*\d{4}|copyright\s+\d{4}", re.IGNORECASE),
    # Emails
    re.compile(r"\b[\w.+-]+@[\w-]+\.\w{2,}\b"),
    # URLs
    re.compile(r"https?://"),
    re.compile(r"www\.\S+\.\w{2,}"),
    # Pure page numbers
    re.compile(r"^\s*\d{1,4}\s*$"),
    # Submission / acceptance dates
    re.compile(r"(?:received|accepted|published|revised)\s*:\s*\w", re.IGNORECASE),
    # Keywords label
    re.compile(r"^\s*keywords?\s*[:\-—]", re.IGNORECASE),
    # Correspondence / affiliation markers
    re.compile(r"^\s*\*\s*(?:corresponding|contact)", re.IGNORECASE),
    # Journal header patterns (MDPI, Elsevier, IEEE, etc.)
    re.compile(r"mdpi\.com|elsevier\.com|ieee\.org|springer\.com|wiley\.com",
               re.IGNORECASE),
    re.compile(r"remote\s+sens\.\s+\d{4}", re.IGNORECASE),
    re.compile(r"int\.\s+j\.\s+\w+", re.IGNORECASE),
]


def _is_noise_line(line: str) -> bool:
    """True if *line* matches any known noise pattern."""
    return any(p.search(line) for p in _NOISE_RES)


# ── Title extractor ───────────────────────────────────────────────────────────

_TITLE_EXCLUDE_RES: list[re.Pattern] = _NOISE_RES + [
    # Author patterns: "Smith et al." or "J. Smith, M. Jones"
    re.compile(r"\bet\s+al\b", re.IGNORECASE),
    re.compile(r"^[A-Z]\.\s+[A-Z][a-z]+"),  # "J. Smith"
    # Affiliations
    re.compile(r"\buniversity\b|\binstitute\b|\bdepartment\b|\bfaculty\b",
               re.IGNORECASE),
    re.compile(r"\bInc\b|\bLtd\b|\bGmbH\b|\bS\.A\b", re.IGNORECASE),
    # Superscript-heavy author lists (many digits in a row)
    re.compile(r"\d{1,2},\s*\d{1,2}"),
    # Numbered list items in text (likely body text, not title)
    re.compile(r"^\s*\d+\.\s+[a-z]"),
]


def _is_title_candidate(line: str) -> bool:
    """
    Return True if *line* could be (part of) the document title.

    Rules:
    - Length between 30 and 200 characters.
    - First character is alphanumeric (not special symbol).
    - Does not match any exclusion pattern.
    - Does not look like a section heading itself.
    """
    stripped = line.strip()
    n = len(stripped)
    if n < 30 or n > 200:
        return False
    if not stripped[0].isalpha():
        return False
    if any(p.search(stripped) for p in _TITLE_EXCLUDE_RES):
        return False
    if _classify_heading(stripped) is not None:
        return False
    return True


def _extract_title(lines: list[str], up_to: int) -> str | None:
    """
    Find the document title in lines[0:up_to].

    Strategy:
    1. Skip noise lines.
    2. Find the first line that passes _is_title_candidate.
    3. Attempt to extend the title over consecutive candidate lines
       (handles multi-line titles common in IEEE / Elsevier formats).
    4. Return the joined, cleaned title or None if nothing found.
    """
    search_limit = min(up_to, 40)   # title must appear in first 40 lines
    title_lines: list[str] = []
    collecting = False

    for i in range(search_limit):
        line = lines[i].strip()
        if not line:
            if collecting and title_lines:
                break       # blank line ends multi-line title
            continue

        if _is_title_candidate(line):
            title_lines.append(line)
            collecting = True
        elif collecting:
            # Once we started collecting, stop at first non-candidate
            break

    if not title_lines:
        return None

    title = _join_sentence_lines(title_lines)
    title = re.sub(r"\s+", " ", title).strip()
    return title if len(title) >= 30 else None


# ── Abstract fallback ─────────────────────────────────────────────────────────

_MIN_ABSTRACT_CHARS  = 150   # minimum chars for a paragraph to qualify
_MAX_ABSTRACT_CHARS  = 1800  # cap returned abstract length


def _abstract_fallback(lines: list[str], first_section_idx: int) -> str | None:
    """
    When no Abstract heading is found, derive an abstract from the
    first substantial paragraph(s) of the preamble.

    Strategy:
    - Split the preamble into blank-line-separated paragraphs.
    - Discard paragraphs that are too short (title, author lines, affiliations).
    - Return the first paragraph with ≥ _MIN_ABSTRACT_CHARS characters.
      If that's still not enough, combine the first two qualifying paragraphs.
    """
    # Collect all paragraphs before the first section marker
    all_paragraphs: list[str] = []
    current: list[str] = []

    for i in range(first_section_idx):
        line = lines[i].strip()
        if not line:
            if current:
                para = _join_sentence_lines(current)
                all_paragraphs.append(para)
                current = []
        elif not _is_noise_line(line):
            current.append(line)

    if current:
        all_paragraphs.append(_join_sentence_lines(current))

    # Pick the first paragraph with enough content (skips title / author blocks)
    qualifying = [p for p in all_paragraphs if len(p) >= _MIN_ABSTRACT_CHARS]

    if not qualifying:
        # Last resort: combine everything that's not tiny
        combined = " ".join(p for p in all_paragraphs if len(p) >= 30)
        return combined[:_MAX_ABSTRACT_CHARS] if len(combined) >= _MIN_ABSTRACT_CHARS else None

    # Take the best paragraph; if one isn't long enough on its own, add the next
    result = qualifying[0]
    if len(result) < 300 and len(qualifying) > 1:
        result = result + "\n\n" + qualifying[1]

    return result[:_MAX_ABSTRACT_CHARS].strip()


# ── Block extractor ───────────────────────────────────────────────────────────

_MAX_SECTION_CHARS = 8000   # cap individual sections to avoid runaway text


def _extract_block(lines: list[str], start: int, end: int) -> str | None:
    """
    Extract and clean text from lines[start:end].

    Steps:
    1. Filter obvious page artifacts.
    2. Join lines into paragraphs (handle hyphenation, preserve blank lines).
    3. Strip and cap the result.
    """
    if start >= end:
        return None

    relevant = _filter_artifacts(lines[start:end])
    if not relevant:
        return None

    text = _join_block(relevant)
    text = re.sub(r"[ \t]+", " ", text)          # collapse horizontal whitespace
    text = re.sub(r"\n{3,}", "\n\n", text)        # max two consecutive newlines
    text = text.strip()

    if len(text) < 20:                             # discard only truly empty blocks
        return None

    return text[:_MAX_SECTION_CHARS]


# ── Page artifact filter ──────────────────────────────────────────────────────

# Lines that are almost certainly page headers/footers or metadata, not content.
_ARTIFACT_RES: list[re.Pattern] = [
    re.compile(r"^\s*\d{1,4}\s*$"),                          # lone page number
    re.compile(r"^\s*-\s*\d{1,4}\s*-\s*$"),                  # "- 12 -"
    re.compile(r"https?://|www\.\S+\.\w{2,}"),                # URLs
    re.compile(r"mdpi\.com|elsevier\.com|ieee\.org|springer", re.IGNORECASE),
    re.compile(r"[©®]\s*\d{4}", re.IGNORECASE),               # copyright line
    re.compile(r"^\s*(?:received|accepted|published)\s*:",    re.IGNORECASE),
    re.compile(r"10\.\d{4,9}/"),                               # DOI line
    re.compile(r"^\s*(?:figure|fig\.?|table|tab\.?)\s+\d+\s*[.:]?\s*$",
               re.IGNORECASE),                                 # orphaned caption labels
]


def _filter_artifacts(lines: list[str]) -> list[str]:
    """Remove lines that are clearly page artifacts, not section content."""
    cleaned = []
    for line in lines:
        if any(p.search(line) for p in _ARTIFACT_RES):
            continue
        cleaned.append(line)
    return cleaned


# ── Line joining ──────────────────────────────────────────────────────────────

def _join_block(lines: list[str]) -> str:
    """
    Join lines from a section block into coherent text.

    Algorithm:
    - Blank line → paragraph break (emit \n\n).
    - Line ending with hyphen:
        · next line starts lowercase → word split, remove hyphen and join directly.
        · next line starts uppercase/digit → intentional hyphen, keep and join.
    - Otherwise → join with a single space.
    """
    paragraphs: list[str] = []
    current: list[str] = []

    for i, raw in enumerate(lines):
        line = raw.strip()
        if not line:
            if current:
                paragraphs.append(_join_sentence_lines(current))
                current = []
        else:
            current.append(line)

    if current:
        paragraphs.append(_join_sentence_lines(current))

    return "\n\n".join(p for p in paragraphs if p)


def _join_sentence_lines(lines: list[str]) -> str:
    """
    Join a list of lines (one paragraph) into a single string.

    Handles hyphenated line endings:
    - "detec-" + "tion" → "detection"  (lowercase continuation → strip hyphen)
    - "Sentinel-" + "1"  → "Sentinel-1" (digit/uppercase continuation → keep hyphen)
    """
    result = ""
    for line in lines:
        if not line:
            continue
        if not result:
            result = line
        elif result.endswith("-"):
            next_char = line[0] if line else ""
            if next_char.islower():
                result = result[:-1] + line     # "detec-" + "tion" → "detection"
            else:
                result = result + line           # "Sentinel-" + "1" → "Sentinel-1"
        else:
            result = result + " " + line
    return result


# ── Text normalization ────────────────────────────────────────────────────────

_LIGATURE_MAP: dict[str, str] = {
    "ﬀ": "ff",   # ﬀ
    "ﬁ": "fi",   # ﬁ
    "ﬂ": "fl",   # ﬂ
    "ﬃ": "ffi",  # ﬃ
    "ﬄ": "ffl",  # ﬄ
    "­": "",     # soft hyphen (invisible)
    "–": "-",    # en dash
    "—": "-",    # em dash
    "‘": "'",    # left single quote
    "’": "'",    # right single quote
    "“": '"',    # left double quote
    "”": '"',    # right double quote
    "•": "*",    # bullet
    "…": "...",  # ellipsis
    " ": " ",    # non-breaking space
    "​": "",     # zero-width space
    "‌": "",     # zero-width non-joiner
    "‍": "",     # zero-width joiner
    "﻿": "",     # BOM
}


def _normalize(text: str) -> str:
    """
    Normalize raw PDF text:
    1. Replace ligatures, smart quotes, dashes, zero-width chars.
    2. Apply Unicode NFC normalization.
    3. Collapse horizontal whitespace within each line (preserve line breaks).
    4. Remove lines that are pure whitespace.
    """
    # Step 1: character replacements
    for src, dst in _LIGATURE_MAP.items():
        if src in text:
            text = text.replace(src, dst)

    # Step 2: Unicode NFC
    text = unicodedata.normalize("NFC", text)

    # Step 3-4: per-line normalization
    cleaned_lines = []
    for line in text.splitlines():
        line = " ".join(line.split())   # collapse internal whitespace
        cleaned_lines.append(line)      # preserve blank lines (paragraph markers)

    return "\n".join(cleaned_lines)


# ── Convenience: section-level describe ───────────────────────────────────────

def describe_parsed(sections: SectionDict) -> tuple[list[str], list[str]]:
    """Return (found, missing) section key lists (excludes 'title')."""
    content_keys = [k for k in SECTION_KEYS if k != "title"]
    found   = [k for k in content_keys if sections.get(k)]
    missing = [k for k in content_keys if not sections.get(k)]
    return found, missing


# ── Canonical schema dataclass ────────────────────────────────────────────────

from dataclasses import dataclass as _dc  # noqa: E402


@_dc
class DocumentSections:
    """
    Typed container for parsed document sections.

    This is the ONE canonical schema all downstream code must consume.
    All fields default to None when a section was not detected.
    """
    title:        str | None = None
    abstract:     str | None = None
    introduction: str | None = None
    methods:      str | None = None
    results:      str | None = None
    discussion:   str | None = None

    @classmethod
    def from_dict(cls, d: SectionDict) -> "DocumentSections":
        return cls(**{k: d.get(k) for k in SECTION_KEYS})

    def to_dict(self) -> SectionDict:
        return {k: getattr(self, k) for k in SECTION_KEYS}


# ── Debug helper ──────────────────────────────────────────────────────────────

def debug_sections(sections: SectionDict, source_name: str = "") -> None:
    """Print detected / missing sections to stdout (always; used for diagnostics)."""
    found, missing = describe_parsed(sections)
    prefix = f"[{source_name}] " if source_name else ""
    print(f"\n{prefix}Sections detected: {found or '—'}")
    print(f"{prefix}Missing sections:  {missing or '—'}")
