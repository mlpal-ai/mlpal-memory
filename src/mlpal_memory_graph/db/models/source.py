"""Registered sources (memory v7 WP4, design §9): the company's data stays where it is; memory keeps a
manifest of what exists (cold), admits items on demand or by salience, and reads databases through a
tool. Rows here are catalogue entries, never the content."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from .base import SCHEMA, Base, new_uuid


class MemorySource(Base):
    __tablename__ = "memory_sources"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    org_id: Mapped[str | None] = mapped_column(String(64), index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)  # files | sql
    scope: Mapped[str] = mapped_column(String(16), default="org", server_default="org")
    scope_id: Mapped[str | None] = mapped_column(String(64))
    workspace: Mapped[str | None] = mapped_column(String(256))
    config: Mapped[dict] = mapped_column(JSON, default=dict)      # files: {root}; sql: {url, tables, row_limit}
    status: Mapped[str] = mapped_column(String(16), default="cold", server_default="cold")  # cold | warm
    item_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_by: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    indexed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (UniqueConstraint("org_id", "name", name="uq_source_name"), {"schema": SCHEMA})


class SourceItem(Base):
    """One item of a cold files source: its locator and title, not its content. Admission (by a
    question that needed it, or by a manual promote) records the document it became."""

    __tablename__ = "memory_source_items"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    org_id: Mapped[str | None] = mapped_column(String(64), index=True)
    source_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    ref: Mapped[str] = mapped_column(String(1024), nullable=False)   # path relative to the source root
    title: Mapped[str | None] = mapped_column(String(512))
    size: Mapped[int | None] = mapped_column(Integer)
    modified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    admitted: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    admitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    admitted_by: Mapped[str | None] = mapped_column(String(64))       # "question" | "promote" | "salience"
    document_event_id: Mapped[str | None] = mapped_column(String(128))
    declined_reason: Mapped[str | None] = mapped_column(String(256))

    __table_args__ = (UniqueConstraint("source_id", "ref", name="uq_source_item"), {"schema": SCHEMA})
