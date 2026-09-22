"""The HOP registry (memory v7 WP6/WP8): one row per HOP per tenant, carrying what the design's
registry entry named (owner, mode, sharing, tenant) and the memory contract the HOP declared (topics,
reads, writes, ontology). The topics of every registered HOP together are the index across HOP
memories that routes a question (design: "the company brain is shared company facts plus an index
across HOP memories")."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from .base import SCHEMA, Base, new_uuid


class MemoryHop(Base):
    __tablename__ = "memory_hops"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    org_id: Mapped[str | None] = mapped_column(String(64), index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    version: Mapped[str | None] = mapped_column(String(32))
    owner_user_id: Mapped[str | None] = mapped_column(String(64))
    mode: Mapped[str] = mapped_column(String(16), default="review", server_default="review")   # auto | review | manual
    sharing: Mapped[str] = mapped_column(String(16), default="none", server_default="none")   # none | org | fleet
    tenant: Mapped[str | None] = mapped_column(String(64))
    workspace: Mapped[str | None] = mapped_column(String(256))
    contract: Mapped[dict] = mapped_column(JSON, default=dict)   # {topics: [...], reads: [...], writes: [...], ontology: [...]}
    registered_by: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (UniqueConstraint("org_id", "name", name="uq_hop_name"), {"schema": SCHEMA})
