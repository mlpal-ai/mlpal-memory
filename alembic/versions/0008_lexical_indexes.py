"""pg_trgm + lexical (FTS/trigram) indexes for the hybrid lexical leg (PR3 / #12)

The hybrid lexical leg (PostgresDriver.lexical_search_nodes) uses weighted tsvector FTS plus
pg_trgm's ``%`` / ``similarity()`` for identifier recall (service/repo/sk_* names). pg_trgm is
NOT created by 0007 (which only enables ``vector``), so without this migration the lexical leg
errors on a freshly-migrated DB and on prod RDS. Postgres-only: SQLite uses the portable
token-overlap fallback and needs no extension.

Indexes (Postgres):
  - GIN over the FTS expression (IMMUTABLE form — must match the query's ``to_tsvector`` exactly
    so the planner uses it).
  - GIN trigram on ``name`` and ``key`` for ``%`` similarity and identifier prefix lookups.

Revision ID: 0008_lexical_indexes
Revises: 0007_pgvector
Create Date: 2026-06-09
"""

from __future__ import annotations

from alembic import op

from mlpal_memory_graph.core.config import get_settings

revision = "0008_lexical_indexes"
down_revision = "0007_pgvector"
branch_labels = None
depends_on = None

SCHEMA = get_settings().db_schema or None
_Q = f'"{SCHEMA}".' if SCHEMA else ""

# IMMUTABLE form (required for an index expression): cast the config to regconfig and use ``||``
# rather than concat_ws (which is only STABLE). Must stay identical to the query's tsvector in
# PostgresDriver.lexical_search_nodes so the planner can use this index.
_FTS_EXPR = "to_tsvector('english'::regconfig, coalesce(name, '') || ' ' || coalesce(summary, ''))"


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return  # SQLite uses the portable lexical fallback; nothing to install
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute(f"CREATE INDEX IF NOT EXISTS ix_node_fts ON {_Q}nodes USING gin ({_FTS_EXPR})")
    op.execute(
        f"CREATE INDEX IF NOT EXISTS ix_node_name_trgm ON {_Q}nodes USING gin (name gin_trgm_ops)"
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS ix_node_key_trgm ON {_Q}nodes USING gin (key gin_trgm_ops)"
    )


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for name in ("ix_node_key_trgm", "ix_node_name_trgm", "ix_node_fts"):
        op.execute(f"DROP INDEX IF EXISTS {_Q}{name}")
    # leave the pg_trgm extension installed — dropping it could break other objects.
