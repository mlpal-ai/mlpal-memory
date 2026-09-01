"""Served-memory usage counters — the retention policy's evidence base.

`mark_served` bumps counters for memories that actually appeared in a served
answer/search page. Deliberately best-effort: a failure here must never fail a
read, and the read path's latency budget outranks counter precision.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import update

from ..core.logging import get_logger
from ..db.models import Chunk, Node

log = get_logger(__name__)


async def mark_served(
    session, *, chunk_ids: list[str] | None = None, node_ids: list[str] | None = None
) -> None:
    now = datetime.now(UTC)
    try:
        if chunk_ids:
            await session.execute(
                update(Chunk)
                .where(Chunk.id.in_(chunk_ids))
                .values(served_count=Chunk.served_count + 1, last_served_at=now)
            )
        if node_ids:
            await session.execute(
                update(Node)
                .where(Node.id.in_(node_ids))
                .values(served_count=Node.served_count + 1, last_served_at=now)
            )
    except Exception:  # noqa: BLE001 — counters must never break a read
        log.warning("usage.mark_served_failed", chunks=len(chunk_ids or []))
