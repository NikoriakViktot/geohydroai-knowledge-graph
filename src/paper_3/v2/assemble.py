"""Manuscript assembler — product B of ``v2/__init__.py``.

Section templates (``v2/templates/*.md``) are ordinary Markdown with four kinds
of placeholder. Nothing numeric is typed in a template: every number reaches the
page through ``{{claim:…}}`` from ``OWN_EVIDENCE.csv``, every citation through
``{{cite:…}}`` from ``REFERENCES.csv``, and every literature sentence through
the L-series stub. What cannot be supported renders as a ``[PENDING …]`` marker
that the submission gate (``markers.assert_none_remain``) refuses.

    {{claim:M1.1}}          value_resolved
    {{claim:M1.1.n}}        n_resolved      {{claim:M1.1.unc}}   uncertainty_resolved
    {{claim:M1.1.text}}     claim_text      {{claim:M1.1.caveat}} scientific_caveat
    {{cite:arcement1989}}   (Arcement & Schneider, 1989)  — unresolved key → marker
    {{section:7_8}}         the L-series section rendered by literature_matrix
    {{table:T3}}            a machine table from OWN_EVIDENCE (see TABLE_SPECS)
    {{pending:FIG08|title|backs=S1.1|section=6.9|produces=…}}  explicit marker

Paragraph rule: a paragraph that references a claim whose status is not in
``RENDERABLE`` is replaced whole by a marker naming the claim. A claim in
``LIMITATION_ONLY`` may appear only in a paragraph that also contains the word
"limitation" or sits under a Limitations heading — the status is a limitation,
not a finding.
"""
from __future__ import annotations

import hashlib
import logging
import re
from pathlib import Path

import pandas as pd

from src.paper_3._utils import OUT_DIR
from src.paper_3.v2 import markers
from src.paper_3.v2.literature_matrix import FORBIDDEN_NOVELTY_WORDS, STUB_FILE
from src.paper_3.v2.reference_registry import REGISTRY_CSV

logger = logging.getLogger(__name__)

TEMPLATE_ROOT = Path(__file__).with_name("templates")
PAPERS = (1, 2)


def template_dir(paper: int = 1) -> Path:
    return TEMPLATE_ROOT / f"paper{paper}"


def manuscript_name(paper: int = 1, lang: str = "en") -> str:
    return f"paper{paper}_manuscript_{lang}.md"


def open_items_name(paper: int = 1) -> str:
    return f"PAPER{paper}_OPEN_ITEMS.md"


TEMPLATE_DIR = template_dir(1)                 # backwards-compatible defaults
MANUSCRIPT_EN = manuscript_name(1, "en")
OPEN_ITEMS = open_items_name(1)
PATCHES = "v2_patches.csv"

RENDERABLE = {"SUPPORTED", "SUPPORTED_WITH_LIMITATION", "REVISE_UNCERTAINTY", "NOT_TESTABLE"}
LIMITATION_ONLY = {"REVISE_UNCERTAINTY", "NOT_TESTABLE"}

_PH = re.compile(r"\{\{(claim|cite|section|table|pending):([^}]+)\}\}")
#: Priority wording is a human judgement made after reading the precedents;
#: the assembler never writes it. "first season" is fine; "first to" is not.
_FORBIDDEN = re.compile(
    r"\b(novel|unprecedented|unique|pioneering|first to|for the first time|"
    r"the first (study|paper|analysis|attempt|report))\b", re.I)
assert set(FORBIDDEN_NOVELTY_WORDS) >= {"novel", "unprecedented", "unique", "pioneering"}

#: Machine tables: id → (title, claim-id filter, columns shown)
TABLE_SPECS: dict[str, dict] = {
    "T3": {"title": "Table 3. Hydraulic-state comparison, pre- and post-breach.",
           "claims": ["M1.1", "M1.3", "M1.2", "M1.4", "M2.1", "M3.1", "M3.2", "V10.1"],
           "columns": ["claim_id", "claim_text", "value_resolved", "uncertainty_resolved", "n_resolved", "audit_status"]},
    "T5": {"title": "Table 5. Manning roughness priors per surface class (literature-supported, not calibrated).",
           "claims": ["N1.4", "N1.4a"],
           "columns": ["claim_id", "claim_text", "value_resolved", "audit_status"]},
    "T6": {"title": "Table 6. Surface-class areas of the former water surface by year / state (km²).",
           "claims": ["S1.5", "S1.1a", "S1.1b", "S1.1", "S1.2a", "S1.2b", "S1.2", "S1.3", "N1.2a", "N1.2", "W1.1"],
           "columns": ["claim_id", "claim_text", "value_resolved", "n_resolved", "audit_status"]},
    "T7": {"title": "Table 7. Bed-surface cross-validation and shoreline uncertainty by zone.",
           "claims": ["V10.3", "V10.4", "B2.1", "B2.2", "B2.3", "B3.1", "B3.2", "I1.1", "I1.2"],
           "columns": ["claim_id", "claim_text", "value_resolved", "n_resolved", "audit_status"]},
}


class AssemblyError(RuntimeError):
    pass


