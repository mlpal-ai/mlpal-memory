"""GC evidence report — measure junk before deciding what to forget.

Reports the served-usage distribution across the store (migration 0014
counters): how much memory has EVER been served, age × usage quadrants, and
archive-candidate counts under a conservative policy proposal. READ-ONLY:
no deletion happens here — the founder's directive is measurement first,
and the numbers this prints are the input to that policy decision.

    python -m mlpal_memory_graph.tools.gc_report [--json]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from ..db import get_session_factory
from ..db.models import Chunk, Document, Node


async def _report() -> dict:
    factory = get_session_factory()
    out: dict = {"at": datetime.now(UTC).isoformat(timespec="seconds")}
    async with factory() as session:
        for name, model in (("chunks", Chunk), ("nodes", Node)):
            total = (await session.execute(select(func.count()).select_from(model))).scalar_one()
            served = (
                await session.execute(
                    select(func.count()).select_from(model).where(model.served_count > 0)
                )
            ).scalar_one()
            hot = (
                await session.execute(
                    select(func.count()).select_from(model).where(model.served_count >= 3)
                )
            ).scalar_one()
            age_col = model.ingested_at if name == "chunks" else model.created_at
            old_never = (
                await session.execute(
                    select(func.count())
                    .select_from(model)
                    .where(
                        model.served_count == 0,
                        age_col < datetime.now(UTC) - timedelta(days=30),
                    )
                )
            ).scalar_one()
            out[name] = {
                "total": total,
                "ever_served": served,
                "served_rate": round(served / total, 3) if total else 0.0,
                "hot_3plus": hot,
                "archive_candidates_30d_never_served": old_never,
            }
        # documents whose EVERY chunk is unserved — whole-doc archive candidates
        sub = (
            select(Chunk.document_id)
            .group_by(Chunk.document_id)
            .having(func.max(Chunk.served_count) == 0)
        )
        whole_docs = (
            await session.execute(
                select(func.count()).select_from(Document).where(Document.id.in_(sub))
            )
        ).scalar_one()
        out["documents_fully_unserved"] = whole_docs
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    out = asyncio.run(_report())
    if args.json:
        print(json.dumps(out, indent=1))
        return 0
    for name in ("chunks", "nodes"):
        s = out[name]
        print(f"{name}: {s['ever_served']}/{s['total']} ever served "
              f"({s['served_rate']:.0%}) · hot(3+) {s['hot_3plus']} · "
              f"archive candidates (30d+, never served) "
              f"{s['archive_candidates_30d_never_served']}")
    print(f"documents fully unserved: {out['documents_fully_unserved']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
