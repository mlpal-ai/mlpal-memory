"""Units: the org's hierarchy and who may read what through it (memory v12 §2b).

The tree is the org's to define: any depth, any labels, single parent, rolling up to the org.
Three rules make it governed rather than open:

- a person reads their own units and **every ancestor** (what leadership publishes downward
  reaches everyone below); nearest first, so the closest unit's copy of a fact wins;
- reading **descendants** is a grant, not a default: an admin of a unit whose policy says
  ``read_descendants`` sees its subtree;
- a member may write (publish) into their own units and their ancestors — one level at a time
  is the person's act; automatic lifting is the unit policy's (a worker's job, not this module's).

Resolution runs per request, so the answer is cached briefly in-process; every write through
this module drops the tenant's cache. Across replicas a membership change is visible within
``CACHE_TTL_S``.
"""

from __future__ import annotations

import time
from collections.abc import Iterable
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import Unit, UnitMember

CACHE_TTL_S = 30.0
ROLES = ("member", "admin")


class UnitError(ValueError):
    """A request the tree cannot honour (unknown parent, cycle, non-empty delete)."""


class UnitPolicy(BaseModel):
    """Validated at the boundary; unknown keys are refused so a typo never silently means 'off'."""

    model_config = ConfigDict(extra="forbid")

    read_descendants: bool = False  # admins of this unit read its whole subtree
    publish_up: str = Field("members", pattern="^(members|admins)$")  # who may publish to an ancestor
    hops_may_write: bool = True  # a HOP pinned to this unit may write here
    # automatic roll-up (memory v12 §2b): learnings at or above `tier` lift to `to` (the parent);
    # enacted by the worker, recorded here
    lift: dict | None = None


def ancestors(by_id: dict[str, Unit], unit_id: str) -> list[str]:
    """Parent, grandparent, … (nearest first). A cycle in the data would loop; the tree ops
    below refuse to create one, and the guard here bounds the walk anyway."""
    out: list[str] = []
    seen = {unit_id}
    cur = by_id.get(unit_id)
    while cur is not None and cur.parent_id and cur.parent_id not in seen:
        out.append(cur.parent_id)
        seen.add(cur.parent_id)
        cur = by_id.get(cur.parent_id)
    return out


def descendants(by_id: dict[str, Unit], unit_id: str) -> set[str]:
    children: dict[str | None, list[str]] = {}
    for u in by_id.values():
        children.setdefault(u.parent_id, []).append(u.id)
    out: set[str] = set()
    stack = list(children.get(unit_id, []))
    while stack:
        n = stack.pop()
        if n in out:
            continue
        out.add(n)
        stack.extend(children.get(n, []))
    return out


def depth(by_id: dict[str, Unit], unit_id: str) -> int:
    return len(ancestors(by_id, unit_id))


def readable_units(by_id: dict[str, Unit], memberships: Iterable[UnitMember]) -> tuple[str, ...]:
    """The unit ids a person may read, nearest first: own units, then ancestors by distance,
    then the subtrees they administer with ``read_descendants``."""
    own = [m.unit_id for m in memberships if m.unit_id in by_id]
    ordered: list[str] = list(dict.fromkeys(own))
    frontier = list(ordered)
    while frontier:
        nxt: list[str] = []
        for uid in frontier:
            u = by_id.get(uid)
            if u is not None and u.parent_id and u.parent_id not in ordered:
                ordered.append(u.parent_id)
                nxt.append(u.parent_id)
        frontier = nxt
    for m in memberships:
        u = by_id.get(m.unit_id)
        if m.role == "admin" and u is not None and bool((u.policy or {}).get("read_descendants")):
            for d in sorted(descendants(by_id, m.unit_id)):
                if d not in ordered:
                    ordered.append(d)
    return tuple(ordered)


def writable_units(by_id: dict[str, Unit], memberships: Iterable[UnitMember]) -> tuple[str, ...]:
    """Where a person may write: their own units always; an ancestor when its policy lets members
    publish up (`publish_up: members`, the default), or lets admins and the person holds the
    admin role somewhere in their line (`publish_up: admins`). Never a descendant or a sibling."""
    own = [m.unit_id for m in memberships if m.unit_id in by_id]
    is_admin_below = any(m.role == "admin" and m.unit_id in by_id for m in memberships)
    out: list[str] = list(dict.fromkeys(own))
    for uid in list(out):
        for anc in ancestors(by_id, uid):
            pol = by_id[anc].policy or {}
            if pol.get("publish_up", "members") == "admins" and not is_admin_below:
                continue
            if anc not in out:
                out.append(anc)
    return tuple(out)


def hop_closed_units(by_id: dict[str, Unit], unit_ids: Iterable[str]) -> frozenset[str]:
    """Units whose policy says HOPs may not write (`hops_may_write: false`)."""
    return frozenset(u for u in unit_ids if u in by_id and (by_id[u].policy or {}).get("hops_may_write", True) is False)


def administered_units(by_id: dict[str, Unit], memberships: Iterable[UnitMember]) -> set[str]:
    """Units a person administers: the ones they hold the admin role on, and their subtrees."""
    out: set[str] = set()
    for m in memberships:
        if m.role == "admin" and m.unit_id in by_id:
            out.add(m.unit_id)
            out |= descendants(by_id, m.unit_id)
    return out


# ── persistence ──────────────────────────────────────────────────────────────

async def load_tree(session: AsyncSession, org_id: str | None) -> dict[str, Unit]:
    rows = (await session.execute(select(Unit).where(Unit.org_id == org_id))).scalars().all()
    return {u.id: u for u in rows}


