"""status: where a paper stands — registry, repository, manifest, declared inputs, P6 progress."""

from __future__ import annotations

from collections import Counter

from src.workbench import decommission as D
from src.workbench.steps import Context
from src.workbench.steps.deliver import FREEZE


def run(ctx: Context) -> int:
    t = ctx.target
    print(f"{ctx.project_id}: {t.get('paper_label') or ''}")
    print(f"  registry   {'registered' if t['registered'] else 'not registered (run init)'}; "
          f"{t['distro']}:{t['repo_path']}; publication_dir {t.get('publication_dir')}; "
          f"{'public' if ctx.public else 'private'} repository")
    git = ctx.remote.git_state()
    print(f"  repository HEAD {str(git.get('commit') or '-')[:10]} on {git.get('branch') or '-'}, "
          f"{git.get('dirty_paths')} dirty paths")
    try:
        m = ctx.manifest()
    except Exception as exc:  # an invalid manifest is reported, not fixed
        print(f"  manifest   {t.get('manifest_path')}: INVALID — {exc}")
        m = None
    else:
        print(f"  manifest   {t.get('manifest_path')}: " + ("ok" if m else "missing (run init)"))
    if m:
        declared = {k: v for k, v in m.paths.model_dump().items() if v}
        state = ctx.remote.files([v.rstrip("/") for v in declared.values() if "{" not in v])
        ok = []
        for key, path in declared.items():
            if "{" in path:
                ok.append(f"{key}=template")
                continue
            got = state.get(path.rstrip("/"), {})
            present = got.get("sha256") or got.get("git") not in ("absent", None)
            ok.append(f"{key}={'ok' if present else 'missing'}")
        print("  inputs     " + ", ".join(ok))
    rows = [r for r in D.read_inventory(FREEZE) if r["project"] == ctx.project_id]
    if rows:
        counts = Counter((r["disposition"], r["status"]) for r in rows)
        done = len(rows) - len(D.gate(rows))
        print(f"  P6 freeze  {done}/{len(rows)} files accounted for: "
              + ", ".join(f"{d}/{s} {n}" for (d, s), n in sorted(counts.items())))
    return 0
