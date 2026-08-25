"""source provenance columns

Adds ``source`` (the producing source type) to nodes and edges so retrieval can be steered
by source per use-case (design-proposal §4). Additive; existing rows get NULL (unknown
provenance) and are unaffected by source-filtered queries unless a filter is applied.

Revision ID: 0005_source_provenance
Revises: 0004_governance
Create Date: 2026-06-02
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from mlpal_memory_graph.core.config import get_settings

revision = "0005_source_provenance"
down_revision = "0004_governance"
branch_labels = None
depends_on = None

SCHEMA = get_settings().db_schema or None


def upgrade() -> None:
    op.add_column("nodes", sa.Column("source", sa.String(32), nullable=True), schema=SCHEMA)
    op.add_column("edges", sa.Column("source", sa.String(32), nullable=True), schema=SCHEMA)
    op.create_index("ix_node_source", "nodes", ["source"], schema=SCHEMA)


def downgrade() -> None:
    op.drop_index("ix_node_source", table_name="nodes", schema=SCHEMA)
    op.drop_column("edges", "source", schema=SCHEMA)
    op.drop_column("nodes", "source", schema=SCHEMA)
