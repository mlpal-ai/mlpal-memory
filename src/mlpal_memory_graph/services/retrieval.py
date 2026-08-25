"""Hybrid local retrieval + cross-scope resolution, across both memory tiers.

Pipeline: resolve accessible scopes → search each (hard scope predicate) for **derived** facts
(nodes/edges, merged narrowest-wins with provenance) AND **direct** passages (chunks) → expand
the graph neighborhood. The same path backs ``/memory/search`` and ``/memory/explain``.
An ``origin`` filter can restrict to one tier. See design-proposal §1.2, §4, §14.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..core.logging import get_logger
from ..core.scope import Scope
from ..graph import get_driver
from ..graph.driver import ScoredNode
from ..services.direct import ChunkHit, DirectMemory
from ..services.embeddings_client import get_embedder
from .hybrid import distance_boost, rrf_fuse, workspace_boost
from .resolution import (
    MergedNode,
    ResolutionTrace,
    RetrievalContext,
    accessible_scopes,
    merge_scored,
    narrow_to,
)
from .routing import get_router

log = get_logger(__name__)


@dataclass
class Resolution:
    nodes: list[MergedNode]  # derived (inferred) facts
    edges: list
    trace: ResolutionTrace
    passages: list[ChunkHit] = field(default_factory=list)  # direct (verbatim) memory


class Retrieval:
    def __init__(self) -> None:
        self.driver = get_driver()
        self.embedder = get_embedder()
        self.direct = DirectMemory()

    async def _hybrid_nodes(
        self, session, ctx, scopes, query, type_, sources, limit
    ) -> list[ScoredNode]:
        """Two-leg hybrid: vector (``<=>``/cosine) ∪ lexical (tsvector+pg_trgm/overlap), fused by
        RRF. Without a query it's a plain scoped browse. Read path is LLM-free."""
        if not query:
            return await self.driver.search_nodes(
                session,
                tenant_id=ctx.tenant_id,
                scopes=scopes,
                type_=type_,
                sources=sources,
                limit=limit,
            )
        embedding = await self.embedder.embed_one(query)
        vec = await self.driver.search_nodes(
            session,
            tenant_id=ctx.tenant_id,
            scopes=scopes,
            query_embedding=embedding,
            type_=type_,
            sources=sources,
            limit=limit,
        )
        lex = await self.driver.lexical_search_nodes(
            session,
            tenant_id=ctx.tenant_id,
            scopes=scopes,
            text=query,
            type_=type_,
            sources=sources,
            limit=limit,
        )
        by_id = {h.node.id: h.node for h in (*vec, *lex)}
        fused = rrf_fuse([[h.node.id for h in vec], [h.node.id for h in lex]])
        # node-distance rerank: lift facts graph-close to the caller's anchor nodes (their User
        # node + any activated Agent subject). No anchors / unreachable → no change (graceful).
        anchors = await self._anchor_ids(session, ctx, scopes)
        if anchors:
            distances = await self.driver.graph_distance(
                session, anchor_ids=anchors, candidate_ids=list(by_id), max_depth=3
            )
            fused = distance_boost(fused, distances)
        # v3 workspace focus: memories from the active workspace rank first; others stay
        # reachable (focus, not a filter — cross-workspace learning is a feature).
        if ctx.workspace:
            fused = workspace_boost(
                fused, {nid: n.workspace for nid, n in by_id.items()}, ctx.workspace
            )
        log.info(
            "retrieval.mix",
            vector=len(vec),
            lexical=len(lex),
            fused=len(by_id),
            anchors=len(anchors),
        )
        return [ScoredNode(by_id[nid], score) for nid, score in fused.items()]

    async def _contested_ids(self, session, node_ids: list[str]) -> set[str]:
        """Which of ``node_ids`` are touched by a live CONTRADICTS edge."""
        from sqlalchemy import or_, select

        from ..db.models import Edge

        stmt = select(Edge.src_id, Edge.dst_id).where(
            Edge.type == "CONTRADICTS",
            Edge.invalid_at.is_(None),
            or_(Edge.src_id.in_(node_ids), Edge.dst_id.in_(node_ids)),
        )
        touched: set[str] = set()
        for src, dst in (await session.execute(stmt)).all():
            touched.add(src)
            touched.add(dst)
        return touched & set(node_ids)

    async def _anchor_ids(self, session, ctx, scopes) -> list[str]:
        """The caller's graph anchor nodes for node-distance rerank: their User node + the nodes
        of any activated subject scopes (e.g. Agent)."""
        anchors: list[str] = []
        if ctx.user_id:
            anchors += await self.driver.find_node_ids(
                session, tenant_id=ctx.tenant_id, scopes=scopes, type_="User", keys=[ctx.user_id]
            )
        agent_keys = [s.scope_id for s in ctx.subjects if s.scope is Scope.AGENT]
        if agent_keys:
            anchors += await self.driver.find_node_ids(
                session, tenant_id=ctx.tenant_id, scopes=scopes, type_="Agent", keys=agent_keys
            )
        return anchors

    async def resolve(
        self,
        session,
        ctx: RetrievalContext,
        *,
        query: str | None = None,
        type_: str | None = None,
        scope: Scope | None = None,
        origin: str | None = None,  # "direct" | "derived" | None (both)
        as_of=None,
        as_of_mode: str = "valid",
        limit: int = 10,
        depth: int = 1,
        expand: bool = True,
        legs: set[str] | None = None,  # direct-tier ablation: {"vector"} | {"lexical"} | None=both
    ) -> Resolution:
        scopes = narrow_to(accessible_scopes(ctx), scope)
        # use-case routing selects which sources to read from; scope stays a hard predicate.
        route = get_router().route(ctx.use_case)
        sources = list(route.allowed_sources) if route and route.allowed_sources else None

        merged: list[MergedNode] = []
        edges: dict[str, object] = {}
        trace = None
        if origin != "direct":  # derived tier (graph)
            # over-fetch before merge so cross-scope duplicates don't shrink the result page
            overfetch = limit * max(1, len(scopes))
            scored = await self._hybrid_nodes(
                session, ctx, scopes, query, type_, sources, overfetch
            )
            # node-follows-edge-validity: at as_of T a node only surfaces if it has >=1 edge valid
            # at T (so a Fact whose edges were all invalidated by T drops out). Current view (as_of
            # None) is unchanged. Applies to both valid- and system-time modes.
            if as_of is not None and scored:
                live = await self.driver.nodes_with_valid_edges(
                    session, [s.node.id for s in scored], as_of=as_of, as_of_mode=as_of_mode
                )
                scored = [s for s in scored if s.node.id in live]
            merged, trace = merge_scored(scored, scopes, scope)
            merged = merged[:limit]
            # v3: flag contention points — a live CONTRADICTS edge touching a result means
            # writers disagree; label it rather than silently asserting one side.
            if merged:
                contested = await self._contested_ids(session, [m.node.id for m in merged])
                for m in merged:
                    m.contested = m.node.id in contested
            if expand:
                for m in merged:
                    for e in await self.driver.neighbors(
                        session,
                        m.node.id,
                        depth=depth,
                        as_of=as_of,
                        as_of_mode=as_of_mode,
                        tenant_id=ctx.tenant_id,
                        scopes=scopes,
                    ):
                        edges[e.id] = e

        passages: list[ChunkHit] = []
        if origin != "derived":  # direct tier (verbatim chunks)
            passages = await self.direct.search(
                session,
                tenant_id=ctx.tenant_id,
                scopes=scopes,
                query=query,
                sources=sources,
                limit=limit,
                workspace=ctx.workspace,
                legs=legs,
            )

        if trace is None:  # origin == "direct": still surface which scopes were considered
            _, trace = merge_scored([], scopes, scope)
        return Resolution(nodes=merged, edges=list(edges.values()), trace=trace, passages=passages)
