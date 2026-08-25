"""Deterministic bi-temporal correctness for edge supersession (PR1 / P0).

Uses an injected clock (explicit ``valid_at``) and drives the driver directly so the temporal
logic is isolated from the extractor. Covers in-order supersession, the out-of-order/backfill
regression (the P0.1 bug), the overlap guard, the "current view = one" invariant, and an
``as_of`` valid-time query. SQLite, offline, deterministic.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select

from mlpal_memory_graph.core.scope import Scope, ScopeRef
from mlpal_memory_graph.core.temporal import windows_overlap
from mlpal_memory_graph.db.models import Edge
from mlpal_memory_graph.graph.drivers.postgres import PostgresDriver

T1 = datetime(2026, 1, 1, tzinfo=UTC)
T2 = datetime(2026, 3, 1, tzinfo=UTC)
T3 = datetime(2026, 6, 1, tzinfo=UTC)
T1_5 = datetime(2026, 2, 1, tzinfo=UTC)
T2_5 = datetime(2026, 4, 1, tzinfo=UTC)

TENANT = "orgBT"
SCOPE = ScopeRef(Scope.ORG, TENANT)


async def _owned_by(driver, session, dst, valid_at):
    """Upsert a functional OWNED_BY(svc1 -> dst) edge valid from ``valid_at``."""
    return await driver.upsert_edge(
        session,
        tenant_id=TENANT,
        scope=SCOPE,
        type_="OWNED_BY",
        src_id="svc1",
        dst_id=dst,
        fact=f"svc1 owned by {dst}",
        valid_at=valid_at,
    )


async def _supersede(driver, session, new_edge):
    return await driver.invalidate_superseded(
        session, tenant_id=TENANT, scope=SCOPE, new_edge=new_edge
    )


async def _all_edges(session):
    return (await session.execute(select(Edge).where(Edge.type == "OWNED_BY"))).scalars().all()


# ── overlap helper (pure) ─────────────────────────────────────────────────────


def test_windows_overlap_open_ended():
    assert windows_overlap(T1, None, T2, None) is True  # both still-true → overlap


def test_windows_overlap_adjacent_is_disjoint():
    assert windows_overlap(T1, T2, T2, T3) is False  # touching endpoints don't overlap


def test_windows_overlap_disjoint():
    assert windows_overlap(T1, T2, T2_5, T3) is False


def test_windows_overlap_bounded_overlap():
    assert windows_overlap(T1, T3, T2, None) is True


# ── supersession (driver) ─────────────────────────────────────────────────────


async def test_in_order_supersession_closes_old(session):
    drv = PostgresDriver()
    old = await _owned_by(drv, session, "alice", T1)
    new = await _owned_by(drv, session, "bob", T2)  # later fact
    n = await _supersede(drv, session, new)
    await session.flush()

    assert n == 1
    assert old.invalid_at == T2 and old.expired_at is not None  # closed at new.valid_at
    assert new.invalid_at is None and new.expired_at is None  # current


async def test_backfill_out_of_order_does_not_invalidate_newer(session):
    """Regression for P0.1: a later-ingested *earlier-world* fact must bound ITSELF, never
    retroactively close the newer, still-true fact."""
    drv = PostgresDriver()
    newer = await _owned_by(drv, session, "alice", T2)  # ingested first, world-time T2
    older = await _owned_by(drv, session, "bob", T1)  # ingested second, world-time T1 (backfill)
    n = await _supersede(drv, session, older)
    await session.flush()

    assert n == 1
    assert newer.invalid_at is None, "newer still-true fact must stay open"
    assert older.invalid_at == T2, "backfilled older fact is bounded at the newer fact's start"
    assert older.expired_at is not None

    current = [e for e in await _all_edges(session) if e.invalid_at is None]
    assert len(current) == 1 and current[0].dst_id == "alice"


async def test_overlap_guard_skips_already_closed_nonoverlapping(session):
    drv = PostgresDriver()
    a = await _owned_by(drv, session, "alice", T1)
    b = await _owned_by(drv, session, "bob", T2)
    await _supersede(drv, session, b)  # closes alice at T2
    await session.flush()
    c = await _owned_by(drv, session, "carol", T3)
    await _supersede(drv, session, c)  # closes bob at T3; must NOT re-touch alice
    await session.flush()

    assert a.invalid_at == T2, "already-closed non-overlapping edge must not be re-touched"
    assert b.invalid_at == T3
    current = [e for e in await _all_edges(session) if e.invalid_at is None]
    assert len(current) == 1 and current[0].dst_id == "carol"


async def test_same_dst_reassertion_is_not_a_contradiction(session):
    drv = PostgresDriver()
    await _owned_by(drv, session, "alice", T1)
    again = await _owned_by(drv, session, "alice", T2)  # same dst → upsert returns existing
    n = await _supersede(drv, session, again)
    await session.flush()
    assert n == 0
    current = [e for e in await _all_edges(session) if e.invalid_at is None]
    assert len(current) == 1


async def test_as_of_valid_time_returns_one_truth(session):
    drv = PostgresDriver()
    old = await _owned_by(drv, session, "alice", T1)
    new = await _owned_by(drv, session, "bob", T2)
    await _supersede(drv, session, new)
    await session.flush()

    async def as_of(t):
        stmt = select(Edge).where(
            Edge.type == "OWNED_BY",
            Edge.valid_at <= t,
            (Edge.invalid_at.is_(None)) | (Edge.invalid_at > t),
        )
        return (await session.execute(stmt)).scalars().all()

    at_t15 = await as_of(T1_5)
    assert len(at_t15) == 1 and at_t15[0].dst_id == "alice"
    at_t25 = await as_of(T2_5)
    assert len(at_t25) == 1 and at_t25[0].dst_id == "bob"
    _ = old  # silence unused
