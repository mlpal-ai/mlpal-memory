"""Workspace notes — the curated working-memory tier.

A ``Note`` is a small, sectioned markdown document that agents and humans update IN PLACE:
what we are doing now, what was decided, what is open, preferences, pointers. It is the
counterpart of the projection (``services/projection.py``): the projection is a rebuildable
shadow of the graph; a note is a source of truth for working state, and every edit is kept
as a ``NoteVersion`` (plus a content-free ``note.updated`` episode) so it is diffable and
answerable as-of any instant.

Keyed by (org, scope, scope_id, workspace). ``workspace == ""`` is the scope-level note
(the org index; a user's preferences); a non-empty workspace is the working note for that
workspace. Same governance columns as documents so the one scope predicate applies.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from .base import SCHEMA, Base, new_uuid


class Note(Base):
    __tablename__ = "notes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    org_id: Mapped[str | None] = mapped_column(String(64), index=True)  # tenant boundary
    scope: Mapped[str] = mapped_column(String(16), default="org", server_default="org")
    scope_id: Mapped[str] = mapped_column(String(64))
    workspace: Mapped[str] = mapped_column(String(256), default="", server_default="")
    classification: Mapped[str] = mapped_column(
        String(16), default="internal", server_default="internal"
    )
    owner_user_id: Mapped[str | None] = mapped_column(String(64))
    title: Mapped[str | None] = mapped_column(String(256))
    body: Mapped[str] = mapped_column(Text, default="")
    version: Mapped[int] = mapped_column(Integer, default=0)
    updated_by: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint("org_id", "scope", "scope_id", "workspace", name="uq_note_key"),
        Index("ix_note_scope", "org_id", "scope", "scope_id"),
        {"schema": SCHEMA},
    )


class NoteVersion(Base):
    __tablename__ = "note_versions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    note_id: Mapped[str] = mapped_column(String(36), index=True)
    org_id: Mapped[str | None] = mapped_column(String(64), index=True)
    version: Mapped[int] = mapped_column(Integer)
    body: Mapped[str] = mapped_column(Text)
    updated_by: Mapped[str | None] = mapped_column(String(64))
    reason: Mapped[str | None] = mapped_column(String(512))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("note_id", "version", name="uq_note_version"),
        {"schema": SCHEMA},
    )
