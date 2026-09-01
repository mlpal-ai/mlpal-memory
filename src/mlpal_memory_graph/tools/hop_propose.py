"""`hop propose` (skill-pack verb, design §5) — emit citation-enforced HOP diffs.

Reads the CURRENT distilled facts for one HOP (MetricValue nodes in workspace
``hop:<name>`` whose live HAS_VALUE edge is open), runs the deterministic
proposer, VERIFIES every citation resolves to a node that was actually read
(the x5 rule, applied to the optimizer itself — a proposal citing nothing
retrievable is dropped and counted), and prints the diffs as YAML. Nothing is
promoted here: the YAML is the proposal record (MVP: it becomes a PR against
the HOP artifact repo; the merge is the human gate).

    python -m mlpal_memory_graph.tools.hop_propose --org local --hop coding
        [--tier-order cheap,balanced,frontier] [--out proposals.yaml]
"""

from __future__ import annotations

import argparse
import asyncio
import sys

import yaml
from sqlalchemy import select

from ..db import get_session_factory
from ..db.models import Edge, Node
from ..pipeline.hop_proposer import DEFAULT_TIER_ORDER, Fact, propose


async def _current_facts(session, org: str, hop: str) -> list[Fact]:
    rows = (
        await session.execute(
            select(Node)
            .join(Edge, Edge.dst_id == Node.id)
            .where(
                Node.org_id == org,
                Node.type == "MetricValue",
                Node.workspace == f"hop:{hop}",
                Edge.type == "HAS_VALUE",
                Edge.invalid_at.is_(None),
            )
        )
    ).scalars().all()
    facts = []
    for n in rows:
        anchor_key, _, value = n.key.partition("=")
        facts.append(Fact(node_id=n.id, key=anchor_key, value=value))
    return facts


async def _run(org: str, hop: str, tier_order: tuple[str, ...], out: str | None) -> None:
    async with get_session_factory()() as session:
        facts = await _current_facts(session, org, hop)
        print(f"{len(facts)} current distilled facts for hop={hop}")
        proposals = propose(facts, tier_order=tier_order)
        # citation enforcement: every evidence id must be one we actually read
        readable = {f.citation for f in facts}
        kept, dropped = [], 0
        for p in proposals:
            if all(c in readable for c in p.evidence):
                kept.append(p)
            else:
                dropped += 1
        doc = {
            "hop": hop, "org": org, "proposals": [p.as_dict() for p in kept],
            "dropped_unresolvable_citations": dropped,
            "note": "memory proposes, evals dispose — nothing here is promoted; "
                    "run `hop eval` on each candidate before any merge",
        }
        text = yaml.safe_dump(doc, sort_keys=False, width=100)
        if out:
            with open(out, "w") as fh:
                fh.write(text)
            print(f"{len(kept)} proposals → {out} ({dropped} dropped for unresolvable citations)")
        else:
            print(text)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--org", required=True)
    ap.add_argument("--hop", required=True)
    ap.add_argument("--tier-order", default=",".join(DEFAULT_TIER_ORDER),
                    help="cheapest first")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    asyncio.run(_run(args.org, args.hop, tuple(args.tier_order.split(",")), args.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
