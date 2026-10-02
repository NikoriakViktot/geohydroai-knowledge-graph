"""Assemble the complete article package from 09 (text), 09b (captions), the figures of the
bundle and the verified bibliographies: `article/Paper3_manuscript_literature_revised.md`
with figures placed after their first mention, a supplementary-figures section, and a
reference list; plus a JSON model that the docx builder consumes. Nothing is invented: every
reference line comes from 07 / 07c / citation_keys.yaml (corpus records) or 07b (unresolved,
marked)."""
from __future__ import annotations

import json
import re
import shutil
from datetime import date
from pathlib import Path

import yaml

from tools.paper3_audit.bibtex import parse_bib, load_bib_entries
from tools.paper3_audit.config import DELIVERABLES, OUT_DIR, PUB_DIR, WORK_DIR
from tools.paper3_audit import corpus as corpus_mod

ARTICLE_DIR = OUT_DIR / "article"
FIG_RE = re.compile(r"\*\*(Fig(?:S)?\d{2})\b([^*]*)\*\*\s*(.*)")


def load_captions() -> dict[str, tuple[str, str]]:
    """FigNN → (title, caption text) from 09b (main figures one per paragraph; FigS lines may
    hold several captions separated by '**FigSNN**')."""
    text = (OUT_DIR / "09b_captions_revised.md").read_text(encoding="utf-8")
    caps: dict[str, tuple[str, str]] = {}
    body = text.split("## Tables")[0]
    parts = re.split(r"(?=\*\*Fig(?:S)?\d{2}\b)", body)
    for p in parts:
        m = re.match(r"\*\*(Fig(?:S)?\d{2})\b\s*([^*]*)\*\*\s*(.*)", " ".join(p.split()), re.S)
        if m:
            fid, title, cap = m.group(1), m.group(2).strip(" ."), m.group(3).strip()
            cap = re.split(r"\s##\s", cap)[0].strip()  # a following '## …' heading of 09b is not caption text
            caps[fid] = (title, cap)
    return caps


def caption_line(fid: str, title: str, cap: str) -> str:
    return f"**{fid}. {title}.** {cap}" if title else f"**{fid}.** {cap}"


def figure_files() -> dict[str, Path]:
    out = {}
    for p in sorted((PUB_DIR / "figures").glob("Fig*.png")):
        out[p.name.split("_")[0]] = p
    return out


def reference_lines() -> tuple[list[str], list[str]]:
    """(verified entries, unresolved entries) as formatted strings, for EVERY key cited in the
    manuscript text: bib keys (07, 07c) + corpus-only keys (citation_keys.yaml)."""
    verified = {e["key"]: e for e in parse_bib((OUT_DIR / DELIVERABLES["bib"]).read_text(encoding="utf-8"))}
    p07c = OUT_DIR / "07c_method_references_verified.bib"
    if p07c.exists():
        for e in parse_bib(p07c.read_text(encoding="utf-8")):
            verified.setdefault(e["key"], e)
    all_bib = load_bib_entries(WORK_DIR)
    ck = yaml.safe_load((OUT_DIR / "citation_keys.yaml").read_text(encoding="utf-8")) or {}
    index = corpus_mod.load_runtime_index(WORK_DIR)
    meta = {r["paper_id"]: r for r in index.to_dict("records")}
    extra = json.loads((WORK_DIR / "paper_meta_crossref.json").read_text(encoding="utf-8")) if (WORK_DIR / "paper_meta_crossref.json").exists() else {}

    def fmt_bib(e):
        f = e["fields"]
        venue = f.get("journal") or f.get("booktitle") or f.get("publisher") or f.get("howpublished", "")
        vol = f", {f['volume']}" if f.get("volume") else ""
        pages = f", {f['pages']}" if f.get("pages") else ""
        doi = f" https://doi.org/{f['doi']}" if f.get("doi") else ""
        return f"{f.get('author', '')} ({f.get('year', '')}). {f.get('title', '')}. {venue}{vol}{pages}.{doi}".replace("{", "").replace("}", "")

    def fmt_corpus(k):
        spec = ck[k] if isinstance(ck[k], dict) else {"paper_id": ck[k]}
        m = meta.get(spec["paper_id"], {})
        x = extra.get(spec["paper_id"]) or {}
        authors = m.get("authors") or (" and ".join(f"{a['family']}, {a['given']}".strip(", ") for a in x.get("authors", [])) if x and not x.get("_none") else "")
        year = spec.get("year") or m.get("year") or (x.get("year") if x and not x.get("_none") else "")
        journal = spec.get("journal") or m.get("journal") or (x.get("journal") if x and not x.get("_none") else "")
        doi = spec.get("doi") or m.get("doi_clean", "")
        note = spec.get("note", "")
        return f"{authors} ({year}). {m.get('title', '')}. {journal}.{' https://doi.org/' + doi if doi else ''}{' [' + note + ']' if note else ''}"

    ok, un = [], []
    for k in sorted(set(verified) | set(ck)):
        if k in verified:
            ok.append(f"[{k}] " + fmt_bib(verified[k]))
        else:
            ok.append(f"[{k}] " + fmt_corpus(k) + " [corpus record, text read; metadata from CrossRef/corpus]")
    for k, e in all_bib.items():
        if k not in verified:
            un.append(f"[{k}] " + fmt_bib(e) + " — NOT resolvable in CrossRef/OpenAlex (07b): cite with access date or drop")
    return ok, un


