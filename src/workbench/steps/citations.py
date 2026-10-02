"""citations (W1): every citation of the manuscript, resolved to the .bib and checked in its source.

    POST /manuscripts/citations  ->  occurrences with their sentence and quoted words
    POST /quotes/verify          ->  each quotation (and its numbers) in the cited source's full text

One verdict per cited key (AGENT_RULES §7 W1):
- VERIFIED   every quotation found, numbers included;
- FIX        a quotation or one of its numbers is not in the source;
- OPEN       the source text is unavailable, or the key is missing or ambiguous in the .bib;
- UNCHECKED  cited without quoted words: support is decided by POST /claims/check (planned), not here.
WORDING (an overstated claim) needs the model check and is not produced yet.
Report: <reviews>/workbench/citation_verification.{md,json}.
"""

from __future__ import annotations

import json
from collections import defaultdict

from src.services.quotes import too_short          # the API's own rule: no fragment of 25+ characters
from src.workbench import client
from src.workbench.delivery import sha256
from src.workbench.steps import Context, provenance, provenance_md, reports_dir, stage

ORDER = {"FIX": 0, "OPEN": 1, "UNCHECKED": 2, "VERIFIED": 3}


def manuscript_path(m) -> str | None:
    path = m.paths.manuscript_out
    return path.replace("{lang}", m.languages[0]) if path else None


def quotations(occurrences: list[dict], items: list[dict], results: list[dict], skipped: list[dict]) -> list[dict]:
    """One row per quotation of the manuscript: which of the sentence's cited sources holds it.

    A sentence that cites several works repeats its quoted words for each of them; the words can
    come from one source only, so the quotation is judged over all of them, not per key.
    """
    by_item = {r["key"]: r for r in results}
    groups: dict[tuple, dict] = {}
    for it in items + skipped:
        occ = occurrences[int(it["key"].rsplit("#", 1)[1].split(".")[0])]
        g = groups.setdefault((occ["line"], it["quote"]), {"line": occ["line"], "quote": it["quote"],
                                                            "section": occ.get("section"), "keys": {}})
        r = by_item.get(it["key"])
        g["keys"][occ["cite_key"]] = r["status"] if r else "TOO_SHORT"
        if r:
            g.setdefault("results", {})[occ["cite_key"]] = r
    out = []
    for g in groups.values():
        statuses = g["keys"]
        holders = [k for k, st in statuses.items() if st.startswith("FOUND")]
        bad = [n["value"] for k in holders for n in g.get("results", {})[k].get("numbers", []) if not n.get("found")]
        if holders and bad:
            result = "NUMBERS"
        elif holders:
            result = "FOUND"
        elif any(st == "SOURCE_UNAVAILABLE" for st in statuses.values()):
            result = "OPEN"
        elif any(st == "NOT_FOUND" for st in statuses.values()):
            result = "NOT_FOUND"
        else:
            result = "TOO_SHORT"
        secondary = any((g.get("results", {})[k].get("attribution") or {}).get("cites_other_sources") for k in holders)
        out.append({**{k: v for k, v in g.items() if k != "results"}, "result": result, "found_in": holders,
                    "numbers_missing": bad, "secondary_citation": secondary,
                    "closest": {k: ((r.get("span") or {}).get("text") or "")[:300]
                                for k, r in g.get("results", {}).items() if r["status"] == "NOT_FOUND"}})
    out.sort(key=lambda q: (q["line"], q["quote"]))
    return out


def verdicts(occurrences: list[dict], quotes: list[dict]) -> list[dict]:
    by_key: dict[str, dict] = defaultdict(lambda: {"occurrences": [], "quotations": []})
    for o in occurrences:
        by_key[o.get("cite_key") or f"{o['authors']} {o['year']}"]["occurrences"].append(o)
    for q in quotes:
        for key in q["keys"]:
            by_key[key]["quotations"].append(q)
    out = []
    for key, d in by_key.items():
        occ, qs = d["occurrences"], d["quotations"]
        statuses = {o["status"] for o in occ}
        mine = lambda q: q["keys"].get(key)
        if "resolved" not in statuses:
            verdict, why = "OPEN", f"key {', '.join(sorted(statuses))} in the .bib"
        elif any(q["result"] == "NOT_FOUND" and mine(q) == "NOT_FOUND" for q in qs):
            q = next(q for q in qs if q["result"] == "NOT_FOUND" and mine(q) == "NOT_FOUND")
            others = len(q["keys"]) - 1
            verdict, why = "FIX", (f"quotation (line {q['line']}) not in this source"
                                   + (f" nor in the {others} other source(s) cited with it" if others else ""))
        elif any(q["result"] == "NUMBERS" and key in q["found_in"] for q in qs):
            q = next(q for q in qs if q["result"] == "NUMBERS" and key in q["found_in"])
            verdict, why = "FIX", f"numbers not in the quoted passage: {', '.join(q['numbers_missing'])}"
        elif any(q["result"] == "OPEN" and mine(q) == "SOURCE_UNAVAILABLE" for q in qs):
            verdict, why = "OPEN", "source full text unavailable: unverified, not false"
        elif any(key in q["found_in"] for q in qs):
            n = sum(1 for q in qs if key in q["found_in"])
            verdict, why = "VERIFIED", f"{n} quotation(s) found in this source"
        elif qs:
            verdict, why = "UNCHECKED", "its sentence's quotations come from another cited source"
        else:
            verdict, why = "UNCHECKED", "cited without quoted words"
        out.append({"key": key, "verdict": verdict, "reason": why,
                    "doi": next((o.get("doi") for o in occ if o.get("doi")), None), "occurrences": len(occ),
                    "lines": sorted({o["line"] for o in occ}), "sections": sorted({o.get("section") or "" for o in occ}),
                    "secondary_citation": any(q["secondary_citation"] and key in q["found_in"] for q in qs)})
    out.sort(key=lambda v: (ORDER[v["verdict"]], v["key"]))
    return out


