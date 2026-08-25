"""Episode = one captured MLPal interaction (append-only, dedup by event_id)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from .base import SCHEMA, Base


class Episode(Base):
    __tablename__ = "episodes"

    # client-generated UUID — primary key gives idempotent ON CONFLICT DO NOTHING ingest
    event_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    org_id: Mapped[str | None] = mapped_column(String(64), index=True)  # tenant boundary
    # target scope: which layer this episode's extracted memory lands in (default: org).
    scope: Mapped[str] = mapped_column(String(16), default="org", server_default="org")
    scope_id: Mapped[str | None] = mapped_column(String(64))
    # v3: workspace facet — which repo/project (within the personal store) this came from.
    workspace: Mapped[str | None] = mapped_column(String(256), index=True)
    # v3: lifecycle of memories derived from this episode (committed | working).
    lifecycle: Mapped[str] = mapped_column(
        String(16), default="committed", server_default="committed"
    )
    actor: Mapped[dict] = mapped_column(JSON, default=dict)
    source: Mapped[str] = mapped_column(String(32), index=True)
    action_type: Mapped[str] = mapped_column(String(64), index=True)
    subject: Mapped[dict] = mapped_column(JSON, default=dict)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    content: Mapped[str | None] = mapped_column(Text)  # raw text; off by default
    source_ref: Mapped[str | None] = mapped_column(String(256))
    schema_version: Mapped[int] = mapped_column(Integer, default=1)

    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    processed: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    tier: Mapped[str | None] = mapped_column(String(16))  # llm | deterministic
    # set when the fold gate declined the episode (consent off/pause, or a policy deny rule).
    # the row is retained for "what we declined to learn" audit; it is never folded.
    dropped_reason: Mapped[str | None] = mapped_column(String(128), index=True)
    error: Mapped[str | None] = mapped_column(Text)
    # bounded retries: failed folds increment error_count; at the cap the episode is
    # dead-lettered (dead_at set), excluded from the worker cursor, kept for audit/replay.
    error_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    dead_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = ({"schema": SCHEMA},)