# ── inputs ───────────────────────────────────────────────────────────────────

def load_inputs(out_dir: Path = OUT_DIR) -> dict:
    ev = pd.read_csv(out_dir / "OWN_EVIDENCE.csv", dtype=str, keep_default_na=False)
    refs = pd.read_csv(out_dir / REGISTRY_CSV, dtype=str, keep_default_na=False)
    stub_path = out_dir / STUB_FILE
    stub = stub_path.read_text(encoding="utf-8") if stub_path.exists() else ""
    return {"evidence": ev.set_index("claim_id"), "references": refs.set_index("cite_key"), "section_7_8": stub}


# ── placeholder rendering ────────────────────────────────────────────────────

def _latin(name: str) -> str:
    from src.paper_3.v2.reference_registry import _CYR
    if re.search(r"[А-Яа-яІіЇїЄєҐґ]", name):
        return name.lower().translate(_CYR).capitalize()
    return name


def _author_year(row: pd.Series) -> str:
    from src.paper_3.v2.reference_registry import split_authors
    fams = [_latin((a.split(",")[0] if "," in a else a).split()[0]) for a in split_authors(row["authors"])]
    if not fams:
        return f"{row['cite_key']}"
    if len(fams) == 1:
        head = fams[0]
    elif len(fams) == 2:
        head = f"{fams[0]} & {fams[1]}"
    else:
        head = f"{fams[0]} et al."
    return f"{head}, {row['year']}" if row["year"] else head


def _num(seed: str) -> int:
    """Stable 2-digit marker number from a seed (MARKER_HEAD accepts prefix + two digits)."""
    return 10 + int(hashlib.sha1(seed.encode()).hexdigest()[:6], 16) % 90


def _marker(mid: str, title: str, **fields) -> str:
    f = {"status": "blocked", "blocks_submission": "yes"}
    f.update({k: v for k, v in fields.items() if v})
    return markers.render(markers.Marker(id=mid, title=title, fields=f))


_CLAIM_SPEC = re.compile(r"^(?P<cid>.+?)(?:\.(?P<field>n|unc|text|caveat))?$")


def split_claim_spec(spec: str) -> tuple[str, str]:
    """'M1.1' → ('M1.1', ''); 'M1.1.unc' → ('M1.1', 'unc'); 'S1.1a.n' → ('S1.1a', 'n')."""
    m = _CLAIM_SPEC.match(spec.strip())
    return m.group("cid"), m.group("field") or ""


def render_claim(spec: str, ev: pd.DataFrame, used: set[str]) -> str:
    cid, field = split_claim_spec(spec)
    if cid not in ev.index:
        raise AssemblyError(f"unknown claim id {cid!r}")
    used.add(cid)
    r = ev.loc[cid]
    col = {"": "value_resolved", "n": "n_resolved", "unc": "uncertainty_resolved",
           "text": "claim_text", "caveat": "scientific_caveat"}[field]
    return str(r[col]).strip()


def render_cite(key: str, refs: pd.DataFrame, cited: set[str]) -> str:
    # a marker is a block quote: it must start its own line to be machine-visible
    if key not in refs.index:
        return "\n\n" + _marker(f"REF{_num(key)}", f"citation key {key!r} not in REFERENCES.csv") + "\n\n"
    r = refs.loc[key]
    if r["resolved"] != "True":
        return "\n\n" + _marker(f"REF{_num(key)}", f"unresolved reference {key}",
                                stub=r["note"][:120], blocks_submission="yes") + "\n\n"
    cited.add(key)
    if r["source"] == "technical":
        return f"({key})"
    return f"({_author_year(r)})"


def render_table(tid: str, ev: pd.DataFrame, used: set[str]) -> str:
    spec = TABLE_SPECS[tid]
    rows = [dict(ev.loc[c], claim_id=c) for c in spec["claims"] if c in ev.index]
    used.update(c for c in spec["claims"] if c in ev.index)
    cols = spec["columns"]
    head = "| " + " | ".join(cols) + " |"
    sep = "|" + "---|" * len(cols)
    body = ["| " + " | ".join(str(r[c]).replace("|", "/").replace("\n", " ")[:220] for c in cols) + " |" for r in rows]
    return "\n".join([f"**{spec['title']}**", "", head, sep, *body, ""])


def render_pending(spec: str) -> str:
    parts = spec.split("|")
    mid, title = parts[0].strip(), parts[1].strip() if len(parts) > 1 else "pending"
    fields = dict(p.split("=", 1) for p in parts[2:] if "=" in p)
    # block quote: must start its own line to be machine-visible, even mid-paragraph
    return "\n\n" + _marker(mid, title, **fields) + "\n\n"


def paragraph_claims(par: str) -> list[str]:
    return [split_claim_spec(m.group(2))[0] for m in _PH.finditer(par) if m.group(1) == "claim"]


