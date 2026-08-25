"""v3 store model: workspace facet, insight lifecycle, re-observation, document valid-time.

- ``workspace`` on nodes/edges/episodes/documents/chunks — the personal-store partition
  key ("me, in repo X"). Not an authz surface; user scope stays owner-only.
- ``status`` + ``expires_at`` on nodes/edges — working (TTL'd) → committed → published.
- ``observed_count`` on nodes — re-observed insights bump instead of duplicating.
- ``valid_at`` on documents — bitemporal event-time for ingested files/transcripts
  (a 2024 design doc ingested today: valid_at=2024, created_at=now → staleness ranking).

All additive with server defaults; existing rows read as committed/durable.

Revision ID: 0011_v3_store_model
Revises: 0010_episode_dead_letter
Create Date: 2026-07-20
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op
from mlpal_memory_graph.core.config import get_settings

revision = "0011_v3_store_model"
down_revision = "0010_episode_dead_letter"
branch_labels = None
depends_on = None

_SCHEMA = get_settings().db_schema or None

_WS = lambda: sa.Column("workspace", sa.String(256), nullable=True)  # noqa: E731
_STATUS = lambda: sa.Column(  # noqa: E731
    "status", sa.String(16), nullable=False, server_default="committed"
)
_EXPIRES = lambda: sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True)  # noqa: E731


def upgrade() -> None:
    for table in ("nodes", "edges"):
        op.add_column(table, _WS(), schema=_SCHEMA)
        op.add_column(table, _STATUS(), schema=_SCHEMA)
        op.add_column(table, _EXPIRES(), schema=_SCHEMA)
        op.create_index(f"ix_{table}_workspace", table, ["workspace"], schema=_SCHEMA)
        # TTL sweep + expiry filters hit (status, expires_at)
        op.create_index(f"ix_{table}_expiry", table, ["status", "expires_at"], schema=_SCHEMA)
    op.add_column(
        "nodes",
        sa.Column("observed_count", sa.Integer(), nullable=False, server_default="1"),
        schema=_SCHEMA,
    )
    for table in ("episodes", "documents", "chunks"):
        op.add_column(table, _WS(), schema=_SCHEMA)
        op.create_index(f"ix_{table}_workspace", table, ["workspace"], schema=_SCHEMA)
    op.add_column(
        "episodes",
        sa.Column("lifecycle", sa.String(16), nullable=False, server_default="committed"),
        schema=_SCHEMA,
    )
    op.add_column(
        "documents",
        sa.Column("valid_at", sa.DateTime(timezone=True), nullable=True),
        schema=_SCHEMA,
    )


def downgrade() -> None:
    op.drop_column("documents", "valid_at", schema=_SCHEMA)
    op.drop_column("episodes", "lifecycle", schema=_SCHEMA)
    for table in ("episodes", "documents", "chunks"):
        op.drop_index(f"ix_{table}_workspace", table_name=table, schema=_SCHEMA)
        op.drop_column(table, "workspace", schema=_SCHEMA)
    op.drop_column("nodes", "observed_count", schema=_SCHEMA)
    for table in ("nodes", "edges"):
        op.drop_index(f"ix_{table}_expiry", table_name=table, schema=_SCHEMA)
        op.drop_index(f"ix_{table}_workspace", table_name=table, schema=_SCHEMA)
        op.drop_column(table, "expires_at", schema=_SCHEMA)
        op.drop_column(table, "status", schema=_SCHEMA)
        op.drop_column(table, "workspace", schema=_SCHEMA)