def build_markdown() -> tuple[str, dict]:
    text = (OUT_DIR / DELIVERABLES["manuscript"]).read_text(encoding="utf-8")
    text = re.sub(r"^<!--.*?-->\n\n", "", text, count=1, flags=re.S)
    # drop the audit-generated appendix; the full list is rebuilt below
    text = text.split("\n## References added or re-verified by the literature audit")[0]
    caps, figs = load_captions(), figure_files()
    placed: list[str] = []
    paras = text.split("\n\n")
    out = []
    for para in paras:
        out.append(para)
        for fid in re.findall(r"\bFig(?:S)?\d{2}\b", para):
            if fid in figs and fid not in placed and not fid.startswith("FigS"):
                title, cap = caps.get(fid, ("", ""))
                out.append(f"![{fid}](figures/{figs[fid].name})\n\n" + caption_line(fid, title, cap))
                placed.append(fid)
    # main figures the text never cites (Fig03 in 21ba34c) go before the Discussion, flagged
    missing = [f for f in figs if not f.startswith("FigS") and f not in placed]
    if missing:
        block = []
        for fid in missing:
            title, cap = caps.get(fid, ("", ""))
            block.append(f"![{fid}](figures/{figs[fid].name})\n\n**{fid}. {title}.** {cap} *(Not cited in the manuscript text of bundle 21ba34c — placed here by the assembler; see AUDIT_ACTIONS item 13.)*")
            placed.append(fid)
        for i, para in enumerate(out):
            if para.startswith("## 5. Discussion"):
                out[i:i] = block
                break
    body = "\n\n".join(out)
    # supplementary figures
    supp = ["## Supplementary figures", ""]
    for fid, path in figs.items():
        if fid.startswith("FigS"):
            title, cap = caps.get(fid, ("", ""))
            supp += [f"![{fid}](figures/{path.name})", "", caption_line(fid, title, cap), ""]
            placed.append(fid)
    ok, un = reference_lines()
    refs = ["## References", "",
            f"Verified against CrossRef/OpenAlex on 2026-09-28 (`07_references_verified.bib`, `07c_method_references_verified.bib`); "
            "corpus records were read in the GeoHydroAI corpus. Keys in square brackets are the bibliography keys.", ""]
    refs += [f"- {l}" for l in ok]
    refs += ["", "### Unresolved (grey literature, datasets, software — no DOI)", ""] + [f"- {l}" for l in un]
    md = body.rstrip() + "\n\n" + "\n".join(supp) + "\n" + "\n".join(refs) + "\n"
    header = (f"<!-- Assembled {date.today()} by tools/paper3_audit/article.py from 09_manuscript_literature_revised.md "
              f"(floodstate-eo bundle 21ba34c + {len(yaml.safe_load((OUT_DIR / 'revisions.yaml').read_text())['revisions'])} documented changes), "
              "09b_captions_revised.md, publication/figures and the verified bibliographies. -->\n\n")
    model = {"figures": {fid: str(p) for fid, p in figs.items()}, "captions": caps, "placed": placed,
             "references_verified": ok, "references_unresolved": un}
    return header + md, model


def run() -> int:
    ARTICLE_DIR.mkdir(parents=True, exist_ok=True)
    (ARTICLE_DIR / "figures").mkdir(exist_ok=True)
    for p in (PUB_DIR / "figures").glob("Fig*.png"):
        shutil.copy2(p, ARTICLE_DIR / "figures" / p.name)
    md, model = build_markdown()
    (ARTICLE_DIR / "Paper3_manuscript_literature_revised.md").write_text(md, encoding="utf-8")
    (ARTICLE_DIR / "article_model.json").write_text(json.dumps(model, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"article: {len(md.splitlines())} lines, {len(model['figures'])} figures ({len(model['placed'])} placed), "
          f"{len(model['references_verified'])} verified refs, {len(model['references_unresolved'])} unresolved")
    return 0