def render_paragraph(par: str, inputs: dict, used: set[str], cited: set[str],
                     section: str, limitations_ctx: bool) -> str:
    ev = inputs["evidence"]
    ids = paragraph_claims(par)
    for cid in ids:
        if cid not in ev.index:
            raise AssemblyError(f"unknown claim id {cid!r} in section {section!r}")
        st = ev.loc[cid, "audit_status"]
        if st not in RENDERABLE:
            return _marker(f"OPEN{_num(cid)}",
                           f"paragraph withheld: {cid} is {st}", backs=cid, section=section,
                           unblock_by=ev.loc[cid, "remaining_action"] or "audit action")
        if st in LIMITATION_ONLY and not (limitations_ctx or "limitation" in par.lower()):
            return _marker(f"OPEN{_num(cid + 'L')}",
                           f"{cid} is {st}: may appear only as a limitation", backs=cid, section=section)

    def _sub(m: re.Match) -> str:
        kind, spec = m.group(1), m.group(2).strip()
        if kind == "claim":
            return render_claim(spec, ev, used)
        if kind == "cite":
            return render_cite(spec, inputs["references"], cited)
        if kind == "section":
            return inputs["section_7_8"] if spec == "7_8" else _marker("OPEN000", f"no such section {spec}")
        if kind == "table":
            return render_table(spec, ev, used)
        if kind == "pending":
            return render_pending(spec)
        raise AssemblyError(kind)
    return _PH.sub(_sub, par)


def render_template(text: str, inputs: dict, used: set[str], cited: set[str]) -> str:
    out: list[str] = []
    section = ""
    limitations_ctx = False
    for par in re.split(r"\n\s*\n", text):
        stripped = par.strip()
        if stripped.startswith("#"):
            section = stripped.lstrip("# ").strip()
            limitations_ctx = "limitation" in section.lower()
            out.append(stripped)
            continue
        if not stripped:
            continue
        rendered = render_paragraph(stripped, inputs, used, cited, section, limitations_ctx)
        if not rendered.startswith(">") and _FORBIDDEN.search(rendered):
            raise AssemblyError(f"forbidden novelty word in section {section!r}: "
                                f"{_FORBIDDEN.search(rendered).group(0)!r}")
        out.append(rendered)
    return "\n\n".join(out) + "\n"


# ── references section and open items ────────────────────────────────────────

def render_references(refs: pd.DataFrame, cited: set[str]) -> str:
    lines = ["# REFERENCES", ""]
    for key in sorted(cited):
        r = refs.loc[key]
        if r["source"] == "technical":
            lines.append(f"- [{key}] {r['note']}")
        else:
            lines.append(f"- [{key}] {r['authors']} ({r['year']}). {r['title']}. *{r['journal']}*. https://doi.org/{r['doi']}")
    return "\n".join(lines) + "\n"


def open_items(text: str) -> str:
    found = markers.parse_markers(text)
    lines = ["# V2_OPEN_ITEMS — parsed from the assembled manuscript", "",
             f"{len(found)} marker(s) remain. The submission gate is `markers.assert_none_remain`.", ""]
    for m in found:
        f = m.fields
        lines.append(f"- **{m.id}** {m.title} · backs `{f.get('backs', '')}` · section `{f.get('section', '')}` "
                     f"· status `{f.get('status', '')}` · blocks_submission `{f.get('blocks_submission', '')}`")
    return "\n".join(lines) + "\n"


def to_docx(md_path: Path, out_path: Path | None = None) -> Path:
    """Render the assembled markdown with the Article-1 docx builder (headings,
    inline bold/italic, GitHub tables, block quotes as literal text)."""
    from docx import Document
    from src.paper_audit.article_1_final.build_docx import convert_markdown
    doc = Document()
    convert_markdown(doc, md_path)
    target = out_path or md_path.with_suffix(".docx")
    doc.save(target)
    logger.info("docx written: %s", target)
    return target


# ── entry point ──────────────────────────────────────────────────────────────

def template_order(template_dir: Path = TEMPLATE_DIR) -> list[Path]:
    return sorted(p for p in template_dir.glob("*.md") if not p.name.startswith("_"))


def assemble(out_dir: Path = OUT_DIR, paper: int = 1, template_dir: Path | None = None) -> Path:
    tdir = template_dir or template_dir_for(paper)
    inputs = load_inputs(out_dir)
    used: set[str] = set()
    cited: set[str] = set()
    parts = [render_template(p.read_text(encoding="utf-8"), inputs, used, cited) for p in template_order(tdir)]
    body = "\n".join(parts)
    text = body + "\n" + render_references(inputs["references"], cited)
    target = out_dir / manuscript_name(paper, "en")
    target.write_text(text, encoding="utf-8")
    (out_dir / open_items_name(paper)).write_text(open_items(text), encoding="utf-8")
    ev = inputs["evidence"]
    foreign = sorted(c for c in used if "paper" in ev.columns and str(ev.loc[c, "paper"]) not in ("", str(paper)))
    pd.DataFrame({"claim_id": sorted(used)}).to_csv(out_dir / f"paper{paper}_claims_used.csv", index=False)
    logger.info("assembled %s: %d claims used (%d reused from the other paper), %d citations, %d markers",
                target.name, len(used), len(foreign), len(cited), len(markers.find_marker_ids(text)))
    return target


def template_dir_for(paper: int) -> Path:
    return template_dir(paper)
