"""The paper workbench: one place that builds papers for their repositories.

    python -m src.workbench projects                          # the registry of papers
    python -m src.workbench <project_id> status               # where the paper stands
    python -m src.workbench <project_id> init     [--dry-run] # manifest, .mcp.json, CLAUDE.md section; register
    python -m src.workbench <project_id> pull                 # declared inputs -> data/workbench/<project>/in/
    python -m src.workbench <project_id> deliver  [--from-inventory] [--dry-run] [--force]

Every step that writes into a paper repository plans first, refuses to overwrite work that is not
committed (unless --force) and verifies what it wrote; the human commits there. ``--to DIR`` runs a
step against a scratch directory instead of the repository (nothing is recorded).
The steps that build a paper (passport, theses, literature, citations, bibliography, analysis,
figures, tables, assemble, translate, review) follow in docs/api/PAPER_WORKFLOW.md.
"""

from __future__ import annotations

import argparse
import sys

STEPS = ("status", "init", "pull", "deliver")


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["projects"]:
        from src.workbench import registry
        for r in registry.all_projects():
            last = r.get("last_delivery_at")
            print(f"{r['project_id']:<26} {r.get('repo') or '-':<18} {r.get('repo_path') or '-':<36} "
                  f"manifest {r.get('manifest_path') or '-':<48} last delivery {last:%Y-%m-%d %H:%M}" if last else
                  f"{r['project_id']:<26} {r.get('repo') or '-':<18} {r.get('repo_path') or '-':<36} "
                  f"manifest {r.get('manifest_path') or '-'}")
        return 0
    ap = argparse.ArgumentParser(prog="python -m src.workbench", description=__doc__.split("\n\n")[0])
    ap.add_argument("project_id")
    ap.add_argument("step", choices=STEPS)
    ap.add_argument("--dry-run", action="store_true", help="plan only; write nothing, record nothing")
    ap.add_argument("--force", action="store_true", help="overwrite files that differ and are not clean tracked files")
    ap.add_argument("--to", metavar="DIR", help="a scratch directory standing in for the paper repository")
    ap.add_argument("--from-inventory", action="store_true", help="deliver: the P6 freeze rows of this project")
    args = ap.parse_args(argv)

    from src.workbench.steps import context
    ctx = context(args.project_id, to=args.to)
    if args.step == "status":
        from src.workbench.steps import status
        return status.run(ctx)
    if args.step == "init":
        from src.workbench.steps import init
        return init.run(ctx, dry_run=args.dry_run, force=args.force)
    if args.step == "pull":
        from src.workbench.steps import pull
        return pull.run(ctx)
    from src.workbench.steps import deliver
    return deliver.run(ctx, from_inventory=args.from_inventory, dry_run=args.dry_run, force=args.force)


if __name__ == "__main__":
    sys.exit(main())
