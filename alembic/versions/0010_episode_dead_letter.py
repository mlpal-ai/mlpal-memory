"""Episode dead-letter: bounded retries for the fold worker (v3 Phase 0.3)

A failing episode previously retried forever (error recorded, processed left false),
starving newer episodes and burning embedding/LLM spend. Adds:

- ``error_count`` — incremented on each failed fold attempt
- ``dead_at``     — set when error_count reaches the retry cap; the worker's
                    unprocessed-cursor excludes dead episodes (kept for audit/replay)

Additive; both columns are cheap and the cursor keeps using the existing
``processed`` index (dead episodes are a tiny minority by construction).

Revision ID: 0010_episode_dead_letter
Revises: 0009_rls_backstop
Create Date: 2026-07-20
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op
from mlpal_memory_graph.core.config import get_settings

revision = "0010_episode_dead_letter"
down_revision = "0009_rls_backstop"
branch_labels = None
depends_on = None

_SCHEMA = get_settings().db_schema or None


def upgrade() -> None:
    op.add_column(
        "episodes",
        sa.Column("error_count", sa.Integer(), nullable=False, server_default="0"),
        schema=_SCHEMA,
    )
    op.add_column(
        "episodes",
        sa.Column("dead_at", sa.DateTime(timezone=True), nullable=True),
        schema=_SCHEMA,
    )


def downgrade() -> None:
    op.drop_column("episodes", "dead_at", schema=_SCHEMA)
    op.drop_column("episodes", "error_count", schema=_SCHEMA)
