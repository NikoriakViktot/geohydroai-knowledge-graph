"""``ghai.project.yaml``: what a paper repository tells the workbench (docs/api/PROJECT_MANIFEST.md).

The manifest lives in the paper's repository, next to the paper (``manifest_path`` in the
registry), and is committed there. Every path in it is relative to the repository root.
Built outputs go to ``publication_dir`` unless a path says otherwise.
"""

from __future__ import annotations

import hashlib
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.contracts.research import ProjectId

SCHEMA = "ghai.project/v1"
#: never leaves the knowledge repository, whatever a manifest says
ALWAYS_NEVER = ("**/.env", "**/.env.*", "**/*.key", "**/*:Zone.Identifier", "**/__pycache__/**", "**/*.pyc")
#: binary builds that a public repository keeps out of git (they go to an ignored private/ folder)
PUBLIC_BINARY = ("**/*.pdf", "**/*.docx", "**/*.doc", "**/*.tif", "**/*.tiff", "**/*.tiff.*", "**/*.zip")


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _rel(value: str | None) -> str | None:
    if value is None:
        return None
    v = value.strip()
    if not v or v.startswith("/") or ".." in v.split("/") or "\\" in v:
        raise ValueError(f"must be a path relative to the repository root without '..': {value!r}")
    return v


class Repo(_Model):
    distro: str = "Ubuntu-24.04"
    path: str = Field(description="absolute path of the repository inside the distribution")
    remote: str | None = None
    public: bool = Field(default=True, description="public repositories receive text artefacts only")

    @field_validator("path")
    @classmethod
    def _absolute(cls, v: str) -> str:
        if not v.startswith("/"):
            raise ValueError("repo.path must be absolute")
        return v.rstrip("/")


class Paths(_Model):
    manuscript_templates: str | None = Field(default=None, description="directory of section templates (*.md, in name order)")
    manuscript_out: str | None = Field(default=None, description="built manuscript; {lang} is replaced")
    theses: str | None = None
    atomic_claims: str | None = None
    bib: str | None = None
    own_evidence: str | None = Field(default=None, description="the paper's own numbers (OWN_EVIDENCE.csv)")
    tables: str | None = None
    figures: str | None = None
    captions: str | None = None
    reviews: str | None = None
    passport: str | None = None
    literature_audit: str | None = Field(default=None, description="directory of the literature evidence run")

    @field_validator("*")
    @classmethod
    def _relative(cls, v: str | None) -> str | None:
        return _rel(v)


class Placeholders(_Model):
    dialect: Literal["ghai", "floodstate_fill", "none"] = "ghai"


class Citation(_Model):
    style: Literal["apa", "agu", "copernicus", "elsevier-harvard"] = "apa"


class Analysis(_Model):
    runner: Literal["consumer", "none"] = "none"
    command: str | None = Field(default=None, description="run in the repository root by the `analysis` step")


class Literature(_Model):
    chroma_collection: str = "flood_papers_768d_v2"


class ProjectManifest(_Model):
    schema_: Literal["ghai.project/v1"] = Field(default=SCHEMA, alias="schema")
    project_id: ProjectId
    title: str | None = None
    repo: Repo
    publication_dir: str
    paths: Paths = Paths()
    placeholders: Placeholders = Placeholders()
    citation: Citation = Citation()
    analysis: Analysis = Analysis()
    literature: Literature = Literature()
    languages: list[str] = ["en"]
    never_deliver: list[str] = Field(default=["_work/**"], description="globs, repository-relative")

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    @field_validator("publication_dir")
    @classmethod
    def _relative(cls, v: str) -> str:
        return _rel(v)

    def blocked(self) -> tuple[str, ...]:
        """Globs no delivery may write, whatever the step."""
        return tuple(self.never_deliver) + ALWAYS_NEVER


def load(text: str) -> ProjectManifest:
    data = yaml.safe_load(text) or {}
    if not isinstance(data, dict):
        raise ValueError("ghai.project.yaml must be a mapping")
    return ProjectManifest.model_validate(data)


def dump(m: ProjectManifest) -> str:
    data = m.model_dump(mode="json", by_alias=True, exclude_none=True)
    head = (f"# {m.title or m.project_id}: how the GeoHydroAI paper workbench builds this paper.\n"
            "# Schema: docs/api/PROJECT_MANIFEST.md of the knowledge repository. Paths are relative to the\n"
            "# repository root. No keys or secrets belong here: the repository may be public.\n")
    return head + yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=100)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
