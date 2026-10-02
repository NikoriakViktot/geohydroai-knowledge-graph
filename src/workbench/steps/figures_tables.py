"""tables / figures: inventories of the paper's tables and figures, with sha256 and caption checks.

tables  -> <reviews>/workbench/tables_inventory.json: per CSV its sha256, rows, columns; a Markdown
           rendering is staged next to every CSV that has none.
figures -> <reviews>/workbench/figures_inventory.json: per image its sha256 and whether the captions
           file describes it; captions without an image are reported too.
The paper's own manifests (e.g. floodstate-eo tables/manifest.json) are never touched.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
from pathlib import PurePosixPath

from src.workbench.assemble.render import parse_captions
from src.workbench.steps import Context, provenance, reports_dir, stage

IMAGES = (".png", ".jpg", ".jpeg", ".svg", ".pdf", ".tif", ".tiff")


def tables(ctx: Context) -> int:
    m = ctx.manifest()
    if m is None or not m.paths.tables:
        print(f"{ctx.project_id}: the manifest needs paths.tables")
        return 1
    d = m.paths.tables.rstrip("/")
    files = ctx.remote.pull([d])
    csvs = {p: b for p, b in files.items() if p.endswith(".csv") and str(PurePosixPath(p).parent) == d}
    entries = {}
    for path, data in sorted(csvs.items()):
        rows = list(csv.reader(io.StringIO(data.decode("utf-8"))))
        tid = PurePosixPath(path).stem
        entries[tid] = {"csv": path, "sha256": hashlib.sha256(data).hexdigest(),
                        "rows": max(0, len(rows) - 1), "columns": rows[0] if rows else []}
        md = f"{d}/{tid}.md"
        if md not in files and rows:
            stage(ctx, md, f"**{tid}**\n\n| " + " | ".join(rows[0]) + " |\n|" + "---|" * len(rows[0]) + "\n"
                  + "".join("| " + " | ".join(c.replace("|", "/") for c in r) + " |\n" for r in rows[1:]))
            entries[tid]["markdown"] = "rendered by the workbench"
    dest = reports_dir(ctx, m) + "tables_inventory.json"
    stage(ctx, dest, json.dumps({"tables": entries, "provenance": provenance(ctx, {})}, indent=1, ensure_ascii=False) + "\n")
    print(f"{ctx.project_id}: {len(entries)} tables; staged {dest}")
    return 0


def figures(ctx: Context) -> int:
    m = ctx.manifest()
    if m is None or not m.paths.figures:
        print(f"{ctx.project_id}: the manifest needs paths.figures")
        return 1
    d = m.paths.figures.rstrip("/")
    names = [n for n in ctx.remote.listdir(d) if n.lower().endswith(IMAGES)]
    state = ctx.remote.files([f"{d}/{n}" for n in names])
    captions = parse_captions((ctx.remote.read(m.paths.captions) or b"").decode("utf-8")) if m.paths.captions else {}
    ids = {}
    for n in sorted(names):
        fid = PurePosixPath(n).stem.split("_")[0]
        ids.setdefault(fid, []).append({"file": f"{d}/{n}", "sha256": state[f"{d}/{n}"]["sha256"]})
    report = {fid: {"files": files, "caption": fid in captions} for fid, files in ids.items()}
    orphans = sorted(set(captions) - set(ids))
    dest = reports_dir(ctx, m) + "figures_inventory.json"
    stage(ctx, dest, json.dumps({"figures": report, "captions_without_image": orphans, "provenance": provenance(ctx, {})},
                                indent=1, ensure_ascii=False) + "\n")
    missing = [fid for fid, r in report.items() if not r["caption"]]
    print(f"{ctx.project_id}: {len(ids)} figures, {len(missing)} without a caption, {len(orphans)} captions without "
          f"an image; staged {dest}")
    return 2 if missing or orphans else 0
