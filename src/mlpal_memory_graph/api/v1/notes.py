"""Workspace notes — REST surface for the curated working-memory tier.

Reads: the org's notes are visible to every org member; a user's notes only to that user
(and the service identity); an invisible note is a 404, never a 403, so existence does not
leak. Writes go through the same hard gate as every other write surface
(``authorize_write_scope``) and honour consent on the user scope.
"""
from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.scope import Scope, ScopeRef
from ...db import get_session
from ...db.models import Note
from ...schemas.notes import (
    NoteHistoryOut,
    NoteListOut,
    NoteOut,
    NotePatch,
    NotePut,
    NotesContextOut,
    NoteSummary,
    NoteVersionOut,
)
from ...services import notes as svc
from ...services.policy import resolve_consent
from ...services.resolution import RetrievalContext
from ..deps import AuthIdentity, authorize_write_scope, require_permission, rls_guard

router = APIRouter(prefix="/notes", tags=["notes"], dependencies=[Depends(rls_guard)])

_READ = Annotated[AuthIdentity, Depends(require_permission("memory.read"))]
_WRITE = Annotated[AuthIdentity, Depends(require_permission("memory.write"))]
_SESSION = Annotated[AsyncSession, Depends(get_session)]


def _visible(identity: AuthIdentity, scope: str, scope_id: str) -> bool:
    if identity.is_service:
        return True
    if scope == Scope.ORG.value:
        return scope_id == identity.org_id
    if scope == Scope.USER.value:
        return scope_id == identity.user_id
    return False  # team/service/repo/agent notes: not in v1


def _check_scope(scope: str) -> None:
    if scope not in (Scope.ORG.value, Scope.USER.value):
        raise HTTPException(422, detail="notes exist at 'org' or 'user' scope in v1")


def _out(read: svc.NoteRead) -> NoteOut:
    n = read.note
    return NoteOut(
        id=n.id, scope=n.scope, scope_id=n.scope_id, workspace=n.workspace, title=n.title,
        body=read.body, sections=svc.parse_sections(read.body) if read.body else {s: "" for s in svc.SECTIONS},
        version=read.version, updated_by=n.updated_by, updated_at=n.updated_at, as_of=read.as_of,
        stale_citations=read.stale_citations,
    )


async def _authorize_write(session, identity: AuthIdentity, scope: str, scope_id: str) -> None:
    _check_scope(scope)
    authorize_write_scope(identity, scope, scope_id)
    if scope == Scope.ORG.value and scope_id != identity.org_id and not identity.is_service:
        raise HTTPException(403, detail="org notes are written within the caller's own org")
    if scope == Scope.USER.value:
        # A person's notes are theirs alone: not even an org admin writes them (the generic gate
        # trusts admins to backfill any scope; notes are the one place that trust does not reach).
        if scope_id != identity.user_id and not identity.is_service:
            raise HTTPException(403, detail="only the owner writes their own notes")
        blocking = await resolve_consent(session, identity.org_id, ScopeRef(Scope.USER, scope_id))
        if blocking:
            raise HTTPException(403, detail=f"consent is '{blocking}' for this scope; notes are not written")


@router.get("", response_model=NoteListOut)
async def list_notes(session: _SESSION, identity: _READ) -> NoteListOut:
    stmt = select(Note).where(Note.org_id == identity.org_id).order_by(Note.scope, Note.workspace)
    rows = (await session.execute(stmt)).scalars().all()
    return NoteListOut(notes=[
        NoteSummary(id=n.id, scope=n.scope, scope_id=n.scope_id, workspace=n.workspace, title=n.title,
                    version=n.version, updated_by=n.updated_by, updated_at=n.updated_at, chars=len(n.body))
        for n in rows if _visible(identity, n.scope, n.scope_id)
    ])


