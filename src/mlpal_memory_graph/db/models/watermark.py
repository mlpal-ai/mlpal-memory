"""Watermark = per-source ingestion cursor for the watermark-tail adapters."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from .base import SCHEMA, Base


class MemoryWatermark(Base):
    __tablename__ = "memory_watermarks"

    source: Mapped[str] = mapped_column(String(128), primary_key=True)
    last_processed_ref: Mapped[str | None] = mapped_column(String(256))
    last_processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = ({"schema": SCHEMA},)
