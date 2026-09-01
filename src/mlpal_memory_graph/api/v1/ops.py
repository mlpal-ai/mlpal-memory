"""Ops/observability endpoint — corpus stats + the contradiction-backlog invariant gauge."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from ...db import get_session
from ...services.observability import memory_stats
from ..deps import AuthIdentity, require_permission, rls_guard

router = APIRouter(prefix="/ops", tags=["ops"], dependencies=[Depends(rls_guard)])


@router.get("/dev-identity")
async def dev_identity() -> dict:
    """DEV-ONLY identity hint for the local UI. The local corpus is owned by the
    machine user; a wrong sidebar identity legitimately sees an almost-empty
    tenant (owner-only personal memory) with no explanation — the UI uses this
    to default correctly and to explain empty views. 404s outside dev auth."""
    import os

    from fastapi import HTTPException

    from ...core.config import get_settings

    s = get_settings()
    if not s.dev_auth:
        raise HTTPException(status_code=404)
    return {
        "org": os.getenv("MLPAL_DEMO_ORG_ID", "local"),
        "user": os.getenv("MLPAL_DEMO_USER_ID") or os.getenv("USER") or "demo",
    }


@router.get("/stats")
async def stats(
    session: Annotated[AsyncSession, Depends(get_session)],
    identity: Annotated[AuthIdentity, Depends(require_permission("memory.read"))],
) -> dict:
    """Node/edge/episode/document/chunk counts + ``contradiction_backlog`` (should be ~0), scoped
    to the caller's tenant."""
    return await memory_stats(session, tenant_id=identity.org_id)
