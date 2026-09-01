"""Memory retrieval endpoints (hybrid search + cross-scope resolution + explain).

Reads are resolved across the caller's accessible scopes (user → team → org → global),
deduped narrowest-wins with provenance; an optional ``scope`` filter narrows to a single
layer. ``/explain`` returns the resolution trace without mutating anything.
See design-proposal §1, §4.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession


async def _value_since_map(session, resolution) -> dict:
    """valid_at of the live HAS_VALUE edge per retrieved MetricValue node —
    build_packet renders "current since" and labels older evidence with it (x11)."""
    from sqlalchemy import select

    from ...db.models import Edge

    ids = [m.node.id for m in resolution.nodes if m.node.type == "MetricValue"]
    if not ids:
        return {}
    rows = (
        await session.execute(
            select(Edge.dst_id, Edge.valid_at).where(
                Edge.dst_id.in_(ids), Edge.type == "HAS_VALUE", Edge.invalid_at.is_(None)
            )
        )
    ).all()
    return dict(rows)

from ...core.scope import Scope, ScopeRef
from ...db import get_session
from ...graph import get_driver
from ...schemas.memory import (
    AnswerResponse,
    ContentionOut,
    EdgeOut,
    ExplainResponse,
    MetricHistoryOut,
    MetricsResponse,
    MetricValueOut,
    NodeOut,
    PassageOut,
    ProjectionResponse,
    PublishRequest,
    PublishResponse,
    SearchResponse,
    StoreStats,
)
from ...services.embeddings_client import get_embedder
from ...services.projection import render_projection
from ...services.resolution import MergedNode, RetrievalContext, accessible_scopes
from ..deps import (
    AuthIdentity,
    authorize_write_scope,
    get_retrieval,
    require_permission,
    rls_guard,
)

# rls_guard sets the tenant GUC so migration-0009 RLS backstops these reads (no-op unless enabled).
router = APIRouter(prefix="/memory", tags=["memory"], dependencies=[Depends(rls_guard)])


def _node_out(merged: MergedNode) -> NodeOut:
    node = merged.node
    return NodeOut(
        id=node.id,
        type=node.type,
        key=node.key,
        name=node.name,
        summary=node.summary,
        score=merged.score,
        props=node.props or {},
        scope=node.scope,
        scope_id=node.scope_id,
        also_known_at=[str(s) for s in merged.also_known_at],
        origin="derived",
        confidence=node.confidence,
        status=node.status,
        workspace=node.workspace,
        contested=merged.contested,
        observed_count=node.observed_count or 1,
        derived_from=list(node.derived_from or []),
    )


def _passage_out(hit, doc_meta: dict | None = None) -> PassageOut:
    c = hit.chunk
    meta = (doc_meta or {}).get(c.document_id, {})
    return PassageOut(
        id=c.id,
        document_id=c.document_id,
        content=c.content,
        score=hit.score,
        ordinal=c.ordinal,
        scope=c.scope,
        scope_id=c.scope_id,
        source=c.source,
        origin="direct",
        workspace=c.workspace,
        document_uri=meta.get("uri"),
        document_title=meta.get("title"),
        valid_at=meta.get("valid_at"),
    )


async def _doc_meta_for(session, passages) -> dict:
    """Parent-document metadata for a passage set (one query; powers citations)."""
    from sqlalchemy import select as _select

    from ...db.models import Document

    doc_ids = {p.chunk.document_id for p in passages}
    if not doc_ids:
        return {}
    rows = (await session.execute(_select(Document).where(Document.id.in_(doc_ids)))).scalars()
    return {d.id: {"title": d.title, "valid_at": d.valid_at, "uri": d.uri} for d in rows}


def _bare_node_out(node, score: float = 0.0) -> NodeOut:
    return NodeOut(
        id=node.id,
        type=node.type,
        key=node.key,
        name=node.name,
        summary=node.summary,
        score=score,
        props=node.props or {},
        scope=node.scope,
        scope_id=node.scope_id,
    )


def _edge_out(edge) -> EdgeOut:
    return EdgeOut(
        id=edge.id,
        type=edge.type,
        src_id=edge.src_id,
        dst_id=edge.dst_id,
        fact=edge.fact,
        valid_at=edge.valid_at,
        invalid_at=edge.invalid_at,
        scope=edge.scope,
        scope_id=edge.scope_id,
    )


def _context(
    identity: AuthIdentity,
    *,
    repo: str | None = None,
    service: str | None = None,
    agent: str | None = None,
    use_case: str | None = None,
    workspace: str | None = None,
) -> RetrievalContext:
    """Build the retrieval context, activating any subject scopes named in the request.

    Subject scopes are org-internal: a caller may activate a repo/service/agent within their
    own tenant. Personal (USER) memory stays owner-only and is keyed by the caller's id.
    ``workspace`` focuses ranking inside the personal store ("me, in repo X") and also
    activates the matching repo subject when none was named explicitly.
    """
    subjects: list[ScopeRef] = []
    if repo:
        subjects.append(ScopeRef(Scope.REPO, repo))
    if service:
        subjects.append(ScopeRef(Scope.SERVICE, service))
    if agent:
        subjects.append(ScopeRef(Scope.AGENT, agent))
    if workspace and not repo:
        subjects.append(ScopeRef(Scope.REPO, workspace))
    return RetrievalContext(
        tenant_id=identity.org_id,
        user_id=identity.user_id,
        team_ids=tuple(identity.team_ids),
        subjects=tuple(subjects),
        use_case=use_case,
        workspace=workspace,
    )


@router.get("/search", response_model=SearchResponse)
async def search_memory(
    session: Annotated[AsyncSession, Depends(get_session)],
    identity: Annotated[AuthIdentity, Depends(require_permission("memory.read"))],
    q: str | None = Query(None, description="natural-language query"),
    type: str | None = Query(None, description="filter by ontology node type"),
    scope: Scope | None = Query(None, description="narrow to a single scope kind"),
    repo: str | None = Query(None, description="activate a repo subject scope"),
    service: str | None = Query(None, description="activate a service subject scope"),
    agent: str | None = Query(None, description="activate an agent subject scope"),
    use_case: str | None = Query(None, description="retrieval routing profile"),
    workspace: str | None = Query(
        None, description="active workspace facet — focuses personal-store ranking"
    ),
    origin: str | None = Query(None, description="restrict to 'direct' or 'derived' memory"),
    as_of: datetime | None = Query(None, description="time-travel: facts as of this instant"),
    as_of_mode: str = Query("valid", description="'valid' (world-time) | 'system' (belief-time)"),
    limit: int = Query(10, ge=1, le=100),
    depth: int = Query(1, ge=0, le=3),
    legs: str | None = Query(
        None,
        description="direct-tier ablation (evals): 'vector' or 'lexical' to run one leg alone",
        pattern="^(vector|lexical)$",
    ),
    workspace_mode: str = Query(
        "boost", pattern="^(boost|filter)$",
        description="boost (default: workspace focuses ranking, other workspaces can "
        "appear) | filter (hard-bound results to the workspace — the Graph's focus "
        "semantics; may return fewer than limit)",
    ),
) -> SearchResponse:
    ctx = _context(
        identity,
        repo=repo,
        service=service,
        agent=agent,
        use_case=use_case,
        workspace=workspace,
    )
    res = await get_retrieval().resolve(
        session,
        ctx,
        query=q,
        type_=type,
        scope=scope,
        origin=origin,
        as_of=as_of,
        as_of_mode=as_of_mode,
        limit=limit,
        depth=depth,
        legs={legs} if legs else None,
    )
    if workspace and workspace_mode == "filter":
        # hard focus (UI QA: a soft boost let 19 foreign-workspace facts outrank
        # the focused workspace's 1 — truthful focus chips need a real bound)
        res.nodes[:] = [m for m in res.nodes if m.node.workspace == workspace]
        res.passages[:] = [p for p in res.passages if p.chunk.workspace == workspace]
        kept = {m.node.id for m in res.nodes}
        res.edges[:] = [e for e in res.edges if e.src_id in kept or e.dst_id in kept]
    doc_meta = await _doc_meta_for(session, res.passages)
    from ...services.usage import mark_served

    await mark_served(
        session,
        chunk_ids=[p.chunk.id for p in res.passages],
        node_ids=[m.node.id for m in res.nodes],
    )
    return SearchResponse(
        nodes=[_node_out(m) for m in res.nodes],
        edges=[_edge_out(e) for e in res.edges],
        passages=[_passage_out(p, doc_meta) for p in res.passages],
    )


@router.get("/projection", response_model=ProjectionResponse)
async def memory_projection(
    session: Annotated[AsyncSession, Depends(get_session)],
    identity: Annotated[AuthIdentity, Depends(require_permission("memory.read"))],
    repo: str | None = Query(None, description="activate a repo subject scope"),
    service: str | None = Query(None, description="activate a service subject scope"),
    agent: str | None = Query(None, description="activate an agent subject scope"),
    token_budget: int = Query(5000, ge=200, le=50000, description="max tokens to render"),
) -> ProjectionResponse:
    """The always-on Markdown memory tier — current facts across scopes, budget-capped."""
    ctx = _context(identity, repo=repo, service=service, agent=agent)
    p = await render_projection(session, ctx, token_budget=token_budget)
    return ProjectionResponse(
        markdown=p.markdown,
        estimated_tokens=p.estimated_tokens,
        fact_count=p.fact_count,
        truncated=p.truncated,
    )


@router.get("/explain", response_model=ExplainResponse)
async def explain_memory(
    session: Annotated[AsyncSession, Depends(get_session)],
    identity: Annotated[AuthIdentity, Depends(require_permission("memory.read"))],
    q: str | None = Query(None, description="natural-language query"),
    type: str | None = Query(None, description="filter by ontology node type"),
    scope: Scope | None = Query(None, description="narrow to a single scope kind"),
    repo: str | None = Query(None, description="activate a repo subject scope"),
    service: str | None = Query(None, description="activate a service subject scope"),
    agent: str | None = Query(None, description="activate an agent subject scope"),
    limit: int = Query(10, ge=1, le=100),
) -> ExplainResponse:
    ctx = _context(identity, repo=repo, service=service, agent=agent)
    res = await get_retrieval().resolve(
        session, ctx, query=q, type_=type, scope=scope, limit=limit, expand=False
    )
    t = res.trace
    return ExplainResponse(
        query=q,
        accessible_scopes=[str(s) for s in t.accessible],
        requested_scope=t.requested_scope.value if t.requested_scope else None,
        per_scope_hits=t.per_scope_hits,
        candidates=t.candidates,
        merged=t.merged,
        shadowed=t.shadowed,
        results=[_node_out(m) for m in res.nodes],
    )


@router.get("/stats", response_model=StoreStats)
async def memory_stats(
    session: Annotated[AsyncSession, Depends(get_session)],
    identity: Annotated[AuthIdentity, Depends(require_permission("memory.read"))],
) -> StoreStats:
    """Store composition for the caller's tenant (UI dashboard). Tenant-bounded counts —
    personal-memory contents stay owner-only; these are aggregates, not contents."""
    from sqlalchemy import func as _f
    from sqlalchemy import or_ as _or
    from sqlalchemy import select as _select

    from ...db.models import Chunk, Document, Edge, Episode, Node

    def _tenant(model):
        return _or(model.org_id == identity.org_id, model.org_id.is_(None))

    async def _count(model) -> int:
        return (
            await session.execute(_select(_f.count()).where(_tenant(model)))
        ).scalar() or 0

    async def _group(model, col) -> dict[str, int]:
        rows = await session.execute(
            _select(col, _f.count()).where(_tenant(model)).group_by(col)
        )
        return {str(k): v for k, v in rows.all() if k is not None}

    embedder = get_embedder()
    ws_rows = await session.execute(
        _select(Node.workspace, _f.count())
        .where(_tenant(Node), Node.workspace.isnot(None))
        .group_by(Node.workspace)
        .order_by(_f.count().desc())
        .limit(12)
    )
    contested = (
        await session.execute(
            _select(_f.count()).where(
                _tenant(Edge), Edge.type == "CONTRADICTS", Edge.invalid_at.is_(None)
            )
        )
    ).scalar() or 0
    return StoreStats(
        documents=await _count(Document),
        chunks=await _count(Chunk),
        nodes=await _count(Node),
        edges=await _count(Edge),
        episodes=await _count(Episode),
        by_scope=await _group(Node, Node.scope),
        by_source=await _group(Document, Document.source),
        by_status=await _group(Node, Node.status),
        top_workspaces=[{"workspace": w, "nodes": n} for w, n in ws_rows.all()],
        contested=contested,
        embedder={
            "name": embedder.name,
            "quality": getattr(embedder, "quality", "semantic"),
            "dim": embedder.dim,
        },
    )


@router.get("/answer/stream")
async def answer_memory_stream(
    identity: Annotated[AuthIdentity, Depends(require_permission("memory.read"))],
    q: str = Query(..., min_length=2),
    workspace: str | None = Query(None),
    as_of: datetime | None = Query(None),
    as_of_mode: str = Query("valid"),
    limit: int = Query(8, ge=1, le=25),
    agent_mode: bool = Query(False),
    synth_model: str | None = Query(None),
    max_hops: int = Query(3, ge=1, le=5),
):
    """The memory hop as a LIVE event stream (SSE) — the loop is the product's
    demo, so the UI watches it instead of awaiting it. Events: retrieved,
    deciding, early_stop, composing, answer, error. Same governance as /answer
    (scope resolution, as-of, agent-mode) on every hop."""
    import asyncio
    import json as _json

    from fastapi.responses import StreamingResponse
    from sqlalchemy import select as _select

    from ...core.config import get_settings
    from ...db import get_session_factory
    from ...db.models import Document
    from ...services.memory_hop import run_memory_hop
    from ...services.packets import build_packet
    from ...services.usage import mark_served

    ctx = _context(identity, workspace=workspace)
    served_model = synth_model or get_settings().answer_synthesis_model
    queue: asyncio.Queue = asyncio.Queue()
    factory = get_session_factory()

    async def fetch_packet(hop_q: str) -> str:
        # each hop owns a FRESH session: the request-scoped session's lifecycle
        # (dependency teardown) races a long-lived streaming generator, and one
        # failed statement aborts every later hop (found live: multi-hop streams
        # died with InFailedSQLTransactionError). Fresh session per hop isolates
        # failures and commits usage counters immediately.
        async with factory() as hop_session:
            res = await get_retrieval().resolve(
                hop_session, ctx, query=hop_q, as_of=as_of, as_of_mode=as_of_mode,
                limit=limit, expand=False,
            )
            doc_ids = {p.chunk.document_id for p in res.passages}
            meta: dict = {}
            if doc_ids:
                rows = (
                    await hop_session.execute(
                        _select(Document).where(Document.id.in_(doc_ids))
                    )
                ).scalars()
                meta = {d.id: {"title": d.title, "valid_at": d.valid_at, "uri": d.uri}
                        for d in rows}
            md, summary = build_packet(
                query=hop_q, resolution=res, doc_meta=meta, as_of=as_of,
                workspace=workspace, agent_mode=agent_mode,
                value_since=await _value_since_map(hop_session, res),
            )
            await mark_served(
                hop_session,
                chunk_ids=summary.pop("served_chunk_ids", []),
                node_ids=summary.pop("served_node_ids", []),
            )
            await hop_session.commit()
        return md

    async def worker() -> None:
        try:
            result = await run_memory_hop(
                query=q, fetch_packet=fetch_packet, max_hops=max_hops,
                model=served_model, on_event=queue.put,
            )
            await queue.put({
                "type": "answer", "markdown": result.answer, "hops": result.hops,
                "trace": result.trace, "invented_citations": result.invented_citations,
                "model": served_model,
            })
        except Exception as exc:  # noqa: BLE001 — surface, never hang the stream
            await queue.put({"type": "error", "detail": str(exc)[:300]})
        await queue.put(None)

    async def gen():
        task = asyncio.create_task(worker())
        try:
            while True:
                ev = await queue.get()
                if ev is None:
                    break
                yield f"event: {ev['type']}\ndata: {_json.dumps(ev)}\n\n"
        finally:
            task.cancel()

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache"})


@router.get("/metrics", response_model=MetricsResponse)
async def metric_histories(
    session: Annotated[AsyncSession, Depends(get_session)],
    identity: Annotated[AuthIdentity, Depends(require_permission("memory.read"))],
    workspace: str | None = Query(None),
) -> MetricsResponse:
    """Watched-value histories — every value each metric has held, with validity
    windows (timeline UI: supersession made visible). Caller-visible scope only."""
    from sqlalchemy import select as _select

    from ...db.models import Edge, Node
    from ...db.scoping import browse_clause

    visible = browse_clause(
        Node,
        tenant_id=identity.org_id,
        user_id=identity.user_id,
        team_ids=tuple(identity.team_ids),
    )
    anchors_q = _select(Node).where(visible, Node.type == "Metric")
    if workspace:
        anchors_q = anchors_q.where(Node.workspace == workspace)
    anchors = (await session.execute(anchors_q)).scalars().all()
    out: list[MetricHistoryOut] = []
    for anchor in anchors:
        rows = (
            await session.execute(
                _select(Edge, Node)
                .join(Node, Node.id == Edge.dst_id)
                .where(Edge.src_id == anchor.id, Edge.type == "HAS_VALUE")
                .order_by(Edge.valid_at)
            )
        ).all()
        out.append(
            MetricHistoryOut(
                key=anchor.key,
                label=anchor.name,
                workspace=anchor.workspace,
                values=[
                    MetricValueOut(
                        value=str((v.props or {}).get("value", v.name)),
                        display=v.name,
                        valid_at=e.valid_at,
                        invalid_at=e.invalid_at,
                        current=e.invalid_at is None,
                        evidence_span=(v.props or {}).get("evidence_span"),
                    )
                    for e, v in rows
                ],
            )
        )
    return MetricsResponse(metrics=out)


@router.get("/answer", response_model=AnswerResponse)
async def answer_memory(
    session: Annotated[AsyncSession, Depends(get_session)],
    identity: Annotated[AuthIdentity, Depends(require_permission("memory.read"))],
    q: str = Query(..., min_length=2, description="the question to answer from memory"),
    workspace: str | None = Query(None),
    repo: str | None = Query(None),
    service: str | None = Query(None),
    agent: str | None = Query(None),
    as_of: datetime | None = Query(None),
    as_of_mode: str = Query("valid"),
    limit: int = Query(8, ge=1, le=25),
    agent_mode: bool = Query(
        False,
        description="suppress failed-attempt narrative; negative knowledge as constraints",
    ),
    mode: str = Query(
        "packet",
        pattern="^(packet|synthesized|hybrid|hop)$",
        description="packet (deterministic, default, $0) | synthesized (one gateway call "
        "composes a cited answer FROM the packet) | hybrid (synthesized + full packet) | "
        "hop (bounded retrieval loop — reformulates with corpus vocabulary; costs "
        "up to max_hops+1 model calls; the user chooses to spend)",
    ),
    synth_model: str | None = Query(
        None, description="model override for synthesis (x5 experiment arms)"
    ),
    max_hops: int = Query(3, ge=1, le=5, description="hop budget for mode=hop"),
) -> AnswerResponse:
    """The memory packet — the system's designed answer format (task #5).

    Deterministic (no LLM): scope-resolved hybrid retrieval assembled into an
    llms.txt-style markdown document with memory:// citations, contested labels,
    explicit gaps, and recency-decay ranking (half-life 180d) on evidence. Works
    identically for humans (UI) and agents (MCP), and abstains honestly.
    """
    import time as _time

    from sqlalchemy import select as _select

    from ...db.models import Document
    from ...services.packets import build_packet

    t0 = _time.monotonic()
    ctx = _context(identity, repo=repo, service=service, agent=agent, workspace=workspace)
    res = await get_retrieval().resolve(
        session,
        ctx,
        query=q,
        as_of=as_of,
        as_of_mode=as_of_mode,
        limit=limit,
        expand=False,
    )
    doc_ids = {p.chunk.document_id for p in res.passages}
    doc_meta: dict = {}
    if doc_ids:
        rows = (
            await session.execute(_select(Document).where(Document.id.in_(doc_ids)))
        ).scalars()
        doc_meta = {
            d.id: {"title": d.title, "valid_at": d.valid_at, "uri": d.uri} for d in rows
        }
    markdown, summary = build_packet(
        query=q,
        resolution=res,
        doc_meta=doc_meta,
        as_of=as_of,
        workspace=workspace,
        agent_mode=agent_mode,
        value_since=await _value_since_map(session, res),
    )
    from ...services.usage import mark_served

    await mark_served(
        session,
        chunk_ids=summary.pop("served_chunk_ids", []),
        node_ids=summary.pop("served_node_ids", []),
    )
    synth_ms = None
    served_model = None
    hops = None
    hop_trace: list[str] | None = None
    invented = 0
    # model layers compose FROM packets; an empty packet short-circuits to the
    # packet's own abstention (no model call, no cost — honest by construction)
    if mode != "packet" and (summary.get("facts") or summary.get("passages")):
        from ...core.config import get_settings
        from ...services.memory_hop import enforce_citations, run_memory_hop
        from ...services.synthesis import synthesize_answer

        served_model = synth_model or get_settings().answer_synthesis_model
        t1 = _time.monotonic()
        if mode == "hop":

            async def fetch_packet(hop_q: str) -> str:
                hop_res = await get_retrieval().resolve(
                    session, ctx, query=hop_q, as_of=as_of, as_of_mode=as_of_mode,
                    limit=limit, expand=False,
                )
                hop_ids = {p.chunk.document_id for p in hop_res.passages}
                meta: dict = doc_meta
                if hop_ids - set(doc_meta):
                    rows = (
                        await session.execute(
                            _select(Document).where(Document.id.in_(hop_ids))
                        )
                    ).scalars()
                    meta = {**doc_meta, **{
                        d.id: {"title": d.title, "valid_at": d.valid_at, "uri": d.uri}
                        for d in rows
                    }}
                md, _ = build_packet(
                    query=hop_q, resolution=hop_res, doc_meta=meta, as_of=as_of,
                    workspace=workspace, agent_mode=agent_mode,
                    value_since=await _value_since_map(session, hop_res),
                )
                return md

            first = markdown

            async def fetch(hop_q: str) -> str:
                return first if hop_q == q else await fetch_packet(hop_q)

            result = await run_memory_hop(
                query=q, fetch_packet=fetch, max_hops=max_hops, model=served_model
            )
            markdown, hops, hop_trace = result.answer, result.hops, result.trace
            invented = result.invented_citations
        else:
            answer, _usage = await synthesize_answer(
                query=q, packet_markdown=markdown, model=served_model
            )
            # server-enforced grounding: strip citations that are not in the packet
            from ...services.memory_hop import CIT_RE

            answer, invented = enforce_citations(answer, set(CIT_RE.findall(markdown)))
            markdown = answer if mode == "synthesized" else f"{answer}\n\n---\n\n{markdown}"
        synth_ms = int((_time.monotonic() - t1) * 1000)
    return AnswerResponse(
        query=q,
        markdown=markdown,
        took_ms=int((_time.monotonic() - t0) * 1000),
        mode=mode,
        synth_model=served_model,
        synth_ms=synth_ms,
        hops=hops,
        hop_trace=hop_trace,
        invented_citations=invented,
        **summary,
    )


@router.delete("/workspaces/{workspace}")
async def purge_workspace(
    workspace: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    identity: Annotated[AuthIdentity, Depends(require_permission("memory.write"))],
) -> dict:
    """Forget an entire workspace within the caller's OWN personal scope — both
    tiers (documents+chunks AND derived facts+edges), audited.

    The granularity consent-CLEAR lacks: "this project is over" must not nuke a
    person's whole store. Deliberately owner-scoped: org/team workspace purges
    are a governance action, not this endpoint.
    """
    from sqlalchemy import delete as _delete
    from sqlalchemy import select as _sel

    from ...db.models import Chunk, Document, Edge, Node
    from ...ingest.envelope import Actor, EpisodeEnvelope
    from ...repositories.episodes import insert_episode

    def _mine(model):
        return (
            (model.org_id == identity.org_id)
            & (model.scope == "user")
            & (model.scope_id == identity.user_id)
            & (model.workspace == workspace)
        )

    node_ids = (await session.execute(_sel(Node.id).where(_mine(Node)))).scalars().all()
    edges = 0
    if node_ids:
        r = await session.execute(
            _delete(Edge).where(Edge.src_id.in_(node_ids) | Edge.dst_id.in_(node_ids))
        )
        edges = r.rowcount or 0
    nodes = (await session.execute(_delete(Node).where(_mine(Node)))).rowcount or 0
    doc_ids = (
        (await session.execute(_sel(Document.id).where(_mine(Document)))).scalars().all()
    )
    chunks = 0
    if doc_ids:
        r = await session.execute(_delete(Chunk).where(Chunk.document_id.in_(doc_ids)))
        chunks = r.rowcount or 0
    docs = (await session.execute(_delete(Document).where(_mine(Document)))).rowcount or 0

    env = EpisodeEnvelope(
        org_id=identity.org_id,
        scope="user",
        scope_id=identity.user_id,
        workspace=workspace,
        actor=Actor(user_id=identity.user_id),
        source="governance",
        action_type="memory.workspace_purged",
        payload={"workspace": workspace, "documents": docs, "chunks": chunks,
                 "facts": nodes, "edges": edges},
    )
    await insert_episode(session, env.to_episode_kwargs(capture_content=False))
    return {"workspace": workspace, "documents": docs, "chunks": chunks,
            "facts": nodes, "edges": edges}


@router.post("/curate")
async def curate_memory(
    body: dict,
    session: Annotated[AsyncSession, Depends(get_session)],
    identity: Annotated[AuthIdentity, Depends(require_permission("memory.write"))],
) -> dict:
    """Natural-language curation, two-phase and governed.

    Phase 1 (no confirm_ids): {"instruction": "...", "workspace": "..."} — one
    model call classifies the caller's VISIBLE documents in that workspace
    against the instruction and returns a PREVIEW (forget-candidates with
    reasons + usage evidence). NOTHING is deleted.
    Phase 2: {"confirm_ids": [...]} — deletes exactly those ids through the
    audited forget path. The model proposes; the human disposes; the server
    only deletes what was explicitly confirmed.
    """
    from sqlalchemy import func as _f
    from sqlalchemy import select as _select

    from ...db.models import Chunk, Document
    from ...db.scoping import browse_clause
    from ...services.llm_client import get_llm_client
    from .documents import forget_document

    workspace = body.get("workspace")
    if not workspace:
        raise HTTPException(status_code=422, detail="workspace is required")
    visible = browse_clause(
        Document, tenant_id=identity.org_id, user_id=identity.user_id,
        team_ids=tuple(identity.team_ids),
    )
    docs = (
        (
            await session.execute(
                _select(Document).where(visible, Document.workspace == workspace)
                .order_by(Document.valid_at).limit(300)
            )
        )
        .scalars()
        .all()
    )
    if body.get("confirm_ids"):
        allowed = {d.id for d in docs}
        bad = [i for i in body["confirm_ids"] if i not in allowed]
        if bad:
            raise HTTPException(
                status_code=422,
                detail=f"{len(bad)} ids are not visible workspace documents",
            )
        results = []
        for doc_id in body["confirm_ids"]:
            results.append(await forget_document(doc_id, session, identity))
        return {
            "mode": "executed",
            "forgotten": len(results),
            "purged_chunks": sum(r["purged_chunks"] for r in results),
            "documents": results,
        }

    instruction = (body.get("instruction") or "").strip()
    if not instruction:
        raise HTTPException(status_code=422, detail="instruction is required for preview")
    served: dict[str, int] = dict(
        (
            await session.execute(
                _select(Chunk.document_id, _f.max(Chunk.served_count))
                .where(Chunk.document_id.in_([d.id for d in docs]))
                .group_by(Chunk.document_id)
            )
        ).all()
    ) if docs else {}
    listing = "\n".join(
        f"- id={d.id} | {d.valid_at.date() if d.valid_at else 'undated'} | "
        f"served={served.get(d.id, 0)} | {(d.title or '')[:90]}"
        for d in docs
    )
    schema = {
        "type": "object",
        "properties": {"forget": {"type": "array", "items": {
            "type": "object",
            "properties": {"id": {"type": "string"}, "reason": {"type": "string"}},
            "required": ["id", "reason"], "additionalProperties": False,
        }}},
        "required": ["forget"], "additionalProperties": False,
    }
    out = await get_llm_client().complete_json(
        system=(
            "You curate an organization's memory. Given a curation instruction and a "
            "document listing (id | event date | times-served | title), choose which "
            "documents to FORGET per the instruction. Be conservative: when unsure, "
            "keep. Never forget documents the instruction wants preserved. Output "
            "STRICT JSON per the schema; reasons ≤15 words."
        ),
        user=f"Instruction: {instruction}\n\nDocuments:\n{listing[:24_000]}",
        schema=schema,
        max_tokens=1500,
    )
    allowed = {d.id for d in docs}
    by_id = {d.id: d for d in docs}
    candidates = [
        {
            "id": f["id"],
            "title": by_id[f["id"]].title,
            "valid_at": by_id[f["id"]].valid_at.isoformat()
            if by_id[f["id"]].valid_at else None,
            "served_count": served.get(f["id"], 0),
            "reason": f.get("reason", ""),
        }
        for f in out.get("forget", [])
        if f.get("id") in allowed
    ]
    return {
        "mode": "preview",
        "workspace": workspace,
        "candidates": candidates,
        "keep_count": len(docs) - len(candidates),
        "note": "nothing deleted — POST again with confirm_ids to execute",
    }


@router.post("/publish", response_model=PublishResponse)
async def publish_memory(
    body: PublishRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    identity: Annotated[AuthIdentity, Depends(require_permission("memory.write"))],
) -> PublishResponse:
    """Promote personal memories into a shared scope (v3 lifecycle: committed → published).

    Deliberate, auditable sharing — never automatic. Conflicting knowledge is kept as a
    CONTENTION (both sides live, linked CONTRADICTS, labeled in search), not overwritten:
    two people publishing contradictory conventions is data, not an error.
    """
    if body.scope not in ("org", "team"):
        raise HTTPException(status_code=422, detail="publish target must be org or team scope")
    target = ScopeRef(Scope(body.scope), body.scope_id or identity.org_id)
    authorize_write_scope(identity, target.scope.value, target.scope_id)

    driver = get_driver()
    published = merged_count = 0
    contentions: list[ContentionOut] = []
    for nid in body.node_ids:
        node = await driver.get_node(session, nid)
        if node is None:
            raise HTTPException(status_code=404, detail=f"node {nid} not found")
        if (
            node.scope != Scope.USER.value
            or node.scope_id != identity.user_id
            or node.org_id != identity.org_id
        ):
            raise HTTPException(
                status_code=403, detail="only your own personal memories can be published"
            )
        existing = await driver.find_node(
            session, identity.org_id, target, node.type, node.key
        )
        same = existing is not None and (
            existing.name == node.name and (existing.summary or "") == (node.summary or "")
        )
        if same:
            # identical knowledge already shared → endorsement, not duplication
            existing.observed_count = (existing.observed_count or 1) + 1
            existing.derived_from = list(
                dict.fromkeys([*(existing.derived_from or []), *(node.derived_from or [])])
            )
            merged_count += 1
            continue
        # distinct identity per writer when contending, so both assertions coexist under
        # the (scope, type, key) uniqueness constraint.
        key = node.key if existing is None else f"{node.key}~{identity.user_id}"
        copy = await driver.upsert_node(
            session,
            tenant_id=identity.org_id,
            scope=target,
            type_=node.type,
            key=key,
            name=node.name,
            summary=node.summary,
            props={**(node.props or {}), "published_by": identity.user_id},
            embedding=node.embedding,
            embedding_model=node.embedding_model,
            embedding_dim=node.embedding_dim,
            source=node.source,
        )
        copy.status = "published"
        copy.workspace = node.workspace
        copy.derived_from = list(node.derived_from or [])
        published += 1
        if existing is not None:
            edge = await driver.upsert_edge(
                session,
                tenant_id=identity.org_id,
                scope=target,
                type_="CONTRADICTS",
                src_id=copy.id,
                dst_id=existing.id,
                fact=(
                    f"'{node.name}' (published by {identity.user_id}) disagrees with "
                    f"'{existing.name}'"
                ),
            )
            contentions.append(
                ContentionOut(
                    published_id=copy.id, conflicts_with_id=existing.id, fact=edge.fact
                )
            )
    await session.commit()
    return PublishResponse(published=published, merged=merged_count, contentions=contentions)


@router.get("/nodes/{node_id}", response_model=SearchResponse)
async def get_node(
    node_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    identity: Annotated[AuthIdentity, Depends(require_permission("memory.read"))],
    depth: int = Query(1, ge=0, le=3),
    as_of: datetime | None = Query(None, description="time-travel: facts as of this instant"),
    as_of_mode: str = Query("valid", description="'valid' (world-time) | 'system' (belief-time)"),
) -> SearchResponse:
    driver = get_driver()
    node = await driver.get_node(session, node_id)
    if node is None:
        raise HTTPException(status_code=404, detail="node not found")
    # GLOBAL is cross-tenant and readable; everything else is tenant-bounded.
    if node.scope != Scope.GLOBAL.value and node.org_id != identity.org_id:
        raise HTTPException(status_code=403, detail="not permitted for this tenant")
    # personal (user-scoped) memory is owner-only — admins included (design-proposal §6.2).
    if node.scope == Scope.USER.value and node.scope_id != identity.user_id:
        raise HTTPException(status_code=403, detail="personal memory is owner-only")
    # TEAM memory is membership-gated (same rule the search path enforces via
    # accessible_scopes) — node-by-id must not be a side door around it.
    if (
        node.scope == Scope.TEAM.value
        and node.scope_id not in identity.team_ids
        and not identity.is_admin()
    ):
        raise HTTPException(status_code=403, detail="team memory requires membership")

    # Neighbor expansion is confined to the caller's accessible scopes (plus this node's
    # own scope: repo/service/agent subjects are org-shared by design, and team/user were
    # authorized above). An authorized start node must not expose edges in scopes the
    # caller cannot read — e.g. another user's personal memory one hop away.
    ctx = RetrievalContext(
        tenant_id=identity.org_id,
        user_id=identity.user_id,
        team_ids=tuple(identity.team_ids),
    )
    allowed = accessible_scopes(ctx)
    own = ScopeRef(Scope(node.scope), node.scope_id)
    if own not in allowed:
        allowed = [own, *allowed]
    edges = await driver.neighbors(
        session,
        node_id,
        depth=depth,
        as_of=as_of,
        as_of_mode=as_of_mode,
        tenant_id=identity.org_id,
        scopes=allowed,
    )
    return SearchResponse(nodes=[_bare_node_out(node)], edges=[_edge_out(e) for e in edges])
