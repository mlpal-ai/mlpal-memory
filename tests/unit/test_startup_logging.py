"""The in-process `alembic upgrade head` at startup must not silence the server's own loggers.

alembic's env.py calls logging.config.fileConfig(alembic.ini); its default disable_existing_loggers=True
disables every logger that already exists and is not named in the ini, uvicorn's error and access
loggers included, so an unhandled exception left no trace."""

from __future__ import annotations

import logging

from alembic import command

from mlpal_memory_graph.core.config import get_settings
from mlpal_memory_graph.main import _alembic_config

SERVER_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.access", "mlpal_memory_graph", "mlpal_memory_graph.main")


def test_the_startup_migration_leaves_the_servers_loggers_enabled(tmp_path, monkeypatch):
    # the server's alembic Config and env.py (where fileConfig runs) against a scratch database; `current`
    # executes env.py exactly as `upgrade head` does, without the DDL, which SQLite cannot replay in full
    monkeypatch.setenv("MLPAL_DATABASE_URL_OVERRIDE", f"sqlite+aiosqlite:///{tmp_path / 'startup.db'}")
    monkeypatch.setenv("MLPAL_DB_SCHEMA", "")
    get_settings.cache_clear()
    root = logging.getLogger()
    saved_handlers, saved_level = list(root.handlers), root.level  # fileConfig replaces root's handlers and level
    try:
        for name in SERVER_LOGGERS:
            logging.getLogger(name).disabled = False  # they exist before the migration runs, as in the server
        command.current(_alembic_config())
        silenced = [name for name in SERVER_LOGGERS if logging.getLogger(name).disabled]
        assert silenced == [], f"fileConfig disabled {silenced}; env.py must pass disable_existing_loggers=False"
    finally:
        get_settings.cache_clear()
        root.handlers[:] = saved_handlers
        root.setLevel(saved_level)
