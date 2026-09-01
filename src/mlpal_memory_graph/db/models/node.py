"""Node = an ontology-typed entity in the memory graph."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from ..types import Embedding
from .base import SCHEMA, Base, new_uuid


class Node(Base):
    __tablename__ = "nodes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    org_id: Mapped[str | None] = mapped_column(String(64), index=True)  # tenant boundary
    # hierarchy: which layer this node is owned by (global|org|team|user) + the id within it.
    scope: Mapped[str] = mapped_column(String(16), default="org", server_default="org")
    scope_id: Mapped[str | None] = mapped_column(String(64))
    type: Mapped[str] = mapped_column(String(64), index=True)  # ontology class
    key: Mapped[str] = mapped_column(String(512))  # canonical natural key
    name: Mapped[str] = mapped_column(String(512))
    summary: Mapped[str | None] = mapped_column(Text)
    # access/visibility (ABAC): public|internal|personal. owner set for personal (user) memory.
    classification: Mapped[str] = mapped_column(
        String(16), default="internal", server_default="internal"
    )
    owner_user_id: Mapped[str | None] = mapped_column(String(64))
    # provenance: the source type this node was first learned from (steers source routing).
    source: Mapped[str | None] = mapped_column(String(32), index=True)
    # this node is DERIVED (inferred); these link/score it back to the direct memory it came
    # from. derived_from holds episode and/or chunk ids; confidence is None for rule extraction.
    confidence: Mapped[float | None] = mapped_column()
    derived_from: Mapped[list | None] = mapped_column(JSON)
    props: Mapped[dict] = mapped_column(JSON, default=dict)
    # embedding: pgvector(1536) on Postgres, JSON on SQLite (dialect-aware). Stamp the model +
    # dim used so a future re-embed migration never mixes embedding spaces (D2).
    # usage evidence for retention/GC (migration 0014): has this memory EVER been
    # served, and when last. Bumped fire-and-forget by the read path.
    served_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    last_served_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    embedding: Mapped[list | None] = mapped_column(Embedding())
    embedding_model: Mapped[str | None] = mapped_column(String(64))
    embedding_dim: Mapped[int | None] = mapped_column(Integer)
    ontology_version: Mapped[str] = mapped_column(String(32), default="core/0.1.0")

    # --- v3 store model (migration 0011) ---
    # workspace facet: partitions the PERSONAL store by repo/project ("me, in repo X").
    # NOT an authz surface — user scope stays owner-only; the facet focuses retrieval.
    workspace: Mapped[str | None] = mapped_column(String(256), index=True)
    # lifecycle: working (session-scoped, TTL'd) → committed (durable personal) →
    # published (proposed/merged into a shared scope).
    status: Mapped[str] = mapped_column(
        String(16), default="committed", server_default="committed"
    )
    # working-tier TTL; NULL = durable. Swept by the retention worker.
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # re-observation count: the same insight seen again bumps this instead of duplicating —
    # a ranking signal (frequently re-learned facts matter) and a dedup outcome.
    observed_count: Mapped[int] = mapped_column(Integer, default=1, server_default="1")

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        # Identity is per-scope: the same key may exist at org, team and user layers.
        UniqueConstraint("org_id", "scope", "scope_id", "type", "key", name="uq_node_identity"),
        Index("ix_node_scope", "org_id", "scope", "scope_id"),
        {"schema": SCHEMA},
    )
