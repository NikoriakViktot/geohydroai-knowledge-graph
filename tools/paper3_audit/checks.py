"""Deterministic manuscript checks A–H of the brief (§10). No LLM.

Every finding names its location, what was observed, what the terminology freeze or
the tables require, and a suggested fix; severities are CRITICAL / MAJOR / MINOR / INFO.
The whitespace-collapsing forbidden-phrase scan catches phrases split across a line
break, which the bundle's own test (line-based) misses.
"""
from __future__ import annotations

import csv
import json
import logging
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from tools.paper3_audit.bibtex import forbidden_phrases_from_test
from tools.paper3_audit.config import PUB_DIR, WORK_DIR

logger = logging.getLogger(__name__)

FINDINGS_JSON = "checks_findings.json"
FINDINGS_MD = "checks_findings.md"

FALLBACK_FORBIDDEN = ["physical reconstruction", "false SAR water", "false radar water",
                      "peak breach discharge", "breach discharge was", "breach discharge of",
                      "peak breach outflow", "implied breach outflow", "passed to the liman",
                      "went to the liman", "without loss of recall", "without a detectable loss",
                      "without a detectable recall loss", "daily observed", "flooded area = "]


@dataclass
class Finding:
    check_id: str
    severity: str            # CRITICAL | MAJOR | MINOR | INFO
    location: str            # file:line
    observed: str
    expected: str
    suggested_fix: str
    tags: str = ""


def _read(name: str) -> tuple[str, list[str]]:
    p = PUB_DIR / name
    text = p.read_text(encoding="utf-8") if p.exists() else ""
    return text, text.splitlines()


