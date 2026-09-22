"""`hop distill` (skill-pack verb, design §5) — run the telemetry distillation now.

Reads D11.2 run.completed episodes for one org (optionally one HOP), aggregates
them deterministically (pipeline/hop_distiller), and writes the resulting
watched facts through the same anchor/HAS_VALUE/supersession path every other
watched fact uses. Facts land at org scope, workspace ``hop:<name>`` — the
isolated per-HOP workspace from design §1.1, so proposals can focus retrieval
on exactly one HOP's history.

    python -m mlpal_memory_graph.tools.hop_distill --org local [--hop coding]
        [--window-days 30] [--wipe]

--wipe: recompute-not-accumulate for the touched hop workspaces (same rule the
value backfill learned the hard way).
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, or_, select

from ..core.scope import Scope, ScopeRef
from ..db import get_session_factory
from ..db.models import Episode
from ..graph import get_driver
from ..pipeline.hop_distiller import distill_deviations, distill_runs
from ..pipeline.hop_names import contract_at_least, hop_base


def apply_hop_alias(payload: dict, aliases: dict[str, str] | None) -> dict:
    """A variant's runs count toward its parent HOP: `infra-ro=infra` rewrites the payload's hop name
    and keeps the variant in `hop.variant`. Pure; the input dict is not mutated."""
    if not aliases:
        return payload
    name = (payload.get("hop") or {}).get("name")
    if name not in aliases:
        return payload
    return {**payload, "hop": {**payload["hop"], "name": aliases[name], "variant": name}}


async def _last_turn_at(session, org: str, hop: str | None):
    """The last scored tune turn for the HOP in `org` (the ledger `tune_turn` writes), or None."""
    rows = (await session.execute(
        select(Episode.occurred_at, Episode.payload)
        .where(Episode.org_id == org, Episode.source == "harness_telemetry", Episode.action_type == "hop.eval_scored")
        .order_by(Episode.occurred_at.desc())
    )).all()
    for occurred, payload in rows:
        if hop is None or hop_base(((payload or {}).get("hop") or {}).get("name")) == hop_base(hop):
            return occurred
    return None


async def _distill(
    org: str, hop: str | None, window_days: int, wipe: bool,
    read_orgs: list[str] | None = None, drop_earliest: int = 0,
    hop_aliases: dict[str, str] | None = None, since_last_turn: bool = False,
) -> None:
    """``org`` is where facts are WRITTEN; ``read_orgs`` (default [org]) are the
    tenants whose telemetry is aggregated — experiments keep arms in separate
    tenants, so cross-arm facts (routing needs >=2 tiers) read several and write
    one. ``drop_earliest`` excludes the N earliest events per source tenant
    (pilot-run exclusion, x12 A4) — filter, never purge."""
    factory = get_session_factory()
    driver = get_driver()
    since = datetime.now(UTC) - timedelta(days=window_days)
    sources = read_orgs or [org]
    async with factory() as session:
        if since_last_turn:
            # memory v10: the window a tune turn should read is everything since the last turn; the
            # day count is the fallback for a HOP that has never been tuned
            last = await _last_turn_at(session, org, hop)
            if last is not None:
                since = last if last.tzinfo else last.replace(tzinfo=UTC)
                print(f"window: since the last scored turn at {since.isoformat()}")
        payloads: list[dict] = []
        total_rows = 0
        for src in sources:
            rows = (
                (
                    await session.execute(
                        select(Episode)
                        .where(
                            Episode.org_id == src,
                            Episode.source == "harness_telemetry",
                            Episode.action_type == "run.completed",
                            Episode.occurred_at >= since,
                        )
                        .order_by(Episode.occurred_at)
                    )
                )
                .scalars()
                .all()
            )
            total_rows += len(rows)
            if drop_earliest:
                dropped = rows[:drop_earliest]
                rows = rows[drop_earliest:]
                for d in dropped:
                    print(f"  pilot-excluded {src}: {d.event_id} @ {d.occurred_at.isoformat()}")
            for r in rows:
                p = apply_hop_alias(r.payload, hop_aliases)
                if not contract_at_least(p.get("contract")):
                    continue
                if hop is None or hop_base((p.get("hop") or {}).get("name")) == hop_base(hop):
                    payloads.append(p)
        print(f"{total_rows} telemetry episodes across {sources}, {len(payloads)} d11.2+ in window "
              f"({window_days}d{f', hop={hop}' if hop else ''}) -> writing to {org}")
        ents, edges = distill_runs(payloads)
        # hop-v1.1 §9.2: deviation memories shipped by the harness (source harness_memory)
        dev_payloads: list[dict] = []
        for src in sources:
            dev_rows = (
                (
                    await session.execute(
                        select(Episode)
                        .where(
                            Episode.org_id == src,
                            Episode.occurred_at >= since,
                            or_(
                                and_(Episode.source == "harness_memory", Episode.action_type == "deviation"),
                                and_(Episode.action_type == "memory.claim", Episode.payload["kind"].as_string() == "deviation"),
                            ),
                        )
                        .order_by(Episode.occurred_at)
                    )
                )
                .scalars()
                .all()
            )
            dev_payloads.extend(
                r.payload for r in dev_rows
                if hop is None or hop_base(str(r.payload.get("hop") or "").split("@", 1)[0]) == hop_base(hop)
            )
        if dev_payloads:
            d_ents, d_edges = distill_deviations(dev_payloads)
            print(f"{len(dev_payloads)} deviation memories in window -> {len(d_edges)} deviation fact(s)")
            ents += d_ents
            edges += d_edges
        if not edges:
            print("nothing cleared its floor — no facts written (silence, not weak claims)")
            return
        hops_touched = sorted({e.key.split("|", 1)[0].removeprefix("hop:")
                               for e in ents if e.type == "Metric"})
        if wipe:
            from sqlalchemy import delete as _del

            from ..db.models import Edge as _E
            from ..db.models import Node as _N

            for h in hops_touched:
                targets = select(_N.id).where(
                    _N.org_id == org,
                    _N.type.in_(("Metric", "MetricValue")),
                    _N.workspace == f"hop:{h}",
                )
                await session.execute(
                    _del(_E).where(_E.src_id.in_(targets) | _E.dst_id.in_(targets))
                )
                await session.execute(_del(_N).where(_N.id.in_(targets.scalar_subquery())))
            print(f"wiped watched facts for hop workspaces: {hops_touched}")
        scope = ScopeRef(Scope.ORG, org)  # org scope is keyed by the org id
        node_map = {}
        for ent in ents:
            node = await driver.upsert_node(
                session, tenant_id=org, scope=scope, type_=ent.type,
                key=ent.key, name=ent.name, props=ent.props or None,
            )
            h = ent.key.split("|", 1)[0].removeprefix("hop:")
            node.workspace = node.workspace or f"hop:{h}"
            node_map[(ent.type, ent.key)] = node
        made = 0
        for e in edges:
            edge = await driver.upsert_edge(
                session, tenant_id=org, scope=scope, type_=e.type,
                src_id=node_map[(e.src_type, e.src_key)].id,
                dst_id=node_map[(e.dst_type, e.dst_key)].id,
                fact=e.fact, props=e.props,
            )
            await driver.invalidate_superseded(
                session, tenant_id=org, scope=scope, new_edge=edge
            )
            made += 1
            print(f"  {e.fact}")
        await session.commit()
        print(f"distilled {made} watched facts across {len(hops_touched)} hop(s)")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--org", required=True)
    ap.add_argument("--hop", default=None)
    ap.add_argument("--hop-alias", action="append", default=[], metavar="VARIANT=HOP",
                    help="count a variant's runs toward a HOP (e.g. infra-ro=infra): the read-only twin of an artifact is the same HOP for tuning")
    ap.add_argument("--window-days", type=int, default=30)
    ap.add_argument("--since-last-turn", action="store_true",
                    help="read everything since the last scored tune turn in --org (the day window is the fallback)")
    ap.add_argument("--wipe", action="store_true")
    ap.add_argument("--orgs", default=None,
                    help="comma-separated source tenants to read (default: --org)")
    ap.add_argument("--drop-earliest", type=int, default=0,
                    help="exclude the N earliest events per source tenant (pilot exclusion)")
    args = ap.parse_args()
    asyncio.run(_distill(
        args.org, args.hop, args.window_days, args.wipe,
        read_orgs=args.orgs.split(",") if args.orgs else None,
        drop_earliest=args.drop_earliest,
        hop_aliases=dict(a.split("=", 1) for a in args.hop_alias) if args.hop_alias else None,
        since_last_turn=args.since_last_turn,
    ))
    return 0


if __name__ == "__main__":
    sys.exit(main())
