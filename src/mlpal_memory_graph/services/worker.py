"""Background incremental-update worker.

Mirrors the platform's advisory-locked poll-loop workers (mlpal-usage UsageMeteringWorker,
backend CUProcessingWorker): only one replica processes at a time; each episode runs in its
own transaction so one failure can't roll back the batch.
"""

from __future__ import annotations

import asyncio

from sqlalchemy import text

from ..core.config import get_settings
from ..core.logging import get_logger
from ..db import get_session_factory
from ..db.models import Episode
from ..pipeline.updater import Updater
from ..repositories.episodes import fetch_unprocessed_ids

from .resilience import ModelUnavailable

log = get_logger(__name__)


async def _try_advisory_lock(session, lock_id: int, is_postgres: bool) -> bool:
    if not is_postgres:
        return True
    result = await session.execute(text("SELECT pg_try_advisory_lock(:id)"), {"id": lock_id})
    return bool(result.scalar())


async def _advisory_unlock(session, lock_id: int, is_postgres: bool) -> None:
    if is_postgres:
        await session.execute(text("SELECT pg_advisory_unlock(:id)"), {"id": lock_id})


class PollWorker:
    def __init__(self, name: str, poll_interval: float) -> None:
        self.name = name
        self.poll_interval = poll_interval
        self._task: asyncio.Task | None = None
        self._running = False

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run())
        log.info("worker.started", worker=self.name, interval=self.poll_interval)

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        log.info("worker.stopped", worker=self.name)

    async def _run(self) -> None:
        while self._running:
            try:
                await self.tick()
            except asyncio.CancelledError:
                break
            except Exception as exc:  # noqa: BLE001
                log.error("worker.tick_failed", worker=self.name, error=str(exc))
            await asyncio.sleep(self.poll_interval)

    async def tick(self) -> None:  # pragma: no cover
        raise NotImplementedError


