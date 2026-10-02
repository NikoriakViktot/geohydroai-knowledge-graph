"""bibliography (W2): audit the paper's .bib against the registries and render its reference list.

    POST /bib/audit   ->  per entry ok | fix | unresolved, with the problems and a suggested entry
    POST /bib/render  ->  the reference list of the keys the manuscript cites, in the manifest's style

Reports: <reviews>/workbench/bibliography_audit.{md,json}, REFERENCES.md (rendered list) and
bibliography_suggestions.bib: the suggested entries, for a person to review. The .bib itself is
never rewritten: metadata come from the registries, decisions from the author.
"""

from __future__ import annotations

import json

from src.workbench import client
from src.workbench.delivery import sha256
from src.workbench.steps import Context, provenance, provenance_md, reports_dir, stage
from src.workbench.steps.citations import manuscript_path


def run(ctx: Context) -> int:
    m = ctx.manifest()
    if m is None or not m.paths.bib:
        print(f"{ctx.project_id}: the manifest needs paths.bib")
        return 1
    ms_path = manuscript_path(m)
    files = ctx.remote.pull([p for p in (m.paths.bib, ms_path) if p])
    if m.paths.bib not in files:
        print(f"{ctx.project_id}: {m.paths.bib} not in the repository")
        return 1
    bibtex = files[m.paths.bib].decode("utf-8")
    manuscript = files[ms_path].decode("utf-8") if ms_path in files else None
    api = client.api()
    audit = api.bib.audit(bibtex, project_id=ctx.project_id)
    rendered = api.bib.render(bibtex, manuscript=manuscript, style=m.citation.style)
    inputs = {path: sha256(data) for path, data in files.items()}
    prov = provenance(ctx, inputs, audit.get("provenance"))
    base = reports_dir(ctx, m)
    doc = {"project_id": ctx.project_id, "summary": audit.get("summary", {}), "entries": audit["entries"],
           "mixed_field_names": audit.get("mixed_field_names", []), "style": m.citation.style,
           "unresolved_keys": rendered.get("unresolved_keys", []), "uncited_entries": rendered.get("uncited_entries", []),
           "provenance": prov}
    stage(ctx, base + "bibliography_audit.json", json.dumps(doc, indent=1, ensure_ascii=False) + "\n")
    stage(ctx, base + "bibliography_audit.md", render_audit(ctx.project_id, m.paths.bib, doc) + provenance_md(prov))
    refs = [f"# References ({m.citation.style})", "",
            f"Rendered by `POST /bib/render` from `{m.paths.bib}`"
            + (f", for the keys cited in `{ms_path}`" if manuscript else "") + ".", ""]
    refs += [f"{i}. {r['text']}" for i, r in enumerate(rendered["references"], 1)]
    if rendered.get("unresolved_keys"):
        refs += ["", "Cited but not in the .bib: " + ", ".join(rendered["unresolved_keys"])]
    stage(ctx, base + "REFERENCES.md", "\n".join(refs) + "\n" + provenance_md(prov))
    suggestions = [e["suggested_bibtex"] for e in audit["entries"] if e.get("suggested_bibtex")]
    if suggestions:
        stage(ctx, base + "bibliography_suggestions.bib",
              "% Suggested entries from POST /bib/audit (registry metadata). Review each before replacing yours.\n\n"
              + "\n\n".join(suggestions) + "\n")
    s = audit.get("summary", {})
    print(f"{ctx.project_id}: {s.get('entries', len(audit['entries']))} entries — ok {s.get('ok', 0)}, "
          f"fix {s.get('fix', 0)}, unresolved {s.get('unresolved', 0)}; {len(rendered['references'])} references rendered")
    print(f"staged {base}bibliography_audit.md, REFERENCES.md (deliver to send them)")
    return 2 if s.get("fix") else 0


def render_audit(project_id: str, bib_path: str, doc: dict) -> str:
    esc = lambda s: str(s).replace("|", "\\|").replace("\n", " ")
    s = doc["summary"]
    out = [f"# Bibliography audit — {project_id}", "",
           f"`{bib_path}` checked by `POST /bib/audit`: DOIs against Crossref/DataCite/OpenAlex, missing DOIs "
           "searched, duplicates, the year in the key, VERIFY notes. The .bib is not changed; suggested entries are in "
           "`bibliography_suggestions.bib`.", "",
           f"**{s.get('entries', 0)} entries: {s.get('ok', 0)} ok · {s.get('fix', 0)} to fix · "
           f"{s.get('unresolved', 0)} unresolved**", "",
           "| key | status | verdict | DOI | suggested DOI | problems |", "|---|---|---|---|---|---|"]
    order = {"fix": 0, "unresolved": 1, "ok": 2}
    for e in sorted(doc["entries"], key=lambda e: (order.get(e["status"], 9), e["key"])):
        if e["status"] == "ok" and not e.get("warnings"):
            continue
        out.append(f"| {e['key']} | **{e['status']}** | {e.get('verdict') or ''} | {e.get('doi') or ''} | "
                   f"{e.get('suggested_doi') or ''} | {esc('; '.join(e.get('problems', []) + e.get('warnings', [])))} |")
    if doc.get("unresolved_keys"):
        out += ["", "Cited in the manuscript but not in the .bib: " + ", ".join(doc["unresolved_keys"])]
    if doc.get("uncited_entries"):
        out += ["", f"In the .bib but never cited ({len(doc['uncited_entries'])}): " + ", ".join(doc["uncited_entries"])]
    return "\n".join(out) + "\n"
