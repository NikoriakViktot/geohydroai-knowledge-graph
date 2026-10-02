"""project registry: repository location, publication dir and last delivery; project_id by pattern

The closed list of five project ids becomes a pattern ("<repository>:<paper>" or a bare name),
so a new paper is registered by a row, not a migration. Every research table keeps its
foreign key to project.project, so rows of an unregistered project are still refused.

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-02 15:20:00
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0005'
down_revision = '0004'
branch_labels = None
depends_on = None

PATTERN = r"^[a-z0-9][a-z0-9-]*(:[a-z0-9][a-z0-9-]*)?$"
OLD_IDS = ("floodstate-eo:paper3", "kakhovka-terrain:paper2", "swot-dnipro:paper1", "kakhovka-report:v1", "article1")
COLUMNS = (
    ("distro", sa.Text()),
    ("repo_path", sa.Text()),
    ("publication_dir", sa.Text()),
    ("public", sa.Boolean()),
    ("manifest_path", sa.Text()),
    ("manifest_sha256", sa.Text()),
    ("last_delivery_at", sa.DateTime(timezone=True)),
    ("last_delivery_commit", sa.Text()),
    ("last_delivery", postgresql.JSONB(astext_type=sa.Text())),
)


def upgrade() -> None:
    op.drop_constraint(op.f('ck_project_project_id'), 'project', schema='project', type_='check')
    op.create_check_constraint(op.f('ck_project_project_id'), 'project', f"project_id ~ '{PATTERN}'", schema='project')
    for name, type_ in COLUMNS:
        op.add_column('project', sa.Column(name, type_, nullable=True), schema='project')
    op.add_column('project', sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
                                       nullable=True), schema='project')


def downgrade() -> None:
    op.drop_column('project', 'updated_at', schema='project')
    for name, _ in reversed(COLUMNS):
        op.drop_column('project', name, schema='project')
    op.drop_constraint(op.f('ck_project_project_id'), 'project', schema='project', type_='check')
    ids = ", ".join(repr(i) for i in OLD_IDS)
    op.create_check_constraint(op.f('ck_project_project_id'), 'project', f"project_id IN ({ids})", schema='project')
