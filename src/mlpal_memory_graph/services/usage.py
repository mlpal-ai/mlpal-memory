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


def run_context(headers) -> dict:
    """The calling run, as the memory MCP sidecar forwards it from the engine's `_meta`
    (X-Run-Id / X-Hop / X-Origin). Empty when the caller is not a run."""
    get = headers.get
    ctx = {k: get(h) for k, h in (("run_id", "x-run-id"), ("hop", "x-hop"), ("origin", "x-origin"))}
    return {k: v for k, v in ctx.items() if v}


async def record_served(session, *, tenant_id: str | None, run: dict, tool: str,
                        node_ids: list[str] | None = None, chunk_ids: list[str] | None = None) -> None:
    """Trust by consequence needs to know which memories a run saw. One content-free ledger row per
    read that a run made (`memory.served`), joined nightly with the run's verdict. Best-effort."""
    if not run.get("run_id") or not (node_ids or chunk_ids):
        return
    try:
        from datetime import UTC, datetime
        from uuid import uuid4

        from ..db.models import Episode

        session.add(Episode(
            event_id=str(uuid4()), occurred_at=datetime.now(UTC), org_id=tenant_id, scope="org", scope_id=tenant_id,
            lifecycle="committed", actor={}, source="harness_memory", action_type="memory.served",
            subject={}, payload={"run_id": run["run_id"], "tool": tool, "node_ids": list(node_ids or []),
                                 "chunk_ids": list(chunk_ids or []), **{k: run[k] for k in ("hop", "origin") if run.get(k)}},
            processed=True, processed_at=datetime.now(UTC), tier="deterministic",
        ))
    except Exception:  # noqa: BLE001 — the served log must never break a read
        log.warning("usage.record_served_failed", run_id=run.get("run_id"))
