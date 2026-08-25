"""Edge = a bi-temporal, ontology-typed relationship between two nodes.

Bi-temporal (Graphiti model): ``valid_at``/``invalid_at`` track when the fact was true
in the world; ``ingested_at``/``expired_at`` track when we knew it. Contradictions
*invalidate* (set ``invalid_at``) instead of deleting — non-lossy, auditable.

References to nodes are logical (no hard FK), matching the platform convention that
events survive producer deletes.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, Index, Integer, String, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column

from ..types import Embedding
from .base import SCHEMA, Base, new_uuid


class Edge(Base):
    __tablename__ = "edges"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    org_id: Mapped[str | None] = mapped_column(String(64), index=True)  # tenant boundary
    scope: Mapped[str] = mapped_column(String(16), default="org", server_default="org")
    scope_id: Mapped[str | None] = mapped_column(String(64))
    type: Mapped[str] = mapped_column(String(64), index=True)  # relation type
    src_id: Mapped[str] = mapped_column(String(36), index=True)
    dst_id: Mapped[str] = mapped_column(String(36), index=True)
    fact: Mapped[str | None] = mapped_column(Text)  # natural-language fact
    classification: Mapped[str] = mapped_column(
        String(16), default="internal", server_default="internal"
    )
    source: Mapped[str | None] = mapped_column(String(32))  # provenance: producing source
    # DERIVED provenance: link/score back to the direct memory this fact was inferred from.
    confidence: Mapped[float | None] = mapped_column()
    derived_from: Mapped[list | None] = mapped_column(JSON)
    # embedding of the normalized fact sentence — powers semantic contradiction candidates (D3).
    fact_embedding: Mapped[list | None] = mapped_column(Embedding())
    embedding_model: Mapped[str | None] = mapped_column(String(64))
    embedding_dim: Mapped[int | None] = mapped_column(Integer)
    props: Mapped[dict] = mapped_column(JSON, default=dict)

    # bi-temporal stamps
    valid_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    invalid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    expired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    ontology_version: Mapped[str] = mapped_column(String(32), default="core/0.1.0")

    # --- v3 store model (migration 0011): see node.py for semantics ---
    workspace: Mapped[str | None] = mapped_column(String(256), index=True)
    status: Mapped[str] = mapped_column(
        String(16), default="committed", server_default="committed"
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_edge_pair", "src_id", "dst_id"),
        Index("ix_edge_org_type", "org_id", "type"),
        Index("ix_edge_scope", "org_id", "scope", "scope_id"),
        # temporal access paths (P1.4): hot "current facts", invalidation lookup, time slices.
        Index(
            "ix_edge_current",
            "org_id",
            "src_id",
            "type",
            sqlite_where=text("invalid_at IS NULL AND expired_at IS NULL"),
            postgresql_where=text("invalid_at IS NULL AND expired_at IS NULL"),
        ),
        Index("ix_edge_invalidation", "org_id", "src_id", "type", "invalid_at"),
        Index("ix_edge_valid_time", "valid_at", "invalid_at"),
        Index("ix_edge_system_time", "ingested_at", "expired_at"),
        {"schema": SCHEMA},
    )
