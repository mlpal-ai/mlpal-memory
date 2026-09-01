"""`hop eval` / `hop promote` (skill-pack verbs, design §5).

    python -m mlpal_memory_graph.tools.hop_eval eval <hop.yaml> [--baseline-frontier N]
        [--out verdict.json] [--ledger --org ORG]
    python -m mlpal_memory_graph.tools.hop_eval promote <hop.yaml> <verdict.json>

`eval` runs the probe → golden → frontier ladder and writes a machine-readable
verdict; with --ledger it also records a `hop.eval_scored` episode in memory so
rejected candidates are remembered (design §1.4: rejected proposals are memory
too). `promote` applies the promotion gate — including the tuner-owned
apply-capable check the engine loader cannot make — and prints the decision.
Neither verb mutates a HOP artifact: the merge is the promotion.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

import yaml

from ..pipeline.hop_eval import run_ladder
from ..pipeline.hop_promote import decide_promotion


async def _ledger(org: str, verdict: dict) -> None:
    from ..repositories.episodes import insert_episode
    from ..db import get_session_factory
    from ..ingest.envelope import Actor, EpisodeEnvelope

    fm = verdict.get("frontier") or {}
    env = EpisodeEnvelope(
        org_id=org, scope="org", actor=Actor(user_id="hop-optimizer"),
        source="harness_telemetry", action_type="hop.eval_scored",
        payload={
            "hop": {"name": verdict["hop"]},
            "to_version": verdict["version"],
            "eval": {
                "suite_digest": verdict["suite_digest"],
                "score": float(fm.get("score") or 0.0),
                "pass_bar": 0.0,
                "runs": int(fm.get("runs") or 0),
                "eval_run_id": verdict["suite_digest"][:16],
            },
            "decision": "adopted" if verdict["disposition"] == "accepted"
            else ("rejected" if verdict["disposition"] == "rejected" else None),
            "proposed_by": "hop-optimizer",
        },
        content=None,
    )
    env.payload = {k: v for k, v in env.payload.items() if v is not None}
    async with get_session_factory()() as session:
        await insert_episode(session, env.to_episode_kwargs(capture_content=False))
        await session.commit()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="verb", required=True)
    e = sub.add_parser("eval")
    e.add_argument("hop")
    e.add_argument("--baseline-frontier", type=float, default=None)
    e.add_argument("--out", default=None)
    e.add_argument("--ledger", action="store_true")
    e.add_argument("--org", default=None)
    p = sub.add_parser("promote")
    p.add_argument("hop")
    p.add_argument("verdict")
    args = ap.parse_args()

    if args.verb == "eval":
        v = run_ladder(Path(args.hop), baseline_frontier=args.baseline_frontier).as_dict()
        text = json.dumps(v, indent=1)
        if args.out:
            Path(args.out).write_text(text)
        print(text if not args.out else f"{v['disposition']}: {v['reason']} → {args.out}")
        if args.ledger:
            if not args.org:
                sys.exit("--ledger requires --org")
            asyncio.run(_ledger(args.org, v))
            print("ledger episode recorded (hop.eval_scored)")
        return 0 if v["disposition"] != "rejected" else 1

    hop = yaml.safe_load(Path(args.hop).read_text())
    verdict = json.loads(Path(args.verdict).read_text())
    d = decide_promotion(hop, verdict)
    print(json.dumps(d.as_dict(), indent=1))
    return 0 if d.allowed else 1


if __name__ == "__main__":
    sys.exit(main())
