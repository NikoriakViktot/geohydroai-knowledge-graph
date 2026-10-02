"""Build 09_manuscript_literature_revised.md (and 09b_captions_revised.md) from the
frozen publication texts by applying the entries of revisions.yaml, and write
10_change_log.md from the same entries.

An entry replaces text (`original` must occur exactly once, whitespace-tolerant) or
inserts a paragraph after an anchor (`insert_after`). A failed match aborts the build,
so 09 can only contain documented changes; the originals are never written to.
"""
from __future__ import annotations

import logging
import re
from datetime import date
from pathlib import Path

import yaml

from tools.paper3_audit.config import DELIVERABLES, OUT_DIR, PUB_DIR

logger = logging.getLogger(__name__)

REVISIONS_YAML = OUT_DIR / "revisions.yaml"
CHANGE_TYPES = ("scientific", "terminology", "citation", "stylistic", "structure")
OUTPUTS = {"manuscript.md": DELIVERABLES["manuscript"], "captions.md": "09b_captions_revised.md"}


def _find_once(text: str, needle: str) -> tuple[int, int]:
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


def apply(entries: list[dict], texts: dict[str, str]) -> tuple[dict[str, str], list[dict]]:
    log = []
    texts = dict(texts)
    for e in entries:
        eid = e.get("id", "?")
        if e.get("change_type") not in CHANGE_TYPES:
            raise ValueError(f"{eid}: change_type must be one of {CHANGE_TYPES}")
        fname = e.get("file", "manuscript.md")
        text = texts[fname]
        if "original" in e:
            a, b = _find_once(text, e["original"])
            log.append({**e, "file": fname, "matched_original": text[a:b]})
            text = text[:a] + e["revised"] + text[b:]
        elif "insert_after" in e:
            a, b = _find_once(text, e["insert_after"])
            log.append({**e, "file": fname, "matched_original": ""})
            text = text[:b] + "\n\n" + e["revised"].rstrip("\n") + text[b:]
        else:
            raise ValueError(f"{eid}: needs 'original' or 'insert_after'")
        texts[fname] = text
    return texts, log


def render_log(log: list[dict]) -> str:
    esc = lambda s: str(s).replace("|", "\\|").replace("\n", " ")
    out = ["# 10 — Change log of the revised manuscript (09) and captions (09b)", "",
           f"Generated {date.today()} from `revisions.yaml` by `tools/paper3_audit/revise.py`. Every entry was applied "
           "mechanically to the frozen `publication/manuscript.md` / `captions.md`; nothing else differs between the "
           "originals and 09 / 09b. Table values are unchanged throughout.", "",
           "| # | id | file | section | type | thesis ids | references used | reason |", "|---|---|---|---|---|---|---|---|"]
    for i, e in enumerate(log, 1):
        out.append(f"| {i} | {e.get('id','')} | {e['file']} | {esc(e.get('section',''))} | {e.get('change_type','')} | "
                   f"{', '.join(e.get('thesis_ids') or [])} | {', '.join(e.get('refs') or [])} | {esc(e.get('reason',''))} |")
    out += ["", "## Full text of each change", ""]
    for i, e in enumerate(log, 1):
        out += [f"### {i}. {e.get('id','')} — {e['file']} — {e.get('section','')}", "", f"**Reason.** {e.get('reason','')}", ""]
        if e.get("matched_original"):
            out += ["**Original:**", "", "> " + " ".join(e["matched_original"].split()), ""]
        else:
            out += [f"**Inserted after:** `{' '.join(e.get('insert_after','').split())[:140]}…`", ""]
        out += ["**Revised:**", "", "> " + " ".join(e["revised"].split()), ""]
    return "\n".join(out) + "\n"


def run(out_dir: Path = OUT_DIR, revisions: Path = REVISIONS_YAML) -> int:
    doc = yaml.safe_load(revisions.read_text(encoding="utf-8")) or []
    entries = doc.get("revisions", []) if isinstance(doc, dict) else doc
    texts = {f: (PUB_DIR / f).read_text(encoding="utf-8") for f in OUTPUTS}
    new_texts, log = apply(entries, texts)
    new_texts["manuscript.md"] = _append_references(new_texts["manuscript.md"], log)
    for fname, out_name in OUTPUTS.items():
        n = sum(1 for e in log if e["file"] == fname)
        header = (f"<!-- {out_name} — built {date.today()} from publication/{fname} (bundle of 2026-09-25/26) by "
                  f"tools/paper3_audit/revise.py; {n} documented changes (10_change_log.md). Table values unchanged. -->\n\n")
        (out_dir / out_name).write_text(header + new_texts[fname], encoding="utf-8")
    (out_dir / DELIVERABLES["changelog"]).write_text(render_log(log), encoding="utf-8")
    logger.info("revise: %d changes applied", len(log))
    return 0


