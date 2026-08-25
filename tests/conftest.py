"""Test harness — runs entirely on SQLite (no Postgres/network needed).

Sets MLPAL_* env BEFORE importing the app (platform convention), creates a fresh DB per
test, and uses dev auth (X-Test-* headers). Mirrors mlpal-storage-service/tests/conftest.py.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

os.environ.setdefault("MLPAL_ENVIRONMENT", "test")
os.environ.setdefault("MLPAL_DEV_AUTH", "true")
os.environ.setdefault("MLPAL_DB_SCHEMA", "")  # no schema on sqlite
os.environ.setdefault("MLPAL_UPDATER_ENABLED", "false")
_DB = Path(tempfile.gettempdir()) / "mlpal_memgraph_test.db"
os.environ.setdefault("MLPAL_DATABASE_URL_OVERRIDE", f"sqlite+aiosqlite:///{_DB}")

import pytest_asyncio  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402

from mlpal_memory_graph.db import get_engine, get_session_factory, reset_engine  # noqa: E402
from mlpal_memory_graph.db.models import Base  # noqa: E402


@pytest_asyncio.fixture(autouse=True)
async def _db():
    if _DB.exists():
        _DB.unlink()
    reset_engine()
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    await engine.dispose()
    reset_engine()
    if _DB.exists():
        _DB.unlink()


@pytest_asyncio.fixture
async def session():
    factory = get_session_factory()
    async with factory() as s:
        yield s


@pytest_asyncio.fixture
async def client():
    from mlpal_memory_graph.main import create_app

    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c
