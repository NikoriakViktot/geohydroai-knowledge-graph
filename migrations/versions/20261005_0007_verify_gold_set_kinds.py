"""verify: target kinds and problems for the equation gold set

The equation-centric KG is evaluated on a human-labelled gold set
(docs_v2/EQUATION_KG_PLAN.md). Its items are judged in the same append-only table:

  target kinds  equation        one EquationOccurrence (formula, COMPUTES quantity, role)
                parameter       one symbol ↔ definition / unit / value link
                quantity_name   one surface form → canonical Quantity
                equation_pair   one pair; corrected.relation ∈ EXACT, ALGEBRAIC, SAME_LAW,
                                RELATED, DIFFERENT, UNCERTAIN
                qa_item         one end-to-end question with its answer and evidence
  problems      definition_wrong, quantity_wrong

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-05 12:00:00
"""
from __future__ import annotations

from alembic import op

revision = '0007'
down_revision = '0006'
branch_labels = None
depends_on = None

OLD_KINDS = ("paper", "section", "region", "formula", "table", "figure", "metric_fact", "entity_edge",
             "claim_evidence", "thesis", "reference", "location")
NEW_KINDS = OLD_KINDS + ("equation", "parameter", "quantity_name", "equation_pair", "qa_item")
OLD_PROBLEMS = ("text_missing", "text_garbled", "table_broken", "formula_broken", "crop_wrong", "value_wrong",
                "unit_wrong", "entity_wrong", "location_wrong", "metadata_wrong", "evidence_not_supporting", "other")
NEW_PROBLEMS = OLD_PROBLEMS + ("definition_wrong", "quantity_wrong")


def _in(col: str, values: tuple[str, ...]) -> str:
    return f"{col} IN ({', '.join(repr(v) for v in values)})"


def _set(kinds: tuple[str, ...], problems: tuple[str, ...]) -> None:
    op.drop_constraint(op.f('ck_human_check_target_kind'), 'human_check', schema='verify', type_='check')
    op.create_check_constraint(op.f('ck_human_check_target_kind'), 'human_check',
                               _in('target_kind', kinds), schema='verify')
    op.drop_constraint(op.f('ck_human_check_problem'), 'human_check', schema='verify', type_='check')
    op.create_check_constraint(op.f('ck_human_check_problem'), 'human_check',
                               f"problem IS NULL OR {_in('problem', problems)}", schema='verify')


def upgrade() -> None:
    _set(NEW_KINDS, NEW_PROBLEMS)


def downgrade() -> None:
    _set(OLD_KINDS, OLD_PROBLEMS)
