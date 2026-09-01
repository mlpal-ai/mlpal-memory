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

from sqlalchemy import select

from ..core.scope import Scope, ScopeRef
from ..db import get_session_factory
from ..db.models import Episode
from ..graph import get_driver
from ..pipeline.hop_distiller import distill_runs


async def _distill(org: str, hop: str | None, window_days: int, wipe: bool) -> None:
    factory = get_session_factory()
    driver = get_driver()
    since = datetime.now(UTC) - timedelta(days=window_days)
    async with factory() as session:
        rows = (
            (
                await session.execute(
                    select(Episode)
                    .where(
                        Episode.org_id == org,
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
        payloads = [
            r.payload for r in rows
            if r.payload.get("contract") == "d11.2"
            and (hop is None or (r.payload.get("hop") or {}).get("name") == hop)
        ]
        print(f"{len(rows)} telemetry episodes, {len(payloads)} d11.2 in window "
              f"({window_days}d{f', hop={hop}' if hop else ''})")
        ents, edges = distill_runs(payloads)
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
        scope = ScopeRef(Scope.ORG, None)
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
    ap.add_argument("--window-days", type=int, default=30)
    ap.add_argument("--wipe", action="store_true")
    args = ap.parse_args()
    asyncio.run(_distill(args.org, args.hop, args.window_days, args.wipe))
    return 0


if __name__ == "__main__":
    sys.exit(main())
