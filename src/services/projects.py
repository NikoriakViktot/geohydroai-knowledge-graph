"""The project registry (project.project): which papers exist and where their repositories are."""

from __future__ import annotations


def registered(project_id: str) -> bool | None:
    """True/False from project.project; None when Postgres cannot be read."""
    try:
        from sqlalchemy import select

        from src.db.engine import session_scope
        from src.db.models import Project
        with session_scope() as s:
            return s.scalar(select(Project.project_id).where(Project.project_id == project_id)) is not None
    except Exception:
        return None
