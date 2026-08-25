"""Generic ingestion endpoint — the 'easy to plug in anywhere' write surface.

Any MLPal service, CI step, git hook or MCP tool POSTs a batch of episode envelopes with a
Bearer token or X-Internal-Service-Key. Episodes are deduped by event_id and folded into the
graph asynchronously by the worker; pass ?process=true to fold inline (handy for demos/tests).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.config import get_settings
from ...db import get_session
from ...db.models import Episode
from ...db.scoping import browse_clause
from ...repositories.episodes import insert_episode
from ...schemas.episode import (
    EpisodeDetailResponse,
    EpisodeListResponse,
    EpisodeOut,
    IngestRequest,
    IngestResponse,
)
from ..deps import AuthIdentity, authorize_write_scope, get_updater, require_permission

router = APIRouter(prefix="/episodes", tags=["ingest"])


@router.post("", status_code=202, response_model=IngestResponse)
async def ingest_episodes(
    body: IngestRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    identity: Annotated[AuthIdentity, Depends(require_permission("memory:write"))],
    process: bool = False,
) -> IngestResponse:
    settings = get_settings()
    accepted = duplicates = processed = 0
    updater = get_updater() if process else None

    for env in body.episodes:
        # Tenant boundary: only the trusted machine-to-machine service may target a tenant
        # per-episode (multi-tenant ingest). Every other caller — users, org admins — is pinned
        # to their own org, so a body-supplied org_id can't smuggle a write into another tenant.
        if identity.is_service:
            if env.org_id is None:
                env.org_id = identity.org_id
        else:
            env.org_id = identity.org_id
        authorize_write_scope(identity, env.scope, env.scope_id)
        kwargs = env.to_episode_kwargs(capture_content=settings.content_capture_default)
        inserted = await insert_episode(session, kwargs)
        if not inserted:
            duplicates += 1
            continue
        accepted += 1
        if updater is not None:
            episode = await session.get(Episode, env.event_id)
            await updater.process_episode(session, episode)
            processed += 1

    return IngestResponse(accepted=accepted, duplicates=duplicates, processed=processed)


def _status_of(e: Episode) -> str:
    if e.dead_at is not None:
        return "dead"
    if e.dropped_reason:
        return "dropped"
    return "processed" if e.processed else "pending"


def _episode_out(e: Episode) -> EpisodeOut:
    return EpisodeOut(
        event_id=e.event_id,
        occurred_at=e.occurred_at,
        ingested_at=e.ingested_at,
        source=e.source,
        action_type=e.action_type,
        scope=e.scope,
        scope_id=e.scope_id,
        workspace=e.workspace,
        lifecycle=e.lifecycle,
        tier=e.tier,
        status=_status_of(e),
        processed_at=e.processed_at,
        dropped_reason=e.dropped_reason,
        error_count=e.error_count,
        dead_at=e.dead_at,
    )


def _browse(identity: AuthIdentity):
    return browse_clause(
        Episode,
        tenant_id=identity.org_id,
        user_id=identity.user_id,
        team_ids=tuple(identity.team_ids),
    )


@router.get("", response_model=EpisodeListResponse)
async def list_episodes(
    session: Annotated[AsyncSession, Depends(get_session)],
    identity: Annotated[AuthIdentity, Depends(require_permission("memory:read"))],
    status: str | None = Query(None, pattern="^(pending|processed|dropped|dead)$"),
    source: str | None = Query(None),
    workspace: str | None = Query(None),
    limit: int = Query(25, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> EpisodeListResponse:
    """Browse the episode ledger (UI listing) — what flowed in, what folded, what was
    declined (dropped_reason) and what dead-lettered. Same visibility rule as retrieval."""
    where = [_browse(identity)]
    if source:
        where.append(Episode.source == source)
    if workspace:
        where.append(Episode.workspace == workspace)
    if status == "dead":
        where.append(Episode.dead_at.isnot(None))
    elif status == "dropped":
        where.append(Episode.dropped_reason.isnot(None))
    elif status == "processed":
        where.append(Episode.processed.is_(True))
        where.append(Episode.dropped_reason.is_(None))
    elif status == "pending":
        where.append(Episode.processed.is_(False))
        where.append(Episode.dead_at.is_(None))
        where.append(Episode.dropped_reason.is_(None))

    total = (
        await session.execute(select(func.count()).select_from(Episode).where(*where))
    ).scalar_one()
    rows = (
        (
            await session.execute(
                select(Episode)
                .where(*where)
                .order_by(Episode.ingested_at.desc())
                .limit(limit)
                .offset(offset)
            )
        )
        .scalars()
        .all()
    )
    return EpisodeListResponse(
        episodes=[_episode_out(e) for e in rows], total=total, limit=limit, offset=offset
    )


@router.get("/{event_id}", response_model=EpisodeDetailResponse)
async def get_episode(
    event_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    identity: Annotated[AuthIdentity, Depends(require_permission("memory:read"))],
) -> EpisodeDetailResponse:
    e = (
        await session.execute(
            select(Episode).where(Episode.event_id == event_id, _browse(identity))
        )
    ).scalar_one_or_none()
    if e is None:
        raise HTTPException(status_code=404, detail="episode not found")
    return EpisodeDetailResponse(
        **_episode_out(e).model_dump(),
        payload=e.payload or {},
        error=e.error,
        has_content=bool(e.content),
    )
