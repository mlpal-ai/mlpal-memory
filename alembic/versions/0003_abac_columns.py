"""access/visibility (ABAC) columns

Adds ``classification`` (public|internal|personal) to nodes and edges, and ``owner_user_id``
to nodes. Backfills from the owning scope: user-scoped rows become ``personal`` (owned by the
scope user), global rows ``public``, everything else ``internal``. Non-breaking. See §6.

The Postgres Row-Level Security backstop (per design-proposal §6.1) is intentionally NOT
added here: it requires a per-transaction tenant GUC set from auth context plus a Postgres
integration test to prove it doesn't block the worker's writes. Until that lands, isolation
is enforced by the driver's scope predicate (``graph/drivers/postgres.py``) and the
cross-scope leak tests in CI. Tracked as a follow-up before any multi-tenant production rollout.

Revision ID: 0003_abac_columns
Revises: 0002_hierarchy_scope
Create Date: 2026-06-02
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from mlpal_memory_graph.core.config import get_settings

revision = "0003_abac_columns"
down_revision = "0002_hierarchy_scope"
branch_labels = None
depends_on = None

SCHEMA = get_settings().db_schema or None


def upgrade() -> None:
    qualified = f'"{SCHEMA}".' if SCHEMA else ""

    op.add_column(
        "nodes",
        sa.Column("classification", sa.String(16), nullable=False, server_default="internal"),
        schema=SCHEMA,
    )
    op.add_column("nodes", sa.Column("owner_user_id", sa.String(64), nullable=True), schema=SCHEMA)
    op.add_column(
        "edges",
        sa.Column("classification", sa.String(16), nullable=False, server_default="internal"),
        schema=SCHEMA,
    )

    for table in ("nodes", "edges"):
        op.execute(f"UPDATE {qualified}{table} SET classification = 'personal' WHERE scope = 'user'")
        op.execute(f"UPDATE {qualified}{table} SET classification = 'public' WHERE scope = 'global'")
    op.execute(f"UPDATE {qualified}nodes SET owner_user_id = scope_id WHERE scope = 'user'")


def downgrade() -> None:
    op.drop_column("edges", "classification", schema=SCHEMA)
    op.drop_column("nodes", "owner_user_id", schema=SCHEMA)
    op.drop_column("nodes", "classification", schema=SCHEMA)
