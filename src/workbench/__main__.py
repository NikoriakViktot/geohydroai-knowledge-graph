"""The paper workbench: one place that builds papers for their repositories.

    python -m src.workbench projects                          # the registry of papers
    python -m src.workbench <project_id> status               # where the paper stands
    python -m src.workbench <project_id> init     [--dry-run] # manifest, .mcp.json, CLAUDE.md section; register
    python -m src.workbench <project_id> pull                 # declared inputs -> data/workbench/<project>/in/
    python -m src.workbench <project_id> theses               # contract v1 check of theses + atomic claims
    python -m src.workbench <project_id> citations            # W1: citations -> quotes verified in the sources
    python -m src.workbench <project_id> bibliography         # W2: .bib audit + rendered reference list
    python -m src.workbench <project_id> review               # the paper's rules + markers + the steps above
    python -m src.workbench <project_id> tables | figures     # inventories with sha256 and caption checks
    python -m src.workbench <project_id> assemble [--final]   # manuscript + OPEN_ITEMS + docx; --final = gate
    python -m src.workbench <project_id> translate            # other languages, numbers locked
    python -m src.workbench <project_id> literature <command> # the evidence run: prepare … export, report
    python -m src.workbench <project_id> deliver  [--from-inventory] [--dry-run] [--force]

Checking steps stage their reports in data/workbench/<project>/out/<reviews>/workbench/; deliver sends them.

Every step that writes into a paper repository plans first, refuses to overwrite work that is not
committed (unless --force) and verifies what it wrote; the human commits there. ``--to DIR`` runs a
step against a scratch directory instead of the repository (nothing is recorded).
The remaining steps (passport, analysis) follow; the whole sequence is docs/api/PAPER_WORKFLOW.md.
"""

from __future__ import annotations

import argparse
import sys

STEPS = ("status", "init", "pull", "theses", "citations", "bibliography", "review", "tables", "figures",
         "assemble", "translate", "literature", "deliver")


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
    ap.add_argument("--final", action="store_true", help="assemble: refuse a manuscript with open markers")
    ap.add_argument("--refresh", action="store_true", help="literature: pull the audit directory again")
    ap.add_argument("--seed-work", metavar="DIR", help="literature: copy an earlier run's _work once")
    args, rest = ap.parse_known_args(argv)
    if rest and args.step != "literature":
        ap.error(f"unrecognized arguments: {' '.join(rest)}")

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
    if args.step in ("theses", "citations", "bibliography", "review", "translate"):
        import importlib
        return importlib.import_module(f"src.workbench.steps.{args.step}").run(ctx)
    if args.step in ("tables", "figures"):
        from src.workbench.steps import figures_tables
        return getattr(figures_tables, args.step)(ctx)
    if args.step == "assemble":
        from src.workbench.steps import assemble
        return assemble.run(ctx, final=args.final)
    if args.step == "literature":          # --force / --dry-run belong to the literature command here
        from src.workbench.steps import literature
        rest = [x for x in rest if x != "--"] + ["--force"] * args.force + ["--dry-run"] * args.dry_run
        return literature.run(ctx, rest, refresh=args.refresh, seed_work=args.seed_work)
    from src.workbench.steps import deliver
    return deliver.run(ctx, from_inventory=args.from_inventory, dry_run=args.dry_run, force=args.force)


if __name__ == "__main__":
    sys.exit(main())
