"""pgvector + fact_embedding + embedding provenance + temporal indexes (PR2 / P1.1-1.2-1.4)

On **Postgres**: enable pgvector, convert the JSON embedding columns to ``vector(1536)`` (D2),
add ``edges.fact_embedding`` (D3), build HNSW indexes, and add temporal indexes. On other
dialects (defensive — the unit suite builds tables via ``create_all``, not migrations) the
embedding columns stay JSON. pgvector is referenced only via raw SQL so this module imports
without the ``pgvector`` package (keeps offline ``alembic`` usable).

Every embedded row also gets ``embedding_model`` + ``embedding_dim`` so a future re-embed
migration can detect and never mix embedding spaces.

Revision ID: 0007_pgvector
Revises: 0006_direct_memory
Create Date: 2026-06-08
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from mlpal_memory_graph.core.config import get_settings
from mlpal_memory_graph.db.types import EMBED_DIM

revision = "0007_pgvector"
down_revision = "0006_direct_memory"
branch_labels = None
depends_on = None

SCHEMA = get_settings().db_schema or None
_Q = f'"{SCHEMA}".' if SCHEMA else ""
_CURRENT = "invalid_at IS NULL AND expired_at IS NULL"


def upgrade() -> None:
    is_pg = op.get_bind().dialect.name == "postgresql"

    # provenance columns (portable)
    for table in ("nodes", "edges", "chunks"):
        op.add_column(table, sa.Column("embedding_model", sa.String(64)), schema=SCHEMA)
        op.add_column(table, sa.Column("embedding_dim", sa.Integer()), schema=SCHEMA)

    if is_pg:
        op.execute("CREATE EXTENSION IF NOT EXISTS vector")
        # convert existing JSON embedding columns -> vector(1536) (no-op on empty tables)
        for table in ("nodes", "chunks"):
            op.execute(
                f"ALTER TABLE {_Q}{table} ALTER COLUMN embedding TYPE vector({EMBED_DIM}) "
                f"USING embedding::text::vector"
            )
        op.execute(f"ALTER TABLE {_Q}edges ADD COLUMN fact_embedding vector({EMBED_DIM})")
        # HNSW indexes (cosine). The driver co-filters by scope AFTER the ANN scan, so callers
        # set hnsw.iterative_scan = relaxed_order (see PostgresDriver.search_nodes).
        for name, table, col in (
            ("ix_node_embedding_hnsw", "nodes", "embedding"),
            ("ix_chunk_embedding_hnsw", "chunks", "embedding"),
            ("ix_edge_fact_embedding_hnsw", "edges", "fact_embedding"),
        ):
            op.execute(
                f"CREATE INDEX IF NOT EXISTS {name} ON {_Q}{table} "
                f"USING hnsw ({col} vector_cosine_ops) WITH (m = 16, ef_construction = 64)"
            )
    else:
        op.add_column("edges", sa.Column("fact_embedding", sa.JSON()), schema=SCHEMA)

    # temporal indexes (P1.4) — both dialects
    op.create_index(
        "ix_edge_current",
        "edges",
        ["org_id", "src_id", "type"],
        schema=SCHEMA,
        postgresql_where=sa.text(_CURRENT),
        sqlite_where=sa.text(_CURRENT),
    )
    op.create_index(
        "ix_edge_invalidation", "edges", ["org_id", "src_id", "type", "invalid_at"], schema=SCHEMA
    )
    op.create_index("ix_edge_valid_time", "edges", ["valid_at", "invalid_at"], schema=SCHEMA)
    op.create_index("ix_edge_system_time", "edges", ["ingested_at", "expired_at"], schema=SCHEMA)


def downgrade() -> None:
    is_pg = op.get_bind().dialect.name == "postgresql"
    for name in (
        "ix_edge_system_time",
        "ix_edge_valid_time",
        "ix_edge_invalidation",
        "ix_edge_current",
    ):
        op.drop_index(name, table_name="edges", schema=SCHEMA)

    if is_pg:
        for name in (
            "ix_edge_fact_embedding_hnsw",
            "ix_chunk_embedding_hnsw",
            "ix_node_embedding_hnsw",
        ):
            op.execute(f"DROP INDEX IF EXISTS {_Q}{name}")
        op.execute(f"ALTER TABLE {_Q}edges DROP COLUMN fact_embedding")
        for table in ("nodes", "chunks"):
            op.execute(
                f"ALTER TABLE {_Q}{table} ALTER COLUMN embedding TYPE json USING embedding::text::json"
            )
    else:
        op.drop_column("edges", "fact_embedding", schema=SCHEMA)

    for table in ("chunks", "edges", "nodes"):
        op.drop_column(table, "embedding_dim", schema=SCHEMA)
        op.drop_column(table, "embedding_model", schema=SCHEMA)
