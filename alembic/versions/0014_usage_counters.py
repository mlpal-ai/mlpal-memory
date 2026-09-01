"""Usage counters on chunks and nodes — the junk decider's evidence base.

The retention/GC policy (v4 §D10) must be measured before anything is deleted:
these columns record whether a memory has EVER been served in an answer/search
result and when. served_count is bumped by the read path (fire-and-forget batch
update); the GC report (tools/gc.py) turns the distribution into archive
candidates. No automatic deletion — measurement first, policy second.

Revision ID: 0014_usage_counters
Revises: 0013_chunk_tsv_column
Create Date: 2026-08-30
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from mlpal_memory_graph.core.config import get_settings

revision = "0014_usage_counters"
down_revision = "0013_chunk_tsv_column"
branch_labels = None
depends_on = None

_SCHEMA = get_settings().db_schema or None


def upgrade() -> None:
    for table in ("chunks", "nodes"):
        op.add_column(
            table,
            sa.Column("served_count", sa.Integer, nullable=False, server_default="0"),
            schema=_SCHEMA,
        )
        op.add_column(
            table,
            sa.Column("last_served_at", sa.DateTime(timezone=True), nullable=True),
            schema=_SCHEMA,
        )


def downgrade() -> None:
    for table in ("chunks", "nodes"):
        op.drop_column(table, "served_count", schema=_SCHEMA)
        op.drop_column(table, "last_served_at", schema=_SCHEMA)
