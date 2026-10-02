"""literature: the evidence run for the paper's atomic claims (from tools/paper3_audit, src/workbench/literature).

    python -m src.workbench <project_id> literature <command> [options]
        prepare | retrieve | select | screen | references | enrich-retained | kakhovka-numbers | export | report

The run reads the paper's theses (paths.theses) and its literature audit directory
(paths.literature_audit: atomic_claims.yaml, overrides.yaml, novelty_verdicts.yaml, …) and writes the
deliverables 01–10 there. Locations:
- the audit directory is staged at data/workbench/<project>/out/<literature_audit>/, pulled from the
  repository on the first run (or with --refresh); ``deliver`` sends what changed;
- caches and intermediates stay in data/workbench/<project>/work/literature/ and are never delivered;
  --seed-work DIR copies an earlier run's _work there once;
- the vector collection is the manifest's literature.chroma_collection.
Corpus access is still direct (Chroma, normalized texts); POST /theses/evidence-run will replace it.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from src.workbench.decommission import ROOT
from src.workbench.steps import Context, project_dir

CHROMA_DIRS = {"flood_papers_768d_v2": ROOT / ".chromadb_v2", "flood_papers_768d": ROOT / ".chromadb"}


def paths(ctx: Context, m, refresh: bool = False):
    from src.workbench.literature import config as cfg
    audit = m.paths.literature_audit.rstrip("/")
    out_dir = project_dir(ctx.project_id) / "out" / audit
    work_dir = project_dir(ctx.project_id) / "work" / "literature"
    if refresh or not out_dir.exists():
        files = ctx.remote.pull([audit])
        for rel, data in files.items():
            if "/_work/" in rel or rel.endswith(":Zone.Identifier"):
                continue
            dest = project_dir(ctx.project_id) / "out" / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
    theses = project_dir(ctx.project_id) / "in" / m.paths.theses
    raw = ctx.remote.read(m.paths.theses)
    if raw is not None:
        theses.parent.mkdir(parents=True, exist_ok=True)
        theses.write_bytes(raw)
    collection = m.literature.chroma_collection
    return cfg.Paths(pub_dir=theses.parent.parent, out_dir=out_dir, work_dir=work_dir, theses_json=theses,
                     chroma_dir=CHROMA_DIRS.get(collection, ROOT / ".chromadb_v2"), collection=collection,
                     distro=ctx.target["distro"], repo=ctx.target["repo_path"], bib=m.paths.bib or "docs/references.bib",
                     terminology_test="tests/test_terminology_freeze.py")


def run(ctx: Context, argv: list[str], *, refresh: bool = False, seed_work: str | None = None) -> int:
    m = ctx.manifest()
    if m is None or not m.paths.literature_audit or not m.paths.theses:
        print(f"{ctx.project_id}: the manifest needs paths.literature_audit and paths.theses")
        return 1
    from src.workbench.literature import cli, config as cfg
    p = paths(ctx, m, refresh=refresh)
    if seed_work:
        src = Path(seed_work)
        if p.work_dir.exists() and any(p.work_dir.iterdir()):
            print(f"{p.work_dir} is not empty; --seed-work only fills an empty work directory")
            return 1
        shutil.copytree(src, p.work_dir, dirs_exist_ok=True)
        for f in p.work_dir.rglob("*"):
            f.chmod(f.stat().st_mode | 0o200)
        print(f"seeded {p.work_dir} from {src}")
    p.work_dir.mkdir(parents=True, exist_ok=True)
    cfg.configure(p)
    print(f"{ctx.project_id}: literature run — audit {p.out_dir}, work {p.work_dir}, collection {p.collection}")
    if not argv:
        print("give a command: prepare | retrieve | select | screen | references | enrich-retained | "
              "kakhovka-numbers | export | report")
        return 1
    return cli.main(argv)
