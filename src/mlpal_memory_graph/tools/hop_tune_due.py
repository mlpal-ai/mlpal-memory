"""`hop tune-due` — is a tuning turn due for a HOP, by the artifact's cadence rule?

    python -m mlpal_memory_graph.tools.hop_tune_due --org hop-infra-eval --hop infra
        [--orgs hop-infra-eval,hop-infra-real] [--min-runs 30] [--max-age 4w]
        [--event on-model-release ...] [--json]

Counts main-role `run.completed` episodes for the HOP since the last `hop.eval_scored`
episode (the ledger a tune turn writes), measures the age of that turn, and adds the
event triggers the caller observed. Exit 0 = due, 3 = not due (so a script can branch).
The decision is in pipeline/hop_cadence; this file is the queries around it.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime

from sqlalchemy import select

from ..db import get_session_factory
from ..db.models import Episode
from ..pipeline.hop_cadence import CadencePolicy, CadenceVerdict, decide_due, parse_max_age
from ..pipeline.hop_names import hop_base


async def _query(org: str, hop: str, read_orgs: list[str]) -> tuple[int, datetime | None]:
    factory = get_session_factory()
    async with factory() as session:
        last_turn: datetime | None = None
        rows = (
            await session.execute(
                select(Episode.occurred_at, Episode.payload)
                .where(
                    Episode.org_id == org,
                    Episode.source == "harness_telemetry",
                    Episode.action_type == "hop.eval_scored",
                )
                .order_by(Episode.occurred_at.desc())
            )
        ).all()
        for occurred, payload in rows:
            if hop_base(((payload or {}).get("hop") or {}).get("name")) == hop_base(hop):
                last_turn = occurred
                break
        runs = 0
        for src in read_orgs:
            q = select(Episode.payload).where(
                Episode.org_id == src,
                Episode.source == "harness_telemetry",
                Episode.action_type == "run.completed",
            )
            if last_turn is not None:
                q = q.where(Episode.occurred_at > last_turn)
            for (payload,) in (await session.execute(q)).all():
                p = payload or {}
                if hop_base((p.get("hop") or {}).get("name")) == hop_base(hop) and p.get("role") == "main":
                    runs += 1  # memory v10: the read-only twin's runs count toward its parent
        return runs, last_turn


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--org", required=True, help="tenant whose ledger holds the scored turns")
    ap.add_argument("--hop", required=True)
    ap.add_argument("--orgs", default=None, help="comma-separated tenants whose runs count (default: --org)")
    ap.add_argument("--min-runs", type=int, default=30)
    ap.add_argument("--max-age", default="4w")
    ap.add_argument("--event", action="append", default=[], help="observed trigger, e.g. on-model-release")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    read_orgs = a.orgs.split(",") if a.orgs else [a.org]
    runs, last_turn = asyncio.run(_query(a.org, a.hop, read_orgs))
    policy = CadencePolicy(min_runs_since_last=a.min_runs, max_age_days=parse_max_age(a.max_age))
    v: CadenceVerdict = decide_due(policy, runs_since_last=runs, last_turn_at=last_turn,
                                   now=datetime.now(UTC), events=tuple(a.event))
    out = {"hop": a.hop, "due": v.due, "reasons": v.reasons, "runs_since_last": v.runs_since_last,
           "days_since_last": v.days_since_last,
           "last_turn_at": v.last_turn_at.isoformat() if v.last_turn_at else None,
           "policy": {"min_runs_since_last": policy.min_runs_since_last, "max_age_days": policy.max_age_days}}
    if a.json:
        print(json.dumps(out))
    else:
        print(("DUE: " if v.due else "NOT DUE: ") + (("; ".join(v.reasons)) if v.reasons else
              f"{runs} main runs since the last turn (floor {policy.min_runs_since_last}), "
              f"last turn {out['last_turn_at'] or 'never'}"))
    return 0 if v.due else 3


if __name__ == "__main__":
    sys.exit(main())
