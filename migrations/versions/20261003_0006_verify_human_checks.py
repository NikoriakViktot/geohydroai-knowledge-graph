"""verify: the human verification layer (append-only checks of parsed data) and the 'verify' scope

Every row is one judgement by a person about one piece of parsed or extracted data (a region,
a formula, a table, a metric fact, an entity edge, a thesis evidence row, a paper's metadata).
Rows are never updated: a correction is a new row (``supersedes`` names the one it replaces),
and ``verify.current`` is the latest judgement per (target_kind, target_id, field).
``labeler_kind`` is always 'human'; only keys with the 'verify' scope may write, and that scope
is for people, not agents.

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-03 08:00:00
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0006'
down_revision = '0005'
branch_labels = None
depends_on = None

TARGET_KINDS = ("paper", "section", "region", "formula", "table", "figure", "metric_fact", "entity_edge",
                "claim_evidence", "thesis", "reference", "location")
VERDICTS = ("correct", "incorrect", "partial", "unsure")
PROBLEMS = ("text_missing", "text_garbled", "table_broken", "formula_broken", "crop_wrong", "value_wrong",
            "unit_wrong", "entity_wrong", "location_wrong", "metadata_wrong", "evidence_not_supporting", "other")


def _in(col: str, values: tuple[str, ...]) -> str:
    return f"{col} IN ({', '.join(repr(v) for v in values)})"


def upgrade() -> None:
    op.drop_constraint(op.f('ck_api_key_scopes'), 'api_key', schema='ops', type_='check')
    op.create_check_constraint(op.f('ck_api_key_scopes'), 'api_key',
                               "scopes <@ ARRAY['read','llm','write','admin','verify']::text[]", schema='ops')
    op.execute("CREATE SCHEMA IF NOT EXISTS verify")
    op.create_table(
        'human_check',
        sa.Column('check_id', sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column('target_kind', sa.Text(), nullable=False),
        sa.Column('target_id', sa.Text(), nullable=False),
        sa.Column('field', sa.Text(), nullable=True),
        sa.Column('paper_id', sa.Text(), nullable=True),
        sa.Column('project_id', sa.Text(), nullable=True),
        sa.Column('verdict', sa.Text(), nullable=False),
        sa.Column('problem', sa.Text(), nullable=True),
        sa.Column('corrected', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('note', sa.Text(), nullable=True),
        sa.Column('shown', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('labeler_kind', sa.Text(), server_default='human', nullable=False),
        sa.Column('labeler', sa.Text(), nullable=False),
        sa.Column('key_id', sa.Text(), nullable=True),
        sa.Column('supersedes', sa.BigInteger(), sa.ForeignKey('verify.human_check.check_id'), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint(_in('target_kind', TARGET_KINDS), name=op.f('ck_human_check_target_kind')),
        sa.CheckConstraint(_in('verdict', VERDICTS), name=op.f('ck_human_check_verdict')),
        sa.CheckConstraint(f"problem IS NULL OR {_in('problem', PROBLEMS)}", name=op.f('ck_human_check_problem')),
        sa.CheckConstraint("labeler_kind = 'human'", name=op.f('ck_human_check_labeler_kind')),
        schema='verify',
    )
    op.create_index('ix_human_check_target', 'human_check', ['target_kind', 'target_id'], schema='verify')
    op.create_index('ix_human_check_paper', 'human_check', ['paper_id'], schema='verify')
    op.execute("""
        CREATE VIEW verify.current AS
        SELECT DISTINCT ON (target_kind, target_id, coalesce(field, '')) *
        FROM verify.human_check
        ORDER BY target_kind, target_id, coalesce(field, ''), created_at DESC, check_id DESC
    """)
    # append-only: a correction is a new row
    op.execute("""
        CREATE FUNCTION verify.refuse_change() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'verify.human_check is append-only: add a new row with supersedes'; END $$
    """)
    op.execute("""
        CREATE TRIGGER human_check_append_only BEFORE UPDATE OR DELETE ON verify.human_check
        FOR EACH ROW EXECUTE FUNCTION verify.refuse_change()
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS human_check_append_only ON verify.human_check")
    op.execute("DROP FUNCTION IF EXISTS verify.refuse_change()")
    op.execute("DROP VIEW IF EXISTS verify.current")
    op.drop_index('ix_human_check_paper', table_name='human_check', schema='verify')
    op.drop_index('ix_human_check_target', table_name='human_check', schema='verify')
    op.drop_table('human_check', schema='verify')
    op.execute("DROP SCHEMA IF EXISTS verify")
    op.drop_constraint(op.f('ck_api_key_scopes'), 'api_key', schema='ops', type_='check')
    op.create_check_constraint(op.f('ck_api_key_scopes'), 'api_key',
                               "scopes <@ ARRAY['read','llm','write','admin']::text[]", schema='ops')
