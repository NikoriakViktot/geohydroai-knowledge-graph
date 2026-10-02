"""Final article: the revised text (09) with the final-assembly edits below, every figure after
its first mention, every table after its first mention (compact column views for the wide ones,
CSV pointers for the long ones), supplementary figures and tables, and the verified reference
list. Writes article/Paper3_final.md, article/tables/ (all CSVs + README) and
article/final_model.json. No number is altered: table cells come from publication/tables/."""
from __future__ import annotations

import json
import re
import shutil
from datetime import date
from pathlib import Path

import yaml

from tools.paper3_audit.article import ARTICLE_DIR, caption_line, figure_files, load_captions, reference_lines
from tools.paper3_audit.config import DELIVERABLES, OUT_DIR, PUB_DIR

TABLES_DIR = PUB_DIR / "tables"
LONG_ROWS = 60  # longer tables are cited by caption and shipped as CSV
# wide tables: 1-based column numbers of the CSV header kept in the printed view
COMPACT = {
    "T12": list(range(1, 16)),
    "T21": [1, 3, 4, 5, 6, 7, 8, 10, 11, 12, 13, 17, 18, 19],
    "T14": list(range(1, 10)) + [20],
    "T20": list(range(1, 15)),
    "T24": list(range(1, 16)),
}
T_RE = re.compile(r"\bT\d{2}[a-c]?\b")

# (id, old, new, expected count, reason) — applied to 09 before assembly; logged in 10_change_log.md
FINAL_EDITS = [
    ("FA-01", re.compile(r"\*\*Manuscript draft \(Paper 3 of the Kakhovka series\).*?\*\*\n\n", re.S), "", 1,
     "bundle build note (fill_manuscript provenance, VERIFY flag) is not article text; provenance moves to the header comment"),
    ("FA-02", "literature_reported, VERIFY)", "literature_reported)", 2,
     "the VERIFY flag of the bundle is resolved: UNOSAT product sheets stay listed under unresolved references, Kadam 2024 is CrossRef-verified"),
    ("FA-03", re.compile(r"## References\n\n`docs/references\.bib`; entries added for this paper carry `note = \{VERIFY\}` until checked \(`references_to_verify\.md`\)\.\s*$"), "", 1,
     "replaced by the verified reference list assembled from 07 / 07c / corpus records"),
    ("FA-04", "*Label effect.* At fixed inputs, U2 predicts", "*Label effect* (Fig03; paired differences in T06 and T07b). At fixed inputs, U2 predicts", 1,
     "Fig03 (U-Net weak-label experiment) and the paired-difference tables were never cited in the text of bundle 21ba34c",
     re.compile(r"Label effect[^\n]{0,60}Fig03")),
]


def apply_final_edits(text: str) -> tuple[str, list[dict]]:
    log = []
    for eid, old, new, n_expected, reason, *already in FINAL_EDITS:
        if isinstance(old, re.Pattern):
            text, n = old.subn(new, text)
        else:
            n = text.count(old)
            text = text.replace(old, new)
        if n == 0 and already and already[0].search(text):
            # the bundle already carries the revised wording (floodstate-eo applied the action item)
            log.append({"id": eid, "old": old.pattern if isinstance(old, re.Pattern) else old, "new": new, "n": 0,
                        "reason": reason + " — already present in the bundle, nothing applied"})
            continue
        if n != n_expected:
            raise RuntimeError(f"{eid}: expected {n_expected} occurrence(s), found {n}")
        log.append({"id": eid, "old": old.pattern if isinstance(old, re.Pattern) else old, "new": new, "n": n, "reason": reason})
    return text, log


def parse_md_table(path: Path) -> tuple[list[str], list[str], list[list[str]] | None]:
    """→ (caption lines, header cells, rows) ; rows None when a cell contains '|' (unparseable)."""
    lines = path.read_text(encoding="utf-8").splitlines()
    cap = [l.rstrip() for l in lines if l.strip() and not l.startswith("|")]
    pipe = [l for l in lines if l.startswith("|")]
    header = [c.strip() for c in pipe[0].strip().strip("|").split("|")]
    rows = []
    for l in pipe[2:]:
        cells = [c.strip() for c in l.strip().strip("|").split("|")]
        if len(cells) != len(header):
            return cap, header, None
        rows.append(cells)
    return cap, header, rows


