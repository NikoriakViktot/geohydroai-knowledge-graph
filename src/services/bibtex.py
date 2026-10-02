"""Minimal BibTeX reading: entries, fields with nested braces, author lists.

Moved from tools/paper3_audit/bibtex.py so that the API does not depend on a
project-specific tool; that module re-exports parse_bib from here.
"""

from __future__ import annotations

import re
import unicodedata

_ENTRY = re.compile(r"@(\w+)\s*\{\s*([^,\s]+)\s*,", re.MULTILINE)
_FIELD = re.compile(r"\s*([A-Za-z_][\w-]*)\s*=\s*")


def split_fields(body: str) -> dict[str, str]:
    """Parse ``key={value}, key2="value"`` with nested braces."""
    fields: dict[str, str] = {}
    i, n = 0, len(body)
    while i < n:
        m = _FIELD.match(body, i)
        if not m:
            break
        key = m.group(1).lower()
        i = m.end()
        if i >= n:
            break
        if body[i] == "{":
            depth, j = 0, i
            while j < n:
                if body[j] == "{":
                    depth += 1
                elif body[j] == "}":
                    depth -= 1
                    if depth == 0:
                        break
                j += 1
            value = body[i + 1:j]
            i = j + 1
        elif body[i] == '"':
            j = body.find('"', i + 1)
            value = body[i + 1:j]
            i = j + 1
        else:
            j = body.find(",", i)
            j = n if j == -1 else j
            value = body[i:j].strip()
            i = j
        fields[key] = " ".join(value.split())
        comma = body.find(",", i)
        i = n if comma == -1 else comma + 1
    return fields


def parse_bib(text: str) -> list[dict]:
    """→ [{'key', 'type', 'fields': {...}}] in file order. Comments are ignored."""
    entries = []
    for m in _ENTRY.finditer(text):
        start = m.end()
        depth, j = 1, start
        while j < len(text) and depth:
            if text[j] == "{":
                depth += 1
            elif text[j] == "}":
                depth -= 1
            j += 1
        body = text[start:j - 1]
        entries.append({"key": m.group(2), "type": m.group(1).lower(), "fields": split_fields(body)})
    return entries


def strip_braces(value: str | None) -> str:
    return re.sub(r"[{}]", "", value or "").strip()


def fold(name: str | None) -> str:
    """Case-, accent- and punctuation-insensitive form of a name, for comparison."""
    text = unicodedata.normalize("NFKD", strip_braces(name))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"[^a-z]", "", text.casefold())


def _split_and(value: str) -> list[str]:
    """Split a BibTeX author field on ' and ' outside braces."""
    parts, depth, cur, i = [], 0, [], 0
    while i < len(value):
        ch = value[i]
        depth += ch == "{"
        depth -= ch == "}"
        if depth == 0 and value[i:i + 5].lower() == " and ":
            parts.append("".join(cur))
            cur, i = [], i + 5
            continue
        cur.append(ch)
        i += 1
    parts.append("".join(cur))
    return parts


def parse_authors(value: str | list | None) -> tuple[list[tuple[str, str, bool]], bool]:
    """BibTeX 'Family, Given and Given Family and {Org Name} and others' (or a list of such
    strings) → ([(family, given, corporate)], truncated). A name wholly in braces is a
    corporate author; `truncated` is True for 'and others' / 'et al.'."""
    if not value:
        return [], False
    parts = value if isinstance(value, list) else _split_and(" ".join(str(value).split()))
    out, truncated = [], False
    for raw in parts:
        p = str(raw).strip().rstrip(",")
        if not p:
            continue
        if p.lower() in ("others", "et al.", "et al"):
            truncated = True
            continue
        if p.startswith("{") and p.endswith("}") and p.count("{") == 1:
            out.append((strip_braces(p), "", True))
            continue
        p = strip_braces(p)
        if "," in p:
            family, given = (x.strip() for x in p.split(",", 1))
        else:
            bits = p.split()
            family, given = (bits[-1], " ".join(bits[:-1])) if len(bits) > 1 else (p, "")
        out.append((family, given, False))
    return out, truncated