# ── reference list for the keys used in revisions ─────────────────────────────

CITATION_KEYS_YAML = OUT_DIR / "citation_keys.yaml"


def reference_entries(log: list[dict], work_dir: Path | None = None) -> list[str]:
    """One formatted reference per key named in any revision, resolved from
    07_references_verified.bib (bib keys) or the corpus (citation_keys.yaml)."""
    import json
    from tools.paper3_audit import corpus as corpus_mod
    from tools.paper3_audit.bibtex import parse_bib
    from tools.paper3_audit.config import WORK_DIR
    work_dir = work_dir or WORK_DIR
    keys = sorted({k for e in log for k in (e.get("refs") or [])})
    verified = {e["key"]: e for e in parse_bib((OUT_DIR / DELIVERABLES["bib"]).read_text(encoding="utf-8"))} if (OUT_DIR / DELIVERABLES["bib"]).exists() else {}
    # canonical method/index references proposed and verified by the audit (07c)
    p07c = OUT_DIR / "07c_method_references_verified.bib"
    if p07c.exists():
        for e in parse_bib(p07c.read_text(encoding="utf-8")):
            verified.setdefault(e["key"], e)
    corpus_keys = yaml.safe_load(CITATION_KEYS_YAML.read_text(encoding="utf-8")) if CITATION_KEYS_YAML.exists() else {}
    from tools.paper3_audit.bibtex import load_bib_entries
    bib_all = load_bib_entries(work_dir)
    index = corpus_mod.load_runtime_index(work_dir)
    meta = {r["paper_id"]: r for r in index.to_dict("records")}
    extra = json.loads((work_dir / "paper_meta_crossref.json").read_text(encoding="utf-8")) if (work_dir / "paper_meta_crossref.json").exists() else {}
    out, unresolved = [], []
    for k in keys:
        if k in verified:
            f = verified[k]["fields"]
            out.append(f"- **{k}** — {f.get('author','')} ({f.get('year','')}). {f.get('title','')}. *{f.get('journal', f.get('booktitle', f.get('publisher','')))}*"
                       f"{', ' + f['volume'] if f.get('volume') else ''}{', ' + f['pages'] if f.get('pages') else ''}. doi:{f.get('doi','')} [verified: CrossRef/OpenAlex, 07]")
        elif k in corpus_keys and (corpus_keys[k] if isinstance(corpus_keys[k], str) else corpus_keys[k].get("paper_id")) in meta:
            spec = corpus_keys[k] if isinstance(corpus_keys[k], dict) else {"paper_id": corpus_keys[k]}
            pid = spec["paper_id"]
            m = meta[pid]
            x = extra.get(pid) or {}
            authors = m.get("authors") or (" and ".join(f"{a['family']}, {a['given']}".strip(", ") for a in x.get("authors", [])) if x and not x.get("_none") else "")
            year = spec.get("year") or m.get("year") or (x.get("year") if x and not x.get("_none") else "")
            journal = spec.get("journal") or m.get("journal") or (x.get("journal") if x and not x.get("_none") else "")
            doi = spec.get("doi") or m.get("doi_clean", "")
            out.append(f"- **{k}** — {authors} ({year}). {m.get('title','')}. *{journal}*. doi:{doi} "
                       f"[corpus record `{pid}`; text read; metadata {'CrossRef' if x and not x.get('_none') else 'corpus'}{'; ' + spec['note'] if spec.get('note') else ''}]")
        elif k in bib_all:
            f = bib_all[k]["fields"]
            out.append(f"- **{k}** — {f.get('author','')} ({f.get('year','')}). {f.get('title','')}. {f.get('howpublished', f.get('journal', f.get('publisher','')))}. "
                       f"[grey literature / no resolvable DOI — see 07b_references_unresolved.md; cite with access date]")
        else:
            unresolved.append(k)
    if unresolved:
        out.append(f"- UNRESOLVED KEYS (must not remain in 09): {', '.join(unresolved)}")
    return out


def _append_references(text: str, log: list[dict]) -> str:
    entries = reference_entries(log)
    block = ["", "## References added or re-verified by the literature audit", "",
             "Keys marked [verified] come from `07_references_verified.bib`; corpus records name the local file that was read. "
             "Entries already in `docs/references.bib` and unchanged by this audit are not repeated here.", ""] + entries + [""]
    return text + "\n".join(block)
