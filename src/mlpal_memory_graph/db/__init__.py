"""Async SQLAlchemy engine + session factory + FastAPI ``get_session`` dependency.

Lazy module-global engine (mirrors the platform convention). For Postgres the
connection sets ``search_path`` to the service schema so models need no explicit
schema-qualified FKs; for SQLite (tests) the schema is ignored.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from ..core.config import get_settings

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        s = get_settings()
        url = s.database_url
        if url.startswith("postgresql"):
            # Schema-first search_path with ``public`` appended: our tables resolve from the
            # service schema, while extension types installed in public (pgvector's ``vector``)
            # stay resolvable — with search_path = schema only, ``SELECT '[1]'::vector`` fails
            # with UndefinedObject on any stack whose extension lives in public (the default).
            search_path = f"{s.db_schema},public" if s.db_schema else "public"
            _engine = create_async_engine(
                url,
                echo=s.debug,
                pool_size=s.db_pool_size,
                max_overflow=s.db_max_overflow,
                pool_pre_ping=True,
                connect_args={"server_settings": {"search_path": search_path}},
            )
        else:
            # sqlite/aiosqlite (tests) — no pool sizing, no search_path
            _engine = create_async_engine(url, echo=s.debug)
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            get_engine(), expire_on_commit=False, class_=AsyncSession
        )
    return _session_factory


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: yield a session, commit on success, rollback on error."""
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


def reset_engine() -> None:
    """Test helper: drop the cached engine/factory so a new URL takes effect."""
    global _engine, _session_factory
    _engine = None
    _session_factory = None