@router.get("/context", response_model=NotesContextOut)
async def notes_context(
    session: _SESSION, identity: _READ,
    workspace: str | None = Query(None),
    token_budget: int = Query(3000, ge=200, le=20000),
) -> NotesContextOut:
    """The always-on bundle: org index → workspace note → your notes, budget-capped."""
    ctx = RetrievalContext(tenant_id=identity.org_id, user_id=identity.user_id,
                           team_ids=tuple(identity.team_ids), subjects=(), workspace=workspace)
    c = await svc.render_context(session, ctx, workspace=workspace, token_budget=token_budget)
    return NotesContextOut(markdown=c.markdown, estimated_tokens=c.estimated_tokens, notes=c.notes,
                           truncated=c.truncated)


@router.get("/{scope}/{scope_id}", response_model=NoteOut)
async def get_note(
    scope: str, scope_id: str, session: _SESSION, identity: _READ,
    workspace: str = Query(""), as_of: datetime | None = Query(None),
) -> NoteOut:
    _check_scope(scope)
    if not _visible(identity, scope, scope_id):
        raise HTTPException(404, detail="note not found")
    read = await svc.get_note(session, identity.org_id, scope, scope_id, workspace, as_of=as_of)
    if read is None:
        raise HTTPException(404, detail="note not found")
    return _out(read)


@router.put("/{scope}/{scope_id}", response_model=NoteOut)
async def put_note(
    scope: str, scope_id: str, body: NotePut, session: _SESSION, identity: _WRITE,
    workspace: str = Query(""),
) -> NoteOut:
    await _authorize_write(session, identity, scope, scope_id)
    try:
        read = await svc.put_note(session, org_id=identity.org_id, scope=scope, scope_id=scope_id,
                                  workspace=workspace, body=body.body, updated_by=identity.user_id,
                                  reason=body.reason, base_version=body.base_version, title=body.title)
    except svc.NoteError as exc:
        raise HTTPException(422, detail=str(exc)) from exc
    except svc.NoteConflict as exc:
        raise HTTPException(409, detail=str(exc)) from exc
    await session.refresh(read.note)  # server-side timestamps, inside the transaction
    out = _out(read)
    await session.commit()
    return out


@router.patch("/{scope}/{scope_id}", response_model=NoteOut)
async def patch_note(
    scope: str, scope_id: str, body: NotePatch, session: _SESSION, identity: _WRITE,
    workspace: str = Query(""),
) -> NoteOut:
    await _authorize_write(session, identity, scope, scope_id)
    try:
        read = await svc.patch_section(session, org_id=identity.org_id, scope=scope, scope_id=scope_id,
                                       workspace=workspace, section=body.section, op=body.op,
                                       text=body.text, updated_by=identity.user_id, reason=body.reason,
                                       base_version=body.base_version)
    except svc.NoteError as exc:
        raise HTTPException(422, detail=str(exc)) from exc
    except svc.NoteConflict as exc:
        raise HTTPException(409, detail=str(exc)) from exc
    await session.refresh(read.note)  # server-side timestamps, inside the transaction
    out = _out(read)
    await session.commit()
    return out


@router.get("/{scope}/{scope_id}/history", response_model=NoteHistoryOut)
async def note_history(
    scope: str, scope_id: str, session: _SESSION, identity: _READ,
    workspace: str = Query(""), limit: int = Query(50, ge=1, le=500),
) -> NoteHistoryOut:
    _check_scope(scope)
    if not _visible(identity, scope, scope_id):
        raise HTTPException(404, detail="note not found")
    note = await svc.find_note(session, identity.org_id, scope, scope_id, workspace)
    if note is None:
        raise HTTPException(404, detail="note not found")
    versions = await svc.history(session, note, limit=limit)
    return NoteHistoryOut(note_id=note.id, versions=[
        NoteVersionOut(version=v.version, updated_by=v.updated_by, reason=v.reason,
                       created_at=v.created_at, chars=len(v.body)) for v in versions])
