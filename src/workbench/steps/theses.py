"""theses: validate the paper's theses and atomic claims against contract v1 (POST /theses/validate).

Nothing is coerced: every problem comes back located with the document's own field names, to be
fixed in the paper's repository (R-DATA-1). Report: <reviews>/workbench/theses_validation.{md,json}.
Importing into the layer of truth is POST /bundles/import (planned); until then the rows of
2026-10-02 stay as the P6 import left them.
"""

from __future__ import annotations

import json

from src.workbench import client
from src.workbench.delivery import sha256
from src.workbench.steps import Context, provenance, provenance_md, reports_dir, stage


def run(ctx: Context) -> int:
    m = ctx.manifest()
    if m is None:
        print(f"{ctx.project_id}: no manifest; run init first")
        return 1
    api = client.api()
    results, inputs, api_prov = [], {}, {}
    for kind, path in (("theses", m.paths.theses), ("atomic_claims", m.paths.atomic_claims)):
        if not path:
            continue
        raw = ctx.remote.read(path)
        if raw is None:
            results.append({"kind": kind, "path": path, "status": "MISSING"})
            continue
        inputs[path] = sha256(raw)
        text = raw.decode("utf-8")
        document = json.loads(text) if path.endswith(".json") else text
        try:
            body = api.theses.validate(kind, ctx.project_id, document)
            api_prov = body.get("provenance") or api_prov
            results.append({"kind": kind, "path": path, "status": "VALID", "counts": body.get("counts", {}),
                            "warnings": body.get("warnings", [])})
        except client.GHAIError as exc:
            problem = exc.problem or {}
            results.append({"kind": kind, "path": path, "status": exc.code, "detail": exc.detail,
                            "errors": problem.get("errors", []), "counts": problem.get("counts", {}),
                            "warnings": problem.get("warnings", [])})
    prov = provenance(ctx, inputs, api_prov)
    base = reports_dir(ctx, m)
    stage(ctx, base + "theses_validation.json",
          json.dumps({"project_id": ctx.project_id, "results": results, "provenance": prov}, indent=1,
                     ensure_ascii=False) + "\n")
    stage(ctx, base + "theses_validation.md", render(ctx.project_id, results) + provenance_md(prov))
    for r in results:
        print(f"{r['kind']:<14} {r['status']:<18} {r['path']}  "
              + (f"{r.get('counts')}" if r["status"] == "VALID" else f"{len(r.get('errors', []))} errors"))
    print(f"staged {base}theses_validation.md (deliver to send it)")
    return 0 if all(r["status"] == "VALID" for r in results) else 2


def render(project_id: str, results: list[dict]) -> str:
    esc = lambda s: str(s).replace("|", "\\|").replace("\n", " ")
    out = [f"# Theses and atomic claims — validation against contract v1 ({project_id})", "",
           "Checked by `POST /theses/validate` of the GeoHydroAI Knowledge API. Nothing is coerced: fix the "
           "source document; the locations use its own field names.", ""]
    for r in results:
        out += [f"## `{r['path']}` ({r['kind']}): **{r['status']}**", ""]
        if r.get("detail"):
            out += [r["detail"], ""]
        if r.get("counts"):
            out += ["Counts: " + ", ".join(f"{k} {v}" for k, v in r["counts"].items()), ""]
        if r.get("errors"):
            out += ["| location | problem | type |", "|---|---|---|"]
            out += [f"| `{' → '.join(map(str, e.get('loc', [])))}` | {esc(e.get('msg', ''))} | {e.get('type', '')} |"
                    for e in r["errors"]]
            out.append("")
        for w in r.get("warnings", []):
            out.append(f"- warning: {w}")
        out.append("")
    return "\n".join(out)
