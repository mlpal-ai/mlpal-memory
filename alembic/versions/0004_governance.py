"""governance: consent_states + extraction_policies + episode drop reason

Adds per-scope consent (collect/update opt-out) and extraction-policy tables, plus an
``episodes.dropped_reason`` column recording why the fold gate declined an episode. All
additive. See design-proposal §2, §5.

Revision ID: 0004_governance
Revises: 0003_abac_columns
Create Date: 2026-06-02
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from mlpal_memory_graph.core.config import get_settings

revision = "0004_governance"
down_revision = "0003_abac_columns"
branch_labels = None
depends_on = None

SCHEMA = get_settings().db_schema or None


def upgrade() -> None:
    op.add_column(
        "episodes", sa.Column("dropped_reason", sa.String(128), index=True), schema=SCHEMA
    )

    op.create_table(
        "consent_states",
        sa.Column("org_id", sa.String(64), primary_key=True),
        sa.Column("scope", sa.String(16), primary_key=True),
        sa.Column("scope_id", sa.String(64), primary_key=True),
        sa.Column("state", sa.String(16), nullable=False, server_default="active"),
        sa.Column("updated_by", sa.String(64)),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        schema=SCHEMA,
    )

    op.create_table(
        "extraction_policies",
        sa.Column("org_id", sa.String(64), primary_key=True),
        sa.Column("scope", sa.String(16), primary_key=True),
        sa.Column("scope_id", sa.String(64), primary_key=True),
        sa.Column("policy", sa.JSON()),
        sa.Column("version", sa.Integer(), server_default="1"),
        sa.Column("updated_by", sa.String(64)),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_table("extraction_policies", schema=SCHEMA)
    op.drop_table("consent_states", schema=SCHEMA)
    op.drop_column("episodes", "dropped_reason", schema=SCHEMA)
