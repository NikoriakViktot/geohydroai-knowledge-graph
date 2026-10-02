"""Contracts stay in sync with their exported schemas and with the database constraints."""

from __future__ import annotations

import json

import pydantic
import pytest

from src.contracts import export as contract_export
from src.contracts import identity, research
from src.db import models


@pytest.mark.parametrize("model, major", contract_export.CONTRACTS, ids=lambda x: getattr(x, "__name__", str(x)))
def test_committed_schema_matches_the_model(model, major):
    path = contract_export.OUT_DIR / f"{model.__name__}.v{major}.json"
    assert path.exists(), f"run: python -m src.contracts.export ({path} missing)"
    committed = json.loads(path.read_text(encoding="utf-8"))
    current = model.model_json_schema()
    committed.pop("$id", None)
    assert committed == current, f"{path.name} is stale: run python -m src.contracts.export"


def _check_sql(table, name: str) -> str:
    for c in table.constraints:
        if hasattr(c, "sqltext") and str(getattr(c, "name", "")) in (name, f"ck_{table.name}_{name}"):
            return str(c.sqltext)
    raise AssertionError(f"no CHECK constraint {name} on {table.fullname}")


@pytest.mark.parametrize("table, constraint, values", [
    (models.Paper.__table__, "identity_status", identity.IDENTITY_STATUSES),
    (models.PaperAlias.__table__, "alias_type", identity.ALIAS_TYPES),
    (models.PaperFile.__table__, "kind", identity.FILE_KINDS),
    (models.PaperFile.__table__, "status", identity.FILE_STATUSES),
    (models.Run.__table__, "kind", identity.RUN_KINDS),
    (models.Run.__table__, "status", identity.RUN_STATUSES),
    (models.Thesis.__table__, "kind", research.THESIS_KINDS),
    (models.Thesis.__table__, "labeler_kind", research.LABELER_KINDS),
    (models.ThesisRef.__table__, "relation", research.REF_RELATIONS),
    (models.ThesisRef.__table__, "status", research.REF_STATUSES),
    (models.ScreeningLabel.__table__, "relevance", research.RELEVANCES),
    (models.QuoteCheck.__table__, "verdict", research.CITATION_VERDICTS),
    (models.BibVerification.__table__, "verdict", research.BIB_VERDICTS),
])
def test_database_checks_allow_exactly_the_contract_values(table, constraint, values):
    sql = _check_sql(table, constraint)
    for v in values:
        assert f"'{v}'" in sql
    assert sql.count("'") == 2 * len(values)


def test_project_ids_follow_one_pattern_in_contract_and_database():
    sql = _check_sql(models.Project.__table__, "project_id")
    assert research.PROJECT_ID_PATTERN in sql
    adapter = pydantic.TypeAdapter(research.ProjectId)
    for good in research.SEED_PROJECT_IDS + ("article2", "new-repo:paper-4"):
        assert adapter.validate_python(good) == good
    for bad in ("Floodstate-eo:paper3", "a:b:c", ":paper", "repo:", "with space", "x" * 65):
        with pytest.raises(pydantic.ValidationError):
            adapter.validate_python(bad)
