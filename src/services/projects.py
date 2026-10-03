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


def theses_set(project_id: str) -> dict:
    """The stored theses of a project with their references, atomic claims and evidence rows.

    The evidence statuses (VERIFIED_SUPPORTED, …) were assigned by the literature run's models,
    not by people: the human judgements live in verify.human_check (target_kind 'claim_evidence').
    """
    from sqlalchemy import select

    from src.db.engine import session_scope
    from src.db.models import AtomicClaim, ClaimEvidence, Thesis, ThesisRef

    def plain(row, cols):
        return {c: getattr(row, c) for c in cols}

    with session_scope() as s:
        theses = s.scalars(select(Thesis).where(Thesis.project_id == project_id).order_by(Thesis.thesis_id)).all()
        refs = s.scalars(select(ThesisRef).where(ThesisRef.project_id == project_id)).all()
        claims = s.scalars(select(AtomicClaim).where(AtomicClaim.project_id == project_id)
                           .order_by(AtomicClaim.atomic_id)).all()
        evidence = s.scalars(select(ClaimEvidence).where(ClaimEvidence.project_id == project_id)
                             .order_by(ClaimEvidence.thesis_id, ClaimEvidence.atomic_id, ClaimEvidence.id)).all()
        out = {t.thesis_id: {**plain(t, ("thesis_id", "kind", "statement", "section", "category", "priority",
                                          "quantitative", "tables", "needs", "labeler_kind", "labeler")),
                             "refs": [], "atomic_claims": [], "evidence": []} for t in theses}
        for r in refs:
            if r.thesis_id in out:
                out[r.thesis_id]["refs"].append(plain(r, ("key", "relation", "status")))
        for c in claims:
            if c.thesis_id in out:
                out[c.thesis_id]["atomic_claims"].append(plain(c, ("atomic_id", "statement", "required_roles",
                                                                   "manuscript_relevance", "labeler_kind")))
        orphans = []
        for e in evidence:
            row = {"evidence_id": f"ce:{e.id}", **plain(e, ("atomic_id", "role", "status", "paper_id", "cite_key", "doi",
                                                            "section", "page", "chunk_id", "quote_verified",
                                                            "evidence_quote", "status_rule"))}
            (out[e.thesis_id]["evidence"] if e.thesis_id in out else orphans).append({**row, "thesis_id": e.thesis_id})
    return {"project_id": project_id, "theses": list(out.values()), "unattached_evidence": orphans,
            "counts": {"theses": len(theses), "atomic_claims": len(claims), "evidence": len(evidence)},
            "status_source": "model-assessed (literature run); human checks: GET /verifications?target_kind=claim_evidence"}
