"""The registry of papers (project.project, migration 0005): where each repository is, what was delivered.

``SEED`` holds the locations known on 2026-10-02 (the author's decisions). ``init`` writes them
into the registry and into the paper's own ``ghai.project.yaml``; after that the manifest in the
paper repository is the source of truth and the registry mirrors it.
"""

from __future__ import annotations

from datetime import datetime, timezone

DISTRO = "Ubuntu-24.04"
REPO_ROOT = "/home/niko/repo"

SEED: dict[str, dict] = {
    "floodstate-eo:paper3": {
        "repo": "floodstate-eo", "repo_path": f"{REPO_ROOT}/floodstate-eo", "public": True,
        "paper_label": "Paper 3 — daily inundation of the Kakhovka breach (U-Net)",
        "publication_dir": "case_studies/kakhovka_2023/publication",
        "manifest_path": "case_studies/kakhovka_2023/ghai.project.yaml",
        "dialect": "floodstate_fill",
        "paths": {
            "manuscript_templates": "case_studies/kakhovka_2023/publication/manuscript_template.md",
            "manuscript_out": "case_studies/kakhovka_2023/publication/manuscript.md",
            "theses": "case_studies/kakhovka_2023/publication/literature/theses.json",
            "atomic_claims": "case_studies/kakhovka_2023/literature_audit/atomic_claims.yaml",
            "bib": "docs/references.bib",
            "tables": "case_studies/kakhovka_2023/publication/tables/",
            "figures": "case_studies/kakhovka_2023/publication/figures/",
            "captions": "case_studies/kakhovka_2023/publication/captions.md",
            "reviews": "case_studies/kakhovka_2023/reviews/",
            "passport": "case_studies/kakhovka_2023/publication/PASSPORT.md",
            "literature_audit": "case_studies/kakhovka_2023/literature_audit/",
        },
    },
    "article1": {
        "repo": "floodstate-eo", "repo_path": f"{REPO_ROOT}/floodstate-eo", "public": True,
        "paper_label": "Article 1 — flood-mapping methods review",
        "publication_dir": "articles/flood_mapping_methods_review",
        "manifest_path": "articles/flood_mapping_methods_review/ghai.project.yaml",
        "dialect": "none",
        "paths": {
            "manuscript_out": "articles/flood_mapping_methods_review/manuscript/Article_1_Flood_Mapping_Methods_V3.md",
            "theses": "articles/flood_mapping_methods_review/literature/theses_v4.json",
            "tables": "articles/flood_mapping_methods_review/publication/tables/",
            "figures": "articles/flood_mapping_methods_review/publication/figures/",
            "reviews": "articles/flood_mapping_methods_review/reviews/",
            "passport": "articles/flood_mapping_methods_review/PASSPORT.md",
        },
    },
    "kakhovka-terrain:paper2": {
        "repo": "kakhovka-terrain", "repo_path": f"{REPO_ROOT}/kakhovka-terrain", "public": True,
        "paper_label": "Paper 2 — bed DEM, terrain and roughness; also the vegetation/roughness paper",
        "publication_dir": "case_studies/kakhovka/publication",
        "manifest_path": "case_studies/kakhovka/ghai.project.yaml",
        "dialect": "ghai",
        "paths": {
            "manuscript_templates": "case_studies/kakhovka/publication/templates/",
            "manuscript_out": "case_studies/kakhovka/publication/manuscript_draft.md",
            "theses": "case_studies/kakhovka/publication/literature/theses.json",
            "atomic_claims": "case_studies/kakhovka/publication/literature/atomic_claims.yaml",
            "bib": "case_studies/kakhovka/publication/literature/references_p74.bib",
            "tables": "case_studies/kakhovka/publication/tables/",
            "figures": "case_studies/kakhovka/publication/figures/",
            "captions": "case_studies/kakhovka/publication/captions.md",
            "passport": "case_studies/kakhovka/publication/PASSPORT.md",
        },
    },
    "swot-dnipro:paper1": {
        "repo": "SWOT-DNIPRO", "repo_path": f"{REPO_ROOT}/SWOT-DNIPRO", "public": True,
        "paper_label": "Paper 1 — water-surface geometry of the former reservoir",
        "publication_dir": "outputs/paper",
        "manifest_path": "ghai.project.yaml",
        "dialect": "ghai",
        "paths": {
            "manuscript_templates": "outputs/paper/templates/",
            "manuscript_out": "outputs/paper/knowledge_repo/paper1_manuscript_{lang}.md",
            "own_evidence": "outputs/paper/knowledge_repo/evidence/OWN_EVIDENCE.csv",
            "tables": "outputs/paper/tables/",
            "figures": "outputs/paper/figures/",
            "passport": "outputs/paper/PASSPORT.md",
        },
    },
}
FIELDS = ("repo", "paper_label", "note", "distro", "repo_path", "publication_dir", "public", "manifest_path",
          "manifest_sha256", "last_delivery_at", "last_delivery_commit", "last_delivery", "updated_at")


def _row(p) -> dict:
    return {"project_id": p.project_id, **{f: getattr(p, f) for f in FIELDS}}


def get(project_id: str) -> dict | None:
    from src.db.engine import session_scope
    from src.db.models import Project
    with session_scope() as s:
        p = s.get(Project, project_id)
        return _row(p) if p else None


def all_projects() -> list[dict]:
    from sqlalchemy import select

    from src.db.engine import session_scope
    from src.db.models import Project
    with session_scope() as s:
        return [_row(p) for p in s.scalars(select(Project).order_by(Project.project_id))]


def target(project_id: str) -> dict:
    """Where the paper lives: the registry row, completed from SEED where it is still empty."""
    row = get(project_id) or {}
    seed = SEED.get(project_id, {})
    out = {k: (row.get(k) if row.get(k) is not None else seed.get(k))
           for k in ("repo", "repo_path", "publication_dir", "public", "manifest_path", "paper_label")}
    out["seed_paths"], out["seed_dialect"] = seed.get("paths", {}), seed.get("dialect", "ghai")
    out["distro"] = row.get("distro") or DISTRO
    out["project_id"] = project_id
    out["registered"] = bool(row)
    if not out["repo_path"]:
        raise LookupError(f"{project_id}: no repository known; register it with `init --repo-path …`")
    return out


def upsert(project_id: str, **fields) -> None:
    from sqlalchemy.dialects.postgresql import insert

    from src.db.engine import session_scope
    from src.db.models import Project
    bad = set(fields) - set(FIELDS)
    if bad:
        raise ValueError(f"unknown registry fields {sorted(bad)}")
    values = {"project_id": project_id, **fields, "updated_at": datetime.now(timezone.utc)}
    with session_scope() as s:
        s.execute(insert(Project).values(**values)
                  .on_conflict_do_update(index_elements=["project_id"],
                                         set_={k: v for k, v in values.items() if k != "project_id"}))


def record_delivery(project_id: str, summary: dict, consumer_commit: str | None) -> None:
    upsert(project_id, last_delivery_at=datetime.now(timezone.utc), last_delivery_commit=consumer_commit,
           last_delivery=summary)
