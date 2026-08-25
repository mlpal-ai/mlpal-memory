"""Declarative base + shared helpers. Schema is taken from settings (``memory`` in
prod, ``None`` for SQLite tests when MLPAL_DB_SCHEMA is empty)."""

from __future__ import annotations

import uuid

from sqlalchemy.orm import DeclarativeBase

from ...core.config import get_settings

# Empty schema (e.g. SQLite tests) -> None (default schema).
SCHEMA: str | None = get_settings().db_schema or None


class Base(DeclarativeBase):
    pass


def new_uuid() -> str:
    return str(uuid.uuid4())
