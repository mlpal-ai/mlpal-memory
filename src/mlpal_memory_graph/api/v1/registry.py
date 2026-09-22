"""HOP registry, routing across HOP memories, the builder's brief, departure, fleet (memory v7)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...db import get_session
from ...db.models import Node
from ...schemas.registry import (
    TuneDecisionIn,
    TuneDecisionOut,
    TunePendingResponse,
    TuneProposalIn,
    TuneProposalOut,
    BriefResponse,
    DepartRequest,
    FleetResponse,
    HopListResponse,
    HopOut,
    HopRegisterRequest,
    RouteResponse,
)
from ...services import registry as svc
from ..deps import AuthIdentity, require_permission

router = APIRouter(prefix="/memory", tags=["registry"])


def _out(h) -> HopOut:
    c = h.contract or {}
    return HopOut(name=h.name, version=h.version, owner=h.owner_user_id, mode=h.mode, sharing=h.sharing, tenant=h.tenant,
                  workspace=h.workspace, topics=c.get("topics") or [], reads=c.get("reads") or [], writes=c.get("writes") or [],
                  ontology=c.get("ontology") or [])


@router.put("/hops/{name}", response_model=HopOut)
async def register_hop(
    name: str,
    body: HopRegisterRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    identity: Annotated[AuthIdentity, Depends(require_permission("memory.write"))],
) -> HopOut:
    """Idempotent: the engine (or a deploy step) registers the HOP's memory block. Owner, mode and
    sharing are the design's registry entry; topics, reads, writes and ontology are its contract."""
    row = await svc.register_hop(session, org_id=identity.org_id, user_id=identity.user_id, name=name, version=body.version,
                                 owner=body.owner, mode=body.mode, sharing=body.sharing, tenant=body.tenant, workspace=body.workspace,
                                 topics=body.topics, reads=body.reads, writes=body.writes, ontology=body.ontology)
    await session.commit()
    return _out(row)


@router.get("/hops", response_model=HopListResponse)
async def list_hops(
    session: Annotated[AsyncSession, Depends(get_session)],
    identity: Annotated[AuthIdentity, Depends(require_permission("memory.read"))],
) -> HopListResponse:
    return HopListResponse(hops=[_out(h) for h in await svc.list_hops(session, identity.org_id)],
                           ontology_extension=await svc.extension_classes(session, identity.org_id))


@router.get("/route", response_model=RouteResponse)
async def route_question(
    session: Annotated[AsyncSession, Depends(get_session)],
    identity: Annotated[AuthIdentity, Depends(require_permission("memory.read"))],
    q: str = Query(..., min_length=2),
    limit: int = Query(5, ge=1, le=20),
) -> RouteResponse:
    """The index across HOP memories: which registered topics, of which HOPs, could answer this."""
    return RouteResponse(question=q, routes=await svc.route(session, identity.org_id, q, limit))


@router.get("/brief", response_model=BriefResponse)
async def builder_brief(
    session: Annotated[AsyncSession, Depends(get_session)],
    identity: Annotated[AuthIdentity, Depends(require_permission("memory.read"))],
    hop: str = Query(..., min_length=1),
    window_days: int = Query(30, ge=1, le=365),
) -> BriefResponse:
    """What a builder turn reads before step one: build-phase memory, version scores, deviations,
    what the company already knows for the workspace, fleet facts."""
    return BriefResponse(**await svc.brief(session, org_id=identity.org_id, hop=hop, window_days=window_days))


@router.post("/depart")
async def depart_member(
    body: DepartRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    identity: Annotated[AuthIdentity, Depends(require_permission("memory.write"))],
) -> dict:
    """A member leaves: their person scope is purged in both tiers, lifted facts keep a provenance
    pointer, and a certificate is returned and ledgered. An admin's act."""
    if not identity.is_admin() and not identity.is_service:
        raise HTTPException(status_code=403, detail="departure is an admin act")
    from ...services.depart import depart

    cert = await depart(session, org_id=identity.org_id, user_id=body.user_id, issued_by=identity.user_id, reason=body.reason)
    await session.commit()
    return cert


