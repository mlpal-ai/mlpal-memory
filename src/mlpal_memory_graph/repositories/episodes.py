"""Episode persistence: idempotent insert + unprocessed-cursor reads."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import Episode


async def insert_episode(session: AsyncSession, kwargs: dict) -> bool:
    """Insert one episode; returns False if event_id already present (dedup)."""
    existing = await session.get(Episode, kwargs["event_id"])
    if existing is not None:
        return False
    session.add(Episode(**kwargs))
    await session.flush()
    return True


async def fetch_unprocessed_ids(session: AsyncSession, limit: int) -> list[str]:
    """Worker cursor: pending episodes, dead-letter excluded, fresh work before retries.

    Ordering by (error_count, ingested_at) means a batch of failing episodes cannot
    starve newer arrivals — retries queue behind first-attempt work every tick.
    """
    stmt = (
        select(Episode.event_id)
        .where(Episode.processed.is_(False), Episode.dead_at.is_(None))
        .order_by(Episode.error_count, Episode.ingested_at)
        .limit(limit)
    )
    return list((await session.execute(stmt)).scalars().all())
