"""Units — the org's hierarchy (memory v12 §2b): define the tree, place people in it, set each
unit's policy. Who may administer: a memory admin anywhere in the tenant; otherwise a person who
holds the admin role on the unit or on one of its ancestors."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...db import get_session
from ...db.models import Unit, UnitMember
from ...schemas.units import MemberIn, MemberOut, MembersOut, UnitIn, UnitListOut, UnitOut, UnitPatch
from ...services import units as svc
from ..deps import AuthIdentity, require_permission

router = APIRouter(prefix="/units", tags=["units"])

_SESSION = Annotated[AsyncSession, Depends(get_session)]
_READ = Annotated[AuthIdentity, Depends(require_permission("memory.read"))]
_WRITE = Annotated[AuthIdentity, Depends(require_permission("memory.write"))]


def _tenant(identity: AuthIdentity) -> str:
    if identity.org_id is None:
        raise HTTPException(status_code=403, detail="units need a tenant")
    return identity.org_id


async def _may_administer(session: AsyncSession, identity: AuthIdentity, unit_id: str | None) -> None:
    """Root-level changes need a memory admin; changes inside the tree need the admin role on the
    unit or an ancestor (the resolved `administered` set already includes subtrees)."""
    if identity.is_service or identity.is_admin():
        return
    if unit_id is None:
        raise HTTPException(status_code=403, detail="creating a top-level unit needs memory.admin")
    if unit_id not in identity.administered_units:
        raise HTTPException(status_code=403, detail="not an admin of this unit or one above it")


async def _out(session: AsyncSession, by_id: dict[str, Unit]) -> list[UnitOut]:
    counts = dict((await session.execute(
        select(UnitMember.unit_id, func.count()).where(UnitMember.unit_id.in_(list(by_id) or ["-"]))
        .group_by(UnitMember.unit_id))).all())
    return [UnitOut(id=u.id, parent_id=u.parent_id, name=u.name, kind=u.kind, policy=u.policy or {},
                    depth=svc.depth(by_id, u.id), members=int(counts.get(u.id, 0)), created_by=u.created_by,
                    created_at=u.created_at.isoformat() if u.created_at else None)
            for u in sorted(by_id.values(), key=lambda u: (svc.depth(by_id, u.id), u.name))]


@router.get("", response_model=UnitListOut)
async def list_units(session: _SESSION, identity: _READ) -> UnitListOut:
    org = _tenant(identity)
    by_id = await svc.load_tree(session, org)
    return UnitListOut(units=await _out(session, by_id), mine=list(identity.team_ids), administered=list(identity.administered_units),
                       admin=bool(identity.is_service or identity.is_admin()))


@router.post("", response_model=UnitOut, status_code=201)
async def create_unit(body: UnitIn, session: _SESSION, identity: _WRITE) -> UnitOut:
    org = _tenant(identity)
    await _may_administer(session, identity, body.parent_id)
    try:
        unit = await svc.create_unit(session, org_id=org, name=body.name, parent_id=body.parent_id,
                                     kind=body.kind, policy=body.policy, created_by=identity.user_id)
    except (svc.UnitError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await session.commit()
    by_id = await svc.load_tree(session, org)
    return next(o for o in await _out(session, by_id) if o.id == unit.id)


@router.patch("/{unit_id}", response_model=UnitOut)
async def update_unit(unit_id: str, body: UnitPatch, session: _SESSION, identity: _WRITE) -> UnitOut:
    org = _tenant(identity)
    await _may_administer(session, identity, unit_id)
    if body.move:
        await _may_administer(session, identity, body.parent_id)  # the destination too
    try:
        await svc.update_unit(session, org_id=org, unit_id=unit_id, name=body.name, kind=body.kind,
                              parent_id=body.parent_id if body.move else svc._UNSET, policy=body.policy)
    except (svc.UnitError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await session.commit()
    by_id = await svc.load_tree(session, org)
    return next(o for o in await _out(session, by_id) if o.id == unit_id)


@router.delete("/{unit_id}", status_code=204)
async def delete_unit(unit_id: str, session: _SESSION, identity: _WRITE) -> None:
    org = _tenant(identity)
    await _may_administer(session, identity, unit_id)
    try:
        await svc.delete_unit(session, org_id=org, unit_id=unit_id)
    except svc.UnitError as exc:
        raise HTTPException(status_code=409 if "first" in str(exc) else 404, detail=str(exc)) from exc
    await session.commit()


@router.get("/{unit_id}/members", response_model=MembersOut)
async def list_members(unit_id: str, session: _SESSION, identity: _READ) -> MembersOut:
    org = _tenant(identity)
    if not (identity.is_service or identity.is_admin() or unit_id in identity.team_ids or unit_id in identity.administered_units):
        raise HTTPException(status_code=404, detail="unit not found")
    rows = (await session.execute(select(UnitMember).where(UnitMember.org_id == org, UnitMember.unit_id == unit_id))).scalars().all()
    return MembersOut(members=[MemberOut(unit_id=m.unit_id, user_id=m.user_id, role=m.role, added_by=m.added_by,
                                         created_at=m.created_at.isoformat() if m.created_at else None) for m in rows])


@router.post("/{unit_id}/members", response_model=MemberOut, status_code=201)
async def add_member(unit_id: str, body: MemberIn, session: _SESSION, identity: _WRITE) -> MemberOut:
    org = _tenant(identity)
    await _may_administer(session, identity, unit_id)
    try:
        m = await svc.set_member(session, org_id=org, unit_id=unit_id, user_id=body.user_id, role=body.role,
                                 added_by=identity.user_id)
    except svc.UnitError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await session.commit()
    return MemberOut(unit_id=m.unit_id, user_id=m.user_id, role=m.role, added_by=m.added_by,
                     created_at=m.created_at.isoformat() if m.created_at else None)


@router.delete("/{unit_id}/members/{user_id}", status_code=204)
async def remove_member(unit_id: str, user_id: str, session: _SESSION, identity: _WRITE) -> None:
    org = _tenant(identity)
    await _may_administer(session, identity, unit_id)
    if not await svc.remove_member(session, org_id=org, unit_id=unit_id, user_id=user_id):
        raise HTTPException(status_code=404, detail="no such membership")
    await session.commit()