@router.get("/fleet", response_model=FleetResponse)
async def fleet_facts(
    session: Annotated[AsyncSession, Depends(get_session)],
    identity: Annotated[AuthIdentity, Depends(require_permission("memory.read"))],
    hop: str | None = Query(None),
) -> FleetResponse:
    rows = (await session.execute(select(Node).where(Node.scope == "global", Node.type == "Fact"))).scalars().all()
    facts = [{"fact": (n.props or {}).get("statement") or n.name, "hops": (n.props or {}).get("hops"), "companies": (n.props or {}).get("companies"),
              "computed_at": (n.props or {}).get("computed_at")} for n in rows if (n.props or {}).get("fleet")]
    if hop:
        facts = [f for f in facts if hop in (f.get("hops") or [])]
    return FleetResponse(facts=facts)


# ---- memory v10: the owner's door on the tuning loop --------------------------------------------
# A tune turn writes a proposal record; the engine lists the pending ones on open and opens one for
# review; the owner's decision is recorded beside it. Records are episodes (`tune.proposal`,
# `tune.decision`) so they are auditable, org-scoped and read by the brief like the ledger is.
# A rejection's reason becomes a build learning (`build/<hop>/learning`) that the next turn reads.


def _proposal_out(e, decision) -> TuneProposalOut:
    p = e.payload or {}
    d = (decision.payload or {}) if decision is not None else None
    return TuneProposalOut(
        id=e.event_id, at=e.occurred_at.isoformat() if e.occurred_at else "", actor=(e.actor or {}).get("user_id"),
        status=(d or {}).get("decision", "pending") if d else "pending", decision=d,
        **{k: p.get(k) for k in ("hop", "turn", "from_version", "candidate_version", "candidate_path", "summary", "verdict", "cost_usd")},
        proposals=list(p.get("proposals") or []), facts=list(p.get("facts") or []), twins=list(p.get("twins") or []),
    )


async def _proposals_and_decisions(session, org_id: str | None, hop: str | None):
    from ...db.models import Episode
    from ...pipeline.hop_names import hop_base

    q = select(Episode).where(Episode.org_id == org_id, Episode.action_type.in_(("tune.proposal", "tune.decision"))).order_by(Episode.occurred_at)
    rows = (await session.execute(q)).scalars().all()
    proposals, decisions = {}, {}
    for e in rows:
        p = e.payload or {}
        if hop and hop_base(p.get("hop")) != hop_base(hop):
            continue
        key = (hop_base(p.get("hop")), p.get("turn"))
        (proposals if e.action_type == "tune.proposal" else decisions)[key] = e
    return proposals, decisions


@router.post("/tune/proposals", response_model=TuneProposalOut, status_code=201)
async def write_tune_proposal(
    body: TuneProposalIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    identity: Annotated[AuthIdentity, Depends(require_permission("memory.write"))],
) -> TuneProposalOut:
    from ...ingest.envelope import Actor, EpisodeEnvelope
    from ...repositories.episodes import insert_episode

    env = EpisodeEnvelope(org_id=identity.org_id, scope="org", scope_id=identity.org_id, source="tune", action_type="tune.proposal",
                          actor=Actor(user_id=identity.user_id), payload=body.model_dump(), content=body.summary)
    # the ledger row, not a passage: captured content becomes searchable direct memory, and a tune
    # proposal's summary is not something an agent should retrieve as evidence (seen in the UI)
    await insert_episode(session, env.to_episode_kwargs(capture_content=False))
    await session.commit()
    from ...db.models import Episode

    e = await session.get(Episode, env.event_id)
    return _proposal_out(e, None)


