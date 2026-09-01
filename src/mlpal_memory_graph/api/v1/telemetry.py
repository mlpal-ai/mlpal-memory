"""Harness telemetry ingest — the HTTP entry point for the D11.x contracts.

The harness's ``telemetryEmit`` seam POSTs raw RunOutcomeEvents / TuningLedger
entries here; each is validated + normalized by the content-free plugin
(``ingest/plugins/harness_telemetry``) and stored as an episode. Contract
violations are rejected per event with the exact reason (422) — never coerced,
never partially accepted silently: the response lists every rejection so an
emitter bug is loud on the first run, not discovered at distillation time.

Tenant pinning mirrors /episodes: only the internal service identity may target
a tenant per event (multi-tenant fleet ingest); every other caller is pinned to
its own org.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ...db import get_session
from ...ingest.plugins.harness_telemetry import (
    LEDGER_ACTIONS,
    TelemetryContractError,
    normalize_ledger_entry,
    normalize_run_outcome,
)
from ...repositories.episodes import insert_episode
from ..deps import AuthIdentity, require_permission

router = APIRouter(prefix="/telemetry", tags=["ingest"])


class TelemetryBatch(BaseModel):
    events: list[dict] = Field(..., min_length=1, max_length=500)


class TelemetryRejection(BaseModel):
    index: int
    reason: str


class TelemetryResponse(BaseModel):
    accepted: int
    duplicates: int
    rejected: list[TelemetryRejection]


@router.post("", status_code=202, response_model=TelemetryResponse)
async def ingest_telemetry(
    body: TelemetryBatch,
    session: Annotated[AsyncSession, Depends(get_session)],
    identity: Annotated[AuthIdentity, Depends(require_permission("memory.write"))],
) -> TelemetryResponse:
    accepted = duplicates = 0
    rejected: list[TelemetryRejection] = []
    for i, event in enumerate(body.events):
        try:
            action = event.get("action_type") if isinstance(event, dict) else None
            if action in LEDGER_ACTIONS:
                env = normalize_ledger_entry(event, user_id=str(identity.user_id or "harness"))
            else:
                env = normalize_run_outcome(event, user_id=str(identity.user_id or "harness"))
        except (TelemetryContractError, KeyError, TypeError, ValueError) as exc:
            rejected.append(TelemetryRejection(index=i, reason=str(exc)[:300]))
            continue
        # tenant boundary — same rule as /episodes
        if identity.is_service:
            env.org_id = event.get("org_id") or identity.org_id
        else:
            env.org_id = identity.org_id
        inserted = await insert_episode(session, env.to_episode_kwargs(capture_content=False))
        if inserted:
            accepted += 1
        else:
            duplicates += 1
    return TelemetryResponse(accepted=accepted, duplicates=duplicates, rejected=rejected)
