"""hierarchy scope columns + drop communities

Adds the (scope, scope_id) hierarchy axis to nodes/edges/episodes and migrates the flat
v1 data: every existing row is org-scoped, so ``scope='org'`` (column default) and
``scope_id := org_id`` (backfill). Non-breaking and reversible.

Drops the unused ``communities`` table (no producer ever existed — see design-proposal §8).

NOTE: Postgres Row-Level Security policies (the read-time isolation backstop) are added in
the M4 migration, where they pair with the per-transaction tenant GUC set from auth context.
M1 isolation is enforced by the driver's scope predicate and covered by a cross-scope leak
test. See docs/redesign/design-proposal.md §6, §10.

Revision ID: 0002_hierarchy_scope
Revises: 0001_initial
Create Date: 2026-06-02
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from mlpal_memory_graph.core.config import get_settings

revision = "0002_hierarchy_scope"
down_revision = "0001_initial"
branch_labels = None
depends_on = None

SCHEMA = get_settings().db_schema or None


def _add_scope_columns(table: str) -> None:
    op.add_column(
        table,
        sa.Column("scope", sa.String(16), nullable=False, server_default="org"),
        schema=SCHEMA,
    )
    op.add_column(table, sa.Column("scope_id", sa.String(64), nullable=True), schema=SCHEMA)


def upgrade() -> None:
    qualified = f'"{SCHEMA}".' if SCHEMA else ""

    for table in ("nodes", "edges", "episodes"):
        _add_scope_columns(table)
        # backfill: flat v1 data is org-scoped; the org *is* the tenant.
        op.execute(f"UPDATE {qualified}{table} SET scope_id = org_id WHERE scope = 'org'")

    # node identity is now per-scope
    op.drop_constraint("uq_node_identity", "nodes", schema=SCHEMA, type_="unique")
    op.create_unique_constraint(
        "uq_node_identity", "nodes", ["org_id", "scope", "scope_id", "type", "key"], schema=SCHEMA
    )
    op.create_index("ix_node_scope", "nodes", ["org_id", "scope", "scope_id"], schema=SCHEMA)
    op.create_index("ix_edge_scope", "edges", ["org_id", "scope", "scope_id"], schema=SCHEMA)

    op.drop_table("communities", schema=SCHEMA)


def downgrade() -> None:
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

    op.drop_index("ix_edge_scope", table_name="edges", schema=SCHEMA)
    op.drop_index("ix_node_scope", table_name="nodes", schema=SCHEMA)
    op.drop_constraint("uq_node_identity", "nodes", schema=SCHEMA, type_="unique")
    op.create_unique_constraint(
        "uq_node_identity", "nodes", ["org_id", "type", "key"], schema=SCHEMA
    )

    for table in ("episodes", "edges", "nodes"):
        op.drop_column(table, "scope_id", schema=SCHEMA)
        op.drop_column(table, "scope", schema=SCHEMA)