class MemoryUpdateWorker(PollWorker):
    def __init__(self) -> None:
        s = get_settings()
        super().__init__("memory-updater", s.updater_poll_interval)
        self.settings = s
        self.batch = s.updater_batch_size
        self.deferred = 0  # memory v9: folds deferred on a model outage (observability, tests)
        self.lock_id = s.updater_advisory_lock_id
        self.updater = Updater()
        # pull sources (if any configured) feed the same episodes table the fold drains.
        from ..ingest import plugins  # noqa: F401  import-for-registration
        from .scheduler import SourceScheduler, load_source_configs

        configs = load_source_configs()
        self.scheduler = SourceScheduler(configs) if configs else None
        # memory v9 round 3 (L43): these are compared against time.monotonic(), which counts from
        # boot — starting at 0.0 meant a host booted less than an interval ago (a day for the
        # trust join) ran neither the nightly consequence join nor the retention purge until its
        # uptime exceeded the interval. -inf makes the first tick eligible.
        self._last_retention = float("-inf")
        self._last_trust_join = float("-inf")
        self._last_notes_curation = float("-inf")

    async def tick(self) -> None:
        factory = get_session_factory()
        is_pg = self.settings.is_postgres
        ids: list[str] = []
        async with factory() as s0:
            if not await _try_advisory_lock(s0, self.lock_id, is_pg):
                return
            try:
                if self.scheduler is not None:
                    await self.scheduler.poll_once(s0)
                    await s0.commit()
                ids = await fetch_unprocessed_ids(s0, self.batch)
                await s0.commit()
                for event_id in ids:
                    try:
                        await self._process_one(factory, event_id)
                    except ModelUnavailable:
                        break  # the rest of the batch would fail the same way; next tick

                await self._maybe_purge_retention(s0)  # holds the lock → single-writer
                await self._sweep_expired_working(s0)  # v3: working-tier TTL
                await self._close_expired_committed(s0)  # v7: writer-set expiry, closed not deleted
                await self._maybe_trust_join(s0)  # v7: nightly consequence join, single-writer
                await self._maybe_curate_notes(s0)  # memory v10: nightly note curation, deterministic
            finally:
                # If the unlock itself fails, the pooled connection would silently keep
                # the session-level lock and every future tick would no-op. Invalidate
                # the connection instead — the server releases the lock on close.
                try:
                    await _advisory_unlock(s0, self.lock_id, is_pg)
                    await s0.commit()
                except Exception as exc:  # noqa: BLE001
                    log.error("worker.unlock_failed", worker=self.name, error=str(exc))
                    await s0.invalidate()
        if ids:
            log.info("memory.batch_processed", count=len(ids))

    async def _maybe_purge_retention(self, session) -> None:
        """DIRECT-tier retention, run at most every retention_interval_seconds (never per tick).
        Off unless direct_retention_days > 0. Derived facts are never aged out here."""
        import time
        from datetime import UTC, datetime, timedelta

        days = self.settings.direct_retention_days
        if days <= 0:
            return
        if time.monotonic() - self._last_retention < self.settings.retention_interval_seconds:
            return
        self._last_retention = time.monotonic()
        from .retention import purge_expired_direct

        cutoff = datetime.now(UTC) - timedelta(days=days)
        await purge_expired_direct(session, cutoff=cutoff)
        await session.commit()

    async def _close_expired_committed(self, session) -> None:
        """memory v7 WP3: a committed memory whose writer-set `valid_until` has passed is CLOSED, never
        deleted: status `expired`, its live edges invalidated at the expiry instant. Reads already hide
        it (expires_at filters); this keeps the as-of view honest and the ledger append-only."""
        from datetime import UTC, datetime

        from sqlalchemy import or_, select, update

        from ..db.models import Edge, Node

        now = datetime.now(UTC)
        due = (await session.execute(
            select(Node).where(Node.status.in_(("committed", "published")), Node.expires_at.isnot(None), Node.expires_at < now)
        )).scalars().all()
        if not due:
            return
        for n in due:
            await session.execute(
                update(Edge).where(or_(Edge.src_id == n.id, Edge.dst_id == n.id), Edge.invalid_at.is_(None))
                .values(invalid_at=n.expires_at, expired_at=now)
            )
            n.status = "expired"
        await session.commit()
        log.info("memory.expired_closed", nodes=len(due))

    async def _maybe_trust_join(self, session) -> None:
        """Trust by consequence for every org that produced a run verdict in the window, at most every
        trust_join_interval_seconds. The tool is idempotent, so a restart re-running it is harmless."""
        import time
        from datetime import UTC, datetime, timedelta

        if not self.settings.trust_join_enabled:
            return
        if time.monotonic() - self._last_trust_join < self.settings.trust_join_interval_seconds:
            return
        self._last_trust_join = time.monotonic()
        from sqlalchemy import select

        from ..db.models import Episode
        from ..tools.hop_trust import compute

        since = datetime.now(UTC) - timedelta(days=self.settings.trust_join_window_days)
        orgs = (await session.execute(
            select(Episode.org_id).where(Episode.action_type == "run.completed", Episode.occurred_at >= since).distinct()
        )).scalars().all()
        for org in orgs:
            if not org:
                continue
            try:
                out = await compute(str(org), self.settings.trust_join_window_days)
                log.info("trust.join", org=org, runs_with_verdict=out.get("runs_with_verdict"), tiers=out.get("tiers"))
            except Exception as exc:  # noqa: BLE001 — one org's failure must not stop the others
                log.error("trust.join_failed", org=org, error=str(exc))

    async def _maybe_curate_notes(self, session) -> None:
        """memory v10: close stale open threads and rebuild the current-state block of every
        org workspace note, per tenant, at most every notes_curation_interval_seconds. No model."""
        import time

        if not self.settings.notes_curation_enabled:
            return
        if time.monotonic() - self._last_notes_curation < self.settings.notes_curation_interval_seconds:
            return
        self._last_notes_curation = time.monotonic()
        from sqlalchemy import select

        from ..db.models import Note
        from .notes_curation import curate_workspace_notes

        orgs = (await session.execute(select(Note.org_id).where(Note.scope == "org").distinct())).scalars().all()
        for org in orgs:
            try:
                results = await curate_workspace_notes(session, org_id=org, thread_ttl_days=self.settings.notes_thread_ttl_days)
                await session.commit()
                for r in results:
                    if r.changed:
                        log.info("notes.curation", org=org, workspace=r.workspace, closed=len(r.closed_threads), state_lines=r.state_lines)
            except Exception as exc:  # noqa: BLE001 — one tenant's failure must not stop the others
                await session.rollback()
                log.error("notes.curation_failed", org=org, error=str(exc))

    async def _sweep_expired_working(self, session) -> None:
        """Physically remove expired working-tier memories (search already excludes them,
        so this is hygiene, not correctness). Runs under the advisory lock."""
        from datetime import UTC, datetime

        from sqlalchemy import delete

        from ..db.models import Edge, Node

        now = datetime.now(UTC)
        removed = 0
        for model in (Edge, Node):  # edges first: no dangling references while sweeping
            res = await session.execute(
                delete(model).where(
                    model.status == "working",
                    model.expires_at.isnot(None),
                    model.expires_at < now,
                )
            )
            removed += res.rowcount or 0
        await session.commit()
        if removed:
            log.info("memory.working_swept", removed=removed)

    async def _process_one(self, factory, event_id: str) -> None:
        async with factory() as session:
            episode = await session.get(Episode, event_id)
            if episode is None or episode.processed or episode.dead_at is not None:
                return
            try:
                await self.updater.process_episode(session, episode)
                await session.commit()
            except ModelUnavailable as exc:
                # memory v9 round 3: the model, not the episode, is the problem — leave the retry
                # budget alone, note it, and let the next tick try again after the breaker window
                await session.rollback()
                self.deferred += 1
                log.warning("worker.deferred", event_id=event_id, client=exc.client, reason=exc.reason)
                raise
            except Exception as exc:  # noqa: BLE001
                await session.rollback()
                await self._record_failure(factory, event_id, exc)

    async def _record_failure(self, factory, event_id: str, exc: Exception) -> None:
        """Increment the retry counter; dead-letter at the cap (bounded retries)."""
        from datetime import UTC, datetime

        async with factory() as s2:
            ep = await s2.get(Episode, event_id)
            if ep is None:
                return
            ep.error = str(exc)[:500]
            ep.error_count = (ep.error_count or 0) + 1
            if ep.error_count >= self.settings.updater_max_retries:
                ep.dead_at = datetime.now(UTC)
                log.error(
                    "memory.episode_dead",
                    event_id=event_id,
                    attempts=ep.error_count,
                    error=ep.error,
                )
            else:
                log.error(
                    "memory.episode_failed",
                    event_id=event_id,
                    attempt=ep.error_count,
                    error=ep.error,
                )
            await s2.commit()