def _line_of(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1


def _window(text: str, pos: int, width: int = 250) -> str:
    return text[max(0, pos - width): pos + width]


def _table_rows(name: str) -> list[dict]:
    p = PUB_DIR / "tables" / name
    if not p.exists():
        return []
    with p.open(encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _num(v) -> float | None:
    try:
        return float(str(v).replace(",", "."))
    except (TypeError, ValueError):
        return None


# ── A: deterministic vs Monte-Carlo ───────────────────────────────────────────

def check_A(findings: list[Finding], man: str) -> None:
    rows = _table_rows("T12.csv")
    row = next((r for r in rows if "2023-06-07" in str(r.get("date", r.get("day", "")))
                and "CORRIDOR" in str(r.get("region", "")).upper()), None)
    if row is None:
        findings.append(Finding("A", "INFO", "tables/T12.csv", "no DNIPRO_CORRIDOR 2023-06-07 row found",
                                "the row that carries the headline values", "check T12 column names"))
        return
    cols = {k.lower(): k for k in row}

    def get(*pats):
        for k in cols:
            if all(p in k for p in pats):
                return _num(row[cols[k]])
        return None
    for qty, unit, pats in (("A_new", "km²", ("a_",)), ("W_total", "km²", ("w_total",)), ("V_new", "hm³", ("v_",))):
        det = get(*pats, "central")
        p05, p50, p95 = get(*pats, "p05"), get(*pats, "p50"), get(*pats, "p95")
        if None in (det, p05, p50, p95):
            continue
        outside = det < p05 or det > p95
        tag = f"{qty}: deterministic {det:.0f} vs MC p05/p50/p95 {p05:.0f}/{p50:.0f}/{p95:.0f} {unit}"
        if outside:
            findings.append(Finding(
                "A", "MAJOR", "tables/T12.csv (2023-06-07)", tag + " — the nominal run lies OUTSIDE its own interval",
                "TERMINOLOGY.md: intervals 'reported next to the central value rather than centred on it'; "
                "C07 explains the displacement (correlated DEM noise adds depth and connections)",
                "Report 'MC median [p05–p95]' as the uncertainty-based estimate and name the deterministic "
                "value separately as the nominal run; wherever the nominal value is quoted with the interval, "
                "state the MC median and that the interval is not centred on the nominal run (reviewer note).",
                tags="reporting"))
        # every quoted nominal value must carry p05–p95 AND the median or the 'not centred' statement
        for m in re.finditer(rf"\b{int(round(det))}\s*{re.escape(unit)}", man):
            w = _window(man, m.start(), 300)
            has_int = ("p05" in w) or ("p05–p95" in w) or ("p05-p95" in w)
            has_med = (str(int(round(p50))) in w) or ("median" in w.lower()) or ("not centred" in w.lower()) or ("displaced" in w.lower())
            if has_int and not has_med:
                findings.append(Finding(
                    "A", "MAJOR" if qty == "A_new" else "MINOR", f"manuscript.md:{_line_of(man, m.start())}",
                    f"'{m.group(0)}' quoted with its p05–p95 but without the MC median ({p50:.0f}) or the "
                    f"statement that the interval is not centred on it",
                    "TERMINOLOGY.md L54 / C07: value + p05–p95 + MC median", 
                    f"'{det:.0f} {unit} (nominal run; MC median {p50:.0f}, p05–p95 {p05:.0f}–{p95:.0f} {unit})'",
                    tags="reporting"))


# ── B: volume rule ────────────────────────────────────────────────────────────

def check_B(findings: list[Finding], man: str, cap: str) -> None:
    for name, text in (("manuscript.md", man), ("captions.md", cap)):
        for m in re.finditer(r"\b(\d{2,4})\s*hm³", text):
            if re.search(r"[–-]\s*$", text[max(0, m.start() - 3): m.start()]):
                continue  # the upper bound of an interval, not a separate volume
            w = _window(text, m.start(), 220)
            if "p05" not in w and "interval" not in w.lower() and "pool" not in w.lower():
                findings.append(Finding("B", "MINOR", f"{name}:{_line_of(text, m.start())}",
                                        f"volume '{m.group(0)}' without any p05–p95",
                                        "TERMINOLOGY.md L54: volumes always with p05–p95 and MC median",
                                        "add the T12/T21 interval or state explicitly that the quantity is outside the MC budget",
                                        tags="volume"))
            elif "p05" in w and "median" not in w.lower():
                sev = "MAJOR" if "abstract" in _section_of(text, m.start()).lower() else "MINOR"
                findings.append(Finding("B", sev, f"{name}:{_line_of(text, m.start())}",
                                        f"volume '{m.group(0)}' with p05–p95 but without the MC median",
                                        "TERMINOLOGY.md L54: volumes always with p05–p95 AND MC median",
                                        "add 'Monte-Carlo median 566 hm³' (T12) next to the interval", tags="volume"))


def _section_of(text: str, pos: int) -> str:
    heads = [(m.start(), m.group(0)) for m in re.finditer(r"^#+ .*$", text[:pos], re.MULTILINE)]
    return heads[-1][1] if heads else ""


# ── C: forbidden phrases (whitespace-tolerant) ────────────────────────────────

def check_C(findings: list[Finding], files: dict[str, str], phrases: list[str]) -> None:
    for name, text in files.items():
        low = text.lower()
        collapsed = re.sub(r"\s+", " ", low)
        # map collapsed positions back to original positions
        pos_map, j = [], 0
        for i, ch in enumerate(low):
            if ch.isspace():
                if j < len(collapsed) and collapsed[j] == " " and (not pos_map or pos_map[-1] != i - 1 or low[i - 1] != " "):
                    pass
        # simpler: search collapsed, then locate the first word in the original near the same fraction
        for phrase in phrases:
            ph = re.sub(r"\s+", " ", phrase.lower())
            start = 0
            while True:
                k = collapsed.find(ph, start)
                if k == -1:
                    break
                first_word = ph.split()[0]
                approx = int(k / max(1, len(collapsed)) * len(low))
                seg_start = max(0, approx - 400)
                orig = low.find(first_word, seg_start)
                line = _line_of(text, orig) if orig != -1 else -1
                split = "\n" in text[orig: orig + len(phrase) + 5] if orig != -1 else False
                sev = "MAJOR" if name in ("manuscript.md", "captions.md", "claims.md") else "MINOR"
                findings.append(Finding("C", sev, f"{name}:{line}",
                                        f"forbidden phrase '{phrase}'" + (" (split across a line break — the bundle's test misses it)" if split else ""),
                                        "TERMINOLOGY.md forbidden list",
                                        "replace with the frozen term (e.g. 'S1-only detections topographically unsupported by the reconstructed connected water surface')",
                                        tags="terminology"))
                start = k + 1


# ── D: causal attribution of S1-only detections ───────────────────────────────

def check_D(findings: list[Finding], man: str) -> None:
    for m in re.finditer(r"dark-water rule, not the flood", man):
        findings.append(Finding("D", "MAJOR", f"manuscript.md:{_line_of(man, m.start())}",
                                f"categorical attribution: '{m.group(0)}'",
                                "brief CHECK D: no categorical cause unless event-specific evidence establishes it",
                                "'…is not supported as connected breach-induced inundation by the available terrain/WSE "
                                "constraints and is consistent with known C-band look-alike behaviour on wet fields and sand; "
                                "whether it is non-water is not tested here'", tags="attribution"))


# ── E: reconstructed maximum wording ──────────────────────────────────────────

def check_E(findings: list[Finding], man: str) -> None:
    for m in re.finditer(r"7 June", man):
        w = _window(man, m.start(), 160).lower()
        if re.search(r"maximum|peak", w) and not re.search(r"reconstruct|between the sentinel|between them|no scene|not an observation", w):
            findings.append(Finding("E", "MAJOR", f"manuscript.md:{_line_of(man, m.start())}",
                                    "7 June maximum mentioned without 'reconstructed' / 'between acquisitions' in the sentence",
                                    "brief CHECK E: the 7 June maximum is a property of the reconstructed series",
                                    "write 'the daily reconstruction places the reconstructed areal maximum on 7 June'", tags="wording"))
        if re.search(r"observed (on|the) 7 june|peaked on 7 june|flood extent peaked", w):
            findings.append(Finding("E", "CRITICAL", f"manuscript.md:{_line_of(man, m.start())}",
                                    "7 June maximum stated as observed", "never observed: no S1 scene covers it",
                                    "rephrase as reconstructed", tags="wording"))
    for m in re.finditer(r"\bthe peak\b(?!\s+(stage|stages|day|date|dates|flood|discharge|outflow|breach|storage|floodplain))", man):
        findings.append(Finding("E", "MINOR", f"manuscript.md:{_line_of(man, m.start())}",
                                f"bare noun '{man[m.start():m.end()+18].strip()}…'",
                                "TERMINOLOGY.md L12: never 'peak' without a noun (areal maximum ≠ peak stage)",
                                "'the reconstructed areal maximum' / 'the peak stage'", tags="wording"))


# ── F: ICESat-2 wording ───────────────────────────────────────────────────────

def check_F(findings: list[Finding], man: str, claims_md: str, cap: str) -> None:
    for m in re.finditer(r"ICESat-2", man):
        w = _window(man, m.start(), 150).lower()
        if re.search(r"\bvalidat|\bprove|\bproof", w) and not re.search(r"not a validation|does not validate|without proving|no evidence|not validation|consistency check", w):
            findings.append(Finding("F", "MAJOR", f"manuscript.md:{_line_of(man, m.start())}",
                                    "ICESat-2 within 150 chars of 'validat/prove'",
                                    "TERMINOLOGY.md: 'altimetric consistency check'; never proves / validates the map",
                                    "if this refers to Paper 2's DEM validation, say 'Paper 2 assessed the DEM against night ICESat-2'; "
                                    "for this paper keep 'altimetric consistency check'", tags="wording"))
    if "few decimetres" in man and "few centimetres" in claims_md:
        findings.append(Finding("F", "MINOR", "manuscript.md (Abstract) vs claims.md C06",
                                "Abstract/Fig08: 'within a few decimetres'; C06: 'to a few centimetres (median)'",
                                "one consistent statement: median +0.02/+0.03 m, p10–p90 −0.31…+0.64 m (T15)",
                                "'median agreement of a few centimetres with a p10–p90 spread of a few decimetres'", tags="consistency"))


# ── G: reservoir balance ──────────────────────────────────────────────────────

def check_G(findings: list[Finding], man: str, claims_md: str, cap: str, tables_readme: str) -> None:
    for m in re.finditer(r"40\s?057|4\s?[x×]\s?10[⁴4]|4e4", man + "\n" + claims_md):
        text = man + "\n" + claims_md
        w = _window(text, m.start(), 300).lower()
        if "effective release" not in w:
            findings.append(Finding("G", "MAJOR", f"(manuscript|claims):{_line_of(text, m.start())}",
                                    "the ~4×10⁴ m³/s figure without 'daily-mean effective release' nearby",
                                    "TERMINOLOGY.md L60", "name the quantity 'daily-mean effective release (−dV/dt + Q_in)'", tags="c14"))
    for m in re.finditer(r"breach discharge|breach outflow|breach flow", man):
        w = man[max(0, m.start() - 60): m.end()].lower()
        if not re.search(r"not an instantaneous|instantaneous|initial|different physical|published", w):
            findings.append(Finding("G", "MAJOR", f"manuscript.md:{_line_of(man, m.start())}",
                                    f"'{m.group(0)}' used affirmatively", "only in negation / as a comparator's quantity",
                                    "keep 'daily-mean effective release'", tags="c14"))
    gaps = set()
    for text in (man, claims_md, cap, tables_readme):
        gaps.update(re.findall(r"[-−–]?\s?(?:1[0-9]|[5-9]|20)\s?[–-]\s?(?:1[0-9]|20|25)\s?%|[-−–]\s?(?:9|14|20)\s?%", text))
    if len(gaps) > 3:
        findings.append(Finding("G", "MINOR", "manuscript §4.1 / C14 / FigS07 caption / tables/README T22",
                                f"hypsometry gap stated in several forms: {sorted(gaps)}",
                                "one canonical statement of the gap per level from T22",
                                "quote T22 values once (−9 % at 17.5 m, −14 % at 13 m, −20 % at 11 m) and reuse them", tags="consistency"))


# ── H: operational areas ──────────────────────────────────────────────────────

def check_H(findings: list[Finding], man: str) -> None:
    for value in ("620", "180", "410", "420", "823", "520"):
        for m in re.finditer(rf"[~≈]?\s?{value}\s*km²", man):
            w = _window(man, m.start(), 320).lower()
            missing = []
            if not re.search(r"cumulative|snapshot|persisten|scenario|on \d{1,2} june|over \d", w):
                missing.append("temporal semantics")
            if not re.search(r"flooded \*?land|flooded land|land|model_extent|scenario|extent", w):
                missing.append("land-vs-total definition")
            if value in ("620", "180") and not re.search(r"reference water|pre-existing water|reference", w):
                missing.append("reference-water treatment")
            if missing:
                findings.append(Finding("H", "MINOR", f"manuscript.md:{_line_of(man, m.start())}",
                                        f"external figure '{m.group(0).strip()}' without: {', '.join(missing)}",
                                        "brief CHECK H: AOI, dates, snapshot/cumulative, reference water, sensor, land-vs-total",
                                        "attach the T16 semantics in the sentence", tags="comparator"))
    t16 = _table_rows("T16.csv")
    for r in t16:
        if "literature" in str(r.get("area_semantics", "")).lower() and not str(r.get("verify", "")).strip():
            findings.append(Finding("H", "MINOR", "tables/T16.csv", f"literature row '{r.get('quantity','')}' without a verify note",
                                    "every literature row carries its verification state", "fill `verify`", tags="comparator"))
    t12 = _table_rows("T12.csv")
    note = " ".join(str(r.get("definition_note", "")) for r in t12[:1])
    if "comparable with operational" in note:
        findings.append(Finding("H", "MAJOR", "tables/T12.csv definition_note",
                                "W_total described as 'the quantity comparable with operational flooded area products'",
                                "TERMINOLOGY.md L26–27 and T16: operational flooded LAND is closer in kind to A_new, never validation",
                                "regenerate T12 note in floodstate-eo (p96_paper_tables.py) — table not edited here", tags="consistency"))
    if "410" not in man and "420" not in man:
        findings.append(Finding("H", "INFO", "manuscript.md", "NASA Harvest 410–420 km² (TH-INT-02) and Monti 2024 are not in the manuscript or T16",
                                "brief §9A: every published area with its quantity", "add as literature_reported rows only if the source can be verified", tags="comparator"))


# ── runner ────────────────────────────────────────────────────────────────────

def run(work_dir: Path = WORK_DIR) -> int:
    man, _ = _read("manuscript.md")
    cap, _ = _read("captions.md")
    claims_md, _ = _read("claims.md")
    term, _ = _read("TERMINOLOGY.md")
    tables_readme, _ = _read("tables/README.md")
    theses_md, _ = _read("literature/theses.md")
    ev, _ = _read("evidence_matrix.csv")
    phrases = forbidden_phrases_from_test(work_dir) or FALLBACK_FORBIDDEN
    findings: list[Finding] = []
    check_A(findings, man)
    check_B(findings, man, cap)
    check_C(findings, {"manuscript.md": man, "captions.md": cap, "claims.md": claims_md,
                       "tables/README.md": tables_readme, "evidence_matrix.csv": ev,
                       "literature/theses.md": theses_md}, phrases)
    check_D(findings, man)
    check_E(findings, man)
    check_F(findings, man, claims_md, cap)
    check_G(findings, man, claims_md, cap, tables_readme)
    check_H(findings, man)
    order = {"CRITICAL": 0, "MAJOR": 1, "MINOR": 2, "INFO": 3}
    findings.sort(key=lambda f: (order.get(f.severity, 9), f.check_id, f.location))
    work_dir.mkdir(parents=True, exist_ok=True)
    (work_dir / FINDINGS_JSON).write_text(json.dumps([asdict(f) for f in findings], indent=1, ensure_ascii=False), encoding="utf-8")
    (work_dir / FINDINGS_MD).write_text(render(findings, phrases), encoding="utf-8")
    counts = {s: sum(1 for f in findings if f.severity == s) for s in order}
    logger.info("checks: %s (forbidden list: %d phrases from %s)", counts, len(phrases),
                "floodstate test" if forbidden_phrases_from_test(work_dir) else "fallback")
    return 0


def render(findings: list[Finding], phrases: list[str]) -> str:
    out = ["## Deterministic checks A–H (tools/paper3_audit/checks.py)", "",
           f"Forbidden-phrase list: {len(phrases)} phrases (from `tests/test_terminology_freeze.py` of floodstate-eo, "
           "scanned with whitespace collapsed).", "",
           "| # | check | severity | location | observed | expected | suggested fix |", "|---|---|---|---|---|---|---|"]
    for i, f in enumerate(findings, 1):
        esc = lambda s: str(s).replace("|", "\\|").replace("\n", " ")
        out.append(f"| {i} | {f.check_id} | {f.severity} | `{esc(f.location)}` | {esc(f.observed)} | {esc(f.expected)} | {esc(f.suggested_fix)} |")
    return "\n".join(out) + "\n"
