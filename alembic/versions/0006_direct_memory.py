"""direct memory: documents + chunks, and derived-provenance columns

Adds the DIRECT tier — ``documents`` and ``chunks`` (verbatim, embedded, retrievable) — and
links the DERIVED tier back to it: ``confidence`` + ``derived_from`` on nodes and edges. Both
new tables carry the same (scope, scope_id, classification, owner_user_id, source) governance
columns as the graph, so the shared scope predicate applies. Additive. See design-proposal §14.

Revision ID: 0006_direct_memory
Revises: 0005_source_provenance
Create Date: 2026-06-06
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from mlpal_memory_graph.core.config import get_settings

revision = "0006_direct_memory"
down_revision = "0005_source_provenance"
branch_labels = None
depends_on = None

SCHEMA = get_settings().db_schema or None


def upgrade() -> None:
    for table in ("nodes", "edges"):
        op.add_column(table, sa.Column("confidence", sa.Float(), nullable=True), schema=SCHEMA)
        op.add_column(table, sa.Column("derived_from", sa.JSON(), nullable=True), schema=SCHEMA)

    op.create_table(
        "documents",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("org_id", sa.String(64), index=True),
        sa.Column("scope", sa.String(16), nullable=False, server_default="org"),
        sa.Column("scope_id", sa.String(64)),
        sa.Column("classification", sa.String(16), nullable=False, server_default="internal"),
        sa.Column("owner_user_id", sa.String(64)),
        sa.Column("source", sa.String(32)),
        sa.Column("source_ref", sa.String(256)),
        sa.Column("title", sa.String(512)),
        sa.Column("uri", sa.String(1024)),
        sa.Column("props", sa.JSON()),
        sa.Column("ingested_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("expired_at", sa.DateTime(timezone=True)),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_document_scope", "documents", ["org_id", "scope", "scope_id"], schema=SCHEMA
    )

    op.create_table(
        "chunks",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("document_id", sa.String(36), index=True),
        sa.Column("org_id", sa.String(64), index=True),
        sa.Column("scope", sa.String(16), nullable=False, server_default="org"),
        sa.Column("scope_id", sa.String(64)),
        sa.Column("classification", sa.String(16), nullable=False, server_default="internal"),
        sa.Column("owner_user_id", sa.String(64)),
        sa.Column("source", sa.String(32)),
        sa.Column("ordinal", sa.Integer(), server_default="0"),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("embedding", sa.JSON()),
        sa.Column("ingested_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        schema=SCHEMA,
    )
    op.create_index("ix_chunk_scope", "chunks", ["org_id", "scope", "scope_id"], schema=SCHEMA)


def downgrade() -> None:
    op.drop_index("ix_chunk_scope", table_name="chunks", schema=SCHEMA)
    op.drop_table("chunks", schema=SCHEMA)
    op.drop_index("ix_document_scope", table_name="documents", schema=SCHEMA)
    op.drop_table("documents", schema=SCHEMA)
    for table in ("nodes", "edges"):
        op.drop_column(table, "derived_from", schema=SCHEMA)
        op.drop_column(table, "confidence", schema=SCHEMA)
