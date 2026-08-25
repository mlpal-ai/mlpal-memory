"""#11: functional/stative relations make bi-temporal supersession fire through the *fold*.

Two paths, both deterministic (no LLM) and offline:
  - functional supersession: HAS_STATUS is single-valued per subject, so a later status
    (running → stopped) closes the prior one (the D1 fast-path);
  - explicit end: org.member_removed ENDS the target's MEMBER_OF (stative invalidation).

We drive the real fold (Updater.process_episode) and assert via the driver's as_of neighbors,
so this exercises extractor → updater → driver end-to-end.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select

from mlpal_memory_graph.db.models import Edge, Episode, Node
from mlpal_memory_graph.graph.drivers.postgres import PostgresDriver
from mlpal_memory_graph.pipeline.updater import Updater

T1 = datetime(2026, 1, 1, tzinfo=UTC)
T1_5 = datetime(2026, 2, 1, tzinfo=UTC)
T2 = datetime(2026, 3, 1, tzinfo=UTC)
T2_5 = datetime(2026, 4, 1, tzinfo=UTC)

ORG = "orgZ"


def _naive(dt):
    # SQLite drops tzinfo on reload; compare wall-clock instants regardless of dialect
    return dt.replace(tzinfo=None) if dt is not None and dt.tzinfo else dt


async def _fold(session, *, action_type, occurred_at, subject, event_id, payload=None):
    ep = Episode(
        event_id=event_id,
        occurred_at=occurred_at,
        org_id=ORG,
        scope="org",
        scope_id=ORG,
        actor={"user_id": "admin1"},
        source="backend",
        action_type=action_type,
        subject=subject,
        payload=payload or {},
        processed=False,
    )
    session.add(ep)
    await session.flush()
    await Updater().process_episode(session, ep)
    await session.flush()
    return ep


async def _node(session, type_, key):
    return (
        (await session.execute(select(Node).where(Node.type == type_, Node.key == key)))
        .scalars()
        .first()
    )


async def test_functional_status_supersession(session):
    drv = PostgresDriver()
    # deploy then stop the same agent → HAS_STATUS running, then stopped
    await _fold(
        session,
        action_type="agent.deployed",
        occurred_at=T1,
        subject={"agent_id": "bot1"},
        event_id="d1",
    )
    await _fold(
        session,
        action_type="agent.stopped",
        occurred_at=T2,
        subject={"agent_id": "bot1"},
        event_id="s1",
    )

    agent = await _node(session, "Agent", "bot1")
    assert agent is not None

    def statuses(edges):
        return {e.fact for e in edges if e.type == "HAS_STATUS"}

    # at T1.5 the agent was running; at T2.5 it is stopped (the running edge was superseded)
    at_15 = statuses(await drv.neighbors(session, agent.id, as_of=T1_5))
    assert any("running" in f for f in at_15) and not any("stopped" in f for f in at_15)
    at_25 = statuses(await drv.neighbors(session, agent.id, as_of=T2_5))
    assert any("stopped" in f for f in at_25) and not any("running" in f for f in at_25)

    # exactly one HAS_STATUS edge is currently open (the stopped one); running was invalidated
    has_status = (
        (await session.execute(select(Edge).where(Edge.type == "HAS_STATUS"))).scalars().all()
    )
    open_edges = [e for e in has_status if e.invalid_at is None]
    closed = [e for e in has_status if e.invalid_at is not None]
    assert len(open_edges) == 1 and "stopped" in open_edges[0].fact
    assert len(closed) == 1 and "running" in closed[0].fact
    assert _naive(closed[0].invalid_at) == _naive(T2)


async def test_member_removed_ends_membership(session):
    drv = PostgresDriver()
    await _fold(
        session,
        action_type="org.member_added",
        occurred_at=T1,
        subject={"target_user_id": "9"},
        event_id="m1",
    )
    await _fold(
        session,
        action_type="org.member_removed",
        occurred_at=T2,
        subject={"target_user_id": "9"},
        event_id="m2",
    )

    member = await _node(session, "User", "9")
    assert member is not None

    def orgs(edges):
        return {e.dst_id for e in edges if e.type == "MEMBER_OF"}

    # member of the org at T1.5, no longer at T2.5
    org_node = await _node(session, "Org", ORG)
    assert org_node.id in orgs(await drv.neighbors(session, member.id, as_of=T1_5))
    assert org_node.id not in orgs(await drv.neighbors(session, member.id, as_of=T2_5))

    # the edge was closed at the removal time, not deleted (history preserved)
    edge = (
        (
            await session.execute(
                select(Edge).where(Edge.type == "MEMBER_OF", Edge.src_id == member.id)
            )
        )
        .scalars()
        .one()
    )
    assert _naive(edge.invalid_at) == _naive(T2) and _naive(edge.valid_at) == _naive(T1)
