"""Workbench steps, one module each, in the order a paper is built (src/workbench/__main__.py)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from src.workbench import manifest as M
from src.workbench import registry
from src.workbench.decommission import ROOT
from src.workbench.remote import LocalRunner, RemoteRepo

WORKBENCH_DIR = ROOT / "data" / "workbench"


def slug(project_id: str) -> str:
    return project_id.replace(":", "__")


def project_dir(project_id: str) -> Path:
    return WORKBENCH_DIR / slug(project_id)


@dataclass
class Context:
    project_id: str
    target: dict                  # registry.target(): repo_path, distro, publication_dir, manifest_path, public …
    remote: RemoteRepo
    local_target: bool = False    # --to DIR: a scratch directory instead of the paper repository

    @property
    def public(self) -> bool:
        return bool(self.target.get("public", True))

    def manifest(self) -> M.ProjectManifest | None:
        path = self.target.get("manifest_path")
        raw = self.remote.read(path) if path else None
        return M.load(raw.decode("utf-8")) if raw else None


def context(project_id: str, to: str | None = None, runner=None) -> Context:
    target = registry.target(project_id)
    if to:              # the target stays the real repository; only the files go to the scratch directory
        path = str(Path(to).resolve())
        Path(path).mkdir(parents=True, exist_ok=True)
        return Context(project_id, target, RemoteRepo("local", path, runner=runner or LocalRunner()),
                       local_target=True)
    return Context(project_id, target, RemoteRepo(target["distro"], target["repo_path"], runner=runner))


# ── outputs of the checking steps ─────────────────────────────────────────────

def reports_dir(ctx: Context, m: M.ProjectManifest | None) -> str:
    """Where the workbench's reports go in the paper repository: <reviews>/workbench/."""
    base = m.paths.reviews if m and m.paths.reviews else f"{ctx.target['publication_dir']}/reviews/"
    return base.rstrip("/") + "/workbench/"


def stage(ctx: Context, dest: str, data: bytes | str) -> Path:
    """Write a delivery-ready file into data/workbench/<project>/out/<dest> (deliver sends it)."""
    path = project_dir(ctx.project_id) / "out" / dest
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))
    return path


def staged_json(ctx: Context, dest: str) -> dict | None:
    path = project_dir(ctx.project_id) / "out" / dest
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def provenance(ctx: Context, inputs: dict[str, str], api: dict | None = None) -> dict:
    """What an output was made from: knowledge commit, paper repository HEAD, input sha256, API provenance."""
    from src.workbench.delivery import knowledge_commit
    return {"generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "knowledge_commit": knowledge_commit(), "repository": ctx.remote.git_state(),
            "inputs": inputs, "api": api or {}}


def provenance_md(prov: dict) -> str:
    repo = prov.get("repository") or {}
    api = prov.get("api") or {}
    lines = ["", "---", "", "**Provenance.** Generated "
             f"{prov['generated_at']} by the GeoHydroAI paper workbench (knoweledg_graf "
             f"`{str(prov.get('knowledge_commit'))[:12]}`) from this repository at `{str(repo.get('commit'))[:12]}`"
             + (f"; corpus manifest `{str(api.get('corpus_manifest_id'))[:16]}`" if api.get("corpus_manifest_id") else "")
             + ". Inputs (sha256):"]
    lines += [f"- `{path}` {sha[:16]}…" for path, sha in sorted(prov.get("inputs", {}).items())]
    return "\n".join(lines) + "\n"
