"""verify: target kinds law_link and qa_answer (equation KG evaluation, phase 6)

  law_link   one equation–law pair of the gold set; corrected = {instance: yes|no|unsure,
             variant?} — calibrates the law score of src/ontology/laws.py
  qa_answer  one system's answer to a gold question; corrected = {correct, citations_correct,
             evidence_complete} — the end-to-end QA comparison (vector RAG / KG / KG + RAG)

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-06 12:00:00
"""
from __future__ import annotations

from alembic import op

revision = '0008'
down_revision = '0007'
branch_labels = None
depends_on = None

PROBLEMS = ("text_missing", "text_garbled", "table_broken", "formula_broken", "crop_wrong", "value_wrong",
            "unit_wrong", "entity_wrong", "location_wrong", "metadata_wrong", "evidence_not_supporting", "other",
            "definition_wrong", "quantity_wrong")
OLD_KINDS = ("paper", "section", "region", "formula", "table", "figure", "metric_fact", "entity_edge",
             "claim_evidence", "thesis", "reference", "location",
             "equation", "parameter", "quantity_name", "equation_pair", "qa_item")
NEW_KINDS = OLD_KINDS + ("law_link", "qa_answer")


def _in(col: str, values: tuple[str, ...]) -> str:
    return f"{col} IN ({', '.join(repr(v) for v in values)})"


def _set(kinds: tuple[str, ...]) -> None:
    op.drop_constraint(op.f('ck_human_check_target_kind'), 'human_check', schema='verify', type_='check')
    op.create_check_constraint(op.f('ck_human_check_target_kind'), 'human_check',
                               _in('target_kind', kinds), schema='verify')


def upgrade() -> None:
    _set(NEW_KINDS)


def downgrade() -> None:
    _set(OLD_KINDS)
