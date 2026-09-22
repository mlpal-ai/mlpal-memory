"""The HOP registry (memory v7 WP6/WP8).

Revision ID: 0017_hops
Revises: 0016_sources
Create Date: 2026-09-17
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from mlpal_memory_graph.core.config import get_settings

revision = "0017_hops"
down_revision = "0016_sources"
branch_labels = None
depends_on = None

_SCHEMA = get_settings().db_schema or None


def upgrade() -> None:
    op.create_table(
        "memory_hops",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("org_id", sa.String(64), index=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("version", sa.String(32)),
        sa.Column("owner_user_id", sa.String(64)),
        sa.Column("mode", sa.String(16), nullable=False, server_default="review"),
        sa.Column("sharing", sa.String(16), nullable=False, server_default="none"),
        sa.Column("tenant", sa.String(64)),
        sa.Column("workspace", sa.String(256)),
        sa.Column("contract", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("registered_by", sa.String(64)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("org_id", "name", name="uq_hop_name"),
        schema=_SCHEMA,
    )


def downgrade() -> None:
    op.drop_table("memory_hops", schema=_SCHEMA)
