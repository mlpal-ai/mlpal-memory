"""Backfill watched-value facts over documents folded BEFORE the mechanism existed.

Re-runs value extraction (LLM tier when configured, pattern tier otherwise) over
existing documents in a workspace, in valid-time order, writing through the same
anchor/HAS_VALUE/supersession path the fold uses. Idempotent: re-observation of
an already-current value bumps nothing destructive; supersession is valid-time
aware either way.

    python -m mlpal_memory_graph.tools.backfill_values --org local \
        --user alice --workspace aws-migration
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from sqlalchemy import select

from ..core.config import get_settings
from ..core.scope import Scope, ScopeRef
from ..db import get_session_factory
from ..db.models import Chunk, Document
from ..graph import get_driver
from ..pipeline.value_facts import (
    extract_value_specs,
    llm_extract_state_specs,
    llm_extract_value_specs,
)


async def _backfill(org: str, user: str, workspace: str, wipe: bool = False) -> None:
    factory = get_session_factory()
    driver = get_driver()
    use_llm = get_settings().value_extractor == "llm"
    async with factory() as session:
        if wipe:
            # Recompute semantics: re-running extraction must not ACCUMULATE — six
            # stacked runs left 87 daily-cost edges with five conflicting "current"
            # values open at once (x11 trace, 2026-09-01). Watched facts are fully
            # derivable from the documents, so wipe-and-rebuild is lossless.
            from sqlalchemy import delete as _del

            from ..db.models import Edge as _E2
            from ..db.models import Node as _N2

            targets = select(_N2.id).where(
                _N2.org_id == org,
                _N2.type.in_(("Metric", "MetricValue")),
                _N2.workspace == workspace,
            )
            await session.execute(
                _del(_E2).where(_E2.src_id.in_(targets) | _E2.dst_id.in_(targets))
            )
            n = (await session.execute(
                _del(_N2).where(_N2.id.in_(targets.scalar_subquery()))
            )).rowcount
            print(f"wiped {n} watched-fact nodes (org={org}, ws={workspace})")
        docs = (
            (
                await session.execute(
                    select(Document)
                    .where(Document.org_id == org, Document.workspace == workspace)
                    .order_by(Document.valid_at.asc().nulls_last())
                )
            )
            .scalars()
            .all()
        )
        print(f"{len(docs)} documents in {org}/{workspace} (valid-time order)")
        # sticky state subjects (x11): seed from existing anchors, grow as written —
        # chronological processing + a stable name list is what keeps one subject
        # on one key so supersession can actually fire
        from ..db.models import Node

        known: list[str] = [
            n.removesuffix(" status")
            for n in (
                await session.execute(
                    select(Node.name).where(
                        Node.org_id == org, Node.type == "Metric", Node.key.like("state:%")
                    )
                )
            ).scalars()
        ]
        made = 0
        for doc in docs:
            chunks = (
                (
                    await session.execute(
                        select(Chunk.content)
                        .where(Chunk.document_id == doc.id)
                        .order_by(Chunk.ordinal)
                    )
                )
                .scalars()
                .all()
            )
            content = "\n".join(chunks)
            if use_llm:
                ents, edges = await llm_extract_value_specs(content)
                s_ents, s_edges = await llm_extract_state_specs(content, known_subjects=known)
                for e in s_ents:
                    if e.type == "Metric":
                        name = e.name.removesuffix(" status")
                        if name not in known:
                            known.append(name)
                ents, edges = ents + s_ents, edges + s_edges
            else:
                ents, edges = extract_value_specs(content)
            if not edges:
                continue
            scope = ScopeRef(Scope(doc.scope), doc.scope_id)
            node_map = {}
            for ent in ents:
                node = await driver.upsert_node(
                    session, tenant_id=org, scope=scope, type_=ent.type,
                    key=ent.key, name=ent.name, props=ent.props or None,
                )
                node.workspace = node.workspace or workspace
                node_map[(ent.type, ent.key)] = node
            for e in edges:
                src = node_map[(e.src_type, e.src_key)]
                dst = node_map[(e.dst_type, e.dst_key)]
                edge = await driver.upsert_edge(
                    session, tenant_id=org, scope=scope, type_=e.type,
                    src_id=src.id, dst_id=dst.id, fact=e.fact, props=e.props,
                    valid_at=doc.valid_at,
                )
                await driver.invalidate_superseded(
                    session, tenant_id=org, scope=scope, new_edge=edge
                )
                made += 1
                print(f"  {doc.valid_at.date() if doc.valid_at else '?'} {e.fact}")
        # superseded-status hygiene (same rule as the fold)
        from sqlalchemy import update as _upd

        from ..db.models import Edge as _E
        from ..db.models import Node as _N

        open_dsts = select(_E.dst_id).where(_E.type == "HAS_VALUE", _E.invalid_at.is_(None))
        closed_dsts = select(_E.dst_id).where(
            _E.type == "HAS_VALUE", _E.invalid_at.isnot(None)
        )
        await session.execute(
            _upd(_N)
            .where(_N.id.in_(closed_dsts), _N.id.notin_(open_dsts),
                   _N.status != "superseded")
            .values(status="superseded")
        )
        await session.commit()
        print(f"backfilled {made} value observations")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--org", required=True)
    ap.add_argument("--user", required=True)
    ap.add_argument("--workspace", required=True)
    ap.add_argument("--wipe", action="store_true",
                    help="delete this workspace's watched facts first (recompute, not accumulate)")
    args = ap.parse_args()
    asyncio.run(_backfill(args.org, args.user, args.workspace, wipe=args.wipe))
    return 0


if __name__ == "__main__":
    sys.exit(main())
