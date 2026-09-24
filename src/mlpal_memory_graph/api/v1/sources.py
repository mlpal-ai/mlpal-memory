"""Sources API (memory v7 WP4): register a files or sql source, list, promote items for a question,
and query a sql source read-only. Registration is a write in the source's scope."""

from __future__ import annotations

import time
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...db import get_session
from ...db.models import MemorySource
from ...schemas.sources import (
    PromoteRequest,
    PromoteResponse,
    RegisterSourceRequest,
    SourceListResponse,
    SourceOut,
    SourceQueryRequest,
    SourceQueryResponse,
)
from ...services import sources as svc
from ..deps import AuthIdentity, authorize_write_scope, require_permission

router = APIRouter(prefix="/sources", tags=["sources"])


def _out(s: MemorySource, schema: dict | None = None) -> SourceOut:
    c = s.config or {}
    return SourceOut(name=s.name, kind=s.kind, status=s.status, item_count=s.item_count, scope=s.scope, scope_id=s.scope_id,
                     workspace=s.workspace, tables=list(c.get("tables") or []), schema_tables=schema,
                     indexed_at=s.indexed_at.isoformat() if s.indexed_at else None,
                     repo=c.get("repo"), branch=c.get("branch"), admit=c.get("admit"), last_sync=c.get("last_sync"), last_error=c.get("last_error"))


@router.post("", response_model=SourceOut, status_code=201)
async def register_source(
    body: RegisterSourceRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    identity: Annotated[AuthIdentity, Depends(require_permission("memory.write"))],
) -> SourceOut:
    authorize_write_scope(identity, body.scope, body.scope_id or (identity.org_id if body.scope == "org" else None))
    scope_id = body.scope_id or (identity.org_id if body.scope == "org" else None)
    try:
        if body.kind == "files":
            if not body.root:
                raise svc.SourceError("a files source needs `root`")
            src = await svc.register_files(session, org_id=identity.org_id, user_id=identity.user_id, name=body.name, root=body.root,
                                           scope=body.scope, scope_id=scope_id, workspace=body.workspace)
            schema = None
        elif body.kind == "github":
            if not body.repo:
                raise svc.SourceError("a GitHub source needs `repo` as owner/name")
            src = await svc.register_github(session, org_id=identity.org_id, user_id=identity.user_id, name=body.name, repo=body.repo,
                                            branch=body.branch, paths=body.paths, credential_ref=body.credential_ref, admit_mode=body.admit,
                                            interval_minutes=body.interval_minutes, scope=body.scope, scope_id=scope_id, workspace=body.workspace)
            schema = None
        else:
            if not body.url:
                raise svc.SourceError("a sql source needs `url`")
            src, schema = await svc.register_sql(session, org_id=identity.org_id, user_id=identity.user_id, name=body.name, url=body.url,
                                                 tables=body.tables, row_limit=body.row_limit, scope=body.scope, scope_id=scope_id,
                                                 workspace=body.workspace)
    except svc.SourceError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await session.commit()
    return _out(src, schema)


@router.get("", response_model=SourceListResponse)
async def list_sources(
    session: Annotated[AsyncSession, Depends(get_session)],
    identity: Annotated[AuthIdentity, Depends(require_permission("memory.read"))],
) -> SourceListResponse:
    rows = (await session.execute(select(MemorySource).where(MemorySource.org_id == identity.org_id).order_by(MemorySource.name))).scalars().all()
    return SourceListResponse(sources=[_out(s) for s in rows])


@router.post("/{name}/promote", response_model=PromoteResponse)
async def promote(
    name: str,
    body: PromoteRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    identity: Annotated[AuthIdentity, Depends(require_permission("memory.write"))],
) -> PromoteResponse:
    src = await svc.get_source(session, identity.org_id, name)
    if src is None or src.kind != "files":
        raise HTTPException(status_code=404, detail=f"files source {name} not found")
    out = []
    for item in await svc.candidates(session, src, body.query, body.limit):
        out.append({"source": src.name, **(await svc.admit(session, src, item, user_id=identity.user_id, by="promote"))})
    await session.commit()
    return PromoteResponse(promoted=out)


@router.post("/{name}/query", response_model=SourceQueryResponse)
async def query_source(
    name: str,
    body: SourceQueryRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    identity: Annotated[AuthIdentity, Depends(require_permission("memory.read"))],
) -> SourceQueryResponse:
    src = await svc.get_source(session, identity.org_id, name)
    if src is None:
        raise HTTPException(status_code=404, detail=f"source {name} not found")
    t0 = time.monotonic()
    try:
        res = await svc.query(session, src, sql=body.sql, user_id=identity.user_id)
    except svc.SourceError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — the source's own error, reported not swallowed
        raise HTTPException(status_code=400, detail=f"query failed: {str(exc)[:300]}") from exc
    await session.commit()
    return SourceQueryResponse(query_id=res.query_id, columns=res.columns, rows=res.rows, row_count=res.row_count,
                               truncated=res.truncated, tables=res.tables, took_ms=int((time.monotonic() - t0) * 1000))
