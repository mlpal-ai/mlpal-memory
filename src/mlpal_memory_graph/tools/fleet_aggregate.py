"""Fleet aggregates (memory v7 WP9, design: "every company running the same HOP, seen only as
de-identified aggregates at MLPal"): over every tenant's content-free run telemetry, per HOP name,
how often each failure class and deviation kind recurs and which model tiers run it. Written to the
GLOBAL scope as Fact nodes (platform-write-only, readable by every tenant's builder brief). Off when
the deployment is self-hosted (MLPAL_SELF_HOSTED=true): a self-hosted company has no fleet.

    uv run python -m mlpal_memory_graph.tools.fleet_aggregate [--window-days 30] [--min-tenants 2]
"""

from __future__ import annotations

import argparse
import asyncio
import os
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from ..core.scope import Scope, ScopeRef
from ..db import get_session_factory
from ..db.models import Episode
from ..graph import get_driver


async def compute(window_days: int, min_tenants: int) -> dict:
    if os.environ.get("MLPAL_SELF_HOSTED", "").lower() in ("1", "true", "yes"):
        return {"skipped": "self-hosted deployment: no fleet tier"}
    factory = get_session_factory()
    since = datetime.now(UTC) - timedelta(days=window_days)
    async with factory() as session:
        eps = (await session.execute(select(Episode).where(Episode.action_type == "run.completed", Episode.occurred_at >= since))).scalars().all()
        per_hop: dict[str, dict] = defaultdict(lambda: {"tenants": set(), "runs": 0, "failures": Counter(), "tiers": Counter()})
        for e in eps:
            p = e.payload or {}
            hop = ((p.get("hop") or {}).get("name") or "").split("-")[0]
            if not hop:
                continue
            agg = per_hop[hop]
            agg["tenants"].add(e.org_id); agg["runs"] += 1
            if p.get("failure_class"):
                agg["failures"][p["failure_class"]] += 1
            if p.get("tier"):
                agg["tiers"][p["tier"]] += 1
        written = []
        driver = get_driver()
        for hop, agg in per_hop.items():
            if len(agg["tenants"]) < min_tenants:
                continue   # below the k-anonymity floor: an aggregate over one company is that company
            n = agg["runs"]
            parts = [f"fleet {hop}: {len(agg['tenants'])} companies, {n} runs in {window_days} d"]
            if agg["failures"]:
                parts.append("failure classes per 100 runs: " + ", ".join(f"{k} {100*v/n:.1f}" for k, v in agg["failures"].most_common(5)))
            if agg["tiers"]:
                parts.append("tiers: " + ", ".join(f"{k} {100*v/n:.0f}%" for k, v in agg["tiers"].most_common(3)))
            statement = "; ".join(parts)
            node = await driver.upsert_node(session, tenant_id=None, scope=ScopeRef(Scope.GLOBAL, None), type_="Fact",
                                            key=f"fleet:{hop}:{window_days}d", name=statement, summary=None,
                                            props={"statement": statement, "hops": [hop], "companies": len(agg["tenants"]), "runs": n,
                                                   "computed_at": datetime.now(UTC).isoformat(), "fleet": True},
                                            embedding=None, embedding_model=None, embedding_dim=None, source="fleet")
            written.append({"hop": hop, "companies": len(agg["tenants"]), "runs": n, "node": node.id})
        await session.commit()
    return {"window_days": window_days, "written": written}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--window-days", type=int, default=30)
    ap.add_argument("--min-tenants", type=int, default=2)
    a = ap.parse_args()
    print(asyncio.run(compute(a.window_days, a.min_tenants)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
