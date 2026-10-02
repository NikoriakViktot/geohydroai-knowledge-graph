"""Contracts stay in sync with their exported schemas and with the database constraints."""

from __future__ import annotations

import json

import pytest

from src.contracts import export as contract_export
from src.contracts import identity
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
        if getattr(c, "name", None) and str(c.name).endswith(name) and hasattr(c, "sqltext"):
            return str(c.sqltext)
    raise AssertionError(f"no CHECK constraint {name} on {table.fullname}")


@pytest.mark.parametrize("table, constraint, values", [
    (models.Paper.__table__, "identity_status", identity.IDENTITY_STATUSES),
    (models.PaperAlias.__table__, "alias_type", identity.ALIAS_TYPES),
    (models.PaperFile.__table__, "kind", identity.FILE_KINDS),
    (models.PaperFile.__table__, "status", identity.FILE_STATUSES),
    (models.Run.__table__, "kind", identity.RUN_KINDS),
    (models.Run.__table__, "status", identity.RUN_STATUSES),
])
def test_database_checks_allow_exactly_the_contract_values(table, constraint, values):
    sql = _check_sql(table, constraint)
    for v in values:
        assert f"'{v}'" in sql
    assert sql.count("'") == 2 * len(values)
