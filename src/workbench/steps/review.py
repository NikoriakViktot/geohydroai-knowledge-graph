"""review: one report of what stands between the manuscript and submission.

- the paper's own rules (``paths.review_rules``, ghai.review_rules/v1) run by the review engine;
- unresolved placeholders and markers left in the manuscript;
- the results of the theses, citations and bibliography steps, when they were run.
Report: <reviews>/workbench/REVIEW_REPORT.md and review_findings.json. Exit 2 when anything is
CRITICAL or MAJOR.
"""

from __future__ import annotations

import json
import re

import yaml

from src.workbench.delivery import sha256
from src.workbench.review import engine as E
from src.workbench.steps import Context, provenance, provenance_md, reports_dir, stage, staged_json
from src.workbench.steps.citations import manuscript_path

MARKERS = (r"\{\{[^{}\n]{1,200}\}\}", r"\[\[MISSING[^\]\n]*\]\]", r"\[PENDING[^\]\n]*\]",
           r"\[(?:TODO|TBD|VERIFY)\b[^\]\n]*\]")


def markers(text: str, label: str) -> list[E.Finding]:
    out = []
    for rx in MARKERS:
        for m in re.finditer(rx, text):
            out.append(E.Finding("MARKER", "MAJOR", f"{label}:{E.line_of(text, m.start())}",
                                 f"unresolved marker {m.group(0)[:120]}", "a submitted manuscript has no open markers",
                                 "resolve it from the paper's tables or the literature, or remove the sentence",
                                 "markers"))
    return out


def from_steps(staged: dict) -> list[E.Finding]:
    out = []
    th = staged.get("theses")
    for r in (th or {}).get("results", []):
        if r["status"] != "VALID":
            out.append(E.Finding("THESES", "MAJOR", r["path"], f"{r['kind']}: {r['status']} "
                                 f"({len(r.get('errors', []))} errors)", "contract v1 (POST /theses/validate)",
                                 "fix the document; see theses_validation.md", "theses"))
    cit = staged.get("citations")
    for v in (cit or {}).get("citations", []):
        if v["verdict"] in ("FIX", "OPEN"):
            out.append(E.Finding("CITATION", "MAJOR" if v["verdict"] == "FIX" else "MINOR",
                                 f"citation {v['key']} (lines {', '.join(map(str, v['lines'][:5]))})",
                                 f"{v['verdict']}: {v['reason']}", "every quotation found in its source (W1)",
                                 "see citation_verification.md", "citations"))
        if v.get("secondary_citation"):
            out.append(E.Finding("CITATION", "MINOR", f"citation {v['key']}",
                                 "the quoted passage itself cites other works", "cite the original (R-SCI-4)",
                                 "trace the original source", "citations"))
    bib = staged.get("bibliography")
    for e in (bib or {}).get("entries", []):
        if e["status"] != "ok":
            out.append(E.Finding("BIB", "MINOR" if e["status"] == "unresolved" else "MAJOR", f"bib {e['key']}",
                                 f"{e['status']}: {'; '.join(e.get('problems', []))[:300]}",
                                 "registry metadata (POST /bib/audit)", "see bibliography_audit.md", "bibliography"))
    for k in (bib or {}).get("unresolved_keys", []):
        out.append(E.Finding("BIB", "MAJOR", f"citation {k}", "cited but not in the .bib", "every citation resolves",
                             "add the entry (format_bib from its DOI)", "bibliography"))
    return out


def run(ctx: Context) -> int:
    m = ctx.manifest()
    if m is None:
        print(f"{ctx.project_id}: no manifest; run init first")
        return 1
    findings: list[E.Finding] = []
    inputs: dict[str, str] = {}
    rules_path = m.paths.review_rules
    if rules_path:
        raw = ctx.remote.read(rules_path)
        if raw is None:
            print(f"  review rules {rules_path} not in the repository: paper-specific checks skipped")
        else:
            inputs[rules_path] = sha256(raw)
            doc = yaml.safe_load(raw)
            paths = _rule_paths(doc)
            files = ctx.remote.pull(paths)
            inputs.update({p: sha256(d) for p, d in files.items()})
            findings += E.run(doc, lambda p: files[p].decode("utf-8") if p in files else None)
    ms_path = manuscript_path(m)
    if ms_path:
        raw = ctx.remote.read(ms_path)
        if raw is not None:
            inputs[ms_path] = sha256(raw)
            findings += markers(raw.decode("utf-8"), ms_path.rsplit("/", 1)[-1])
    base = reports_dir(ctx, m)
    staged = {"theses": staged_json(ctx, base + "theses_validation.json"),
              "citations": staged_json(ctx, base + "citation_verification.json"),
              "bibliography": staged_json(ctx, base + "bibliography_audit.json")}
    findings += from_steps(staged)
    findings.sort(key=lambda f: (E.SEVERITY_ORDER.get(f.severity, 9), f.check_id, f.location))
    prov = provenance(ctx, inputs)
    counts = {s: sum(1 for f in findings if f.severity == s) for s in E.SEVERITY_ORDER}
    stage(ctx, base + "review_findings.json", json.dumps({"project_id": ctx.project_id, "counts": counts,
                                                          "steps_included": [k for k, v in staged.items() if v],
                                                          "findings": E.as_dicts(findings), "provenance": prov},
                                                         indent=1, ensure_ascii=False) + "\n")
    head = [f"# Review report — {ctx.project_id}", "",
            f"**{counts['CRITICAL']} critical · {counts['MAJOR']} major · {counts['MINOR']} minor · {counts['INFO']} info.** "
            "Deterministic: the paper's rules"
            + (f" (`{rules_path}`)" if rules_path else "")
            + ", open markers, and the results of the steps run before this one: "
            + (", ".join(k for k, v in staged.items() if v) or "none") + ". Model-assessed checks (claim support, "
            "novelty) are not part of it yet.", ""]
    stage(ctx, base + "REVIEW_REPORT.md", "\n".join(head) + "\n" + E.render(findings, "Findings") + provenance_md(prov))
    print(f"{ctx.project_id}: " + ", ".join(f"{k} {v}" for k, v in counts.items()) + f"; staged {base}REVIEW_REPORT.md")
    return 2 if counts["CRITICAL"] or counts["MAJOR"] else 0


def _rule_paths(doc: dict) -> list[str]:
    paths = set()
    for section in ("files", "tables"):
        for spec in (doc.get(section) or {}).values():
            paths.add(spec["path"] if isinstance(spec, dict) else spec)
    for rule in doc.get("rules", []):
        if rule.get("phrases_from"):
            paths.add(rule["phrases_from"]["path"])
    return sorted(paths)
