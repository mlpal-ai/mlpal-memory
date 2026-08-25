from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select

from mlpal_memory_graph.db.models import Edge, Episode, Node
from mlpal_memory_graph.pipeline.updater import Updater


async def _make_episode(session, **kw):
    base = dict(
        event_id="e1",
        occurred_at=datetime.now(UTC),
        org_id="org1",
        actor={"user_id": "u1"},
        source="backend",
        action_type="agent.deployed",
        subject={"agent_id": "a1"},
        payload={},
        schema_version=1,
        processed=False,
    )
    base.update(kw)
    ep = Episode(**base)
    session.add(ep)
    await session.flush()
    return ep


async def test_process_episode_builds_graph(session):
    ep = await _make_episode(session)
    result = await Updater().process_episode(session, ep)
    await session.commit()

    assert result["edges"] >= 1
    nodes = (await session.execute(select(Node))).scalars().all()
    assert {"Org", "User", "Agent"} <= {n.type for n in nodes}
    edges = (await session.execute(select(Edge))).scalars().all()
    assert {"DEPLOYED", "MEMBER_OF"} <= {e.type for e in edges}
    assert ep.processed is True


async def test_fact_node_dedup(session):
    # two fact episodes about the same statement should resolve to one Fact node
    # (node-dedup, not temporal logic — see test_bitemporal.py for supersession)
    ep1 = await _make_episode(
        session,
        event_id="f1",
        action_type="fact.observed",
        subject={},
        payload={"statement": "team standardizes on Postgres"},
    )
    await Updater().process_episode(session, ep1)
    ep2 = await _make_episode(
        session,
        event_id="f2",
        action_type="fact.observed",
        subject={},
        payload={"statement": "team standardizes on Postgres"},
    )
    await Updater().process_episode(session, ep2)
    await session.commit()

    facts = (await session.execute(select(Node).where(Node.type == "Fact"))).scalars().all()
    assert len(facts) == 1  # deduped, not duplicated
