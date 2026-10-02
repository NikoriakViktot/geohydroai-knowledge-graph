"""Manuscript assembly, dialect ``ghai`` (from src/paper_3/v2/assemble.py, generalised).

Section templates are ordinary Markdown with placeholders. Nothing numeric is typed in a template:
every number reaches the page through ``{{claim:…}}`` from the paper's OWN_EVIDENCE.csv, every
citation through ``{{cite:…}}``, and what cannot be supported renders as a ``[PENDING …]`` marker
that the final gate refuses.

    {{claim:M1.1}}  value_resolved      .n n_resolved · .unc uncertainty_resolved · .text · .caveat
    {{cite:key}} {{cite:a,b}}           (Family et al., 2024) — unresolved key -> marker
    {{section:NAME}}                    a section file declared in the manifest (assembly.sections)
    {{table:T3}}                        a claim table (assembly.table_specs) or tables/T3.csv
    {{figure:F16}}                      the image and its caption (paths.figures, paths.captions)
    {{ref:table:T3}} {{ref:figure:F16}} "Table n" / "Figure n", following the numbering
    {{pending:FIG08|title|backs=S1.1|section=6.9|produces=…}}   an explicit marker

Tables and figures are numbered by first appearance (titles typed as "Table 5." are renumbered),
so the printed order is always 1, 2, 3 …; references typed by hand ("Table 5") are reported.
Paragraph rule (unchanged): a paragraph that uses a claim whose audit_status is not RENDERABLE is
replaced whole by a marker; a LIMITATION_ONLY claim may appear only as a limitation.
"""

from __future__ import annotations

import csv
import hashlib
import io
import re
from dataclasses import dataclass, field
from typing import Callable

import pandas as pd

from src.workbench.assemble import markers

RENDERABLE = {"SUPPORTED", "SUPPORTED_WITH_LIMITATION", "REVISE_UNCERTAINTY", "NOT_TESTABLE"}
LIMITATION_ONLY = {"REVISE_UNCERTAINTY", "NOT_TESTABLE"}
PH = re.compile(r"\{\{(claim|cite|section|table|figure|ref|pending):([^}]+)\}\}")
#: Priority wording is a human judgement made after reading the precedents; the assembler never
#: writes it (R-SCI-3). "first season" is fine; "first to" is not.
FORBIDDEN = re.compile(r"\b(novel|unprecedented|unique|pioneering|first to|for the first time|"
                       r"the first (study|paper|analysis|attempt|report))\b", re.I)
_CLAIM_SPEC = re.compile(r"^(?P<cid>.+?)(?:\.(?P<field>n|unc|text|caveat))?$")
_TITLE_NUMBER = re.compile(r"^(Table|Figure)\s+[A-Za-z0-9-]+\.\s*")
_HAND_REF = re.compile(r"\b(Table|Figure|Fig\.)\s+(\d+)\b")

#: Cyrillic family names in citations (the Paper 1 registry's transliteration, kept exact).
_CYR = str.maketrans({
    "а": "a", "б": "b", "в": "v", "г": "h", "ґ": "g", "д": "d", "е": "e", "є": "ie",
    "ж": "zh", "з": "z", "и": "y", "і": "i", "ї": "i", "й": "i", "к": "k", "л": "l",
    "м": "m", "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "shch", "ь": "",
    "ю": "iu", "я": "ia", "ы": "y", "э": "e", "ё": "e", "ъ": "",
})
_AUTHOR_SPLIT = re.compile(r",\s*(?=[A-ZА-ЯІЇЄҐ][^\s,;]+\s+[A-ZА-ЯІЇЄҐ]\.)")


class AssemblyError(RuntimeError):
    pass


@dataclass
class Inputs:
    evidence: pd.DataFrame | None = None          # OWN_EVIDENCE, indexed by claim_id
    references: pd.DataFrame | None = None        # legacy REFERENCES.csv, indexed by cite_key
    bib: dict[str, dict] | None = None            # .bib entries by key: {"fields": {...}}
    sections: dict[str, str] = field(default_factory=dict)
    table_specs: dict[str, dict] = field(default_factory=dict)
    table_csv: Callable[[str], str | None] = lambda tid: None
    captions: dict[str, str] = field(default_factory=dict)
    figures: dict[str, str] = field(default_factory=dict)   # figure id -> image path for the markdown


