"""Registered sources and their cold items (memory v7 WP4, design §9).

Revision ID: 0016_sources
Revises: 0015_notes
Create Date: 2026-09-17
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from mlpal_memory_graph.core.config import get_settings

revision = "0016_sources"
down_revision = "0015_notes"
branch_labels = None
depends_on = None

_SCHEMA = get_settings().db_schema or None


def upgrade() -> None:
    op.create_table(
        "memory_sources",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("org_id", sa.String(64), index=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("scope", sa.String(16), nullable=False, server_default="org"),
        sa.Column("scope_id", sa.String(64)),
        sa.Column("workspace", sa.String(256)),
        sa.Column("config", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("status", sa.String(16), nullable=False, server_default="cold"),
        sa.Column("item_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_by", sa.String(64)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("indexed_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("org_id", "name", name="uq_source_name"),
        schema=_SCHEMA,
    )
    op.create_table(
        "memory_source_items",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("org_id", sa.String(64), index=True),
        sa.Column("source_id", sa.String(36), nullable=False, index=True),
        sa.Column("ref", sa.String(1024), nullable=False),
        sa.Column("title", sa.String(512)),
        sa.Column("size", sa.Integer()),
        sa.Column("modified_at", sa.DateTime(timezone=True)),
        sa.Column("admitted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("admitted_at", sa.DateTime(timezone=True)),
        sa.Column("admitted_by", sa.String(64)),
        sa.Column("document_event_id", sa.String(128)),
        sa.Column("declined_reason", sa.String(256)),
        sa.UniqueConstraint("source_id", "ref", name="uq_source_item"),
        schema=_SCHEMA,
    )


def downgrade() -> None:
    op.drop_table("memory_source_items", schema=_SCHEMA)
    op.drop_table("memory_sources", schema=_SCHEMA)
