"""Direct memory: documents and their retrievable verbatim chunks.

DIRECT memory is content stored as-is and retrievable as passages (conversation history, PDFs,
docs) — as opposed to DERIVED memory (the inferred nodes/edges in the graph). A ``Document`` is
one source artifact; a ``Chunk`` is an embedded, individually-retrievable slice of it. Both
carry the same (scope, scope_id, classification, owner, source) governance columns as the graph,
so the one scope predicate (``db/scoping.py``) and the same consent/policy/secret gates apply.

See docs/redesign/design-proposal.md §14 (direct vs derived memory).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from ..types import Embedding
from .base import SCHEMA, Base, new_uuid


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    org_id: Mapped[str | None] = mapped_column(String(64), index=True)  # tenant boundary
    scope: Mapped[str] = mapped_column(String(16), default="org", server_default="org")
    scope_id: Mapped[str | None] = mapped_column(String(64))
    # v3 (migration 0011): workspace facet + document event-time (bitemporal valid time —
    # when the content was true/written, vs created_at = when we ingested it).
    workspace: Mapped[str | None] = mapped_column(String(256), index=True)
    valid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    classification: Mapped[str] = mapped_column(
        String(16), default="internal", server_default="internal"
    )
    owner_user_id: Mapped[str | None] = mapped_column(String(64))
    source: Mapped[str | None] = mapped_column(String(32))  # producing source type
    source_ref: Mapped[str | None] = mapped_column(String(256))  # dedup / provenance key
    title: Mapped[str | None] = mapped_column(String(512))
    uri: Mapped[str | None] = mapped_column(String(1024))
    props: Mapped[dict] = mapped_column(JSON, default=dict)

    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    expired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_document_scope", "org_id", "scope", "scope_id"),
        {"schema": SCHEMA},
    )


class Chunk(Base):
    __tablename__ = "chunks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    document_id: Mapped[str] = mapped_column(String(36), index=True)
    org_id: Mapped[str | None] = mapped_column(String(64), index=True)  # tenant boundary
    scope: Mapped[str] = mapped_column(String(16), default="org", server_default="org")
    scope_id: Mapped[str | None] = mapped_column(String(64))
    workspace: Mapped[str | None] = mapped_column(String(256), index=True)
    classification: Mapped[str] = mapped_column(
        String(16), default="internal", server_default="internal"
    )
    owner_user_id: Mapped[str | None] = mapped_column(String(64))
    source: Mapped[str | None] = mapped_column(String(32))
    ordinal: Mapped[int] = mapped_column(Integer, default=0)  # position within the document
    content: Mapped[str] = mapped_column(Text)  # the verbatim passage
    # embedding: pgvector(1536) on Postgres, JSON on SQLite (dialect-aware). Stamp model + dim.
    embedding: Mapped[list | None] = mapped_column(Embedding())
    embedding_model: Mapped[str | None] = mapped_column(String(64))
    embedding_dim: Mapped[int | None] = mapped_column(Integer)

    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        Index("ix_chunk_scope", "org_id", "scope", "scope_id"),
        {"schema": SCHEMA},
    )
