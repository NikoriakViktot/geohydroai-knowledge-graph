"""Anchored revisions: apply documented edits to a text, or fail (from tools/paper3_audit/revise.py).

An entry replaces text (``original`` must occur exactly once, whitespace-tolerant) or inserts a
paragraph after an anchor (``insert_after``). A failed match aborts, so a revised text can only
contain documented changes; the original is never written to. ``render_log`` writes the change log.
"""

from __future__ import annotations

import re

CHANGE_TYPES = ("scientific", "terminology", "citation", "stylistic", "structure")


def find_once(text: str, needle: str) -> tuple[int, int]:
    idx = text.find(needle)
    if idx != -1:
        if text.find(needle, idx + 1) != -1:
            raise ValueError(f"not unique: {needle[:60]!r}")
        return idx, idx + len(needle)
    pattern = r"\s+".join(re.escape(w) for w in needle.split())
    hits = list(re.finditer(pattern, text))
    if len(hits) != 1:
        raise ValueError(f"{'not found' if not hits else 'not unique'}: {needle[:80]!r}")
    return hits[0].start(), hits[0].end()


def apply(entries: list[dict], texts: dict[str, str], default_file: str) -> tuple[dict[str, str], list[dict]]:
    log, texts = [], dict(texts)
    for e in entries:
        eid = e.get("id", "?")
        if e.get("change_type") not in CHANGE_TYPES:
            raise ValueError(f"{eid}: change_type must be one of {CHANGE_TYPES}")
        name = e.get("file", default_file)
        text = texts[name]
        if "original" in e:
            a, b = find_once(text, e["original"])
            log.append({**e, "file": name, "matched_original": text[a:b]})
            text = text[:a] + e["revised"] + text[b:]
        elif "insert_after" in e:
            a, b = find_once(text, e["insert_after"])
            log.append({**e, "file": name, "matched_original": ""})
            text = text[:b] + "\n\n" + e["revised"].rstrip("\n") + text[b:]
        else:
            raise ValueError(f"{eid}: needs 'original' or 'insert_after'")
        texts[name] = text
    return texts, log


def render_log(log: list[dict], title: str) -> str:
    esc = lambda s: str(s).replace("|", "\\|").replace("\n", " ")
    out = [f"# {title}", "", "| # | id | file | section | type | thesis ids | references used | reason |",
           "|---|---|---|---|---|---|---|---|"]
    for i, e in enumerate(log, 1):
        out.append(f"| {i} | {e.get('id', '')} | {e['file']} | {esc(e.get('section', ''))} | {e.get('change_type', '')} | "
                   f"{', '.join(e.get('thesis_ids') or [])} | {', '.join(e.get('refs') or [])} | {esc(e.get('reason', ''))} |")
    out += ["", "## Full text of each change", ""]
    for i, e in enumerate(log, 1):
        out += [f"### {i}. {e.get('id', '')} — {e['file']} — {e.get('section', '')}", "", f"**Reason.** {e.get('reason', '')}", ""]
        if e.get("matched_original"):
            out += ["**Original:**", "", "> " + " ".join(e["matched_original"].split()), ""]
        else:
            out += [f"**Inserted after:** `{' '.join(e.get('insert_after', '').split())[:140]}…`", ""]
        out += ["**Revised:**", "", "> " + " ".join(e["revised"].split()), ""]
    return "\n".join(out) + "\n"
