"""initial memory-graph schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-05-31
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from mlpal_memory_graph.core.config import get_settings

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None

SCHEMA = get_settings().db_schema or None


def upgrade() -> None:
    if SCHEMA:
        op.execute(f'CREATE SCHEMA IF NOT EXISTS "{SCHEMA}"')

    op.create_table(
        "episodes",
        sa.Column("event_id", sa.String(64), primary_key=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("org_id", sa.String(64), index=True),
        sa.Column("actor", sa.JSON()),
        sa.Column("source", sa.String(32), index=True),
        sa.Column("action_type", sa.String(64), index=True),
        sa.Column("subject", sa.JSON()),
        sa.Column("payload", sa.JSON()),
        sa.Column("content", sa.Text()),
        sa.Column("source_ref", sa.String(256)),
        sa.Column("schema_version", sa.Integer()),
        sa.Column("ingested_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("processed", sa.Boolean(), index=True),
        sa.Column("processed_at", sa.DateTime(timezone=True)),
        sa.Column("tier", sa.String(16)),
        sa.Column("error", sa.Text()),
        schema=SCHEMA,
    )

    op.create_table(
        "nodes",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("org_id", sa.String(64), index=True),
        sa.Column("type", sa.String(64), index=True),
        sa.Column("key", sa.String(512), nullable=False),
        sa.Column("name", sa.String(512), nullable=False),
        sa.Column("summary", sa.Text()),
        sa.Column("props", sa.JSON()),
        sa.Column("embedding", sa.JSON()),
        sa.Column("ontology_version", sa.String(32)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("org_id", "type", "key", name="uq_node_identity"),
        schema=SCHEMA,
    )

    op.create_table(
        "edges",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("org_id", sa.String(64), index=True),
        sa.Column("type", sa.String(64), index=True),
        sa.Column("src_id", sa.String(36), index=True),
        sa.Column("dst_id", sa.String(36), index=True),
        sa.Column("fact", sa.Text()),
        sa.Column("props", sa.JSON()),
        sa.Column("valid_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("invalid_at", sa.DateTime(timezone=True)),
        sa.Column("ingested_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("expired_at", sa.DateTime(timezone=True)),
        sa.Column("ontology_version", sa.String(32)),
        schema=SCHEMA,
    )
    op.create_index("ix_edge_pair", "edges", ["src_id", "dst_id"], schema=SCHEMA)
    op.create_index("ix_edge_org_type", "edges", ["org_id", "type"], schema=SCHEMA)

    op.create_table(
        "communities",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("org_id", sa.String(64), index=True),
        sa.Column("label", sa.String(128), nullable=False),
        sa.Column("summary", sa.Text()),
        sa.Column("member_ids", sa.JSON()),
        sa.Column("embedding", sa.JSON()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        schema=SCHEMA,
    )

    op.create_table(
        "memory_watermarks",
        sa.Column("source", sa.String(128), primary_key=True),
        sa.Column("last_processed_ref", sa.String(256)),
        sa.Column("last_processed_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_table("memory_watermarks", schema=SCHEMA)
    op.drop_table("communities", schema=SCHEMA)
    op.drop_index("ix_edge_org_type", table_name="edges", schema=SCHEMA)
    op.drop_index("ix_edge_pair", table_name="edges", schema=SCHEMA)
    op.drop_table("edges", schema=SCHEMA)
    op.drop_table("nodes", schema=SCHEMA)
    op.drop_table("episodes", schema=SCHEMA)
