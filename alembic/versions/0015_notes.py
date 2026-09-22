"""Workspace notes — the curated working-memory tier (notes + note_versions).

A note is a small sectioned markdown document per (org, scope, scope_id, workspace) that
agents and humans update in place; every write is kept as a version. Companion to the
rebuildable projection: notes are a source of truth for working state.

Revision ID: 0015_notes
Revises: 0014_usage_counters
Create Date: 2026-09-03
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from mlpal_memory_graph.core.config import get_settings

revision = "0015_notes"
down_revision = "0014_usage_counters"
branch_labels = None
depends_on = None

_SCHEMA = get_settings().db_schema or None


def upgrade() -> None:
    op.create_table(
        "notes",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("org_id", sa.String(64), index=True),
        sa.Column("scope", sa.String(16), nullable=False, server_default="org"),
        sa.Column("scope_id", sa.String(64), nullable=False),
        sa.Column("workspace", sa.String(256), nullable=False, server_default=""),
        sa.Column("classification", sa.String(16), nullable=False, server_default="internal"),
        sa.Column("owner_user_id", sa.String(64)),
        sa.Column("title", sa.String(256)),
        sa.Column("body", sa.Text(), nullable=False, server_default=""),
        sa.Column("version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_by", sa.String(64)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("org_id", "scope", "scope_id", "workspace", name="uq_note_key"),
        schema=_SCHEMA,
    )
    op.create_index("ix_note_scope", "notes", ["org_id", "scope", "scope_id"], schema=_SCHEMA)
    op.create_table(
        "note_versions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("note_id", sa.String(36), nullable=False, index=True),
        sa.Column("org_id", sa.String(64), index=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("updated_by", sa.String(64)),
        sa.Column("reason", sa.String(512)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("note_id", "version", name="uq_note_version"),
        schema=_SCHEMA,
    )


def downgrade() -> None:
    op.drop_table("note_versions", schema=_SCHEMA)
    op.drop_index("ix_note_scope", table_name="notes", schema=_SCHEMA)
    op.drop_table("notes", schema=_SCHEMA)
