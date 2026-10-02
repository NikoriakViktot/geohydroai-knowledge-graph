"""init: connect a paper repository to the workbench and the Knowledge API, once.

Writes, through an ordinary verified delivery (so it is planned, checked and recorded):
- the paper's ``ghai.project.yaml`` at its manifest path, unless one exists (the paper owns it);
- ``.mcp.json`` at the repository root with the ``ghai`` server (merged into an existing file);
- a section of the repository's ``CLAUDE.md`` between ``<!-- ghai:begin -->`` and ``<!-- ghai:end -->``,
  listing every paper of that repository.
Then the paper is registered in project.project. No key is ever written: ``.mcp.json`` refers to
``${GHAI_API_KEY}``, which lives in ``~/.config/ghai/env`` of the distribution.
"""

from __future__ import annotations

import json
import re

from src.workbench import manifest as M
from src.workbench import registry
from src.workbench.delivery import Item, make_plan, render, write
from src.workbench.steps import Context

MCP_URL = "http://127.0.0.1:8090/mcp"
MCP_SERVER = {"type": "http", "url": MCP_URL, "headers": {"X-API-Key": "${GHAI_API_KEY}"}}
BEGIN, END = "<!-- ghai:begin -->", "<!-- ghai:end -->"


def build_manifest(ctx: Context) -> M.ProjectManifest:
    t = ctx.target
    return M.ProjectManifest(
        project_id=ctx.project_id, title=t.get("paper_label"),
        repo=M.Repo(distro=t["distro"], path=t["repo_path"], public=bool(t.get("public", True))),
        publication_dir=t["publication_dir"], paths=M.Paths(**t.get("seed_paths", {})),
        placeholders=M.Placeholders(dialect=t.get("seed_dialect", "ghai")),
    )


def mcp_json(existing: bytes | None) -> bytes:
    """Add the ghai server; a file that already has exactly this server is returned byte for byte."""
    doc = json.loads(existing) if existing else {}
    servers = doc.setdefault("mcpServers", {})
    if existing and servers.get("ghai") == MCP_SERVER:
        return existing
    servers["ghai"] = MCP_SERVER
    return (json.dumps(doc, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def claude_section(papers: list[tuple[str, str]]) -> str:
    rows = "\n".join(f"- `{pid}`: manifest `{path}`" for pid, path in papers)
    return f"""{BEGIN}
## Literature and the paper workbench (GeoHydroAI Knowledge API)

Papers of this repository built with the workbench of the knowledge repository (knoweledg_graf):
{rows}

- Literature facts come only from the `ghai` MCP tools (`.mcp.json`; the key is `GHAI_API_KEY` in
  `~/.config/ghai/env`, never in this repository). Read the resources `ghai://docs/AGENT_RULES` and
  `ghai://docs/PAPER_WORKFLOW` before writing from the literature.
- Never quote a paper from memory: use `get_paper_text` or `verify_quotes`, and record
  `corpus_manifest_id` and span ids for every citation you check.
- Manuscripts, tables, figures, bibliography and reviews are built in the knowledge repository and
  delivered here with a manifest under `.ghai/deliveries/`; review `git status` and commit. Files
  edited by hand after a delivery are reported as conflicts by the next one, never overwritten.
- The paper's own numbers stay in this repository (its analysis scripts and tables); the knowledge
  repository holds the literature.
{END}
"""


def claude_md(existing: bytes | None, papers: list[tuple[str, str]]) -> bytes:
    section = claude_section(papers)
    text = existing.decode("utf-8") if existing else ""
    if BEGIN in text and END in text:
        text = re.sub(re.escape(BEGIN) + r".*?" + re.escape(END) + r"\n?", section, text, count=1, flags=re.S)
    else:
        text = (text.rstrip("\n") + "\n\n" if text else "") + section
    return text.encode("utf-8")


def papers_of_repo(ctx: Context) -> list[tuple[str, str]]:
    repo_path = ctx.target["repo_path"]
    found = {ctx.project_id: ctx.target["manifest_path"]}
    try:
        rows = registry.all_projects()
    except Exception:
        rows = []
    for r in rows:              # papers of the same repository that were initialised before
        if r.get("repo_path") == repo_path and r.get("manifest_path") and r["project_id"] != ctx.project_id:
            found[r["project_id"]] = r["manifest_path"]
    return sorted(found.items())


def run(ctx: Context, *, dry_run: bool = False, force: bool = False) -> int:
    t = ctx.target
    manifest_path = t["manifest_path"]
    current = ctx.manifest()
    m = current or build_manifest(ctx)
    text = M.dump(m)
    items = [] if current else [Item(manifest_path, text.encode("utf-8"), "workbench:init", "deliver")]
    items.append(Item(".mcp.json", mcp_json(ctx.remote.read(".mcp.json")), "workbench:init", "deliver"))
    items.append(Item("CLAUDE.md", claude_md(ctx.remote.read("CLAUDE.md"), papers_of_repo(ctx)), "workbench:init",
                      "deliver"))
    plan = make_plan(ctx.project_id, ctx.remote, items, public=ctx.public, never=tuple(m.blocked()), force=force)
    print(render(plan))
    if current:
        print(f"  manifest {manifest_path} exists in the repository: kept as is (the paper owns it)")
    if dry_run:
        print("dry run: nothing written, nothing registered")
        return 2 if plan.by_kind("conflict", "blocked") else 0
    result = write(plan, ctx.remote)
    print(f"written {result['written']} files; manifest {result['manifest']}")
    if not ctx.local_target:
        raw = text if not current else (ctx.remote.read(manifest_path) or b"").decode("utf-8")
        registry.upsert(ctx.project_id, repo=t.get("repo"), paper_label=m.title, distro=m.repo.distro,
                        repo_path=m.repo.path, publication_dir=m.publication_dir, public=m.repo.public,
                        manifest_path=manifest_path, manifest_sha256=M.sha256_text(raw))
        print(f"registered {ctx.project_id} in project.project")
    return 2 if plan.by_kind("conflict", "blocked") else 0
