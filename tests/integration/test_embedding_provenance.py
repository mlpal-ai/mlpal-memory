"""PR2: every embedded row stamps embedding_model + embedding_dim (D2 re-embed guard), and
edges carry a fact_embedding for semantic contradiction candidates (D3). SQLite/offline:
the dev embedder is 'dev-hash'."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select

from mlpal_memory_graph.core.scope import Scope, ScopeRef
from mlpal_memory_graph.db.models import Chunk, Edge, Node
from mlpal_memory_graph.pipeline.updater import Updater
from mlpal_memory_graph.services.direct import DirectMemory


async def _fact_episode(session, **kw):
    from mlpal_memory_graph.db.models import Episode

    base = dict(
        event_id="emb1",
        occurred_at=datetime.now(UTC),
        org_id="orgE",
        scope="org",
        scope_id="orgE",
        actor={"user_id": "u1"},
        source="backend",
        action_type="fact.observed",
        subject={},
        payload={"statement": "the team standardizes on postgres"},
        processed=False,
    )
    base.update(kw)
    ep = Episode(**base)
    session.add(ep)
    await session.flush()
    return ep


async def test_fact_node_and_edge_carry_embedding_provenance(session):
    ep = await _fact_episode(session)
    await Updater().process_episode(session, ep)
    await session.flush()

    fact = (await session.execute(select(Node).where(Node.type == "Fact"))).scalars().one()
    assert fact.embedding is not None
    assert fact.embedding_model == "dev-hash"
    assert fact.embedding_dim == 1536

    decided = (await session.execute(select(Edge).where(Edge.type == "DECIDED"))).scalars().one()
    assert decided.fact_embedding is not None, "edge fact should be embedded for candidates"
    assert decided.embedding_model == "dev-hash"
    assert decided.embedding_dim == 1536


async def test_chunk_carries_embedding_provenance(session):
    await DirectMemory().add_document(
        session,
        tenant_id="orgE",
        scope=ScopeRef(Scope.ORG, "orgE"),
        content="the checkout service retries are capped at three",
        source="docs",
    )
    await session.flush()
    chunk = (await session.execute(select(Chunk))).scalars().first()
    assert chunk is not None
    assert chunk.embedding_model == "dev-hash"
    assert chunk.embedding_dim == 1536
