"""Retention / decay — DIRECT tier ONLY (episodes + documents + chunks).

The raw source content is bulky and loses value with age; the DERIVED facts distilled from it
(nodes/edges) are the durable asset and are NEVER aged out here (append-only; only the existing
GDPR/CLEAR scope purge deletes facts). So retention deletes only processed episodes and the
direct-memory documents/chunks older than a cutoff, keeping the graph intact (their provenance
ref on a fact simply becomes a dangling episode_id, which is fine).

Off unless ``MLPAL_DIRECT_RETENTION_DAYS`` > 0. Triggered periodically by the update worker.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import delete

from ..core.logging import get_logger
from ..db.models import Chunk, Document, Episode

log = get_logger(__name__)


async def purge_expired_direct(session, *, cutoff: datetime) -> dict[str, int]:
    """Delete DIRECT memory older than ``cutoff``. Returns per-table counts. Never touches
    nodes/edges (derived facts)."""
    eps = await session.execute(
        delete(Episode).where(Episode.occurred_at < cutoff, Episode.processed.is_(True))
    )
    chunks = await session.execute(delete(Chunk).where(Chunk.ingested_at < cutoff))
    docs = await session.execute(delete(Document).where(Document.ingested_at < cutoff))
    await session.flush()
    counts = {
        "episodes": eps.rowcount or 0,
        "chunks": chunks.rowcount or 0,
        "documents": docs.rowcount or 0,
    }
    if any(counts.values()):
        log.info("retention.purged_direct", cutoff=cutoff.isoformat(), **counts)
    return counts
