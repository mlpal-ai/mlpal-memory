"""Materialized tsvector column for chunks — the lexical leg's latency fix.

Eval runs 192510/192522 measured p95 2.8s cold / 1.1s warm: the IDF-coverage scoring
recomputes ``to_tsvector`` over every matching row per term. A STORED generated column
computes it once per write; the GIN index moves onto the column and per-term checks
become index-friendly column ops. Postgres-only (SQLite uses the in-process leg).

Revision ID: 0013_chunk_tsv_column
Revises: 0012_chunk_fts_index
Create Date: 2026-07-20
"""

from __future__ import annotations

from alembic import op
from mlpal_memory_graph.core.config import get_settings

revision = "0013_chunk_tsv_column"
down_revision = "0012_chunk_fts_index"
branch_labels = None
depends_on = None

_SCHEMA = get_settings().db_schema or None
_PREFIX = f"{_SCHEMA}." if _SCHEMA else ""


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(
        f"ALTER TABLE {_PREFIX}chunks ADD COLUMN IF NOT EXISTS content_tsv tsvector "
        "GENERATED ALWAYS AS (to_tsvector('english'::regconfig, content)) STORED"
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS ix_chunk_tsv ON {_PREFIX}chunks USING GIN (content_tsv)"
    )
    op.execute(f"DROP INDEX IF EXISTS {_PREFIX}ix_chunk_fts")  # superseded expression index


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(
        f"CREATE INDEX IF NOT EXISTS ix_chunk_fts ON {_PREFIX}chunks "
        "USING GIN (to_tsvector('english'::regconfig, content))"
    )
    op.execute(f"DROP INDEX IF EXISTS {_PREFIX}ix_chunk_tsv")
    op.execute(f"ALTER TABLE {_PREFIX}chunks DROP COLUMN IF EXISTS content_tsv")
