"""Units — the org's own hierarchy (memory v12 §2b).

Revision ID: 0018_units
Revises: 0017_hops
Create Date: 2026-09-23
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from mlpal_memory_graph.core.config import get_settings

revision = "0018_units"
down_revision = "0017_hops"
branch_labels = None
depends_on = None

_SCHEMA = get_settings().db_schema or None


def upgrade() -> None:
    op.create_table(
        "units",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("org_id", sa.String(64), index=True),
        sa.Column("parent_id", sa.String(36), index=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False, server_default="unit"),
        sa.Column("policy", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_by", sa.String(64)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("org_id", "parent_id", "name", name="uq_unit_sibling_name"),
        schema=_SCHEMA,
    )
    op.create_table(
        "unit_members",
        sa.Column("org_id", sa.String(64), index=True),
        sa.Column("unit_id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(64), primary_key=True),
        sa.Column("role", sa.String(16), nullable=False, server_default="member"),
        sa.Column("added_by", sa.String(64)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        schema=_SCHEMA,
    )


def downgrade() -> None:
    op.drop_table("unit_members", schema=_SCHEMA)
    op.drop_table("units", schema=_SCHEMA)
