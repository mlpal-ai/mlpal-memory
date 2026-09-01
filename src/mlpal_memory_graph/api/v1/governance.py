"""Consent (collect/update opt-out) + extraction-policy administration.

Authorization (design-proposal §5, §6): a user may govern their *own* personal (USER) scope;
governing org/team scope requires ``memory.admin``. Org/team consent composes via
deny-precedence at fold time, so an org OFF cannot be overridden by a narrower scope.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.logging import get_logger
from ...core.scope import Scope, ScopeRef
from ...db import get_session
from ...db.models import ConsentState, ExtractionPolicy
from ...graph import get_driver
from ...schemas.governance import (
    ConsentRequest,
    ConsentResponse,
    PolicyRequest,
    PolicyResponse,
)
from ..deps import AuthIdentity, require_permission

router = APIRouter(prefix="/memory", tags=["governance"])
log = get_logger(__name__)


def _authorize_scope_write(identity: AuthIdentity, scope: Scope, scope_id: str) -> None:
    """A user governs only their own personal scope; org/team requires admin."""
    if scope is Scope.USER and scope_id == identity.user_id:
        return
    if identity.is_admin():
        return
    raise HTTPException(status_code=403, detail="not permitted to govern this scope")


def _pk(identity: AuthIdentity, scope: Scope, scope_id: str) -> tuple[str, str, str]:
    if identity.org_id is None:
        raise HTTPException(status_code=400, detail="no tenant in identity")
    return (identity.org_id, scope.value, scope_id)


@router.put("/consent", response_model=ConsentResponse)
async def set_consent(
    body: ConsentRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    identity: Annotated[AuthIdentity, Depends(require_permission("memory.write"))],
) -> ConsentResponse:
    _authorize_scope_write(identity, body.scope, body.scope_id)
    org_id, scope, scope_id = _pk(identity, body.scope, body.scope_id)

    purged_nodes = purged_edges = purged_documents = purged_chunks = 0
    stored_state = body.state
    if body.state == "clear":
        # irreversible: purge existing memory at this scope (both tiers), then record OFF.
        ref = ScopeRef(body.scope, scope_id)
        purged_nodes, purged_edges = await get_driver().purge_scope(
            session, tenant_id=org_id, scope=ref
        )
        from ...services.direct import DirectMemory

        purged_documents, purged_chunks = await DirectMemory().purge_scope(
            session, tenant_id=org_id, scope=ref
        )
        stored_state = "off"

    row = await session.get(ConsentState, (org_id, scope, scope_id))
    if row is None:
        row = ConsentState(org_id=org_id, scope=scope, scope_id=scope_id)
        session.add(row)
    row.state = stored_state
    row.updated_by = identity.user_id
    # commit HERE, not in the session dependency's teardown: teardown runs after the
    # response is sent, so two sequential governance PUTs can land their commits out of
    # order (observed live by the deletion probe: a `clear` commit overtaking the
    # following `active` commit). A governance decision must be durable — and ordered —
    # the moment the caller sees 200.
    await session.commit()

    log.info(
        "consent.state_changed",
        scope=f"{scope}:{scope_id}",
        action=body.state,
        purged_nodes=purged_nodes,
        by=identity.user_id,
    )
    return ConsentResponse(
        scope=scope,
        scope_id=scope_id,
        state=stored_state,
        purged_nodes=purged_nodes,
        purged_edges=purged_edges,
        purged_documents=purged_documents,
        purged_chunks=purged_chunks,
    )


@router.get("/consent", response_model=ConsentResponse)
async def get_consent(
    session: Annotated[AsyncSession, Depends(get_session)],
    identity: Annotated[AuthIdentity, Depends(require_permission("memory.read"))],
    scope: Scope = Query(...),
    scope_id: str = Query(...),
) -> ConsentResponse:
    _authorize_scope_write(identity, scope, scope_id)
    org_id, scope_v, scope_id = _pk(identity, scope, scope_id)
    row = await session.get(ConsentState, (org_id, scope_v, scope_id))
    return ConsentResponse(scope=scope_v, scope_id=scope_id, state=row.state if row else "active")


@router.put("/policy", response_model=PolicyResponse)
async def set_policy(
    body: PolicyRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    identity: Annotated[AuthIdentity, Depends(require_permission("memory.write"))],
) -> PolicyResponse:
    _authorize_scope_write(identity, body.scope, body.scope_id)
    org_id, scope, scope_id = _pk(identity, body.scope, body.scope_id)

    row = await session.get(ExtractionPolicy, (org_id, scope, scope_id))
    policy = {
        "deny_sources": body.deny_sources,
        "allow_sources": body.allow_sources,
        "metadata_deny": body.metadata_deny,
    }
    if row is None:
        row = ExtractionPolicy(org_id=org_id, scope=scope, scope_id=scope_id, policy=policy)
        session.add(row)
    else:
        row.policy = policy
        row.version += 1
    row.updated_by = identity.user_id
    await session.commit()  # same ordering guarantee as set_consent
    log.info("extraction.policy_updated", scope=f"{scope}:{scope_id}", by=identity.user_id)
    return PolicyResponse(scope=scope, scope_id=scope_id, version=row.version)
