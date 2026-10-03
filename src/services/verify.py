"""The human verification layer: record and read people's judgements of parsed data.

verify.human_check is append-only (a database trigger refuses UPDATE and DELETE); the latest
judgement per (target_kind, target_id, field) is the view verify.current. Writers need the
'verify' scope, which belongs to people: labeler_kind is always 'human' and labeler is the
consumer of the key that wrote the row.
"""

from __future__ import annotations

from collections import Counter, defaultdict

from src.contracts.verify import PARSE_PROBLEMS, CheckIn

_COLUMNS = ("check_id", "target_kind", "target_id", "field", "paper_id", "project_id", "verdict", "problem",
            "corrected", "note", "shown", "labeler", "supersedes", "created_at")


def record(checks: list[CheckIn], labeler: str, key_id: str | None) -> list[int]:
    from src.db.engine import session_scope
    from src.db.models import HumanCheck
    rows = [HumanCheck(**c.model_dump(), labeler_kind="human", labeler=labeler, key_id=key_id) for c in checks]
    with session_scope() as s:
        s.add_all(rows)
        s.flush()
        return [r.check_id for r in rows]


def _rows(sql: str, params: dict) -> list[dict]:
    from sqlalchemy import text

    from src.db.engine import session_scope
    with session_scope() as s:
        return [dict(r._mapping) for r in s.execute(text(sql), params)]


def listing(*, current: bool = True, target_kind: str | None = None, target_ids: list[str] | None = None,
            paper_id: str | None = None, project_id: str | None = None, verdict: str | None = None,
            problem: str | None = None, limit: int = 500) -> list[dict]:
    """Checks, newest first. ``current`` keeps only the latest judgement per target and field."""
    where, params = [], {"limit": limit}
    for col, val in (("target_kind", target_kind), ("paper_id", paper_id), ("project_id", project_id),
                     ("verdict", verdict), ("problem", problem)):
        if val:
            where.append(f"{col} = :{col}")
            params[col] = val
    if target_ids:
        where.append("target_id = ANY(:target_ids)")
        params["target_ids"] = list(target_ids)
    table = "verify.current" if current else "verify.human_check"
    sql = (f"SELECT {', '.join(_COLUMNS)} FROM {table}" + (f" WHERE {' AND '.join(where)}" if where else "")
           + " ORDER BY created_at DESC, check_id DESC LIMIT :limit")
    return _rows(sql, params)


def summary(project_id: str | None = None, top: int = 30) -> dict:
    """Counts over the current judgements, and the papers with the most parse problems."""
    rows = _rows("SELECT target_kind, verdict, problem, paper_id FROM verify.current"
                 + (" WHERE project_id = :p" if project_id else ""), {"p": project_id} if project_id else {})
    by_kind: dict[str, Counter] = defaultdict(Counter)
    papers: dict[str, Counter] = defaultdict(Counter)
    for r in rows:
        by_kind[r["target_kind"]][r["verdict"]] += 1
        if r["problem"] in PARSE_PROBLEMS and r["paper_id"]:
            papers[r["paper_id"]][r["problem"]] += 1
    worst = sorted(papers.items(), key=lambda kv: -sum(kv[1].values()))[:top]
    return {"total": len(rows),
            "by_verdict": dict(Counter(r["verdict"] for r in rows)),
            "by_problem": dict(Counter(r["problem"] for r in rows if r["problem"])),
            "by_target_kind": {k: dict(v) for k, v in by_kind.items()},
            "parse_problem_papers": [{"paper_id": p, "problems": dict(c), "total": sum(c.values())} for p, c in worst]}
