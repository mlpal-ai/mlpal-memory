"""v3 lifecycle: working-tier TTL, committed promotion, re-observation counting,
workspace facet stamping through the fold."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from mlpal_memory_graph.core.scope import Scope, ScopeRef
from mlpal_memory_graph.db.models import Chunk, Document, Episode, Node
from mlpal_memory_graph.pipeline.updater import Updater

ORG = "orgA"


def _episode(event_id: str, *, lifecycle: str = "committed", workspace: str | None = "repo-x",
             content: str | None = None, statement: str = "the deploy uses blue-green") -> Episode:
    return Episode(
        event_id=event_id,
        occurred_at=datetime.now(UTC),
        org_id=ORG,
        scope="user",
        scope_id="alice",
        workspace=workspace,
        lifecycle=lifecycle,
        actor={"user_id": "alice"},
        source="claude_code",
        action_type="fact.observed",
        subject={},
        payload={"statement": statement},
        content=content,
    )


async def _fold(session, episode: Episode) -> None:
    session.add(episode)
    await session.flush()
    await Updater().process_episode(session, episode)
    await session.commit()


@pytest.mark.asyncio
async def test_working_episode_creates_ttld_memories(session):
    await _fold(session, _episode("w1", lifecycle="working"))
    facts = (
        (await session.execute(select(Node).where(Node.type == "Fact"))).scalars().all()
    )
    assert facts
    for n in facts:
        assert n.status == "working"
        assert n.expires_at is not None
        assert n.workspace == "repo-x"


@pytest.mark.asyncio
async def test_committed_reobservation_promotes_working(session):
    await _fold(session, _episode("w2", lifecycle="working"))
    await _fold(session, _episode("c2", lifecycle="committed"))  # same statement → same Fact key
    facts = (
        (await session.execute(select(Node).where(Node.type == "Fact"))).scalars().all()
    )
    assert len(facts) == 1  # deduped, not duplicated
    n = facts[0]
    assert n.status == "committed"
    assert n.expires_at is None
    assert n.observed_count >= 2


@pytest.mark.asyncio
async def test_working_never_downgrades_committed(session):
    await _fold(session, _episode("c3", lifecycle="committed"))
    await _fold(session, _episode("w3", lifecycle="working"))
    n = (
        (await session.execute(select(Node).where(Node.type == "Fact"))).scalars().first()
    )
    assert n.status == "committed"
    assert n.expires_at is None


@pytest.mark.asyncio
async def test_expired_working_memories_are_invisible_and_swept(session):
    await _fold(session, _episode("w4", lifecycle="working"))
    # force-expire
    n = (
        (await session.execute(select(Node).where(Node.type == "Fact"))).scalars().first()
    )
    n.expires_at = datetime.now(UTC) - timedelta(days=1)
    await session.commit()

    # invisible to search even before the sweep
    from mlpal_memory_graph.graph import get_driver

    drv = get_driver()
    hits = await drv.lexical_search_nodes(
        session,
        tenant_id=ORG,
        scopes=[ScopeRef(Scope.USER, "alice")],
        text="blue-green",
    )
    assert hits == []

    # and the sweep physically removes it
    from mlpal_memory_graph.services.worker import MemoryUpdateWorker

    await MemoryUpdateWorker()._sweep_expired_working(session)
    left = (await session.execute(select(Node).where(Node.type == "Fact"))).scalars().all()
    assert left == []


@pytest.mark.asyncio
async def test_workspace_flows_to_direct_tier(session):
    await _fold(
        session,
        _episode(
            "d1",
            content="Always run migrations before starting the api container.",
        ),
    )
    doc = (await session.execute(select(Document))).scalars().first()
    chunk = (await session.execute(select(Chunk))).scalars().first()
    assert doc.workspace == "repo-x"
    assert doc.valid_at is not None
    assert chunk.workspace == "repo-x"
