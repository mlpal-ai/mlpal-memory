"""Dialect-aware embedding column.

Stored as pgvector ``vector(dim)`` on Postgres (HNSW-indexable, queried with ``<=>``), and as
a JSON list on every other dialect — so the offline SQLite unit suite runs with **pgvector
never a hard dependency** (it is imported lazily, only when the dialect is actually Postgres).
Bind and return a plain ``list[float]`` on both. See design-proposal §7 / D2 (dim = 1536).
"""

from __future__ import annotations

from sqlalchemy import JSON
from sqlalchemy.types import TypeDecorator

# D2 (locked): platform-standard embedding dimension. Stamp `embedding_dim` on every row so a
# future re-embed migration can detect/avoid mixing embedding spaces.
EMBED_DIM = 1536


class Embedding(TypeDecorator):
    impl = JSON
    cache_ok = True

    def __init__(self, dim: int = EMBED_DIM) -> None:
        self.dim = dim
        super().__init__()

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            from pgvector.sqlalchemy import Vector  # lazy: Postgres-only dependency

            return dialect.type_descriptor(Vector(self.dim))
        return dialect.type_descriptor(JSON())

    def process_result_value(self, value, dialect):
        # pgvector returns a numpy array on Postgres; normalize to plain list[float] so callers
        # (cosine baseline, projection, JSON-serialization) behave identically across dialects.
        if value is None:
            return None
        return [float(x) for x in value]
