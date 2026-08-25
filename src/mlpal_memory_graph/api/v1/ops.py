"""Ops/observability endpoint — corpus stats + the contradiction-backlog invariant gauge."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from ...db import get_session
from ...services.observability import memory_stats
from ..deps import AuthIdentity, require_permission, rls_guard

router = APIRouter(prefix="/ops", tags=["ops"], dependencies=[Depends(rls_guard)])


@router.get("/stats")
async def stats(
    session: Annotated[AsyncSession, Depends(get_session)],
    identity: Annotated[AuthIdentity, Depends(require_permission("memory:read"))],
) -> dict:
    """Node/edge/episode/document/chunk counts + ``contradiction_backlog`` (should be ~0), scoped
    to the caller's tenant."""
    return await memory_stats(session, tenant_id=identity.org_id)