@router.get("/tune/pending", response_model=TunePendingResponse)
async def pending_tune_proposals(
    session: Annotated[AsyncSession, Depends(get_session)],
    identity: Annotated[AuthIdentity, Depends(require_permission("memory.read"))],
    hop: str | None = Query(None),
) -> TunePendingResponse:
    proposals, decisions = await _proposals_and_decisions(session, identity.org_id, hop)
    return TunePendingResponse(pending=[_proposal_out(e, None) for k, e in proposals.items() if k not in decisions])


@router.get("/tune/proposals", response_model=TunePendingResponse)
async def all_tune_proposals(
    session: Annotated[AsyncSession, Depends(get_session)],
    identity: Annotated[AuthIdentity, Depends(require_permission("memory.read"))],
    hop: str | None = Query(None),
) -> TunePendingResponse:
    proposals, decisions = await _proposals_and_decisions(session, identity.org_id, hop)
    return TunePendingResponse(pending=[_proposal_out(e, decisions.get(k)) for k, e in proposals.items()])


@router.post("/tune/decide", response_model=TuneDecisionOut)
async def decide_tune_proposal(
    body: TuneDecisionIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    identity: Annotated[AuthIdentity, Depends(require_permission("memory.write"))],
) -> TuneDecisionOut:
    """The owner's decision. A person's identity is required (an agent never approves its own
    tuning). `reject` needs a reason, which is written as a build learning for the next turn."""
    from ...ingest.envelope import Actor, EpisodeEnvelope
    from ...pipeline.hop_names import hop_base
    from ...repositories.episodes import insert_episode

    if not identity.user_id:
        raise HTTPException(status_code=403, detail="a tune decision needs a person's identity")
    if body.decision not in ("approve", "reject", "edit"):
        raise HTTPException(status_code=422, detail="decision must be approve, reject or edit")
    if body.decision == "reject" and not body.reason.strip():
        raise HTTPException(status_code=422, detail="a rejection needs a reason (it becomes a build learning)")
    proposals, decisions = await _proposals_and_decisions(session, identity.org_id, body.hop)
    key = (hop_base(body.hop), body.turn)
    if key not in proposals:
        raise HTTPException(status_code=404, detail=f"no tune proposal for {body.hop} {body.turn}")
    if key in decisions:
        raise HTTPException(status_code=409, detail=f"{body.turn} already decided: {(decisions[key].payload or {}).get('decision')}")
    env = EpisodeEnvelope(org_id=identity.org_id, scope="org", scope_id=identity.org_id, source="tune", action_type="tune.decision",
                          actor=Actor(user_id=identity.user_id), payload=body.model_dump(), content=body.reason or body.decision)
    await insert_episode(session, env.to_episode_kwargs(capture_content=False))  # the reason reaches memory as the learning claim below
    learning = False
    if body.decision in ("reject", "edit") and body.reason.strip():
        base = hop_base(body.hop)
        text = f"Tune turn {body.turn} for {base} was {body.decision}ed by the owner: {body.reason.strip()}"
        claim = EpisodeEnvelope(org_id=identity.org_id, scope="org", scope_id=identity.org_id, workspace=base, source="harness_memory",
                                action_type="memory.claim", actor=Actor(user_id=identity.user_id), content=text,
                                payload={"kind": "learning", "topic": f"build/{base}/learning", "key": "dedup", "value": text, "phase": "build",
                                         "evidence_ids": [env.event_id], "hop": f"{base}@{proposals[key].payload.get('from_version', '')}", "origin": "tune-decision"})
        await insert_episode(session, claim.to_episode_kwargs(capture_content=True))
        # fold it now, as the documents door does, so the next brief already carries it
        from ...db.models import Episode
        from ..deps import get_updater

        ep = await session.get(Episode, claim.event_id)
        if ep is not None:
            await get_updater().process_episode(session, ep)
        learning = True
    await session.commit()
    return TuneDecisionOut(turn=body.turn, hop=body.hop, status=body.decision, learning_written=learning)
