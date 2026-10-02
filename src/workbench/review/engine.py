"""Deterministic review of a manuscript against the paper's own rules (review_rules.yaml). No LLM.

The engine is generic; the rules are data in the paper's repository (manifest ``paths.review_rules``),
so the knowledge repository holds no paper-specific check. Every finding names its location, what
was observed, what the paper's terminology or tables require, and a suggested fix; severities are
CRITICAL / MAJOR / MINOR / INFO. Ported from tools/paper3_audit/checks.py (checks A–H of the
Paper 3 audit), whose findings the floodstate-eo rules reproduce.

Rule types (``type:``)
- ``forbidden``      phrases from a list or from a list variable of a file, whitespace-collapsed
- ``pattern``        every match of a regex is a finding
- ``near``           a match whose window lacks (``missing``) or contains (``present``) other terms
- ``requirements``   a match whose window lacks some of the named requirements (one finding, all names)
- ``co_occurrence``  substrings that together in their files are an inconsistency
- ``variants``       a quantity written in more than N forms across files
- ``absent``         none of the substrings occurs in a file
- ``table_interval`` a central value of a table row against its p05/p50/p95, and how the text quotes it
- ``table_rows``     table rows matching a filter with an empty required column
- ``table_text``     a table column containing a phrase
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import asdict, dataclass
from typing import Callable

SEVERITY_ORDER = {"CRITICAL": 0, "MAJOR": 1, "MINOR": 2, "INFO": 3}


@dataclass
class Finding:
    check_id: str
    severity: str
    location: str
    observed: str
    expected: str
    suggested_fix: str
    tags: str = ""


class Sources:
    """Named files and tables of a rules document, read through ``read(path) -> str | None``."""

    def __init__(self, doc: dict, read: Callable[[str], str | None]):
        self.doc, self._read = doc, read
        self._cache: dict[str, str] = {}

    def path(self, name: str) -> str:
        spec = self.doc.get("files", {}).get(name) or self.doc.get("tables", {}).get(name)
        if spec is None:
            raise KeyError(f"review rules: unknown file or table {name!r}")
        return spec["path"] if isinstance(spec, dict) else spec

    def label(self, name: str) -> str:
        spec = self.doc.get("files", {}).get(name) or self.doc.get("tables", {}).get(name)
        return spec.get("label", name) if isinstance(spec, dict) else name

    def text(self, name: str) -> str:
        if name not in self._cache:
            self._cache[name] = self._read(self.path(name)) or ""
        return self._cache[name]

    def rows(self, name: str) -> list[dict]:
        text = self.text(name)
        return list(csv.DictReader(io.StringIO(text))) if text else []


def line_of(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1


def _window(text: str, start: int, end: int, spec) -> str:
    if isinstance(spec, dict):
        return text[max(0, start - spec.get("before", 0)): end + spec.get("after", 0)]
    return text[max(0, start - spec): start + spec]


def _any(terms, text: str) -> bool:
    return any(re.search(t, text) for t in terms)


def _section_of(text: str, pos: int) -> str:
    heads = [m.group(0) for m in re.finditer(r"^#+ .*$", text[:pos], re.MULTILINE)]
    return heads[-1] if heads else ""


def _fmt(template: str, **values) -> str:
    return template.format(**values) if template else ""


def _finding(rule: dict, case: dict | None, location: str, **values) -> Finding:
    get = lambda k, d="": (case or {}).get(k, rule.get(k, d))
    return Finding(rule["id"], get("severity", "MINOR"), location, _fmt(get("observed"), **values),
                   _fmt(get("expected"), **values), _fmt(get("fix"), **values), get("tags", ""))


def _texts(rule: dict, src: Sources) -> list[tuple[str, str]]:
    names = rule["in"] if isinstance(rule["in"], list) else [rule["in"]]
    if rule.get("combine"):
        return [(rule.get("label") or "(" + "|".join(src.label(n).removesuffix(".md") for n in names) + ")",
                 "\n".join(src.text(n) for n in names))]
    return [(src.label(n), src.text(n)) for n in names]


# ── rule types ─────────────────────────────────────────────────────────────────

def forbidden(rule: dict, src: Sources) -> list[Finding]:
    phrases = list(rule.get("phrases") or [])
    spec = rule.get("phrases_from")
    if spec:
        text = src._read(spec["path"]) or ""
        m = re.search(rf"{spec.get('variable', 'FORBIDDEN')}\s*=\s*\[(.*?)\]", text, re.DOTALL)
        found = re.findall(r'"([^"]+)"', m.group(1)) if m else []
        phrases = found or phrases
    out = []
    major = set(rule.get("major_in", []))
    for name in rule["in"]:
        text = src.text(name)
        low = text.lower()
        collapsed = re.sub(r"\s+", " ", low)
        for phrase in phrases:
            ph = re.sub(r"\s+", " ", phrase.lower())
            start = 0
            while True:
                k = collapsed.find(ph, start)
                if k == -1:
                    break
                approx = int(k / max(1, len(collapsed)) * len(low))
                orig = low.find(ph.split()[0], max(0, approx - 400))
                line = line_of(text, orig) if orig != -1 else -1
                split = orig != -1 and "\n" in text[orig: orig + len(phrase) + 5]
                case = {"severity": "MAJOR" if name in major else rule.get("severity", "MINOR")}
                f = _finding(rule, case, f"{src.label(name)}:{line}", phrase=phrase)
                if split:
                    f.observed += rule.get("split_note", " (split across a line break)")
                out.append(f)
                start = k + 1
    return out


def pattern(rule: dict, src: Sources) -> list[Finding]:
    out = []
    for label, text in _texts(rule, src):
        for m in re.finditer(rule["pattern"], text):
            after = text[m.start(): m.end() + rule.get("after_chars", 0)].strip()
            out.append(_finding(rule, None, f"{label}:{line_of(text, m.start())}", match=m.group(0),
                                match_plus=after))
    return out


def near(rule: dict, src: Sources) -> list[Finding]:
    out = []
    for label, text in _texts(rule, src):
        for m in re.finditer(rule["pattern"], text):
            skip = rule.get("skip_if_before")
            if skip and re.search(skip, text[max(0, m.start() - rule.get("skip_chars", 3)): m.start()]):
                continue
            w = _window(text, m.start(), m.end(), rule.get("window", 200))
            wl = w.lower() if rule.get("lower", True) else w
            for case in rule.get("cases") or [rule]:
                if case.get("missing") and _any(case["missing"], wl):
                    continue
                if case.get("present") and not _any(case["present"], wl):
                    continue
                sev = case.get("severity", rule.get("severity", "MINOR"))
                for sect, s in (case.get("severity_in_section") or {}).items():
                    if sect in _section_of(text, m.start()).lower():
                        sev = s
                f = _finding(rule, {**case, "severity": sev}, f"{label}:{line_of(text, m.start())}", match=m.group(0))
                out.append(f)
                if not rule.get("all_cases"):
                    break
    return out


def requirements(rule: dict, src: Sources) -> list[Finding]:
    out = []
    for value in rule.get("values", [None]):
        rx = rule["pattern"].replace("{value}", re.escape(str(value))) if value is not None else rule["pattern"]
        for label, text in _texts(rule, src):
            for m in re.finditer(rx, text):
                wl = _window(text, m.start(), m.end(), rule.get("window", 200)).lower()
                missing = [r["name"] for r in rule["require"]
                           if (not r.get("only_values") or str(value) in map(str, r["only_values"]))
                           and not re.search(r["any"], wl)]
                if missing:
                    out.append(_finding(rule, None, f"{label}:{line_of(text, m.start())}",
                                        match=m.group(0).strip(), missing=", ".join(missing), value=value))
    return out


def co_occurrence(rule: dict, src: Sources) -> list[Finding]:
    if all(c["contains"] in src.text(c["in"]) for c in rule["all"]):
        return [_finding(rule, None, rule["location"])]
    return []


def variants(rule: dict, src: Sources) -> list[Finding]:
    found: set[str] = set()
    for name in rule["in"]:
        found.update(re.findall(rule["pattern"], src.text(name)))
    if len(found) > rule.get("more_than", 1):
        return [_finding(rule, None, rule["location"], variants=sorted(found))]
    return []


def absent(rule: dict, src: Sources) -> list[Finding]:
    text = src.text(rule["in"])
    if not any(s in text for s in rule["none_of"]):
        return [_finding(rule, None, rule.get("location") or src.label(rule["in"]))]
    return []


def _col(row: dict, *parts: str) -> float | None:
    for k in row:
        if all(p in k.lower() for p in parts):
            try:
                return float(str(row[k]).replace(",", "."))
            except (TypeError, ValueError):
                return None
    return None


def _row_matches(row: dict, filters: list[dict]) -> bool:
    for f in filters:
        cols = f["columns"] if isinstance(f.get("columns"), list) else [f["column"]]
        value = next((row.get(c) for c in cols if row.get(c) not in (None, "")), "")
        if f["contains"].lower() not in str(value).lower():
            return False
    return True


def table_interval(rule: dict, src: Sources) -> list[Finding]:
    rows = src.rows(rule["table"])
    row = next((r for r in rows if _row_matches(r, rule["row"])), None)
    if row is None:
        return [_finding(rule, rule["missing_row"], rule.get("missing_location", src.label(rule["table"])))]
    out = []
    stats = rule.get("stats", {"central": "central", "p05": "p05", "p50": "p50", "p95": "p95"})
    text_name = rule.get("quoted_in")
    text = src.text(text_name) if text_name else ""
    for q in rule["quantities"]:
        v = {k: _col(row, *q["columns"], col) for k, col in stats.items()}
        if None in v.values():
            continue
        values = {**v, "name": q["name"], "unit": q["unit"]}
        if v["central"] < v["p05"] or v["central"] > v["p95"]:
            out.append(_finding(rule, rule["outside"], rule["location"], **values))
        if not text_name:
            continue
        quote = rule["quote"]
        for m in re.finditer(rf"\b{int(round(v['central']))}\s*{re.escape(q['unit'])}", text):
            w = _window(text, m.start(), m.end(), quote.get("window", 300))
            has_int = _any(quote["interval"], w)
            has_med = str(int(round(v["p50"]))) in w or _any(quote["median"], w.lower())
            if has_int and not has_med:
                out.append(_finding(rule, {**quote, "severity": q.get("quote_severity", quote.get("severity", "MINOR"))},
                                    f"{src.label(text_name)}:{line_of(text, m.start())}", match=m.group(0), **values))
    return out


def table_rows(rule: dict, src: Sources) -> list[Finding]:
    return [_finding(rule, None, rule.get("location") or src.label(rule["table"]), row=r)
            for r in src.rows(rule["table"])
            if _row_matches(r, rule.get("where", [])) and not str(r.get(rule["required"], "")).strip()]


def table_text(rule: dict, src: Sources) -> list[Finding]:
    rows = src.rows(rule["table"])
    rows = rows[:1] if rule.get("rows", "all") == "first" else rows
    joined = " ".join(str(r.get(rule["column"], "")) for r in rows)
    return [_finding(rule, None, rule["location"])] if rule["contains"] in joined else []


RULE_TYPES = {f.__name__: f for f in (forbidden, pattern, near, requirements, co_occurrence, variants, absent,
                                      table_interval, table_rows, table_text)}


def run(doc: dict, read: Callable[[str], str | None]) -> list[Finding]:
    if doc.get("schema") != "ghai.review_rules/v1":
        raise ValueError("review rules must declare schema: ghai.review_rules/v1")
    src = Sources(doc, read)
    findings: list[Finding] = []
    for rule in doc.get("rules", []):
        kind = rule.get("type")
        if kind not in RULE_TYPES:
            raise ValueError(f"rule {rule.get('id')}: unknown type {kind!r}; one of {sorted(RULE_TYPES)}")
        findings += RULE_TYPES[kind](rule, src)
    findings.sort(key=lambda f: (SEVERITY_ORDER.get(f.severity, 9), f.check_id, f.location))
    return findings


def as_dicts(findings: list[Finding]) -> list[dict]:
    return [asdict(f) for f in findings]


def render(findings: list[Finding], title: str = "Deterministic checks") -> str:
    esc = lambda s: str(s).replace("|", "\\|").replace("\n", " ")
    out = [f"## {title}", "", "| # | check | severity | location | observed | expected | suggested fix |",
           "|---|---|---|---|---|---|---|"]
    for i, f in enumerate(findings, 1):
        out.append(f"| {i} | {f.check_id} | {f.severity} | `{esc(f.location)}` | {esc(f.observed)} | "
                   f"{esc(f.expected)} | {esc(f.suggested_fix)} |")
    return "\n".join(out) + "\n"
