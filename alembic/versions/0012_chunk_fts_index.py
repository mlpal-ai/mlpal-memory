"""Chunk full-text GIN index — the direct tier's lexical leg at scale.

The v3 eval (evals/results/20260730-182214-memory.json) exposed the direct tier's
lexical leg as a whole-query phrase ILIKE — silent for multi-word queries. The leg is
now term-based tsvector ranking; this index makes it an index scan. The expression
MUST match services/direct.py's query form exactly (regconfig cast, bare column) or
Postgres won't use the index. Postgres-only; SQLite tests use the portable overlap leg.

Revision ID: 0012_chunk_fts_index
Revises: 0011_v3_store_model
Create Date: 2026-07-20
"""

from __future__ import annotations

from alembic import op
from mlpal_memory_graph.core.config import get_settings

revision = "0012_chunk_fts_index"
down_revision = "0011_v3_store_model"
branch_labels = None
depends_on = None

_SCHEMA = get_settings().db_schema or None
_PREFIX = f"{_SCHEMA}." if _SCHEMA else ""


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(
        f"CREATE INDEX IF NOT EXISTS ix_chunk_fts ON {_PREFIX}chunks "
        "USING GIN (to_tsvector('english'::regconfig, content))"
    )


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(f"DROP INDEX IF EXISTS {_PREFIX}ix_chunk_fts")
