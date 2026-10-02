"""pull: copy the inputs the manifest declares into data/workbench/<project>/in/, with their sha256.

The copy is what later steps read (theses, citations, bibliography, assembly), so a step never
reads a file that changed under it, and every output can name the exact inputs and the paper
repository's HEAD it came from (in/PULL_MANIFEST.json).
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone

from src.workbench.delivery import sha256
from src.workbench.steps import Context, project_dir


def run(ctx: Context) -> int:
    m = ctx.manifest()
    if m is None:
        print(f"{ctx.project_id}: no manifest at {ctx.target.get('manifest_path')}; run init first")
        return 1
    declared = {k: v.rstrip("/") for k, v in m.paths.model_dump().items() if v and "{" not in v}
    files = ctx.remote.pull(sorted(set(declared.values())))
    in_dir = project_dir(ctx.project_id) / "in"
    if in_dir.exists():
        shutil.rmtree(in_dir)
    for rel, data in files.items():
        dest = in_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
    record = {
        "project_id": ctx.project_id,
        "pulled_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "repository": {"distro": ctx.target["distro"], "path": ctx.target["repo_path"], **ctx.remote.git_state()},
        "declared": declared,
        "missing": sorted(k for k, v in declared.items() if not any(f == v or f.startswith(v + "/") for f in files)),
        "files": {rel: {"sha256": sha256(data), "bytes": len(data)} for rel, data in sorted(files.items())},
    }
    in_dir.mkdir(parents=True, exist_ok=True)
    (in_dir / "PULL_MANIFEST.json").write_text(json.dumps(record, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"{ctx.project_id}: pulled {len(files)} files ({sum(len(d) for d in files.values()) / 1e6:.1f} MB) "
          f"into {in_dir}; missing inputs: {', '.join(record['missing']) or 'none'}")
    return 0
