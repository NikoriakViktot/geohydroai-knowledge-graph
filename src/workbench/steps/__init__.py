"""Workbench steps, one module each, in the order a paper is built (src/workbench/__main__.py)."""

from __future__ import annotations

from dataclasses import dataclass
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