def split_authors(raw: str) -> list[str]:
    """'Arcement G.J., Schneider V.R.' / 'A & B' / 'Family, Given; …' -> one string per author."""
    if not raw:
        return []
    text = raw.replace(" & ", "; ").replace(" and ", "; ")
    if ";" in text:
        return [c.strip() for c in text.split(";") if c.strip()]
    head = text.split(",", 1)[0].strip()
    if "," in text and " " not in head:
        return [text.strip()]
    return [p.strip() for p in _AUTHOR_SPLIT.split(text) if p.strip()]


def _latin(name: str) -> str:
    if re.search(r"[А-Яа-яІіЇїЄєҐґ]", name):
        return name.lower().translate(_CYR).capitalize()
    return name


def _head(families: list[str]) -> str:
    if len(families) == 1:
        return families[0]
    if len(families) == 2:
        return f"{families[0]} & {families[1]}"
    return f"{families[0]} et al."


def _num(seed: str) -> int:
    """Stable 2-digit marker number from a seed (the marker head takes a prefix + two digits)."""
    return 10 + int(hashlib.sha1(seed.encode()).hexdigest()[:6], 16) % 90


def _marker(mid: str, title: str, **fields) -> str:
    f = {"status": "blocked", "blocks_submission": "yes"}
    f.update({k: v for k, v in fields.items() if v})
    return markers.render(markers.Marker(id=mid, title=title, fields=f))


def split_claim_spec(spec: str) -> tuple[str, str]:
    m = _CLAIM_SPEC.match(spec.strip())
    return m.group("cid"), m.group("field") or ""


