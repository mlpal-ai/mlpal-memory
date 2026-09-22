"""hop_trust — trust by consequence, computed over a window and written to node.props["trust"].

    uv run python -m mlpal_memory_graph.tools.hop_trust --org <org> [--window-days 30] [--hop-alias infra-ro=infra]

Joins three ledgers, all content-free: `memory.served` episodes (run_id → node ids a read returned),
`run.completed` telemetry (run_id → verdict; d11.5 adds `memories_injected`, local topic event ids
that map to nodes through `derived_from`), and the nodes' own observed_count / endorsed_by. Writes
the tier; never deletes anything. Idempotent: rerunning recomputes.
"""

from __future__ import annotations

import argparse
import asyncio
from collections import defaultdict
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from ..db import get_session_factory
from ..db.models import Episode, Node
from ..core.halflife import half_life_days_for
from ..pipeline.trust import join_consequences, trust_record


def _last_observed(n):
    """The last time the world showed this memory: the fold's stamp, else the row's ingest time.
    Never updated_at — the trust join itself rewrites props and would reset survival."""
    from datetime import UTC, datetime

    raw = (n.props or {}).get("last_observed_at") if isinstance(n.props, dict) else None
    if raw:
        try:
            dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
        except ValueError:
            pass
    return getattr(n, "ingested_at", None) or getattr(n, "created_at", None)


async def compute(org: str, window_days: int) -> dict:
    factory = get_session_factory()
    since = datetime.now(UTC) - timedelta(days=window_days)
    async with factory() as session:
        eps = (await session.execute(select(Episode).where(Episode.org_id == org, Episode.occurred_at >= since))).scalars().all()
        served: dict[str, set[str]] = defaultdict(set)
        verdicts: dict[str, str] = {}
        injected_by_run: dict[str, list[str]] = {}
        for e in eps:
            p = e.payload or {}
            if e.action_type == "memory.served" and p.get("run_id"):
                served[p["run_id"]].update(p.get("node_ids") or [])
            elif e.action_type == "run.completed" and p.get("run_id") and p.get("run_result"):
                verdicts[p["run_id"]] = p["run_result"]
                if p.get("memories_injected"):
                    injected_by_run[p["run_id"]] = list(p["memories_injected"])
        nodes = (await session.execute(select(Node).where(Node.org_id == org))).scalars().all()
        by_episode: dict[str, set[str]] = defaultdict(set)     # claim episode id → nodes derived from it
        for n in nodes:
            for ev in (n.derived_from or []):
                by_episode[str(ev)].add(n.id)
        for run_id, ev_ids in injected_by_run.items():
            for ev in ev_ids:
                served[run_id].update(by_episode.get(ev, ()))
        cons = join_consequences(served, verdicts)
        tiers: dict[str, int] = defaultdict(int)
        touched = 0
        for n in nodes:
            if n.type not in ("Fact", "MetricValue", "Metric"):
                continue
            props = dict(n.props or {})
            rec = trust_record(grounded=props.get("grounded"), observed_count=n.observed_count or 1,
                               consequence=cons.get(n.id), endorsed=bool(props.get("endorsed_by")),
                               last_seen=_last_observed(n), half_life_days=half_life_days_for(props))
            prev = dict(props.get("trust") or {}); prev.pop("computed_at", None)
            cur = dict(rec); cur.pop("computed_at", None)
            if prev != cur:
                props["trust"] = rec
                n.props = props
                touched += 1
            tiers[rec["tier"]] += 1
        await session.commit()
    return {"org": org, "window_days": window_days, "runs_with_verdict": len(verdicts), "served_runs": len(served),
            "nodes_scored": sum(tiers.values()), "updated": touched, "tiers": dict(tiers)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--org", required=True)
    ap.add_argument("--window-days", type=int, default=30)
    a = ap.parse_args()
    print(asyncio.run(compute(a.org, a.window_days)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