def run(ctx: Context) -> int:
    m = ctx.manifest()
    if m is None or not manuscript_path(m) or not m.paths.bib:
        print(f"{ctx.project_id}: the manifest needs paths.manuscript_out and paths.bib")
        return 1
    ms_path, bib_path = manuscript_path(m), m.paths.bib
    files = ctx.remote.pull([ms_path, bib_path])
    if ms_path not in files or bib_path not in files:
        print(f"{ctx.project_id}: missing in the repository: {[p for p in (ms_path, bib_path) if p not in files]}")
        return 1
    manuscript, bibtex = files[ms_path].decode("utf-8"), files[bib_path].decode("utf-8")
    api = client.api()
    cit = api.bib.citations(manuscript, bibtex, project_id=ctx.project_id)
    items, short = [], []
    for i, o in enumerate(cit["occurrences"]):
        source = o.get("doi") or o.get("cite_key")
        if o["status"] != "resolved" or not source:
            continue
        for j, q in enumerate(o.get("quoted") or []):
            item = {"key": f"{o['cite_key']}#{i}.{j}", "source": source, "quote": q, "manuscript_sentence": o["sentence"]}
            (short if too_short(q) else items).append(item)
    checked = api.quotes.verify(items, project_id=ctx.project_id) if items else {"items": [], "not_checked": [],
                                                                                 "summary": {}, "provenance": None}
    quotes = quotations(cit["occurrences"], items, checked["items"], short)
    table = verdicts(cit["occurrences"], quotes)
    counts = {k: sum(1 for v in table if v["verdict"] == k) for k in ORDER}
    prov = provenance(ctx, {ms_path: sha256(files[ms_path]), bib_path: sha256(files[bib_path])},
                      checked.get("provenance") or cit.get("provenance"))
    base = reports_dir(ctx, m)
    doc = {"project_id": ctx.project_id, "counts": counts, "summary": cit.get("summary", {}),
           "missing_keys": cit.get("missing_keys", []), "uncited_entries": cit.get("uncited_entries", []),
           "citations": table, "quotations": quotes, "provenance": prov}
    stage(ctx, base + "citation_verification.json", json.dumps(doc, indent=1, ensure_ascii=False) + "\n")
    stage(ctx, base + "citation_verification.md", render(ctx.project_id, ms_path, doc) + provenance_md(prov))
    print(f"{ctx.project_id}: {len(cit['occurrences'])} citations of {len(table)} keys, {len(items)} quotations; "
          + ", ".join(f"{k} {v}" for k, v in counts.items()))
    print(f"staged {base}citation_verification.md (deliver to send it)")
    return 2 if counts["FIX"] else 0


def render(project_id: str, manuscript: str, doc: dict) -> str:
    esc = lambda s: str(s).replace("|", "\\|").replace("\n", " ")
    c = doc["counts"]
    out = [f"# Citation verification — {project_id}", "",
           f"Manuscript `{manuscript}`. Workflow W1 of the Knowledge API: citations resolved to the .bib "
           "(`POST /manuscripts/citations`), quotations and their numbers checked in each source's full text "
           "(`POST /quotes/verify`). Support of an unquoted claim needs the model check (`POST /claims/check`, "
           "planned) and is marked UNCHECKED.", "",
           f"**{c['VERIFIED']} verified · {c['FIX']} to fix · {c['OPEN']} open · {c['UNCHECKED']} unchecked**", "",
           "| key | verdict | why | DOI | lines | secondary |", "|---|---|---|---|---|---|"]
    for v in doc["citations"]:
        out.append(f"| {v['key']} | **{v['verdict']}** | {esc(v['reason'])} | {v.get('doi') or ''} | "
                   f"{', '.join(map(str, v['lines'][:8]))} | {'yes — trace the original' if v['secondary_citation'] else ''} |")
    if doc.get("quotations"):
        out += ["", "## Quotations", "",
                "Each quotation is judged over every source its sentence cites; `found in` names the source that holds it.",
                "", "| line | quotation | cited | result | found in | note |", "|---|---|---|---|---|---|"]
        for q in doc["quotations"]:
            note = ("numbers missing: " + ", ".join(q["numbers_missing"])) if q["numbers_missing"] else \
                   ("the passage cites other works: trace the original" if q["secondary_citation"] else "")
            if q["result"] == "NOT_FOUND":
                close = next((t for t in q["closest"].values() if t), "")
                note = note or (f"closest wording: “{esc(close)[:160]}”" if close else "")
            out.append(f"| {q['line']} | “{esc(q['quote'])[:160]}” | {', '.join(q['keys'])} | **{q['result']}** | "
                       f"{', '.join(q['found_in'])} | {note} |")
    if doc.get("missing_keys"):
        out += ["", "## Citations without a .bib entry", ""]
        out += [f"- {esc(k.get('cite_text') or k) if isinstance(k, dict) else esc(k)}" for k in doc["missing_keys"]]
    if doc.get("uncited_entries"):
        out += ["", "## .bib entries never cited", "", ", ".join(doc["uncited_entries"])]
    return "\n".join(out) + "\n"