class Assembler:
    def __init__(self, inputs: Inputs, *, number_tables: bool = True, number_figures: bool = True,
                 reference_source: str = "REFERENCES.csv"):
        self.i = inputs
        self.number_tables, self.number_figures = number_tables, number_figures
        self.reference_source = reference_source
        self.used: set[str] = set()
        self.cited: set[str] = set()
        self.warnings: list[str] = []
        self.numbers: dict[tuple[str, str], int] = {}

    # ── numbering ──────────────────────────────────────────────────────────────
    def prescan(self, texts: list[tuple[str, str]]) -> None:
        """Number tables and figures by their first appearance across the templates, in order."""
        count = {"table": 0, "figure": 0}
        for _, text in texts:
            for m in PH.finditer(text):
                if m.group(1) in ("table", "figure"):
                    key = (m.group(1), m.group(2).strip())
                    if key not in self.numbers:
                        count[m.group(1)] += 1
                        self.numbers[key] = count[m.group(1)]
        for name, text in texts:
            for m in _HAND_REF.finditer(text):
                kind = "table" if m.group(1) == "Table" else "figure"
                if (kind == "table" and self.number_tables) or (kind == "figure" and self.number_figures
                                                                and any(k == "figure" for k, _ in self.numbers)):
                    line = text.count("\n", 0, m.start()) + 1
                    self.warnings.append(f"{name}:{line}: '{m.group(0)}' typed by hand; write "
                                         f"{{{{ref:{kind}:<id>}}}} so the number follows the {kind}")

    def number(self, kind: str, ident: str) -> int | None:
        return self.numbers.get((kind, ident))

    # ── placeholders ───────────────────────────────────────────────────────────
    def claim(self, spec: str) -> str:
        ev = self.i.evidence
        cid, fld = split_claim_spec(spec)
        if ev is None or cid not in ev.index:
            raise AssemblyError(f"unknown claim id {cid!r}")
        self.used.add(cid)
        r = ev.loc[cid]
        col = {"": "value_resolved", "n": "n_resolved", "unc": "uncertainty_resolved", "text": "claim_text",
               "caveat": "scientific_caveat"}[fld]
        out = str(r[col]).strip()
        if not out and fld == "":
            out = str(r["claim_text"]).strip()       # a statement stands in for the value it does not have
        return out

    def cite(self, key: str) -> str:
        if self.i.references is not None:
            refs = self.i.references
            if key not in refs.index:
                return "\n\n" + _marker(f"REF{_num(key)}", f"citation key {key!r} not in {self.reference_source}") + "\n\n"
            r = refs.loc[key]
            if r["resolved"] != "True":
                return "\n\n" + _marker(f"REF{_num(key)}", f"unresolved reference {key}", stub=r["note"][:120],
                                        blocks_submission="yes") + "\n\n"
            self.cited.add(key)
            return f"({self._author_year_csv(r)})"
        entry = (self.i.bib or {}).get(key)
        if entry is None:
            return "\n\n" + _marker(f"REF{_num(key)}", f"citation key {key!r} not in the .bib") + "\n\n"
        self.cited.add(key)
        return f"({self._author_year_bib(entry)})"

    @staticmethod
    def _author_year_csv(row: pd.Series) -> str:
        verbatim = str(row.get("cite_as", "") or "").strip()
        if verbatim:                                   # corporate author, cited as the entry says
            return verbatim
        fams = [_latin((a.split(",")[0] if "," in a else a).split()[0]) for a in split_authors(row["authors"])]
        if not fams:
            return str(row.get("cite_key", "") or getattr(row, "name", "") or "")
        return f"{_head(fams)}, {row['year']}" if row["year"] else _head(fams)

    @staticmethod
    def _author_year_bib(entry: dict) -> str:
        from src.services.bibtex import parse_authors
        f = entry.get("fields", {})
        people, _ = parse_authors(f.get("author") or f.get("editor"))
        fams = [_latin(fam) for fam, _given, _corp in people if fam]
        year = str(f.get("year", "")).strip()
        if not fams:
            return f"{entry.get('key', '')}, {year}" if year else entry.get("key", "")
        return f"{_head(fams)}, {year}" if year else _head(fams)

    def table(self, tid: str) -> str:
        spec = self.i.table_specs.get(tid)
        n = self.number("table", tid) if self.number_tables else None
        if spec:
            ev = self.i.evidence
            cols = spec["columns"]
            rows = [(label, cid) for label, cid in spec["rows"] if ev is not None and cid in ev.index]
            self.used.update(cid for _, cid in rows)
            body = []
            for label, cid in rows:
                r = ev.loc[cid]
                cells = []
                for _, fld in cols:
                    v = label if fld == "__label__" else str(r.get(fld, ""))
                    cells.append(v.replace("|", "/").replace("\n", " ").strip()[:200] or "—")
                body.append("| " + " | ".join(cells) + " |")
            title = spec["title"]
            if n is not None:
                title = _TITLE_NUMBER.sub(f"Table {n}. ", title) if _TITLE_NUMBER.match(title) else f"Table {n}. {title}"
            out = [f"**{title}**", "", "| " + " | ".join(h for h, _ in cols) + " |", "|" + "---|" * len(cols), *body, ""]
            if spec.get("note"):
                out += [f"*{spec['note']}*", ""]
            return "\n".join(out)
        text = self.i.table_csv(tid)
        if text is None:
            return "\n\n" + _marker(f"TAB{_num(tid)}", f"table {tid}: no spec and no tables/{tid}.csv",
                                    section="", produces=f"tables/{tid}.csv") + "\n\n"
        rows = list(csv.reader(io.StringIO(text)))
        caption = self.i.captions.get(tid, "")
        head = f"**Table {n}.** {caption}".rstrip() if n is not None else (f"**{tid}.** {caption}".rstrip())
        out = [head, "", "| " + " | ".join(rows[0]) + " |", "|" + "---|" * len(rows[0])]
        out += ["| " + " | ".join(c.replace("|", "/") for c in r) + " |" for r in rows[1:]]
        return "\n".join(out) + "\n"

    def figure(self, fid: str) -> str:
        n = self.number("figure", fid) if self.number_figures else None
        path, caption = self.i.figures.get(fid), self.i.captions.get(fid)
        if not path or caption is None:
            missing = " and ".join(x for x, ok in (("image", path), ("caption", caption is not None)) if not ok)
            return "\n\n" + _marker(f"FIG{_num(fid)}", f"figure {fid}: no {missing}", section="",
                                    produces=f"figures/{fid}") + "\n\n"
        label = f"Figure {n}" if n is not None else fid
        return f"![{label}]({path})\n\n**{label}.** {caption}"

    def ref(self, spec: str) -> str:
        kind, _, ident = spec.partition(":")
        n = self.number(kind.strip(), ident.strip())
        if n is None:
            self.warnings.append(f"{{{{ref:{spec}}}}}: no such {kind} placeholder in the templates")
            return f"{kind.strip().capitalize()} ??"
        return f"{kind.strip().capitalize()} {n}"

    @staticmethod
    def pending(spec: str) -> str:
        parts = spec.split("|")
        mid, title = parts[0].strip(), parts[1].strip() if len(parts) > 1 else "pending"
        fields = dict(p.split("=", 1) for p in parts[2:] if "=" in p)
        return "\n\n" + _marker(mid, title, **fields) + "\n\n"

    # ── paragraphs and templates ───────────────────────────────────────────────
    def paragraph(self, par: str, section: str, limitations_ctx: bool) -> str:
        ev = self.i.evidence
        ids = [split_claim_spec(m.group(2))[0] for m in PH.finditer(par) if m.group(1) == "claim"]
        for cid in ids:
            if ev is None or cid not in ev.index:
                raise AssemblyError(f"unknown claim id {cid!r} in section {section!r}")
            st = ev.loc[cid, "audit_status"]
            if st not in RENDERABLE:
                return _marker(f"OPEN{_num(cid)}", f"paragraph withheld: {cid} is {st}", backs=cid, section=section,
                               unblock_by=ev.loc[cid, "remaining_action"] or "audit action")
            if st in LIMITATION_ONLY and not (limitations_ctx or "limitation" in par.lower()):
                return _marker(f"OPEN{_num(cid + 'L')}", f"{cid} is {st}: may appear only as a limitation",
                               backs=cid, section=section)

        def sub(m: re.Match) -> str:
            kind, spec = m.group(1), m.group(2).strip()
            if kind == "claim":
                return self.claim(spec)
            if kind == "cite":
                keys = [k.strip() for k in spec.split(",") if k.strip()]
                if len(keys) > 1:
                    parts = [self.cite(k) for k in keys]
                    if all(x.startswith("(") and x.endswith(")") for x in parts):
                        return "(" + "; ".join(x[1:-1] for x in parts) + ")"
                    return " ".join(parts)
                return self.cite(spec)
            if kind == "section":
                return self.i.sections.get(spec) or _marker("OPEN000", f"no such section {spec}")
            if kind == "table":
                return self.table(spec)
            if kind == "figure":
                return self.figure(spec)
            if kind == "ref":
                return self.ref(spec)
            return self.pending(spec)
        return PH.sub(sub, par)

    def template(self, text: str) -> str:
        out: list[str] = []
        section, limitations_ctx = "", False
        for par in re.split(r"\n\s*\n", text):
            stripped = par.strip()
            if stripped.startswith("#"):
                section = stripped.lstrip("# ").strip()
                limitations_ctx = "limitation" in section.lower()
                out.append(stripped)
                continue
            if not stripped:
                continue
            rendered = self.paragraph(stripped, section, limitations_ctx)
            if not rendered.startswith(">") and FORBIDDEN.search(rendered):
                raise AssemblyError(f"forbidden novelty word in section {section!r}: "
                                    f"{FORBIDDEN.search(rendered).group(0)!r}")
            out.append(rendered)
        return "\n\n".join(out) + "\n"

    def assemble(self, templates: list[tuple[str, str]]) -> str:
        """Templates as (name, text) in order; returns the body (references are appended by the caller)."""
        self.prescan(templates)
        return "\n".join(self.template(text) for _, text in templates)

    def references_csv_section(self) -> str:
        refs = self.i.references
        lines = ["# REFERENCES", ""]
        for key in sorted(self.cited):
            r = refs.loc[key]
            if r["source"] == "technical":
                lines.append(f"- [{key}] {r['note']}")
            else:
                lines.append(f"- [{key}] {r['authors']} ({r['year']}). {r['title']}. *{r['journal']}*. "
                             f"https://doi.org/{r['doi']}")
        return "\n".join(lines) + "\n"


def open_items(text: str) -> str:
    found = markers.parse_markers(text)
    lines = ["# OPEN ITEMS — parsed from the assembled manuscript", "",
             f"{len(found)} marker(s) remain. The final assembly refuses a manuscript that still has one.", ""]
    for m in found:
        f = m.fields
        lines.append(f"- **{m.id}** {m.title} · backs `{f.get('backs', '')}` · section `{f.get('section', '')}` "
                     f"· status `{f.get('status', '')}` · blocks_submission `{f.get('blocks_submission', '')}`")
    return "\n".join(lines) + "\n"


def parse_captions(text: str) -> dict[str, str]:
    """'**Fig01 Study area.** …' or '**T12 …**' paragraphs -> {id: caption text after the bold head}."""
    out: dict[str, str] = {}
    for par in re.split(r"\n\s*\n", text):
        m = re.match(r"\*\*(?P<id>[A-Za-z]+[\w-]*\d[\w-]*)\b(?P<head>[^*]*)\*\*\s*(?P<rest>.*)", par.strip(), re.S)
        if m:
            head = m.group("head").strip().rstrip(".")
            out[m.group("id")] = (head + ". " if head else "") + " ".join(m.group("rest").split())
    return out
