"""Observability for the memory graph: a contradiction-backlog invariant gauge + corpus stats.

These back the ops endpoint and the worker's periodic health log. The per-ingest counts
(entities/facts extracted, edges invalidated, contradictions) are emitted by the updater; the
retrieval leg mix is emitted by the retrieval service.
"""

from __future__ import annotations

from sqlalchemy import func, select

from ..db.models import Chunk, Document, Edge, Episode, Node


async def contradiction_backlog(session, *, tenant_id: str | None = None) -> int:
    """Number of ``(org, src, dst, type)`` relations with MORE THAN ONE simultaneously-open edge.

    Invalidate-not-delete means at most one edge per relation should be open (invalid_at IS NULL)
    at a time, so this gauge should stay ~0 — a non-zero value flags a supersession/judge bug.
    """
    inner = (
        select(func.count().label("n"))
        .select_from(Edge)
        .where(Edge.invalid_at.is_(None))
        .group_by(Edge.org_id, Edge.src_id, Edge.dst_id, Edge.type)
        .having(func.count() > 1)
    )
    if tenant_id is not None:
        inner = inner.where(Edge.org_id == tenant_id)
    return (await session.execute(select(func.count()).select_from(inner.subquery()))).scalar() or 0


async def memory_stats(session, *, tenant_id: str | None = None) -> dict:
    """Corpus counts + the contradiction-backlog gauge (scoped to ``tenant_id`` when given)."""

    async def _count(model, *where) -> int:
        q = select(func.count()).select_from(model)
        for w in where:
            q = q.where(w)
        if tenant_id is not None:
            q = q.where(model.org_id == tenant_id)
        return (await session.execute(q)).scalar() or 0

    return {
        "nodes": await _count(Node),
        "edges_open": await _count(Edge, Edge.invalid_at.is_(None)),
        "edges_total": await _count(Edge),
        "episodes": await _count(Episode),
        "documents": await _count(Document),
        "chunks": await _count(Chunk),
        "contradiction_backlog": await contradiction_backlog(session, tenant_id=tenant_id),
    }
