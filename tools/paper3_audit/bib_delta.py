"""07d_corpus_references.bib: BibTeX entries for the corpus-only citation keys (citation_keys.yaml)
that docs/references.bib of floodstate-eo does not hold. Metadata comes from CrossRef (by the
record's DOI) or, when the record has no DOI, from an OpenAlex title search (accepted only at
title ratio >= TITLE_MIN and matching year); anything else is written to 07d_unresolved.md, not
to the .bib. Nothing is typed by hand."""
from __future__ import annotations

import json
import re
import sys
from datetime import date
from pathlib import Path

import yaml

from tools.paper3_audit import corpus as corpus_mod
from tools.paper3_audit.bibtex import parse_bib
from tools.paper3_audit.config import OUT_DIR, WORK_DIR
from tools.paper3_audit.references import _bib_escape, crossref_message, openalex_title_search, openalex_work


def _authors(meta: dict) -> str:
    return " and ".join(f"{a['family']}, {a['given']}".strip(", ") for a in meta.get("authors", []) if a.get("family"))


def build(bib_path: Path, out_dir: Path = OUT_DIR, work_dir: Path = WORK_DIR, crossref=crossref_message,
          title_search=openalex_title_search, oa_work=openalex_work) -> tuple[int, int]:
    have = {e["key"] for e in parse_bib(bib_path.read_text(encoding="utf-8"))}
    ck = yaml.safe_load((out_dir / "citation_keys.yaml").read_text(encoding="utf-8")) or {}
    index = corpus_mod.load_runtime_index(work_dir)
    meta = {r["paper_id"]: r for r in index.to_dict("records")}
    cache_p = work_dir / "bib_delta_cache.json"
    cache = json.loads(cache_p.read_text()) if cache_p.exists() else {}
    entries, unresolved = [], []
    for key in sorted(k for k in ck if k not in have):
        spec = ck[key] if isinstance(ck[key], dict) else {"paper_id": ck[key]}
        m = meta.get(spec["paper_id"], {})
        doi = corpus_mod.clean_doi(spec.get("doi") or m.get("doi_clean", "") or "")
        route = ""
        if not doi and m.get("title"):
            hit = cache.get("ts:" + key) or title_search(m["title"], str(m.get("year") or ""))
            cache["ts:" + key] = hit
            if hit and hit.get("doi"):
                doi, route = hit["doi"], f"openalex_title_search ratio {hit['ratio']}"
        cr = None
        if doi:
            cr = cache.get("cr:" + doi) or crossref(doi)
            cache["cr:" + doi] = cr
        if not cr:
            oa = (cache.get("oa:" + doi) or oa_work(doi)) if doi else None
            if doi:
                cache["oa:" + doi] = oa
            unresolved.append((key, spec["paper_id"], doi, m.get("title", ""),
                               "DOI in OpenAlex only (DataCite/preprint)" if oa else ("no DOI in the corpus record; title search found nothing" if not doi else "DOI not in CrossRef/OpenAlex")))
            continue
        btype = {"journal-article": "article", "proceedings-article": "inproceedings", "book-chapter": "incollection",
                 "posted-content": "misc", "book": "book"}.get(cr.get("type", ""), "misc")
        fields = [("author", _authors(cr)), ("title", cr["title"]), ("year", cr["year"]), ("doi", doi)]
        if btype == "inproceedings":
            fields.append(("booktitle", cr.get("journal", "")))
        elif btype in ("article", "incollection"):
            fields.append(("journal" if btype == "article" else "booktitle", cr.get("journal", "")))
        else:
            fields.append(("publisher", cr.get("publisher", "")))
        for k, v in (("volume", cr.get("volume")), ("number", cr.get("issue")), ("pages", cr.get("pages") or cr.get("article_number"))):
            if v:
                fields.append((k, v))
        fields += [("note", f"corpus record {spec['paper_id'][:60]}; read in the GeoHydroAI corpus" + (f"; DOI via {route}" if route else "")),
                   ("verifiedon", str(date.today()))]
        body = ",\n".join(f"  {k} = {{{_bib_escape(v)}}}" for k, v in fields if v)
        entries.append(f"@{btype}{{{key},\n{body}\n}}\n")
    cache_p.write_text(json.dumps(cache, ensure_ascii=False, indent=0))
    head = [f"% 07d_corpus_references.bib — generated {date.today()} by tools/paper3_audit/bib_delta.py",
            f"% Citation keys used by the audited article that {bib_path.name} ({len(have)} keys) does not hold.",
            "% Every field is CrossRef's for the record's DOI; keys without a CrossRef record are in 07d_unresolved.md.", ""]
    (out_dir / "07d_corpus_references.bib").write_text("\n".join(head + entries), encoding="utf-8")
    un = ["# 07d — corpus-only citation keys without a CrossRef record", "",
          "| key | corpus record | DOI tried | title (corpus) | why |", "|---|---|---|---|---|"]
    un += [f"| {k} | {p[:60]} | {d} | {t[:90]} | {w} |" for k, p, d, t, w in unresolved]
    (out_dir / "07d_unresolved.md").write_text("\n".join(un) + "\n", encoding="utf-8")
    print(f"07d: {len(entries)} bib entries, {len(unresolved)} unresolved (of {len(entries) + len(unresolved)} missing keys)")
    return len(entries), len(unresolved)


if __name__ == "__main__":
    build(Path(sys.argv[1]))
