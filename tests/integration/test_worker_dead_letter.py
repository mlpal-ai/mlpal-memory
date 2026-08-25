"""Bounded retries: a poison episode is retried at most updater_max_retries times,
then dead-lettered and excluded from the worker cursor; fresh work outranks retries."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from mlpal_memory_graph.core.config import get_settings
from mlpal_memory_graph.db import get_session_factory
from mlpal_memory_graph.db.models import Episode
from mlpal_memory_graph.repositories.episodes import fetch_unprocessed_ids
from mlpal_memory_graph.services.worker import MemoryUpdateWorker


def _episode(event_id: str, ingested_offset: int = 0) -> Episode:
    return Episode(
        event_id=event_id,
        occurred_at=datetime.now(UTC),
        org_id="orgA",
        scope="org",
        scope_id="orgA",
        actor={"user_id": "alice"},
        source="test",
        action_type="fact.observed",
        subject={},
        payload={"statement": f"statement {event_id}"},
    )


@pytest.mark.asyncio
async def test_poison_episode_is_dead_lettered(session, monkeypatch):
    session.add(_episode("poison-1"))
    await session.commit()

    worker = MemoryUpdateWorker()

    async def _boom(sess, episode):  # noqa: ARG001
        raise RuntimeError("extractor exploded")

    monkeypatch.setattr(worker.updater, "process_episode", _boom)
    factory = get_session_factory()
    max_retries = get_settings().updater_max_retries

    for attempt in range(1, max_retries + 1):
        await worker._process_one(factory, "poison-1")
        async with factory() as s:
            ep = await s.get(Episode, "poison-1")
            assert ep.error_count == attempt
            assert "extractor exploded" in ep.error

    async with factory() as s:
        ep = await s.get(Episode, "poison-1")
        assert ep.dead_at is not None
        assert not ep.processed

    # dead episodes leave the cursor; further attempts are no-ops
    async with factory() as s:
        assert await fetch_unprocessed_ids(s, 10) == []
    await worker._process_one(factory, "poison-1")
    async with factory() as s:
        ep = await s.get(Episode, "poison-1")
        assert ep.error_count == max_retries  # unchanged


@pytest.mark.asyncio
async def test_fresh_work_outranks_retries(session):
    failing = _episode("failing-old")
    failing.error_count = 2
    session.add(failing)
    session.add(_episode("fresh-new"))
    await session.commit()

    ids = await fetch_unprocessed_ids(session, 10)
    assert ids == ["fresh-new", "failing-old"]
