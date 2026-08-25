"""Postgres fixture for the @pytest.mark.postgres suite — a DB built via ``alembic upgrade head``,
NOT ``create_all`` + hand-added extensions. This is what catches missing migrations (e.g. pg_trgm
for the lexical leg): the real migration chain (incl. 0007 HNSW indexes + 0008 pg_trgm/FTS/trgm
indexes) is applied into a fresh schema, so the tests exercise exactly the prod path.

Opt-in via MLPAL_TEST_POSTGRES_DSN (skipped otherwise). The schema drop/create runs in-process
(asyncpg), not via the shell, and the migration runs in a subprocess with its own MLPAL_DB_* env.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

_REPO = Path(__file__).resolve().parents[2]
_SCHEMA = "memory_ci_test"
DSN = os.getenv("MLPAL_TEST_POSTGRES_DSN")


@pytest_asyncio.fixture
async def pg_session():
    url = make_url(DSN)
    # 1. fresh, empty schema (in-process — not the shell, so the destructive-SQL guard is moot)
    admin = create_async_engine(DSN)
    async with admin.begin() as conn:
        await conn.execute(text(f"DROP SCHEMA IF EXISTS {_SCHEMA} CASCADE"))
        await conn.execute(text(f"CREATE SCHEMA {_SCHEMA}"))
    await admin.dispose()

    # 2. alembic upgrade head INTO that schema — exercises the real migration chain (0008 creates
    #    pg_trgm + FTS/trgm indexes; 0007 creates the HNSW indexes). Subprocess gets a clean
    #    MLPAL_DB_* env so it talks Postgres, not the suite's SQLite override.
    env = {k: v for k, v in os.environ.items() if not k.startswith("MLPAL_")}
    env.update(
        {
            "MLPAL_ENVIRONMENT": "test",
            "MLPAL_DB_HOST": url.host or "127.0.0.1",
            "MLPAL_DB_PORT": str(url.port or 5432),
            "MLPAL_DB_NAME": url.database or "",
            "MLPAL_DB_USER": url.username or "",
            "MLPAL_DB_PASSWORD": url.password or "",
            "MLPAL_DB_SCHEMA": _SCHEMA,
            "MLPAL_DEV_AUTH": "true",
        }
    )
    r = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=_REPO,
        env=env,
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, f"alembic upgrade head failed:\n{r.stdout}\n{r.stderr}"

    # 3. a session whose search_path is the migrated schema (the ORM models are schema-less in the
    #    test process, so unqualified table refs resolve there; extensions live in public).
    engine = create_async_engine(
        DSN, connect_args={"server_settings": {"search_path": f"{_SCHEMA},public"}}
    )
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        yield s
    await engine.dispose()
    admin = create_async_engine(DSN)
    async with admin.begin() as conn:
        await conn.execute(text(f"DROP SCHEMA IF EXISTS {_SCHEMA} CASCADE"))
    await admin.dispose()
