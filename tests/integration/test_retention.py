"""PR6: DIRECT-tier retention. Ages out old episodes/documents/chunks; NEVER derived facts."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select

from mlpal_memory_graph.core.scope import Scope, ScopeRef
from mlpal_memory_graph.db.models import Chunk, Document, Edge, Episode, Node
from mlpal_memory_graph.graph.drivers.postgres import PostgresDriver
from mlpal_memory_graph.services.retention import purge_expired_direct

OLD = datetime(2020, 1, 1, tzinfo=UTC)
RECENT = datetime(2026, 1, 1, tzinfo=UTC)
CUTOFF = datetime(2021, 1, 1, tzinfo=UTC)
A = ScopeRef(Scope.ORG, "orgR")


async def test_purge_ages_out_direct_but_keeps_derived(session):
    drv = PostgresDriver()
    # OLD direct memory (should be purged)
    session.add(
        Episode(
            event_id="old",
            occurred_at=OLD,
            org_id="orgR",
            scope="org",
            scope_id="orgR",
            actor={},
            source="x",
            action_type="fact.observed",
            subject={},
            payload={},
            processed=True,
        )
    )
    session.add(Document(id="d1", org_id="orgR", ingested_at=OLD))
    session.add(
        Chunk(id="c1", document_id="d1", org_id="orgR", ingested_at=OLD, content="x", ordinal=0)
    )
    # RECENT direct memory (should survive)
    session.add(
        Episode(
            event_id="new",
            occurred_at=RECENT,
            org_id="orgR",
            scope="org",
            scope_id="orgR",
            actor={},
            source="x",
            action_type="fact.observed",
            subject={},
            payload={},
            processed=True,
        )
    )
    # a DERIVED fact — must NEVER be aged out by retention
    await drv.upsert_node(session, tenant_id="orgR", scope=A, type_="Fact", key="f", name="a fact")
    await drv.upsert_edge(
        session,
        tenant_id="orgR",
        scope=A,
        type_="DECIDED",
        src_id="u",
        dst_id="f",
        fact="a fact",
        valid_at=OLD,
    )
    await session.flush()

    counts = await purge_expired_direct(session, cutoff=CUTOFF)
    assert counts == {"episodes": 1, "chunks": 1, "documents": 1}

    eps = {e.event_id for e in (await session.execute(select(Episode))).scalars().all()}
    assert eps == {"new"}  # only the recent episode remains
    assert (await session.execute(select(Document))).scalars().all() == []
    assert (await session.execute(select(Chunk))).scalars().all() == []
    # derived facts untouched
    assert (await session.execute(select(Node).where(Node.type == "Fact"))).scalars().first()
    assert (await session.execute(select(Edge).where(Edge.type == "DECIDED"))).scalars().first()


async def test_purge_skips_unprocessed_episodes(session):
    # an old but UNprocessed episode must not be deleted (it still needs to be folded)
    session.add(
        Episode(
            event_id="pending",
            occurred_at=OLD,
            org_id="orgR",
            scope="org",
            scope_id="orgR",
            actor={},
            source="x",
            action_type="fact.observed",
            subject={},
            payload={},
            processed=False,
        )
    )
    await session.flush()
    counts = await purge_expired_direct(session, cutoff=CUTOFF)
    assert counts["episodes"] == 0
    assert (await session.execute(select(Episode))).scalars().first().event_id == "pending"