def render_pipe(header: list[str], rows: list[list[str]]) -> str:
    out = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    out += ["| " + " | ".join(r) + " |" for r in rows]
    return "\n".join(out)


def table_block(tid: str, tables_dir: Path = TABLES_DIR) -> tuple[str, str]:
    """(markdown block, mode) with mode ∈ {full, compact, pointer}."""
    cap, header, rows = parse_md_table(tables_dir / f"{tid}.md")
    n_rows, n_cols = (len(rows) if rows is not None else sum(1 for l in (tables_dir / f"{tid}.md").read_text().splitlines() if l.startswith("|")) - 2), len(header)
    head = "\n".join(cap)
    if rows is None or n_rows > LONG_ROWS:
        return (f"{head}\n\n*Table {tid} has {n_rows} rows × {n_cols} columns and is supplied as "
                f"`tables/{tid}.csv` (Supplementary Data); it is not printed here.*"), "pointer"
    if tid in COMPACT:
        keep = [i - 1 for i in COMPACT[tid]]
        dropped = [h for i, h in enumerate(header) if i not in keep]
        body = render_pipe([header[i] for i in keep], [[r[i] for i in keep] for r in rows])
        note = (f"*Compact view: {len(keep)} of {n_cols} columns; the columns {', '.join(dropped)} are in "
                f"`tables/{tid}.csv`.*")
        return f"{head}\n\n{body}\n\n{note}", "compact"
    return f"{head}\n\n{render_pipe(header, rows)}", "full"


def table_sort_key(tid: str):
    m = re.match(r"T(\d+)([a-c]?)", tid)
    return int(m.group(1)), m.group(2)


def build_final() -> tuple[str, dict]:
    text = (OUT_DIR / DELIVERABLES["manuscript"]).read_text(encoding="utf-8")
    text = re.sub(r"^<!--.*?-->\n\n", "", text, count=1, flags=re.S)
    text = text.split("\n## References added or re-verified by the literature audit")[0]
    text, edit_log = apply_final_edits(text)
    caps, figs = load_captions(), figure_files()
    tables = {p.stem: p for p in TABLES_DIR.glob("T*.md")}
    placed_figs, placed_tabs, modes = [], [], {}
    out = []
    for para in text.split("\n\n"):
        out.append(para)
        if para.startswith("## ") or para.startswith("### "):
            continue
        for fid in re.findall(r"\bFig(?:S)?\d{2}\b", para):
            if fid in figs and fid not in placed_figs and not fid.startswith("FigS"):
                title, cap = caps.get(fid, ("", ""))
                out.append(f"![{fid}](figures/{figs[fid].name})\n\n" + caption_line(fid, title, cap))
                placed_figs.append(fid)
        for tid in T_RE.findall(para):
            if tid in tables and tid not in placed_tabs:
                block, modes[tid] = table_block(tid)
                out.append(block)
                placed_tabs.append(tid)
    missing = [f for f in figs if not f.startswith("FigS") and f not in placed_figs]
    if missing:
        raise RuntimeError(f"main figures never cited: {missing}")
    body = "\n\n".join(out).rstrip()
    supp = ["## Supplementary figures", ""]
    for fid, path in figs.items():
        if fid.startswith("FigS"):
            title, cap = caps.get(fid, ("", ""))
            supp += [f"![{fid}](figures/{path.name})", "", caption_line(fid, title, cap), ""]
            placed_figs.append(fid)
    supp += ["## Supplementary tables", "",
             "Tables of the publication bundle not cited in the main text (their identifiers are the bundle's, `tables/README.md` "
             "defines every metric and semantics column). All 39 tables are supplied as CSV in `tables/`.", ""]
    for tid in sorted(set(tables) - set(placed_tabs), key=table_sort_key):
        block, modes[tid] = table_block(tid)
        supp += [block, ""]
        placed_tabs.append(tid)
    ok, un = reference_lines()
    refs = ["## References", "",
            "Verified against CrossRef/OpenAlex on 2026-09-28 (`07_references_verified.bib`, `07c_method_references_verified.bib`); "
            "corpus records were read in the GeoHydroAI corpus. Keys in square brackets are the bibliography keys.", ""]
    refs += [f"- {l}" for l in ok]
    refs += ["", "### Unresolved (grey literature, datasets, software — no DOI)", ""] + [f"- {l}" for l in un]
    n_rev = len(yaml.safe_load((OUT_DIR / "revisions.yaml").read_text())["revisions"])
    header = (f"<!-- Paper3_final.md — assembled {date.today()} by tools/paper3_audit/final_article.py: floodstate-eo bundle 21ba34c "
              f"(manuscript.md filled from publication/tables by fill_manuscript.py) + {n_rev} literature-audit revisions (10_change_log.md) "
              f"+ {len(edit_log)} final-assembly edits (FA-*); figures from publication/figures, tables from publication/tables, "
              "references from 07/07c and corpus records. -->\n\n")
    md = header + body + "\n\n" + "\n".join(supp) + "\n" + "\n".join(refs) + "\n"
    model = {"figures_placed": placed_figs, "tables_placed": placed_tabs, "table_modes": modes, "final_edits": edit_log,
             "references_verified": len(ok), "references_unresolved": len(un)}
    return md, model


