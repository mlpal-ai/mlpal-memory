"""The ProducerTailSource composite keyset over a real Postgres table with an INTEGER id column —
the Phase-1 plugins' shape (audit/agents/mcp/skills). Proves same-timestamp rows aren't skipped and
the int-typed keyset param binds. @pytest.mark.postgres; the live tail is pending a memory_reader
DSN, so we validate against a seeded local table."""

from __future__ import annotations

import os
from datetime import UTC, datetime

import pytest
import pytest_asyncio

from mlpal_memory_graph.ingest import SourceConfig, SourceItem
from mlpal_memory_graph.ingest.plugins._producer_tail import ProducerTailSource

DSN = os.getenv("MLPAL_TEST_POSTGRES_DSN")
PG_DSN = DSN.replace("+asyncpg", "") if DSN else None
pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(not DSN, reason="set MLPAL_TEST_POSTGRES_DSN to run"),
]

SCHEMA = "tail_test"
T = datetime(2026, 1, 1, tzinfo=UTC)  # all rows share one timestamp → exercises the id tiebreaker


class _IntTail(ProducerTailSource):
    TABLE = "events"
    CURSOR = "created_at"  # int id keyset (ID_SQL_TYPE defaults to bigint)
    COLUMNS = ("id", "created_at", "label")
    REQUIRED = ("id", "created_at", "label")

    @classmethod
    def source_type(cls) -> str:
        return "int_tail_test"

    def map_row(self, row: dict) -> list[SourceItem]:
        return [
            SourceItem(
                source_id=str(row["id"]),
                occurred_at=row["created_at"],
                metadata={"label": row["label"]},
            )
        ]


@pytest_asyncio.fixture
async def events():
    import asyncpg

    conn = await asyncpg.connect(PG_DSN)
    await conn.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
    await conn.execute(f"CREATE SCHEMA {SCHEMA}")
    await conn.execute(
        f"CREATE TABLE {SCHEMA}.events (id bigint PRIMARY KEY, created_at timestamptz, label text)"
    )
    for i in (1, 2, 3):
        await conn.execute(f"INSERT INTO {SCHEMA}.events VALUES ($1, $2, $3)", i, T, f"e{i}")
    yield conn
    await conn.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
    await conn.close()


async def test_same_timestamp_keyset_does_not_skip(events):
    src = _IntTail(
        SourceConfig(type="int_tail_test", options={"read_dsn": PG_DSN, "schema": SCHEMA})
    )
    # all three rows share the same created_at — a timestamp-only cursor would skip ids 2 and 3.
    first, c1 = await src.list_items(None, 2)
    assert [i.metadata["label"] for i in first] == ["e1", "e2"]
    second, c2 = await src.list_items(c1, 10)
    assert [i.metadata["label"] for i in second] == ["e3"]  # composite (ts, id) keyset advanced
    assert await src.list_items(c2, 10) == ([], c2)  # nothing new, no repeats
