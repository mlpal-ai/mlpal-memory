"""Automatic roll-up by unit policy (memory v12 §2b): a unit whose policy says
``lift: {tier: endorsed|corroborated, to: parent}`` has its learnings at or above that trust tier
copied one level up — to the parent unit, or to the org when the unit is top-level. Same
mechanics as a person's publish (dedup on identical knowledge, distinct key when contending),
one difference: the copy says it was lifted by policy from which unit, and every lift is a
ledger row. Nothing rolls past a unit whose policy has no `lift`; nothing rolls up silently.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select

from ..core.logging import get_logger
from ..core.scope import Scope, ScopeRef
from ..db.models import Node
from ..graph import get_driver
from ..ingest.envelope import Actor, EpisodeEnvelope
from ..repositories.episodes import insert_episode
from . import units as units_svc
from .pii import pii_in_node

log = get_logger(__name__)

TIER_RANK = {"probation": 0, "corroborated": 1, "endorsed": 2}
LIFTED_BY = "unit-policy"


@dataclass
class LiftResult:
    unit_id: str
    target: str  # "team:<parent>" or "org:<org>"
    lifted: int
    merged: int
    skipped_pii: int


def _tier(node: Node) -> str:
    trust = (node.props or {}).get("trust") if isinstance(node.props, dict) else None
    return str((trust or {}).get("tier") or "probation")


async def lift_unit(session, *, org_id: str, unit, target: ScopeRef, min_tier: str) -> LiftResult:
    driver = get_driver()
    src = ScopeRef(Scope.TEAM, unit.id)
    rows = (await session.execute(
        select(Node).where(Node.org_id == org_id, Node.scope == src.scope.value, Node.scope_id == src.scope_id,
                           Node.type == "Fact", Node.status.in_(("committed", "published")))
    )).scalars().all()
    lifted = merged = skipped = 0
    for node in rows:
        if TIER_RANK.get(_tier(node), 0) < TIER_RANK[min_tier]:
            continue
        if (node.props or {}).get("lifted_from") == unit.id:
            continue  # a copy that arrived here by lift is not re-lifted from here
        if pii_in_node(node.name, node.summary, node.props):
            skipped += 1
            continue
        existing = await driver.find_node(session, org_id, target, node.type, node.key)
        if existing is not None and existing.name == node.name and (existing.summary or "") == (node.summary or ""):
            if unit.id not in (existing.props or {}).get("lifted_from_units", []):
                existing.props = {**(existing.props or {}), "lifted_from_units": [*(existing.props or {}).get("lifted_from_units", []), unit.id]}
                existing.observed_count = (existing.observed_count or 1) + 1
                merged += 1
            continue
        key = node.key if existing is None else f"{node.key}~{unit.id}"
        copy = await driver.upsert_node(
            session, tenant_id=org_id, scope=target, type_=node.type, key=key, name=node.name, summary=node.summary,
            props={**(node.props or {}), "published_by": LIFTED_BY, "lifted_from": unit.id, "lifted_tier": _tier(node)},
            embedding=node.embedding, embedding_model=node.embedding_model, embedding_dim=node.embedding_dim, source=node.source,
        )
        copy.status = "published"
        copy.workspace = node.workspace
        copy.derived_from = list(node.derived_from or [])
        lifted += 1
        env = EpisodeEnvelope(org_id=org_id, scope=target.scope.value, scope_id=target.scope_id, source="units",
                              action_type="memory.lifted", actor=Actor(user_id=LIFTED_BY),
                              payload={"node_id": copy.id, "from_node_id": node.id, "from_unit": unit.id, "unit_name": unit.name,
                                       "tier": _tier(node), "min_tier": min_tier})
        await insert_episode(session, env.to_episode_kwargs(capture_content=False))
    return LiftResult(unit_id=unit.id, target=f"{target.scope.value}:{target.scope_id}", lifted=lifted, merged=merged, skipped_pii=skipped)


async def lift_by_policy(session, *, org_id: str) -> list[LiftResult]:
    """Every unit of the tenant with a `lift` policy, deepest first so a fact can climb several
    levels across successive nights, never more than one level per run."""
    by_id = await units_svc.load_tree(session, org_id)
    out: list[LiftResult] = []
    for unit in sorted(by_id.values(), key=lambda u: -units_svc.depth(by_id, u.id)):
        lift = (unit.policy or {}).get("lift") if isinstance(unit.policy, dict) else None
        if not isinstance(lift, dict):
            continue
        min_tier = str(lift.get("tier") or "endorsed")
        if min_tier not in TIER_RANK:
            log.error("units.lift_policy_invalid", unit=unit.id, tier=min_tier)
            continue
        target = ScopeRef(Scope.TEAM, unit.parent_id) if unit.parent_id else ScopeRef(Scope.ORG, org_id)
        res = await lift_unit(session, org_id=org_id, unit=unit, target=target, min_tier=min_tier)
        if res.lifted or res.merged or res.skipped_pii:
            log.info("units.lifted", org=org_id, unit=unit.name, target=res.target, lifted=res.lifted, merged=res.merged, skipped_pii=res.skipped_pii)
        out.append(res)
    return out