def write_change_log(edit_log: list[dict]) -> None:
    p = OUT_DIR / DELIVERABLES["changelog"] if "changelog" in DELIVERABLES else OUT_DIR / "10_change_log.md"
    text = p.read_text(encoding="utf-8")
    marker = "## Final-assembly edits (article/Paper3_final.md)"
    text = text.split("\n" + marker)[0].rstrip() + "\n\n" + marker + "\n\n"
    text += "Applied by `tools/paper3_audit/final_article.py` on top of the revisions above; not part of 09.\n\n"
    text += "| id | original | revised | reason |\n|---|---|---|---|\n"
    for e in edit_log:
        old = e["old"].replace("\n", " ").replace("|", "\\|")[:160]
        new = e["new"].replace("\n", " ").replace("|", "\\|")[:160] or "(removed)"
        text += f"| {e['id']} | {old} | {new} | {e['reason']} |\n"
    p.write_text(text, encoding="utf-8")


def run() -> int:
    ARTICLE_DIR.mkdir(parents=True, exist_ok=True)
    (ARTICLE_DIR / "figures").mkdir(exist_ok=True)
    (ARTICLE_DIR / "tables").mkdir(exist_ok=True)
    for p in (PUB_DIR / "figures").glob("Fig*.png"):
        shutil.copy2(p, ARTICLE_DIR / "figures" / p.name)
    for p in list(TABLES_DIR.glob("T*.csv")) + [TABLES_DIR / "README.md"]:
        shutil.copy2(p, ARTICLE_DIR / "tables" / p.name)
    md, model = build_final()
    (ARTICLE_DIR / "Paper3_final.md").write_text(md, encoding="utf-8")
    (ARTICLE_DIR / "final_model.json").write_text(json.dumps(model, indent=1, ensure_ascii=False), encoding="utf-8")
    write_change_log(model["final_edits"])
    m = model["table_modes"]
    print(f"final article: {len(md.splitlines())} lines, {len(model['figures_placed'])} figures, "
          f"{len(model['tables_placed'])} tables (full {sum(v == 'full' for v in m.values())}, compact "
          f"{sum(v == 'compact' for v in m.values())}, pointer {sum(v == 'pointer' for v in m.values())}), "
          f"{model['references_verified']} verified refs, {model['references_unresolved']} unresolved")
    return 0
