"""RLS backstop — defence-in-depth tenant isolation (PR6 / #15)

The PRIMARY isolation stays the app-layer scope predicate (db/scoping.scope_clause). This adds
Row-Level Security on the tenant tables as a second layer: when the read path sets the
``app.current_org`` GUC (only when MLPAL_RLS_ENABLED=true), the DB itself restricts rows to that
tenant, so even a buggy/forgotten scope_clause can't leak across tenants.

The policy is PERMISSIVE-WHEN-UNSET: with the GUC unset OR empty (the default, and all non-request
contexts like the ingest worker) it allows everything, so enabling RLS changes nothing until the
app opts in. GLOBAL rows (org_id IS NULL) are always visible. Postgres-only; SQLite skips.

NOTE: a superuser bypasses RLS entirely — in prod the app must connect as a NON-superuser role for
this backstop to bite (the least-privilege role; see docs/redesign/design-proposal.md §12.3).

Revision ID: 0009_rls_backstop
Revises: 0008_lexical_indexes
Create Date: 2026-06-09
"""

from __future__ import annotations

from alembic import op

from mlpal_memory_graph.core.config import get_settings

revision = "0009_rls_backstop"
down_revision = "0008_lexical_indexes"
branch_labels = None
depends_on = None

SCHEMA = get_settings().db_schema or None
_Q = f'"{SCHEMA}".' if SCHEMA else ""
_TABLES = ("nodes", "edges", "documents", "chunks", "episodes")  # tenant tables (have org_id)

# permissive when the GUC is unset OR empty (coalesce handles both — a reset GUC reads as '');
# global rows (org_id NULL) always visible; otherwise the row's org must match the GUC.
_PRED = (
    "coalesce(current_setting('app.current_org', true), '') = '' "
    "OR org_id IS NULL OR org_id = current_setting('app.current_org', true)"
)


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for t in _TABLES:
        op.execute(f"ALTER TABLE {_Q}{t} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {_Q}{t} FORCE ROW LEVEL SECURITY")  # subject the owner too
        op.execute(
            f"CREATE POLICY {t}_tenant_isolation ON {_Q}{t} FOR ALL "
            f"USING ({_PRED}) WITH CHECK ({_PRED})"
        )


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for t in reversed(_TABLES):
        op.execute(f"DROP POLICY IF EXISTS {t}_tenant_isolation ON {_Q}{t}")
        op.execute(f"ALTER TABLE {_Q}{t} DISABLE ROW LEVEL SECURITY")
