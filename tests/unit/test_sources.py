from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select

from mlpal_memory_graph.db.models import Episode, MemoryWatermark
from mlpal_memory_graph.ingest import plugins  # noqa: F401  registers built-in plugins
from mlpal_memory_graph.ingest.sources import (
    SOURCE_REGISTRY,
    SourceCapabilities,
    SourceConfig,
    SourceItem,
    register_source,
)
from mlpal_memory_graph.services.scheduler import SourceScheduler


@register_source
class _FakeSource:
    """A trivial in-memory source — proves adding a Source is ~10 lines + a decorator."""

    items: list[SourceItem] = []

    def __init__(self, config: SourceConfig) -> None:
        self.config = config

    @classmethod
    def source_type(cls) -> str:
        return "fake_test"

    @classmethod
    def capabilities(cls) -> SourceCapabilities:
        return SourceCapabilities(incremental=True)

    async def list_items(self, since, limit):
        return list(self.items), "cursor-1"


def test_builtin_plugin_is_registered():
    assert "langgraph_store" in SOURCE_REGISTRY
    assert SOURCE_REGISTRY["langgraph_store"].capabilities().incremental


async def test_scheduler_ingests_and_is_idempotent(session):
    _FakeSource.items = [
        SourceItem(source_id="a1", occurred_at=datetime.now(UTC), content="alpha fact",
                   metadata={"org_id": "orgA"})
    ]
    scheduler = SourceScheduler([SourceConfig(type="fake_test", default_scope="org")])

    first = await scheduler.poll_once(session)
    await session.commit()
    assert first["fake_test"] == 1

    wm = await session.get(MemoryWatermark, "fake_test")
    assert wm.last_processed_ref == "cursor-1"
    episodes = (await session.execute(select(Episode))).scalars().all()
    assert len(episodes) == 1
    assert episodes[0].source == "fake_test"

    # second poll re-sees the same item → idempotent insert drops it
    second = await scheduler.poll_once(session)
    await session.commit()
    assert second["fake_test"] == 0


def test_unknown_source_fails_loud():
    import pytest

    with pytest.raises(ValueError):
        SourceScheduler([SourceConfig(type="does_not_exist")])
