"""Re-embed the store into the configured embedding space.

Rewrites chunk, node, and edge embeddings whose stamped ``embedding_model``
differs from the active embedder (D2: spaces never mix — every row carries its
space name, and this is the migration that moves rows between spaces). Safe to
re-run; rows already in the target space are skipped.

Run inside the deployed container (or any env with DB access):

    python -m mlpal_memory_graph.tools.reembed [--batch 64] [--dry-run]
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from sqlalchemy import func, or_, select

from ..core.text import normalize_fact
from ..db import get_session_factory
from ..db.models.document import Chunk
from ..db.models.edge import Edge
from ..db.models.node import Node
from ..services.embeddings_client import get_embedder


async def _reembed(batch: int, dry_run: bool) -> int:
    embedder = get_embedder()
    factory = get_session_factory()
    total = 0
    # (model, embedding attribute, text-of-row) per embedded surface
    surfaces = [
        (Chunk, "embedding", lambda r: r.content),
        (Node, "embedding", lambda r: r.name),
        (Edge, "fact_embedding", lambda r: normalize_fact(r.fact)),
    ]
    for model, embed_attr, text_of in surfaces:
        stale = or_(model.embedding_model.is_(None), model.embedding_model != embedder.name)
        # nodes/edges only re-embed rows that were embedded before (Fact nodes, fact edges)
        if model is not Chunk:
            stale = stale & getattr(model, embed_attr).isnot(None)
        async with factory() as session:
            count = (
                await session.execute(select(func.count()).select_from(model).where(stale))
            ).scalar_one()
        print(f"{model.__tablename__}: {count} rows to re-embed -> space {embedder.name!r}")
        if dry_run:
            continue
        done = 0
        while True:
            async with factory() as session:
                rows = (
                    (await session.execute(select(model).where(stale).limit(batch)))
                    .scalars()
                    .all()
                )
                if not rows:
                    break
                texts = [text_of(r) or "" for r in rows]
                vectors = await embedder.embed(texts)
                for row, vec, text in zip(rows, vectors, texts, strict=True):
                    setattr(row, embed_attr, vec if text else None)
                    row.embedding_model = embedder.name
                    row.embedding_dim = embedder.dim
                await session.commit()
            done += len(rows)
            total += len(rows)
            print(f"  {model.__tablename__}: {done}/{count}", flush=True)
    return total


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--batch", type=int, default=64)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    total = asyncio.run(_reembed(args.batch, args.dry_run))
    print(f"re-embedded {total} rows")
    return 0


if __name__ == "__main__":
    sys.exit(main())