async def memberships_of(session: AsyncSession, org_id: str | None, user_id: str) -> list[UnitMember]:
    return list((await session.execute(
        select(UnitMember).where(UnitMember.org_id == org_id, UnitMember.user_id == user_id)
    )).scalars().all())


async def create_unit(session: AsyncSession, *, org_id: str, name: str, parent_id: str | None,
                      kind: str = "unit", policy: dict | None = None, created_by: str | None) -> Unit:
    name = name.strip()
    if not name:
        raise UnitError("a unit needs a name")
    by_id = await load_tree(session, org_id)
    if parent_id is not None and parent_id not in by_id:
        raise UnitError("parent unit not found in this tenant")
    if any(u.parent_id == parent_id and u.name == name for u in by_id.values()):
        raise UnitError(f"a unit named {name!r} already exists under that parent")
    unit = Unit(org_id=org_id, parent_id=parent_id, name=name, kind=kind.strip() or "unit",
                policy=UnitPolicy(**(policy or {})).model_dump(), created_by=created_by)
    session.add(unit)
    await session.flush()
    invalidate(org_id)
    return unit


_UNSET = object()


async def update_unit(session: AsyncSession, *, org_id: str, unit_id: str, name: str | None = None,
                      kind: str | None = None, parent_id: object = _UNSET, policy: dict | None = None) -> Unit:
    by_id = await load_tree(session, org_id)
    unit = by_id.get(unit_id)
    if unit is None:
        raise UnitError("unit not found")
    if parent_id is not _UNSET:
        new_parent = parent_id  # type: ignore[assignment]
        if new_parent is not None:
            if new_parent not in by_id:
                raise UnitError("parent unit not found in this tenant")
            if new_parent == unit_id or new_parent in descendants(by_id, unit_id):
                raise UnitError("a unit cannot be moved under itself or one of its descendants")
        unit.parent_id = new_parent  # type: ignore[assignment]
    if name is not None:
        if not name.strip():
            raise UnitError("a unit needs a name")
        unit.name = name.strip()
    if kind is not None:
        unit.kind = kind.strip() or "unit"
    if policy is not None:
        unit.policy = UnitPolicy(**policy).model_dump()
    if any(u.id != unit.id and u.parent_id == unit.parent_id and u.name == unit.name for u in by_id.values()):
        raise UnitError(f"a unit named {unit.name!r} already exists under that parent")
    await session.flush()
    invalidate(org_id)
    return unit


async def delete_unit(session: AsyncSession, *, org_id: str, unit_id: str) -> None:
    """Refuses a unit with children or members: nothing under it is deleted by accident."""
    by_id = await load_tree(session, org_id)
    if unit_id not in by_id:
        raise UnitError("unit not found")
    if any(u.parent_id == unit_id for u in by_id.values()):
        raise UnitError("move or delete its child units first")
    n = (await session.execute(select(UnitMember).where(UnitMember.unit_id == unit_id))).scalars().first()
    if n is not None:
        raise UnitError("remove its members first")
    await session.execute(delete(Unit).where(Unit.id == unit_id, Unit.org_id == org_id))
    invalidate(org_id)


async def set_member(session: AsyncSession, *, org_id: str, unit_id: str, user_id: str,
                     role: str = "member", added_by: str | None) -> UnitMember:
    if role not in ROLES:
        raise UnitError(f"role must be one of {', '.join(ROLES)}")
    if unit_id not in await load_tree(session, org_id):
        raise UnitError("unit not found")
    m = await session.get(UnitMember, (unit_id, user_id))
    if m is None:
        m = UnitMember(org_id=org_id, unit_id=unit_id, user_id=user_id, role=role, added_by=added_by)
        session.add(m)
    else:
        m.role = role
    await session.flush()
    invalidate(org_id)
    return m


async def remove_member(session: AsyncSession, *, org_id: str, unit_id: str, user_id: str) -> bool:
    res = await session.execute(delete(UnitMember).where(
        UnitMember.org_id == org_id, UnitMember.unit_id == unit_id, UnitMember.user_id == user_id))
    invalidate(org_id)
    return (res.rowcount or 0) > 0


# ── per-request resolution, cached briefly ───────────────────────────────────

@dataclass(frozen=True)
class UnitAccess:
    """What the tree grants one person: where they read (nearest first), where they write,
    what they administer, and which of their writable units refuse HOP writes."""

    readable: tuple[str, ...] = ()
    writable: tuple[str, ...] = ()
    administered: frozenset[str] = frozenset()
    hop_closed: frozenset[str] = frozenset()


_NONE = UnitAccess()
_CACHE: dict[tuple[str | None, str], tuple[float, UnitAccess]] = {}


def invalidate(org_id: str | None) -> None:
    for k in [k for k in _CACHE if k[0] == org_id]:
        _CACHE.pop(k, None)


async def resolve(session: AsyncSession, org_id: str | None, user_id: str | None) -> UnitAccess:
    if not user_id or org_id is None:
        return _NONE
    key = (org_id, user_id)
    hit = _CACHE.get(key)
    now = time.monotonic()
    if hit and hit[0] > now:
        return hit[1]
    by_id = await load_tree(session, org_id)
    ms = await memberships_of(session, org_id, user_id)
    writable = writable_units(by_id, ms)
    access = UnitAccess(readable=readable_units(by_id, ms), writable=writable,
                        administered=frozenset(administered_units(by_id, ms)), hop_closed=hop_closed_units(by_id, writable))
    _CACHE[key] = (now + CACHE_TTL_S, access)
    return access
