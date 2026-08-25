"""#14 item-4: the real LangGraphStoreSource.list_items tail, against a SEEDED local store table
(@pytest.mark.postgres). The live run against the agent runtime's mlpal_events.store is pending a
memory_reader read DSN (hand-back). Here we prove the keyset tail + dedup + fail-loud contract on a
table that mirrors the langgraph AsyncPostgresStore schema (namespace text[], key, value jsonb)."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime

import pytest
import pytest_asyncio

from mlpal_memory_graph.ingest import SourceConfig
from mlpal_memory_graph.ingest.plugins.langgraph_store import LangGraphStoreSource

DSN = os.getenv("MLPAL_TEST_POSTGRES_DSN")
PG_DSN = DSN.replace("+asyncpg", "") if DSN else None  # asyncpg wants a plain postgres:// DSN
pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(not DSN, reason="set MLPAL_TEST_POSTGRES_DSN to run"),
]

SCHEMA = "lg_test"
# prod-verified langgraph store shape (2026-06-10): prefix is dot-joined TEXT, PK (prefix, key)
NS = "user.7.agent.3.chat.9"
T1 = datetime(2026, 1, 1, tzinfo=UTC)
T2 = datetime(2026, 1, 2, tzinfo=UTC)


@pytest_asyncio.fixture
async def store():
    import asyncpg

    conn = await asyncpg.connect(PG_DSN)
    await conn.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
    await conn.execute(f"CREATE SCHEMA {SCHEMA}")
    await conn.execute(
        f"CREATE TABLE {SCHEMA}.store (prefix text, key text, value jsonb, "
        f"created_at timestamptz DEFAULT now(), updated_at timestamptz, "
        f"PRIMARY KEY (prefix, key))"
    )
    yield conn
    await conn.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
    await conn.close()


def _source():
    return LangGraphStoreSource(
        SourceConfig(type="langgraph_store", options={"read_dsn": PG_DSN, "schema": SCHEMA})
    )


async def _insert(conn, key, value, updated_at, ns=NS):
    await conn.execute(
        f"INSERT INTO {SCHEMA}.store (prefix, key, value, updated_at) "
        f"VALUES ($1, $2, $3::jsonb, $4)",
        ns,
        key,
        json.dumps(value),
        updated_at,
    )


async def test_tail_maps_message_and_trace(store):
    await _insert(store, "k1", {"type": "MESSAGE", "data": {"content": "hello"}}, T1)
    await _insert(store, "k2", {"type": "TRACE", "data": {"trace_type": "LLM"}}, T2)

    items, cursor = await _source().list_items(None, 10)
    by_action = {i.metadata["action_type"]: i for i in items}
    assert by_action["chat.message"].content == "hello"
    assert by_action["agent.tool_called"].content is None
    assert cursor and "|" in cursor  # composite (updated_at, key) watermark advanced


async def test_keyset_progresses_and_does_not_repeat(store):
    await _insert(store, "k1", {"type": "MESSAGE", "data": {"content": "one"}}, T1)
    await _insert(store, "k2", {"type": "MESSAGE", "data": {"content": "two"}}, T2)
    src = _source()

    first, c1 = await src.list_items(None, 1)
    assert [i.content for i in first] == ["one"]
    second, c2 = await src.list_items(c1, 10)
    assert [i.content for i in second] == ["two"]  # keyset moved past k1
    third, _ = await src.list_items(c2, 10)
    assert third == []  # nothing new — no repeats


async def test_column_contract_drift_fails_loud(store):
    import asyncpg

    await _insert(store, "k1", {"type": "MESSAGE", "data": {"content": "x"}}, T1)
    await store.execute(f"ALTER TABLE {SCHEMA}.store DROP COLUMN value")  # producer drift
    # the SELECT of a dropped column fails loud (UndefinedColumnError), not silently mis-mapping
    with pytest.raises(asyncpg.PostgresError):
        await _source().list_items(None, 10)
